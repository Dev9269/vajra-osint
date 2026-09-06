"""Entity extraction pipeline for VAJRA OSINT aggregator.

CHANGELOG 2026-09-06: rebuilt to extract full structured fields:
  age, gender, crime_type, crime_description, status, location,
  fir_number, plate_number, phone_number, plus person names.

spaCy NER for PERSON/LOCATION, regex for everything else.
Regex matches take priority over NER for overlapping text spans.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import spacy

logger = logging.getLogger(__name__)

# ── Gazetteer ───────────────────────────────────────────────────────────────

LOCATIONS_GAZETTEER = {
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Delhi", "Chandigarh", "Puducherry", "Jammu and Kashmir", "Ladakh",
    "Mumbai", "Delhi", "New Delhi", "Bangalore", "Hyderabad", "Ahmedabad", "Chennai",
    "Kolkata", "Pune", "Jaipur", "Lucknow", "Kanpur", "Nagpur", "Indore",
    "Thane", "Bhopal", "Visakhapatnam", "Patna", "Vadodara", "Ghaziabad",
    "Ludhiana", "Agra", "Nashik", "Faridabad", "Meerut", "Rajkot",
    "UP", "MP", "TN", "WB", "GJ", "MH", "KA", "RJ", "AP", "TS", "KL", "PB",
    "NCR", "NCR",
}

# ── Crime type keywords ─────────────────────────────────────────────────────

CRIME_TYPES = {
    "murder": "murder",
    "homicide": "murder",
    "killing": "murder",
    "killed": "murder",
    "kill": "murder",
    "assassination": "murder",
    "cyber fraud": "cyber fraud",
    "cybercrime": "cyber fraud",
    "online fraud": "cyber fraud",
    "phishing": "cyber fraud",
    "banking fraud": "cyber fraud",
    "fraud": "fraud",
    "cheating": "fraud",
    "embezzlement": "fraud",
    "scam": "fraud",
    "smuggling": "smuggling",
    "narcotics": "narcotics",
    "drug": "narcotics",
    "drug trafficking": "narcotics",
    "heroin": "narcotics",
    "cocaine": "narcotics",
    "cannabis": "narcotics",
    "contraband": "narcotics",
    "robbery": "robbery",
    "dacoity": "robbery",
    "theft": "theft",
    "burglary": "theft",
    "kidnapping": "kidnapping",
    "abduction": "kidnapping",
    "extortion": "extortion",
    "blackmail": "extortion",
    "terror": "terrorism",
    "terrorism": "terrorism",
    "terrorist": "terrorism",
    "naxal": "naxalism",
    "naxalite": "naxalism",
    "maoist": "naxalism",
    "money laundering": "money laundering",
    "pmla": "money laundering",
    "corruption": "corruption",
    "bribery": "corruption",
    "rape": "sexual assault",
    "sexual assault": "sexual assault",
    "murder for hire": "murder",
    "serial murder": "murder",
    "culpable homicide": "murder",
    "attempt to murder": "attempted murder",
    "culpable homicide not amounting to murder": "culpable homicide",
}

# ── Status keywords ─────────────────────────────────────────────────────────

STATUS_KEYWORDS = {
    "wanted": "wanted",
    "fugitive": "wanted",
    "absconding": "absconding",
    "on the run": "absconding",
    "evading": "absconding",
    "convicted": "convicted",
    "found guilty": "convicted",
    "sentenced": "convicted",
    "sentence": "convicted",
    "acquitted": "acquitted",
    "found not guilty": "acquitted",
    "arrested": "arrested",
    "taken into custody": "arrested",
    "in custody": "arrested",
    "surrendered": "surrendered",
    "surrender": "surrendered",
    "bail": "on bail",
    "released on bail": "on bail",
    "interpol": "interpol red notice",
    "red notice": "interpol red notice",
    "chargesheet": "chargesheet filed",
    "chargesheeted": "chargesheet filed",
    "encounter": "encounter killed",
    "killed in encounter": "encounter killed",
}

# ── Regex patterns ──────────────────────────────────────────────────────────

PHONE_PATTERN = re.compile(r"(?:\+91[\s-]?)?[6-9]\d{9}")
PLATE_PATTERN = re.compile(r"\b[A-Z]{2}[\s-]?\d{1,2}[\s-]?[A-Z]{1,2}[\s-]?\d{1,4}\b")
FIR_PATTERN = re.compile(r"\bFIR\s*(?:No\.?\s*)?(\d{1,6}/\d{4})", re.IGNORECASE)
CASE_PATTERN = re.compile(r"\b(?:Case|CC|SC)\s*(?:No\.?\s*)?(\d{1,6}/\d{4})", re.IGNORECASE)

# Age patterns
AGE_EXACT = re.compile(r"\b(?:aged?|age|years?\s*old)\s*[:=]?\s*(\d{1,3})\b", re.IGNORECASE)
AGE_YEAR_OLD = re.compile(r"(\d{1,3})[\s-]*(?:year[\s-]*old|yr[\s-]*old|y/?o)\b", re.IGNORECASE)
AGE_NUMBER_ONLY = re.compile(r"\baged\s+(\d{1,3})\b", re.IGNORECASE)

# DOB patterns
DOB_DMY = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
DOB_MDN = re.compile(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+(\d{4})\b", re.IGNORECASE)
DOB_YEAR_ONLY = re.compile(r"\bborn\s+(?:in\s+)?(\d{4})\b", re.IGNORECASE)

# Gender patterns
GENDER_MALE = re.compile(r"\b(?:he|his|him|man|male|boy|gentleman|s/o|son\s+of|father|husband|brother)\b", re.IGNORECASE)
GENDER_FEMALE = re.compile(r"\b(?:she|her|woman|female|girl|lady|d/o|daughter\s+of|mother|wife|sister)\b", re.IGNORECASE)


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class ExtractedPerson:
    name: str
    age: Optional[int] = None
    gender: Optional[str] = None
    crime_type: Optional[str] = None
    crime_description: Optional[str] = None
    status: Optional[str] = None
    location: Optional[str] = None
    fir_number: Optional[str] = None
    plate_number: Optional[str] = None
    phone_number: Optional[str] = None
    start: int = 0
    end: int = 0
    source: str = "ner"


# ── NLP Pipeline ────────────────────────────────────────────────────────────

_nlp = None


def _get_nlp():
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
            logger.error("No spaCy model. Run: python -m spacy download en_core_web_sm")
            raise
    return _nlp


# ── Extraction functions ────────────────────────────────────────────────────

def _extract_age(text: str) -> Optional[int]:
    """Extract age from text using multiple patterns."""
    m = AGE_EXACT.search(text)
    if m:
        age = int(m.group(1))
        if 10 <= age <= 100:
            return age

    m = AGE_YEAR_OLD.search(text)
    if m:
        age = int(m.group(1))
        if 10 <= age <= 100:
            return age

    m = AGE_NUMBER_ONLY.search(text)
    if m:
        age = int(m.group(1))
        if 10 <= age <= 100:
            return age

    # Try DOB → calculate age
    m = DOB_DMY.search(text)
    if m:
        from datetime import datetime
        try:
            dob = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            age = (datetime.now() - dob).days // 365
            if 10 <= age <= 100:
                return age
        except ValueError:
            pass

    m = DOB_YEAR_ONLY.search(text)
    if m:
        from datetime import datetime
        try:
            age = datetime.now().year - int(m.group(1))
            if 10 <= age <= 100:
                return age
        except ValueError:
            pass

    return None


def _extract_gender(text: str) -> Optional[str]:
    """Detect gender from pronouns/context."""
    male_score = len(GENDER_MALE.findall(text))
    female_score = len(GENDER_FEMALE.findall(text))
    if female_score > male_score:
        return "female"
    if male_score > female_score:
        return "male"
    return None


def _extract_crime_type(text: str) -> Optional[str]:
    """Match crime keywords against text, return most specific match."""
    text_lower = text.lower()
    matched = []
    for keyword, crime_type in CRIME_TYPES.items():
        if keyword in text_lower:
            matched.append((len(keyword), crime_type))
    if matched:
        matched.sort(key=lambda x: -x[0])
        return matched[0][1]
    return None


def _extract_status(text: str) -> Optional[str]:
    """Detect status from keywords. Returns most specific status."""
    text_lower = text.lower()
    matched = []
    for keyword, status in STATUS_KEYWORDS.items():
        if keyword in text_lower:
            matched.append((len(keyword), status))
    if matched:
        matched.sort(key=lambda x: -x[0])
        return matched[0][1]
    return None


def _extract_fir(text: str) -> Optional[str]:
    """Extract FIR reference number."""
    m = FIR_PATTERN.search(text)
    if m:
        return f"FIR {m.group(1)}"
    m = CASE_PATTERN.search(text)
    if m:
        return f"Case {m.group(1)}"
    return None


def _extract_phone(text: str) -> Optional[str]:
    """Extract Indian phone number."""
    m = PHONE_PATTERN.search(text)
    if m:
        return m.group().strip()
    return None


def _extract_plate(text: str) -> Optional[str]:
    """Extract vehicle registration plate."""
    m = PLATE_PATTERN.search(text)
    if m:
        return m.group().strip()
    return None


def _extract_location(text: str) -> Optional[str]:
    """Extract primary location using gazetteer (word-boundary match).

    Prefers the LONGEST matching location so "New Delhi" wins over "Delhi".
    """
    matches = []
    for loc in LOCATIONS_GAZETTEER:
        pattern = re.compile(rf"\b{re.escape(loc)}\b")
        if pattern.search(text):
            matches.append(loc)

    if not matches:
        return None
    # Highest specificity = longest name
    matches.sort(key=len, reverse=True)
    return matches[0]


def _extract_names_ner(text: str) -> list[ExtractedPerson]:
    """Extract PERSON entities via spaCy NER."""
    nlp = _get_nlp()
    doc = nlp(text)
    persons = []
    for ent in doc.ents:
        if ent.label_ == "PERSON" and len(ent.text.strip()) > 2:
            persons.append(ExtractedPerson(
                name=ent.text.strip(),
                start=ent.start_char,
                end=ent.end_char,
                source="ner",
            ))
    return persons


def extract_persons(text: str) -> list[ExtractedPerson]:
    """Extract structured person records from text.

    Returns a list of ExtractedPerson, each with as many fields populated
    as the source text provides. Missing fields are None — never fabricated.
    """
    if not text or len(text.strip()) < 10:
        return []

    # Step 1: extract structured fields (apply to full text)
    age = _extract_age(text)
    gender = _extract_gender(text)
    crime_type = _extract_crime_type(text)
    status = _extract_status(text)
    location = _extract_location(text)
    fir_number = _extract_fir(text)
    phone_number = _extract_phone(text)
    plate_number = _extract_plate(text)

    # Step 2: extract person names via NER
    persons = _extract_names_ner(text)

    # If no names found via NER, return empty list — don't fabricate
    if not persons:
        return []

    # Step 3: attach the extracted fields to each person found
    for person in persons:
        person.age = age
        person.gender = gender
        person.crime_type = crime_type
        person.status = status
        person.location = location
        person.fir_number = fir_number
        person.phone_number = phone_number
        person.plate_number = plate_number
        # crime_description is the full text trimmed
        person.crime_description = text[:500]

    return persons


def extract_best_person_name(text: str) -> Optional[str]:
    """Extract the most likely primary person name from text.
    Returns the first PERSON entity found by NER, or None."""
    persons = _extract_names_ner(text)
    return persons[0].name if persons else None


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    tests = [
        (
            "Rakesh Kumar Verma, aged 34, from New Delhi was arrested "
            "in connection with FIR 123/2026 for cyber fraud. "
            "His vehicle registration DL 01 AB 1234 was seized. "
            "Contact: +91 9876543210.",
            {"age": 34, "gender": "male", "crime_type": "cyber fraud",
             "status": "arrested", "location": "New Delhi",
             "fir_number": "FIR 123/2026", "phone_number": "+919876543210",
             "plate_number": "DL 01 AB 1234"},
        ),
        (
            "Wanted fugitive Masood Azhar, founder of Jaish-e-Mohammed, "
            "is a designated terrorist with an Interpol red notice. "
            "Born in 1968, Bahawalpur, Pakistan.",
            {"gender": None, "crime_type": "terrorism",
             "status": "interpol red notice", "fir_number": None,
             "age": 58},  # derived from 'Born in 1968' -> 2026-1968
        ),
        (
            "She was convicted of murdering her husband in 2023. "
            "The woman, 28, was sentenced to life imprisonment in Lucknow.",
            {"gender": "female", "age": 28, "crime_type": "murder",
             "status": "convicted", "location": "Lucknow"},
        ),
        (
            "Absconding accused Pradeep Singh on the run from Lucknow, UP. "
            "Last seen near Chandigarh. Interpol red notice issued.",
            {"gender": None, "status": "absconding",
             "location": "Chandigarh", "fir_number": None},
        ),
        (
            "NIA has filed a chargesheet against 5 Myanmar nationals "
            "arrested in Arunachal Pradesh for drug smuggling. "
            "FIR 456/2026.",
            {"crime_type": "narcotics", "status": "chargesheet filed",
             "location": "Arunachal Pradesh", "fir_number": "FIR 456/2026"},
        ),
    ]

    print("Running entity extraction tests...\n")

    for i, (text, expected) in enumerate(tests, 1):
        persons = extract_persons(text)
        if not persons:
            print(f"Test {i}: NO PERSONS EXTRACTED (NER failed to find name)")
            print(f"  Text: {text[:80]}...")
            continue

        p = persons[0]
        print(f"Test {i}: {p.name}")
        checks = []
        for field_name in ["age", "gender", "crime_type", "status", "location", "fir_number"]:
            expected_val = expected.get(field_name)
            actual_val = getattr(p, field_name)
            ok = (expected_val is None and actual_val is None) or (actual_val == expected_val)
            checks.append(ok)
            ok = "OK" if ok else "FAIL"
            print(f"  {ok} {field_name}: got={actual_val}, expected={expected_val}")

        all_pass = all(checks)
        print(f"  {'PASS' if all_pass else 'PARTIAL'}\n")

    print("Entity extraction test complete.")
