"""Focused Flask API tests for file, OCR, batch, and settings routes.

These tests use temporary project directories and tiny generated PDFs.  They
exercise the HTTP boundary directly, including success and validation paths,
without starting a server or contacting an LLM provider.
"""

import csv
import io

import pymupdf
import pytest

from correct_ai import app as app_module
from correct_ai.database import get_db, init_db


@pytest.fixture
def project_client(tmp_path, monkeypatch):
    """Return a Flask client configured with an isolated project directory."""
    copies_dir = tmp_path / "copies_anonymes_validees"
    copies_dir.mkdir()
    monkeypatch.setattr(app_module.config, "project_dir", str(tmp_path))
    monkeypatch.setattr(app_module.config, "save", lambda: None)
    return app_module.app.test_client(), tmp_path


def make_pdf(path, pages=1):
    """Create a small valid PDF containing the requested number of pages."""
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((72, 72), f"Page {index + 1}")
    document.save(path)
    document.close()


def test_favicon_is_served(project_client):
    """The application should expose its PNG favicon route."""
    client, _ = project_client
    response = client.get("/favicon-512x512.png")
    assert response.status_code == 200
    assert response.content_type == "image/png"


def test_settings_get_and_post(project_client):
    """Settings should reject invalid paths and accept existing directories."""
    client, project = project_client
    assert client.get("/settings").status_code == 200
    assert client.post("/settings", data={"project_dir": "/missing"}).status_code == 200
    assert client.post("/settings", data={"project_dir": str(project)}).status_code == 302


def test_batch_api_rejects_invalid_payload(project_client):
    """Batch start should reject malformed JSON and non-integer bounds."""
    client, project = project_client
    (project / "copies_anonymes_validees" / "Copie_01.pdf").touch()
    assert client.post("/api/batch/start", json={"type": "ocr", "start": "bad"}).status_code == 400
    assert (
        client.post("/api/batch/start", json={"type": "ocr", "start": 2, "end": 1}).status_code
        == 400
    )
    assert (
        client.post("/api/batch/start", data="not-json", content_type="text/plain").status_code
        == 400
    )


def test_batch_status_and_stop_without_job(project_client):
    """Batch status is readable and stopping an idle job returns a client error."""
    client, _ = project_client
    assert client.get("/api/batch/status").status_code == 200
    assert client.post("/api/batch/stop").status_code == 400


def test_legacy_extraction_endpoint(project_client):
    """The legacy extraction endpoint should round-trip Markdown content."""
    client, _ = project_client
    payload = {"content": "# OCR\nTexte"}
    assert client.post("/api/extraction/Copie_01", json=payload).get_json()["success"]
    assert client.get("/api/extraction/Copie_01").get_json() == payload


def test_legacy_correction_endpoint(project_client):
    """The legacy correction endpoint should write and read CSV rows."""
    client, project = project_client
    (project / "bareme.csv").write_text("question,points,note\nQ1,1,\n", encoding="utf-8")
    rows = [{"question": "Q1", "points": "1", "note": "0.5"}]
    assert client.post("/api/correction/Copie_01", json=rows).get_json()["success"]
    assert client.get("/api/correction/Copie_01").get_json() == rows


def test_ocr_page_content_and_rebuild(project_client):
    """OCR page content should persist and rebuild the aggregate extraction."""
    client, project = project_client
    assert client.post("/api/ocr/content/Copie_01/1", json={"content": "Q1"}).status_code == 200
    assert client.get("/api/ocr/content/Copie_01/01").get_json()["content"] == "Q1"
    response = client.post("/api/ocr/rebuild_extraction/Copie_01")
    assert response.get_json()["success"] is True
    extraction = project / "copies_anonymes_validees" / "Copie_01" / "extraction.md"
    assert "page 01" in extraction.read_text(encoding="utf-8")


def test_ocr_pages_and_pdf_routes(project_client):
    """OCR listing routes should expose page count and PDF/page image responses."""
    client, project = project_client
    pdf_path = project / "copies_anonymes_validees" / "Copie_01.pdf"
    make_pdf(pdf_path, pages=2)
    page_data = client.get("/api/ocr/pages/Copie_01").get_json()
    assert page_data["total_pages"] == 2
    assert client.get("/api/ocr/pdf/Copie_01").status_code == 200
    image = client.get("/api/ocr/pdf/Copie_01?page=1")
    assert image.status_code == 200
    assert image.content_type == "image/png"


def test_file_copy_listing_and_bareme(project_client):
    """File-based copy and rubric endpoints should return available records."""
    client, project = project_client
    output = project / "copies_anonymes_validees"
    (output / "Copie_02.pdf").touch()
    (project / "bareme.csv").write_text("question,answer\nQ1,OK\n", encoding="utf-8")
    assert client.get("/api/copies").get_json() == ["Copie_02"]
    assert client.get("/api/bareme").get_json()[0]["question"] == "Q1"
    assert client.get("/api/pdf/Copie_02").status_code == 200
    assert client.get("/api/pdf/Missing").status_code == 404


def test_split_thumbnail_without_document(project_client):
    """Thumbnail requests should return not-found before a booklet is imported."""
    client, _ = project_client
    assert client.get("/api/split/thumbnail/1").status_code == 404


def test_database_copy_listing(project_client):
    """SQLite copy listing should expose initialized database rows."""
    client, project = project_client
    db = get_db(str(project))
    init_db(db)
    db.execute("INSERT INTO copies (id, nom, prenom) VALUES (?, ?, ?)", [1, "Doe", "Jane"])
    db.commit()
    db.close()
    assert client.get("/api/db/copies").get_json()[0]["nom"] == "Doe"
