"""CCTG public disease-site list. Rows already ported from CTSU are not added again."""

import re

from ..classify import classify_text, phase_language
from ..http_client import fetch
from ..model import blank_trial, norm_nct
from ..textutil import clip, today_iso, utc_now

URL = "https://www.ctg.queensu.ca/public/all-disease-sites"

STATUS_MAP = {
    "open to accrual": "recruiting",
    "planned": "not yet recruiting",
    "closed to accrual": "closed to accrual",
    "permanently closed": "completed",
    "completed": "completed",
    "on hold": "suspended",
    "suspended": "suspended",
}


def _clean(html):
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _rows(html):
    pattern = re.compile(
        r"<tr>\s*<td>([\s\S]*?)</td>\s*<td>([\s\S]*?)</td>\s*<td>([\s\S]*?)</td>\s*</tr>",
        re.I,
    )
    rows = []
    for protocol_html, body_html, status_html in pattern.findall(html):
        protocol = _clean(protocol_html)
        body = _clean(body_html)
        status = _clean(status_html)
        if not protocol or len(protocol) > 80:
            continue
        rows.append((protocol, body, status))
    return rows


def _status(text):
    low = text.lower()
    for key, value in STATUS_MAP.items():
        if key in low:
            return value
    return "unknown"


def load(store, log, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "all.html"
    if path.exists():
        html = path.read_text(errors="replace")
    else:
        html, _ = fetch(URL, timeout=90)
        path.write_text(html)
    added = 0
    skipped_existing = 0
    skipped_not_rt = 0
    for protocol, body, status in _rows(html):
        title_match = re.match(r"(.{20,240}?)\s+(?:Read More|Cancers |The purpose|This (?:study|trial|research))", body)
        title = title_match.group(1) if title_match else body[:220]
        phase = phase_language(title + " " + body[:400])
        if phase in {"1", "1/2"}:
            continue
        nct = norm_nct(body)
        role, evidence, modality = classify_text(title, body[:1200], randomized=bool(re.search(r"random|phase iii|phase ii", title, re.I)))
        if role is None:
            skipped_not_rt += 1
            continue
        probe = {"nct": nct, "protocols": [protocol], "name": protocol}
        existing = store.find(probe)
        if existing:
            if protocol not in existing["protocols"]:
                existing["protocols"].append(protocol)
            if "CCTG" not in existing["groups"]:
                existing["groups"].append("CCTG")
            if "cctg" not in existing["pipelines"]:
                existing["pipelines"].append("cctg")
            store.upsert({
                "nct": nct,
                "protocols": [protocol],
                "rt_role": role,
                "rt_evidence": evidence,
                "modality": modality,
            }, "cctg")
            skipped_existing += 1
            continue
        objectives = ""
        obj = re.search(r"Objectives\s*:?\s*(.{20,500})", body)
        if obj:
            objectives = obj.group(1)
        record = blank_trial()
        record["name"] = protocol
        record["title"] = title
        record["nct"] = nct
        record["protocols"] = [protocol]
        record["groups"] = ["CCTG"]
        record["lead_group"] = "CCTG"
        record["phase"] = phase
        record["phase_code"] = phase
        record["status"] = _status(status)
        record["status_source"] = "CCTG public list"
        record["status_checked"] = today_iso()
        record["population"] = clip(body, 400)
        record["primary_endpoints"] = [clip(objectives, 300)] if objectives else []
        record["rt_role"] = role
        record["rt_evidence"] = evidence
        record["modality"] = modality or "external beam"
        record["accrual"] = {
            "quality": "status-only",
            "actual": None,
            "target": None,
            "enrollment_type": None,
            "as_of": today_iso(),
            "source": "CCTG public trial list",
            "source_url": URL,
            "reported": None,
            "rate_per_month": None,
            "notes": ["The CCTG public list gives status and does not give a study-wide enrolled count."],
        }
        record["urls"]["cctg"] = URL
        if nct:
            record["urls"]["ctgov"] = f"https://clinicaltrials.gov/study/{nct}"
        record["sources"] = [{"pipeline": "cctg", "url": URL, "checked": today_iso()}]
        record["checked_at"] = utc_now()
        record["sponsor"] = "Canadian Cancer Trials Group"
        store.upsert(record, "cctg")
        added += 1
    log.append({
        "pipeline": "cctg",
        "added": added,
        "merged_into_existing": skipped_existing,
        "not_rt": skipped_not_rt,
    })
    return added
