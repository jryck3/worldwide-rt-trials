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
PUBLIC_EXPORT_URL = PUBLIC_PAGE + "ctsu-public.json"
PUBLIC_MANIFEST_URL = PUBLIC_PAGE + "ctsu-public.manifest.json"
WW_ROOT = Path(__file__).resolve().parents[2]
EXPORT_CACHE = WW_ROOT / "data" / "cache" / "ctsu-public.json"
MANIFEST_CACHE = WW_ROOT / "data" / "cache" / "ctsu-public.manifest.json"
_PUBLIC_EXPORT = None


def _content_hash(export):
    """Same consumed-field hash as the CTSU public_export module."""
    import hashlib
    studies = []
    for study in export.get("studies") or []:
        copied = {key: value for key, value in study.items() if key not in ("projected_completion", "avg_rate")}
        measurement = dict(copied.get("measurement") or {})
        measurement.pop("snapshot_at", None)
        copied["measurement"] = measurement
        studies.append(copied)
    consumed = {
        "schema_version": export.get("schema_version"),
        "taxonomy_version": export.get("taxonomy_version"),
        "studies": studies,
        "history": export.get("history") or {},
    }
    raw = json.dumps(consumed, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _checked(export, manifest):
    if not isinstance(export, dict) or not export.get("studies"):
        raise RuntimeError("CTSU export has no studies")
    expected = (manifest or {}).get("content_hash")
    if expected and _content_hash(export) != expected:
        raise RuntimeError("CTSU export hash does not match its manifest")
    if export.get("schema_version") not in (None, 1):
        raise RuntimeError("Unsupported CTSU export schema")
    return export, "ctsu-public.json"


def _read_cached():
    if not EXPORT_CACHE.is_file() or not MANIFEST_CACHE.is_file():
        return None
    export = json.loads(EXPORT_CACHE.read_text())
    manifest = json.loads(MANIFEST_CACHE.read_text())
    return _checked(export, manifest)


def _store_cache(export, manifest):
    EXPORT_CACHE.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_CACHE.write_text(json.dumps(export))
    MANIFEST_CACHE.write_text(json.dumps(manifest, indent=1) + "\n")


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
    """The published CTSU export. A failed fetch keeps the last good cache."""
    global _PUBLIC_EXPORT
    if _PUBLIC_EXPORT is not None:
        return _PUBLIC_EXPORT
    from ..http_client import fetch
    try:
        body, status = fetch(PUBLIC_EXPORT_URL, timeout=180)
        manifest_body, _manifest_status = fetch(PUBLIC_MANIFEST_URL, timeout=60)
        if status and status >= 400:
            raise RuntimeError(f"CTSU export HTTP {status}")
        export = json.loads(body)
        manifest = json.loads(manifest_body)
        checked = _checked(export, manifest)
    except Exception:
        cached = _read_cached()
        if cached is None:
            raise
        _PUBLIC_EXPORT = cached[0]
        return cached[0]
    _store_cache(export, manifest)
    _PUBLIC_EXPORT = export
    return export


def load_export():
    """Read the published CTSU export. Local studies.json is not the contract."""
    try:
        export = _fetch_public()
        return export, PUBLIC_EXPORT_URL
    except Exception:
        cached = _read_cached()
        if cached is not None:
            return cached
        raise RuntimeError("No verified CTSU public export is available")


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
    trial["phase"] = study.get("phase_normalized") or study.get("phase")
    trial["phase_code"] = study.get("phase")
    trial["status"] = STATUS_MAP.get(study.get("ctsu_status") or study.get("status"), "unknown")
    trial["status_source"] = "CTSU public export"
    trial["status_checked"] = (fetched_at or "")[:10] or today_iso()
    registry_status = study.get("registry_status") or study.get("ctgov_status")
    if registry_status:
        trial["registry_status"] = CTGOV_STATUS.get(registry_status, registry_status)
    if study.get("status_basis"):
        trial["status_basis"] = study["status_basis"]
    if study.get("cancer_types"):
        trial["cancer_types"] = list(study["cancer_types"])
        trial["reviewed"] = {"cancer_types": list(study["cancer_types"]), "burden": list(study.get("burden") or [])}
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
