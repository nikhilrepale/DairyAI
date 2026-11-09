"""Focused offline tests for the vision-LLM scanning path (no external API calls)."""
import io
import json
from unittest.mock import Mock

from PIL import Image
import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _tiny_image():
    b = io.BytesIO()
    Image.new("RGB", (120, 80), "white").save(b, format="PNG")
    return b.getvalue()


def test_llm_scanning_skips_opencv_and_preserves_photo(monkeypatch):
    from app.database import Base
    from app.models import Dairy
    from app.services.extraction import ExtractionService

    monkeypatch.setenv("SCAN_IMAGE_STORAGE", "database")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    img = _tiny_image()

    class NoOpenCV:
        def process(self, image_bytes):
            raise AssertionError("Classical OCR image processing must be skipped")

    class FakeVision:
        providers = [type("Provider", (), {"name": "gemini"})()]
        def recognize(self, image_bytes, cells, register_type):
            assert image_bytes == img
            assert cells == []
            assert register_type == "RATE_CHART"
            return {
                "text": "FAT SNF chart", "provider": "gemini", "words": [], "cells": [],
                "metadata": {"model": "fake-vision"},
                "structured_extraction": {"register_type": "RATE_CHART",
                                          "raw_text": "FAT SNF chart", "matrix_cells": [],
                                          "warnings": [], "extraction_errors": []},
            }

    with Session(engine) as session:
        dairy = Dairy(name="Test dairy", dairy_code="LLM-TEST")
        session.add(dairy)
        session.flush()
        batch, provider, layout = ExtractionService(
            session, dairy.id, preprocessing_service=NoOpenCV(), ocr_service=FakeVision()
        ).scan(img, "chart.png", "rate_chart", "image/png")
        assert provider == "gemini"
        assert layout["extraction_mode"] == "vision_llm_full_image"
        assert layout["cells"] == []
        assert batch.original_image == img
        assert layout["structured_extraction"]["register_type"] == "RATE_CHART"

    engine.dispose()


def test_ollama_document_classification(monkeypatch):
    from app.services.document_classifier import detect_kind
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "mock-vision-model")
    response = Mock()
    response.json.return_value = {"message": {"content": json.dumps({
        "kind": "CATTLE_FEED_PURCHASE", "confidence": 0.95, "reason": "product rows"
    })}}
    response.raise_for_status.return_value = None
    calls = []
    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return response
    monkeypatch.setattr("app.services.document_classifier.requests.post", fake_post)
    result = detect_kind(_tiny_image())
    assert result["kind"] == "feed"
    assert calls[0][0].endswith("/api/chat")
    assert calls[0][1]["json"]["messages"][0]["images"]


def test_gemini_classification_falls_back_to_ollama(monkeypatch):
    from app.services.document_classifier import detect_kind

    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("AI_FALLBACK_PROVIDERS", "ollama")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "mock-gemini")
    monkeypatch.setenv("OLLAMA_MODEL", "mock-vision-model")
    response = Mock()
    response.json.return_value = {"message": {"content": json.dumps({
        "kind": "MILK_REGISTER", "confidence": 0.96, "reason": "milk rows"
    })}}
    response.raise_for_status.return_value = None
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if "generativelanguage.googleapis.com" in url:
            raise requests.Timeout("Gemini timed out")
        return response

    monkeypatch.setattr("app.services.document_classifier.requests.post", fake_post)

    result = detect_kind(_tiny_image())

    assert result["kind"] == "milk"
    assert calls[0].startswith("https://generativelanguage.googleapis.com/")
    assert calls[1] == "http://localhost:11434/api/chat"


def test_gemini_classification_reports_provider_http_error(monkeypatch):
    from app.services.document_classifier import DocumentClassificationError, detect_kind

    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("AI_FALLBACK_PROVIDERS", "")
    monkeypatch.setenv("GEMINI_API_KEY", "do-not-expose-this")
    monkeypatch.setenv("GEMINI_MODEL", "mock-vision-model")
    response = Mock()
    response.status_code = 400
    response.json.return_value = {"error": {"message": "Unsupported response MIME type"}}
    response.raise_for_status.side_effect = requests.HTTPError("bad request")
    monkeypatch.setattr(
        "app.services.document_classifier.requests.post", lambda *args, **kwargs: response
    )

    with pytest.raises(DocumentClassificationError) as error:
        detect_kind(_tiny_image())

    assert str(error.value) == (
        "Automatic document classification failed: gemini: "
        "Gemini classification request failed (HTTP 400): Unsupported response MIME type. "
        "Select a type manually and retry."
    )
    assert "do-not-expose-this" not in str(error.value)


def test_gemini_classification_reports_network_error_without_leaking_key(monkeypatch):
    from app.services.document_classifier import DocumentClassificationError, detect_kind

    secret = "do-not-expose-this"
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("AI_FALLBACK_PROVIDERS", "")
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    monkeypatch.setenv("GEMINI_MODEL", "mock-vision-model")
    monkeypatch.setattr(
        "app.services.document_classifier.requests.post",
        Mock(side_effect=requests.ConnectionError(f"Could not connect; token={secret}")),
    )

    with pytest.raises(DocumentClassificationError) as error:
        detect_kind(_tiny_image())

    assert "ConnectionError" in str(error.value)
    assert "Could not connect" in str(error.value)
    assert secret not in str(error.value)


def test_gemini_explicit_setting_disables_classical_fallback(monkeypatch):
    from app.services.ocr import OCRProviderFactory
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("AI_FALLBACK_PROVIDERS", "")
    monkeypatch.setenv("OCR_FALLBACK_PROVIDERS", "tesseract")
    providers = OCRProviderFactory.configured_chain()
    assert [p.name for p in providers] == ["gemini"]


def test_gemini_explicit_setting_uses_ollama_ai_fallback(monkeypatch):
    from app.services.ocr import OCRProviderFactory

    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("AI_FALLBACK_PROVIDERS", "ollama")

    providers = OCRProviderFactory.configured_chain()

    assert [provider.name for provider in providers] == ["gemini", "ollama"]


def test_ollama_uses_longer_configurable_inference_timeout(monkeypatch):
    from app.services.ocr import OllamaVisionOCRProvider

    monkeypatch.setenv("OLLAMA_EXTRACTION_TIMEOUT", "300")
    provider = OllamaVisionOCRProvider()

    assert provider.timeout == 300
