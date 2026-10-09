"""Trial record helpers, identifiers, and accrual rules."""

import re
from datetime import date, datetime

DAYS_PER_MONTH = 30.4375

STATUS_LABELS = {
    "not yet recruiting": "Not yet recruiting",
    "recruiting": "Recruiting",
    "enrolling by invitation": "Enrolling by invitation",
    "closed to accrual": "Closed to accrual",
    "completed": "Completed",
    "suspended": "Suspended",
    "terminated": "Terminated",
    "withdrawn": "Withdrawn",
    "unknown": "Unknown",
}

CTGOV_STATUS = {
    "NOT_YET_RECRUITING": "not yet recruiting",
    "RECRUITING": "recruiting",
    "ENROLLING_BY_INVITATION": "enrolling by invitation",
    "ACTIVE_NOT_RECRUITING": "closed to accrual",
    "COMPLETED": "completed",
    "SUSPENDED": "suspended",
    "TERMINATED": "terminated",
    "WITHDRAWN": "withdrawn",
}

QUALITY_LABELS = {
    "snapshot": "Snapshot",
    "target-only": "Target only",
    "reported-once": "Reported once",
    "status-only": "Status only",
    "sponsor-posted": "Sponsor-posted actual",
}

GROUP_PATTERNS = [
    ("JCOG", re.compile(r"\bJCOG\b|Japan Clinical Oncology Group", re.I)),
    ("GORTEC", re.compile(r"\bGORTEC\b", re.I)),
    ("UNICANCER", re.compile(r"\bUNICANCER\b", re.I)),
    ("DAHANCA", re.compile(r"\bDAHANCA\b", re.I)),
    ("DBCG", re.compile(r"\bDBCG\b|Danish Breast Cancer Group", re.I)),
    ("SAKK", re.compile(r"\bSAKK\b", re.I)),
    ("Cancer Trials Ireland", re.compile(r"Cancer Trials Ireland|\bCTRIAL-IE\b|\bICORG\b", re.I)),
    ("EORTC", re.compile(r"\bEORTC\b", re.I)),
    ("ESTRO", re.compile(r"\bESTRO\b", re.I)),
    ("TROG", re.compile(r"\bTROG\b|Trans Tasman Radiation Oncology Group", re.I)),
    ("TOGA", re.compile(r"\bTOGA\b|Thoracic Oncology Group of Australasia", re.I)),
    ("CCTG", re.compile(r"\bCCTG\b|Canadian Cancer Trials Group", re.I)),
    ("NRG", re.compile(r"\bNRG\b", re.I)),
    ("Alliance", re.compile(r"Alliance for Clinical Trials")),
    ("ECOG-ACRIN", re.compile(r"ECOG-ACRIN|\bECOG\b|\bACRIN\b", re.I)),
    ("SWOG", re.compile(r"\bSWOG\b", re.I)),
    ("KROG", re.compile(r"\bKROG\b|Korean Radiation Oncology Group", re.I)),
    ("IAEA", re.compile(r"\bIAEA\b", re.I)),
    ("Tata Memorial", re.compile(r"Tata Memorial", re.I)),
    ("National Cancer Grid", re.compile(r"National Cancer Grid", re.I)),
    ("ICR-CTSU", re.compile(r"ICR-CTSU|Institute of Cancer Research Clinical Trials", re.I)),
    ("MRC CTU at UCL", re.compile(r"MRC CTU|MRC Clinical Trials Unit", re.I)),
    ("UCL", re.compile(r"\bUCL\b|University College London|University College, London", re.I)),
    ("Leeds", re.compile(r"University of Leeds", re.I)),
    ("Cardiff", re.compile(r"Cardiff University|Velindre", re.I)),
    ("Southampton Clinical Trials Unit", re.compile(r"Southampton Clinical Trials Unit|\bSCTU\b", re.I)),
    ("Christie", re.compile(r"The Christie", re.I)),
    ("BC Cancer", re.compile(r"BC Cancer|British Columbia Cancer", re.I)),
    ("MD Anderson", re.compile(r"MD Anderson|M\.D\. Anderson", re.I)),
    ("London Health Sciences Centre", re.compile(r"London Health Sciences Centre", re.I)),
]


def blank_trial():
    return {
        "id": None,
        "name": None,
        "title": None,
        "nct": None,
        "isrctn": None,
        "eudract": None,
        "protocols": [],
        "groups": [],
        "lead_group": None,
        "phase": None,
        "phase_code": None,
        "phase_in_title": None,
        "status": None,
        "status_source": None,
        "status_checked": None,
        "registry_status": None,
        "registry_checked": None,
        "population": None,
        "conditions": [],
        "primary_endpoints": [],
        "rt_role": None,
        "rt_evidence": [],
        "modality": None,
        "record_kind": "trial",
        "platform": None,
        "study_type": "interventional",
        "accrual": {
            "quality": "status-only",
            "actual": None,
            "target": None,
            "enrollment_type": None,
            "as_of": None,
            "source": None,
            "source_url": None,
            "reported": None,
            "rate_per_month": None,
            "notes": [],
        },
        "countries_open": [],
        "countries_listed": [],
        "country_limit": None,
        "source_pipeline": None,
        "pipelines": [],
        "sources": [],
        "disagreements": [],
        "urls": {},
        "sponsor": None,
        "collaborators": [],
        "start_date": None,
        "checked_at": None,
    }


def norm_nct(value):
    if not value:
        return None
    match = re.search(r"NCT\d{8}", str(value).upper())
    return match.group(0) if match else None


def norm_isrctn(value):
    if not value:
        return None
    match = re.search(r"(\d{8})", str(value))
    if re.search(r"ISRCTN", str(value), re.I) or (match and len(str(value)) < 20):
        return f"ISRCTN{match.group(1)}" if match else None
    return None


def protocol_keys(value):
    """Group protocol numbers only. Grant numbers and free-text compact keys are not identifiers."""
    if not value:
        return set()
    raw = str(value).upper().replace("–", "-").replace("—", "-")
    keys = set()
    for match in re.finditer(r"\bTROG\s*(\d{2})\.(\d{2})\b", raw):
        keys.add(f"TROG-{match.group(1)}.{match.group(2)}")
    for match in re.finditer(r"\bKROG\s*(\d{2})\s*[- ]\s*(\d{2})\b", raw):
        keys.add(f"KROG-{match.group(1)}-{match.group(2)}")
    for match in re.finditer(r"\b(?:EORTC|RP)[-\s]*(\d{3,5})\b", raw):
        keys.add("EORTC-" + match.group(1))
    for match in re.finditer(r"\bCCTG[-\s]*([A-Z]{2})\.?(\d{1,3})\b", raw):
        keys.add(f"CCTG-{match.group(1)}{match.group(2)}")
    for match in re.finditer(r"\bNRG[-\s]*([A-Z]{1,4})[-\s]*(\d{2,4})\b", raw):
        keys.add(f"NRG-{match.group(1)}{match.group(2)}")
    for match in re.finditer(r"\bRTOG[-\s]*(\d{2,4})(?:[-\s.](\d{2}))?\b", raw):
        keys.add("RTOG-" + match.group(1) + (("-" + match.group(2)) if match.group(2) else ""))
    for match in re.finditer(r"\b(JCOG|DAHANCA|DBCG|NCIC|NCCTG)[-\s]*(\d{2,4})\b", raw):
        keys.add(f"{match.group(1)}-{match.group(2)}")
    for match in re.finditer(r"\bSAKK[-\s]*(\d{2})[/\-](\d{2})\b", raw):
        keys.add(f"SAKK-{match.group(1)}-{match.group(2)}")
    for match in re.finditer(r"\bGORTEC[-\s]*(\d{4})[-\s](\d{2})\b", raw):
        keys.add(f"GORTEC-{match.group(1)}-{match.group(2)}")
    for match in re.finditer(r"\bGORTEC[-\s]*(\d{2,3})\b(?![-\s]\d)", raw):
        keys.add(f"GORTEC-{match.group(1)}")
    for match in re.finditer(r"\bSWOG[-\s]*([A-Z]?\d{3,4})\b", raw):
        keys.add("SWOG-" + match.group(1))
    for match in re.finditer(r"\bALLIANCE[-\s]*([A-Z]?\d{4,7})\b", raw):
        keys.add("ALLIANCE-" + match.group(1))
    for match in re.finditer(r"\bECOG(?:-ACRIN)?[-\s]*([A-Z]{0,3}\d{3,6})\b", raw):
        keys.add("ECOG-" + match.group(1))
    # CCTG public ids look like BR38 or HN.11. Do not apply this to grant numbers.
    if len(raw) <= 48 and not re.match(r"(?:NRG|RTOG|EORTC|RP|TROG|KROG|SWOG|JCOG|GORTEC|DAHANCA|DBCG|SAKK|ALLIANCE|ECOG|NCIC|NCCTG|CCTG)", raw):
        start = re.match(r"([A-Z]{2,4})(\d{1,3})(?:\s*\(.*\))?$", raw.strip())
        if start:
            keys.add(f"CCTG-{start.group(1)}{start.group(2)}")
        for match in re.finditer(r"\(([A-Z]{2})\.(\d{1,3})\)", raw):
            keys.add(f"CCTG-{match.group(1)}{match.group(2)}")
    return keys


def add_unique(items, value):
    if value is None:
        return
    text = str(value).strip()
    if text and text not in items:
        items.append(text)


def accrual_quality_rank(quality):
    return {
        "snapshot": 5,
        "sponsor-posted": 4,
        "reported-once": 3,
        "target-only": 2,
        "status-only": 1,
    }.get(quality, 0)


def apply_accrual(trial, quality, actual=None, target=None, as_of=None, source=None,
                  source_url=None, enrollment_type=None, note=None, reported=None):
    """Keep the more informative accrual record. A snapshot is never replaced by an estimate."""
    slot = trial["accrual"]
    if note:
        add_unique(slot["notes"], note)
    if reported and reported.get("n") is not None:
        slot["reported"] = reported
        if quality == "reported-once" and accrual_quality_rank(slot["quality"]) < accrual_quality_rank("target-only"):
            slot["quality"] = "reported-once"
    incoming = accrual_quality_rank(quality)
    current = accrual_quality_rank(slot["quality"])
    if quality == "reported-once":
        return
    if incoming < current:
        if target is not None and slot["target"] is None:
            slot["target"] = target
        return
    if incoming == current == accrual_quality_rank("snapshot") and slot["actual"] is not None:
        return
    slot["quality"] = quality
    if actual is not None:
        slot["actual"] = actual
    if target is not None:
        slot["target"] = target
    if as_of:
        slot["as_of"] = as_of
    if source:
        slot["source"] = source
    if source_url:
        slot["source_url"] = source_url
    if enrollment_type:
        slot["enrollment_type"] = enrollment_type


def set_snapshot_rate(trial, today=None):
    slot = trial["accrual"]
    slot["rate_per_month"] = None
    if slot["quality"] != "snapshot":
        return
    if slot["actual"] is None or not trial.get("start_date"):
        return
    today = today or date.today()
    try:
        start = datetime.strptime(trial["start_date"][:10], "%Y-%m-%d").date()
    except ValueError:
        return
    months = (today - start).days / DAYS_PER_MONTH
    if months >= 0.5:
        slot["rate_per_month"] = round(slot["actual"] / months, 2)


def tag_groups(trial):
    blob = " ".join(filter(None, [
        trial.get("title"), trial.get("name"), trial.get("sponsor"),
        " ".join(trial.get("collaborators") or []),
        " ".join(trial.get("protocols") or []),
        " ".join(trial.get("groups") or []),
    ]))
    for name, pattern in GROUP_PATTERNS:
        if pattern.search(blob):
            add_unique(trial["groups"], name)
    if trial.get("lead_group"):
        add_unique(trial["groups"], trial["lead_group"])


def country_limit(trial):
    parts = []
    groups = " ".join(trial.get("groups") or [])
    blob = groups + " " + (trial.get("title") or "") + " " + " ".join(trial.get("protocols") or [])
    if re.search(r"\bJCOG\b", blob):
        parts.append("Enrollment is limited to Japan. JCOG trials do not enroll patients outside Japan.")
    if re.search(r"\bKROG\b", blob) or "krog" in (trial.get("pipelines") or []):
        parts.append("KROG enrollment is in Korea.")
    if trial.get("countries_open"):
        parts.append("Enrollment is open in: " + ", ".join(trial["countries_open"]) + ".")
    elif trial.get("countries_listed"):
        parts.append("Sites are listed in: " + ", ".join(trial["countries_listed"]) + ".")
    trial["country_limit"] = " ".join(parts) or None


def choose_id(trial):
    if trial.get("nct"):
        return trial["nct"]
    if trial.get("isrctn"):
        return trial["isrctn"]
    if trial.get("protocols"):
        return "protocol:" + trial["protocols"][0]
    return "title:" + re.sub(r"\s+", " ", (trial.get("name") or trial.get("title") or "unknown"))[:80]


def disagreement(trial, field, registry_value, other_value, other_source):
    if registry_value == other_value or other_value in (None, ""):
        return
    item = {
        "field": field,
        "registry_value": registry_value,
        "other_value": other_value,
        "other_source": other_source,
    }
    if item not in trial["disagreements"]:
        trial["disagreements"].append(item)
