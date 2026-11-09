from sqlalchemy import (
    CheckConstraint, Column, Date, DateTime, Float, ForeignKey, Index, Integer, Boolean, LargeBinary,
    JSON, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base
from sqlalchemy import event

class Dairy(Base):
    __tablename__ = "dairies"
    id = Column(Integer, primary_key=True)
    name = Column(String(160), nullable=False)
    dairy_code = Column(String(40), nullable=False)
    address = Column(String(255), default="")
    village = Column(String(100), default="")
    phone = Column(String(30), default="")
    preferred_language = Column(String(10), default="en", nullable=False)
    bank_details_encrypted = Column(Text, nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("dairy_code", name="uq_dairies_dairy_code"),
    )

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    phone = Column(String(30), nullable=True)
    email = Column(String(255), nullable=True)
    password_hash = Column(String(255), nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    memberships = relationship("DairyMembership", back_populates="user")
    __table_args__ = (
        UniqueConstraint("phone", name="uq_users_phone"),
        UniqueConstraint("email", name="uq_users_email"),
    )

class DairyMembership(Base):
    __tablename__ = "dairy_memberships"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    user = relationship("User", back_populates="memberships")
    dairy = relationship("Dairy")
    __table_args__ = (
        UniqueConstraint("dairy_id", "user_id", name="uq_dairy_membership_user"),
        CheckConstraint("role IN ('OWNER', 'MANAGER', 'OPERATOR')", name="ck_dairy_membership_role"),
    )

class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    code = Column(String(30), nullable=False)
    name = Column(String(120), nullable=False, index=True)
    phone = Column(String(20), default="")
    village = Column(String(100), default="")
    bank_details_encrypted = Column(Text, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    ledger = relationship("LedgerEntry", back_populates="customer")
    __table_args__ = (UniqueConstraint("dairy_id", "code", name="uq_customer_dairy_code"),)

class RateRule(Base):
    __tablename__ = "rate_rules"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    milk_type = Column(String(20), default="cow")
    fat_min = Column(Float, nullable=False)
    fat_max = Column(Float, nullable=False)
    base_rate = Column(Numeric(10,2), nullable=False)
    per_fat_rate = Column(Numeric(10,2), default=0)
    snf_bonus = Column(Numeric(10,2), default=0)

class MilkRateChart(Base):
    __tablename__ = "milk_rate_charts"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    name = Column(String(120), nullable=False)
    milk_type = Column(String(20), nullable=False)
    chart_type = Column(String(24), nullable=False)
    status = Column(String(12), nullable=False, default="DRAFT")
    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date, nullable=True)
    fixed_rate = Column(Numeric(12, 4), nullable=True)
    fat_increment = Column(Numeric(8, 4), nullable=True)
    snf_increment = Column(Numeric(8, 4), nullable=True)
    match_policy = Column(String(32), nullable=False, default="EXACT_MATCH")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    entries = relationship(
        "MilkRateChartEntry", back_populates="chart", cascade="all, delete-orphan",
        order_by="MilkRateChartEntry.id",
    )
    __table_args__ = (
        UniqueConstraint("dairy_id", "milk_type", "version", name="uq_milk_rate_chart_version"),
        CheckConstraint("milk_type IN ('cow', 'buffalo')", name="ck_milk_rate_chart_type"),
        CheckConstraint(
            "chart_type IN ('FIXED_RATE', 'FAT_ONLY', 'FAT_SNF_MATRIX')",
            name="ck_milk_rate_chart_chart_type",
        ),
        CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'RETIRED')", name="ck_milk_rate_chart_status"),
        CheckConstraint(
            "match_policy IN ('EXACT_MATCH', 'NEAREST_LOWER', 'NEAREST', "
            "'BILINEAR_INTERPOLATION', 'INTERPOLATE')",
            name="ck_milk_rate_chart_policy",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_milk_rate_chart_dates",
        ),
    )

class MilkRateChartEntry(Base):
    __tablename__ = "milk_rate_chart_entries"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    chart_id = Column(Integer, ForeignKey("milk_rate_charts.id"), nullable=False, index=True)
    fat_value = Column(Numeric(8, 4), nullable=True)
    snf_value = Column(Numeric(8, 4), nullable=True)
    rate = Column(Numeric(12, 4), nullable=False)
    chart = relationship("MilkRateChart", back_populates="entries")
    __table_args__ = (
        UniqueConstraint("chart_id", "fat_value", "snf_value", name="uq_milk_rate_chart_entry"),
        CheckConstraint("rate >= 0", name="ck_milk_rate_chart_entry_rate"),
    )

class DairyFinancialPolicy(Base):
    __tablename__ = "dairy_financial_policies"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date, nullable=True)
    anamat_enabled = Column(Boolean, nullable=False, default=False)
    anamat_rate = Column(Numeric(12, 4), nullable=False, default=0)
    anamat_cow_rate = Column(Numeric(12, 4), nullable=True)
    anamat_buffalo_rate = Column(Numeric(12, 4), nullable=True)
    interest_enabled = Column(Boolean, nullable=False, default=False)
    annual_interest_rate = Column(Numeric(8, 4), nullable=False, default=0)
    interest_period = Column(String(24), nullable=False, default="MONTHLY")
    interest_method = Column(String(24), nullable=False, default="SIMPLE")
    day_count_convention = Column(String(16), nullable=False, default="ACTUAL_365")
    billing_term_days = Column(Integer, nullable=False, default=10)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("dairy_id", "version", name="uq_dairy_financial_policy_version"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_dairy_financial_policy_dates",
        ),
        CheckConstraint(
            "interest_period IN ('MONTHLY', 'PER_BILLING_TERM')",
            name="ck_dairy_financial_policy_interest_period",
        ),
        CheckConstraint(
            "interest_method IN ('SIMPLE', 'REDUCING_BALANCE')",
            name="ck_dairy_financial_policy_interest_method",
        ),
        CheckConstraint(
            "day_count_convention IN ('ACTUAL_365', 'ACTUAL_360')",
            name="ck_dairy_financial_policy_day_count",
        ),
        CheckConstraint(
            "annual_interest_rate >= 0 AND billing_term_days > 0",
            name="ck_dairy_financial_policy_values",
        ),
        CheckConstraint(
            "(anamat_cow_rate IS NULL OR anamat_cow_rate >= 0) AND "
            "(anamat_buffalo_rate IS NULL OR anamat_buffalo_rate >= 0) AND anamat_rate >= 0",
            name="ck_dairy_financial_policy_anamat",
        ),
    )

class ScanBatch(Base):
    __tablename__ = "scan_batches"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    kind = Column(String(30), nullable=False)
    filename = Column(String(255), default="")
    raw_text = Column(Text, default="")
    image_layout = Column(JSON, nullable=True)
    original_image = Column(LargeBinary, nullable=True)
    image_media_type = Column(String(40), nullable=True)
    status = Column(String(20), default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    rows = relationship("ScanRow", back_populates="batch", cascade="all, delete-orphan")

class ScanRow(Base):
    __tablename__ = "scan_rows"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    batch_id = Column(Integer, ForeignKey("scan_batches.id"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    extracted_name = Column(String(120), default="")
    extracted_code = Column(String(30), default="")
    quantity = Column(Float, default=0)
    fat = Column(Float, default=0)
    snf = Column(Float, default=0)
    amount = Column(Numeric(10,2), default=0)
    description = Column(String(255), default="")
    confidence = Column(Float, default=0)
    approved = Column(Boolean, default=False)
    original_customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    original_quantity = Column(Numeric(14, 4), nullable=True)
    original_fat = Column(Numeric(8, 4), nullable=True)
    original_snf = Column(Numeric(8, 4), nullable=True)
    extracted_fields = Column(JSON, nullable=False, default=dict)
    field_confidence = Column(JSON, nullable=False, default=dict)
    extraction_warnings = Column(JSON, nullable=False, default=list)
    model_row_confidence = Column(Float, nullable=True)
    duplicate_warning = Column(String(255), nullable=True)
    collection_date = Column(Date, nullable=True)
    session = Column(String(12), nullable=True)
    milk_type = Column(String(20), nullable=True)
    rate_chart_id = Column(Integer, ForeignKey("milk_rate_charts.id"), nullable=True, index=True)
    rate_per_litre = Column(Numeric(12, 4), nullable=True)
    calculated_amount = Column(Numeric(12, 2), nullable=True)
    calculation_status = Column(String(24), nullable=False, default="NEEDS_REVIEW")
    calculation_method = Column(String(32), nullable=True)
    calculation_policy = Column(String(32), nullable=True)
    calculation_warnings = Column(JSON, nullable=False, default=list)
    calculated_at = Column(DateTime(timezone=True), nullable=True)
    approval_status = Column(String(24), nullable=False, default="pending")
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    corrected_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    corrected_at = Column(DateTime(timezone=True), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    batch = relationship("ScanBatch", back_populates="rows")
    __table_args__ = (
        CheckConstraint(
            "session IS NULL OR session IN ('morning', 'evening')",
            name="ck_scan_rows_session",
        ),
        CheckConstraint(
            "milk_type IS NULL OR milk_type IN ('cow', 'buffalo')",
            name="ck_scan_rows_milk_type",
        ),
        Index("ix_scan_rows_approval_status", "dairy_id", "approval_status"),
    )
    __mapper_args__ = {"version_id_col": version}

class ScanRowCorrection(Base):
    __tablename__ = "scan_row_corrections"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    row_id = Column(Integer, ForeignKey("scan_rows.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    field_name = Column(String(40), nullable=False)
    original_value = Column(JSON, nullable=True)
    corrected_value = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    entry_type = Column(String(40), nullable=False)
    amount = Column(Numeric(12,2), nullable=False) # signed: credit positive, debit negative
    description = Column(String(255), default="")
    reference = Column(String(80), default="", index=True)
    entry_date = Column(Date, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    customer = relationship("Customer", back_populates="ledger")
    __table_args__ = (UniqueConstraint("dairy_id", "reference", name="uq_ledger_dairy_reference"),)

class InventoryItem(Base):
    __tablename__ = "inventory_items"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    item_code = Column(String(40), nullable=False)
    name = Column(String(160), nullable=False)
    unit = Column(String(30), default="unit", nullable=False)
    quantity = Column(Numeric(12, 3), default=0, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("dairy_id", "item_code", name="uq_inventory_dairy_code"),)

class FeedProduct(Base):
    __tablename__ = "feed_products"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    product_name = Column(String(160), nullable=False)
    product_code = Column(String(40), nullable=False)
    category = Column(String(80), default="", nullable=False)
    unit = Column(String(10), nullable=False)
    purchase_price = Column(Numeric(12, 2), nullable=False, default=0)
    selling_price = Column(Numeric(12, 2), nullable=False, default=0)
    low_stock_threshold = Column(Numeric(14, 3), nullable=False, default=0)
    stock_quantity = Column(Numeric(14, 3), nullable=False, default=0)
    average_cost = Column(Numeric(12, 2), nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("dairy_id", "product_code", name="uq_feed_product_dairy_code"),
        CheckConstraint("unit IN ('KG', 'BAG', 'TON', 'LITRE')", name="ck_feed_product_unit"),
        CheckConstraint("purchase_price >= 0 AND selling_price >= 0", name="ck_feed_product_prices"),
        CheckConstraint("low_stock_threshold >= 0", name="ck_feed_product_threshold"),
        CheckConstraint("stock_quantity >= 0", name="ck_feed_product_stock"),
        CheckConstraint("average_cost >= 0", name="ck_feed_product_average_cost"),
    )

class StockPurchase(Base):
    __tablename__ = "stock_purchases"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("feed_products.id"), nullable=False, index=True)
    quantity = Column(Numeric(14, 3), nullable=False)
    purchase_rate = Column(Numeric(12, 2), nullable=False)
    supplier = Column(String(160), nullable=False)
    purchase_date = Column(Date, nullable=False)
    invoice_reference = Column(String(100), default="", nullable=False)
    idempotency_key = Column(String(100), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("dairy_id", "idempotency_key", name="uq_stock_purchase_idempotency"),
        CheckConstraint("quantity > 0 AND purchase_rate >= 0", name="ck_stock_purchase_values"),
    )

class FeedSale(Base):
    __tablename__ = "feed_sales"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    sale_date = Column(Date, nullable=False)
    total_amount = Column(Numeric(12, 2), nullable=False)
    idempotency_key = Column(String(100), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    customer = relationship("Customer")
    items = relationship("FeedSaleItem", back_populates="sale", cascade="all, delete-orphan")
    __table_args__ = (
        UniqueConstraint("dairy_id", "idempotency_key", name="uq_feed_sale_idempotency"),
        CheckConstraint("total_amount >= 0", name="ck_feed_sale_total"),
    )

class FeedSaleItem(Base):
    __tablename__ = "feed_sale_items"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    sale_id = Column(Integer, ForeignKey("feed_sales.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("feed_products.id"), nullable=False, index=True)
    quantity = Column(Numeric(14, 3), nullable=False)
    selling_price = Column(Numeric(12, 2), nullable=False)
    purchase_cost = Column(Numeric(12, 2), nullable=False)
    line_total = Column(Numeric(12, 2), nullable=False)
    sale = relationship("FeedSale", back_populates="items")
    product = relationship("FeedProduct")
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_feed_sale_item_quantity"),
        CheckConstraint(
            "selling_price >= 0 AND purchase_cost >= 0 AND line_total >= 0",
            name="ck_feed_sale_item_prices",
        ),
    )

class StockAdjustment(Base):
    __tablename__ = "stock_adjustments"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("feed_products.id"), nullable=False, index=True)
    quantity_delta = Column(Numeric(14, 3), nullable=False)
    reason = Column(String(30), nullable=False)
    note = Column(String(255), nullable=False)
    adjustment_date = Column(Date, nullable=False)
    idempotency_key = Column(String(100), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("dairy_id", "idempotency_key", name="uq_stock_adjustment_idempotency"),
        CheckConstraint(
            "reason IN ('damaged', 'expired', 'physical_correction', "
            "'supplier_return', 'customer_return')",
            name="ck_stock_adjustment_reason",
        ),
        CheckConstraint("quantity_delta != 0", name="ck_stock_adjustment_quantity"),
    )

class StockMovement(Base):
    __tablename__ = "stock_movements"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("feed_products.id"), nullable=False, index=True)
    movement_type = Column(String(30), nullable=False)
    quantity_delta = Column(Numeric(14, 3), nullable=False)
    unit_cost = Column(Numeric(12, 2), nullable=False, default=0)
    reference_type = Column(String(30), nullable=False)
    reference_id = Column(Integer, nullable=False)
    reason = Column(String(30), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    product = relationship("FeedProduct")
    __table_args__ = (
        CheckConstraint("quantity_delta != 0", name="ck_stock_movement_quantity"),
        CheckConstraint(
            "movement_type IN ('purchase', 'sale', 'adjustment', 'opening_balance')",
            name="ck_stock_movement_type",
        ),
        CheckConstraint("unit_cost >= 0", name="ck_stock_movement_cost"),
    )

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey("dairies.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    action = Column(String(60), nullable=False)
    entity_type = Column(String(60), nullable=False)
    entity_id = Column(String(80), nullable=False)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _immutable_stock_movement(_mapper, _connection, target):
    raise ValueError(f"Stock movements are immutable (movement id {target.id})")


event.listen(StockMovement, "before_update", _immutable_stock_movement)
event.listen(StockMovement, "before_delete", _immutable_stock_movement)

# V2: billing and settlement are append-only records. Milk/stock postings remain in
# the existing ledger; bills snapshot the exact inputs at finalization.
class Advance(Base):
    __tablename__ = 'advances'
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey('dairies.id'), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=False, index=True)
    principal = Column(Numeric(12, 2), nullable=False)
    outstanding = Column(Numeric(12, 2), nullable=False)
    installment = Column(Numeric(12, 2), nullable=False)
    advance_date = Column(Date, nullable=False)
    note = Column(String(255), default='', nullable=False)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint('principal > 0 AND outstanding >= 0 AND installment >= 0', name='ck_advance_amounts'),)

class AdvanceRepayment(Base):
    __tablename__ = 'advance_repayments'
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey('dairies.id'), nullable=False, index=True)
    advance_id = Column(Integer, ForeignKey('advances.id'), nullable=False, index=True)
    bill_id = Column(Integer, ForeignKey('customer_bills.id'), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    interest = Column(Numeric(12, 2), nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint('advance_id', 'bill_id', name='uq_advance_bill_repayment'),)

class AnamatEntry(Base):
    __tablename__ = 'anamat_entries'
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey('dairies.id'), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=False, index=True)
    entry_date = Column(Date, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)  # positive withheld/owed; negative payout
    entry_type = Column(String(20), nullable=False)
    reference = Column(String(100), nullable=False)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint('dairy_id', 'reference', name='uq_anamat_reference'),)

class BillingPeriod(Base):
    __tablename__ = 'billing_periods'
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey('dairies.id'), nullable=False, index=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    status = Column(String(16), nullable=False, default='DRAFT')
    finalized_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint('dairy_id', 'start_date', 'end_date', name='uq_billing_term'), CheckConstraint('end_date >= start_date', name='ck_billing_dates'))

class CustomerBill(Base):
    __tablename__ = 'customer_bills'
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey('dairies.id'), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=False, index=True)
    period_id = Column(Integer, ForeignKey('billing_periods.id'), nullable=False)
    status = Column(String(16), nullable=False, default='FINALIZED')
    milk_amount = Column(Numeric(12, 2), nullable=False)
    feed_amount = Column(Numeric(12, 2), nullable=False)
    advance_principal = Column(Numeric(12, 2), nullable=False)
    advance_interest = Column(Numeric(12, 2), nullable=False)
    anamat_amount = Column(Numeric(12, 2), nullable=False)
    payable = Column(Numeric(12, 2), nullable=False)
    snapshot = Column(JSON, nullable=False)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint('dairy_id', 'customer_id', 'period_id', name='uq_customer_term_bill'),)

class MilkDispatch(Base):
    __tablename__ = 'milk_dispatches'
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey('dairies.id'), nullable=False, index=True)
    dispatch_date = Column(Date, nullable=False)
    litres = Column(Numeric(14, 3), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    buyer = Column(String(160), default='', nullable=False)
    __table_args__ = (CheckConstraint('litres > 0 AND amount >= 0', name='ck_dispatch_values'),)

class OperatingExpense(Base):
    __tablename__ = 'operating_expenses'
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey('dairies.id'), nullable=False, index=True)
    expense_date = Column(Date, nullable=False)
    category = Column(String(60), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    note = Column(String(255), default='', nullable=False)
    __table_args__ = (CheckConstraint('amount > 0', name='ck_expense_amount'),)


class BankPaymentExport(Base):
    """A downloaded file is not proof of payment."""
    __tablename__ = 'bank_payment_exports'
    id = Column(Integer, primary_key=True)
    dairy_id = Column(Integer, ForeignKey('dairies.id'), nullable=False, index=True)
    period_id = Column(Integer, ForeignKey('billing_periods.id'), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    beneficiary_count = Column(Integer, nullable=False)
    total_amount = Column(Numeric(14, 2), nullable=False)
    content_sha256 = Column(String(64), nullable=False)
