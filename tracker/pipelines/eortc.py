"""EORTC studies and E2-RADIatE cohorts from the public Prisma API."""

import json
from concurrent.futures import ThreadPoolExecutor

from ..classify import classify_text, phase_language
from ..http_client import fetch_json, with_query
from ..model import blank_trial
from ..textutil import clip, today_iso, utc_now

API = "https://api.eortc.be/prisma"
KEEP_PHASES = {"2", "2/3", "3", "cohort"}
ALWAYS = {"1811", "1822", "2011", "2387"}


def _odata(path, params, cache_dir, name):
    cache = cache_dir / name
    if cache.exists():
        return json.loads(cache.read_text())
    rows = []
    skip = 0
    while True:
        query = dict(params)
        query["$top"] = 200
        query["$skip"] = skip
        payload = fetch_json(with_query(API + path, query), timeout=90)
        batch = payload.get("value") or []
        rows.extend(batch)
        if len(batch) < 200:
            break
        skip += 200
    cache.write_text(json.dumps(rows))
    return rows


def _phase(raw):
    text = (raw or "").strip().lower()
    if "within cohort" in text or text in {"cohort", "cohorts"}:
        return "cohort"
    spoken = phase_language(raw or "")
    return spoken


def _status(raw):
    text = (raw or "").lower()
    if "recruit" in text:
        return "recruiting"
    if "to be opened" in text or "not yet" in text:
        return "not yet recruiting"
    if "ongoing" in text or text == "open trials":
        return "recruiting"
    if "all trial activities ended" in text or "completed" in text:
        return "completed"
    if "closed" in text or "ended" in text:
        return "closed to accrual"
    if "suspend" in text:
        return "suspended"
    return "unknown"


def _get_json(url):
    try:
        return fetch_json(url, timeout=40).get("value") or []
    except Exception:
        return []


def _treatments(study_id):
    return [row.get("Name") for row in _get_json(f"{API}/Treatments?studyId={study_id}") if row.get("Name")]


def _countries(study_id, research=False):
    url = f"{API}/ParticipatingServiceRP?rpId={study_id}" if research else f"{API}/ParticipatingServices?studyId={study_id}"
    rows = _get_json(url)
    countries = []
    for row in rows:
        country = row.get("Country")
        if country and country not in countries:
            countries.append(country)
    return countries


def _groups(study_id):
    names = []
    for row in _get_json(f"{API}/Groups?studyId={study_id}"):
        if row.get("Name") and row["Name"] not in names:
            names.append(row["Name"])
    return names


def _record_from_study(study, treatments, countries, groups, linked_platform=None):
    title = " ".join((study.get("Title") or "").split())
    phase = _phase(study.get("Phase"))
    role, evidence, modality = classify_text(
        title,
        treatments=treatments,
        randomized="random" in (study.get("Randomized") or "").lower() or "random" in (study.get("Phase") or "").lower(),
    )
    if role is None and any("radiotherapy" in (t or "").lower() or "brachy" in (t or "").lower() for t in treatments):
        role, evidence, modality = classify_text(title + " radiotherapy", treatments=treatments, randomized=True)
    number = str(study.get("Number") or "")
    if role is None and number not in ALWAYS and not linked_platform:
        return None
    if phase not in KEEP_PHASES and number not in ALWAYS and not linked_platform:
        if not (phase is None and role):
            return None
    if role is None and number in ALWAYS:
        role, evidence, modality = "RT-focused", ["EORTC radiotherapy infrastructure or cohort"], "external beam"
    target = study.get("TargetedGlobalAccrual") or study.get("TargetedEortcAccrual")
    trial = blank_trial()
    trial["name"] = study.get("Alias") or study.get("ExternalId")
    trial["title"] = title
    trial["nct"] = study.get("NCTNumber")
    trial["eudract"] = study.get("EudraCTNumber")
    trial["protocols"] = [p for p in [study.get("ExternalId"), f"EORTC {number}" if number else None] if p]
    trial["groups"] = ["EORTC"] + groups
    trial["lead_group"] = "EORTC"
    trial["phase"] = phase
    trial["phase_code"] = phase
    trial["status"] = _status(study.get("Status"))
    trial["status_source"] = "EORTC"
    trial["status_checked"] = today_iso()
    trial["population"] = title
    trial["primary_endpoints"] = []
    trial["rt_role"] = role
    trial["rt_evidence"] = evidence
    trial["modality"] = modality or "external beam"
    trial["record_kind"] = "platform" if number == "1811" else "trial"
    trial["platform"] = linked_platform or ("E2-RADIatE" if number in {"1811", "2387"} else None)
    trial["study_type"] = "interventional"
    trial["countries_listed"] = countries
    if trial["status"] in {"recruiting", "not yet recruiting"}:
        trial["countries_open"] = list(countries)
    quality = "target-only" if target else "status-only"
    trial["accrual"] = {
        "quality": quality,
        "actual": None,
        "target": target,
        "enrollment_type": None,
        "as_of": today_iso(),
        "source": "EORTC trial record",
        "source_url": f"https://www.eortc.org/research_field/clinical-detail/{number}/",
        "reported": None,
        "rate_per_month": None,
        "notes": ["EORTC publishes a target sample size and does not publish a running enrolled count."],
    }
    trial["urls"]["eortc"] = f"https://www.eortc.org/research_field/clinical-detail/{number}/"
    if trial["nct"]:
        trial["urls"]["ctgov"] = f"https://clinicaltrials.gov/study/{trial['nct']}"
    trial["sources"] = [{"pipeline": "eortc", "url": trial["urls"]["eortc"], "checked": today_iso()}]
    trial["checked_at"] = utc_now()
    trial["sponsor"] = "EORTC"
    return trial


def _record_from_project(project, countries, child_names):
    title = " ".join((project.get("Title") or "").split())
    number = str(project.get("Number") or "")
    role, evidence, modality = classify_text(title, treatments=["Radiotherapy"])
    if role is None and number not in ALWAYS:
        return None
    trial = blank_trial()
    trial["name"] = project.get("Alias") or project.get("ExternalId")
    trial["title"] = title
    trial["protocols"] = [p for p in [project.get("ExternalId"), f"EORTC {number}"] if p]
    trial["groups"] = ["EORTC", "ESTRO"]
    trial["lead_group"] = "EORTC"
    trial["phase"] = "cohort"
    trial["phase_code"] = "cohort"
    trial["status"] = _status(project.get("Status"))
    trial["status_source"] = "EORTC"
    trial["status_checked"] = today_iso()
    trial["population"] = title
    trial["rt_role"] = role or "RT-focused"
    trial["rt_evidence"] = evidence or ["EORTC radiotherapy cohort"]
    trial["modality"] = modality or "external beam"
    trial["record_kind"] = "cohort"
    trial["platform"] = "E2-RADIatE"
    trial["study_type"] = "observational"
    trial["countries_listed"] = countries
    if trial["status"] == "recruiting":
        trial["countries_open"] = list(countries)
    note = "EORTC research-project record has status and no public enrolled numerator."
    if child_names:
        note += " Linked trials: " + ", ".join(child_names) + "."
    trial["accrual"] = {
        "quality": "status-only",
        "actual": None,
        "target": None,
        "enrollment_type": None,
        "as_of": today_iso(),
        "source": "EORTC research project",
        "source_url": f"https://www.eortc.org/?research_project_id={number}",
        "reported": None,
        "rate_per_month": None,
        "notes": [note],
    }
    trial["urls"]["eortc"] = f"https://www.eortc.org/?research_project_id={number}"
    trial["sources"] = [{"pipeline": "eortc", "url": trial["urls"]["eortc"], "checked": today_iso()}]
    trial["checked_at"] = utc_now()
    trial["sponsor"] = "EORTC"
    return trial


def load(store, log, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    studies = _odata("/Studies", {}, cache_dir, "studies.json")
    projects = _odata("/ResearchProjects", {}, cache_dir, "research_projects.json")
    linked = {}
    for project in projects:
        if str(project.get("Number")) in {"1822", "2011"}:
            children = _get_json(f"{API}/Studies?linkedRpId={project.get('Id')}")
            linked[project.get("Id")] = children
            cache = cache_dir / f"linked-{project.get('Number')}.json"
            cache.write_text(json.dumps(children))

    linked_ids = {row.get("Id") for rows in linked.values() for row in rows}
    candidates = []
    for study in studies:
        phase = _phase(study.get("Phase"))
        title = study.get("Title") or ""
        number = str(study.get("Number") or "")
        rt_title = any(word in title.lower() for word in ("radiotherapy", "radiation", "sbrt", "sabr", "brachy", "irradiation"))
        if number in ALWAYS or study.get("Id") in linked_ids or rt_title or phase in KEEP_PHASES:
            candidates.append(study)

    treatment_cache = cache_dir / "treatments.json"
    treatments = json.loads(treatment_cache.read_text()) if treatment_cache.exists() else {}

    def ensure_treatment(study):
        key = str(study.get("Id"))
        if key not in treatments:
            treatments[key] = _treatments(study.get("Id"))
        return study, treatments[key]

    with ThreadPoolExecutor(max_workers=8) as pool:
        enriched = list(pool.map(ensure_treatment, candidates))
    treatment_cache.write_text(json.dumps(treatments))

    kept = []
    for study, treats in enriched:
        number = str(study.get("Number") or "")
        platform = None
        if number == "1811":
            platform = "E2-RADIatE"
        elif number == "2387":
            platform = "E2-RADIatE / OligoCare"
        else:
            for project in projects:
                if str(project.get("Number")) == "1822" and study.get("Id") in {row.get("Id") for row in linked.get(project.get("Id"), [])}:
                    platform = "E2-RADIatE / OligoCare"
                if str(project.get("Number")) == "2011" and study.get("Id") in {row.get("Id") for row in linked.get(project.get("Id"), [])}:
                    platform = "E2-RADIatE / ReCare"
        record = _record_from_study(study, treats, [], [], platform)
        if record:
            kept.append((study, record))

    def add_places(item):
        study, record = item
        record["countries_listed"] = _countries(study.get("Id"))
        if record["status"] in {"recruiting", "not yet recruiting"}:
            record["countries_open"] = list(record["countries_listed"])
        extra_groups = [name for name in _groups(study.get("Id")) if name not in record["groups"]]
        record["groups"].extend(extra_groups)
        return record

    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(add_places, kept))
    for record in records:
        store.upsert(record, "eortc")

    for project in projects:
        title = project.get("Title") or ""
        number = str(project.get("Number") or "")
        if number not in ALWAYS and "radiotherapy" not in title.lower() and "re-irradiation" not in title.lower() and "irradiation" not in title.lower():
            continue
        if project.get("Discriminator") == "Study":
            continue
        children = linked.get(project.get("Id"), [])
        child_names = [row.get("Alias") or row.get("ExternalId") for row in children]
        countries = _countries(project.get("Id"), research=True)
        record = _record_from_project(project, countries, child_names)
        if record:
            store.upsert(record, "eortc")

    log.append({"pipeline": "eortc", "studies_read": len(studies), "kept_studies": len(records), "research_projects": len(projects)})
    return len(records)
