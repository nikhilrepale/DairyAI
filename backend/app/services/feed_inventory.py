import hashlib
import json
import logging
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..models import (
    AuditLog, Customer, FeedProduct, FeedSale, FeedSaleItem, LedgerEntry,
    StockAdjustment, StockMovement, StockPurchase,
)

logger = logging.getLogger(__name__)
CENT = Decimal("0.01")
THREE_PLACES = Decimal("0.001")


class FeedInventoryError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def payload_fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _quantity(value: Decimal) -> Decimal:
    return Decimal(value).quantize(THREE_PLACES, rounding=ROUND_HALF_UP)


class FeedInventoryService:
    """Transactional feed inventory workflows and dairy-scoped reports."""

    def __init__(self, db: Session, dairy_id: int, user_id: int):
        self.db = db
        self.dairy_id = dairy_id
        self.user_id = user_id

    def create_product(self, values: dict) -> FeedProduct:
        product = FeedProduct(dairy_id=self.dairy_id, **values)
        self.db.add(product)
        try:
            self.db.flush()
        except IntegrityError as exc:
            raise FeedInventoryError("Product code already exists in this dairy", 409) from exc
        return product

    def update_product(self, product_id: int, values: dict) -> FeedProduct:
        product = self._product(product_id, lock=True, active=False)
        if values.get("active") is False and product.stock_quantity > 0:
            raise FeedInventoryError(
                "A product with available stock cannot be deactivated", 409
            )
        if values.get("unit") != product.unit and product.stock_quantity > 0:
            raise FeedInventoryError(
                "A product unit cannot change while stock is available", 409
            )
        for field, value in values.items():
            setattr(product, field, value)
        try:
            self.db.flush()
        except IntegrityError as exc:
            raise FeedInventoryError("Product code already exists in this dairy", 409) from exc
        return product

    def purchase(self, values: dict, payload_hash: str) -> tuple[StockPurchase, bool]:
        existing = self.db.query(StockPurchase).filter_by(
            dairy_id=self.dairy_id, idempotency_key=values["idempotency_key"]
        ).first()
        if existing:
            self._check_payload(existing.payload_hash, payload_hash)
            return existing, False

        try:
            with self.db.begin_nested():
                product = self._product(values["product_id"], lock=True)
                quantity = _quantity(values["quantity"])
                rate = _money(values["purchase_rate"])
                purchase = StockPurchase(
                    dairy_id=self.dairy_id,
                    product_id=product.id,
                    quantity=quantity,
                    purchase_rate=rate,
                    supplier=values["supplier"].strip(),
                    purchase_date=values["purchase_date"],
                    invoice_reference=values.get("invoice_reference", "").strip(),
                    idempotency_key=values["idempotency_key"],
                    payload_hash=payload_hash,
                    created_by=self.user_id,
                )
                self.db.add(purchase)
                self.db.flush()

                old_quantity = _quantity(product.stock_quantity)
                new_quantity = old_quantity + quantity
                old_value = old_quantity * _money(product.average_cost)
                product.average_cost = _money((old_value + quantity * rate) / new_quantity)
                product.stock_quantity = new_quantity
                product.purchase_price = rate
                self.db.add(StockMovement(
                    dairy_id=self.dairy_id,
                    product_id=product.id,
                    movement_type="purchase",
                    quantity_delta=quantity,
                    unit_cost=rate,
                    reference_type="stock_purchase",
                    reference_id=purchase.id,
                    created_by=self.user_id,
                ))
                self._audit(
                    "stock_purchased", "stock_purchase", purchase.id,
                    {"product_id": product.id, "quantity": str(quantity), "rate": str(rate),
                     "supplier": purchase.supplier, "invoice_reference": purchase.invoice_reference},
                )
                self.db.flush()
            return purchase, True
        except IntegrityError as exc:
            duplicate = self.db.query(StockPurchase).filter_by(
                dairy_id=self.dairy_id, idempotency_key=values["idempotency_key"]
            ).first()
            if duplicate:
                self._check_payload(duplicate.payload_hash, payload_hash)
                return duplicate, False
            raise FeedInventoryError("Product code or stock purchase conflicts with existing data", 409) from exc

    def sell(
        self,
        values: dict,
        payload_hash: str,
        allow_price_override: bool,
    ) -> tuple[FeedSale, bool]:
        existing = (
            self.db.query(FeedSale)
            .options(joinedload(FeedSale.items).joinedload(FeedSaleItem.product))
            .filter_by(dairy_id=self.dairy_id, idempotency_key=values["idempotency_key"])
            .first()
        )
        if existing:
            self._check_payload(existing.payload_hash, payload_hash)
            return existing, False

        try:
            with self.db.begin_nested():
                customer = self.db.query(Customer).filter_by(
                    id=values["customer_id"], dairy_id=self.dairy_id, active=True
                ).first()
                if customer is None:
                    raise FeedInventoryError("Customer not found", 404)

                product_ids = sorted(item["product_id"] for item in values["items"])
                products = (
                    self.db.query(FeedProduct)
                    .filter(
                        FeedProduct.dairy_id == self.dairy_id,
                        FeedProduct.id.in_(product_ids),
                        FeedProduct.active.is_(True),
                    )
                    .order_by(FeedProduct.id)
                    .with_for_update()
                    .all()
                )
                if len(products) != len(product_ids):
                    raise FeedInventoryError("One or more feed products were not found or are inactive", 404)
                products_by_id = {product.id: product for product in products}

                sale_lines = []
                total = Decimal("0")
                for requested in values["items"]:
                    product = products_by_id[requested["product_id"]]
                    quantity = _quantity(requested["quantity"])
                    if requested.get("selling_price") is not None and not allow_price_override:
                        raise FeedInventoryError("Only a dairy owner can override a sale price", 403)
                    selling_price = _money(
                        requested["selling_price"]
                        if requested.get("selling_price") is not None
                        else product.selling_price
                    )
                    if _quantity(product.stock_quantity) < quantity:
                        raise FeedInventoryError(
                            f"Insufficient stock for {product.product_name}; available "
                            f"{product.stock_quantity} {product.unit}",
                            409,
                        )
                    line_total = _money(quantity * selling_price)
                    cost = _money(product.average_cost)
                    sale_lines.append((product, quantity, selling_price, cost, line_total))
                    total += line_total

                sale = FeedSale(
                    dairy_id=self.dairy_id,
                    customer_id=customer.id,
                    sale_date=values["sale_date"],
                    total_amount=_money(total),
                    idempotency_key=values["idempotency_key"],
                    payload_hash=payload_hash,
                    created_by=self.user_id,
                )
                self.db.add(sale)
                self.db.flush()

                for product, quantity, selling_price, cost, line_total in sale_lines:
                    product.stock_quantity = _quantity(product.stock_quantity) - quantity
                    item = FeedSaleItem(
                        dairy_id=self.dairy_id,
                        sale_id=sale.id,
                        product_id=product.id,
                        quantity=quantity,
                        selling_price=selling_price,
                        purchase_cost=cost,
                        line_total=line_total,
                    )
                    self.db.add(item)
                    self.db.flush()
                    self.db.add(StockMovement(
                        dairy_id=self.dairy_id,
                        product_id=product.id,
                        movement_type="sale",
                        quantity_delta=-quantity,
                        unit_cost=cost,
                        reference_type="feed_sale_item",
                        reference_id=item.id,
                        created_by=self.user_id,
                    ))

                ledger_entry = LedgerEntry(
                    dairy_id=self.dairy_id,
                    customer_id=customer.id,
                    entry_type="feed_debit",
                    amount=-sale.total_amount,
                    description=f"Feed sale #{sale.id}",
                    reference=f"feed-sale-{sale.id}",
                    entry_date=sale.sale_date,
                )
                self.db.add(ledger_entry)
                self._audit(
                    "feed_sale_created", "feed_sale", sale.id,
                    {"customer_id": customer.id, "total_amount": str(sale.total_amount),
                     "item_count": len(sale_lines)},
                )
                self.db.flush()
            return sale, True
        except IntegrityError as exc:
            duplicate = self.db.query(FeedSale).filter_by(
                dairy_id=self.dairy_id, idempotency_key=values["idempotency_key"]
            ).first()
            if duplicate:
                self._check_payload(duplicate.payload_hash, payload_hash)
                return duplicate, False
            raise FeedInventoryError("Feed sale conflicts with existing data", 409) from exc

    def adjust(self, values: dict, payload_hash: str) -> tuple[StockAdjustment, bool]:
        existing = self.db.query(StockAdjustment).filter_by(
            dairy_id=self.dairy_id, idempotency_key=values["idempotency_key"]
        ).first()
        if existing:
            self._check_payload(existing.payload_hash, payload_hash)
            return existing, False

        delta = _quantity(values["quantity_delta"])
        if delta == 0:
            raise FeedInventoryError("Adjustment quantity cannot be zero")
        reason = values["reason"]
        if reason in {"damaged", "expired", "supplier_return"} and delta >= 0:
            raise FeedInventoryError(f"{reason} adjustments must reduce stock")
        if reason == "customer_return" and delta <= 0:
            raise FeedInventoryError("Customer returns must increase stock")

        try:
            with self.db.begin_nested():
                product = self._product(values["product_id"], lock=True)
                if delta < 0 and _quantity(product.stock_quantity) < abs(delta):
                    raise FeedInventoryError("Adjustment would make stock negative", 409)
                adjustment = StockAdjustment(
                    dairy_id=self.dairy_id,
                    product_id=product.id,
                    quantity_delta=delta,
                    reason=reason,
                    note=values["note"].strip(),
                    adjustment_date=values["adjustment_date"],
                    idempotency_key=values["idempotency_key"],
                    payload_hash=payload_hash,
                    created_by=self.user_id,
                )
                self.db.add(adjustment)
                self.db.flush()
                unit_cost = _money(product.average_cost or product.purchase_price)
                if delta > 0:
                    old_quantity = _quantity(product.stock_quantity)
                    total_quantity = old_quantity + delta
                    product.average_cost = _money(
                        (old_quantity * _money(product.average_cost) + delta * unit_cost)
                        / total_quantity
                    )
                product.stock_quantity = _quantity(product.stock_quantity) + delta
                self.db.add(StockMovement(
                    dairy_id=self.dairy_id,
                    product_id=product.id,
                    movement_type="adjustment",
                    quantity_delta=delta,
                    unit_cost=unit_cost,
                    reference_type="stock_adjustment",
                    reference_id=adjustment.id,
                    reason=reason,
                    created_by=self.user_id,
                ))
                self._audit(
                    "stock_adjusted", "stock_adjustment", adjustment.id,
                    {"product_id": product.id, "quantity_delta": str(delta),
                     "reason": reason, "note": adjustment.note},
                )
                self.db.flush()
            return adjustment, True
        except IntegrityError as exc:
            duplicate = self.db.query(StockAdjustment).filter_by(
                dairy_id=self.dairy_id, idempotency_key=values["idempotency_key"]
            ).first()
            if duplicate:
                self._check_payload(duplicate.payload_hash, payload_hash)
                return duplicate, False
            raise FeedInventoryError("Stock adjustment conflicts with existing data", 409) from exc

    def dashboard(self) -> dict:
        products = self.db.query(FeedProduct).filter_by(
            dairy_id=self.dairy_id, active=True
        ).order_by(FeedProduct.product_name).all()
        rows = [self._product_payload(product) for product in products]
        return {
            "products": rows,
            "total_inventory_value": str(sum(
                (_money(product.stock_quantity * product.average_cost) for product in products),
                Decimal("0"),
            )),
            "low_stock_alerts": [row for row in rows if row["low_stock"]],
        }

    def purchase_history(self, limit: int = 100) -> list[dict]:
        rows = (
            self.db.query(StockPurchase, FeedProduct.product_name, FeedProduct.unit)
            .join(FeedProduct, FeedProduct.id == StockPurchase.product_id)
            .filter(StockPurchase.dairy_id == self.dairy_id)
            .order_by(StockPurchase.purchase_date.desc(), StockPurchase.id.desc())
            .limit(limit).all()
        )
        return [
            {
                "id": purchase.id, "product_id": purchase.product_id,
                "product_name": name, "unit": unit, "quantity": str(purchase.quantity),
                "purchase_rate": str(purchase.purchase_rate), "supplier": purchase.supplier,
                "purchase_date": purchase.purchase_date.isoformat(),
                "invoice_reference": purchase.invoice_reference,
            }
            for purchase, name, unit in rows
        ]

    def sales_history(self, limit: int = 100) -> list[dict]:
        sales = (
            self.db.query(FeedSale)
            .options(joinedload(FeedSale.customer), joinedload(FeedSale.items).joinedload(FeedSaleItem.product))
            .filter(FeedSale.dairy_id == self.dairy_id)
            .order_by(FeedSale.sale_date.desc(), FeedSale.id.desc())
            .limit(limit).all()
        )
        return [self._sale_payload(sale) for sale in sales]

    def movements(self, product_id: int | None, start_date: date | None, end_date: date | None, limit: int):
        query = self.db.query(StockMovement, FeedProduct.product_name, FeedProduct.unit).join(
            FeedProduct, FeedProduct.id == StockMovement.product_id
        ).filter(StockMovement.dairy_id == self.dairy_id)
        if product_id is not None:
            query = query.filter(StockMovement.product_id == product_id)
        if start_date:
            query = query.filter(func.date(StockMovement.created_at) >= start_date.isoformat())
        if end_date:
            query = query.filter(func.date(StockMovement.created_at) <= end_date.isoformat())
        return [
            {
                "id": movement.id, "product_id": movement.product_id,
                "product_name": name, "unit": unit, "movement_type": movement.movement_type,
                "quantity_delta": str(movement.quantity_delta), "unit_cost": str(movement.unit_cost),
                "reference_type": movement.reference_type, "reference_id": movement.reference_id,
                "reason": movement.reason, "created_at": movement.created_at.isoformat(),
            }
            for movement, name, unit in query.order_by(
                StockMovement.created_at.desc(), StockMovement.id.desc()
            ).limit(limit).all()
        ]

    def sales_report(self, start_date: date, end_date: date) -> dict:
        sales = self.db.query(FeedSale).options(
            joinedload(FeedSale.customer),
            joinedload(FeedSale.items).joinedload(FeedSaleItem.product),
        ).filter(
            FeedSale.dairy_id == self.dairy_id,
            FeedSale.sale_date >= start_date,
            FeedSale.sale_date <= end_date,
        ).order_by(FeedSale.sale_date, FeedSale.id).all()
        product_totals: dict[int, dict] = {}
        total_sales = Decimal("0")
        estimated_profit = Decimal("0")
        for sale in sales:
            total_sales += sale.total_amount
            for item in sale.items:
                record = product_totals.setdefault(item.product_id, {
                    "product_id": item.product_id,
                    "product_name": item.product.product_name,
                    "quantity_sold": Decimal("0"),
                    "sales_total": Decimal("0"),
                    "estimated_profit": Decimal("0"),
                })
                record["quantity_sold"] += item.quantity
                record["sales_total"] += item.line_total
                record["estimated_profit"] += _money(
                    item.line_total - item.purchase_cost * item.quantity
                )
                estimated_profit += _money(item.line_total - item.purchase_cost * item.quantity)
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "sale_count": len(sales),
            "total_sales": str(_money(total_sales)),
            "estimated_profit": str(_money(estimated_profit)),
            "product_sales": [
                {key: (str(value) if isinstance(value, Decimal) else value) for key, value in row.items()}
                for row in product_totals.values()
            ],
            "sales": [self._sale_payload(sale) for sale in sales],
        }

    def _product(self, product_id: int, lock: bool = False, active: bool = True) -> FeedProduct:
        query = self.db.query(FeedProduct).filter_by(
            id=product_id, dairy_id=self.dairy_id
        )
        if active:
            query = query.filter(FeedProduct.active.is_(True))
        if lock:
            query = query.with_for_update()
        product = query.first()
        if product is None:
            raise FeedInventoryError("Feed product not found or inactive", 404)
        return product

    def _audit(self, action: str, entity_type: str, entity_id: int, details: dict):
        self.db.add(AuditLog(
            dairy_id=self.dairy_id,
            user_id=self.user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            details=details,
        ))

    @staticmethod
    def _check_payload(existing_hash: str, payload_hash: str):
        if existing_hash != payload_hash:
            raise FeedInventoryError(
                "Idempotency key was already used with a different request", 409
            )

    @staticmethod
    def _product_payload(product: FeedProduct) -> dict:
        stock = _quantity(product.stock_quantity)
        threshold = _quantity(product.low_stock_threshold)
        average_cost = _money(product.average_cost)
        return {
            "id": product.id,
            "product_name": product.product_name,
            "product_code": product.product_code,
            "category": product.category,
            "unit": product.unit,
            "available_quantity": str(stock),
            "purchase_price": str(product.purchase_price),
            "selling_price": str(product.selling_price),
            "average_cost": str(average_cost),
            "stock_value": str(_money(stock * average_cost)),
            "low_stock_threshold": str(threshold),
            "low_stock": stock <= threshold,
            "active": product.active,
        }

    @staticmethod
    def _sale_payload(sale: FeedSale) -> dict:
        return {
            "id": sale.id,
            "customer_id": sale.customer_id,
            "customer_name": sale.customer.name if sale.customer else "",
            "sale_date": sale.sale_date.isoformat(),
            "total_amount": str(sale.total_amount),
            "items": [
                {
                    "product_id": item.product_id,
                    "product_name": item.product.product_name,
                    "unit": item.product.unit,
                    "quantity": str(item.quantity),
                    "selling_price": str(item.selling_price),
                    "purchase_cost": str(item.purchase_cost),
                    "line_total": str(item.line_total),
                }
                for item in sale.items
            ],
        }
