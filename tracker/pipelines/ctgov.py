"""ClinicalTrials.gov v2 catch-all for phase 2, 2/3, and 3 radiotherapy trials.

Phase coded as not applicable is kept when the title, brief summary, design text,
or the opening of the detailed description describes the study as phase 2 or 3.
Estimated enrollment is a target. Actual enrollment is a sponsor-posted count, not a live snapshot.
"""

import json
import re

from ..classify import classify_record, phase_language
from ..http_client import fetch_json, with_query
from ..model import CTGOV_STATUS, add_unique, blank_trial
from ..textutil import clip, today_iso, utc_now

API = "https://clinicaltrials.gov/api/v2/studies"
FIELDS = ",".join([
    "protocolSection.identificationModule",
    "protocolSection.statusModule",
    "protocolSection.sponsorCollaboratorsModule",
    "protocolSection.descriptionModule",
    "protocolSection.conditionsModule",
    "protocolSection.designModule",
    "protocolSection.armsInterventionsModule",
    "protocolSection.outcomesModule",
    "protocolSection.contactsLocationsModule",
])

QUERIES = [
    (
        "phase2-or-3",
        {
            "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND AREA[InterventionType]RADIATION AND (AREA[Phase]PHASE2 OR AREA[Phase]PHASE3)",
        },
    ),
    (
        "na-phase-language",
        {
            "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND AREA[InterventionType]RADIATION AND AREA[Phase]NA",
            "query.term": '"phase III" OR "phase II" OR "phase 3" OR "phase 2" OR "phase II/III" OR "phase 2/3"',
        },
    ),
    (
        "na-randomized",
        {
            "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND AREA[InterventionType]RADIATION AND AREA[Phase]NA AND AREA[DesignAllocation]RANDOMIZED",
        },
    ),
]


def _pages(name, params, cache_dir, refresh):
    cache = cache_dir / f"{name}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    studies = []
    token = None
    while True:
        query = dict(params)
        query["pageSize"] = 200
        query["fields"] = FIELDS
        if token:
            query["pageToken"] = token
        payload = fetch_json(with_query(API, query), timeout=120)
        studies.extend(payload.get("studies") or [])
        token = payload.get("nextPageToken")
        if not token:
            break
    cache.write_text(json.dumps(studies))
    return studies


def _phase_code(phases):
    labels = []
    for phase in phases or []:
        labels.append({
            "PHASE1": "1", "PHASE2": "2", "PHASE3": "3", "PHASE4": "4", "NA": "NA",
        }.get(phase, phase))
    if "2" in labels and "3" in labels:
        return "2/3"
    if "1" in labels and "2" in labels and "3" not in labels:
        return "1/2"
    if "3" in labels:
        return "3"
    if "2" in labels:
        return "2"
    if "1" in labels:
        return "1"
    if "4" in labels:
        return "4"
    if "NA" in labels or not labels:
        return "NA"
    return labels[0]


def _countries(locations):
    listed, open_countries = [], []
    for loc in locations or []:
        country = loc.get("country")
        if not country:
            continue
        add_unique(listed, country)
        if loc.get("status") in {"RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"}:
            add_unique(open_countries, country)
    return listed, open_countries


_SKIP_ACRONYM = {"NSCLC", "SCLC", "LCT", "RT", "SABR", "SBRT", "SRS", "IMRT", "VMAT", "OS", "PFS", "QOL"}
_GRANT = re.compile(r"^(?:U10|U19|U24|P01|P30|P50|R01|R03|R21|R56|K12|NCI-|CDR\d|5U10|5P30|5R01|5U19)", re.I)


def _display_name(ident, official, brief):
    acronym = (ident.get("acronym") or "").strip()
    if acronym:
        return acronym
    for text in (official, brief):
        found = re.findall(r"\(([A-Z][A-Z0-9][A-Z0-9+\-]{1,28})\)", text or "")
        for token in reversed(found):
            if token not in _SKIP_ACRONYM:
                return token
    org = (ident.get("orgStudyIdInfo") or {}).get("id")
    return org or ident.get("nctId")


def _keep_protocol(value):
    if not value or _GRANT.match(str(value).strip()):
        return False
    return True


def _opening_detail(text):
    """First paragraph of the detailed description, capped so a later phase mention does not reclassify the trial."""
    paragraph = (text or "").strip().split("\n\n", 1)[0]
    return paragraph[:800]


_GENERIC_MEASURE = re.compile(
    r"(?:primary|secondary)\s+(?:outcome\s+measure|endpoint|objective)(?:\s+\d+)?",
    re.I,
)


def _endpoint_text(outcome):
    """Use the description when the registry measure is only a placeholder label."""
    measure = (outcome.get("measure") or "").strip()
    if measure and not _GENERIC_MEASURE.fullmatch(measure):
        return measure
    description = (outcome.get("description") or "").strip()
    sentence = re.split(r"(?<=[.])\s+", description, maxsplit=1)[0].strip()
    return sentence or measure


def parse_study(study):
    ps = study.get("protocolSection") or study
    ident = ps.get("identificationModule") or {}
    status = ps.get("statusModule") or {}
    design = ps.get("designModule") or {}
    arms_mod = ps.get("armsInterventionsModule") or {}
    outcomes = ps.get("outcomesModule") or {}
    sponsor_mod = ps.get("sponsorCollaboratorsModule") or {}
    description = ps.get("descriptionModule") or {}
    conditions = (ps.get("conditionsModule") or {}).get("conditions") or []
    info = design.get("designInfo") or {}
    official = ident.get("officialTitle") or ""
    brief = ident.get("briefTitle") or ""
    summary = description.get("briefSummary") or ""
    design_text = " ".join(filter(None, [
        info.get("interventionModelDescription"),
        info.get("designAllocationDescription") if isinstance(info.get("designAllocationDescription"), str) else None,
    ]))
    phase_code = _phase_code(design.get("phases"))
    spoken = phase_language(" ".join([
        official, brief, summary, design_text,
        _opening_detail(description.get("detailedDescription")),
    ]))
    role, evidence, modality = classify_record(
        [brief, official, ident.get("acronym")],
        arms_mod.get("interventions") or [],
        arms_mod.get("armGroups") or [],
        info.get("allocation"),
        design_text,
    )
    listed, open_countries = _countries((ps.get("contactsLocationsModule") or {}).get("locations"))
    endpoints = []
    for outcome in outcomes.get("primaryOutcomes") or []:
        measure = _endpoint_text(outcome)
        if measure:
            add_unique(endpoints, measure)
    enrollment = design.get("enrollmentInfo") or {}
    count = enrollment.get("count")
    enrollment_type = enrollment.get("type")
    updated = (status.get("lastUpdatePostDateStruct") or {}).get("date")
    nct = ident.get("nctId")
    protocols = []
    org = (ident.get("orgStudyIdInfo") or {}).get("id")
    if org and _keep_protocol(org):
        protocols.append(org)
    for secondary in ident.get("secondaryIdInfos") or []:
        sid = secondary.get("id")
        if sid and _keep_protocol(sid):
            protocols.append(sid)
    lead = (sponsor_mod.get("leadSponsor") or {}).get("name")
    collaborators = [c.get("name") for c in sponsor_mod.get("collaborators") or [] if c.get("name")]
    record = blank_trial()
    record["name"] = _display_name(ident, official, brief)
    record["title"] = official or brief
    record["nct"] = nct
    record["protocols"] = protocols
    record["sponsor"] = lead
    record["lead_group"] = lead
    record["groups"] = [lead] if lead else []
    record["collaborators"] = collaborators
    record["phase"] = phase_code
    record["phase_code"] = phase_code
    record["phase_in_title"] = spoken
    record["status"] = CTGOV_STATUS.get(status.get("overallStatus"), "unknown")
    record["status_source"] = "ClinicalTrials.gov"
    record["status_checked"] = today_iso()
    record["registry"] = True
    record["population"] = clip(summary, 500)
    record["conditions"] = conditions[:8]
    record["primary_endpoints"] = endpoints
    record["rt_role"] = role
    record["rt_evidence"] = evidence
    record["modality"] = modality
    record["countries_listed"] = listed
    record["countries_open"] = open_countries
    record["start_date"] = (status.get("startDateStruct") or {}).get("date")
    record["checked_at"] = utc_now()
    record["urls"]["ctgov"] = f"https://clinicaltrials.gov/study/{nct}" if nct else None
    record["sources"] = [{
        "pipeline": "ctgov",
        "url": record["urls"].get("ctgov"),
        "checked": today_iso(),
        "registry_updated": updated,
    }]
    notes = []
    if updated:
        notes.append(f"ClinicalTrials.gov last update posted {updated}.")
    if enrollment_type == "ACTUAL" and count is not None:
        quality, actual, target = "sponsor-posted", count, None
        notes.append("This is the sponsor-posted actual enrollment, not a daily accrual feed and not a patients-per-month denominator.")
    elif enrollment_type == "ESTIMATED" and count is not None:
        quality, actual, target = "target-only", None, count
        notes.append("ClinicalTrials.gov marks this enrollment as Estimated. It is a target, not an accrued headcount.")
    else:
        quality, actual, target = "status-only", None, None
    if phase_code == "NA" and spoken in {"2", "3", "2/3"}:
        record["disagreements"].append({
            "field": "phase",
            "registry_value": "Not applicable",
            "other_value": f"The same ClinicalTrials.gov record describes the study as phase {spoken} in the record text.",
            "other_source": "ClinicalTrials.gov record text",
        })
    record["accrual"] = {
        "quality": quality,
        "actual": actual,
        "target": target,
        "enrollment_type": enrollment_type,
        "as_of": updated or today_iso(),
        "source": "ClinicalTrials.gov",
        "source_url": record["urls"].get("ctgov"),
        "reported": None,
        "rate_per_month": None,
        "notes": notes,
    }
    record["_keep_phase"] = phase_code in {"2", "2/3", "3"} or (phase_code == "NA" and spoken in {"2", "3", "2/3"})
    return record


def _fetch_ids(ncts):
    studies = []
    for start in range(0, len(ncts), 50):
        batch = ncts[start:start + 50]
        token = None
        while True:
            params = {"filter.ids": ",".join(batch), "pageSize": 50, "fields": FIELDS}
            if token:
                params["pageToken"] = token
            payload = fetch_json(with_query(API, params), timeout=120)
            studies.extend(payload.get("studies") or [])
            token = payload.get("nextPageToken")
            if not token:
                break
    return studies


def load(store, log, cache_dir, refresh=False):
    cache_dir.mkdir(parents=True, exist_ok=True)
    seen = set()
    added = 0
    enriched = 0
    for name, params in QUERIES:
        studies = _pages(name, params, cache_dir, refresh)
        for study in studies:
            record = parse_study(study)
            nct = record.get("nct")
            if nct and nct in seen and name != "phase2-or-3":
                # Still allow the first query to create the row. Later queries only fill gaps.
                if store.find(record):
                    seen.add(nct)
                    continue
            if nct:
                seen.add(nct)
            if not record.get("rt_role") or not record.get("_keep_phase"):
                existing = store.find(record)
                if existing and nct:
                    if "ctsu" in (existing.get("pipelines") or []):
                        record["prefer_existing_status"] = True
                    store.upsert(record, "ctgov")
                    enriched += 1
                continue
            existing = store.find(record)
            if existing and "ctsu" in (existing.get("pipelines") or []):
                record["prefer_existing_status"] = True
            _existed = existing is not None
            store.upsert(record, "ctgov")
            if _existed:
                enriched += 1
            else:
                added += 1
    missing = [trial["nct"] for trial in store.trials if trial.get("nct") and trial["nct"] not in seen]
    if missing:
        for study in _fetch_ids(missing):
            record = parse_study(study)
            if not store.find(record):
                continue
            existing = store.find(record)
            if existing and "ctsu" in (existing.get("pipelines") or []):
                record["prefer_existing_status"] = True
            store.upsert(record, "ctgov")
            enriched += 1
            if record.get("nct"):
                seen.add(record["nct"])
    log.append({"pipeline": "ctgov", "added": added, "enriched": enriched, "nct_seen": len(seen)})
    return added
