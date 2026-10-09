"""NIHR radiotherapy trials from the UK RTTQA portfolio, resolved in ISRCTN."""

import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from ..classify import classify_text, phase_language
from ..http_client import fetch, fetch_json, with_query
from ..model import blank_trial, norm_isrctn, norm_nct
from ..textutil import clip, html_to_text, today_iso, utc_now

PAGES = "https://rttrialsqa.org.uk/index.php?rest_route=/wp/v2/pages&per_page=100"
ISRCTN = "https://www.isrctn.com/api/query/format/default"
SKIP = {
    "trials", "front-page", "about-rttqa", "data-upload", "audit",
    "trial-complexity", "__trashed", "the-sabr-expansion-programme-sep",
}


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(node):
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def _find(node, name):
    for child in node.iter():
        if _local(child.tag) == name:
            return child
    return None


def _findall(node, name):
    return [child for child in node.iter() if _local(child.tag) == name]


def _parse_trials(xml_text):
    if not xml_text.strip() or "<fullTrial>" not in xml_text and "<trial " not in xml_text:
        return []
    root = ET.fromstring(xml_text)
    trials = []
    for trial in root.iter():
        if _local(trial.tag) != "trial":
            continue
        ident = trial.attrib.get("publicIdentifierCanonical")
        if not ident:
            node = _find(trial, "isrctn")
            ident = "ISRCTN" + _text(node) if node is not None and _text(node) else None
        acronym = _text(_find(trial, "acronym"))
        title = _text(_find(trial, "scientificTitle")) or _text(_find(trial, "title"))
        primary = _text(_find(trial, "primaryOutcome"))
        phase = _text(_find(trial, "phase"))
        target = _text(_find(trial, "targetEnrolment"))
        nct = norm_nct(_text(_find(trial, "clinicalTrialsGovNumber")))
        start = _text(_find(trial, "recruitmentStart"))
        end = _text(_find(trial, "recruitmentEnd"))
        override = _text(_find(trial, "recruitmentStatusOverride"))
        condition = _text(_find(trial, "description"))
        countries = []
        for country in _findall(trial, "country"):
            name = _text(country)
            if name and name not in countries and name not in {"England", "Scotland", "Wales", "Northern Ireland"}:
                countries.append(name)
        if any(c in countries for c in ("United Kingdom",)) is False and any(
            part in " ".join(countries) for part in ("England",)
        ):
            pass
        # Nation names were skipped above. Put the UK back when the trial recruits there.
        raw_countries = [_text(c) for c in _findall(trial, "country")]
        if any(name in {"England", "Scotland", "Wales", "Northern Ireland", "United Kingdom"} for name in raw_countries):
            if "United Kingdom" not in countries:
                countries.insert(0, "United Kingdom")
        trials.append({
            "isrctn": norm_isrctn(ident),
            "acronym": acronym,
            "title": title,
            "primary": primary,
            "phase": phase or None,
            "target": int(target) if target.isdigit() else None,
            "nct": nct,
            "start": start[:10] if start else None,
            "end": end[:10] if end else None,
            "override": override or None,
            "condition": condition,
            "countries": countries,
            "updated": (trial.attrib.get("lastUpdated") or "")[:10],
            "final_enrolment": int(final) if (final := _text(_find(trial, "totalFinalEnrolment"))).isdigit() else None,
        })
    return trials


def _isrctn_search(query):
    url = with_query(ISRCTN, {"q": query, "limit": 8})
    body, _status = fetch(url, timeout=40, headers={"Accept": "application/xml"})
    return _parse_trials(body)


def _status_from_dates(row):
    if row.get("override"):
        text = row["override"].lower()
        if "not yet" in text:
            return "not yet recruiting"
        if "recruit" in text and "no longer" not in text and "not" not in text:
            return "recruiting"
        if "stop" in text or "suspend" in text:
            return "suspended"
        if "no longer" in text or "closed" in text:
            return "closed to accrual"
    today = today_iso()
    start, end = row.get("start"), row.get("end")
    if start and start > today:
        return "not yet recruiting"
    if end and end < today:
        return "closed to accrual"
    if start and start <= today:
        return "recruiting"
    return "unknown"


def _ctgov_acronym(acronym):
    payload = fetch_json(with_query("https://clinicaltrials.gov/api/v2/studies", {
        "query.term": acronym,
        "pageSize": 5,
        "fields": "protocolSection.identificationModule,protocolSection.statusModule,protocolSection.designModule,protocolSection.outcomesModule,protocolSection.sponsorCollaboratorsModule,protocolSection.descriptionModule,protocolSection.conditionsModule",
    }))
    for study in payload.get("studies") or []:
        ident = study.get("protocolSection", {}).get("identificationModule", {})
        got = (ident.get("acronym") or "").upper().replace("-", " ").strip()
        wanted = acronym.upper().replace("-", " ").strip()
        got_fold = re.sub(r"[^A-Z0-9]", "", got)
        wanted_fold = re.sub(r"[^A-Z0-9]", "", wanted)
        if got == wanted or got.startswith(wanted + " ") or (len(wanted_fold) >= 5 and got_fold == wanted_fold):
            return study
    return None


def _page_bits(page):
    title = (page.get("title") or {}).get("rendered") or page.get("slug") or ""
    title = re.sub(r"<[^>]+>", "", title)
    content = (page.get("content") or {}).get("rendered") or ""
    text = html_to_text(content)
    return title.strip(), text


def _split_tourist(title, text):
    marker = re.search(r"Quality of life After Radiotherapy|QUARTZ LUNG is a multi", text, re.I)
    if not marker:
        marker = re.search(r"QUARTZ\s+LUNG", text, re.I)
    if not marker:
        return [(title, text, None)]
    prince = text[:marker.start()]
    quartz = text[marker.start():]
    return [
        ("PRINCE", prince, "TOURIST"),
        ("QUARTZ LUNG", quartz, "TOURIST"),
    ]


def _page_orgs(text):
    sponsor = None
    match = re.search(r"Sponsor:\s*([^(\n]+)", text)
    if match:
        sponsor = match.group(1).strip(" ,.;")
    groups = []
    if re.search(r"Southampton Clinical Trials Unit", text, re.I):
        groups.append("Southampton Clinical Trials Unit")
    if re.search(r"\bNIHR\b", text):
        groups.append("NIHR")
    return sponsor, groups


def _one(name, text, platform, page_url, sponsor=None, extra_groups=None):
    acronym = name.strip()
    phase_page = phase_language(text)
    role, evidence, modality = classify_text(acronym + " " + text[:1500], randomized=bool(re.search(r"randomi", text, re.I)))
    if role is None:
        role, evidence, modality = "RT-focused", ["RTTQA portfolio trial with a radiotherapy component"], "external beam"
    isrctn_rows = []
    for query in (f'acronym:"{acronym}"', acronym):
        try:
            isrctn_rows = _isrctn_search(query)
        except Exception:
            isrctn_rows = []
        if isrctn_rows:
            break
    chosen = None
    for row in isrctn_rows:
        if acronym.lower() in (row.get("acronym") or "").lower() or acronym.lower() in (row.get("title") or "").lower():
            chosen = row
            break
    if chosen is None and len(isrctn_rows) == 1:
        chosen = isrctn_rows[0]
    ctgov = None
    if chosen is None:
        try:
            ctgov = _ctgov_acronym(acronym)
        except Exception:
            ctgov = None
    trial = blank_trial()
    trial["name"] = acronym
    trial["title"] = (chosen or {}).get("title") or clip(text, 280) or acronym
    trial["isrctn"] = (chosen or {}).get("isrctn")
    trial["nct"] = (chosen or {}).get("nct")
    if ctgov and not trial["nct"]:
        trial["nct"] = ctgov["protocolSection"]["identificationModule"].get("nctId")
    trial["protocols"] = [acronym]
    trial["groups"] = ["RTTQA"]
    trial["lead_group"] = None
    trial["platform"] = platform
    trial["rt_role"] = role
    trial["rt_evidence"] = evidence
    trial["modality"] = modality or "external beam"
    trial["study_type"] = "interventional"
    trial["record_kind"] = "trial"
    notes = []
    if chosen:
        trial["registry"] = True
        if chosen.get("phase"):
            trial["phase"] = phase_language(chosen["phase"]) or chosen["phase"]
            trial["phase_code"] = trial["phase"]
        else:
            trial["phase_in_title"] = phase_page
            notes.append(
                f"ISRCTN does not state a phase. The RTTQA page describes this as phase {phase_page}."
                if phase_page else "ISRCTN does not state a phase."
            )
        trial["status"] = _status_from_dates(chosen)
        trial["status_source"] = "ISRCTN"
        trial["status_checked"] = chosen.get("updated") or today_iso()
        trial["registry_status"] = trial["status"]
        trial["registry_checked"] = trial["status_checked"]
        if chosen.get("primary"):
            trial["primary_endpoints"] = [clip(chosen["primary"], 400)]
        trial["population"] = clip(chosen.get("condition"), 400)
        trial["countries_listed"] = chosen.get("countries") or ["United Kingdom"]
        if trial["status"] in {"recruiting", "not yet recruiting"}:
            trial["countries_open"] = list(trial["countries_listed"])
        trial["start_date"] = chosen.get("start")
        trial["accrual"] = {
            "quality": "target-only" if chosen.get("target") else "status-only",
            "actual": None,
            "target": chosen.get("target"),
            "enrollment_type": None,
            "as_of": chosen.get("updated") or today_iso(),
            "source": "ISRCTN",
            "source_url": f"https://www.isrctn.com/{trial['isrctn']}" if trial["isrctn"] else page_url,
            "reported": None,
            "rate_per_month": None,
            "notes": ["ISRCTN publishes a recruitment target, not a live enrolled count."] + notes,
        }
        final_n = chosen.get("final_enrolment")
        if final_n:
            trial["accrual"]["notes"].append(
                f"ISRCTN totalFinalEnrolment is {final_n}. That field is not a live enrolled count, so it is not stored as accrual."
            )
        if chosen.get("end") and chosen["end"] < today_iso() and not chosen.get("override"):
            trial["accrual"]["notes"].append(
                f"ISRCTN recruitment end date is {chosen['end']}. Status is closed to accrual from that date."
            )
        trial["urls"]["isrctn"] = f"https://www.isrctn.com/{trial['isrctn']}" if trial["isrctn"] else None
    else:
        trial["phase_in_title"] = phase_page
        notes.append("No ISRCTN record matched this RTTQA name.")
        if ctgov:
            notes.append(f"ClinicalTrials.gov acronym search returned {trial['nct']}.")
        trial["status"] = "unknown"
        trial["status_source"] = "RTTQA"
        trial["status_checked"] = today_iso()
        trial["accrual"] = {
            "quality": "status-only",
            "actual": None,
            "target": None,
            "enrollment_type": None,
            "as_of": today_iso(),
            "source": "RTTQA portfolio",
            "source_url": page_url,
            "reported": None,
            "rate_per_month": None,
            "notes": notes,
        }
    trial["urls"]["rttqa"] = page_url
    if trial.get("nct"):
        trial["urls"]["ctgov"] = f"https://clinicaltrials.gov/study/{trial['nct']}"
    if sponsor and not trial.get("sponsor"):
        trial["sponsor"] = sponsor
    for group in extra_groups or []:
        if group not in trial["groups"]:
            trial["groups"].append(group)
    if "Southampton Clinical Trials Unit" in (extra_groups or []) and not trial.get("lead_group"):
        trial["lead_group"] = "Southampton Clinical Trials Unit"
    trial["sources"] = [{"pipeline": "rttqa", "url": page_url, "checked": today_iso()}]
    if trial.get("isrctn"):
        trial["sources"].append({"pipeline": "isrctn", "url": trial["urls"].get("isrctn"), "checked": today_iso()})
    trial["checked_at"] = utc_now()
    return trial


def load(store, log, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / "pages.json"
    if cache.exists():
        pages = json.loads(cache.read_text())
    else:
        body, _status = fetch(PAGES, timeout=60, headers={"Accept": "application/json"})
        pages = json.loads(body)
        cache.write_text(json.dumps(pages))
    made = 0
    for page in pages:
        slug = page.get("slug") or ""
        if slug in SKIP or page.get("status") != "publish":
            continue
        title, text = _page_bits(page)
        if len(text) < 80:
            continue
        url = page.get("link") or f"https://rttrialsqa.org.uk/?page_id={page.get('id')}"
        sponsor, extra_groups = _page_orgs(text)
        parts = _split_tourist(title, text) if slug == "tourist" else [(title, text, None)]
        for name, chunk, platform in parts:
            record = _one(name, chunk, platform, url, sponsor=sponsor, extra_groups=extra_groups)
            store.upsert(record, "rttqa")
            made += 1
    log.append({"pipeline": "rttqa", "pages": len(pages), "rows": made})
    return made
