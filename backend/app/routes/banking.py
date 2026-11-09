"""Owner-only bank payment download. No bank-side transaction is initiated."""
import hashlib
from datetime import date
import re
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Customer, CustomerBill, BillingPeriod, BankPaymentExport
from ..security import OwnerManagerDep, OwnerDep
from ..services.audit import record_audit
from ..services.banking import (BankConfigurationError,build_payment_xlsx,
    encrypt_details,decrypt_details,masked_details,validate_details)

router=APIRouter(prefix='/api/v2/bank',tags=['secure bank details and payment exports'])

class AccountIn(BaseModel):
    beneficiary_name:str=Field(min_length=2,max_length=120)
    account_number:str=Field(min_length=6,max_length=30)
    confirm_account_number:str=Field(min_length=6,max_length=30)
    ifsc:str=Field(min_length=11,max_length=11)
    bank_name:str=Field(default='',max_length=120)
    account_type:str=Field(default='',max_length=30)
    payment_mode:str=Field(default='NEFT',pattern='^(NEFT|FT|RTGS)$')

def _decode(encrypted):
    try:return decrypt_details(encrypted)
    except BankConfigurationError as exc:raise HTTPException(503,str(exc)) from exc

def _store(body):
    try:return encrypt_details(validate_details(body.model_dump()))
    except BankConfigurationError as exc:raise HTTPException(422,str(exc)) from exc

@router.get('/dairy')
def dairy_account(context:OwnerDep):
    return masked_details(_decode(context.dairy.bank_details_encrypted))

@router.put('/dairy')
def set_dairy_account(body:AccountIn,context:OwnerDep,db:Session=Depends(get_db)):
    context.dairy.bank_details_encrypted=_store(body)
    record_audit(db,context.dairy.id,context.user.id,'dairy_bank_updated','dairy',context.dairy.id,{'has_bank_account':True})
    db.commit()
    return masked_details(_decode(context.dairy.bank_details_encrypted))

@router.get('/customers/{customer_id}')
def customer_bank(customer_id:int,context:OwnerManagerDep,db:Session=Depends(get_db)):
    farmer=db.query(Customer).filter_by(id=customer_id,dairy_id=context.dairy.id,active=True).first()
    if farmer is None: raise HTTPException(404,'Farmer not found')
    return masked_details(_decode(farmer.bank_details_encrypted))

@router.put('/customers/{customer_id}')
def set_customer_bank(customer_id:int,body:AccountIn,context:OwnerManagerDep,db:Session=Depends(get_db)):
    farmer=db.query(Customer).filter_by(id=customer_id,dairy_id=context.dairy.id,active=True).first()
    if farmer is None:raise HTTPException(404,'Farmer not found')
    farmer.bank_details_encrypted=_store(body)
    record_audit(db,context.dairy.id,context.user.id,'farmer_bank_updated','customer',customer_id,{'has_bank_account':True})
    db.commit()
    return masked_details(_decode(farmer.bank_details_encrypted))

def _mobile(value):
    digits=re.sub(r'\D','',value or '')
    if len(digits)==12 and digits.startswith('91'):
        digits=digits[2:]
    return digits if len(digits)==10 else ''


def _prepare(db,dairy,period_id):
    period=db.query(BillingPeriod).filter_by(id=period_id,dairy_id=dairy.id,status='FINALIZED').first()
    if period is None:raise HTTPException(409,'Finalize this billing period before generating bank payments')
    bills=db.query(CustomerBill,Customer).join(Customer,Customer.id==CustomerBill.customer_id).filter(
        CustomerBill.dairy_id==dairy.id,CustomerBill.period_id==period.id,
        Customer.dairy_id==dairy.id).order_by(Customer.code).all()
    missing=[];rows=[]
    source=_decode(dairy.bank_details_encrypted)
    for bill,customer in bills:
        amount=Decimal(str((bill.snapshot or {}).get('payout',max(Decimal('0'),bill.payable))))
        if amount <= 0:continue
        dest=_decode(customer.bank_details_encrypted)
        if not dest:
            missing.append({'customer_id':customer.id,'code':customer.code,'name':customer.name,
                'reason':'Bank details missing'})
            continue
        if not source:continue
        rows.append({'debit_account':source['account_number'],'name':dest['beneficiary_name'],
            'account':dest['account_number'],'ifsc':dest['ifsc'],'amount':amount,
            'payment_mode':dest.get('payment_mode','NEFT'),
            'mobile':_mobile(customer.phone), 'code':customer.code,'bill_id':bill.id})
    if not source:
        missing.insert(0,{'customer_id':None,'code':'DAIRY','name':dairy.name,'reason':'Set dairy debit account'})
    return period,rows,missing

@router.get('/billing/{period_id}/preview')
def payment_preview(period_id:int,context:OwnerDep,payment_date:date|None=Query(None),db:Session=Depends(get_db)):
    period,rows,missing=_prepare(db,context.dairy,period_id)
    total=db.query(CustomerBill).filter_by(dairy_id=context.dairy.id,period_id=period.id).all()
    eligible=sum(1 for x in total if Decimal(str((x.snapshot or {}).get('payout',0)))>0)
    last=db.query(BankPaymentExport).filter_by(dairy_id=context.dairy.id,period_id=period.id).order_by(BankPaymentExport.id.desc()).first()
    return {'period_id':period.id,'start':str(period.start_date),'end':str(period.end_date),
        'eligible':eligible,'ready':len(rows),'missing':missing,
        'total':str(sum((r['amount'] for r in rows),Decimal('0.00'))),
        'exported_before':last is not None,
        'format':'ICICI_PAB_VENDOR_19_COLUMNS_SAMPLE_MATCH','payment_date':str(payment_date or date.today())}

@router.post('/billing/{period_id}/export')
def download_bank_xlsx(period_id:int,context:OwnerDep,payment_date:date|None=Query(None),db:Session=Depends(get_db)):
    period,rows,missing=_prepare(db,context.dairy,period_id)
    if missing:raise HTTPException(409,{'message':'Complete bank details before exporting the full batch','missing':missing})
    if not rows:raise HTTPException(409,'No positive-payable farmers in this finalized period')
    selected_date=payment_date or date.today()
    data=build_payment_xlsx(rows,selected_date)
    item=BankPaymentExport(dairy_id=context.dairy.id,period_id=period.id,created_by=context.user.id,
        beneficiary_count=len(rows),total_amount=sum((r['amount'] for r in rows),Decimal('0.00')),
        content_sha256=hashlib.sha256(data).hexdigest())
    db.add(item);db.flush()
    record_audit(db,context.dairy.id,context.user.id,'bank_batch_downloaded','bank_payment_export',item.id,
        {'period_id':period.id,'count':len(rows),'amount':str(item.total_amount),'format':'ICICI_PAB_VENDOR_19_COLUMNS','payment_date':str(selected_date)})
    db.commit()
    return Response(content=data,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={
        'Content-Disposition':f'attachment; filename="ICICI_PAB_Payments_{period.start_date}_{period.end_date}_pay_{selected_date}.xlsx"',
        'Cache-Control':'no-store','Pragma':'no-cache','X-Content-Type-Options':'nosniff'})
