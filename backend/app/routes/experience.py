"""Owner-focused API facade. Reuses existing inventory, auth and rate services.

All mutations are dairy-scoped and tenant roles are checked. Financial calculations
are deterministic; AI never supplies money to be posted.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (Advance, AdvanceRepayment, AnamatEntry, BillingPeriod, Customer,
    CustomerBill, DairyFinancialPolicy, FeedProduct, FeedSale, LedgerEntry, ScanRowCorrection,
    MilkDispatch, OperatingExpense, ScanBatch, ScanRow)
from ..security import TenantDep, OwnerManagerDep, OwnerDep
from ..services.audit import record_audit
from ..services.feed_inventory import FeedInventoryService, FeedInventoryError, payload_fingerprint
from ..services.ledger import LedgerService
from ..services.settlements import SettlementService, term_for, dec, cash, ZERO

router=APIRouter(prefix='/api/v2', tags=['simple dairy operations'])

class DateRange(BaseModel):
    start:date
    end:date
    @model_validator(mode='after')
    def valid(self):
        if self.end < self.start or (self.end-self.start).days > 34:
            raise ValueError('Choose a valid billing period of at most 35 days')
        return self

class AdvanceIn(BaseModel):
    customer_id:int=Field(gt=0)
    principal:Decimal=Field(gt=0, max_digits=12, decimal_places=2)
    installment:Decimal=Field(ge=0, max_digits=12, decimal_places=2)
    advance_date:date
    note:str=Field(default='',max_length=255)

class PayoutIn(BaseModel):
    customer_id:int=Field(gt=0)
    amount:Decimal=Field(gt=0, max_digits=12, decimal_places=2)
    payout_date:date
    note:str=Field(default='',max_length=255)

class StockItemIn(BaseModel):
    product_id:int=Field(gt=0)
    quantity:Decimal=Field(gt=0, max_digits=14, decimal_places=3)
    purchase_rate:Decimal=Field(ge=0, max_digits=12, decimal_places=2)
    selling_price:Decimal|None=Field(default=None, ge=0, max_digits=12, decimal_places=2)

class BulkStockIn(BaseModel):
    purchase_date:date
    items:list[StockItemIn]=Field(min_length=1,max_length=80)
    idempotency_key:str=Field(min_length=8,max_length=100)

class DispatchIn(BaseModel):
    dispatch_date:date
    litres:Decimal=Field(gt=0,max_digits=14,decimal_places=3)
    amount:Decimal=Field(ge=0,max_digits=12,decimal_places=2)
    buyer:str=Field(default='',max_length=160)

class ExpenseIn(BaseModel):
    expense_date:date
    amount:Decimal=Field(gt=0,max_digits=12,decimal_places=2)
    category:str=Field(min_length=1,max_length=60)
    note:str=Field(default='',max_length=255)

class FeedScanApprove(BaseModel):
    row_ids:list[int]=Field(min_length=1, max_length=250)
    sale_date:date

@router.get('/home')
def home(context:TenantDep,db:Session=Depends(get_db)):
    today=date.today();start,end=term_for(today)
    service=SettlementService(db,context.dairy.id)
    rows=service.milk_summary(start,end)
    today_rows=[r for r in rows if r['date']==today.isoformat()]
    pending=db.query(ScanRow).join(ScanBatch).filter(
        ScanRow.dairy_id==context.dairy.id,ScanRow.approved.is_(False),
        ScanRow.approval_status!='deleted',ScanBatch.kind.in_(['milk','MILK_REGISTER','feed','CATTLE_FEED_PURCHASE'])).count()
    return {
      'today':today.isoformat(),'term_start':start.isoformat(),'term_end':end.isoformat(),
      'today_litres':str(sum((dec(r['quantity']) for r in today_rows),ZERO)),
      'today_amount':str(sum((dec(r['amount']) for r in today_rows),ZERO)),
      'term_litres':str(sum((dec(r['quantity']) for r in rows),ZERO)),
      'term_amount':str(sum((dec(r['amount']) for r in rows),ZERO)),
      'pending_rows':pending,
      'pending_batches':db.query(ScanBatch).filter(ScanBatch.dairy_id==context.dairy.id,
            ScanBatch.status.in_(['pending','partially_approved'])).count(),
      'low_stock':sum(1 for p in db.query(FeedProduct).filter_by(dairy_id=context.dairy.id,active=True)
                     if dec(p.stock_quantity)<=dec(p.low_stock_threshold)),
      'customer_count':db.query(Customer).filter_by(dairy_id=context.dairy.id,active=True).count(),
      'recent_milk':rows[:8],
    }

@router.get('/term-board')
def term_board(context:TenantDep,start:date=Query(...),end:date=Query(...),db:Session=Depends(get_db)):
    """Every registered farmer, each collection date, and current net settlement estimate.

    Recomputed on every GET from approved milk records and current draft financial
    data; finalized bills use their immutable snapshot instead.
    """
    from datetime import timedelta
    DateRange(start=start,end=end)
    service=SettlementService(db,context.dairy.id)
    current=date.today()
    through=min(current,end)
    days=[]
    day=start
    while day<=end:
        days.append(day.isoformat());day+=timedelta(days=1)
    lines=service.milk_summary(start,end)
    buckets={}
    for r in lines:
        key=(r['customer_id'],r['date'])
        if key not in buckets:buckets[key]={'litres':ZERO,'amount':ZERO,'row_ids':[]}
        buckets[key]['litres']+=dec(r['quantity'])
        buckets[key]['amount']+=cash(r['amount'])
        buckets[key]['row_ids'].append(r['id'])
    frozen=db.query(BillingPeriod).filter_by(dairy_id=context.dairy.id,start_date=start,end_date=end,status='FINALIZED').first()
    frozen_bills={}
    if frozen:
        frozen_bills={b.customer_id:b.snapshot for b in db.query(CustomerBill).filter_by(dairy_id=context.dairy.id,period_id=frozen.id)}
    farmers=[]
    for farmer in db.query(Customer).filter_by(dairy_id=context.dairy.id,active=True).order_by(Customer.code).all():
        milk_total=ZERO;milk_litres=ZERO
        day_totals={}
        for day_iso in days:
            item=buckets.get((farmer.id,day_iso))
            if item:
                day_totals[day_iso]={'litres':str(item['litres']),'amount':str(item['amount']),'row_ids':item['row_ids']}
                milk_total+=item['amount'];milk_litres+=item['litres']
            else:day_totals[day_iso]={'litres':'0','amount':'0.00','row_ids':[]}
        if farmer.id in frozen_bills:
            statement=frozen_bills[farmer.id]
        elif through>=start:
            statement=service.customer_preview(farmer,start,through)
        else:statement={'payable':'0.00','payout':'0.00','carry_forward':'0.00'}
        farmers.append({'customer_id':farmer.id,'code':farmer.code,'name':farmer.name,
            'days':day_totals,'litres':str(milk_litres),'milk_amount':str(cash(milk_total)),
            'payable':str(statement['payable']), 'payout':str(statement['payout']),
            'carry_forward':str(statement['carry_forward'])})
    return {'start':str(start),'end':str(end),'as_of':str(through),'dates':days,
        'finalized':bool(frozen),'farmers':farmers,
        'total_litres':str(sum((dec(f['litres']) for f in farmers),ZERO)),
        'total_milk_amount':str(sum((dec(f['milk_amount']) for f in farmers),ZERO)),
        'total_payable':str(sum((dec(f['payout']) for f in farmers),ZERO))}


@router.get('/milk')
def milk(context:TenantDep, start:date=Query(...),end:date=Query(...),db:Session=Depends(get_db)):
    DateRange(start=start,end=end)
    rows=SettlementService(db,context.dairy.id).milk_summary(start,end)
    return {'rows':rows,'litres':str(sum((dec(r['quantity']) for r in rows),ZERO)),
            'amount':str(sum((dec(r['amount']) for r in rows),ZERO)),'start':str(start),'end':str(end)}

@router.get('/scans')
def scan_history(context:TenantDep,limit:int=Query(30,ge=1,le=100),db:Session=Depends(get_db)):
    batches=db.query(ScanBatch).filter_by(dairy_id=context.dairy.id).order_by(ScanBatch.id.desc()).limit(limit).all()
    return [{'id':b.id,'kind':b.kind,'status':b.status,'filename':b.filename,
      'date':b.created_at.isoformat() if b.created_at else None,
      'total_rows':len(b.rows),'pending_rows':sum(1 for r in b.rows if not r.approved and r.approval_status!='deleted')} for b in batches]

@router.post('/feed/scans/{batch_id}/approve')
def approve_feed_scan(batch_id:int,body:FeedScanApprove,context:OwnerManagerDep,db:Session=Depends(get_db)):
    batch=db.query(ScanBatch).filter_by(id=batch_id,dairy_id=context.dairy.id).first()
    if batch is None or batch.kind not in {'feed','CATTLE_FEED_PURCHASE'}:
        raise HTTPException(404,'Cattle-feed scan not found')
    if len(body.row_ids)!=len(set(body.row_ids)):
        raise HTTPException(422,'Duplicate row IDs in request')
    service=FeedInventoryService(db,context.dairy.id,context.user.id)
    posted=[]
    # All-or-nothing approval; never silently charge only a subset.
    try:
        for row_id in body.row_ids:
            row=db.query(ScanRow).filter_by(id=row_id,dairy_id=context.dairy.id,batch_id=batch.id).with_for_update().first()
            if row is None or row.approval_status=='deleted':raise ValueError(f'Row {row_id} is unavailable')
            if row.approved:
                posted.append({'row_id':row_id,'already_approved':True})
                continue
            if row.customer_id is None or not row.quantity or row.quantity<=0:
                raise ValueError(f'Row {row_id}: select farmer and quantity')
            fields=row.extracted_fields or {}
            def field(k):
                value=fields.get(k) or {}
                return str(value.get('corrected_value',value.get('value')) or '').strip()
            code,name=field('product_code'),field('product_name')
            query=db.query(FeedProduct).filter_by(dairy_id=context.dairy.id,active=True)
            product=query.filter(FeedProduct.product_code==code).first() if code else None
            if product is None and name:product=query.filter(func.lower(FeedProduct.product_name)==name.lower()).first()
            if product is None:raise ValueError(f'Row {row_id}: select a registered feed product before approval')
            details={'customer_id':row.customer_id,'sale_date':body.sale_date,'items':[
               {'product_id':product.id,'quantity':dec(row.quantity),'selling_price':None}],
               'idempotency_key':f'feed-scan-{batch_id}-row-{row_id}'}
            payload={**details,'items':[{'product_id':product.id,'quantity':str(dec(row.quantity)),'selling_price':None}]}
            sale,created=service.sell(details,payload_fingerprint(payload),allow_price_override=False)
            row.approved=True;row.approval_status='approved';row.approved_by=context.user.id
            row.approved_at=datetime.now(timezone.utc);row.amount=sale.total_amount
            posted.append({'row_id':row_id,'sale_id':sale.id,'amount':str(sale.total_amount),'created':created})
        if all(r.approved or r.approval_status=='deleted' for r in batch.rows):batch.status='approved'
        elif posted:batch.status='partially_approved'
        record_audit(db,context.dairy.id,context.user.id,'feed_scan_approved','scan_batch',batch.id,{'row_ids':body.row_ids})
        db.commit()
    except (FeedInventoryError,ValueError,IntegrityError) as exc:
        db.rollback()
        raise HTTPException(409,str(exc)) from exc
    return {'approved':posted,'batch_status':batch.status}

@router.post('/feed/stock/bulk')
def bulk_stock(body:BulkStockIn,context:OwnerManagerDep,db:Session=Depends(get_db)):
    service=FeedInventoryService(db,context.dairy.id,context.user.id)
    purchases=[]
    try:
        for index,item in enumerate(body.items):
            values={'product_id':item.product_id,'quantity':item.quantity,
             'purchase_rate':item.purchase_rate,'supplier':'Owner stock entry',
             'purchase_date':body.purchase_date,'invoice_reference':'',
             'idempotency_key':f'{body.idempotency_key}-{index}'}
            purchase,created=service.purchase(values,payload_fingerprint({k:str(v) for k,v in values.items()}))
            if created and item.selling_price is not None:
                product=db.query(FeedProduct).filter_by(id=item.product_id,dairy_id=context.dairy.id).with_for_update().one()
                product.selling_price=item.selling_price
                record_audit(db,context.dairy.id,context.user.id,'feed_selling_price_updated',
                    'feed_product',product.id,{'new_selling_price':str(item.selling_price),'stock_purchase_id':purchase.id})
            purchases.append({'id':purchase.id,'created':created})
        db.commit()
    except (FeedInventoryError,IntegrityError) as exc:
        db.rollback();raise HTTPException(409,str(exc)) from exc
    return {'purchases':purchases}

@router.post('/advances',status_code=201)
def give_advance(body:AdvanceIn,context:OwnerManagerDep,db:Session=Depends(get_db)):
    if not db.query(Customer).filter_by(id=body.customer_id,dairy_id=context.dairy.id,active=True).first():
        raise HTTPException(404,'Farmer not found')
    advance=Advance(dairy_id=context.dairy.id,customer_id=body.customer_id,
        principal=body.principal,outstanding=body.principal,installment=body.installment,
        advance_date=body.advance_date,note=body.note,created_by=context.user.id)
    db.add(advance);db.flush()
    LedgerService(db,context.dairy.id).post(body.customer_id,'advance_disbursed',-body.principal,
        f'Advance #{advance.id}',f'advance-{advance.id}',body.advance_date)
    record_audit(db,context.dairy.id,context.user.id,'advance_disbursed','advance',advance.id,
        {'principal':str(body.principal),'installment':str(body.installment)})
    db.commit()
    return {'id':advance.id,'outstanding':str(advance.outstanding)}

@router.get('/advances')
def advances(context:TenantDep,customer_id:int|None=None,db:Session=Depends(get_db)):
    query=db.query(Advance).filter_by(dairy_id=context.dairy.id)
    if customer_id is not None:query=query.filter_by(customer_id=customer_id)
    return [{'id':a.id,'customer_id':a.customer_id,'principal':str(a.principal),
             'outstanding':str(a.outstanding),'installment':str(a.installment),'date':str(a.advance_date)} for a in query.order_by(Advance.id.desc()).all()]

@router.get('/anamat/{customer_id}')
def anamat_balance(customer_id:int,context:TenantDep,db:Session=Depends(get_db)):
    if not db.query(Customer).filter_by(id=customer_id,dairy_id=context.dairy.id).first():raise HTTPException(404,'Farmer not found')
    entries=db.query(AnamatEntry).filter_by(dairy_id=context.dairy.id,customer_id=customer_id).order_by(AnamatEntry.id.desc()).all()
    return {'balance':str(sum((cash(e.amount) for e in entries),ZERO)),
            'entries':[{'date':str(e.entry_date),'type':e.entry_type,'amount':str(e.amount)} for e in entries]}

@router.post('/anamat/payout',status_code=201)
def anamat_payout(body:PayoutIn,context:OwnerDep,db:Session=Depends(get_db)):
    if not db.query(Customer).filter_by(dairy_id=context.dairy.id,id=body.customer_id).first():raise HTTPException(404,'Farmer not found')
    db.query(Customer).filter_by(dairy_id=context.dairy.id,id=body.customer_id).with_for_update().first()
    balance=db.query(func.coalesce(func.sum(AnamatEntry.amount),0)).filter_by(
       dairy_id=context.dairy.id,customer_id=body.customer_id).scalar()
    if cash(body.amount)>cash(balance):raise HTTPException(409,'Payout exceeds accumulated anamat')
    ref=f'anamat-payout-{uuid.uuid4().hex}'
    entry=AnamatEntry(dairy_id=context.dairy.id,customer_id=body.customer_id,
         entry_date=body.payout_date,amount=-body.amount,entry_type='payout',reference=ref,created_by=context.user.id)
    db.add(entry)
    LedgerService(db,context.dairy.id).post(body.customer_id,'anamat_payout',-body.amount,
        body.note or 'Anamat paid',ref,body.payout_date)
    db.commit()
    return {'balance':str(cash(balance)-cash(body.amount))}

@router.get('/billing/preview')
def bill_preview(context:TenantDep,start:date=Query(...),end:date=Query(...),db:Session=Depends(get_db)):
    DateRange(start=start,end=end)
    existing=db.query(BillingPeriod).filter_by(dairy_id=context.dairy.id,start_date=start,end_date=end,status='FINALIZED').first()
    if existing:
        bills=db.query(CustomerBill).filter_by(dairy_id=context.dairy.id,period_id=existing.id).all()
        snapshots=[bill.snapshot for bill in bills]
        total_keys=('litres','milk_amount','feed_amount','advance_principal','advance_interest','anamat_amount','payable')
        return {'start':str(start),'end':str(end),'bills':snapshots,
            'totals':{key:str(sum((dec(b[key]) for b in snapshots),ZERO)) for key in total_keys},
            'bill_count':len(snapshots),'status':'FINALIZED','period_id':existing.id}
    return {**SettlementService(db,context.dairy.id).term_preview(start,end),'status':'DRAFT'}

@router.get('/billing/history')
def billing_history(context:TenantDep,db:Session=Depends(get_db)):
    periods=db.query(BillingPeriod).filter_by(dairy_id=context.dairy.id).order_by(BillingPeriod.id.desc()).limit(40).all()
    return [{'id':p.id,'start':str(p.start_date),'end':str(p.end_date),'status':p.status,
      'count':db.query(CustomerBill).filter_by(dairy_id=context.dairy.id,period_id=p.id).count()} for p in periods]

@router.post('/billing/finalize')
def finalize_billing(body:DateRange,context:OwnerDep,db:Session=Depends(get_db)):
    existing=db.query(BillingPeriod).filter_by(dairy_id=context.dairy.id,start_date=body.start,end_date=body.end).first()
    if body.end > date.today():
        raise HTTPException(409,'Cannot finalize a period that has not ended yet')
    if existing and existing.status=='FINALIZED':
        return {'period_id':existing.id,'already_finalized':True}
    overlapping=db.query(BillingPeriod).filter(
        BillingPeriod.dairy_id==context.dairy.id,BillingPeriod.status=='FINALIZED',
        BillingPeriod.start_date<=body.end,BillingPeriod.end_date>=body.start).first()
    if overlapping:raise HTTPException(409,'Overlapping finalized settlement period exists')
    # All pending milk rows dated inside this period must be reviewed first.
    pending=db.query(ScanRow).join(ScanBatch).filter(
        ScanRow.dairy_id==context.dairy.id,ScanBatch.kind.in_(['milk','MILK_REGISTER']),
        ScanRow.approved.is_(False),ScanRow.approval_status!='deleted',
        ScanRow.collection_date.between(body.start,body.end)).count()
    if pending:raise HTTPException(409,f'{pending} milk entries still need approval')
    service=SettlementService(db,context.dairy.id)
    preview=service.term_preview(body.start,body.end)
    period=existing or BillingPeriod(dairy_id=context.dairy.id,start_date=body.start,end_date=body.end)
    db.add(period);db.flush()
    for item in preview['bills']:
        bill=CustomerBill(dairy_id=context.dairy.id,customer_id=item['customer_id'],
         period_id=period.id,milk_amount=dec(item['milk_amount']),feed_amount=dec(item['feed_amount']),
         advance_principal=dec(item['advance_principal']),advance_interest=dec(item['advance_interest']),
         anamat_amount=dec(item['anamat_amount']),payable=dec(item['payable']),
         snapshot=item,created_by=context.user.id)
        db.add(bill);db.flush()
        for repayment in item['repayments']:
            adv=db.query(Advance).filter_by(id=repayment['advance_id'],dairy_id=context.dairy.id).with_for_update().first()
            amount=dec(repayment['installment']); interest=dec(repayment['interest'])
            if adv is None or cash(adv.outstanding)<amount:raise HTTPException(409,'Advance changed; refresh bills')
            adv.outstanding=cash(adv.outstanding)-amount
            db.add(AdvanceRepayment(dairy_id=context.dairy.id,advance_id=adv.id,bill_id=bill.id,
                amount=amount,interest=interest))
            if amount:
                LedgerService(db,context.dairy.id).post(item['customer_id'],'advance_repayment',amount,
                    f'Advance #{adv.id} principal recovered',f'bill-{bill.id}-advance-{adv.id}-principal',body.end)
                LedgerService(db,context.dairy.id).post(item['customer_id'],'advance_installment_withheld',-amount,
                    f'Installment withheld in bill #{bill.id}',f'bill-{bill.id}-withheld-{adv.id}',body.end)
        if dec(item['advance_interest']):
            LedgerService(db,context.dairy.id).post(item['customer_id'],'advance_interest',-dec(item['advance_interest']),
                'Advance interest for settlement',f'bill-{bill.id}-interest',body.end)
        if dec(item['anamat_amount']):
            db.add(AnamatEntry(dairy_id=context.dairy.id,customer_id=item['customer_id'],
              entry_date=body.end,amount=dec(item['anamat_amount']),entry_type='withheld',
              reference=f'bill-{bill.id}-anamat',created_by=context.user.id))
            LedgerService(db,context.dairy.id).post(item['customer_id'],'anamat_withheld',-dec(item['anamat_amount']),
                'Anamat withholding',f'bill-{bill.id}-anamat',body.end)
    period.status='FINALIZED';period.finalized_at=datetime.now(timezone.utc)
    record_audit(db,context.dairy.id,context.user.id,'bills_finalized','billing_period',period.id,
      {'start':str(body.start),'end':str(body.end),'count':len(preview['bills'])})
    try:db.commit()
    except IntegrityError as exc:
        db.rollback();raise HTTPException(409,'Bills already finalized or period conflict') from exc
    return {'period_id':period.id,'bill_count':len(preview['bills']),'already_finalized':False}

@router.get('/billing/{period_id}/bills')
def finalized_bills(period_id:int,context:TenantDep,db:Session=Depends(get_db)):
    period=db.query(BillingPeriod).filter_by(id=period_id,dairy_id=context.dairy.id).first()
    if period is None:raise HTTPException(404,'Billing period not found')
    bills=db.query(CustomerBill).filter_by(period_id=period.id,dairy_id=context.dairy.id).all()
    return {'period_id':period.id,'start':str(period.start_date),'end':str(period.end_date),
            'bills':[{'id':b.id,**b.snapshot} for b in bills]}

@router.post('/dispatch',status_code=201)
def dispatch(body:DispatchIn,context:OwnerManagerDep,db:Session=Depends(get_db)):
    item=MilkDispatch(dairy_id=context.dairy.id,**body.model_dump());db.add(item);db.commit()
    return {'id':item.id}

@router.post('/expenses',status_code=201)
def expense(body:ExpenseIn,context:OwnerManagerDep,db:Session=Depends(get_db)):
    item=OperatingExpense(dairy_id=context.dairy.id,**body.model_dump());db.add(item);db.commit()
    return {'id':item.id}

@router.get('/reports')
def reports(context:TenantDep,start:date=Query(...),end:date=Query(...),db:Session=Depends(get_db)):
    DateRange(start=start,end=end)
    return SettlementService(db,context.dairy.id).business_report(start,end)

class ManualMilkIn(BaseModel):
    customer_id:int=Field(gt=0)
    collection_date:date
    session:Literal['morning','evening']
    milk_type:Literal['cow','buffalo']='cow'
    quantity:Decimal=Field(gt=0,le=200,max_digits=14,decimal_places=4)
    fat:Decimal=Field(gt=0,le=15,max_digits=8,decimal_places=4)
    snf:Decimal=Field(gt=0,le=20,max_digits=8,decimal_places=4)

@router.post('/milk/manual',status_code=201)
def manual_milk(body:ManualMilkIn,context:OwnerManagerDep,db:Session=Depends(get_db)):
    from ..services.rate_charts import MilkRateCalculationService
    customer=db.query(Customer).filter_by(dairy_id=context.dairy.id,id=body.customer_id,active=True).first()
    if customer is None:raise HTTPException(404,'Farmer not found')
    duplicate=db.query(ScanRow).join(ScanBatch).filter(
        ScanRow.dairy_id==context.dairy.id,ScanRow.customer_id==customer.id,
        ScanRow.collection_date==body.collection_date,ScanRow.session==body.session,
        ScanRow.milk_type==body.milk_type,ScanRow.approved.is_(True),
        ScanBatch.kind.in_(['milk','MILK_REGISTER'])).first()
    if duplicate:raise HTTPException(409,'An approved entry already exists for this farmer/session; review it before adding another.')
    batch=ScanBatch(dairy_id=context.dairy.id,kind='milk',filename='Manual entry',
        raw_text='',status='pending',image_layout={'manual':True})
    db.add(batch);db.flush()
    row=ScanRow(dairy_id=context.dairy.id,batch_id=batch.id,customer_id=customer.id,
        extracted_code=customer.code,extracted_name=customer.name,
        quantity=float(body.quantity),fat=float(body.fat),snf=float(body.snf),
        collection_date=body.collection_date,session=body.session,milk_type=body.milk_type,
        confidence=1.0,field_confidence={'quantity_litres':1,'fat_percent':1,'snf_percent':1,'customer_code':1},
        approval_status='pending',extracted_fields={},extraction_warnings=[])
    db.add(row);db.flush()
    calculated=MilkRateCalculationService(db,context.dairy.id).calculate_row(row,require_confidence=False)
    if calculated['calculation_status']!='CALCULATED':
        db.rollback();raise HTTPException(422,'; '.join(calculated['warnings']))
    row.amount=cash(calculated['total_amount']);row.approved=True;row.approval_status='approved'
    row.approved_by=context.user.id;row.approved_at=datetime.now(timezone.utc)
    batch.status='approved'
    LedgerService(db,context.dairy.id).post(customer.id,'milk_credit',row.amount,
        f'Milk {body.quantity}L · FAT {body.fat} · SNF {body.snf}',
        f'milk-batch-{batch.id}-row-{row.id}',body.collection_date)
    record_audit(db,context.dairy.id,context.user.id,'manual_milk_approved','scan_row',row.id,
        {'rate':str(row.rate_per_litre),'amount':str(row.amount)})
    db.commit()
    return {'id':row.id,'rate':str(row.rate_per_litre),'amount':str(row.amount)}



class CorrectApprovedMilkIn(ManualMilkIn):
    expected_version:int=Field(ge=1)

@router.put('/milk/{row_id}')
def correct_approved_milk(row_id:int, body:CorrectApprovedMilkIn,
                          context:OwnerManagerDep, db:Session=Depends(get_db)):
    """Audit an unbilled correction; append ledger adjustments, never rewrite ledger history."""
    from ..services.rate_charts import MilkRateCalculationService
    from sqlalchemy.orm.exc import StaleDataError
    row=db.query(ScanRow).join(ScanBatch).filter(
        ScanRow.id==row_id, ScanRow.dairy_id==context.dairy.id,
        ScanRow.approved.is_(True), ScanBatch.kind.in_(['milk','MILK_REGISTER'])
    ).with_for_update().first()
    if row is None: raise HTTPException(404,'Approved milk entry not found')
    if row.version != body.expected_version:
        raise HTTPException(409,'Entry was updated by another user; refresh before editing')
    for dt in [row.collection_date,body.collection_date]:
        frozen=db.query(BillingPeriod.id).filter(
            BillingPeriod.dairy_id==context.dairy.id,
            BillingPeriod.status=='FINALIZED',
            BillingPeriod.start_date<=dt,BillingPeriod.end_date>=dt).first()
        if frozen:raise HTTPException(409,'Entry belongs to a finalized bill. Use a separate authorized settlement adjustment.')
    customer=db.query(Customer).filter_by(dairy_id=context.dairy.id,id=body.customer_id,active=True).first()
    if not customer:raise HTTPException(404,'Farmer not found')
    duplicate=db.query(ScanRow.id).join(ScanBatch).filter(
        ScanRow.dairy_id==context.dairy.id,ScanRow.id!=row.id,ScanRow.approved.is_(True),
        ScanBatch.kind.in_(['milk','MILK_REGISTER']),
        ScanRow.customer_id==customer.id,ScanRow.collection_date==body.collection_date,
        ScanRow.session==body.session,ScanRow.milk_type==body.milk_type).first()
    if duplicate:raise HTTPException(409,'Another approved milk entry already exists for this farmer/session')
    old={'customer_id':row.customer_id,'collection_date':row.collection_date,
         'session':row.session,'milk_type':row.milk_type,'quantity':row.quantity,
         'fat':row.fat,'snf':row.snf,'amount':str(row.calculated_amount)}
    for key,value in body.model_dump(exclude={'expected_version'}).items():
        setattr(row,key,float(value) if key in {'quantity','fat','snf'} else value)
    # This field is called customer_id in both the request and SQLAlchemy model.
    calculated=MilkRateCalculationService(db,context.dairy.id).calculate_row(row,require_confidence=False)
    if calculated['calculation_status']!='CALCULATED':
        db.rollback();raise HTTPException(422,'; '.join(calculated['warnings']))
    new_amount=cash(row.calculated_amount); old_amount=cash(old['amount'])
    row.amount=new_amount;row.corrected_by=context.user.id;row.corrected_at=datetime.now(timezone.utc)
    ledger=LedgerService(db,context.dairy.id)
    reference=f'milk-correction-{row.id}-v{body.expected_version}'
    if old['customer_id']==row.customer_id:
        difference=new_amount-old_amount
        if difference:
            ledger.post(row.customer_id,'milk_correction',difference,
              f'Milk entry #{row.id} correction',reference,body.collection_date)
    else:
        ledger.post(old['customer_id'],'milk_correction',-old_amount,
          f'Milk entry #{row.id} moved to another farmer',reference+'-reverse',old['collection_date'])
        ledger.post(row.customer_id,'milk_correction',new_amount,
          f'Milk entry #{row.id} reassigned to farmer',reference+'-new',body.collection_date)
    for key,old_value in old.items():
        new_value=getattr(row,key) if key!='amount' else str(new_amount)
        if str(old_value)!=str(new_value):
            db.add(ScanRowCorrection(dairy_id=context.dairy.id,row_id=row.id,
               user_id=context.user.id,field_name=key,original_value=str(old_value),corrected_value=str(new_value)))
    record_audit(db,context.dairy.id,context.user.id,'approved_milk_corrected','scan_row',row.id,
      {'old':{k:str(v) for k,v in old.items()},'new_amount':str(new_amount),
       'new_customer_id':row.customer_id,'reference':reference})
    try: db.commit()
    except (IntegrityError,StaleDataError) as exc:
        db.rollback();raise HTTPException(409,'Concurrent edit or conflict; refresh and retry') from exc
    return {'id':row.id,'rate':str(row.rate_per_litre),
       'amount':str(row.calculated_amount),'version':row.version}
