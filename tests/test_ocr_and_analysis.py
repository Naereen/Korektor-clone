"""Unit tests for OCR helpers, subject parsing, and extraction reconstruction.

The tests focus on deterministic file and parsing behavior.  LLM calls are
monkeypatched where subject analysis reaches the enrichment step, so malformed
provider responses and fallback rubric generation can be checked offline.
"""

import json
from types import SimpleNamespace

import fitz

from correct_ai import batch_extraction, rebuild_all_extraction, subject_analysis


def test_extract_markdown_tags_and_missing_tags():
    """Markdown extraction should trim tagged content and reject untagged text."""
    assert batch_extraction.extract_markdown("before<markdown>  text  </markdown>after") == "text"
    assert batch_extraction.extract_markdown("plain response") is None


def test_page_to_base64_renders_requested_page(tmp_path):
    """Page rendering should return non-empty base64 image data."""
    pdf_path = tmp_path / "pages.pdf"
    document = fitz.open()
    document.new_page()
    document.save(pdf_path)
    document.close()
    encoded = batch_extraction.page_to_base64(str(pdf_path), 0, dpi=50)
    assert encoded


def test_extract_copy_recalculates_pages_when_forced(monkeypatch, tmp_path):
    """Forced extraction should replace old page files and write new OCR output."""
    base_dir = tmp_path / "copies_anonymes_validees"
    copy_dir = base_dir / "Copie_01"
    copy_dir.mkdir(parents=True)
    pdf_path = base_dir / "Copie_01.pdf"
    document = fitz.open()
    document.new_page()
    document.save(pdf_path)
    document.close()
    (tmp_path / "sujet.md").write_text("Subject", encoding="utf-8")
    (tmp_path / "corrige.md").write_text("Answer key", encoding="utf-8")
    (copy_dir / "page01.md").write_text("old", encoding="utf-8")
    (copy_dir / "extraction.md").write_text("old", encoding="utf-8")

    provider = SimpleNamespace(
        model="test-model",
        url="http://test",
        verify_ssl=True,
        api_key=lambda: "dummy",
    )

    class Response:
        """Small HTTP response fake for the extraction request."""

        def raise_for_status(self):
            """Represent a successful HTTP response."""

        def json(self):
            """Return a tagged transcription response."""
            return {"choices": [{"message": {"content": "<markdown>new</markdown>"}}]}

    monkeypatch.setattr(batch_extraction, "get_provider", lambda name: provider)
    monkeypatch.setattr(batch_extraction.requests, "post", lambda *args, **kwargs: Response())

    assert batch_extraction.extract_copy(1, str(tmp_path), force=True) is True
    assert (copy_dir / "page01.md").read_text(encoding="utf-8") == "new"
    assert "new" in (copy_dir / "extraction.md").read_text(encoding="utf-8")


def test_extract_copy_stops_before_processing(tmp_path):
    """An already-requested cancellation should avoid touching the project."""
    assert batch_extraction.extract_copy(1, str(tmp_path), stop_callback=lambda: True) is False


def test_rebuild_extraction_for_copy(tmp_path):
    """Reconstruction should order page files and mark empty pages."""
    (tmp_path / "page02.md").write_text("second", encoding="utf-8")
    (tmp_path / "page01.md").write_text("first", encoding="utf-8")
    rebuild_all_extraction.rebuild_extraction_for_copy(tmp_path)
    content = (tmp_path / "extraction.md").read_text(encoding="utf-8")
    assert content.index("page 01") < content.index("page 02")
    assert "first" in content and "second" in content


def test_rebuild_extraction_for_empty_copy(tmp_path):
    """Reconstruction should report a copy without page files without writing."""
    rebuild_all_extraction.rebuild_extraction_for_copy(tmp_path)
    assert not (tmp_path / "extraction.md").exists()


def test_analyze_subject_reconstructs_structure_without_llm(monkeypatch, tmp_path):
    """Subject markers should produce byte-reconstructible structure JSON."""
    (tmp_path / "sujet.md").write_text("Question one (--Q1)\nQuestion two (--Q2)\n", encoding="utf-8")
    generated = {}

    def fake_bareme(data, project_dir):
        generated["data"] = data

    monkeypatch.setattr(subject_analysis, "generate_bareme_from_structure", fake_bareme)
    assert subject_analysis.analyze_subject(str(tmp_path)) is True
    structure = json.loads((tmp_path / "structure.json").read_text(encoding="utf-8"))
    assert [item["numero"] for item in structure if item["numero"] != "END"] == ["Q1", "Q2"]
    assert generated["data"] == structure


def test_analyze_subject_rejects_missing_markers(tmp_path):
    """Subjects without annotations should fail before generating output."""
    (tmp_path / "sujet.md").write_text("No question markers", encoding="utf-8")
    assert subject_analysis.analyze_subject(str(tmp_path)) is False


def test_generate_bareme_uses_fallback_for_invalid_response(monkeypatch, tmp_path):
    """Invalid LLM enrichment should still create a usable fallback CSV."""
    (tmp_path / "corrige.md").write_text("Answer key", encoding="utf-8")
    monkeypatch.setattr(subject_analysis, "get_provider", lambda: object())
    monkeypatch.setattr(subject_analysis, "ask_llm", lambda *args, **kwargs: "not-json")
    subject_analysis.generate_bareme_from_structure(
        [{"numero": "Q1", "texte": "Question"}], str(tmp_path)
    )
    content = (tmp_path / "bareme.csv").read_text(encoding="utf-8")
    assert "Q1" in content
    assert "?" in content
