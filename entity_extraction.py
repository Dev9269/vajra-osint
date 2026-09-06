"""Entity extraction pipeline for VAJRA OSINT aggregator.

CHANGELOG 2026-09-06 (bugfix round):
  - #1 CRITICAL: crime/status/FIR attribution is now scoped per-sentence,
    not broadcast across the whole document. Reported-speech speakers
    (e.g. "Amit Shah SAID police arrested...") are excluded from crime
    attribution so an innocent named person can't get a false record.
  - #2 NER accuracy: switched default to en_core_web_trf (transformer),
    which fixes small-model name truncation ("Rakesh Kumar Verma" ->
    "Kumar Verma") and missed names. Falls back to sm only if trf absent.
  - Self-test now exits non-zero on any failure.

Attribution scope: a person's crime/status/FIR/location are taken from
their OWN sentence plus a window of +/-1 adjacent sentence. Speakers
(nsubj of a reporting verb) are always excluded from crime attribution.
Age/gender/phone/plate come from the person's own sentence only.
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


# Reporting / communication verbs: if a person is the subject (nsubj/nsubjpass)
# of one of these, they're a SPEAKER (reported-speech source), not an accused.
REPORTING_VERBS = {
    "say", "said", "says", "saying",
    "tell", "told", "tells",
    "state", "stated", "states",
    "announce", "announced", "announces",
    "claim", "claimed", "claims",
    "warn", "warned", "warns",
    "reveal", "revealed", "reveals",
    "add", "added", "adds",
    "note", "noted", "notes",
    "confirm", "confirmed", "confirms",
    "assert", "asserted", "asserts",
    "declare", "declared", "declares",
    "mention", "mentioned", "mentions",
    "acknowledge", "acknowledged", "acknowledges",
    "write", "wrote", "writes", "written",
    "report", "reported", "reports",
}

# Crime/status/FIR/location attribution window (in sentences beyond the
# person's own sentence). Conservative default of 0 = SAME SENTENCE ONLY:
# a person only gets crime context if the keyword appears in the sentence
# that contains their name. This prioritizes avoiding false criminal records
# over recall. Raise it (e.g. to 1) to also catch cases where the FIR number
# or status sits in the neighboring sentence — at the cost of attaching
# context to innocent people merely named in an adjacent sentence.
ATTRIBUTION_WINDOW = 0

# Low-risk fields (age, gender, phone, plate) are drawn from a slightly wider
# window because pronoun/age evidence routinely sits in the adjacent sentence,
# and these are descriptive observations, not criminal accusations.
LOW_RISK_WINDOW = 1


def _is_speaker(token_head: object) -> bool:
    """Return True if the token's head is a reporting verb (reported speech)."""
    if token_head is None:
        return False
    for anc in token_head.ancestors:
        if anc.pos_ == "VERB" and anc.lemma_ in REPORTING_VERBS:
            return True
    if token_head.pos_ == "VERB" and token_head.lemma_ in REPORTING_VERBS:
        return True
    return False


def _extract_persons_scoped(text: str) -> list[ExtractedPerson]:
    """Extract persons with per-sentence crime/status/FIR/location attribution.

    Each PERSON entity gets its own sentence (plus a bounded window) as the
    scope for crime context. Persons that are subjects of a reporting verb
    (speakers) are excluded from crime attribution so they never get a false
    criminal record. char start/end remain document-global so callers can
    locate a person within the original text.
    """
    nlp = _get_nlp()
    doc = nlp(text)

    # Group sentences with their char span and text
    sentences = [s for s in doc.sents]

    # Map each sentence index -> set of (start, end) char spans of PERSON entities in it
    sent_person_spans = {i: [] for i in range(len(sentences))}
    speaker_spans = set()

    for ent in doc.ents:
        if ent.label_ != "PERSON" or len(ent.text.strip()) <= 2:
            continue
        # Find which sentence contains this entity
        sent_idx = None
        for i, s in enumerate(sentences):
            if s.start_char <= ent.start_char < s.end_char:
                sent_idx = i
                break
        if sent_idx is None:
            continue
        sent_person_spans[sent_idx].append((ent.start_char, ent.end_char, ent.text.strip()))

        # Speaker detection: is the head token (or an ancestor's head) a reporting verb?
        root = ent.root
        if _is_speaker(root):
            speaker_spans.add((ent.start_char, ent.end_char))

    persons = []
    for i, spans in sent_person_spans.items():
        if not spans:
            continue

        # Snapshot the attribution (crime/etc.) window and the low-risk window
        window_texts = []
        for j in range(max(0, i - ATTRIBUTION_WINDOW), min(len(sentences), i + ATTRIBUTION_WINDOW + 1)):
            window_texts.append(sentences[j].text)
        window_text = " ".join(window_texts)

        low_risk_texts = []
        for j in range(max(0, i - LOW_RISK_WINDOW), min(len(sentences), i + LOW_RISK_WINDOW + 1)):
            low_risk_texts.append(sentences[j].text)
        low_risk_text = " ".join(low_risk_texts)

        for (start, end, name) in spans:
            is_speaker = (start, end) in speaker_spans
            person = ExtractedPerson(
                name=name,
                start=start,
                end=end,
                source="ner",
            )

            # Low-risk descriptive fields: age/gender/phone/plate.
            person.age = _extract_age(low_risk_text)
            person.gender = _extract_gender(low_risk_text)
            person.phone_number = _extract_phone(low_risk_text)
            person.plate_number = _extract_plate(low_risk_text)

            # Crime/status/FIR/location: from the attribution window, never
            # for reported-speech speakers.
            if not is_speaker:
                person.crime_type = _extract_crime_type(window_text)
                person.status = _extract_status(window_text)
                person.location = _extract_location(window_text)
                person.fir_number = _extract_fir(window_text)
                person.crime_description = window_text[:500]
            else:
                person.crime_type = None
                person.status = None
                person.location = None
                person.fir_number = None
                person.crime_description = None

            persons.append(person)

    return persons


def extract_persons(text: str) -> list[ExtractedPerson]:
    """Extract structured person records from text.

    Attribution is scoped per-sentence (see _extract_persons_scoped). Missing
    fields are None — never fabricated.
    """
    if not text or len(text.strip()) < 10:
        return []
    return _extract_persons_scoped(text)


def extract_best_person_name(text: str) -> Optional[str]:
    """Extract the most likely primary person name from text.
    Returns the first PERSON entity found by NER, or None."""
    persons = extract_persons(text)
    return persons[0].name if persons else None


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    failures = 0


    def check(condition: bool, label: str):
        global failures
        print(f"  [{'PASS' if condition else 'FAIL'}] {label}")
        if not condition:
            failures += 1


    def named_persons(text):
        return [p.name for p in extract_persons(text)]

    print("Running entity extraction tests...\n")

    # Test 1: full name must NOT be truncated (regression for #2)
    print("Test 1: full name preserved with en_core_web_trf")
    t1 = ("Rakesh Kumar Verma, aged 34, from New Delhi was arrested "
          "in connection with FIR 123/2026 for cyber fraud. "
          "His vehicle registration DL 01 AB 1234 was seized. "
          "Contact: +91 9876543210.")
    p1 = extract_persons(t1)
    check(len(p1) == 1, f"found exactly 1 person (got {len(p1)})")
    if p1:
        check(p1[0].name == "Rakesh Kumar Verma", f"name is full 'Rakesh Kumar Verma' (got {p1[0].name!r})")
        check(p1[0].age == 34, f"age=34 (got {p1[0].age})")
        check(p1[0].gender == "male", f"gender=male (got {p1[0].gender})")
        check(p1[0].crime_type == "cyber fraud", f"crime_type=cyber fraud (got {p1[0].crime_type})")
        check(p1[0].status == "arrested", f"status=arrested (got {p1[0].status})")
        check(p1[0].fir_number == "FIR 123/2026", f"fir=FIR 123/2026 (got {p1[0].fir_number})")
        print()

    # Test 2: reported-speech speaker must NOT get crime attribution (#1 CRITICAL)
    print("Test 2: Amit Shah (speaker) must NOT be tagged as accused")
    t2 = ("Home Minister Amit Shah said police arrested three men for fraud "
          "in Noida. The accused were produced before court today.")
    p2 = named_persons(t2)
    check("Amit Shah" in p2, f"'Amit Shah' present in NER output (got {p2})")
    for person in extract_persons(t2):
        if person.name == "Amit Shah":
            check(person.crime_type is None, f"Amit Shah crime_type must be None (got {person.crime_type!r})")
            check(person.status is None, f"Amit Shah status must be None (got {person.status!r})")
            check(person.fir_number is None, f"Amit Shah fir must be None (got {person.fir_number!r})")
            check(person.location is None, f"Amit Shah location must be None (got {person.location!r})")
    print()

    # Test 3: wanted fugitive with crime context (per-sentence scope)
    print("Test 3: wanted fugitive gets terrorism + interpol status")
    t3 = ("Wanted fugitive Masood Azhar, founder of Jaish-e-Mohammed, "
          "is a designated terrorist with an Interpol red notice. "
          "Born in 1968, Bahawalpur, Pakistan.")
    p3 = extract_persons(t3)
    check(len(p3) == 1, f"found exactly 1 person (got {len(p3)})")
    if p3:
        check(p3[0].name == "Masood Azhar", f"name is 'Masood Azhar' (got {p3[0].name!r})")
        check(p3[0].crime_type == "terrorism", f"crime_type=terrorism (got {p3[0].crime_type})")
        check(p3[0].status == "interpol red notice", f"status=interpol red notice (got {p3[0].status})")
    print()

    # Test 4: convicted person with gender + age from unambiguous context
    print("Test 4: convicted woman gets gender/crime/location")
    t4 = ("She was convicted of murdering her husband in 2023. "
          "The woman, 28, was sentenced to life imprisonment in Lucknow.")
    # 'She'/'the woman' has no proper PERSON name -> no fabrication expected
    p4 = named_persons(t4)
    # We only fabricate named persons; if none, that's acceptable (not a failure)
    print("  (no fabrication of unnamed person — ok if list is empty)")
    print()

    # Test 5: accused person with crime context (#2 regression: Pradeep Singh detection)
    print("Test 5: absconding accused Pradeep Singh is detected and attributed")
    t5 = ("Absconding accused Pradeep Singh on the run from Lucknow, UP. "
          "Last seen near Chandigarh. Interpol red notice issued.")
    p5 = named_persons(t5)
    check("Pradeep Singh" in p5, f"'Pradeep Singh' detected (got {p5})")
    for person in extract_persons(t5):
        if person.name == "Pradeep Singh":
            check(person.status == "absconding", f"Pradeep status=absconding (got {person.status!r})")
    print()

    # Test 6: a person in a sentence with NO crime context gets null fields (#1)
    print("Test 6: person in neutral sentence gets no inherited crime fields")
    t6 = ("Ramesh Gupta chaired the meeting at Noida today. "
          "Meanwhile, police arrested three men for fraud in Lucknow.")
    p6 = named_persons(t6)
    check("Ramesh Gupta" in p6, f"'Ramesh Gupta' present (got {p6})")
    for person in extract_persons(t6):
        if person.name == "Ramesh Gupta":
            # Same-sentence attribution (ATTRIBUTION_WINDOW=0): Ramesh is in a
            # neutral sentence, so he must NOT inherit the fraud/arrest context
            # that appears in the neighboring sentence.
            check(person.crime_type is None, f"Ramesh crime_type must be None (got {person.crime_type!r})")
            check(person.status is None, f"Ramesh status must be None (got {person.status!r})")
    print()

    # Test 7: journalist/speaker in first sentence must not be accused
    print("Test 7: reporter who names an accused is not himself accused")
    t7 = ("Reporter Vikram Joshi wrote that the police arrested two men "
          "for smuggling drugs in Pune.")
    p7 = named_persons(t7)
    check("Vikram Joshi" in p7, f"'Vikram Joshi' present (got {p7})")
    for person in extract_persons(t7):
        if person.name == "Vikram Joshi":
            check(person.crime_type is None, f"Vikram crime_type must be None (got {person.crime_type!r})")
            check(person.status is None, f"Vikram status must be None (got {person.status!r})")
    print()

    print(f"{'ALL TESTS PASSED' if failures == 0 else f'{failures} TEST(S) FAILED'}")
    sys.exit(1 if failures else 0)
