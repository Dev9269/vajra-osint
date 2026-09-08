"""NER adversarial corpus — pytest wrapper (marker: model)."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from adversarial_ner import ADVERSARIAL_CORPUS, run_adversarial_ner  # noqa: E402


def _model_available() -> bool:
    try:
        import spacy.util
        return "en_core_web_trf" in spacy.util.get_installed_models()
    except Exception:
        return False


@pytest.mark.model
@pytest.mark.skipif(not _model_available(), reason="en_core_web_trf not installed")
def test_adversarial_corpus_full_name_match():
    result = run_adversarial_ner()
    assert result["tested"] == len(ADVERSARIAL_CORPUS)
    # Corpus-order aware: accept minor NER collapse, require token-subset >= 80%
    assert result["full_name_matches"] >= len(ADVERSARIAL_CORPUS) * 0.8
    for text, expected, found in result["failures"]:
        print(f"FAIL: {text!r} expected={expected!r} got={found!r}")


@pytest.mark.model
@pytest.mark.skipif(not _model_available(), reason="en_core_web_trf not installed")
def test_critical_speaker_attribution_never_bleeds():
    """Amit Shah (speaker) must never be tagged with crime fields."""
    from entity_extraction import extract_persons

    text = ("Home Minister Amit Shah said police arrested three men for fraud in Noida. "
            "The accused were produced before court today.")
    for person in extract_persons(text):
        if person.name == "Amit Shah":
            assert person.crime_type is None
            assert person.status is None
            assert person.fir_number is None