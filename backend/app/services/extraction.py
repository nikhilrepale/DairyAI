import logging
import os
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import Customer, ScanBatch, ScanRow
from .image_preprocessing import ImagePreprocessingService
from .ocr import OCRService
from .rate_charts import MilkRateCalculationService
from .image_storage import get_store

logger = logging.getLogger(__name__)


class ExtractionService:
    def __init__(
        self,
        db: Session,
        dairy_id: int,
        preprocessing_service: ImagePreprocessingService | None = None,
        ocr_service: OCRService | None = None,
    ):
        self.db, self.dairy_id = db, dairy_id
        debug_dir = os.getenv("IMAGE_PREPROCESS_DEBUG_DIR")
        self.preprocessing_service = preprocessing_service or ImagePreprocessingService(debug_dir)
        self.ocr_service = ocr_service or OCRService()

    def scan(self, image_bytes: bytes, filename: str, kind: str, media_type: str | None = None):
        register_type = self.register_type(kind)
        # Vision LLMs understand the complete document layout and handwriting.
        # Avoid costly OpenCV perspective/grid/cell processing for them; it can
        # also distort small handwritten FAT/SNF digits. Retain the existing
        # OpenCV pipeline for classical OCR providers, where crops are useful.
        configured_providers = getattr(self.ocr_service, "providers", [])
        vision_only = bool(configured_providers) and all(
            getattr(provider, "name", "") in {"gemini", "ollama"}
            for provider in configured_providers
        )
        if vision_only:
            result = self.ocr_service.recognize(
                image_bytes, [], register_type=register_type
            )
            image_layout = {
                "coordinate_space": "original_image_pixels",
                "table_regions": [],
                "cells": [],
                "warnings": [],
                "extraction_mode": "vision_llm_full_image",
            }
        else:
            preprocessing = self.preprocessing_service.process(image_bytes)
            result = self.ocr_service.recognize(
                preprocessing.processed_image, preprocessing.cells, register_type=register_type
            )
            image_layout = preprocessing.layout()
        image_layout["ocr"] = {
            "provider": result["provider"],
            "metadata": result["metadata"],
            "text": result["text"],
            "words": result["words"],
        }
        cells_by_key = {
            (cell["table_index"], cell["row_index"], cell["column_index"]): cell
            for cell in image_layout["cells"]
        }
        for cell_result in result["cells"]:
            key = (
                cell_result["table_index"],
                cell_result["row_index"],
                cell_result["column_index"],
            )
            layout_cell = cells_by_key.get(key)
            if layout_cell is not None:
                layout_cell["ocr"] = {
                    "text": cell_result["text"],
                    "confidence": cell_result["confidence"],
                    "words": cell_result["words"],
                    # Keep cell text and coordinates, not hundreds of base64 crops in Postgres.
                }

        batch = ScanBatch(
            dairy_id=self.dairy_id,
            kind=kind,
            filename=filename,
            raw_text=result["text"],
            image_layout=image_layout,
            original_image=image_bytes if get_store() is None else None,
            image_media_type=media_type,
            status="pending",
        )
        self.db.add(batch)
        self.db.flush()
        store = get_store()
        if store is not None:
            image_layout['image_storage_key'] = store.store(
                self.dairy_id, batch.id, image_bytes, media_type or 'image/jpeg')
        customers = self.db.query(Customer).filter_by(
            dairy_id=self.dairy_id, active=True
        ).all()
        structured = result.get("structured_extraction")
        if structured:
            structured = {
                **structured,
                "provider": result["provider"],
                "model": result["metadata"].get("model"),
            }
            batch.image_layout["structured_extraction"] = structured
            items = self._rows_from_structured(structured, register_type, customers)
        else:
            items = self._rows_from_cells(result["cells"])
        duplicate_keys = set()
        calculation_service = (
            MilkRateCalculationService(self.db, self.dairy_id)
            if register_type == "MILK_REGISTER" else None
        )
        for item in items:
            name = str(item.get("name", "")).strip()
            code = str(item.get("code", "")).strip()
            match = self._match(name, customers, code)
            field_values = item.get("extracted_fields", {})
            field_confidence = item.get("field_confidence", {})
            warnings = item.get("warnings", [])
            duplicate_key = (
                (match.id if match else code.casefold()),
                item.get("collection_date"),
                item.get("session"),
                item.get("product_code") or item.get("product_name"),
            )
            duplicate_warning = None
            if duplicate_key in duplicate_keys:
                duplicate_warning = "Possible duplicate row in this register image"
                warnings.append(duplicate_warning)
            duplicate_keys.add(duplicate_key)
            validated_confidence = item.get("validated_confidence", item.get("confidence", 0.0))
            row = ScanRow(
                dairy_id=self.dairy_id,
                batch_id=batch.id,
                customer_id=match.id if match else None,
                original_customer_id=match.id if match else None,
                extracted_name=name,
                extracted_code=code,
                quantity=item.get("quantity"),
                fat=item.get("fat"),
                snf=item.get("snf"),
                original_quantity=item.get("quantity"),
                original_fat=item.get("fat"),
                original_snf=item.get("snf"),
                collection_date=item.get("collection_date"),
                session=item.get("session"),
                milk_type=item.get("milk_type"),
                description=item.get("description", ""),
                confidence=validated_confidence,
                model_row_confidence=item.get("model_row_confidence"),
                extracted_fields=field_values,
                field_confidence=field_confidence,
                extraction_warnings=warnings,
                duplicate_warning=duplicate_warning,
                approval_status="pending",
            )
            self.db.add(row)
            if calculation_service is not None:
                calculation_service.calculate_row(row)
        # SQLAlchemy JSON columns do not detect in-place nested dictionary edits.
        # Persist structured rate-chart extraction and private image-storage keys.
        flag_modified(batch, "image_layout")
        self.db.flush()
        return batch, result["provider"], image_layout

    @staticmethod
    def register_type(kind: str) -> str:
        aliases = {
            "milk": "MILK_REGISTER",
            "MILK_REGISTER": "MILK_REGISTER",
            "feed": "CATTLE_FEED_PURCHASE",
            "CATTLE_FEED_PURCHASE": "CATTLE_FEED_PURCHASE",
            "rate_chart": "RATE_CHART",
            "RATE_CHART": "RATE_CHART",
            "advance": "ADVANCE_REGISTER",
            "ADVANCE_REGISTER": "ADVANCE_REGISTER",
        }
        try:
            return aliases[kind]
        except KeyError as exc:
            raise ValueError(f"Unsupported register type '{kind}'") from exc

    @classmethod
    def _rows_from_structured(
        cls, structured: dict, register_type: str, customers: list[Customer]
    ) -> list[dict]:
        if register_type == "RATE_CHART":
            return []
        output = []
        seen_codes: set[str] = set()
        threshold = cls._confidence_threshold()
        for extracted in structured.get("extracted_rows", []):
            fields = {
                key: value for key, value in extracted.items()
                if isinstance(value, dict) and {"value", "raw_text"} <= value.keys()
            }
            warnings = list(extracted.get("warnings") or [])
            name = cls._field_value(fields, "customer_name")
            code = cls._field_value(fields, "customer_code")
            match = cls._match(name or "", customers, str(code or ""))
            match_confidence = cls._customer_match_confidence(code, name, match, customers)
            confidence_map: dict[str, float] = {}
            confidence_map["customer_match"] = match_confidence
            important: list[float] = []
            declared_confidence = extracted.get("field_confidence", {})
            if not isinstance(declared_confidence, dict):
                declared_confidence = {}
            for field_name, field in fields.items():
                score = cls._validated_field_confidence(
                    field, field_name, declared_confidence.get(field_name)
                )
                confidence_map[field_name] = score
                if field_name in cls._important_fields(register_type):
                    important.append(score)
                if score < threshold:
                    warnings.append(f"Uncertain {field_name.replace('_', ' ')}")
            for field_name in cls._important_fields(register_type):
                if field_name not in fields:
                    confidence_map[field_name] = 0.0
                    important.append(0.0)
                    warnings.append(f"Missing {field_name.replace('_', ' ')}")
            if register_type == "CATTLE_FEED_PURCHASE":
                product_scores = [
                    confidence_map[name] for name in ("product_code", "product_name")
                    if name in confidence_map
                ]
                product_match = max(product_scores, default=0.0)
                confidence_map["product_match"] = product_match
                important.append(product_match)
                if not product_scores:
                    warnings.append("Missing product code/name")
            if match_confidence < threshold:
                warnings.append("Customer could not be confidently matched")
            if code and str(code).casefold() in seen_codes:
                warnings.append("Duplicate customer code in register")
            if code:
                seen_codes.add(str(code).casefold())
            required = cls._required_fields(register_type)
            missing = [field for field in required if cls._field_value(fields, field) is None]
            if missing:
                warnings.append("Missing required fields: " + ", ".join(missing))
            if not important:
                important = [match_confidence]
            validated = min(important + [match_confidence])
            if missing:
                validated = min(validated, 0.49)
            if extracted.get("row_confidence") is not None:
                validated = min(validated, cls._confidence(extracted["row_confidence"]))
            quantity_key = {
                "MILK_REGISTER": "quantity_litres",
                "CATTLE_FEED_PURCHASE": "quantity",
                "ADVANCE_REGISTER": "amount",
            }[register_type]
            quantity = cls._numeric_field(fields, quantity_key)
            fat = cls._numeric_field(fields, "fat_percent")
            snf = cls._numeric_field(fields, "snf_percent")
            if quantity is not None and (
                quantity <= 0
                or (register_type == "MILK_REGISTER" and quantity > 200)
            ):
                warnings.append("Quantity is outside the supported review range")
                confidence_map[quantity_key] = min(confidence_map.get(quantity_key, 0), 0.25)
                validated = min(validated, 0.25)
            for field_name, value, maximum in (
                ("fat_percent", fat, Decimal("15")),
                ("snf_percent", snf, Decimal("20")),
            ):
                if value is not None and (value <= 0 or value > maximum):
                    warnings.append(f"{field_name} is outside the plausible review range")
                    confidence_map[field_name] = min(confidence_map.get(field_name, 0), 0.25)
                    validated = min(validated, 0.25)
            date_value = cls._date_field(fields, "date")
            if register_type == "MILK_REGISTER" and date_value is None:
                warnings.append("Collection date is missing or unreadable")
                validated = min(validated, 0.49)
            session = cls._enum_value(fields, "session", {"morning", "evening"})
            milk_type = cls._enum_value(fields, "milk_type", {"cow", "buffalo"})
            product_code = cls._field_value(fields, "product_code")
            product_name = cls._field_value(fields, "product_name")
            if register_type == "CATTLE_FEED_PURCHASE" and not (product_code or product_name):
                warnings.append("Missing product code/name")
                validated = min(validated, 0.49)
            output.append({
                "name": str(name or ""),
                "code": str(code or ""),
                "quantity": float(quantity) if quantity is not None else None,
                "fat": float(fat) if fat is not None else None,
                "snf": float(snf) if snf is not None else None,
                "collection_date": date_value,
                "session": session,
                "milk_type": milk_type,
                "product_code": str(product_code or ""),
                "product_name": str(product_name or ""),
                "description": cls._description_from_fields(fields),
                "confidence": validated,
                "validated_confidence": validated,
                "model_row_confidence": cls._confidence(extracted.get("row_confidence"))
                if extracted.get("row_confidence") is not None else None,
                "field_confidence": confidence_map,
                "extracted_fields": {
                    key: {
                        "value": value.get("value"),
                        "raw_text": value.get("raw_text"),
                        "model_confidence": value.get("confidence"),
                        "validated_confidence": confidence_map.get(key, 0.0),
                        "bounding_box": value.get("bounding_box"),
                        "uncertainty_reason": value.get("uncertainty_reason"),
                        "corrected_value": value.get("value"),
                    }
                    for key, value in fields.items()
                },
                "warnings": list(dict.fromkeys(warnings)),
            })
        return output

    @staticmethod
    def _confidence_threshold() -> float:
        return ExtractionService.confidence_thresholds()["warning"]

    @staticmethod
    def confidence_thresholds() -> dict[str, float]:
        defaults = {"green": 0.90, "amber": 0.70, "warning": 0.70}
        values = {}
        names = {
            "green": "AI_CONFIDENCE_GREEN_THRESHOLD",
            "amber": "AI_CONFIDENCE_AMBER_THRESHOLD",
            "warning": "AI_CONFIDENCE_THRESHOLD",
        }
        for key, env_name in names.items():
            try:
                values[key] = float(os.getenv(env_name, str(defaults[key])))
            except ValueError:
                logger.warning("Invalid %s; using %.2f", env_name, defaults[key])
                values[key] = defaults[key]
        if not (0 <= values["amber"] <= values["green"] <= 1):
            logger.warning("Invalid AI confidence color thresholds; using defaults")
            values["green"], values["amber"] = defaults["green"], defaults["amber"]
        if not 0 <= values["warning"] <= 1:
            logger.warning("Invalid AI_CONFIDENCE_THRESHOLD; using %.2f", defaults["warning"])
            values["warning"] = defaults["warning"]
        return values

    @staticmethod
    def _confidence(value) -> float:
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _field_value(fields: dict, name: str):
        field = fields.get(name)
        return field.get("value") if field else None

    @classmethod
    def _numeric_field(cls, fields: dict, name: str) -> Decimal | None:
        value = cls._field_value(fields, name)
        if value is None or value == "":
            return None
        text = str(value).strip().translate(str.maketrans("०१२३४५६७८९", "0123456789"))
        text = text.translate(str.maketrans("૦૧૨૩૪૫૬૭૮૯", "0123456789"))
        try:
            return Decimal(text.replace(",", "."))
        except InvalidOperation:
            return None

    @classmethod
    def _date_field(cls, fields: dict, name: str) -> date | None:
        value = cls._field_value(fields, name)
        if value is None:
            return None
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None

    @staticmethod
    def _enum_value(fields: dict, name: str, allowed: set[str]) -> str | None:
        value = fields.get(name, {}).get("value")
        normalized = str(value).strip().lower() if value is not None else ""
        return normalized if normalized in allowed else None

    @staticmethod
    def _important_fields(register_type: str) -> tuple[str, ...]:
        if register_type == "MILK_REGISTER":
            return ("customer_code", "quantity_litres", "fat_percent", "snf_percent")
        if register_type == "CATTLE_FEED_PURCHASE":
            return ("customer_code", "quantity")
        return ("customer_code", "amount")

    @staticmethod
    def _required_fields(register_type: str) -> tuple[str, ...]:
        if register_type == "MILK_REGISTER":
            return ("customer_code", "quantity_litres", "date")
        if register_type == "CATTLE_FEED_PURCHASE":
            return ("customer_code", "quantity")
        return ("customer_code", "amount")

    @staticmethod
    def _customer_match_confidence(code, name, matched, customers) -> float:
        if matched is None:
            return 0.0
        if code and str(code).casefold() == matched.code.casefold():
            return 1.0
        if name and str(name).strip().casefold() == matched.name.casefold():
            return 0.9
        return 0.55

    @staticmethod
    def _validated_field_confidence(
        field: dict, field_name: str, declared_confidence=None
    ) -> float:
        nested_confidence = field.get("confidence")
        if nested_confidence is not None and declared_confidence is not None:
            model = min(
                ExtractionService._confidence(nested_confidence),
                ExtractionService._confidence(declared_confidence),
            )
        elif nested_confidence is not None or declared_confidence is not None:
            model = ExtractionService._confidence(
                nested_confidence if nested_confidence is not None else declared_confidence
            )
        else:
            model = 0.55
        value = field.get("value")
        if value is None or value == "":
            return 0.0
        score = model
        if field_name in {"quantity_litres", "quantity", "fat_percent", "snf_percent", "amount"}:
            try:
                number = Decimal(str(value).translate(str.maketrans(
                    "०१२३४५६७८९", "0123456789"
                )))
            except InvalidOperation:
                return 0.0
            if number <= 0:
                return 0.0
            score = min(score, 0.95)
        if field.get("uncertainty_reason"):
            score = min(score, 0.49)
        return score

    @staticmethod
    def _description_from_fields(fields: dict) -> str:
        product = ExtractionService._field_value(fields, "product_name")
        code = ExtractionService._field_value(fields, "product_code")
        unit = ExtractionService._field_value(fields, "unit")
        amount = ExtractionService._field_value(fields, "amount")
        notes = ExtractionService._field_value(fields, "notes")
        parts = [
            f"Product: {product}" if product else "",
            f"Product code: {code}" if code else "",
            f"Unit: {unit}" if unit else "",
            f"Written amount: {amount}" if amount else "",
            str(notes) if notes else "",
        ]
        return " | ".join(part for part in parts if part)[:255]

    @staticmethod
    def _rows_from_cells(cells: list[dict]) -> list[dict]:
        grouped: dict[tuple[int, int], dict[str, dict]] = defaultdict(dict)
        for cell in cells:
            if not cell["is_empty"] and cell["text"].strip():
                grouped[(cell["table_index"], cell["row_index"])][cell["field"]] = cell

        rows: list[dict] = []
        fields = ("customer_number", "litres", "fat_percentage", "snf")
        for key in sorted(grouped):
            values = grouped[key]
            code = values.get("customer_number", {}).get("text", "").strip()
            required = ("customer_number", "litres", "fat_percentage", "snf")
            if any(field not in values for field in required):
                logger.info("Skipping incomplete OCR row: table=%s row=%s", *key)
                continue
            # A row without a stable identifier cannot safely be matched to a customer.
            if not code:
                logger.info("Skipping OCR row without a customer number: table=%s row=%s", *key)
                continue
            numbers: dict[str, float] = {}
            for field, cell in (
                ("quantity", values.get("litres")),
                ("fat", values.get("fat_percentage")),
                ("snf", values.get("snf")),
            ):
                match = re.search(r"\d+(?:[.,]\d+)?", cell["text"])
                if match is None:
                    logger.info(
                        "Skipping OCR row with invalid numeric value: table=%s row=%s field=%s",
                        *key, field,
                    )
                    break
                numbers[field] = float(match.group(0).replace(",", "."))
            if len(numbers) != 3:
                continue
            confidences = [
                values[field]["confidence"]
                for field in fields
                if field in values and values[field]["confidence"] is not None
            ]
            rows.append(
                {
                    "code": code,
                    "name": "",
                    **numbers,
                    "confidence": sum(confidences) / len(confidences) if confidences else 0.5,
                }
            )
        return rows

    @staticmethod
    def _match(name, customers, code=""):
        if code:
            for customer in customers:
                if code.casefold() == customer.code.casefold():
                    return customer
        if not name:
            return None
        normalized = name.casefold()
        for customer in customers:
            if normalized == customer.name.casefold() or normalized == customer.code.casefold():
                return customer
        for customer in customers:
            if normalized in customer.name.casefold() or customer.name.casefold() in normalized:
                return customer
        return None
