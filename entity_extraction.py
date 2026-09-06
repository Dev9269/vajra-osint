"""Entity extraction pipeline for VAJRA OSINT aggregator.

spaCy NER for PERSON and LOCATION entities, plus regex for structured
identifiers (phone numbers, vehicle plates, FIR/case reference numbers).

Regex matches take priority over NER for overlapping text spans.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import spacy

logger = logging.getLogger(__name__)

# ── Gazetteer ───────────────────────────────────────────────────────────────
# Known locations that NER might misclassify as person names or miss entirely.
# Add Indian cities, states, and common location abbreviations.

LOCATIONS_GAZETTEER = {
    # States
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    # UTs
    "Delhi", "Chandigarh", "Puducherry", "Jammu and Kashmir", "Ladakh",
    "Andaman and Nicobar Islands", "Dadra and Nagar Haveli", "Lakshadweep",
    # Major cities
    "Mumbai", "Delhi", "Bangalore", "Hyderabad", "Ahmedabad", "Chennai",
    "Kolkata", "Pune", "Jaipur", "Lucknow", "Kanpur", "Nagpur", "Indore",
    "Thane", "Bhopal", "Visakhapatnam", "Patna", "Vadodara", "Ghaziabad",
    "Ludhiana", "Agra", "Nashik", "Faridabad", "Meerut", "Rajkot",
    # State abbreviations used in records
    "UP", "MP", "TN", "WB", "GJ", "MH", "KA", "RJ", "AP", "TS", "KL", "PB",
}

# ── Regex Patterns ──────────────────────────────────────────────────────────

# Indian phone numbers: 10 digits, optional +91 prefix
PHONE_PATTERN = re.compile(
    r"(?:\+91[\s-]?)?[6-9]\d{9}"
)

# Vehicle registration plates: AA 12 AA 1234 format (Indian RTO)
PLATE_PATTERN = re.compile(
    r"\b[A-Z]{2}[\s-]?\d{1,2}[\s-]?[A-Z]{1,2}[\s-]?\d{1,4}\b"
)

# FIR / Case reference numbers
FIR_PATTERN = re.compile(
    r"\bFIR\s*(?:No\.?\s*)?(\d{1,6}/\d{4})",
    re.IGNORECASE,
)

CASE_PATTERN = re.compile(
    r"\b(?:Case|CC|SC)\s*(?:No\.?\s*)?(\d{1,6}/\d{4})",
    re.IGNORECASE,
)

# ── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class ExtractedEntity:
    text: str
    label: str  # PERSON, LOCATION, PHONE, PLATE, FIR, CASE
    start: int
    end: int
    source: str = "ner"  # "ner" or "regex"


@dataclass
class ExtractionResult:
    persons: list[ExtractedEntity] = field(default_factory=list)
    locations: list[ExtractedEntity] = field(default_factory=list)
    phones: list[ExtractedEntity] = field(default_factory=list)
    plates: list[ExtractedEntity] = field(default_factory=list)
    case_refs: list[ExtractedEntity] = field(default_factory=list)


# ── NLP Pipeline ────────────────────────────────────────────────────────────

_nlp = None


def _get_nlp():
    """Load spaCy model — tries en_core_web_trf first, falls back to sm."""
    global _nlp
    if _nlp is not None:
        return _nlp
    try:
        _nlp = spacy.load("en_core_web_trf")
        logger.info("Loaded spaCy model: en_core_web_trf")
    except OSError:
        logger.warning("en_core_web_trf not found, falling back to en_core_web_sm")
        try:
            _nlp = spacy.load("en_core_web_sm")
        except OSError:
            logger.error("No spaCy model available. Run: python -m spacy download en_core_web_sm")
            raise
    return _nlp


# ── Extraction Functions ────────────────────────────────────────────────────

def _extract_regex(text: str) -> list[ExtractedEntity]:
    """Extract structured identifiers via regex (takes priority over NER)."""
    entities = []

    for match in PHONE_PATTERN.finditer(text):
        entities.append(ExtractedEntity(
            text=match.group().strip(),
            label="PHONE",
            start=match.start(),
            end=match.end(),
            source="regex",
        ))

    for match in PLATE_PATTERN.finditer(text):
        entities.append(ExtractedEntity(
            text=match.group().strip(),
            label="PLATE",
            start=match.start(),
            end=match.end(),
            source="regex",
        ))

    for match in FIR_PATTERN.finditer(text):
        entities.append(ExtractedEntity(
            text=f"FIR {match.group(1)}",
            label="FIR",
            start=match.start(),
            end=match.end(),
            source="regex",
        ))

    for match in CASE_PATTERN.finditer(text):
        entities.append(ExtractedEntity(
            text=f"Case {match.group(1)}",
            label="CASE",
            start=match.start(),
            end=match.end(),
            source="regex",
        ))

    return entities


def _extract_ner(text: str) -> list[ExtractedEntity]:
    """Extract PERSON and LOCATION entities via spaCy NER."""
    nlp = _get_nlp()
    doc = nlp(text)
    entities = []

    for ent in doc.ents:
        if ent.label_ == "PERSON":
            entities.append(ExtractedEntity(
                text=ent.text.strip(),
                label="PERSON",
                start=ent.start_char,
                end=ent.end_char,
                source="ner",
            ))
        elif ent.label_ in ("GPE", "LOC", "FAC"):
            entities.append(ExtractedEntity(
                text=ent.text.strip(),
                label="LOCATION",
                start=ent.start_char,
                end=ent.end_char,
                source="ner",
            ))

    return entities


def _gazetteer_locations(text: str) -> list[ExtractedEntity]:
    """Find gazetteer locations that NER might have missed.

    Uses whole-word boundary matching so 'ed' doesn't match inside 'arrested'.
    """
    entities = []

    for location in LOCATIONS_GAZETTEER:
        # Build word-boundary pattern, handling multi-word entries like "Uttar Pradesh"
        pattern = re.compile(rf"\b{re.escape(location)}\b")
        for match in pattern.finditer(text):
            entities.append(ExtractedEntity(
                text=location,
                label="LOCATION",
                start=match.start(),
                end=match.end(),
                source="gazetteer",
            ))

    return entities


def _deduplicate_spans(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
    """Remove overlapping spans, keeping regex results over NER over gazetteer."""
    if not entities:
        return []

    # Sort by start position, then by source priority (regex > ner > gazetteer)
    source_priority = {"regex": 0, "ner": 1, "gazetteer": 2}
    entities.sort(key=lambda e: (e.start, source_priority.get(e.source, 99)))

    deduped = [entities[0]]
    for entity in entities[1:]:
        last = deduped[-1]
        # Check for overlap
        if entity.start < last.end:
            # Keep the one with higher priority (lower source_priority number)
            if source_priority.get(entity.source, 99) < source_priority.get(last.source, 99):
                deduped[-1] = entity
        else:
            deduped.append(entity)

    return deduped


def extract_entities(text: str) -> ExtractionResult:
    """Full extraction pipeline: regex first, then NER, then gazetteer, deduplicate."""
    result = ExtractionResult()

    # Step 1: Regex (highest priority — structured patterns)
    regex_entities = _extract_regex(text)

    # Step 2: NER
    ner_entities = _extract_ner(text)

    # Step 3: Gazetteer fallback for locations
    gazetteer_ents = _gazetteer_locations(text)

    # Step 4: Combine and deduplicate
    all_entities = regex_entities + ner_entities + gazetteer_ents
    deduped = _deduplicate_spans(all_entities)

    # Step 5: Categorize
    for entity in deduped:
        if entity.label == "PERSON":
            result.persons.append(entity)
        elif entity.label == "LOCATION":
            result.locations.append(entity)
        elif entity.label == "PHONE":
            result.phones.append(entity)
        elif entity.label == "PLATE":
            result.plates.append(entity)
        elif entity.label in ("FIR", "CASE"):
            result.case_refs.append(entity)

    return result


def extract_persons_from_record(record_detail: str) -> list[str]:
    """Convenience: extract just person names from a record's detail text."""
    result = extract_entities(record_detail)
    return [p.text for p in result.persons]


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    test_texts = [
        "Rakesh Kumar Verma, age 34, from New Delhi was arrested in connection with FIR 123/2026. Contact: +91 9876543210. Vehicle: DL 01 AB 1234.",
        "R. Verma of Mumbai is wanted by the CBI in case CC 456/2025 for cyber fraud.",
        "Absconding accused Pradeep Singh on the run from Lucknow, UP. Last seen near Chandigarh.",
    ]

    for text in test_texts:
        print(f"\nText: {text[:80]}...")
        result = extract_entities(text)
        print(f"  Persons: {[p.text for p in result.persons]}")
        print(f"  Locations: {[l.text for l in result.locations]}")
        print(f"  Phones: {[p.text for p in result.phones]}")
        print(f"  Plates: {[p.text for p in result.plates]}")
        print(f"  Case refs: {[c.text for c in result.case_refs]}")

    # Test name extraction from records
    names = extract_persons_from_record(
        "FIR 789/2026 — Rakesh Kumar Verma and Pradeep Singh accused in fraud case in Delhi"
    )
    print(f"\nExtracted names: {names}")
    print("Entity extraction test complete.")
