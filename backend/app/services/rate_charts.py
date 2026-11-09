from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..models import DairyFinancialPolicy, MilkRateChart, MilkRateChartEntry

RATE_QUANTUM = Decimal("0.0001")
MONEY_QUANTUM = Decimal("0.01")


class RateChartError(ValueError):
    pass


class RateChartNotFound(RateChartError):
    pass


def _decimal(value) -> Decimal:
    return Decimal(str(value))


def _round_rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


class RateChartService:
    def __init__(self, db: Session, dairy_id: int, user_id: int):
        self.db = db
        self.dairy_id = dairy_id
        self.user_id = user_id

    def create_draft(self, values: dict) -> MilkRateChart:
        version = (
            self.db.query(func.max(MilkRateChart.version))
            .filter_by(dairy_id=self.dairy_id, milk_type=values["milk_type"])
            .scalar()
            or 0
        ) + 1
        chart = MilkRateChart(
            dairy_id=self.dairy_id,
            version=version,
            created_by=self.user_id,
            status="DRAFT",
            **{key: value for key, value in values.items() if key != "entries"},
        )
        self._validate_chart_shape(chart, values.get("entries", []))
        self.db.add(chart)
        self.db.flush()
        for item in values.get("entries", []):
            self.db.add(MilkRateChartEntry(
                dairy_id=self.dairy_id, chart_id=chart.id, **item
            ))
        self.db.flush()
        return chart

    def update_draft(self, chart_id: int, values: dict) -> MilkRateChart:
        chart = self._chart(chart_id)
        if chart.status != "DRAFT":
            raise RateChartError("An active or retired rate chart is immutable")
        if values["milk_type"] != chart.milk_type:
            raise RateChartError("Create a separate draft to change the milk type")
        for field in (
            "name", "chart_type", "effective_from", "effective_to",
            "fixed_rate", "fat_increment", "snf_increment", "match_policy",
        ):
            setattr(chart, field, values[field])
        self._validate_chart_shape(chart, values.get("entries", []))
        chart.entries.clear()
        self.db.flush()
        for item in values.get("entries", []):
            self.db.add(MilkRateChartEntry(
                dairy_id=self.dairy_id, chart_id=chart.id, **item
            ))
        self.db.flush()
        return chart

    def activate(self, chart_id: int) -> MilkRateChart:
        chart = self._chart(chart_id)
        if chart.status != "DRAFT":
            raise RateChartError("Only a draft chart can be activated")
        self._validate_chart_shape(chart, self._entries_as_values(chart))
        self._validate_complete_chart(chart)
        other_charts = self.db.query(MilkRateChart).filter(
            MilkRateChart.dairy_id == self.dairy_id,
            MilkRateChart.milk_type == chart.milk_type,
            MilkRateChart.status == "ACTIVE",
            MilkRateChart.id != chart.id,
        ).all()
        for other in other_charts:
            if self._ranges_overlap(
                chart.effective_from, chart.effective_to,
                other.effective_from, other.effective_to,
            ):
                if other.effective_to is None and other.effective_from < chart.effective_from:
                    other.effective_to = chart.effective_from - timedelta(days=1)
                else:
                    raise RateChartError(
                        f"Effective dates overlap active chart version {other.version}"
                    )
        chart.status = "ACTIVE"
        self.db.flush()
        return chart

    def get_active_chart(self, milk_type: str, collection_date: date) -> MilkRateChart:
        chart = self.db.query(MilkRateChart).options(
            joinedload(MilkRateChart.entries)
        ).filter(
            MilkRateChart.dairy_id == self.dairy_id,
            MilkRateChart.milk_type == milk_type,
            MilkRateChart.status == "ACTIVE",
            MilkRateChart.effective_from <= collection_date,
            (MilkRateChart.effective_to.is_(None)
             | (MilkRateChart.effective_to >= collection_date)),
        ).first()
        if chart is None:
            raise RateChartError(
                f"No active {milk_type} rate chart is configured for {collection_date}"
            )
        return chart

    def calculate(
        self,
        chart: MilkRateChart,
        quantity: Decimal,
        fat: Decimal | None,
        snf: Decimal | None,
    ) -> tuple[Decimal, Decimal]:
        quantity = _decimal(quantity)
        if quantity <= 0:
            raise RateChartError("Milk quantity must be greater than zero")
        rate = self._rate_for_chart(chart, fat, snf)
        amount = _round_money(quantity * rate)
        return rate, amount

    def preview(
        self,
        milk_type: str,
        collection_date: date,
        quantity: Decimal,
        fat: Decimal | None,
        snf: Decimal | None,
    ) -> dict:
        chart = self.get_active_chart(milk_type, collection_date)
        rate, amount = self.calculate(chart, quantity, fat, snf)
        return {
            "chart_id": chart.id,
            "chart_version": chart.version,
            "chart_name": chart.name,
            "milk_type": chart.milk_type,
            "chart_type": chart.chart_type,
            "collection_date": collection_date.isoformat(),
            "quantity_litres": str(quantity),
            "rate_per_litre": str(rate),
            "amount": str(amount),
            "rounding": "rate 4 decimal places; amount 2 decimal places; ROUND_HALF_UP",
        }

    def _chart(self, chart_id: int) -> MilkRateChart:
        chart = self.db.query(MilkRateChart).options(
            joinedload(MilkRateChart.entries)
        ).filter_by(id=chart_id, dairy_id=self.dairy_id).first()
        if chart is None:
            raise RateChartNotFound("Rate chart not found")
        return chart

    @staticmethod
    def _entries_as_values(chart: MilkRateChart) -> list[dict]:
        return [
            {"fat_value": item.fat_value, "snf_value": item.snf_value, "rate": item.rate}
            for item in chart.entries
        ]

    @staticmethod
    def _validate_chart_shape(chart: MilkRateChart, entries: list[dict]) -> None:
        if chart.effective_to and chart.effective_to < chart.effective_from:
            raise RateChartError("Effective end date must not be before start date")
        if chart.chart_type == "FIXED_RATE":
            if chart.fixed_rate is None or entries:
                raise RateChartError("Fixed-rate charts need a fixed rate and no matrix entries")
            return
        if chart.fixed_rate is not None:
            raise RateChartError("Only FIXED_RATE charts can set a fixed rate")
        if not entries:
            raise RateChartError("Add rate entries before activating this chart")
        if chart.chart_type == "FAT_ONLY":
            if chart.match_policy == "BILINEAR_INTERPOLATION":
                raise RateChartError(
                    "BILINEAR_INTERPOLATION requires a FAT/SNF matrix chart"
                )
            if any(item.get("fat_value") is None or item.get("snf_value") is not None for item in entries):
                raise RateChartError("FAT_ONLY entries need FAT and must not include SNF")
            fat_values = [_decimal(item["fat_value"]) for item in entries]
            if len(fat_values) != len(set(fat_values)):
                raise RateChartError("FAT_ONLY chart contains duplicate FAT points")
            if chart.match_policy == "INTERPOLATE" and len(fat_values) < 2:
                raise RateChartError("Interpolation requires at least two FAT entries")
            return
        if any(item.get("fat_value") is None or item.get("snf_value") is None for item in entries):
            raise RateChartError("FAT_SNF_MATRIX entries need both FAT and SNF values")

    @staticmethod
    def _validate_complete_chart(chart: MilkRateChart) -> None:
        if chart.chart_type == "FIXED_RATE":
            return
        entries = RateChartService._entries_as_values(chart)
        fats = sorted({_decimal(item["fat_value"]) for item in entries})
        if chart.chart_type == "FAT_ONLY":
            return
        snfs = sorted({_decimal(item["snf_value"]) for item in entries})
        coordinates = {
            (_decimal(item["fat_value"]), _decimal(item["snf_value"]))
            for item in entries
        }
        missing = [
            (fat, snf) for fat in fats for snf in snfs
            if (fat, snf) not in coordinates
        ]
        if missing:
            raise RateChartError(
                f"Matrix is incomplete: {len(missing)} FAT/SNF rate cell(s) are missing"
            )

    @staticmethod
    def _ranges_overlap(a_start: date, a_end: date | None, b_start: date, b_end: date | None) -> bool:
        return (a_end is None or b_start <= a_end) and (b_end is None or a_start <= b_end)

    @staticmethod
    def _rate_for_chart(
        chart: MilkRateChart, fat: Decimal | None, snf: Decimal | None
    ) -> Decimal:
        if chart.chart_type == "FIXED_RATE":
            return _round_rate(_decimal(chart.fixed_rate))
        if fat is None:
            raise RateChartError("FAT is required for this rate chart")
        fat = _decimal(fat)
        if fat <= 0 or fat > Decimal("15"):
            raise RateChartError("FAT must be greater than 0 and no more than 15")
        snf = _decimal(snf) if snf is not None else None
        if chart.chart_type == "FAT_ONLY":
            if chart.match_policy == "BILINEAR_INTERPOLATION":
                raise RateChartError(
                    "BILINEAR_INTERPOLATION requires a FAT/SNF matrix chart"
                )
            points = sorted(
                ((_decimal(item.fat_value), _decimal(item.rate)) for item in chart.entries),
                key=lambda pair: pair[0],
            )
            point = RateChartService._choose_point(
                points, fat, chart.match_policy, "FAT"
            )
            return _round_rate(point)
        if snf is None:
            raise RateChartError("SNF is required for a FAT/SNF matrix")
        if snf <= 0 or snf > Decimal("20"):
            raise RateChartError("SNF must be greater than 0 and no more than 20")
        cells = {
            (_decimal(item.fat_value), _decimal(item.snf_value)): _decimal(item.rate)
            for item in chart.entries
        }
        if chart.match_policy == "EXACT_MATCH":
            if (fat, snf) not in cells:
                raise RateChartError("No exact FAT/SNF rate entry exists")
            return _round_rate(cells[(fat, snf)])
        fats = sorted({key[0] for key in cells})
        snfs = sorted({key[1] for key in cells})
        if chart.match_policy == "NEAREST_LOWER":
            lower_fats = [value for value in fats if value <= fat]
            lower_snfs = [value for value in snfs if value <= snf]
            if not lower_fats or not lower_snfs:
                raise RateChartError("No lower FAT/SNF rate entry is available")
            return _round_rate(cells[(lower_fats[-1], lower_snfs[-1])])
        if chart.match_policy == "NEAREST":
            fat_scale = _decimal(chart.fat_increment or 1)
            snf_scale = _decimal(chart.snf_increment or 1)
            coordinate = min(
                cells,
                key=lambda point: (
                    ((point[0] - fat) / fat_scale) ** 2
                    + ((point[1] - snf) / snf_scale) ** 2,
                    point[0], point[1],
                ),
            )
            return _round_rate(cells[coordinate])
        if chart.match_policy not in {"INTERPOLATE", "BILINEAR_INTERPOLATION"}:
            raise RateChartError(f"Unsupported rate calculation policy: {chart.match_policy}")
        fat_low, fat_high = RateChartService._bounds(fats, fat)
        snf_low, snf_high = RateChartService._bounds(snfs, snf)
        corners = [
            (fat_low, snf_low), (fat_low, snf_high),
            (fat_high, snf_low), (fat_high, snf_high),
        ]
        if any(point not in cells for point in corners):
            raise RateChartError("Interpolation requires all surrounding matrix values")
        fat_ratio = Decimal(0) if fat_low == fat_high else (fat - fat_low) / (fat_high - fat_low)
        snf_ratio = Decimal(0) if snf_low == snf_high else (snf - snf_low) / (snf_high - snf_low)
        low_rate = cells[(fat_low, snf_low)] * (1 - snf_ratio) + cells[(fat_low, snf_high)] * snf_ratio
        high_rate = cells[(fat_high, snf_low)] * (1 - snf_ratio) + cells[(fat_high, snf_high)] * snf_ratio
        return _round_rate(low_rate * (1 - fat_ratio) + high_rate * fat_ratio)

    @staticmethod
    def _bounds(values: list[Decimal], target: Decimal) -> tuple[Decimal, Decimal]:
        lower = [value for value in values if value <= target]
        upper = [value for value in values if value >= target]
        if not lower or not upper:
            raise RateChartError("Interpolation value is outside the configured matrix")
        return lower[-1], upper[0]

    @staticmethod
    def _choose_point(
        points: list[tuple[Decimal, Decimal]],
        value: Decimal,
        policy: str,
        label: str,
    ) -> Decimal:
        by_value = dict(points)
        if policy == "EXACT_MATCH":
            if value not in by_value:
                raise RateChartError(f"No exact {label} rate entry exists")
            return by_value[value]
        if policy == "NEAREST_LOWER":
            lower = [point for point, _ in points if point <= value]
            if not lower:
                raise RateChartError(f"No lower {label} rate entry is available")
            return by_value[lower[-1]]
        if policy == "NEAREST":
            nearest = min(points, key=lambda pair: (abs(pair[0] - value), pair[0]))
            return nearest[1]
        low, high = RateChartService._bounds([point for point, _ in points], value)
        if low == high:
            return by_value[low]
        ratio = (value - low) / (high - low)
        return by_value[low] * (1 - ratio) + by_value[high] * ratio


class MilkRateCalculationService:
    """Resolve and calculate one milk entry against a dairy's effective chart."""

    def __init__(self, db: Session, dairy_id: int):
        self.db = db
        self.dairy_id = dairy_id

    def calculate(
        self,
        milk_type: str | None,
        fat: Decimal | None,
        snf: Decimal | None,
        quantity: Decimal | None,
        collection_date: date | None,
        chart_version_id: int | None = None,
        customer_id: int | None = None,
    ) -> dict:
        warnings: list[str] = []
        if customer_id is not None:
            from ..models import Customer

            if not self.db.query(Customer.id).filter_by(
                id=customer_id, dairy_id=self.dairy_id
            ).first():
                warnings.append("Customer does not belong to this dairy")
                return self._result("NEEDS_REVIEW", warnings)
        if collection_date is None:
            warnings.append("Collection date is required")
            return self._result("NEEDS_REVIEW", warnings)
        if milk_type not in {"cow", "buffalo"}:
            warnings.append("Select cow or buffalo milk")
            return self._result("NEEDS_REVIEW", warnings)
        if quantity is None or _decimal(quantity) <= 0:
            warnings.append("Enter a positive milk quantity")
            return self._result("NEEDS_REVIEW", warnings)
        if _decimal(quantity) > Decimal("200"):
            warnings.append("Milk quantity must be 200 litres or less")
            return self._result("NEEDS_REVIEW", warnings)
        if fat is not None and (_decimal(fat) <= 0 or _decimal(fat) > Decimal("15")):
            warnings.append("FAT must be greater than 0 and no more than 15")
            return self._result("NEEDS_REVIEW", warnings)
        if snf is not None and (_decimal(snf) <= 0 or _decimal(snf) > Decimal("20")):
            warnings.append("SNF must be greater than 0 and no more than 20")
            return self._result("NEEDS_REVIEW", warnings)

        try:
            chart_service = RateChartService(self.db, self.dairy_id, 0)
            if chart_version_id is None:
                chart = chart_service.get_active_chart(milk_type, collection_date)
            else:
                chart = self.db.query(MilkRateChart).options(
                    joinedload(MilkRateChart.entries)
                ).filter_by(id=chart_version_id, dairy_id=self.dairy_id).first()
                if (
                    chart is None or chart.milk_type != milk_type
                    or chart.status not in {"ACTIVE", "RETIRED"}
                    or chart.effective_from > collection_date
                    or (chart.effective_to and chart.effective_to < collection_date)
                ):
                    warnings.append("Selected rate chart version is not valid for this dairy, milk type, or date")
                    return self._result("NEEDS_REVIEW", warnings)
            rate, amount = chart_service.calculate(
                chart, _decimal(quantity), fat, snf
            )
        except RateChartError as exc:
            warnings.append(str(exc))
            return self._result("NEEDS_REVIEW", warnings)

        if chart.chart_type == "FIXED_RATE":
            method = "FIXED_RATE"
        elif chart.chart_type == "FAT_ONLY" and chart.match_policy == "INTERPOLATE":
            method = "LINEAR_INTERPOLATION"
        elif chart.chart_type == "FAT_ONLY":
            method = chart.match_policy
        elif (
            chart.chart_type == "FAT_SNF_MATRIX"
            and chart.match_policy in {"INTERPOLATE", "BILINEAR_INTERPOLATION"}
        ):
            method = "BILINEAR_INTERPOLATION"
        else:
            method = chart.match_policy

        return {
            "rate_per_litre": str(rate),
            "total_amount": str(amount),
            "chart_version_id": chart.id,
            "chart_version": chart.version,
            "chart_name": chart.name,
            "chart_type": chart.chart_type,
            "milk_type": chart.milk_type,
            "collection_date": collection_date.isoformat(),
            "quantity_litres": str(_decimal(quantity)),
            "calculation_method": method,
            "calculation_policy": (
                "BILINEAR_INTERPOLATION"
                if chart.chart_type == "FAT_SNF_MATRIX"
                and chart.match_policy == "INTERPOLATE"
                else chart.match_policy
            ),
            "calculation_status": "CALCULATED",
            "rounding": "rate 4 decimal places; amount 2 decimal places; ROUND_HALF_UP",
            "warnings": warnings,
        }

    @staticmethod
    def _result(status: str, warnings: list[str]) -> dict:
        return {
            "rate_per_litre": None,
            "total_amount": None,
            "chart_version_id": None,
            "chart_version": None,
            "chart_name": None,
            "chart_type": None,
            "milk_type": None,
            "collection_date": None,
            "quantity_litres": None,
            "calculation_method": None,
            "calculation_policy": None,
            "calculation_status": status,
            "rounding": "rate 4 decimal places; amount 2 decimal places; ROUND_HALF_UP",
            "warnings": warnings,
        }

    def calculate_row(self, row, require_confidence: bool = True) -> dict:
        result = self.calculate(
            milk_type=row.milk_type,
            fat=Decimal(str(row.fat)) if row.fat is not None else None,
            snf=Decimal(str(row.snf)) if row.snf is not None else None,
            quantity=Decimal(str(row.quantity)) if row.quantity is not None else None,
            collection_date=row.collection_date,
            customer_id=row.customer_id,
        )
        try:
            confidence_threshold = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "0.70"))
        except ValueError:
            confidence_threshold = 0.70
        if (
            require_confidence
            and result["calculation_status"] == "CALCULATED"
            and float(row.confidence or 0) < confidence_threshold
        ):
            result = {
                **result,
                "calculation_status": "NEEDS_REVIEW",
                "warnings": [
                    *result["warnings"],
                    "Backend-validated extraction confidence is below the review threshold",
                ],
            }
        row.rate_chart_id = result["chart_version_id"]
        row.rate_per_litre = (
            Decimal(result["rate_per_litre"])
            if result["rate_per_litre"] is not None else None
        )
        row.calculated_amount = (
            Decimal(result["total_amount"])
            if result["total_amount"] is not None else None
        )
        row.calculation_method = result["calculation_method"]
        row.calculation_policy = result["calculation_policy"]
        row.calculation_status = result["calculation_status"]
        row.calculation_warnings = result["warnings"]
        row.calculated_at = datetime.now(timezone.utc)
        return result


class DairyFinancialPolicyService:
    def __init__(self, db: Session, dairy_id: int, user_id: int):
        self.db = db
        self.dairy_id = dairy_id
        self.user_id = user_id

    def create_version(self, values: dict) -> DairyFinancialPolicy:
        if values.get("effective_to") and values["effective_to"] < values["effective_from"]:
            raise RateChartError("Effective end date must not be before start date")
        policies = self.db.query(DairyFinancialPolicy).filter_by(
            dairy_id=self.dairy_id
        ).all()
        for policy in policies:
            if RateChartService._ranges_overlap(
                values["effective_from"], values.get("effective_to"),
                policy.effective_from, policy.effective_to,
            ):
                if policy.effective_to is None and policy.effective_from < values["effective_from"]:
                    policy.effective_to = values["effective_from"] - timedelta(days=1)
                else:
                    raise RateChartError(
                        f"Effective dates overlap dairy policy version {policy.version}"
                    )
        version = max((policy.version for policy in policies), default=0) + 1
        policy = DairyFinancialPolicy(
            dairy_id=self.dairy_id,
            version=version,
            created_by=self.user_id,
            **values,
        )
        self.db.add(policy)
        self.db.flush()
        return policy

    def list_versions(self) -> list[DairyFinancialPolicy]:
        return self.db.query(DairyFinancialPolicy).filter_by(
            dairy_id=self.dairy_id
        ).order_by(DairyFinancialPolicy.version.desc()).all()

    def effective_on(self, effective_date: date) -> DairyFinancialPolicy | None:
        return self.db.query(DairyFinancialPolicy).filter(
            DairyFinancialPolicy.dairy_id == self.dairy_id,
            DairyFinancialPolicy.effective_from <= effective_date,
            (DairyFinancialPolicy.effective_to.is_(None)
             | (DairyFinancialPolicy.effective_to >= effective_date)),
        ).first()
