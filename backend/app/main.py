import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.orm import Session

from .database import get_db
from .models import (
    Customer, DairyMembership, InventoryItem, LedgerEntry, MilkRateChart, RateRule,
    ScanBatch, ScanRow, ScanRowCorrection,
)
from .routes.auth import router as auth_router
from .routes.feed_inventory import router as feed_inventory_router
from .routes.rate_charts import router as rate_charts_router
from .routes.experience import router as experience_router
from .schemas import (
    ApproveBatch, AssistantIn, CustomerIn, InventoryItemIn, RateIn,
    ScanRowCreate, ScanRowEdit, TransactionIn,
)
from .security import OwnerManagerDep, TenantDep, require_roles
from .services.assistant import AssistantService
from .services.audit import record_audit
from .services.extraction import ExtractionService
from .services.document_classifier import detect_kind, DocumentClassificationError
from .services.image_storage import get_store
from .services.image_preprocessing import ImagePreprocessingError
from .services.ledger import LedgerService
from .services.messaging import MessagingFactory
from .services.ocr import OCRProviderError, OCRProviderFactory, OCRService
from .services.rate_charts import MilkRateCalculationService, RateChartError

app = FastAPI(
    title="DairyAI API",
    version="0.2.0",
    description="Tenant-isolated dairy management API",
)
origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials="*" not in origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(feed_inventory_router)
app.include_router(rate_charts_router)
app.include_router(experience_router)
from .routes.banking import router as banking_router
app.include_router(banking_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "DairyAI"}


@app.get("/api/ocr/health")
def ocr_health():
    try:
        return OCRProviderFactory.diagnostics()
    except OCRProviderError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/ai/health")
def ai_health():
    selected = os.getenv("AI_PROVIDER", "").strip().lower() or (
        "gemini" if os.getenv("GEMINI_API_KEY") else "unconfigured"
    )
    if selected == "gemini":
        from .services.ocr import GeminiVisionOCRProvider

        return GeminiVisionOCRProvider().connection_health()
    if selected == "ollama":
        from .services.ocr import OllamaVisionOCRProvider

        health = OllamaVisionOCRProvider().health()
        return {
            "provider": "ollama", "configured": health.get("configured", False),
            "connected": health.get("available", False), "model": health.get("model"),
            "message": health.get("message"),
        }
    if selected == "tesseract":
        health = OCRProviderFactory.create("tesseract").health()
        return {
            "provider": "tesseract", "configured": health.get("configured", False),
            "connected": health.get("available", False), "model": "local Tesseract",
            "message": health.get("message"),
        }
    return {
        "provider": selected, "configured": False, "connected": False, "model": None,
        "message": "Unsupported AI_PROVIDER. Choose gemini, ollama, or explicitly choose tesseract.",
    }


def get_ocr_service() -> OCRService:
    try:
        return OCRService()
    except OCRProviderError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/dashboard")
def dashboard(context: TenantDep, db: Session = Depends(get_db)):
    customers = db.query(Customer).filter_by(
        dairy_id=context.dairy.id, active=True
    ).all()
    ledger = LedgerService(db, context.dairy.id)
    balances = {customer.id: float(ledger.balance(customer.id)) for customer in customers}
    return {
        "customer_count": len(customers),
        "total_outstanding": round(sum(balances.values()), 2),
        "pending_batches": db.query(ScanBatch).filter_by(
            dairy_id=context.dairy.id, status="pending"
        ).count(),
        "customers_with_balance": sum(1 for value in balances.values() if value > 0),
    }


@app.get("/api/customers")
def customers(context: TenantDep, db: Session = Depends(get_db)):
    result = []
    ledger = LedgerService(db, context.dairy.id)
    for customer in db.query(Customer).filter_by(
        dairy_id=context.dairy.id, active=True
    ).order_by(Customer.name).all():
        result.append({
            "id": customer.id, "code": customer.code, "name": customer.name,
            "phone": customer.phone, "village": customer.village,
            "balance": float(ledger.balance(customer.id)),
        })
    return result


@app.post("/api/customers")
def add_customer(
    body: CustomerIn,
    context: OwnerManagerDep,
    db: Session = Depends(get_db),
):
    if db.query(Customer).filter_by(
        dairy_id=context.dairy.id, code=body.code
    ).first():
        raise HTTPException(409, "Customer code already exists")
    fields = body.model_dump(exclude={'bank_details'})
    customer = Customer(dairy_id=context.dairy.id, **fields)
    if body.bank_details:
        from .services.banking import BankConfigurationError, encrypt_details, validate_details
        try:
            customer.bank_details_encrypted = encrypt_details(validate_details(body.bank_details))
        except BankConfigurationError as exc:
            raise HTTPException(422, str(exc)) from exc
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return {"id": customer.id, "code": customer.code, "name": customer.name}


@app.get("/api/customers/{customer_id}")
def customer_detail(customer_id: int, context: TenantDep, db: Session = Depends(get_db)):
    try:
        statement = LedgerService(db, context.dairy.id).statement(customer_id)
        customer = statement["customer"]
        return {
            "customer": {
                "id": customer.id, "code": customer.code, "name": customer.name,
                "phone": customer.phone, "village": customer.village,
            },
            "balance": float(statement["balance"]),
            "entries": [
                {"id": entry.id, "date": str(entry.entry_date), "type": entry.entry_type,
                 "amount": float(entry.amount), "description": entry.description}
                for entry in statement["entries"]
            ],
        }
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/rates")
def get_rates(context: TenantDep, db: Session = Depends(get_db)):
    return [
        {
            "id": rule.id, "milk_type": rule.milk_type, "fat_min": rule.fat_min,
            "fat_max": rule.fat_max, "base_rate": float(rule.base_rate),
            "per_fat_rate": float(rule.per_fat_rate), "snf_bonus": float(rule.snf_bonus),
        }
        for rule in db.query(RateRule).filter_by(dairy_id=context.dairy.id)
        .order_by(RateRule.fat_min).all()
    ]


@app.post("/api/rates")
def add_rate(body: RateIn, context: OwnerManagerDep, db: Session = Depends(get_db)):
    rule = RateRule(dairy_id=context.dairy.id, **body.model_dump())
    db.add(rule)
    db.flush()
    record_audit(
        db, context.dairy.id, context.user.id, "rate_created", "rate_rule",
        rule.id, body.model_dump(mode="json"),
    )
    db.commit()
    db.refresh(rule)
    return {"id": rule.id, "message": "Rate slab saved"}


@app.delete("/api/rates/{rate_id}")
def delete_rate(rate_id: int, context: OwnerManagerDep, db: Session = Depends(get_db)):
    rule = db.query(RateRule).filter_by(
        id=rate_id, dairy_id=context.dairy.id
    ).first()
    if not rule:
        raise HTTPException(404, "Rate slab not found")
    record_audit(
        db, context.dairy.id, context.user.id, "rate_deleted", "rate_rule",
        rule.id, {"milk_type": rule.milk_type, "fat_min": rule.fat_min, "fat_max": rule.fat_max},
    )
    db.delete(rule)
    db.commit()
    return {"message": "Deleted"}


@app.post("/api/scans")
async def scan_register(
    context: TenantDep,
    kind: str = "milk",
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    ocr_service: OCRService = Depends(get_ocr_service),
):
    supported_kinds = {
        "auto", "milk", "feed", "advance", "MILK_REGISTER", "CATTLE_FEED_PURCHASE",
        "RATE_CHART", "ADVANCE_REGISTER", "rate_chart",
    }
    if kind not in supported_kinds:
        raise HTTPException(
            400, "kind must be milk, feed, advance, RATE_CHART, or a supported register type"
        )
    if Path(file.filename or "").suffix.lower() not in (".jpg", ".jpeg", ".png"):
        raise HTTPException(400, "Upload a JPG, JPEG, or PNG image")
    data = await file.read()
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(413, "Maximum image size is 12 MB")
    if file.content_type not in ("image/jpeg", "image/png"):
        raise HTTPException(400, "Upload a JPG, JPEG, or PNG image")
    try:
        if kind == "auto":
            kind = (await run_in_threadpool(detect_kind, data))["kind"]
        batch, provider, image_layout = await run_in_threadpool(
            ExtractionService(db, context.dairy.id, ocr_service=ocr_service).scan,
            data, file.filename or "register.jpg", kind, file.content_type,
        )
        db.commit()
        structured = (batch.image_layout or {}).get("structured_extraction") or {}
        chart_versions = {
            chart.id: chart.version
            for chart in db.query(MilkRateChart).filter(
                MilkRateChart.dairy_id == context.dairy.id,
                MilkRateChart.id.in_([
                    row.rate_chart_id for row in batch.rows if row.rate_chart_id
                ] or [-1]),
            ).all()
        }
        return {
            "batch_id": batch.id, "provider": provider, "raw_text": batch.raw_text,
            "model": structured.get("model"),
            "register_type": ExtractionService.register_type(kind),
            "structured_extraction": structured or None,
            "extraction_errors": structured.get("extraction_errors", []),
            "warnings": structured.get("warnings", []),
            "confidence_thresholds": ExtractionService.confidence_thresholds(),
            "image_layout": batch.image_layout,
            "summary": _scan_summary(batch),
            "rows": [
                {
                    "id": row.id, "customer_id": row.customer_id,
                    "original_customer_id": row.original_customer_id,
                    "extracted_name": row.extracted_name, "extracted_code": row.extracted_code,
                    "quantity": row.quantity, "fat": row.fat, "snf": row.snf,
                    "description": row.description, "confidence": row.confidence,
                    "collection_date": (
                        row.collection_date.isoformat() if row.collection_date else None
                    ),
                    "session": row.session, "milk_type": row.milk_type,
                    "approval_status": row.approval_status,
                    "original_quantity": (
                        str(row.original_quantity) if row.original_quantity is not None else None
                    ),
                    "original_fat": str(row.original_fat) if row.original_fat is not None else None,
                    "original_snf": str(row.original_snf) if row.original_snf is not None else None,
                    "rate_chart_id": row.rate_chart_id,
                    "rate_chart_version": chart_versions.get(row.rate_chart_id),
                    "rate_per_litre": (
                        str(row.rate_per_litre) if row.rate_per_litre is not None else None
                    ),
                    "calculated_amount": (
                        str(row.calculated_amount)
                        if row.calculated_amount is not None else None
                    ),
                    "calculation_status": row.calculation_status,
                    "calculation_method": row.calculation_method,
                    "calculation_policy": row.calculation_policy,
                    "calculation_warnings": row.calculation_warnings,
                    "field_values": row.extracted_fields,
                    "field_confidence": row.field_confidence,
                    "model_confidence": row.model_row_confidence,
                    "backend_confidence": row.confidence,
                    "warnings": row.extraction_warnings,
                    "duplicate_warning": row.duplicate_warning,
                    "version": row.version,
                }
                for row in batch.rows
            ],
        }
    except DocumentClassificationError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except ImagePreprocessingError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    except OCRProviderError as exc:
        db.rollback()
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, "Extraction failed. Please retry or check server logs.") from exc


def _scan_summary(batch: ScanBatch) -> dict:
    active_rows = [row for row in batch.rows if row.approval_status != "deleted"]
    approved_rows = [row for row in active_rows if row.approved]
    milk_rows = [
        row for row in active_rows
        if batch.kind in {"milk", "MILK_REGISTER"}
    ]

    def totals(rows):
        litres = sum(
            (Decimal(str(row.quantity)) for row in rows if row.quantity is not None),
            Decimal("0"),
        )
        amount = sum(
            (Decimal(str(row.calculated_amount)) for row in rows
             if row.calculated_amount is not None),
            Decimal("0"),
        )
        return {"litres": str(litres), "amount": str(amount)}

    threshold = ExtractionService.confidence_thresholds()
    return {
        "total_entries": len(milk_rows),
        "high_confidence_entries": sum(
            1 for row in milk_rows
            if row.confidence >= threshold["green"]
            and row.calculation_status == "CALCULATED"
        ),
        "entries_needing_review": sum(
            1 for row in milk_rows
            if row.confidence < threshold["green"]
            or row.calculation_status != "CALCULATED"
            or bool(row.extraction_warnings)
        ),
        "total": totals(milk_rows),
        "approved": totals([row for row in approved_rows if row in milk_rows]),
        "pending_corrections": sum(
            1 for row in milk_rows if row.corrected_at and not row.approved
        ),
    }


@app.get("/api/scans/{batch_id}")
def get_batch(batch_id: int, context: TenantDep, db: Session = Depends(get_db)):
    batch = db.query(ScanBatch).filter_by(
        id=batch_id, dairy_id=context.dairy.id
    ).first()
    if not batch:
        raise HTTPException(404, "Batch not found")
    chart_versions = {
        chart.id: chart.version
        for chart in db.query(MilkRateChart).filter(
            MilkRateChart.dairy_id == context.dairy.id,
            MilkRateChart.id.in_([
                row.rate_chart_id for row in batch.rows if row.rate_chart_id
            ] or [-1]),
        ).all()
    }
    row_ids = [row.id for row in batch.rows]
    corrections_by_row = {row_id: [] for row_id in row_ids}
    if row_ids:
        corrections = db.query(ScanRowCorrection).filter(
            ScanRowCorrection.dairy_id == context.dairy.id,
            ScanRowCorrection.row_id.in_(row_ids),
        ).order_by(ScanRowCorrection.created_at).all()
        for correction in corrections:
            corrections_by_row[correction.row_id].append({
                "field": correction.field_name,
                "original_value": correction.original_value,
                "corrected_value": correction.corrected_value,
                "user_id": correction.user_id,
                "corrected_at": (
                    correction.created_at.isoformat() if correction.created_at else None
                ),
            })
    return {
        "batch_id": batch.id, "kind": batch.kind, "status": batch.status,
        "provider": (batch.image_layout or {}).get("ocr", {}).get("provider"),
        "model": (
            (batch.image_layout or {}).get("structured_extraction") or {}
        ).get("model"),
        "register_type": ExtractionService.register_type(batch.kind),
        "confidence_thresholds": ExtractionService.confidence_thresholds(),
        "structured_extraction": (batch.image_layout or {}).get("structured_extraction"),
        "extraction_errors": (
            (batch.image_layout or {}).get("structured_extraction") or {}
        ).get("extraction_errors", []),
        "warnings": (
            (batch.image_layout or {}).get("structured_extraction") or {}
        ).get("warnings", []),
        "raw_text": batch.raw_text, "image_layout": batch.image_layout,
        "summary": _scan_summary(batch),
        "rows": [
            {
                "id": row.id, "customer_id": row.customer_id,
                "original_customer_id": row.original_customer_id,
                "extracted_name": row.extracted_name, "extracted_code": row.extracted_code,
                "quantity": row.quantity, "fat": row.fat, "snf": row.snf,
                "description": row.description, "confidence": row.confidence,
                "approved": row.approved, "approval_status": row.approval_status,
                "approved_by": row.approved_by,
                "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                "corrected_by": row.corrected_by,
                "corrected_at": row.corrected_at.isoformat() if row.corrected_at else None,
                "collection_date": (
                    row.collection_date.isoformat() if row.collection_date else None
                ),
                "session": row.session, "milk_type": row.milk_type,
                "original_quantity": (
                    str(row.original_quantity) if row.original_quantity is not None else None
                ),
                "original_fat": str(row.original_fat) if row.original_fat is not None else None,
                "original_snf": str(row.original_snf) if row.original_snf is not None else None,
                "rate_chart_id": row.rate_chart_id,
                "rate_chart_version": chart_versions.get(row.rate_chart_id),
                "calculation_status": row.calculation_status,
                "calculation_method": row.calculation_method,
                "calculation_policy": row.calculation_policy,
                "calculation_warnings": row.calculation_warnings,
                "calculated_at": row.calculated_at.isoformat() if row.calculated_at else None,
                "rate_per_litre": (
                    str(row.rate_per_litre) if row.rate_per_litre is not None else None
                ),
                "calculated_amount": (
                    str(row.calculated_amount) if row.calculated_amount is not None else None
                ),
                "field_values": row.extracted_fields,
                "field_confidence": row.field_confidence,
                "model_confidence": row.model_row_confidence,
                "backend_confidence": row.confidence,
                "warnings": row.extraction_warnings,
                "duplicate_warning": row.duplicate_warning,
                "corrections": corrections_by_row[row.id],
                "version": row.version,
            }
            for row in batch.rows
        ],
    }


@app.get("/api/scans/{batch_id}/image")
def get_original_scan_image(
    batch_id: int, context: TenantDep, db: Session = Depends(get_db)
):
    batch = db.query(ScanBatch).filter_by(
        id=batch_id, dairy_id=context.dairy.id
    ).first()
    if not batch:
        raise HTTPException(404, "Scan image not found")
    content, media_type = batch.original_image, batch.image_media_type or "application/octet-stream"
    if content is None:
        key=(batch.image_layout or {}).get('image_storage_key')
        if not key:
            raise HTTPException(404, "Scan image not found")
        store=get_store()
        if store is None:
            raise HTTPException(503, "Object storage must be configured to view this image")
        try:
            content,media_type=store.read(key)
        except Exception as exc:
            raise HTTPException(503,"Private image storage is temporarily unavailable") from exc
    return Response(content=content,media_type=media_type,
                    headers={"Cache-Control":"private, no-store"})


@app.put("/api/scans/rows/{row_id}")
def edit_scan_row(
    row_id: int, body: ScanRowEdit, context: TenantDep, db: Session = Depends(get_db)
):
    row = db.query(ScanRow).filter_by(id=row_id, dairy_id=context.dairy.id).first()
    if not row:
        raise HTTPException(404, "Scan row not found")
    if row.approved:
        raise HTTPException(409, "An approved scan row cannot be edited; use a financial adjustment")
    if (
        row.batch.kind in {"milk", "MILK_REGISTER"}
        and body.quantity is not None
        and body.quantity > 200
    ):
        raise HTTPException(422, "Milk quantity must be 200 litres or less")
    if body.customer_id is not None and not db.query(Customer).filter_by(
        id=body.customer_id, dairy_id=context.dairy.id, active=True
    ).first():
        raise HTTPException(404, "Customer not found")
    if body.expected_version is not None and body.expected_version != row.version:
        raise HTTPException(409, "Scan row changed since it was loaded. Refresh and retry.")
    edits = body.model_dump(exclude_unset=True, exclude={"expected_version"})
    editable = {"customer_id", "quantity", "fat", "snf", "collection_date",
                "session", "milk_type", "description"}
    structured_field_names = {
        "quantity": "quantity_litres",
        "fat": "fat_percent",
        "snf": "snf_percent",
        "extracted_code": "customer_code",
        "extracted_name": "customer_name",
        "collection_date": "date",
    }

    def correction_value(value):
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        return value

    field_values = dict(row.extracted_fields or {})
    changed = False
    for key, value in edits.items():
        if key in editable and getattr(row, key) != value:
            changed = True
            old_value = getattr(row, key)
            db.add(ScanRowCorrection(
                dairy_id=context.dairy.id, row_id=row.id, user_id=context.user.id,
                field_name=key,
                original_value=correction_value(old_value),
                corrected_value=correction_value(value),
            ))
            setattr(row, key, value)
            field_name = structured_field_names.get(key, key)
            if field_name in field_values:
                field_values[field_name]["corrected_value"] = correction_value(value)
        elif key in {"product_code", "product_name", "unit", "written_amount"}:
            source_field = field_values.get(key, {})
            old_value = source_field.get("corrected_value", source_field.get("value"))
            stored_value = str(value) if value is not None else None
            if old_value != stored_value:
                changed = True
                db.add(ScanRowCorrection(
                    dairy_id=context.dairy.id, row_id=row.id, user_id=context.user.id,
                    field_name=key, original_value=old_value, corrected_value=stored_value,
                ))
                field_values[key] = {
                    **field_values.get(key, {}),
                    "corrected_value": stored_value,
                    "raw_text": field_values.get(key, {}).get("raw_text"),
                }
    row.extracted_fields = field_values
    calculation = None
    if changed:
        row.corrected_by = context.user.id
        row.corrected_at = datetime.now(timezone.utc)
        if row.approval_status != "pending":
            row.approval_status = "pending"
        if row.batch.kind in {"milk", "MILK_REGISTER"}:
            calculation = MilkRateCalculationService(
                db, context.dairy.id
            ).calculate_row(row)
    try:
        if changed:
            record_audit(
                db, context.dairy.id, context.user.id, "scan_row_corrected",
                "scan_row", row.id, {"fields": sorted(edits)},
            )
        db.commit()
    except StaleDataError as exc:
        db.rollback()
        raise HTTPException(409, "Scan row changed concurrently. Refresh and retry.") from exc
    return {
        "message": "Row updated",
        "version": row.version,
        "calculation": calculation,
    }


@app.post("/api/scans/{batch_id}/rows", status_code=201)
def add_scan_row(
    batch_id: int,
    body: ScanRowCreate,
    context: OwnerManagerDep,
    db: Session = Depends(get_db),
):
    batch = db.query(ScanBatch).filter_by(
        id=batch_id, dairy_id=context.dairy.id
    ).first()
    if not batch or batch.status != "pending":
        raise HTTPException(404, "Pending scan batch not found")
    if (
        batch.kind in {"milk", "MILK_REGISTER"}
        and body.quantity is not None
        and body.quantity > 200
    ):
        raise HTTPException(422, "Milk quantity must be 200 litres or less")
    customer = None
    if body.customer_id:
        customer = db.query(Customer).filter_by(
            id=body.customer_id, dairy_id=context.dairy.id, active=True
        ).first()
        if not customer:
            raise HTTPException(404, "Customer not found")
    values = body.model_dump()
    product_details = {
        key: values[key] for key in ("product_code", "product_name", "unit", "written_amount")
        if values.get(key) is not None
    }
    row = ScanRow(
        dairy_id=context.dairy.id, batch_id=batch.id,
        customer_id=customer.id if customer else None,
        original_customer_id=None,
        extracted_name=customer.name if customer else "",
        extracted_code=customer.code if customer else "",
        quantity=body.quantity, fat=body.fat, snf=body.snf,
        original_quantity=None, original_fat=None, original_snf=None,
        collection_date=body.collection_date, session=body.session, milk_type=body.milk_type,
        description=body.notes or "",
        confidence=0.0, model_row_confidence=None,
        extracted_fields={
            key: {"value": value, "raw_text": None, "corrected_value": value}
            for key, value in product_details.items()
        },
        field_confidence={}, extraction_warnings=["Manually added during review"],
        approval_status="pending",
    )
    db.add(row)
    db.flush()
    calculation = None
    if batch.kind in {"milk", "MILK_REGISTER"}:
        calculation = MilkRateCalculationService(
            db, context.dairy.id
        ).calculate_row(row)
    record_audit(
        db, context.dairy.id, context.user.id, "scan_row_added",
        "scan_row", row.id, {"batch_id": batch.id},
    )
    db.commit()
    return {
        "id": row.id, "version": row.version,
        "approval_status": row.approval_status, "calculation": calculation,
    }


@app.delete("/api/scans/rows/{row_id}")
def delete_scan_row(
    row_id: int, context: OwnerManagerDep, db: Session = Depends(get_db)
):
    row = db.query(ScanRow).filter_by(
        id=row_id, dairy_id=context.dairy.id
    ).first()
    if not row:
        raise HTTPException(404, "Scan row not found")
    if row.approved:
        raise HTTPException(409, "Approved rows cannot be deleted; use a financial reversal")
    row.approval_status = "deleted"
    record_audit(
        db, context.dairy.id, context.user.id, "scan_row_removed",
        "scan_row", row.id, {"batch_id": row.batch_id},
    )
    db.commit()
    return {"message": "Row removed from pending review"}


@app.post("/api/scans/{batch_id}/approve")
def approve_batch(
    batch_id: int,
    body: ApproveBatch,
    context: OwnerManagerDep,
    db: Session = Depends(get_db),
):
    batch = db.query(ScanBatch).filter_by(
        id=batch_id, dairy_id=context.dairy.id
    ).first()
    if not batch:
        raise HTTPException(404, "Batch not found")
    register_type = ExtractionService.register_type(batch.kind)
    if register_type in {"CATTLE_FEED_PURCHASE", "RATE_CHART"}:
        raise HTTPException(
            409,
            "This register type requires its dedicated review and approval workflow.",
        )
    approved, errors = [], []
    approved_row_keys = set()
    ledger = LedgerService(db, context.dairy.id)
    for row_id in body.row_ids:
        row = db.query(ScanRow).filter_by(
            id=row_id, batch_id=batch_id, dairy_id=context.dairy.id
        ).first()
        if not row or row.approved:
            continue
        if row.approval_status == "deleted":
            continue
        if not row.customer_id:
            errors.append(f"Row {row_id}: select a registered customer")
            continue
        try:
            duplicate_key = (
                row.customer_id, row.collection_date, row.session, row.milk_type,
                row.quantity, row.fat, row.snf,
            )
            if duplicate_key in approved_row_keys:
                raise ValueError(
                    "Possible duplicate row in this approval request; only one copy was posted."
                )
            if row.collection_date and row.quantity is not None:
                duplicate = db.query(ScanRow.id).filter(
                    ScanRow.dairy_id == context.dairy.id,
                    ScanRow.customer_id == row.customer_id,
                    ScanRow.collection_date == row.collection_date,
                    ScanRow.session == row.session,
                    ScanRow.milk_type == row.milk_type,
                    ScanRow.quantity == row.quantity,
                    ScanRow.fat == row.fat,
                    ScanRow.snf == row.snf,
                    ScanRow.approved.is_(True),
                    ScanRow.id != row.id,
                ).first()
                if duplicate:
                    raise ValueError(
                        f"Possible duplicate of already approved row {duplicate.id}; "
                        "review the entry before posting."
                    )
            if register_type == "MILK_REGISTER":
                if row.collection_date is None:
                    raise ValueError("Enter the collection date; it cannot be inferred")
                if row.milk_type not in {"cow", "buffalo"}:
                    raise ValueError("Select cow or buffalo milk")
                if row.quantity is None or row.quantity <= 0:
                    raise ValueError("Enter a positive milk quantity")
                calculation = MilkRateCalculationService(
                    db, context.dairy.id
                ).calculate_row(row, require_confidence=False)
                if calculation["calculation_status"] != "CALCULATED":
                    raise ValueError("; ".join(calculation["warnings"]))
                amount = Decimal(calculation["total_amount"])
                row.amount = amount
                _, created = ledger.post(
                    row.customer_id,
                    "milk_credit",
                    amount,
                    f"Milk {row.quantity} L | FAT {row.fat if row.fat is not None else 'unread'} | "
                    f"SNF {row.snf if row.snf is not None else 'unread'} | "
                    f"Rate chart v{calculation['chart_version']}",
                    f"milk-batch-{batch_id}-row-{row.id}",
                    row.collection_date,
                )
                row.approved = True
                row.approval_status = "approved"
                row.approved_by = context.user.id
                row.approved_at = datetime.now(timezone.utc)
            else:
                if register_type != "ADVANCE_REGISTER":
                    raise ValueError(f"Unsupported register type: {register_type}")
                amount = Decimal(str(row.quantity))
                if amount <= 0:
                    raise ValueError("Enter a positive amount")
                entry_type = "advance_disbursed"
                _, created = ledger.post(
                    row.customer_id, entry_type, -amount, row.description or batch.kind,
                    f"{batch.kind}-batch-{batch_id}-row-{row_id}", date.today(),
                )
                row.approved = True
                row.approval_status = "approved"
                row.approved_by = context.user.id
                row.approved_at = datetime.now(timezone.utc)
            approved_row_keys.add(duplicate_key)
            approved.append({"row_id": row_id, "amount": float(amount), "posted": created})
        except (ValueError, RateChartError) as exc:
            errors.append(f"Row {row_id}: {exc}")
    active_rows = [row for row in batch.rows if row.approval_status != "deleted"]
    if active_rows and all(row.approved for row in active_rows):
        batch.status = "approved"
    elif approved:
        batch.status = "partially_approved"
    if approved:
        record_audit(
            db, context.dairy.id, context.user.id, "scan_rows_approved", "scan_batch",
            batch.id, {
                "row_ids": [item["row_id"] for item in approved],
                "rate_chart_ids": sorted({
                    row.rate_chart_id for row in batch.rows
                    if row.id in {item["row_id"] for item in approved}
                    and row.rate_chart_id is not None
                }),
            },
        )
    try:
        db.commit()
    except StaleDataError as exc:
        db.rollback()
        raise HTTPException(
            409, "Scan batch changed during approval. Refresh and review the current status."
        ) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "A ledger entry already exists for one of these rows; refresh before retrying."
        ) from exc
    return {"approved": approved, "errors": errors, "batch_status": batch.status}


@app.post("/api/transactions")
def add_transaction(
    body: TransactionIn, context: OwnerManagerDep, db: Session = Depends(get_db)
):
    customer = db.query(Customer).filter_by(
        id=body.customer_id, dairy_id=context.dairy.id
    ).first()
    if not customer:
        raise HTTPException(404, "Customer not found")
    types = {
        "feed_purchase": "feed_debit", "advance": "advance_disbursed",
        "advance_repayment": "advance_repayment", "manual_credit": "manual_credit",
        "manual_debit": "manual_debit",
    }
    if body.entry_type not in types:
        raise HTTPException(400, "Unsupported transaction type")
    amount = body.amount if body.entry_type in ("manual_credit", "advance_repayment") else -body.amount
    reference = f"manual-{uuid.uuid4().hex}"
    ledger = LedgerService(db, context.dairy.id)
    entry, created = ledger.post(
        body.customer_id, types[body.entry_type], amount, body.description,
        reference, body.entry_date,
    )
    if body.entry_type == "advance" and body.interest_rate:
        interest = body.amount * Decimal(str(body.interest_rate)) / Decimal("100") / Decimal("12")
        ledger.post(
            body.customer_id, "advance_interest", -interest,
            f"Illustrative one-month interest at {body.interest_rate}%",
            reference + "-interest", body.entry_date,
        )
    record_audit(
        db, context.dairy.id, context.user.id, "transaction_created", "ledger_entry",
        entry.id,
        {"entry_type": body.entry_type, "amount": str(body.amount), "customer_id": body.customer_id},
    )
    db.commit()
    return {"created": created, "balance": float(ledger.balance(body.customer_id))}


@app.post("/api/assistant")
def assistant(body: AssistantIn, context: TenantDep, db: Session = Depends(get_db)):
    return AssistantService(db, context.dairy.id).answer(body.query)


@app.post("/api/customers/{customer_id}/whatsapp")
def whatsapp(customer_id: int, context: TenantDep, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter_by(
        id=customer_id, dairy_id=context.dairy.id, active=True
    ).first()
    if not customer:
        raise HTTPException(404, "Customer not found")
    statement = LedgerService(db, context.dairy.id).statement(customer_id)
    lines = [
        f"नमस्कार {customer.name}, तुमचा डेअरी हिशोब:",
        f"सध्याची बाकी: ₹{statement['balance']:,.2f}", "",
    ]
    for entry in statement["entries"][:8]:
        lines.append(f"{entry.entry_date}: {entry.description} — ₹{entry.amount:,.2f}")
    lines.append("धन्यवाद!")
    try:
        return MessagingFactory.create().send(customer.phone, "\n".join(lines))
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/inventory")
def list_inventory(context: TenantDep, db: Session = Depends(get_db)):
    items = db.query(InventoryItem).filter_by(
        dairy_id=context.dairy.id
    ).order_by(InventoryItem.name).all()
    return [
        {
            "id": item.id, "item_code": item.item_code, "name": item.name,
            "unit": item.unit, "quantity": str(item.quantity),
        }
        for item in items
    ]


@app.post("/api/inventory")
def create_inventory_item(
    body: InventoryItemIn, context: OwnerManagerDep, db: Session = Depends(get_db)
):
    item = InventoryItem(dairy_id=context.dairy.id, **body.model_dump())
    db.add(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Inventory item code already exists") from exc
    db.refresh(item)
    return {"id": item.id, "message": "Inventory item saved"}


@app.put("/api/inventory/{item_id}")
def update_inventory_item(
    item_id: int,
    body: InventoryItemIn,
    context: OwnerManagerDep,
    db: Session = Depends(get_db),
):
    item = db.query(InventoryItem).filter_by(
        id=item_id, dairy_id=context.dairy.id
    ).first()
    if not item:
        raise HTTPException(404, "Inventory item not found")
    for key, value in body.model_dump().items():
        setattr(item, key, value)
    db.commit()
    return {"message": "Inventory item updated"}


FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


@app.get("/")
def home():
    return FileResponse(FRONTEND / "index.html")


@app.get("/prototype")
def ui_prototype():
    return FileResponse(FRONTEND / "prototype.html")


@app.get("/app.js")
def js():
    return FileResponse(FRONTEND / "app.js", media_type="application/javascript")


@app.get("/styles.css")
def css():
    return FileResponse(FRONTEND / "styles.css", media_type="text/css")


@app.get("/i18n.js")
def i18n_script():
    return FileResponse(FRONTEND / "i18n.js", media_type="application/javascript")


@app.get("/manifest.json")
def pwa_manifest():
    return FileResponse(FRONTEND / "manifest.json", media_type="application/manifest+json")


@app.get("/service-worker.js")
def service_worker():
    return FileResponse(
        FRONTEND / "service-worker.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/icons/{icon_name}")
def pwa_icon(icon_name: str):
    if icon_name not in {"icon-192.png", "icon-512.png"}:
        raise HTTPException(status_code=404, detail="Icon not found")
    return FileResponse(FRONTEND / "icons" / icon_name, media_type="image/png")
