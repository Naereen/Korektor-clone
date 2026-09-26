import os
import sys


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