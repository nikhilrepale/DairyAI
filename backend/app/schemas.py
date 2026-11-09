from pydantic import BaseModel, Field, model_validator
from typing import Optional, Literal
from datetime import date
from decimal import Decimal

class CustomerIn(BaseModel):
    code: str
    name: str
    phone: str = ""
    village: str = ""
    bank_details: Optional[dict] = None

class ScanRowEdit(BaseModel):
    expected_version: Optional[int] = Field(default=None, ge=1)
    customer_id: Optional[int] = None
    quantity: Optional[float] = Field(default=None, gt=0)
    fat: Optional[float] = Field(default=None, gt=0, le=15)
    snf: Optional[float] = Field(default=None, gt=0, le=20)
    collection_date: Optional[date] = None
    session: Optional[Literal["morning", "evening"]] = None
    milk_type: Optional[Literal["cow", "buffalo"]] = None
    product_code: Optional[str] = Field(default=None, max_length=40)
    product_name: Optional[str] = Field(default=None, max_length=160)
    unit: Optional[str] = Field(default=None, max_length=20)
    written_amount: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    description: Optional[str] = Field(default=None, max_length=255)

class ScanRowCreate(BaseModel):
    customer_id: Optional[int] = None
    quantity: Optional[float] = Field(default=None, gt=0)
    fat: Optional[float] = Field(default=None, gt=0, le=15)
    snf: Optional[float] = Field(default=None, gt=0, le=20)
    collection_date: Optional[date] = None
    session: Optional[Literal["morning", "evening"]] = None
    milk_type: Optional[Literal["cow", "buffalo"]] = None
    product_code: Optional[str] = Field(default=None, max_length=40)
    product_name: Optional[str] = Field(default=None, max_length=160)
    unit: Optional[str] = Field(default=None, max_length=20)
    written_amount: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    notes: Optional[str] = Field(default=None, max_length=255)


class ApproveBatch(BaseModel):
    row_ids: list[int]

class RateIn(BaseModel):
    milk_type: str = "cow"
    fat_min: float
    fat_max: float
    base_rate: Decimal
    per_fat_rate: Decimal = Decimal("0")
    snf_bonus: Decimal = Decimal("0")


class MilkRateChartEntryIn(BaseModel):
    fat_value: Optional[Decimal] = Field(default=None, ge=0, max_digits=8, decimal_places=4)
    snf_value: Optional[Decimal] = Field(default=None, ge=0, max_digits=8, decimal_places=4)
    rate: Decimal = Field(ge=0, max_digits=12, decimal_places=4)


class MilkRateChartIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    milk_type: Literal["cow", "buffalo"]
    chart_type: Literal["FIXED_RATE", "FAT_ONLY", "FAT_SNF_MATRIX"]
    effective_from: date
    effective_to: Optional[date] = None
    fixed_rate: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=4)
    fat_increment: Optional[Decimal] = Field(default=None, gt=0, max_digits=8, decimal_places=4)
    snf_increment: Optional[Decimal] = Field(default=None, gt=0, max_digits=8, decimal_places=4)
    match_policy: Literal[
        "EXACT_MATCH", "NEAREST_LOWER", "NEAREST", "BILINEAR_INTERPOLATION",
        "INTERPOLATE"
    ] = "EXACT_MATCH"
    entries: list[MilkRateChartEntryIn] = Field(default_factory=list, max_length=5000)


class DairyFinancialPolicyIn(BaseModel):
    effective_from: date
    effective_to: Optional[date] = None
    anamat_enabled: bool = False
    anamat_rate: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=4)
    anamat_cow_rate: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=4)
    anamat_buffalo_rate: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=4)
    interest_enabled: bool = False
    annual_interest_rate: Decimal = Field(default=Decimal("0"), ge=0, max_digits=8, decimal_places=4)
    interest_period: Literal["MONTHLY", "PER_BILLING_TERM"] = "MONTHLY"
    interest_method: Literal["SIMPLE", "REDUCING_BALANCE"] = "SIMPLE"
    day_count_convention: Literal["ACTUAL_365", "ACTUAL_360"] = "ACTUAL_365"
    billing_term_days: int = Field(default=10, ge=1, le=31)

    @model_validator(mode="after")
    def validate_policy_dates(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be before effective_from")
        if (
            self.anamat_enabled
            and self.anamat_rate == 0
            and self.anamat_cow_rate in (None, Decimal("0"))
            and self.anamat_buffalo_rate in (None, Decimal("0"))
        ):
            raise ValueError("An enabled anamat policy needs a positive per-litre rate")
        return self


class MilkRatePreviewIn(BaseModel):
    milk_type: Literal["cow", "buffalo"]
    collection_date: date
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    fat: Optional[Decimal] = Field(default=None, ge=0, max_digits=8, decimal_places=4)
    snf: Optional[Decimal] = Field(default=None, ge=0, max_digits=8, decimal_places=4)


class MilkEntryCalculationIn(BaseModel):
    customer_id: Optional[int] = Field(default=None, gt=0)
    milk_type: Literal["cow", "buffalo"]
    quantity: Optional[Decimal] = Field(default=None, gt=0, le=200, max_digits=14, decimal_places=4)
    fat: Optional[Decimal] = Field(default=None, gt=0, le=15, max_digits=8, decimal_places=4)
    snf: Optional[Decimal] = Field(default=None, gt=0, le=20, max_digits=8, decimal_places=4)
    collection_date: date
    chart_version_id: Optional[int] = Field(default=None, gt=0)


class TransactionIn(BaseModel):
    customer_id: int
    entry_type: str
    amount: Decimal = Field(gt=0)
    description: str = ""
    entry_date: date
    interest_rate: Optional[float] = None

class AssistantIn(BaseModel):
    query: str

class RegisterDairyIn(BaseModel):
    dairy_name: str = Field(min_length=2, max_length=160)
    dairy_code: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    name: str = Field(min_length=2, max_length=120)
    email: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=30)
    password: str = Field(min_length=12, max_length=256)
    address: str = Field(default="", max_length=255)
    village: str = Field(default="", max_length=100)
    preferred_language: str = Field(default="en", max_length=10)

    @model_validator(mode="after")
    def require_contact(self):
        if not (self.email and self.email.strip()) and not (self.phone and self.phone.strip()):
            raise ValueError("Provide at least an email address or phone number")
        if self.email:
            self.email = self.email.strip().lower() or None
        if self.phone:
            self.phone = self.phone.strip() or None
        return self

class LoginIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)
    dairy_code: Optional[str] = Field(default=None, max_length=40)

class SelectDairyIn(BaseModel):
    dairy_code: str = Field(min_length=3, max_length=40)

class InventoryItemIn(BaseModel):
    item_code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    unit: str = Field(default="unit", max_length=30)
    quantity: Decimal

FeedUnit = Literal["KG", "BAG", "TON", "LITRE"]
StockAdjustmentReason = Literal[
    "damaged", "expired", "physical_correction", "supplier_return", "customer_return"
]


class FeedProductIn(BaseModel):
    product_name: str = Field(min_length=1, max_length=160)
    product_code: str = Field(min_length=1, max_length=40)
    category: str = Field(default="", max_length=80)
    unit: FeedUnit
    purchase_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    selling_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    low_stock_threshold: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=3)


class FeedProductUpdate(BaseModel):
    product_name: str = Field(min_length=1, max_length=160)
    product_code: str = Field(min_length=1, max_length=40)
    category: str = Field(default="", max_length=80)
    unit: FeedUnit
    purchase_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    selling_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    low_stock_threshold: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=3)
    active: bool


class StockPurchaseIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    purchase_rate: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    supplier: str = Field(min_length=1, max_length=160)
    purchase_date: date
    invoice_reference: str = Field(default="", max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=100)


class FeedSaleItemIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    selling_price: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)


class FeedSaleIn(BaseModel):
    customer_id: int = Field(gt=0)
    sale_date: date
    idempotency_key: str = Field(min_length=8, max_length=100)
    items: list[FeedSaleItemIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_products(self):
        ids = [item.product_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("A feed product can appear only once in a sale")
        return self


class StockAdjustmentIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity_delta: Decimal = Field(max_digits=14, decimal_places=3)
    reason: StockAdjustmentReason
    note: str = Field(min_length=1, max_length=255)
    adjustment_date: date
    idempotency_key: str = Field(min_length=8, max_length=100)

class MembershipIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=255)
    role: Literal["MANAGER", "OPERATOR"]
