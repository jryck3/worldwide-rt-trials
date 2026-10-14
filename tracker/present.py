"""Display names, disease sites, source tags, and protocol details.

Reads CTSU protocol briefs through the CTSU project's protocol_library. Does
not read protocol PDFs and does not write into the CTSU project.
"""

import json
import re
import sys
from pathlib import Path

from .classify_vendor import classify_record
from .pipelines.ctsu import CTSU_ROOT, load_export, protocol_library
from .papers import attach_publications
from .schema_pdf import apply_saved_schemas
from .textutil import clip

try:
    _SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    import paper_library as paper_lib
except Exception:  # noqa: BLE001
    paper_lib = None

NRG_ID = re.compile(r"\bNRG[-\s]?([A-Z]{2})[-\s]?(\d{3,4})\b", re.I)
SWOG_ID = re.compile(r"\bSWOG[-\s]*S?(\d{4})\b|\bS(\d{4})\b", re.I)
ECOG_EA = re.compile(r"\b(EA\d{3,4})\b", re.I)
ECOG_E = re.compile(r"\bE(\d{4})\b", re.I)
RTOG_ID = re.compile(r"\bRTOG[-\s]?(\d{4})\b", re.I)

BRIEF_FIELDS = (
    "version_date", "posted", "amendments", "schema", "schema_source", "eligibility",
    "pretreatment", "during", "followup", "calendar", "specimens", "submission",
)

def _classify_input(trial):
    brief = trial.get("brief") or {}
    return {
        "protocol": (trial.get("protocols") or [""])[0],
        "title": trial.get("title") or "",
        "population": trial.get("population") or "",
        "disease": trial.get("ctsu_disease") or "",
        "conditions": trial.get("conditions") or [],
        "eligibility": brief.get("eligibility"),
        "reviewed": trial.get("reviewed") or {},
    }


def apply_classification(trial):
    classified = classify_record(_classify_input(trial))
    trial["cancer_types"] = classified["cancer_types"]
    trial["anatomy"] = classified["anatomy"]
    trial["histology"] = classified["histology"]
    trial["disease_sites"] = classified["cancer_types"]
    trial["disease_burden"] = classified["burden"]
    trial["intent"] = classified["intent"]
    return classified


def disease_sites(trial):
    if "cancer_types" not in trial:
        apply_classification(trial)
    return list(trial.get("cancer_types") or [])


def disease_burden(trial):
    if "disease_burden" not in trial:
        apply_classification(trial)
    return list(trial.get("disease_burden") or [])


def source_tags(trial):
    blob = " ".join([
        trial.get("lead_group") or "",
        " ".join(trial.get("groups") or []),
        " ".join(trial.get("protocols") or []),
        trial.get("name") or "",
    ])
    tags = []
    for key, pattern in GROUP_TAGS:
        if pattern.search(blob):
            tags.append(key)
    grouped = list(tags)
    for pipe in [trial.get("source_pipeline"), *(trial.get("pipelines") or [])]:
        if pipe in PIPELINE_TAGS and pipe not in tags:
            tags.append(pipe)
    pipelines = [trial.get("source_pipeline"), *(trial.get("pipelines") or [])]
    if not grouped and "ctsu" in pipelines and "nci" not in tags:
        tags.append("nci")
    if not tags:
        tags.append(trial.get("source_pipeline") or "ctgov")
    return tags


def _copy_brief(brief):
    copied = {}
    for field in BRIEF_FIELDS:
        value = brief.get(field)
        if value:
            copied[field] = value
    return copied


def _attach_patient_materials(trials):
    """Match NRG patient pages and brochures onto a trial's protocol ids.

    The file lives in the CTSU project. When that checkout is absent, leave any
    materials already stored on the trial in place.
    """
    path = CTSU_ROOT / "data" / "patient_materials.json"
    if not path.is_file():
        return
    materials = json.loads(path.read_text())
    lib = protocol_library()
    index = {}
    for protocol, record in materials.items():
        spellings = lib.keys(protocol) if lib else [str(protocol).upper()]
        for key in spellings:
            index.setdefault(key, record)
    for trial in trials:
        record = None
        for value in trial.get("protocols") or []:
            spellings = lib.keys(value) if lib else [str(value).upper()]
            for key in spellings:
                if key in index:
                    record = index[key]
                    break
            if record:
                break
        if record:
            trial["patient_materials"] = record
        else:
            trial.pop("patient_materials", None)


def _attach_briefs(trials):
    lib = protocol_library()
    if lib is None:
        return False
    for trial in trials:
        _protocol, brief = lib.find_brief(trial.get("protocols") or [])
        copied = _copy_brief(brief or {})
        if copied:
            trial["brief"] = copied
    return True


def _spellings(value, lib):
    if lib is not None:
        return lib.keys(value)
    text = str(value or "").strip().upper()
    if not text:
        return []
    found = [text]
    if text.startswith("COG-"):
        found.append(text[4:])
    return found


def _attach_ctsu_meta(trials):
    lib = protocol_library()
    payload, _source = load_export()
    by_nct = {}
    by_protocol = {}
    for study in payload.get("studies") or []:
        if study.get("nct"):
            by_nct[study["nct"]] = study
        for key in _spellings(study.get("protocol"), lib):
            by_protocol.setdefault(key, study)
    for trial in trials:
        study = by_nct.get(trial.get("nct"))
        if study is None:
            for protocol in trial.get("protocols") or []:
                for key in _spellings(protocol, lib):
                    study = by_protocol.get(key)
                    if study:
                        break
                if study:
                    break
        if not study:
            continue
        if study.get("program"):
            trial["nci_program"] = study["program"]
        if study.get("screening") is not None:
            trial["screening_accrual"] = study["screening"]
        if study.get("primary_completion"):
            trial["primary_completion"] = study["primary_completion"]
        if study.get("disease"):
            trial["ctsu_disease"] = study["disease"]


def _arm_step(study):
    module = ((study.get("protocolSection") or {}).get("armsInterventionsModule") or {})
    groups = []
    for arm in module.get("armGroups") or []:
        label = (arm.get("label") or arm.get("type") or "Arm").strip()
        text = clip(arm.get("description") or "", 500) or ""
        if label or text:
            groups.append({"label": label, "text": text})
    if groups:
        return {
            "label": "Arms",
            "text": "ClinicalTrials.gov arm list. This is not a protocol schema.",
            "arms": groups,
        }
    interventions = []
    for item in module.get("interventions") or []:
        label = (item.get("type") or "Intervention").replace("_", " ").title()
        text = clip(item.get("description") or item.get("name") or "", 500) or ""
        name = item.get("name")
        if name and name not in text:
            text = f"{name}. {text}".strip()
        if text:
            interventions.append({"label": label, "text": text})
    if not interventions:
        return None
    return {
        "label": "Interventions",
        "text": "ClinicalTrials.gov intervention list. This is not a protocol schema.",
        "arms": interventions,
    }


def _registry_steps(study):
    protocol = study.get("protocolSection") or {}
    description = protocol.get("descriptionModule") or {}
    design = ((protocol.get("designModule") or {}).get("designInfo") or {})
    text = clip(description.get("detailedDescription") or description.get("briefSummary") or "", 700)
    model = design.get("interventionModelDescription")
    if isinstance(model, str) and model and (not text or len(text) < 80):
        text = clip(model, 700)
    steps = []
    if text:
        steps.append({
            "label": "Design",
            "text": text,
            "note": "ClinicalTrials.gov description. This is not a protocol schema page.",
        })
    arms = _arm_step(study)
    if arms:
        steps.append(arms)
    return steps


def _attach_registry_schema(trials, cache_dir):
    if not cache_dir or not Path(cache_dir).exists():
        return
    wanted = {trial.get("nct") for trial in trials if trial.get("nct") and not (trial.get("brief") or {}).get("schema")}
    found = {}
    for path in sorted(Path(cache_dir).glob("*.json")):
        if not wanted - found.keys():
            break
        studies = json.loads(path.read_text())
        if isinstance(studies, dict):
            studies = studies.get("studies") or []
        for study in studies:
            nct = ((study.get("protocolSection") or {}).get("identificationModule") or {}).get("nctId")
            if nct not in wanted or nct in found:
                continue
            steps = _registry_steps(study)
            if steps:
                found[nct] = steps
    for trial in trials:
        steps = found.get(trial.get("nct"))
        if steps and not (trial.get("brief") or {}).get("schema"):
            trial["registry_schema"] = steps


def _load_saved_schemas(root):
    path = Path(root) / "config" / "protocol_schemas.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _attach_paper_status(trials):
    if paper_lib is None:
        return
    access = paper_lib.load_access()
    for trial in trials:
        on_file = 0
        needs = 0
        for paper in trial.get("publications") or []:
            pmid = str(paper.get("pmid") or "")
            row = ((access.get("papers") or {}).get(pmid) or {})
            if paper_lib.current_file(pmid):
                paper["access"] = "on_file"
                on_file += 1
            elif row.get("status"):
                paper["access"] = row["status"]
                if row["status"] == "on_file":
                    on_file += 1
                elif row["status"] != "inaccessible":
                    needs += 1
            else:
                paper["access"] = "needs_access"
                needs += 1
            if row.get("wvu_url"):
                paper["wvu_url"] = row["wvu_url"]
        if on_file:
            trial["paper_on_file"] = on_file
        if needs:
            trial["paper_needs_access"] = needs


def _brief_source(trial):
    brief = trial.get("brief") or {}
    if brief.get("schema") or brief.get("eligibility"):
        source = brief.get("schema_source") or ""
        if not source or re.search(r"protocol on file|protocol page|CTSU", source, re.I):
            return "protocol"
        return "paper"
    if trial.get("registry_schema"):
        return "registry"
    return "none"


def _prior_match(index, trial):
    if trial.get("nct") and trial["nct"] in index["nct"]:
        return index["nct"][trial["nct"]]
    if trial.get("isrctn") and trial["isrctn"] in index["isrctn"]:
        return index["isrctn"][trial["isrctn"]]
    for protocol in trial.get("protocols") or []:
        found = index["protocol"].get(str(protocol).upper())
        if found:
            return found
    return None


def _needs_review(section):
    if isinstance(section, dict):
        return section.get("status") == "needs_review"
    if isinstance(section, list):
        return any(isinstance(item, dict) and item.get("status") == "needs_review" for item in section)
    return False


def _merge_brief(current, exported):
    """A newer export brief replaces the cache. A section still needing review keeps the prior text."""
    current = current or {}
    exported = _copy_brief(exported or {})
    if not exported:
        return current or None
    if not current:
        return exported
    current_version = str(current.get("version_date") or "")
    export_version = str(exported.get("version_date") or "")
    if export_version and current_version and export_version < current_version:
        return current
    merged = dict(exported)
    for field in BRIEF_FIELDS:
        if field in ("version_date", "posted", "schema_source"):
            continue
        if _needs_review(exported.get(field)) and current.get(field):
            merged[field] = current[field]
            merged["review_warnings"] = list(merged.get("review_warnings") or [])
            merged["review_warnings"].append(
                f"{field} still shows the reviewed {current_version or 'earlier'} text. The {export_version or 'newer'} text needs review."
            )
    return merged


def keep_prior_details(trials, prior_trials, export_studies=None, library_present=False):
    """Keep protocol briefs across a full rebuild.

    A newer reviewed export section replaces a cached brief. A section marked
    needs_review keeps the last reviewed text and a version warning. A cloud
    build without the protocol library still keeps the prior library date's brief
    until that newer export arrives.
    """
    index = {"nct": {}, "isrctn": {}, "protocol": {}}
    for old in prior_trials or []:
        if old.get("nct"):
            index["nct"].setdefault(old["nct"], old)
        if old.get("isrctn"):
            index["isrctn"].setdefault(old["isrctn"], old)
        for protocol in old.get("protocols") or []:
            index["protocol"].setdefault(str(protocol).upper(), old)
    if not library_present:
        for trial in trials:
            old = _prior_match(index, trial)
            if not old:
                continue
            if not trial.get("patient_materials") and old.get("patient_materials"):
                trial["patient_materials"] = old["patient_materials"]

    export_by_nct = {}
    export_by_protocol = {}
    for study in export_studies or []:
        if study.get("nct"):
            export_by_nct.setdefault(study["nct"], study)
        if study.get("protocol"):
            export_by_protocol.setdefault(str(study["protocol"]).upper(), study)
    for trial in trials:
        old = _prior_match(index, trial)
        prior_brief = None if library_present else (old or {}).get("brief")
        study = export_by_nct.get(trial.get("nct")) if trial.get("nct") else None
        if study is None:
            for protocol in trial.get("protocols") or []:
                study = export_by_protocol.get(str(protocol).upper())
                if study:
                    break
        base = trial.get("brief") or prior_brief
        merged = _merge_brief(base, (study or {}).get("brief") or {})
        if merged:
            trial["brief"] = merged
        elif prior_brief and not trial.get("brief"):
            trial["brief"] = prior_brief
        trial["brief_source"] = _brief_source(trial)


def annotate(trials, root):
    """Fill display fields on an existing trial list. Safe to run more than once."""
    root = Path(root)
    _attach_ctsu_meta(trials)
    _attach_briefs(trials)
    _attach_patient_materials(trials)
    apply_saved_schemas(trials, _load_saved_schemas(root))
    attach_publications(trials, root)
    _attach_paper_status(trials)
    _attach_registry_schema(trials, root / "data" / "cache" / "ctgov")
    for trial in trials:
        trial["name"] = display_name(trial)
        apply_classification(trial)
        trial["source_tags"] = source_tags(trial)
        trial["brief_source"] = _brief_source(trial)
    return trials
