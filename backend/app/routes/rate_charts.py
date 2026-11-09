from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import MilkRateChart
from ..schemas import (
    DairyFinancialPolicyIn, MilkEntryCalculationIn, MilkRateChartIn,
    MilkRatePreviewIn,
)
from ..security import OwnerDep, TenantDep
from ..services.audit import record_audit
from ..services.rate_charts import (
    DairyFinancialPolicyService, MilkRateCalculationService, RateChartError,
    RateChartNotFound, RateChartService,
)

router = APIRouter(tags=["milk rates and dairy settings"])


def _service(db: Session, context) -> RateChartService:
    return RateChartService(db, context.dairy.id, context.user.id)


def _policy_service(db: Session, context) -> DairyFinancialPolicyService:
    return DairyFinancialPolicyService(db, context.dairy.id, context.user.id)


def _chart_payload(chart: MilkRateChart) -> dict:
    return {
        "id": chart.id,
        "version": chart.version,
        "name": chart.name,
        "milk_type": chart.milk_type,
        "chart_type": chart.chart_type,
        "status": chart.status,
        "effective_from": chart.effective_from.isoformat(),
        "effective_to": chart.effective_to.isoformat() if chart.effective_to else None,
        "fixed_rate": str(chart.fixed_rate) if chart.fixed_rate is not None else None,
        "fat_increment": str(chart.fat_increment) if chart.fat_increment is not None else None,
        "snf_increment": str(chart.snf_increment) if chart.snf_increment is not None else None,
        "match_policy": chart.match_policy,
        "entries": [
            {
                "fat_value": str(entry.fat_value) if entry.fat_value is not None else None,
                "snf_value": str(entry.snf_value) if entry.snf_value is not None else None,
                "rate": str(entry.rate),
            }
            for entry in chart.entries
        ],
    }


def _policy_payload(policy) -> dict:
    return {
        "id": policy.id,
        "version": policy.version,
        "effective_from": policy.effective_from.isoformat(),
        "effective_to": policy.effective_to.isoformat() if policy.effective_to else None,
        "anamat_enabled": policy.anamat_enabled,
        "anamat_rate": str(policy.anamat_rate),
        "anamat_cow_rate": str(policy.anamat_cow_rate) if policy.anamat_cow_rate is not None else None,
        "anamat_buffalo_rate": (
            str(policy.anamat_buffalo_rate) if policy.anamat_buffalo_rate is not None else None
        ),
        "interest_enabled": policy.interest_enabled,
        "annual_interest_rate": str(policy.annual_interest_rate),
        "interest_period": policy.interest_period,
        "interest_method": policy.interest_method,
        "day_count_convention": policy.day_count_convention,
        "billing_term_days": policy.billing_term_days,
    }


@router.get("/api/rate-charts")
def list_rate_charts(
    context: TenantDep,
    milk_type: str | None = Query(default=None, pattern="^(cow|buffalo)$"),
    db: Session = Depends(get_db),
):
    query = db.query(MilkRateChart).options(
        joinedload(MilkRateChart.entries)
    ).filter_by(dairy_id=context.dairy.id)
    if milk_type:
        query = query.filter_by(milk_type=milk_type)
    return [
        _chart_payload(chart)
        for chart in query.order_by(MilkRateChart.version.desc()).all()
    ]


@router.post("/api/rate-charts", status_code=201)
def create_rate_chart(
    body: MilkRateChartIn,
    context: OwnerDep,
    db: Session = Depends(get_db),
):
    try:
        chart = _service(db, context).create_draft(body.model_dump())
        record_audit(
            db, context.dairy.id, context.user.id, "milk_rate_chart_drafted",
            "milk_rate_chart", chart.id,
            {"version": chart.version, "milk_type": chart.milk_type, "chart_type": chart.chart_type},
        )
        db.commit()
        db.refresh(chart)
        return _chart_payload(chart)
    except RateChartError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Duplicate or conflicting rate chart values") from exc


@router.put("/api/rate-charts/{chart_id}")
def update_rate_chart(
    chart_id: int,
    body: MilkRateChartIn,
    context: OwnerDep,
    db: Session = Depends(get_db),
):
    try:
        chart = _service(db, context).update_draft(chart_id, body.model_dump())
        record_audit(
            db, context.dairy.id, context.user.id, "milk_rate_chart_draft_updated",
            "milk_rate_chart", chart.id,
            {"version": chart.version, "milk_type": chart.milk_type, "chart_type": chart.chart_type},
        )
        db.commit()
        return _chart_payload(chart)
    except RateChartNotFound as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except RateChartError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Duplicate or conflicting rate chart values") from exc


@router.post("/api/rate-charts/{chart_id}/activate")
def activate_rate_chart(
    chart_id: int,
    context: OwnerDep,
    db: Session = Depends(get_db),
):
    try:
        chart = _service(db, context).activate(chart_id)
        record_audit(
            db, context.dairy.id, context.user.id, "milk_rate_chart_activated",
            "milk_rate_chart", chart.id, {"version": chart.version},
        )
        db.commit()
        return {"id": chart.id, "version": chart.version, "status": chart.status}
    except RateChartNotFound as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except RateChartError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/rate-charts/preview")
def preview_rate(
    body: MilkRatePreviewIn,
    context: TenantDep,
    db: Session = Depends(get_db),
):
    result = MilkRateCalculationService(db, context.dairy.id).calculate(
            milk_type=body.milk_type,
            collection_date=body.collection_date,
            quantity=body.quantity,
            fat=body.fat,
            snf=body.snf,
        )
    if result["calculation_status"] != "CALCULATED":
        raise HTTPException(422, "; ".join(result["warnings"]))
    return {
        "chart_id": result["chart_version_id"],
        "chart_version": result["chart_version"],
        "chart_name": result["chart_name"],
        "milk_type": result["milk_type"],
        "chart_type": result["chart_type"],
        "collection_date": result["collection_date"],
        "quantity_litres": result["quantity_litres"],
        "rate_per_litre": result["rate_per_litre"],
        "amount": result["total_amount"],
        "rounding": result["rounding"],
        "calculation_method": result["calculation_method"],
        "calculation_policy": result["calculation_policy"],
        "calculation_status": result["calculation_status"],
        "warnings": result["warnings"],
    }


@router.post("/api/milk-entries/calculate")
def calculate_milk_entry(
    body: MilkEntryCalculationIn,
    context: TenantDep,
    db: Session = Depends(get_db),
):
    return MilkRateCalculationService(db, context.dairy.id).calculate(
        milk_type=body.milk_type,
        fat=body.fat,
        snf=body.snf,
        quantity=body.quantity,
        collection_date=body.collection_date,
        chart_version_id=body.chart_version_id,
        customer_id=body.customer_id,
    )


@router.get("/api/settings/financial-policy")
def list_financial_policies(context: TenantDep, db: Session = Depends(get_db)):
    policies = _policy_service(db, context).list_versions()
    return [_policy_payload(policy) for policy in policies]


@router.post("/api/settings/financial-policy", status_code=201)
def create_financial_policy(
    body: DairyFinancialPolicyIn,
    context: OwnerDep,
    db: Session = Depends(get_db),
):
    try:
        policy = _policy_service(db, context).create_version(body.model_dump())
        record_audit(
            db, context.dairy.id, context.user.id, "financial_policy_created",
            "dairy_financial_policy", policy.id,
            {"version": policy.version, "effective_from": policy.effective_from.isoformat()},
        )
        db.commit()
        db.refresh(policy)
        return _policy_payload(policy)
    except RateChartError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Financial policy conflicts with existing data") from exc
