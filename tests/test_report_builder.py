import os
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "correct_ai"))


def test_report_adds_final_llm_feedback(monkeypatch):
    import report_builder

    calls = []

    def fake_ask_llm(provider, prompt_text, system_prompt, thinking):
        calls.append((provider, prompt_text, system_prompt, thinking))
        return "## Appréciation globale\nTravail sérieux.\n\n## Conseils généraux\nRelisez les définitions."

    monkeypatch.setattr(report_builder, "ask_llm", fake_ask_llm)

    report = report_builder.build_report_markdown(
        {"id": 1, "nom": "Durand", "prenom": "Lou", "etudiant_id": "42"},
        [
            {
                "question": "Q1",
                "note": 1,
                "points": 2,
                "commentaires eventuels du correcteur": "Bonne méthode.",
            }
        ],
        provider=object(),
    )

    assert len(calls) == 1
    assert "Bonne méthode." in calls[0][1]
    assert calls[0][3] is False
    assert "Travail sérieux." in report
    assert "Relisez les définitions." in report


def test_student_report_does_not_disclose_internal_reasoning():
    """Teacher-only reasoning is excluded from the student-facing report."""
    import report_builder

    report = report_builder.build_report_markdown(
        {"id": 7, "nom": "Martin", "prenom": "Lou"},
        [
            {
                "question": "Q1",
                "note": 1,
                "points": 2,
                "commentaires eventuels du correcteur": "Méthode à reprendre.",
                "raisonnement": "INTERNAL_REASONING_MUST_NOT_LEAK",
            }
        ],
        provider=None,
    )

    assert "Méthode à reprendre." in report
    assert "INTERNAL_REASONING_MUST_NOT_LEAK" not in report


@pytest.mark.parametrize(
    ("generator", "data"),
    [
        ("generate_grade_chart", (8, [6, 10], 20)),
        (
            "generate_question_profile_chart",
            ([{"question": "Q1", "points": 2, "note": 1, "min": 0, "max": 2, "moyenne": 1}],),
        ),
    ],
)
def test_report_charts_only_write_to_requested_path(tmp_path, generator, data):
    """Both chart builders create the requested image without /tmp side copies."""
    import report_builder

    output = tmp_path / f"{tmp_path.name}-chart.png"

    assert getattr(report_builder, generator)(*data, str(output))
    assert output.is_file()
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert not (Path("/tmp") / output.name).exists()
