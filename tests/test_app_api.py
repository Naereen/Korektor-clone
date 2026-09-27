"""Focused Flask API tests for file, OCR, batch, and settings routes.

These tests use temporary project directories and tiny generated PDFs.  They
exercise the HTTP boundary directly, including success and validation paths,
without starting a server or contacting an LLM provider.
"""

import csv
import io
import json

import pymupdf
import pytest
from PIL import Image

from correct_ai import app as app_module
from correct_ai.database import get_db, init_db


@pytest.fixture
def project_client(tmp_path, monkeypatch):
    """Return a Flask client configured with an isolated project directory."""
    from correct_ai import llm_providers

    copies_dir = tmp_path / "copies_anonymes_validees"
    copies_dir.mkdir()
    monkeypatch.setattr(app_module.config, "project_dir", str(tmp_path))
    monkeypatch.setattr(
        app_module.config, "inference_provider", app_module.config.inference_provider
    )
    monkeypatch.setattr(llm_providers, "ACTIVE_PROVIDER", llm_providers.ACTIVE_PROVIDER)
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


def test_footer_formats_commit_timestamp_and_preserves_copyright_year(project_client, monkeypatch):
    """The footer displays the commit time without changing the copyright year."""
    client, _ = project_client

    def fake_run(command, **kwargs):
        output = (
            "abc123\x1fMessage\x1f2024-02-03T09:07:00+0100"
            if command[1] == "log"
            else "git@github.com:Naereen/Korektor-clone.git"
        )
        return type("Result", (), {"stdout": output})()

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    content = client.get("/documentation").get_data(as_text=True)
    assert "À 09:07 le 03/02/2024" in content
    assert "© 2024" in content


def test_settings_get_and_post(project_client):
    """Settings should reject invalid paths and accept existing directories."""
    client, project = project_client
    page = client.get("/settings")
    assert page.status_code == 200
    assert "ILAAS (Qwen)" in page.get_data(as_text=True)
    assert "Google Gemini (Flash)" in page.get_data(as_text=True)
    assert client.post("/settings", data={"project_dir": "/missing"}).status_code == 200
    response = client.post(
        "/settings",
        data={"project_dir": str(project), "inference_provider": "GOOGLE_GEMINI"},
    )
    assert response.status_code == 302
    assert app_module.config.inference_provider == "GOOGLE_GEMINI"
    assert app_module.get_provider().name == "GOOGLE_GEMINI"


def test_project_config_persists_inference_provider(monkeypatch, tmp_path):
    """The selected inference provider should survive application config reload."""
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(app_module, "CONFIG_FILE", str(config_path))
    config = app_module.ProjectConfig()
    config.inference_provider = "GOOGLE_GEMINI"
    config.save()

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["inference_provider"] == "GOOGLE_GEMINI"
    assert app_module.ProjectConfig().inference_provider == "GOOGLE_GEMINI"


def test_student_roster_page_and_csv_crud(project_client):
    """Student CRUD should preserve roster preamble and existing CSV columns."""
    client, project = project_client
    roster = project / "liste-etudiants.csv"
    roster.write_text(
        "Export de la promotion,2026\n\n,TD,N° étudiant,Nom,Prénom,Email\n"
        "L3 MAP,TD1,21922865,Dupont,Jean,jean@example.fr\n",
        encoding="utf-8",
    )

    page = client.get("/students")
    assert page.status_code == 200
    assert "Ajouter un étudiant" in page.get_data(as_text=True)
    assert "href=\"/students\"" in page.get_data(as_text=True)

    response = client.get("/api/students")
    assert response.status_code == 200
    assert response.get_json() == [
        {
            "id": "21922865",
            "name": "Dupont",
            "firstname": "Jean",
            "group": "TD1",
            "email": "jean@example.fr",
        }
    ]

    added = client.post(
        "/api/students",
        json={
            "id": "ETU-2",
            "name": "Martin",
            "firstname": "Alice",
            "group": "TD2",
            "email": "alice@example.fr",
        },
    )
    assert added.status_code == 201
    assert "Export de la promotion,2026" in roster.read_text(encoding="utf-8")

    updated = client.put(
        "/api/students/ETU-2",
        json={
            "id": "ETU-2",
            "name": "Martin",
            "firstname": "Zoé",
            "group": "TD3",
            "email": "zoe@example.fr",
        },
    )
    assert updated.status_code == 200
    assert client.get("/api/students").get_json()[1]["firstname"] == "Zoé"

    deleted = client.delete("/api/students/ETU-2")
    assert deleted.status_code == 200
    assert [row["id"] for row in client.get("/api/students").get_json()] == ["21922865"]


def test_student_roster_rejects_invalid_and_duplicate_students(project_client):
    """Student APIs should validate required fields, IDs, mail, and uniqueness."""
    client, project = project_client
    (project / "liste-etudiants.csv").write_text(
        "Num;Nom;Prénom\n42;Durand;Camille\n", encoding="utf-8"
    )
    valid = {"id": "42", "name": "Durand", "firstname": "Camille", "group": "", "email": ""}

    assert client.post("/api/students", json=valid).status_code == 409
    assert client.post(
        "/api/students",
        json={**valid, "id": "bad id"},
    ).status_code == 400
    assert client.post(
        "/api/students",
        json={**valid, "id": "43", "email": "pas-une-adresse"},
    ).status_code == 400
    assert client.post(
        "/api/students",
        json={**valid, "id": "43", "firstname": ""},
    ).status_code == 400
    assert client.put(
        "/api/students/42",
        json={**valid, "id": "43"},
    ).status_code == 400
    assert client.delete("/api/students/inconnu").status_code == 404


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


def test_batch_status_tracks_current_ocr_inference(project_client, monkeypatch):
    """OCR inference time starts at the request and freezes when it returns."""
    client, _ = project_client
    job = app_module.BatchJob()
    monkeypatch.setattr(app_module, "current_job", job)
    now = [1000.0]
    monkeypatch.setattr(app_module.time, "time", lambda: now[0])

    assert client.get("/api/batch/status").get_json()["inference_elapsed_seconds"] is None
    job.inference_started()
    now[0] += 65
    assert client.get("/api/batch/status").get_json()["inference_elapsed_seconds"] == 65
    job.inference_finished()
    now[0] += 10
    assert client.get("/api/batch/status").get_json()["inference_elapsed_seconds"] == 65
    job.inference_started()
    assert client.get("/api/batch/status").get_json()["inference_elapsed_seconds"] == 0


def test_batch_status_reports_continuous_elapsed_and_eta(project_client, monkeypatch):
    """Batch duration spans copy boundaries and ETA uses the average copy time."""
    client, _ = project_client
    job = app_module.BatchJob()
    monkeypatch.setattr(app_module, "current_job", job)
    now = [1000.0]
    monkeypatch.setattr(app_module.time, "monotonic", lambda: now[0])

    job.running = True
    job._batch_started_at = now[0]
    job.total_copies = 45
    now[0] += 60
    job.copy_completed(1)

    status = client.get("/api/batch/status").get_json()
    assert status["elapsed_seconds"] == 60
    assert status["completed_copies"] == 1
    assert status["total_copies"] == 45
    assert status["eta_seconds"] == 44 * 60

    job.inference_started()
    now[0] += 20
    job.inference_finished()
    now[0] += 10
    assert client.get("/api/batch/status").get_json()["elapsed_seconds"] == 90

    job.copy_completed(2)
    status = client.get("/api/batch/status").get_json()
    assert status["eta_seconds"] == int(90 / 2 * 43)

    job._batch_elapsed_seconds = job.elapsed_seconds
    job._batch_started_at = None
    job.running = False
    now[0] += 50
    assert client.get("/api/batch/status").get_json()["elapsed_seconds"] == 90


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


def test_student_pdf_is_generated_inline_then_reused(project_client, monkeypatch):
    """A copy's Typst PDF is generated once and served inline on later requests."""
    client, project = project_client
    db = get_db(str(project))
    init_db(db)
    db.execute("INSERT INTO copies (id, nom) VALUES (1, 'Doe')")
    db.commit()
    db.close()
    calls = []

    def fake_generate(copy_id, db_path, output_path):
        calls.append((copy_id, db_path, output_path))
        make_pdf(output_path)
        return str(output_path)

    monkeypatch.setattr(app_module, "generate_student_pdf", fake_generate, raising=False)
    for _ in range(2):
        response = client.get("/api/copies/1/pdf")
        assert response.status_code == 200
        assert response.mimetype == "application/pdf"
        assert "attachment" not in response.headers.get("Content-Disposition", "")
    assert len(calls) == 1
    assert client.get("/api/copies/1/pdf?refresh=1").status_code == 200
    assert len(calls) == 2
    assert calls[0][0] == 1
    assert calls[0][2] == str(project / "exports_pdf" / "Copie_01.pdf")


def test_generate_all_student_pdfs_reports_partial_failure(project_client, monkeypatch):
    """Batch generation reports successes and failures without stopping early."""
    client, project = project_client
    db = get_db(str(project))
    init_db(db)
    db.executemany("INSERT INTO copies (id) VALUES (?)", [(1,), (2,), (3,)])
    db.commit()
    db.close()

    def fake_generate(copy_id, db_path, output_path):
        if copy_id == 2:
            raise RuntimeError("Typst indisponible pour cette copie")
        make_pdf(output_path)
        return str(output_path)

    monkeypatch.setattr(app_module, "generate_student_pdf", fake_generate, raising=False)
    response = client.post("/api/copies/generate-all-pdfs")
    assert response.status_code == 500
    data = response.get_json()
    assert data["generated"] == 2
    assert data["total"] == 3
    assert data["errors"][0]["copie_id"] == 2
    assert (project / "exports_pdf" / "Copie_01.pdf").exists()
    assert (project / "exports_pdf" / "Copie_03.pdf").exists()


def test_correction_crop_is_inline_and_cannot_escape_project(project_client, tmp_path):
    """Only an existing PNG inside the active project can be returned."""
    client, project = project_client
    db = get_db(str(project))
    init_db(db)
    crop = project / "cropped_questions" / "question.png"
    crop.parent.mkdir()
    Image.new("RGB", (16, 16), "white").save(crop)
    db.execute(
        "INSERT INTO corrections (id, copie_id, bareme_id, reponse_etudiant, crop_image_path) "
        "VALUES (1, 1, 1, 'x', 'cropped_questions/question.png')"
    )
    db.commit()
    response = client.get("/api/corrections/1/crop")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data.startswith(b"\x89PNG")
    assert client.get("/api/corrections/999/crop").status_code == 404
    outside = tmp_path.parent / "outside.png"
    Image.new("RGB", (16, 16), "white").save(outside)
    db.execute("UPDATE corrections SET crop_image_path = ? WHERE id = 1", ("../outside.png",))
    db.commit()
    assert client.get("/api/corrections/1/crop").status_code == 404
    db.execute("UPDATE corrections SET crop_image_path = 42 WHERE id = 1")
    db.commit()
    invalid = client.get("/api/corrections/1/crop")
    assert invalid.status_code == 404
    assert "extrait" in invalid.get_json()["error"].lower()
    db.close()


def test_student_pdf_missing_copy_and_compilation_error(project_client, monkeypatch):
    """Absent copies are 404; Typst failures are explicit 500 JSON replies."""
    client, project = project_client
    assert client.get("/api/copies/1/pdf").status_code == 404
    db = get_db(str(project))
    init_db(db)
    db.execute("INSERT INTO copies (id) VALUES (1)")
    db.commit()
    db.close()
    missing = client.get("/api/copies/999/pdf")
    assert missing.status_code == 404
    assert "introuvable" in missing.get_json()["error"]

    def fail_generation(*args):
        raise RuntimeError("Échec Typst")

    monkeypatch.setattr(app_module, "generate_student_pdf", fail_generation)
    failed = client.get("/api/copies/1/pdf")
    assert failed.status_code == 500
    assert "Échec Typst" in failed.get_json()["error"]


def test_student_pdf_route_compiles_real_typst_report(project_client):
    """The Flask endpoint compiles and serves a readable PDF without mocks."""
    client, project = project_client
    db = get_db(str(project))
    init_db(db)
    db.execute("INSERT INTO sujet (contenu) VALUES ('# Contrôle')")
    db.execute("INSERT INTO corrige (contenu) VALUES ('**Q1.** Bonne réponse')")
    db.execute("INSERT INTO bareme (id, question, type_qcm, points) VALUES (1, 'Q1', 0, 2)")
    db.execute("INSERT INTO copies (id, nom, prenom) VALUES (1, 'Éloi', 'Zoé')")
    db.execute(
        "INSERT INTO corrections (copie_id, bareme_id, reponse_etudiant, note_llm) "
        "VALUES (1, 1, 'Réponse', 1.5)"
    )
    db.commit()
    db.close()

    response = client.get("/api/copies/1/pdf")
    assert response.status_code == 200
    assert response.data.startswith(b"%PDF")
    with pymupdf.open(stream=response.data, filetype="pdf") as document:
        assert "Éloi" in " ".join(page.get_text() for page in document)


def test_generate_all_student_pdfs_success(project_client, monkeypatch):
    """A complete promotion returns its successful generation count."""
    client, project = project_client
    db = get_db(str(project))
    init_db(db)
    db.executemany("INSERT INTO copies (id) VALUES (?)", [(1,), (2,)])
    db.commit()
    db.close()

    def fake_generate(copy_id, db_path, output_path):
        make_pdf(output_path)
        return str(output_path)

    monkeypatch.setattr(app_module, "generate_student_pdf", fake_generate)
    response = client.post("/api/copies/generate-all-pdfs")
    assert response.status_code == 200
    assert response.get_json()["generated"] == 2
    assert response.get_json()["success"] is True
    assert response.get_json()["errors"] == []


def test_correction_crop_missing_data_returns_json_404(project_client):
    """Missing database, correction and image all return clear JSON errors."""
    client, project = project_client
    assert "Base de données" in client.get("/api/corrections/1/crop").get_json()["error"]
    db = get_db(str(project))
    init_db(db)
    db.execute(
        "INSERT INTO corrections (id, copie_id, bareme_id, reponse_etudiant) "
        "VALUES (1, 1, 1, 'x')"
    )
    db.commit()
    assert client.get("/api/corrections/999/crop").status_code == 404
    no_image = client.get("/api/corrections/1/crop")
    assert no_image.status_code == 404
    assert "Aucun extrait" in no_image.get_json()["error"]
    db.execute("UPDATE corrections SET crop_image_path = 'missing.png' WHERE id = 1")
    db.commit()
    missing = client.get("/api/corrections/1/crop")
    assert missing.status_code == 404
    assert "introuvable" in missing.get_json()["error"]
    db.close()
