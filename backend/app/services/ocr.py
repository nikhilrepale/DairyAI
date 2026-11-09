from __future__ import annotations

import base64
import importlib.metadata
import io
import json
import logging
import os
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from .image_preprocessing import CellCrop

logger = logging.getLogger(__name__)


class OCRProviderError(RuntimeError):
    pass


class OCRProviderUnavailable(OCRProviderError):
    pass


@dataclass(frozen=True)
class OCRWord:
    text: str
    confidence: float | None
    bounding_box: dict[str, int] | None


@dataclass(frozen=True)
class OCRDocument:
    text: str
    words: list[OCRWord]
    provider: str
    metadata: dict[str, Any] = field(default_factory=dict)


class OCRProvider(ABC):
    """Strategy interface shared by local, cloud, and extension OCR providers."""

    name: str

    @abstractmethod
    def recognize(self, image_bytes: bytes, field_hint: str | None = None) -> OCRDocument:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> dict[str, Any]:
        raise NotImplementedError


class TesseractOCRProvider(OCRProvider):
    name = "tesseract"

    def _module(self):
        try:
            import pytesseract
        except ImportError as exc:
            raise OCRProviderUnavailable(
                "Tesseract Python wrapper is missing. Install the backend requirements."
            ) from exc
        configured_path = os.getenv("TESSERACT_CMD", "").strip()
        if configured_path:
            executable = Path(configured_path).expanduser()
            if not executable.is_file():
                raise OCRProviderUnavailable(
                    f"TESSERACT_CMD points to a file that does not exist: {executable}"
                )
            pytesseract.pytesseract.tesseract_cmd = str(executable)
        else:
            candidates = [
                shutil.which("tesseract"),
                *(
                    [
                        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                                     "Tesseract-OCR", "tesseract.exe"),
                        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                                     "Tesseract-OCR", "tesseract.exe"),
                    ]
                    if os.name == "nt"
                    else []
                ),
            ]
            executable = next(
                (Path(candidate) for candidate in candidates if candidate and Path(candidate).is_file()),
                None,
            )
            if executable:
                pytesseract.pytesseract.tesseract_cmd = str(executable)
        return pytesseract

    def health(self) -> dict[str, Any]:
        try:
            pytesseract = self._module()
            version = str(pytesseract.get_tesseract_version())
            return {
                "provider": self.name,
                "configured": True,
                "available": True,
                "version": version,
                "executable": pytesseract.pytesseract.tesseract_cmd,
            }
        except OCRProviderUnavailable as exc:
            return {
                "provider": self.name,
                "configured": True,
                "available": False,
                "message": str(exc),
                "install": "Install Tesseract OCR, then set TESSERACT_CMD to tesseract.exe if it is not on PATH.",
            }
        except Exception as exc:
            return {
                "provider": self.name,
                "configured": True,
                "available": False,
                "message": (
                    "Tesseract Python wrapper is installed, but the Tesseract OCR executable "
                    f"could not be started: {exc}"
                ),
                "install": (
                    "Install Tesseract OCR and add its folder to PATH, or set TESSERACT_CMD "
                    "to the full path of tesseract.exe."
                ),
            }

    def recognize(self, image_bytes: bytes, field_hint: str | None = None) -> OCRDocument:
        pytesseract = self._module()
        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            config = "--psm 7"
            if field_hint in {"litres", "fat_percentage", "snf"}:
                config += " -c tessedit_char_whitelist=0123456789.,"
            elif field_hint == "customer_number":
                config += " -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
            data = pytesseract.image_to_data(
                image, config=config, output_type=pytesseract.Output.DICT
            )
        except Exception as exc:
            logger.exception("Tesseract OCR failed")
            raise OCRProviderError(f"Tesseract OCR failed: {exc}") from exc

        words: list[OCRWord] = []
        for index, value in enumerate(data.get("text", [])):
            text = str(value).strip()
            if not text:
                continue
            try:
                confidence = float(data["conf"][index])
                confidence = confidence / 100.0 if confidence >= 0 else None
            except (KeyError, TypeError, ValueError):
                confidence = None
            words.append(
                OCRWord(
                    text=text,
                    confidence=confidence,
                    bounding_box={
                        "x": int(data["left"][index]),
                        "y": int(data["top"][index]),
                        "width": int(data["width"][index]),
                        "height": int(data["height"][index]),
                    },
                )
            )
        return OCRDocument(
            text=" ".join(word.text for word in words),
            words=words,
            provider=self.name,
            metadata={"engine": "tesseract", "numeric_hint": field_hint},
        )


class GoogleCloudVisionOCRProvider(OCRProvider):
    name = "google_vision"

    def _client(self):
        try:
            from google.cloud import vision
        except ImportError as exc:
            raise OCRProviderUnavailable(
                "Google Cloud Vision provider requires the optional google-cloud-vision package."
            ) from exc
        try:
            return vision.ImageAnnotatorClient(), vision
        except Exception as exc:
            raise OCRProviderUnavailable(f"Google Cloud Vision credentials are unavailable: {exc}") from exc

    def health(self) -> dict[str, Any]:
        try:
            client, _ = self._client()
            transport = getattr(client, "transport", None)
            if transport is not None and hasattr(transport, "close"):
                transport.close()
            return {
                "provider": self.name,
                "configured": True,
                "available": True,
                "authentication": "Application Default Credentials",
            }
        except Exception as exc:
            return {
                "provider": self.name,
                "configured": bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
                or bool(os.getenv("GOOGLE_CLOUD_PROJECT")),
                "available": False,
                "message": str(exc),
                "install": "Install google-cloud-vision and configure Application Default Credentials.",
            }

    def recognize(self, image_bytes: bytes, field_hint: str | None = None) -> OCRDocument:
        del field_hint
        client, vision = self._client()
        try:
            response = client.document_text_detection(image=vision.Image(content=image_bytes))
            if response.error.message:
                raise OCRProviderError(f"Google Cloud Vision OCR failed: {response.error.message}")
        except OCRProviderError:
            raise
        except Exception as exc:
            logger.exception("Google Cloud Vision request failed")
            raise OCRProviderError(f"Google Cloud Vision request failed: {exc}") from exc

        words: list[OCRWord] = []
        full_text = response.full_text_annotation
        for page in full_text.pages:
            for block in page.blocks:
                for paragraph in block.paragraphs:
                    for word in paragraph.words:
                        text = "".join(symbol.text for symbol in word.symbols)
                        vertices = word.bounding_box.vertices
                        xs = [vertex.x for vertex in vertices]
                        ys = [vertex.y for vertex in vertices]
                        words.append(
                            OCRWord(
                                text=text,
                                confidence=float(word.confidence),
                                bounding_box={
                                    "x": min(xs),
                                    "y": min(ys),
                                    "width": max(xs) - min(xs),
                                    "height": max(ys) - min(ys),
                                },
                            )
                        )
        return OCRDocument(
            text=full_text.text or "",
            words=words,
            provider=self.name,
            metadata={"engine": "document_text_detection"},
        )


class OpenAIVisionOCRProvider(OCRProvider):
    name = "openai_vision"

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("OPENAI_VISION_MODEL", "").strip()

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "configured": bool(self.api_key and self.model),
            "available": bool(self.api_key and self.model),
            "model": self.model,
            "message": (
                None if self.api_key and self.model
                else "Set OPENAI_API_KEY and OPENAI_VISION_MODEL to enable this optional provider."
            ),
        }

    def recognize(self, image_bytes: bytes, field_hint: str | None = None) -> OCRDocument:
        if not self.api_key:
            raise OCRProviderUnavailable("OpenAI Vision is not configured; set OPENAI_API_KEY.")
        if not self.model:
            raise OCRProviderUnavailable("Set OPENAI_VISION_MODEL to select an OpenAI vision model.")
        prompt = (
            "Transcribe this dairy register faithfully. Prioritize handwritten numeric characters "
            "and do not infer missing values. Return JSON with text (full transcription) and cells "
            "array. Each cell has integer table_index, row_index and string fields customer_number, "
            "litres, fat_percentage, snf. Indices are zero-based in left-to-right table order and "
            "top-to-bottom grid-row order, including blank rows; use empty strings for empty or "
            "unreadable fields. No morning/evening assumptions. Field hint: "
            + (field_hint or "whole register")
        )
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(image_bytes).decode("ascii")
                            },
                        },
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=90,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            result = json.loads(content)
        except Exception as exc:
            logger.exception("OpenAI Vision OCR request failed")
            raise OCRProviderError(f"OpenAI Vision OCR request failed: {exc}") from exc
        text = str(result.get("text", "")).strip()
        return OCRDocument(
            text=text,
            words=[],
            provider=self.name,
            metadata={
                "model": self.model,
                "confidence_available": False,
                "bounding_boxes_available": False,
                "cell_text": self._cell_text_map(result.get("cells", [])),
            },
        )

    @staticmethod
    def _cell_text_map(cells: Any) -> dict[str, str]:
        if not isinstance(cells, list):
            return {}
        output = {}
        for item in cells:
            if not isinstance(item, dict):
                continue
            try:
                table_index, row_index = int(item["table_index"]), int(item["row_index"])
            except (KeyError, TypeError, ValueError):
                continue
            for field_name in ("customer_number", "litres", "fat_percentage", "snf"):
                value = item.get(field_name, "")
                if isinstance(value, (str, int, float)):
                    output[f"{table_index}:{row_index}:{field_name}"] = str(value).strip()
        return output


class IndicDocParserOCRProvider(OCRProvider):
    """Load IndicDocParser through the optional dairyai.ocr.providers extension point."""

    name = "indicdocparser"

    @staticmethod
    def _plugin():
        try:
            entries = importlib.metadata.entry_points()
            entry = next(
                iter(entries.select(group="dairyai.ocr.providers", name="indicdocparser")),
                None,
            )
        except (AttributeError, TypeError):
            entries = importlib.metadata.entry_points()
            entry = next(
                (item for item in entries.get("dairyai.ocr.providers", [])
                 if item.name == "indicdocparser"),
                None,
            )
        if entry is None:
            raise OCRProviderUnavailable(
                "No supported IndicDocParser local inference plugin is installed. "
                "Install a plugin exposing the dairyai.ocr.providers/indicdocparser entry point."
            )
        factory = entry.load()
        plugin = factory() if callable(factory) else factory
        if not callable(getattr(plugin, "recognize", None)):
            raise OCRProviderUnavailable(
                "IndicDocParser plugin must implement recognize(image_bytes, field_hint=None)."
            )
        return plugin

    def health(self) -> dict[str, Any]:
        try:
            self._plugin()
            return {"provider": self.name, "configured": True, "available": True, "mode": "local"}
        except Exception as exc:
            return {
                "provider": self.name,
                "configured": False,
                "available": False,
                "message": str(exc),
            }

    def recognize(self, image_bytes: bytes, field_hint: str | None = None) -> OCRDocument:
        plugin = self._plugin()
        try:
            result = plugin.recognize(image_bytes, field_hint=field_hint)
            if isinstance(result, OCRDocument):
                return OCRDocument(
                    result.text, result.words, self.name,
                    {**result.metadata, "mode": "local"},
                )
            return self._normalize_plugin_result(result)
        except OCRProviderError:
            raise
        except Exception as exc:
            logger.exception("IndicDocParser local inference failed")
            raise OCRProviderError(f"IndicDocParser inference failed: {exc}") from exc

    @classmethod
    def _normalize_plugin_result(cls, result: Any) -> OCRDocument:
        if not isinstance(result, dict) or not isinstance(result.get("text", ""), str):
            raise OCRProviderError("IndicDocParser plugin must return a dict containing text.")
        words = []
        for item in result.get("words", []):
            box = item.get("bounding_box")
            words.append(
                OCRWord(
                    text=str(item.get("text", "")),
                    confidence=cls._optional_float(item.get("confidence")),
                    bounding_box=box if isinstance(box, dict) else None,
                )
            )
        return OCRDocument(
            text=result.get("text", ""),
            words=words,
            provider=cls.name,
            metadata={"mode": "local", **result.get("metadata", {})},
        )

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None


class GeminiVisionOCRProvider(OCRProvider):
    """Compatibility adapter for the application's existing Gemini register extraction."""

    name = "gemini"

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model = os.getenv("GEMINI_MODEL", "").strip()
        try:
            self.timeout = max(1, int(os.getenv("AI_EXTRACTION_TIMEOUT", "90")))
        except ValueError as exc:
            raise OCRProviderUnavailable("AI_EXTRACTION_TIMEOUT must be a positive integer.") from exc

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "configured": bool(self.api_key and self.model),
            "available": bool(self.api_key and self.model),
            "model": self.model,
            "message": (
                None if self.api_key and self.model
                else "Set GEMINI_API_KEY and GEMINI_MODEL to enable Gemini."
            ),
        }

    def connection_health(self) -> dict[str, Any]:
        if not self.api_key or not self.model:
            return {
                "provider": self.name, "configured": False, "connected": False,
                "model": self.model,
                "message": "Set GEMINI_API_KEY and GEMINI_MODEL to enable Gemini.",
            }
        try:
            timeout = max(1, int(os.getenv("AI_EXTRACTION_TIMEOUT", "90")))
            response = requests.get(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}",
                headers={"x-goog-api-key": self.api_key},
                timeout=min(timeout, 10),
            )
            if response.status_code in (401, 403):
                message = "Gemini rejected the API key or permissions."
            elif response.status_code == 404:
                message = "Configured Gemini model was not found."
            elif not response.ok:
                message = f"Gemini returned HTTP {response.status_code}."
            else:
                message = None
            return {
                "provider": self.name, "configured": True, "connected": response.ok,
                "model": self.model, "message": message,
            }
        except requests.Timeout:
            return {
                "provider": self.name, "configured": True, "connected": False,
                "model": self.model, "message": "Gemini health check timed out.",
            }
        except requests.RequestException as exc:
            return {
                "provider": self.name, "configured": True, "connected": False,
                "model": self.model, "message": f"Gemini network check failed: {exc}",
            }

    def recognize(self, image_bytes: bytes, field_hint: str | None = None) -> OCRDocument:
        return self.recognize_register(image_bytes, "MILK_REGISTER", field_hint)

    def recognize_register(
        self,
        image_bytes: bytes,
        register_type: str,
        field_hint: str | None = None,
    ) -> OCRDocument:
        if not self.api_key:
            raise OCRProviderUnavailable("Gemini Vision is not configured; set GEMINI_API_KEY.")
        if not self.model:
            raise OCRProviderUnavailable("Set GEMINI_MODEL to select a Gemini vision model.")
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                mime = image.get_format_mimetype() or "image/png"
            prompt = self._prompt(register_type, field_hint)
            payload = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {
                            "mime_type": mime,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }},
                    ]
                }],
                "generationConfig": {"responseMimeType": "application/json"},
            }
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:"
                "generateContent"
            )
            response = self._request_with_retry(url, payload)
            content = self._response_content(response)
            try:
                result = json.loads(content)
            except json.JSONDecodeError as exc:
                raise OCRProviderError("Gemini returned malformed JSON for the register.") from exc
            structured = self._validate_structured_result(result, register_type)
            text = structured["raw_text"]
            return OCRDocument(
                text=text,
                words=[],
                provider=self.name,
                metadata={
                    "model": self.model,
                    "confidence_available": False,
                    "bounding_boxes_available": False,
                    "structured_extraction": structured,
                    "cell_text": self._cell_text_map(structured),
                },
            )
        except OCRProviderError:
            raise
        except Exception as exc:
            logger.exception("Gemini Vision OCR request failed")
            message = self._gemini_error_message(exc)
            raise OCRProviderError(message) from exc

    @staticmethod
    def _prompt(register_type: str, field_hint: str | None) -> str:
        instructions = {
            "MILK_REGISTER": (
                "Extract each milk collection row. Fields: customer_code, customer_name, date "
                "(ISO YYYY-MM-DD only when unambiguous), session (morning/evening only if explicit), "
                "milk_type (cow/buffalo only if identifiable), quantity_litres, fat_percent, "
                "snf_percent. Keep original script/text in raw_text. Normalize Marathi digits "
                "to numeric JSON values but never discard raw text."
            ),
            "CATTLE_FEED_PURCHASE": (
                "Extract every cattle-feed purchase row. Fields: customer_code, customer_name, "
                "product_code, product_name, quantity, unit, date (ISO YYYY-MM-DD only if explicit), "
                "amount (only if explicitly written). Do not calculate amounts or infer products."
            ),
            "RATE_CHART": (
                "Transcribe the milk rate chart. Extract milk_type, chart_type, currency_unit, "
                "effective_date only if visible, printed_instructions, FAT row labels, SNF column "
                "labels, and every rate cell with its exact FAT and SNF coordinates. Return null "
                "for unreadable labels or rates; do not interpolate or guess."
            ),
            "ADVANCE_REGISTER": (
                "Extract advance rows with customer_code, customer_name, amount, date (ISO only "
                "if explicit), reference, and notes. Do not infer missing values."
            ),
        }
        selected = instructions.get(register_type)
        if selected is None:
            raise OCRProviderError(f"Unsupported register type '{register_type}'.")
        if register_type == "RATE_CHART":
            response_shape = (
                "Return a JSON object with register_type, raw_text, milk_type, chart_type, "
                "currency_unit, effective_date, printed_instructions, fat_values, snf_values, "
                "matrix_cells, warnings, and extraction_errors. Each matrix_cells item must have "
                "fat_value, snf_value, and rate."
            )
        else:
            response_shape = (
                "Return a JSON object with register_type, raw_text, extracted_rows, warnings, "
                "and extraction_errors. Every extracted_rows item contains the field names listed "
                "for this register plus field_confidence, row_confidence, and warnings."
            )
        return (
            "Faithfully transcribe this dairy document. Preserve Marathi and other source text. "
            "Never invent, complete, or infer absent/unreadable values. Return only JSON. "
            + response_shape
            + " For each extracted scalar field return an object with value "
            "(normalized value or null), raw_text (verbatim text or null), confidence (optional "
            "model estimate from 0 to 1), bounding_box (optional x/y/width/height in image pixels), "
            "and uncertainty_reason (string or null). Model confidence is untrusted and will be "
            "validated independently. "
            + selected
            + f" Field hint: {field_hint or 'none'}."
        )

    @staticmethod
    def _field_schema() -> dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "value": {"type": ["STRING", "NUMBER", "INTEGER", "NULL"]},
                "raw_text": {"type": ["STRING", "NULL"]},
                "confidence": {"type": ["NUMBER", "NULL"]},
                "bounding_box": {
                    "type": ["OBJECT", "NULL"],
                    "properties": {
                        "x": {"type": "INTEGER"}, "y": {"type": "INTEGER"},
                        "width": {"type": "INTEGER"}, "height": {"type": "INTEGER"},
                    },
                },
                "uncertainty_reason": {"type": ["STRING", "NULL"]},
            },
            "required": ["value", "raw_text", "confidence", "bounding_box", "uncertainty_reason"],
        }

    @classmethod
    def _response_schema(cls, register_type: str) -> dict[str, Any]:
        fields = {
            "MILK_REGISTER": (
                "customer_code", "customer_name", "date", "session", "milk_type",
                "quantity_litres", "fat_percent", "snf_percent",
            ),
            "CATTLE_FEED_PURCHASE": (
                "customer_code", "customer_name", "product_code", "product_name",
                "quantity", "unit", "date", "amount",
            ),
            "ADVANCE_REGISTER": (
                "customer_code", "customer_name", "amount", "date", "reference", "notes",
            ),
        }
        if register_type == "RATE_CHART":
            return {
                "type": "OBJECT",
                "properties": {
                    "register_type": {"type": "STRING"},
                    "raw_text": {"type": "STRING"},
                    "milk_type": cls._field_schema(),
                    "chart_type": cls._field_schema(),
                    "currency_unit": cls._field_schema(),
                    "effective_date": cls._field_schema(),
                    "printed_instructions": cls._field_schema(),
                    "fat_values": {"type": "ARRAY", "items": cls._field_schema()},
                    "snf_values": {"type": "ARRAY", "items": cls._field_schema()},
                    "matrix_cells": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "fat_value": cls._field_schema(),
                                "snf_value": cls._field_schema(),
                                "rate": cls._field_schema(),
                            },
                            "required": ["fat_value", "snf_value", "rate"],
                        },
                    },
                    "warnings": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "extraction_errors": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["register_type", "raw_text", "milk_type", "chart_type",
                             "currency_unit", "effective_date", "printed_instructions",
                             "fat_values", "snf_values", "matrix_cells", "warnings",
                             "extraction_errors"],
            }
        selected = fields.get(register_type)
        if selected is None:
            raise OCRProviderError(f"Unsupported register type '{register_type}'.")
        row_properties = {name: cls._field_schema() for name in selected}
        return {
            "type": "OBJECT",
            "properties": {
                "register_type": {"type": "STRING"},
                "raw_text": {"type": "STRING"},
                "extracted_rows": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            **row_properties,
                            "field_confidence": {
                                "type": "OBJECT",
                                "properties": {
                                    name: {"type": ["NUMBER", "NULL"]} for name in selected
                                },
                            },
                            "row_confidence": {"type": ["NUMBER", "NULL"]},
                            "warnings": {"type": "ARRAY", "items": {"type": "STRING"}},
                        },
                        "required": [*selected, "field_confidence", "row_confidence", "warnings"],
                    },
                },
                "warnings": {"type": "ARRAY", "items": {"type": "STRING"}},
                "extraction_errors": {"type": "ARRAY", "items": {"type": "STRING"}},
            },
            "required": ["register_type", "raw_text", "extracted_rows", "warnings", "extraction_errors"],
        }

    @staticmethod
    def _request_with_retry(url: str, payload: dict) -> requests.Response:
        try:
            max_attempts = max(1, int(os.getenv("AI_EXTRACTION_MAX_ATTEMPTS", "3")))
        except ValueError as exc:
            raise OCRProviderUnavailable(
                "AI_EXTRACTION_MAX_ATTEMPTS must be a positive integer."
            ) from exc
        try:
            timeout = max(1, int(os.getenv("AI_EXTRACTION_TIMEOUT", "90")))
        except ValueError as exc:
            raise OCRProviderUnavailable(
                "AI_EXTRACTION_TIMEOUT must be a positive integer."
            ) from exc
        for attempt in range(max_attempts):
            try:
                response = requests.post(
                    url,
                    headers={"x-goog-api-key": os.getenv("GEMINI_API_KEY", "")},
                    json=payload,
                    timeout=timeout,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 < max_attempts:
                        time.sleep(min(2 ** attempt, 8))
                        continue
                response.raise_for_status()
                return response
            except requests.Timeout as exc:
                if attempt + 1 >= max_attempts:
                    raise OCRProviderError("Gemini request timed out; retry the extraction.") from exc
                time.sleep(min(2 ** attempt, 8))
            except requests.ConnectionError as exc:
                if attempt + 1 >= max_attempts:
                    raise OCRProviderError(
                        f"Gemini network connection failed: {exc}"
                    ) from exc
                time.sleep(min(2 ** attempt, 8))
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                message = GeminiVisionOCRProvider._http_error_message(status, exc.response)
                raise OCRProviderError(message) from exc
        raise OCRProviderError("Gemini request failed after retry attempts.")

    @staticmethod
    def _response_content(response: requests.Response) -> str:
        try:
            candidates = response.json().get("candidates") or []
            if not candidates:
                raise OCRProviderError("Gemini returned an empty response.")
            parts = candidates[0].get("content", {}).get("parts", [])
            content = "".join(str(part.get("text", "")) for part in parts).strip()
            if not content:
                raise OCRProviderError("Gemini returned an empty response.")
            return content
        except (ValueError, TypeError, AttributeError, IndexError) as exc:
            raise OCRProviderError("Gemini returned an invalid response envelope.") from exc

    @classmethod
    def _validate_structured_result(cls, result: Any, register_type: str) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise OCRProviderError("Vision model response must be a JSON object.")
        if result.get("register_type") not in (None, register_type):
            raise OCRProviderError("Vision model response register type does not match the request.")
        raw_text = result.get("raw_text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise OCRProviderError("Vision model returned no transcription text.")
        normalized = {
            "register_type": register_type,
            "raw_text": raw_text,
            "warnings": cls._string_list(result.get("warnings")),
            "extraction_errors": cls._string_list(result.get("extraction_errors")),
        }
        if register_type == "RATE_CHART":
            normalized.update({
                key: cls._normalize_field(result.get(key))
                for key in ("milk_type", "chart_type", "currency_unit", "effective_date",
                            "printed_instructions")
            })
            normalized["fat_values"] = [
                cls._normalize_field(item) for item in result.get("fat_values", [])
            ]
            normalized["snf_values"] = [
                cls._normalize_field(item) for item in result.get("snf_values", [])
            ]
            normalized["matrix_cells"] = [
                {
                    key: cls._normalize_field(cell.get(key))
                    for key in ("fat_value", "snf_value", "rate")
                }
                for cell in result.get("matrix_cells", []) if isinstance(cell, dict)
            ]
            return normalized
        if not isinstance(result.get("extracted_rows"), list):
            raise OCRProviderError("Vision model response is missing extracted_rows.")
        normalized["extracted_rows"] = []
        for row in result["extracted_rows"]:
            if not isinstance(row, dict):
                continue
            item = {
                "field_confidence": row.get("field_confidence")
                if isinstance(row.get("field_confidence"), dict) else {},
                "row_confidence": cls._confidence(row.get("row_confidence")),
                "warnings": cls._string_list(row.get("warnings")),
            }
            for name, value in row.items():
                if name in {"field_confidence", "row_confidence", "warnings"}:
                    continue
                item[name] = cls._normalize_field(value)
            normalized["extracted_rows"].append(item)
        return normalized

    @classmethod
    def _normalize_field(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            value = {"value": value, "raw_text": None}
        raw = value.get("raw_text")
        raw_text = str(raw).strip() if raw is not None else None
        extracted = value.get("value")
        return {
            "value": cls._normalize_numeric(extracted),
            "raw_text": raw_text,
            "confidence": cls._confidence(value.get("confidence")),
            "bounding_box": cls._bounding_box(value.get("bounding_box")),
            "uncertainty_reason": (
                str(value["uncertainty_reason"]).strip()
                if value.get("uncertainty_reason") else None
            ),
        }

    @staticmethod
    def _normalize_numeric(value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            digits = text.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
            if digits == "":
                return None
            if digits.replace(".", "", 1).replace(",", "", 1).isdigit():
                return digits.replace(",", ".")
            return text or None
        return value

    @staticmethod
    def _confidence(value: Any) -> float | None:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return None
        return confidence if 0 <= confidence <= 1 else None

    @staticmethod
    def _bounding_box(value: Any) -> dict[str, int] | None:
        if not isinstance(value, dict):
            return None
        try:
            box = {key: int(value[key]) for key in ("x", "y", "width", "height")}
        except (KeyError, TypeError, ValueError):
            return None
        return box if box["width"] >= 0 and box["height"] >= 0 else None

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        return [str(item) for item in value if isinstance(item, (str, int, float))] \
            if isinstance(value, list) else []

    @staticmethod
    def _cell_text_map(structured: dict[str, Any]) -> dict[str, str]:
        output: dict[str, str] = {}
        for index, row in enumerate(structured.get("extracted_rows", [])):
            for field, cell in row.items():
                if isinstance(cell, dict) and cell.get("value") is not None:
                    output[f"0:{index}:{field}"] = str(cell["value"])
        return output

    @staticmethod
    def _http_error_message(status: int | None, response: requests.Response | None) -> str:
        detail = ""
        if response is not None:
            try:
                detail = str(response.json().get("error", {}).get("message", ""))
            except (ValueError, AttributeError):
                detail = response.text[:300]
        if status in (401, 403):
            return "Gemini rejected the API key or permissions. Verify GEMINI_API_KEY."
        if status == 404:
            return "Gemini model was not found or is unavailable. Verify GEMINI_MODEL."
        if status == 429:
            return "Gemini rate limit exceeded after retries. Wait and retry."
        if status is not None and status >= 500:
            return f"Gemini service returned HTTP {status} after retries."
        return f"Gemini request failed (HTTP {status}): {detail or 'invalid request'}"

    @classmethod
    def _gemini_error_message(cls, error: Exception) -> str:
        if isinstance(error, requests.Timeout):
            return "Gemini request timed out; retry the extraction."
        if isinstance(error, requests.ConnectionError):
            return f"Gemini network connection failed: {error}"
        if isinstance(error, requests.HTTPError):
            response = error.response
            return cls._http_error_message(response.status_code if response else None, response)
        return f"Gemini Vision OCR failed: {error}"

    @staticmethod
    def _cell_text_map(cells: Any) -> dict[str, str]:
        return OpenAIVisionOCRProvider._cell_text_map(cells)


class OllamaVisionOCRProvider(GeminiVisionOCRProvider):
    """Local Ollama vision-model adapter using the same structured extraction contract."""

    name = "ollama"

    def __init__(self):
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.model = os.getenv("OLLAMA_MODEL", "").strip()
        try:
            self.context_tokens = max(512, int(os.getenv("OLLAMA_NUM_CTX", "4096")))
            self.max_output_tokens = max(
                64, int(os.getenv("OLLAMA_MAX_OUTPUT_TOKENS", "2048"))
            )
            self.timeout = max(
                1,
                int(os.getenv(
                    "OLLAMA_EXTRACTION_TIMEOUT",
                    os.getenv("AI_EXTRACTION_TIMEOUT", "300"),
                )),
            )
        except ValueError as exc:
            raise OCRProviderUnavailable(
                "OLLAMA_NUM_CTX, OLLAMA_MAX_OUTPUT_TOKENS, and "
                "OLLAMA_EXTRACTION_TIMEOUT must be positive integers."
            ) from exc

    def health(self) -> dict[str, Any]:
        if not self.model:
            return {
                "provider": self.name, "configured": False, "available": False,
                "model": None, "message": "Set OLLAMA_MODEL to enable the local vision provider.",
            }
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=min(self.timeout, 10))
            response.raise_for_status()
            models = response.json().get("models", [])
            available_models = [
                model.get("name") for model in models if isinstance(model, dict)
            ]
            accepted_names = {self.model}
            if ":" not in self.model:
                accepted_names.add(f"{self.model}:latest")
            found = any(name in accepted_names for name in available_models)
            return {
                "provider": self.name, "configured": True, "available": found,
                "model": self.model,
                "message": None if found else f"Model '{self.model}' is not installed in Ollama.",
            }
        except requests.Timeout:
            return {
                "provider": self.name, "configured": True, "available": False,
                "model": self.model, "message": "Ollama health check timed out.",
            }
        except (requests.RequestException, ValueError, AttributeError) as exc:
            return {
                "provider": self.name, "configured": True, "available": False,
                "model": self.model, "message": f"Ollama is unavailable: {exc}",
            }

    def recognize(self, image_bytes: bytes, field_hint: str | None = None) -> OCRDocument:
        return self.recognize_register(image_bytes, "MILK_REGISTER", field_hint)

    def recognize_register(
        self, image_bytes: bytes, register_type: str, field_hint: str | None = None
    ) -> OCRDocument:
        if not self.model:
            raise OCRProviderUnavailable("Set OLLAMA_MODEL to enable the local vision provider.")
        prompt = self._ollama_prompt(register_type, field_hint)
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                image = image.convert("RGB")
                image.thumbnail((1200, 1200))
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=82)
                ollama_image = output.getvalue()
        except Exception as exc:
            raise OCRProviderError("Ollama could not prepare the uploaded image.") from exc
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": prompt,
                "images": [base64.b64encode(ollama_image).decode("ascii")],
            }],
            "format": "json",
            "stream": False,
            "options": {
                "temperature": 0,
                "num_ctx": self.context_tokens,
                "num_predict": self.max_output_tokens,
            },
        }
        try:
            response = requests.post(
                f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "")
        except requests.Timeout as exc:
            raise OCRProviderError("Ollama request timed out; retry the extraction.") from exc
        except requests.ConnectionError as exc:
            raise OCRProviderError(f"Ollama connection failed: {exc}") from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise OCRProviderError(
                f"Ollama returned HTTP {status}; verify the model and local service."
            ) from exc
        except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
            raise OCRProviderError(f"Ollama returned an invalid response: {exc}") from exc
        if not isinstance(content, str) or not content.strip():
            raise OCRProviderError("Ollama returned an empty response.")
        try:
            structured = self._validate_structured_result(json.loads(content), register_type)
        except json.JSONDecodeError as exc:
            raise OCRProviderError("Ollama returned malformed JSON for the register.") from exc
        return OCRDocument(
            text=structured["raw_text"],
            words=[],
            provider=self.name,
            metadata={
                "model": self.model,
                "confidence_available": False,
                "bounding_boxes_available": False,
                "structured_extraction": structured,
                "cell_text": self._cell_text_map(structured),
            },
        )

    @staticmethod
    def _ollama_prompt(register_type: str, field_hint: str | None) -> str:
        fields = {
            "MILK_REGISTER": (
                "customer_code, customer_name, date, session, milk_type, "
                "quantity_litres, fat_percent, snf_percent"
            ),
            "CATTLE_FEED_PURCHASE": (
                "customer_code, customer_name, product_code, product_name, "
                "quantity, unit, date, amount"
            ),
            "ADVANCE_REGISTER": (
                "customer_code, customer_name, amount, date, reference, notes"
            ),
        }
        if register_type == "RATE_CHART":
            shape = (
                '{"register_type":"RATE_CHART","raw_text":"...","milk_type":null,'
                '"chart_type":null,"currency_unit":null,"effective_date":null,'
                '"printed_instructions":null,"fat_values":[],"snf_values":[],'
                '"matrix_cells":[{"fat_value":null,"snf_value":null,"rate":null}],'
                '"warnings":[],"extraction_errors":[]}'
            )
            task = (
                "Transcribe every visible milk-rate chart cell exactly. Do not guess or interpolate."
            )
        else:
            selected_fields = fields.get(register_type)
            if selected_fields is None:
                raise OCRProviderError(f"Unsupported register type '{register_type}'.")
            row_shape = "{" + ", ".join(
                f'"{name}": null' for name in selected_fields.split(", ")
            ) + "}"
            shape = (
                f'{{"register_type":"{register_type}","raw_text":"...",'
                f'"extracted_rows":[{row_shape}],'
                '"warnings":[],"extraction_errors":[]}'
            )
            task = f"Read each row. Row fields: {selected_fields}."
        return (
            f"{task} Preserve source text including Marathi. Use null for unreadable values; "
            f"never invent values. Return compact JSON only matching this shape: {shape} "
            f"Field hint: {field_hint or 'none'}."
        )


class OCRProviderFactory:
    _providers = {
        "indicdocparser": IndicDocParserOCRProvider,
        "tesseract": TesseractOCRProvider,
        "google_vision": GoogleCloudVisionOCRProvider,
        "openai_vision": OpenAIVisionOCRProvider,
        "gemini": GeminiVisionOCRProvider,
        "ollama": OllamaVisionOCRProvider,
    }

    @classmethod
    def create(cls, name: str) -> OCRProvider:
        provider_type = cls._providers.get(name.strip().lower())
        if provider_type is None:
            supported = ", ".join(sorted(cls._providers))
            raise OCRProviderUnavailable(f"Unknown OCR_PROVIDER '{name}'. Supported providers: {supported}.")
        return provider_type()

    @classmethod
    def configured_chain(cls) -> list[OCRProvider]:
        explicit_ai_provider = os.getenv("AI_PROVIDER", "").strip().lower()
        if explicit_ai_provider:
            if explicit_ai_provider not in {"gemini", "ollama", "tesseract"}:
                raise OCRProviderUnavailable(
                    f"Unsupported AI_PROVIDER '{explicit_ai_provider}'. "
                    "Choose gemini, ollama, or tesseract."
                )
            selected = explicit_ai_provider
            fallback = [
                item.strip().lower()
                for item in os.getenv("AI_FALLBACK_PROVIDERS", "").split(",")
                if item.strip()
            ]
            invalid = [item for item in fallback if item not in {"gemini", "ollama"}]
            if invalid:
                raise OCRProviderUnavailable(
                    "AI_FALLBACK_PROVIDERS supports only gemini and ollama."
                )
        else:
            selected = os.getenv("OCR_PROVIDER", "").strip().lower()
            if not selected:
                selected = "gemini" if os.getenv("GEMINI_API_KEY") else "tesseract"
            fallback = [
                item.strip().lower()
                for item in os.getenv("AI_FALLBACK_PROVIDERS", "").split(",")
                if item.strip()
            ] + [
            item.strip().lower()
            for item in os.getenv("OCR_FALLBACK_PROVIDERS", "").split(",")
            if item.strip()
            ]
        names = list(dict.fromkeys([selected, *fallback]))
        return [cls.create(name) for name in names]

    @classmethod
    def diagnostics(cls) -> dict[str, Any]:
        explicit_ai_provider = os.getenv("AI_PROVIDER", "").strip().lower()
        if explicit_ai_provider:
            selected = explicit_ai_provider
            fallback = [
                item.strip().lower()
                for item in os.getenv("AI_FALLBACK_PROVIDERS", "").split(",")
                if item.strip()
            ]
        else:
            selected = os.getenv("OCR_PROVIDER", "").strip().lower() or (
                "gemini" if os.getenv("GEMINI_API_KEY") else "tesseract"
            )
            fallback = [
                item.strip().lower()
                for item in os.getenv("AI_FALLBACK_PROVIDERS", "").split(",")
                if item.strip()
            ] + [
                item.strip().lower()
                for item in os.getenv("OCR_FALLBACK_PROVIDERS", "").split(",")
                if item.strip()
            ]
        unknown = [
            name for name in [selected, *fallback] if name not in cls._providers
        ]
        return {
            "selected_provider": selected,
            "fallback_providers": fallback,
            "configuration_errors": [
                f"Unknown OCR provider '{name}'." for name in dict.fromkeys(unknown)
            ],
            "providers": [
                cls._provider_health(name) for name in cls._providers
            ],
        }

    @classmethod
    def _provider_health(cls, name: str) -> dict[str, Any]:
        try:
            return cls.create(name).health()
        except Exception as exc:
            logger.exception("OCR provider diagnostics failed for %s", name)
            return {
                "provider": name,
                "configured": False,
                "available": False,
                "message": f"Provider diagnostics failed: {exc}",
            }


class OCRService:
    def __init__(self, providers: list[OCRProvider] | None = None):
        self.providers = providers if providers is not None else OCRProviderFactory.configured_chain()

    def recognize(
        self,
        image_bytes: bytes,
        cells: list[CellCrop] | None = None,
        register_type: str = "MILK_REGISTER",
    ) -> dict[str, Any]:
        if not self.providers:
            raise OCRProviderUnavailable("No OCR providers are configured.")
        errors: list[str] = []
        for provider in self.providers:
            try:
                health = provider.health()
            except Exception as exc:
                logger.exception("OCR provider health check failed for %s", provider.name)
                errors.append(f"{provider.name}: health check failed: {exc}")
                continue
            if not health.get("available", False):
                message = health.get("message", "provider is unavailable")
                errors.append(f"{provider.name}: {message}")
                logger.warning("OCR provider %s unavailable: %s", provider.name, message)
                continue
            try:
                if isinstance(provider, GeminiVisionOCRProvider):
                    document = provider.recognize_register(image_bytes, register_type)
                else:
                    document = provider.recognize(image_bytes)
                cell_results = self._associate_cells(document, cells or [])
                if isinstance(provider, TesseractOCRProvider):
                    warnings = self._refine_numeric_cells(provider, cell_results)
                else:
                    warnings = []
                structured = document.metadata.get("structured_extraction")
                if isinstance(structured, dict):
                    warnings.extend(structured.get("warnings", []))
                    warnings.extend(structured.get("extraction_errors", []))
                return {
                    "text": document.text,
                    "words": [asdict(word) for word in document.words],
                    "provider": document.provider,
                    "metadata": {
                        **document.metadata,
                        "bounding_box_coordinate_space": "processed_image_pixels",
                        "warnings": warnings,
                    },
                    "cells": cell_results,
                    "structured_extraction": structured,
                }
            except OCRProviderError as exc:
                errors.append(f"{provider.name}: {exc}")
                logger.warning("OCR provider %s failed; considering configured fallback: %s", provider.name, exc)
            except Exception as exc:
                logger.exception("Unexpected OCR provider failure: %s", provider.name)
                errors.append(f"{provider.name}: unexpected provider error: {exc}")
        raise OCRProviderError("All configured OCR providers failed: " + " | ".join(errors))

    @staticmethod
    def _associate_cells(document: OCRDocument, cells: list[CellCrop]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for cell in cells:
            x, y, width, height = cell.processed_box
            matched = []
            for word in document.words:
                box = word.bounding_box
                if not box:
                    continue
                center_x = box["x"] + box["width"] / 2
                center_y = box["y"] + box["height"] / 2
                if x <= center_x <= x + width and y <= center_y <= y + height:
                    matched.append(word)
            provider_cell_text = document.metadata.get("cell_text", {}).get(
                f"{cell.table_index}:{cell.row_index}:{cell.field}", ""
            )
            results.append({
                "table_index": cell.table_index,
                "row_index": cell.row_index,
                "column_index": cell.column_index,
                "field": cell.field,
                "coordinates": cell.coordinates,
                "processed_box": {
                    "x": x, "y": y, "width": width, "height": height,
                },
                "text": " ".join(word.text for word in matched) or provider_cell_text,
                "confidence": (
                    sum(word.confidence for word in matched if word.confidence is not None)
                    / len([word for word in matched if word.confidence is not None])
                    if any(word.confidence is not None for word in matched)
                    else None
                ),
                "words": [asdict(word) for word in matched],
                "is_empty": cell.is_empty,
                "image_base64": base64.b64encode(cell.original_image).decode("ascii"),
                "_processed_image": cell.image,
            })
        return results

    @staticmethod
    def _refine_numeric_cells(
        provider: TesseractOCRProvider, cells: list[dict[str, Any]]
    ) -> list[str]:
        warnings = []
        for cell in cells:
            if cell["is_empty"] or cell["field"] not in {
                "customer_number", "litres", "fat_percentage", "snf"
            }:
                continue
            try:
                document = provider.recognize(
                    cell["_processed_image"], field_hint=cell["field"]
                )
            except OCRProviderError as exc:
                logger.exception("Tesseract numeric cell refinement failed for %s", cell["field"])
                warnings.append(
                    f"Numeric refinement failed for table {cell['table_index'] + 1}, "
                    f"row {cell['row_index'] + 1}, field {cell['field']}: {exc}"
                )
                continue
            if document.text:
                cell["text"] = document.text
                cell["words"] = document_as_global_words(document, cell["processed_box"])
                cell["confidence"] = (
                    sum(word.confidence for word in document.words if word.confidence is not None)
                    / len([word for word in document.words if word.confidence is not None])
                    if any(word.confidence is not None for word in document.words)
                    else None
                )
        return warnings


def document_as_global_words(document: OCRDocument, coordinates: dict[str, int]) -> list[dict[str, Any]]:
    words = []
    for word in document.words:
        box = word.bounding_box
        if box:
            box = {
                "x": box["x"] + coordinates["x"],
                "y": box["y"] + coordinates["y"],
                "width": box["width"],
                "height": box["height"],
            }
        words.append({"text": word.text, "confidence": word.confidence, "bounding_box": box})
    return words
