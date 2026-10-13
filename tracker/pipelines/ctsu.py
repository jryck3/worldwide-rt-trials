"""Read every row from the existing CTSU export. Does not write to that project."""

import json
import re
import sys
from pathlib import Path

from ..model import CTGOV_STATUS, blank_trial
from ..textutil import today_iso

CTSU_ROOT = Path(__file__).resolve().parents[3] / "CTSU Trials Workspace"


def protocol_library():
    """The CTSU project's protocol_library module, or None where that project is absent (GitHub Actions)."""
    scripts = CTSU_ROOT / "scripts"
    if not (scripts / "protocol_library.py").exists():
        return None
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import protocol_library
    return protocol_library

STATUS_MAP = {
    "Active": "recruiting",
    "Approved": "not yet recruiting",
    "Temporarily Closed to Accrual": "suspended",
    "Temporarily Closed to Accrual &Treatment": "suspended",
    "Closed to Accrual": "closed to accrual",
    "Closed to Accrual and Treatment": "closed to accrual",
    "Completed": "completed",
}
RT_MODALITY = {
    "RT-focused": "external beam",
    "RT component": "external beam",
    "Radiopharmaceutical": "radiopharmaceutical",
}


PUBLIC_PAGE = "https://jryck3.github.io/CTSU-Tracker/"
_PUBLIC_EXPORT = None


def _load_embedded_text(text):
    match = re.search(r"const DATA = (\{.*?\});\n", text)
    if not match:
        return None
    return json.loads(match.group(1))


def _load_embedded(path):
    if not path.is_file():
        return None
    return _load_embedded_text(path.read_text(errors="replace"))


def _fetch_public():
    """The published CTSU page. Used when this checkout has no sibling CTSU folder."""
    global _PUBLIC_EXPORT
    if _PUBLIC_EXPORT is not None:
        return _PUBLIC_EXPORT
    from ..http_client import fetch
    body, _status = fetch(PUBLIC_PAGE, timeout=180)
    payload = _load_embedded_text(body)
    if not payload or not payload.get("studies"):
        raise RuntimeError("Public CTSU page had no study list")
    _PUBLIC_EXPORT = payload
    return payload


def load_export():
    """Use the newer of studies.json and the local dashboard, or the published page."""
    structured = None
    structured_path = CTSU_ROOT / "data" / "studies.json"
    if structured_path.is_file():
        structured = json.loads(structured_path.read_text())
    embedded = _load_embedded(CTSU_ROOT / "docs" / "index.html")
    chosen = None
    source = None
    if structured and structured.get("studies"):
        chosen = structured
        source = "data/studies.json"
    if embedded and embedded.get("studies") and (embedded.get("generated_at") or "") > ((chosen or {}).get("generated_at") or ""):
        chosen = embedded
        source = "docs/index.html"
    if chosen:
        return chosen, source
    return _fetch_public(), PUBLIC_PAGE


def _record(study, fetched_at, export_source):
    trial = blank_trial()
    protocol = study.get("protocol")
    trial["name"] = study.get("alias") or protocol
    trial["title"] = study.get("title")
    trial["nct"] = study.get("nct")
    trial["protocols"] = [protocol] if protocol else []
    if study.get("lead"):
        trial["lead_group"] = study["lead"]
        trial["groups"] = [study["lead"]]
    trial["phase"] = study.get("phase")
    trial["phase_code"] = study.get("phase")
    trial["status"] = STATUS_MAP.get(study.get("status"), "unknown")
    trial["status_source"] = "CTSU public export"
    trial["status_checked"] = (fetched_at or "")[:10] or today_iso()
    if study.get("ctgov_status"):
        trial["registry_status"] = CTGOV_STATUS.get(study["ctgov_status"], study["ctgov_status"])
    trial["population"] = study.get("disease")
    trial["conditions"] = [study["disease"]] if study.get("disease") else []
    trial["rt_role"] = study.get("rt_role")
    trial["rt_evidence"] = list(study.get("rt_evidence") or [])
    if study.get("rt_role") in RT_MODALITY:
        trial["modality"] = RT_MODALITY[study["rt_role"]]
    trial["study_type"] = "interventional"
    trial["start_date"] = study.get("start_date")
    trial["accrual"] = {
        "quality": "snapshot",
        "actual": study.get("accrual"),
        "target": study.get("target"),
        "enrollment_type": None,
        "as_of": (fetched_at or "")[:10] or today_iso(),
        "source": f"CTSU public export ({export_source})",
        "source_url": "https://ctsu.cancer.gov/protocol",
        "reported": None,
        "rate_per_month": None,
        "notes": [],
    }
    if study.get("nct"):
        trial["urls"]["ctgov"] = f"https://clinicaltrials.gov/study/{study['nct']}"
    trial["urls"]["ctsu"] = "https://ctsu.cancer.gov/protocol"
    trial["sources"] = [{
        "pipeline": "ctsu",
        "url": "https://ctsu.cancer.gov/protocol",
        "checked": trial["status_checked"],
        "export": "CTSU Trials Workspace/" + export_source,
    }]
    trial["checked_at"] = fetched_at
    return trial


def load(store, log):
    payload, export_source = load_export()
    studies = list(payload.get("studies") or [])
    for study in studies:
        store.upsert(_record(study, payload.get("snapshot_fetched_at") or payload.get("generated_at"), export_source), "ctsu")
    log.append({
        "pipeline": "ctsu",
        "export": export_source,
        "generated_at": payload.get("generated_at"),
        "snapshot_fetched_at": payload.get("snapshot_fetched_at"),
        "rows": len(studies),
        "rt_rows": sum(1 for study in studies if study.get("rt_related")),
        "feed_counts": payload.get("counts"),
    })
    return len(studies)
