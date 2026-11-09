from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import func
from ..models import LedgerEntry, Customer
from .rates import RateService

# Balance means settlement payable, not net of outstanding loan principal or
# anamat reserves. Advances and anamat have dedicated subledgers.
NON_SETTLEMENT_ENTRY_TYPES = {'advance_disbursed', 'advance_repayment', 'anamat_payout'}

class LedgerService:
    """Single responsibility: all financial postings and balance queries live here."""
    def __init__(self, db: Session, dairy_id: int):
        self.db, self.dairy_id = db, dairy_id

    def post(self, customer_id, entry_type, amount, description, reference, entry_date):
        customer = self.db.query(Customer).filter_by(
            id=customer_id, dairy_id=self.dairy_id
        ).first()
        if not customer:
            raise ValueError("Customer not found")
        amount = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        existing = self.db.query(LedgerEntry).filter_by(
            dairy_id=self.dairy_id, reference=reference
        ).first() if reference else None
        if existing:
            return existing, False
        entry = LedgerEntry(
            dairy_id=self.dairy_id, customer_id=customer_id, entry_type=entry_type,
            amount=amount, description=description, reference=reference or "",
            entry_date=entry_date,
        )
        self.db.add(entry)
        self.db.flush()
        return entry, True

    def balance(self, customer_id):
        value = self.db.query(func.coalesce(func.sum(LedgerEntry.amount), 0)).filter(
            LedgerEntry.customer_id == customer_id,
            LedgerEntry.dairy_id == self.dairy_id,
            LedgerEntry.entry_type.notin_(NON_SETTLEMENT_ENTRY_TYPES),
        ).scalar()
        return Decimal(value or 0).quantize(Decimal("0.01"))

    def statement(self, customer_id):
        customer = self.db.query(Customer).filter_by(
            id=customer_id, dairy_id=self.dairy_id
        ).first()
        if not customer:
            raise ValueError("Customer not found")
        entries = self.db.query(LedgerEntry).filter_by(
            customer_id=customer_id, dairy_id=self.dairy_id
        ).order_by(
            LedgerEntry.entry_date.desc(), LedgerEntry.id.desc()).all()
        return {"customer": customer, "entries": entries, "balance": self.balance(customer_id)}

    def approve_milk_row(self, row, batch_id, entry_date):
        if row.dairy_id != self.dairy_id:
            raise ValueError("Scan row not found")
        rate = RateService(self.db, self.dairy_id).rate_for(row.fat, row.snf, "cow")
        amount = (Decimal(str(row.quantity)) * rate).quantize(Decimal("0.01"))
        row.amount = amount
        _, created = self.post(row.customer_id, "milk_credit", amount,
            f"Milk {row.quantity} L | Fat {row.fat} | SNF {row.snf}",
            f"milk-batch-{batch_id}-row-{row.id}", entry_date)
        row.approved = True
        return amount, created

    def accrue_simple_interest(self, customer_id, principal, annual_rate_percent, days, reference, entry_date):
        interest = (Decimal(principal) * Decimal(str(annual_rate_percent)) *
                    Decimal(days) / Decimal("36500")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return self.post(customer_id, "advance_interest", -interest,
                         f"Simple interest {annual_rate_percent}% for {days} days",
                         reference, entry_date)
