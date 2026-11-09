"""Predictable finance calculations. No LLM arithmetic and no client-supplied totals."""
from __future__ import annotations
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import func
from sqlalchemy.orm import Session
from ..models import (Customer, ScanBatch, ScanRow, FeedSale, LedgerEntry,
    DairyFinancialPolicy, Advance, AnamatEntry, CustomerBill, BillingPeriod,
    FeedSaleItem, MilkDispatch, OperatingExpense)

CENT = Decimal('0.01')
ZERO = Decimal('0.00')
def dec(v): return Decimal(str(v or 0))
def cash(v): return dec(v).quantize(CENT, rounding=ROUND_HALF_UP)
def iso(d): return d.isoformat()

def term_for(day: date) -> tuple[date, date]:
    last = monthrange(day.year, day.month)[1]
    start = 1 if day.day <= 10 else 11 if day.day <= 20 else 21
    end = 10 if start == 1 else 20 if start == 11 else last
    return date(day.year, day.month, start), date(day.year, day.month, end)

class SettlementService:
    def __init__(self, db: Session, dairy_id: int):
        self.db, self.dairy_id = db, dairy_id

    def policy(self, day):
        return self.db.query(DairyFinancialPolicy).filter(
            DairyFinancialPolicy.dairy_id == self.dairy_id,
            DairyFinancialPolicy.effective_from <= day,
            (DairyFinancialPolicy.effective_to.is_(None) | (DairyFinancialPolicy.effective_to >= day)),
        ).order_by(DairyFinancialPolicy.effective_from.desc()).first()

    def customer_preview(self, customer: Customer, start: date, end: date):
        # Only approved entries contribute. Use frozen per-row amounts and rates.
        milk_rows = self.db.query(ScanRow).join(ScanBatch).filter(
            ScanRow.dairy_id == self.dairy_id, ScanRow.customer_id == customer.id,
            ScanRow.approved.is_(True), ScanBatch.kind.in_(['milk','MILK_REGISTER']),
            ScanRow.collection_date.between(start, end)).order_by(ScanRow.collection_date, ScanRow.id).all()
        milk_lines = [{
            'date': iso(r.collection_date), 'litres': str(dec(r.quantity)), 'fat': r.fat,
            'snf': r.snf, 'milk_type': r.milk_type, 'session': r.session,
            'rate': str(dec(r.rate_per_litre)), 'amount': str(cash(r.calculated_amount)),
            'scan_row_id': r.id, 'chart_id': r.rate_chart_id,
        } for r in milk_rows]
        litres = sum((dec(r.quantity) for r in milk_rows), Decimal(0))
        milk = sum((cash(r.calculated_amount) for r in milk_rows), ZERO)
        feed_sales = self.db.query(FeedSale).filter(
            FeedSale.dairy_id == self.dairy_id, FeedSale.customer_id == customer.id,
            FeedSale.sale_date.between(start,end)).all()
        feed = sum((cash(s.total_amount) for s in feed_sales), ZERO)
        feed_lines = [{'date':iso(s.sale_date),'sale_id':s.id,'amount':str(cash(s.total_amount))} for s in feed_sales]
        # Legacy manual feed purchases are debits without FeedSale.
        manual_feed = self.db.query(LedgerEntry).filter(
            LedgerEntry.dairy_id==self.dairy_id, LedgerEntry.customer_id==customer.id,
            LedgerEntry.entry_type=='feed_debit', LedgerEntry.entry_date.between(start,end),
            ~LedgerEntry.reference.like('feed-sale-%')).all()
        feed += sum((-cash(e.amount) for e in manual_feed), ZERO)
        feed_lines.extend({'date':iso(e.entry_date),'reference':e.reference,'amount':str(-cash(e.amount))} for e in manual_feed)
        policy = self.policy(end)
        anamat = ZERO
        for row in milk_rows:
            row_policy=self.policy(row.collection_date)
            if not row_policy or not row_policy.anamat_enabled: continue
            if row.milk_type == 'cow' and row_policy.anamat_cow_rate is not None: rate = row_policy.anamat_cow_rate
            elif row.milk_type == 'buffalo' and row_policy.anamat_buffalo_rate is not None: rate = row_policy.anamat_buffalo_rate
            else: rate = row_policy.anamat_rate
            anamat += cash(dec(row.quantity)*dec(rate))
        anamat = cash(anamat)
        advances = self.db.query(Advance).filter(
            Advance.dairy_id == self.dairy_id, Advance.customer_id == customer.id,
            Advance.advance_date <= end, Advance.outstanding > 0).order_by(Advance.advance_date, Advance.id).all()
        repayments=[]; principal_total=ZERO; interest_total=ZERO
        annual = dec(policy.annual_interest_rate) if policy and policy.interest_enabled else ZERO
        duration=Decimal((end-start).days+1)
        basis=Decimal(360 if policy and policy.day_count_convention == 'ACTUAL_360' else 365)
        for adv in advances:
            installment = min(cash(adv.outstanding), cash(adv.installment))
            # New advances only accrue from their actual issue date.
            effective_days=Decimal(max(0,(end-max(start,adv.advance_date)).days+1))
            interest_basis = dec(adv.principal if policy and policy.interest_method=='SIMPLE' else adv.outstanding)
            interest = cash(interest_basis * annual / Decimal(100) * effective_days / basis)
            repayments.append({'advance_id':adv.id,'installment':str(installment),
                               'interest':str(interest),'opening_principal':str(cash(adv.outstanding)),
                               'closing_principal':str(cash(dec(adv.outstanding)-installment))})
            principal_total+=installment; interest_total+=interest
        # A negative unpaid prior settlement reduces the next term's payable amount.
        previous=self.db.query(CustomerBill).join(BillingPeriod).filter(
            CustomerBill.dairy_id==self.dairy_id,CustomerBill.customer_id==customer.id,
            BillingPeriod.status=='FINALIZED',BillingPeriod.end_date < start
        ).order_by(BillingPeriod.end_date.desc()).first()
        opening_carry=cash((previous.snapshot or {}).get('carry_forward',0)) if previous else ZERO
        payable=cash(milk-feed-principal_total-interest_total-anamat-opening_carry)
        accumulated = self.db.query(func.coalesce(func.sum(AnamatEntry.amount),0)).filter(
            AnamatEntry.dairy_id == self.dairy_id, AnamatEntry.customer_id == customer.id).scalar()
        return {
            'customer_id':customer.id, 'code':customer.code,'name':customer.name,
            'phone':customer.phone,'start':iso(start),'end':iso(end),
            'litres':str(litres),'milk_amount':str(cash(milk)), 'milk_lines':milk_lines,
            'feed_amount':str(cash(feed)),'feed_lines':feed_lines,
            'advance_principal':str(cash(principal_total)), 'advance_interest':str(cash(interest_total)),
            'repayments':repayments,'anamat_amount':str(anamat),
            'anamat_balance':str(cash(accumulated)), 'opening_carry_forward':str(opening_carry),
            'payable':str(payable),
            'payout':str(max(ZERO,payable)),'carry_forward':str(max(ZERO,-payable)),
            'has_activity':bool(milk_rows or feed_lines or advances or opening_carry),
            'policy_version':policy.version if policy else None,
            'needs_attention':(len(milk_rows)==0 and bool(feed_lines))
        }

    def term_preview(self, start:date, end:date):
        customers=self.db.query(Customer).filter_by(dairy_id=self.dairy_id,active=True).order_by(Customer.code).all()
        bills=[self.customer_preview(c,start,end) for c in customers]
        return {'start':iso(start),'end':iso(end),'bills':bills,
            'totals':{key:str(sum((dec(b[key]) for b in bills),ZERO)) for key in
              ('litres','milk_amount','feed_amount','advance_principal','advance_interest','anamat_amount','payable')},
            'bill_count':len(bills)}

    def milk_summary(self,start:date,end:date):
        rows=self.db.query(ScanRow,Customer).join(ScanBatch,ScanBatch.id==ScanRow.batch_id).join(Customer,Customer.id==ScanRow.customer_id).filter(
            ScanRow.dairy_id==self.dairy_id,ScanRow.approved.is_(True),
            ScanBatch.kind.in_(['milk','MILK_REGISTER']),
            ScanRow.collection_date.between(start,end)).order_by(ScanRow.collection_date.desc(),Customer.code).all()
        return [{ 'id':r.id,'date':iso(r.collection_date),'customer_id':c.id,'code':c.code,
            'name':c.name,'quantity':r.quantity,'fat':r.fat,'snf':r.snf,'session':r.session,
            'milk_type':r.milk_type,'rate':str(dec(r.rate_per_litre)),
            'amount':str(cash(r.calculated_amount)),'chart_id':r.rate_chart_id,'version':r.version} for r,c in rows]

    def business_report(self,start,end):
        preview=self.term_preview(start,end)
        milk_cost=dec(preview['totals']['milk_amount'])
        sales=self.db.query(MilkDispatch).filter(MilkDispatch.dairy_id==self.dairy_id, MilkDispatch.dispatch_date.between(start,end)).all()
        expenses=self.db.query(OperatingExpense).filter(OperatingExpense.dairy_id==self.dairy_id,OperatingExpense.expense_date.between(start,end)).all()
        feed_items=self.db.query(FeedSaleItem).join(FeedSale).filter(
            FeedSale.dairy_id==self.dairy_id,FeedSale.sale_date.between(start,end)).all()
        feed_rev=sum((dec(x.line_total) for x in feed_items),ZERO)
        feed_cost=sum((cash(dec(x.quantity)*dec(x.purchase_cost)) for x in feed_items),ZERO)
        milk_revenue=sum((cash(x.amount) for x in sales),ZERO)
        other_cost=sum((cash(x.amount) for x in expenses),ZERO)
        return {'start':iso(start),'end':iso(end),'litres':preview['totals']['litres'],
            'milk_cost':str(milk_cost),'milk_revenue':str(milk_revenue),
            'feed_revenue':str(feed_rev),'feed_cost':str(feed_cost),
            'feed_margin':str(cash(feed_rev-feed_cost)), 'expenses':str(other_cost),
            'estimated_surplus':str(cash(milk_revenue-milk_cost+feed_rev-feed_cost-other_cost)) if sales else None,
            'complete':bool(sales),'milk_dispatch_count':len(sales),
            'note':'Estimated operating surplus, not audited profit. Enter all sales and expenses.' if sales else 'Add milk dispatch/sales to calculate margin.'}
