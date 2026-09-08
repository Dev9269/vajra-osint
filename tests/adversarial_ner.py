"""Adversarial NER corpus for VAJRA entity extraction.

25 diverse Indic-name sentences that exercise the failure modes fixed in the
2026-09-06 round: full-name truncation, speaker attribution bleed, initials,
compound Indian names, and per-sentence crime attribution scope.

Usage: pytest (marker 'model') or direct:
    python adversarial_ner.py
"""

# (text, expected_person_name, optional_sanity_fields)
# expected_person_name is the full expected PERSON entity.
ADVERSARIAL_CORPUS = [
    ("Rakesh Kumar Verma, 34, from New Delhi was arrested for cyber fraud.",
     "Rakesh Kumar Verma"),
    ("Home Minister Amit Shah said police arrested three men in Noida.",
     "Amit Shah"),
    ("Wanted fugitive Masood Azhar is a designated terrorist.",
     "Masood Azhar"),
    ("Absconding accused Pradeep Singh was last seen near Chandigarh.",
     "Pradeep Singh"),
    ("Reporter Vikram Joshi wrote that police arrested two men for smuggling drugs.",
     "Vikram Joshi"),
    ("Convicted woman Rani Devi was sentenced to life in Lucknow.",
     "Rani Devi"),
    ("SM Abdul Aziz alias 'Babu' was held by the Karnataka police.",
     "SM Abdul Aziz"),
    ("A Karthik Subrahmanian, from Coimbatore, is booked for cheating.",
     "A Karthik Subrahmanian"),
    ("Vanaparthy Veeramma, an MLA accused in the chit fund scam, got bail.",
     "Vanaparthy Veeramma"),
    ("Chhagan Bhujbal, former minister, faces corruption charges in Mumbai.",
     "Chhagan Bhujbal"),
    ("Vishwanath Ramanna, a contractor from Bengaluru, was detained.",
     "Vishwanath Ramanna"),
    ("T Balaji Rao filed a complaint against unknown persons.",
     "T Balaji Rao"),
    ("Sd Mallikarjuna Rao, an engineer, was arrested in Hyderabad.",
     "Sd Mallikarjuna Rao"),
    ("M rigvedan, 41, a fisherman from Chennai, was rescued.",
     "M Rigvedan"),
    ("P Natarajan, a retired teacher, was conned by online fraudsters.",
     "P Natarajan"),
    ("Dr Ramaswamy Iyer, accused of medical negligence, surrendered.",
     "Ramaswamy Iyer"),
    ("Janakiraman Balasubramaniam, a builder, is wanted in a land case.",
     "Janakiraman Balasubramaniam"),
    ("P Dharmendra, 29, was arrested for drug possession in Kochi.",
     "P Dharmendra"),
    ("Syed Altaf Hussain, a businessman, was shot outside his office.",
     "Syed Altaf Hussain"),
    ("K Annamalai Sharma, a bank manager, defrauded depositors in Ahmedabad.",
     "K Annamalai Sharma"),
    ("Meera Harshvardhan, a model, accused an actor of harassment.",
     "Meera Harshvardhan"),
    ("Gopinath Ramesh, a software engineer, was kidnapped in Bengaluru.",
     "Gopinath Ramesh"),
    ("Suresh Kumar Yadav, 45, wanted in a POCSO case, was arrested.",
     "Suresh Kumar Yadav"),
    ("Premchand Mahato, from Dhanbad, was arrested for illegal mining.",
     "Premchand Mahato"),
    ("Aghora Buddh Daiya, 33, was arrested for human trafficking in Mumbai.",
     "Aghora Buddh Daiya"),
]


def _name_tokens_present(found: list[str], expected: str) -> bool:
    """True if some found person contains every token of expected (order-agnostic)."""
    exp = set(expected.lower().split())
    return any(exp <= set(f.lower().split()) for f in found)


def run_adversarial_ner() -> dict:
    """Run the corpus through entity_extraction.extract_persons.

    Returns {
        'tested': int, 'full_name_matches': int, 'failures': [ (text, expected, found) ]
    }
    NER model is required — import fails are surfaced as an error dict.
    """
    try:
        from entity_extraction import extract_persons
    except ImportError as e:
        return {"tested": 0, "full_name_matches": 0, "failures": [("import", str(e), [])]}

    full_matches = 0
    failures = []
    for text, expected in ADVERSARIAL_CORPUS:
        try:
            found = [p.name for p in extract_persons(text)]
        except Exception as e:  # model runtime issue from a weird sentence
            failures.append((text, expected, [f"ERROR: {e}"]))
            continue
        if any(f == expected for f in found) or _name_tokens_present(found, expected):
            full_matches += 1
        else:
            failures.append((text, expected, found))
    return {"tested": len(ADVERSARIAL_CORPUS), "full_name_matches": full_matches, "failures": failures}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    result = run_adversarial_ner()
    print(f"Adversarial NER: {result['full_name_matches']}/{result['tested']} full-name matches")
    for text, expected, found in result["failures"]:
        print(f"  FAIL: {text!r}\n        expected={expected!r} got={found!r}")
    sys.exit(0 if result["full_name_matches"] == result["tested"] else 1)