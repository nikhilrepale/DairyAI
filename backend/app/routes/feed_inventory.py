from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import FeedProduct
from ..schemas import (
    FeedProductIn, FeedProductUpdate, FeedSaleIn, StockAdjustmentIn, StockPurchaseIn,
)
from ..security import OwnerManagerDep, TenantDep
from ..services.audit import record_audit
from ..services.feed_inventory import (
    FeedInventoryError, FeedInventoryService, payload_fingerprint,
)

router = APIRouter(prefix="/api/feed", tags=["feed inventory"])


def _service(db: Session, context) -> FeedInventoryService:
    return FeedInventoryService(db, context.dairy.id, context.user.id)


def _commit(db: Session):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Request conflicts with existing inventory data") from exc


def _handle(error: FeedInventoryError):
    raise HTTPException(error.status_code, str(error)) from error


@router.get("/dashboard")
def inventory_dashboard(context: TenantDep, db: Session = Depends(get_db)):
    return _service(db, context).dashboard()


@router.get("/products")
def list_products(
    context: TenantDep,
    active_only: bool = True,
    db: Session = Depends(get_db),
):
    query = db.query(FeedProduct).filter_by(dairy_id=context.dairy.id)
    if active_only:
        query = query.filter(FeedProduct.active.is_(True))
    products = query.order_by(FeedProduct.product_name).all()
    return [
        FeedInventoryService._product_payload(product)
        for product in products
    ]


@router.post("/products", status_code=201)
def create_product(
    body: FeedProductIn,
    context: OwnerManagerDep,
    db: Session = Depends(get_db),
):
    try:
        product = _service(db, context).create_product(body.model_dump())
        record_audit(
            db, context.dairy.id, context.user.id, "feed_product_created",
            "feed_product", product.id, body.model_dump(mode="json"),
        )
        _commit(db)
        return {"id": product.id, "message": "Feed product created"}
    except FeedInventoryError as exc:
        db.rollback()
        _handle(exc)


@router.put("/products/{product_id}")
def update_product(
    product_id: int,
    body: FeedProductUpdate,
    context: OwnerManagerDep,
    db: Session = Depends(get_db),
):
    try:
        product = _service(db, context).update_product(product_id, body.model_dump())
        record_audit(
            db, context.dairy.id, context.user.id, "feed_product_updated",
            "feed_product", product.id, body.model_dump(mode="json"),
        )
        _commit(db)
        return {"message": "Feed product updated"}
    except FeedInventoryError as exc:
        db.rollback()
        _handle(exc)


@router.post("/purchases", status_code=201)
def add_stock_purchase(
    body: StockPurchaseIn,
    context: OwnerManagerDep,
    db: Session = Depends(get_db),
):
    payload = body.model_dump(mode="json")
    try:
        purchase, created = _service(db, context).purchase(
            body.model_dump(), payload_fingerprint(payload)
        )
        _commit(db)
        return {"id": purchase.id, "created": created, "message": "Stock purchase recorded"}
    except FeedInventoryError as exc:
        db.rollback()
        _handle(exc)


@router.get("/purchases")
def list_stock_purchases(
    context: TenantDep,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return _service(db, context).purchase_history(limit)


@router.post("/sales", status_code=201)
def create_feed_sale(
    body: FeedSaleIn,
    context: OwnerManagerDep,
    db: Session = Depends(get_db),
):
    payload = body.model_dump(mode="json")
    try:
        sale, created = _service(db, context).sell(
            body.model_dump(), payload_fingerprint(payload),
            allow_price_override=context.membership.role == "OWNER",
        )
        _commit(db)
        return {
            "created": created,
            "sale": FeedInventoryService._sale_payload(sale),
        }
    except FeedInventoryError as exc:
        db.rollback()
        _handle(exc)


@router.get("/sales")
def list_feed_sales(
    context: TenantDep,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return _service(db, context).sales_history(limit)


@router.post("/adjustments", status_code=201)
def adjust_stock(
    body: StockAdjustmentIn,
    context: OwnerManagerDep,
    db: Session = Depends(get_db),
):
    payload = body.model_dump(mode="json")
    try:
        adjustment, created = _service(db, context).adjust(
            body.model_dump(), payload_fingerprint(payload)
        )
        _commit(db)
        return {
            "id": adjustment.id,
            "created": created,
            "quantity_delta": str(adjustment.quantity_delta),
            "message": "Stock adjustment recorded",
        }
    except FeedInventoryError as exc:
        db.rollback()
        _handle(exc)


@router.get("/movements")
def stock_movements(
    context: TenantDep,
    product_id: int | None = Query(default=None, gt=0),
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    if start_date and end_date and end_date < start_date:
        raise HTTPException(400, "end_date must be on or after start_date")
    return _service(db, context).movements(product_id, start_date, end_date, limit)


@router.get("/reports/daily")
def daily_feed_sales(
    context: TenantDep,
    report_date: date | None = Query(default=None, alias="date"),
    db: Session = Depends(get_db),
):
    report_date = report_date or date.today()
    return _service(db, context).sales_report(report_date, report_date)


@router.get("/reports/monthly")
def monthly_feed_sales(
    context: TenantDep,
    year: int = Query(ge=2000, le=2200),
    month: int = Query(ge=1, le=12),
    db: Session = Depends(get_db),
):
    first = date(year, month, 1)
    if month == 12:
        last = date(year + 1, 1, 1)
    else:
        last = date(year, month + 1, 1)
    from datetime import timedelta
    return _service(db, context).sales_report(first, last - timedelta(days=1))


@router.get("/reports/product-sales")
def product_sales_report(
    context: TenantDep,
    start_date: date,
    end_date: date,
    db: Session = Depends(get_db),
):
    if end_date < start_date:
        raise HTTPException(400, "end_date must be on or after start_date")
    report = _service(db, context).sales_report(start_date, end_date)
    return {
        "start_date": report["start_date"],
        "end_date": report["end_date"],
        "product_sales": report["product_sales"],
    }


@router.get("/reports/profit")
def feed_profit_report(
    context: TenantDep,
    start_date: date,
    end_date: date,
    db: Session = Depends(get_db),
):
    if end_date < start_date:
        raise HTTPException(400, "end_date must be on or after start_date")
    report = _service(db, context).sales_report(start_date, end_date)
    return {
        "start_date": report["start_date"],
        "end_date": report["end_date"],
        "sales_total": report["total_sales"],
        "estimated_profit": report["estimated_profit"],
        "product_sales": report["product_sales"],
    }
