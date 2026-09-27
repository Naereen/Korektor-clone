"""Unit tests for provider configuration and OpenAI-compatible transports.

All HTTP calls are replaced with small fakes.  The tests cover authentication
lookup, provider overrides, JSON response validation, PDF image conversion,
and streaming progress without requiring API credentials or network access.
"""

import base64
import io
import json
import os
import sys
import traceback
import types
from types import SimpleNamespace

import pymupdf
import pytest
from PIL import Image

from correct_ai.llm_providers import (PROVIDERS, ProviderConfig, ask_llm, ask_llm_with_image,
                                      ask_llm_with_image_data, ask_llm_with_pdf,
                                      ask_llm_with_progress, get_provider, pdf_to_base64_images)


class FakeResponse:
    """Minimal requests response fake used by transport tests."""

    def __init__(self, payload, status_code=200, text=None):
        self.payload = payload
        self.status_code = status_code
        self.text = text or (json.dumps(payload) if not isinstance(payload, list) else "")

    def raise_for_status(self):
        """Raise an HTTP error for non-success status codes."""
        if self.status_code >= 400:
            raise RuntimeError(self.text)

    def json(self):
        """Return the configured JSON payload."""
        return self.payload

    def iter_lines(self):
        """Yield configured streaming lines as UTF-8 bytes."""
        yield from self.payload

    def __enter__(self):
        """Enter the response context manager."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the response context manager."""
        return False


def test_get_provider_supports_overrides():
    """Provider overrides should preserve authentication and TLS settings."""
    provider = get_provider("ILAAS_OSS", override_model="test-model", override_url="http://test")
    assert provider.model == "test-model"
    assert provider.url == "http://test"
    assert provider.api_key_env == PROVIDERS["ILAAS_OSS"].api_key_env


def test_get_provider_rejects_unknown_name():
    """Unknown provider names should produce a useful ValueError."""
    with pytest.raises(ValueError, match="Provider inconnu"):
        get_provider("UNKNOWN")


def test_gemini_provider_uses_flash_model_and_environment_key():
    """The Gemini provider should use the official API key environment variable."""
    provider = get_provider("GOOGLE_GEMINI")
    assert provider.model == "gemini-flash-latest"
    assert provider.api_key_env == "GEMINI_API_KEY"


def test_default_provider_can_be_switched_and_restored(monkeypatch):
    """Application provider selection should affect implicit lookups only."""
    from correct_ai import llm_providers

    original = llm_providers.ACTIVE_PROVIDER
    monkeypatch.setattr(llm_providers, "ACTIVE_PROVIDER", original)
    llm_providers.set_default_provider("GOOGLE_GEMINI")
    assert get_provider().name == "GOOGLE_GEMINI"
    assert get_provider("ILAAS_OSS").name == "ILAAS_OSS"


def test_api_key_uses_environment(monkeypatch):
    """ProviderConfig should read its configured environment variable."""
    monkeypatch.setenv("TEST_LLM_KEY", "secret")
    provider = ProviderConfig("test", "http://test", "model", "TEST_LLM_KEY")
    assert provider.api_key() == "secret"


def test_local_provider_uses_dummy_key():
    """Local providers should return a non-empty placeholder key."""
    provider = ProviderConfig("local", "http://test", "model")
    assert provider.api_key() == "sk-dummy"


def test_ask_llm_builds_qwen_thinking_payload(monkeypatch):
    """Text requests should add Qwen thinking options for Qwen models."""
    captured = {}

    def post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse({"choices": [{"message": {"content": "answer"}}]})

    monkeypatch.setattr("correct_ai.llm_providers.requests.post", post)
    provider = ProviderConfig("qwen", "http://test", "Qwen-test")
    assert ask_llm(provider, "prompt", thinking=False) == "answer"
    assert captured["json"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_ask_llm_rejects_missing_choices(monkeypatch):
    """Malformed provider responses should raise a clear ValueError."""
    monkeypatch.setattr(
        "correct_ai.llm_providers.requests.post",
        lambda *args, **kwargs: FakeResponse({"error": "bad response"}),
    )
    with pytest.raises(ValueError, match="unexpected format"):
        ask_llm(ProviderConfig("test", "http://test", "model"), "prompt")


def test_pdf_to_base64_images(tmp_path):
    """Every PDF page should be rendered as a bounded JPEG at 150 DPI."""
    pdf_path = tmp_path / "copy.pdf"
    doc = pymupdf.open()
    doc.new_page(width=1000, height=600)
    doc.new_page()
    doc.save(pdf_path)
    doc.close()

    images = pdf_to_base64_images(str(pdf_path))
    assert len(images) == 2
    with Image.open(io.BytesIO(base64.b64decode(images[0]))) as image:
        assert image.format == "JPEG"
        assert image.size == (1800, 1080)
    with Image.open(io.BytesIO(base64.b64decode(images[1]))) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= 1800


def test_pdf_to_base64_images_uses_optimized_jpeg_in_memory(monkeypatch, tmp_path):
    """PDF conversion should encode at quality 85 without writing an image file."""
    pdf_path = tmp_path / "copy.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.save(pdf_path)

    original_save = Image.Image.save
    saves = []

    def capture_save(image, output, **kwargs):
        saves.append((output, kwargs))
        return original_save(image, output, **kwargs)

    monkeypatch.setattr(Image.Image, "save", capture_save)
    pdf_to_base64_images(str(pdf_path))
    assert len(saves) == 1
    assert isinstance(saves[0][0], io.BytesIO)
    assert saves[0][1] == {"format": "JPEG", "quality": 85, "optimize": True}


def test_ask_llm_with_pdf_sends_images(monkeypatch, tmp_path):
    """Vision requests should number each PDF page beside its image."""
    pdf_path = tmp_path / "copy.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()
    captured = {}
    events = []

    def post(url, **kwargs):
        events.append("post")
        captured.update(kwargs)
        return FakeResponse({"choices": [{"message": {"content": "ocr"}}]})

    monkeypatch.setattr("correct_ai.llm_providers.requests.post", post)
    provider = ProviderConfig("vision", "http://test", "model")
    assert (
        ask_llm_with_pdf(provider, str(pdf_path), "read", inference_callback=events.append) == "ocr"
    )
    assert events == [True, "post", False]
    content = captured["json"]["messages"][1]["content"]
    assert content[0]["type"] == "text"
    assert [item["text"] for item in content if item["type"] == "text"][1:] == [
        "Page 1",
        "Page 2",
    ]
    image_items = [item for item in content if item["type"] == "image_url"]
    assert len(image_items) == 2
    assert all(item["image_url"]["detail"] == "high" for item in image_items)


def test_ask_llm_with_image_sends_focused_png(monkeypatch, tmp_path):
    """Focused refinement sends one high-detail image and balances callbacks."""
    image_path = tmp_path / "crop.png"
    image_path.write_bytes(b"png-data")
    captured = {}
    events = []

    def post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse({"choices": [{"message": {"content": '{"bbox_2d": [1, 2, 3, 4]}'}}]})

    monkeypatch.setattr("correct_ai.llm_providers.requests.post", post)
    provider = ProviderConfig("vision", "http://test", "model")

    result = ask_llm_with_image(
        provider, str(image_path), "refine", "system", inference_callback=events.append
    )

    assert result == '{"bbox_2d": [1, 2, 3, 4]}'
    assert events == [True, False]
    user_content = captured["json"]["messages"][1]["content"]
    assert user_content[0] == {"type": "text", "text": "refine"}
    assert user_content[1]["image_url"]["detail"] == "high"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_gemini_uses_official_sdk_and_redacts_sdk_errors(monkeypatch):
    """Gemini requests use google-genai and never expose SDK exception text."""
    from correct_ai.llm_providers import _ask_gemini

    secret = "test-gemini-secret"
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    google_module = types.ModuleType("google")
    google_module.__path__ = []
    genai_module = types.ModuleType("google.genai")
    sdk_types = types.ModuleType("google.genai.types")

    class FakePart:
        @staticmethod
        def from_bytes(data, mime_type):
            return {"data": data, "mime_type": mime_type}

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeModels:
        def generate_content(self, **kwargs):
            assert kwargs["model"] == "gemini-flash-latest"
            assert kwargs["contents"][0] == "prompt"
            assert kwargs["contents"][1] == {"data": b"image", "mime_type": "image/png"}
            raise RuntimeError(f"request failed with {secret}")

    class FakeClient:
        def __init__(self, api_key):
            assert api_key == secret
            self.models = FakeModels()

    genai_module.Client = FakeClient
    sdk_types.Part = FakePart
    sdk_types.GenerateContentConfig = FakeGenerateContentConfig
    google_module.genai = genai_module
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", sdk_types)

    with pytest.raises(RuntimeError, match="clé masquée") as raised:
        _ask_gemini(
            get_provider("GOOGLE_GEMINI"), "prompt", "system", [(b"image", "image/png")]
        )

    assert secret not in str(raised.value)
    assert secret not in "".join(traceback.format_exception(raised.value))


def test_streaming_llm_accumulates_content(monkeypatch):
    """Streaming chat responses should concatenate deltas and report progress."""
    lines = [
        b'data: {"choices":[{"delta":{"content":"A"}}]}',
        b'data: {"choices":[{"delta":{"content":"B"}}]}',
        b"data: [DONE]",
    ]
    monkeypatch.setattr(
        "correct_ai.llm_providers.requests.post",
        lambda *args, **kwargs: (events.append("post"), FakeResponse(lines))[1],
    )
    progress = []
    events = []
    provider = ProviderConfig("test", "http://test", "model")
    assert (
        ask_llm_with_progress(
            provider, "prompt", progress_callback=progress.append, inference_callback=events.append
        )
        == "AB"
    )
    assert events == [True, "post", False]
    assert progress[0].startswith("[Tokens envoyés:")
    assert progress[-1].startswith("[Total:")
