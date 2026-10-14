"""Display names, disease sites, source tags, and protocol details.

Reads CTSU protocol briefs through the CTSU project's protocol_library. Does
not read protocol PDFs and does not write into the CTSU project.
"""

import json
import re
import sys
from pathlib import Path

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

DISEASE_RULES = [
    ("Breast", re.compile(r"breast", re.I)),
    ("GU", re.compile(r"prostate|bladder|urothelial|renal cell|renal carcinoma|kidney cancer|testicular|penile|ureter", re.I)),
    ("NSCLC", re.compile(r"non[-\s]?small[-\s]?cell lung|\bnsclc\b", re.I)),
    ("SCLC", re.compile(r"(?<!non)(?<!non-)(?<!non )small[-\s]?cell lung|\bsclc\b", re.I)),
    ("Head and neck", re.compile(r"head and neck|nasopharyn|oropharyn|hypopharyn|laryng|oral cavity|salivary|sinonasal", re.I)),
    ("CNS", re.compile(r"\bgli|brain metast|brain tumor|brain tumour|central nervous system|\bcns\b|meningiom|medulloblast|ependym", re.I)),
    ("GI", re.compile(r"esophag|gastric|stomach|pancrea|hepatocellular|\bliver\b|rectal|\bcolon\b|colorectal|\banal\b|biliary|cholangi|gastrointestinal", re.I)),
    ("Gynecologic", re.compile(r"cervix|cervical cancer|endometr|ovarian|vulvar|uterine|gynecolog", re.I)),
    ("Lymphoma", re.compile(r"lymphoma|hodgkin", re.I)),
    ("Hematologic", re.compile(r"leukemia|leukaemia|myeloma|myelodysplas", re.I)),
    ("Sarcoma", re.compile(r"sarcoma", re.I)),
    ("Skin", re.compile(r"melanoma|merkel|cutaneous|skin cancer", re.I)),
    ("Oligoprogressive disease", re.compile(r"(?<!non[-\s])\boligop", re.I)),
    ("Oligometastatic disease", re.compile(r"(?<!non[-\s])\boligom", re.I)),
    ("Polymetastatic disease", re.compile(r"(?<!non[-\s])\bpolymetast", re.I)),
    ("Mixed histology", re.compile(r"mixed histolog|multiple histolog|various histolog|any solid tumou?r|basket trial", re.I)),
]

GROUP_TAGS = [
    ("nrg", re.compile(r"\bNRG\b")),
    ("swog", re.compile(r"\bSWOG\b")),
    ("ecog", re.compile(r"ECOG-ACRIN|\bECOG\b|\bACRIN\b", re.I)),
    ("alliance", re.compile(r"\bALLIANCE\b|Alliance for Clinical Trials")),
    ("cctg", re.compile(r"\bCCTG\b|Canadian Cancer Trials Group", re.I)),
    ("cog", re.compile(r"\bCOG\b|Children'?s Oncology Group", re.I)),
    ("etctn", re.compile(r"\bETCTN\b|\bLAO-", re.I)),
    ("pep-ctn", re.compile(r"\bPEP-CTN\b|\bPEPN\d", re.I)),
]
PIPELINE_TAGS = ("eortc", "rttqa", "trog", "krog", "ctgov", "cctg")
_USELESS_ACRONYM = re.compile(r"^(?:CDR\d+|NCI-\d{4}-\d+|NCT\d{8}|UM1CA\d+)$", re.I)


def _same(left, right):
    def fold(value):
        return re.sub(r"[^A-Z0-9]", "", (value or "").upper())
    return fold(left) == fold(right) and fold(left) != ""


def _ids(pattern, blob):
    found = []
    for match in pattern.finditer(blob or ""):
        found.append(next(group for group in match.groups() if group))
    return found


def _acronym(name, protocol):
    name = (name or "").strip()
    if not name or _same(name, protocol):
        return ""
    match = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", name)
    if match:
        left, right = match.group(1).strip(), match.group(2).strip()
        if _same(right, protocol) or protocol.upper() in right.upper():
            return "" if _same(left, protocol) else left
        if _same(left, protocol) or protocol.upper() in left.upper():
            return "" if _same(right, protocol) else right
    if protocol.upper() in name.upper():
        return ""
    if len(name) > 48 or _USELESS_ACRONYM.match(name):
        return ""
    return name


def _lead_allows(trial, network):
    lead = trial.get("lead_group") or ""
    groups = " ".join(trial.get("groups") or [])
    if network == "NRG" and re.search(r"\bNRG\b|\bRTOG\b", lead, re.I):
        return True
    if network == "SWOG" and re.search(r"\bSWOG\b", lead, re.I):
        return True
    if network == "ECOG" and re.search(r"ECOG", lead, re.I):
        return True
    if re.search(r"National Cancer Institute|(?<![A-Za-z])NCI(?![A-Za-z])", lead):
        if network == "NRG" and re.search(r"\bNRG\b", groups):
            return True
        if network == "SWOG" and re.search(r"\bSWOG\b", groups):
            return True
        if network == "ECOG" and re.search(r"ECOG", groups, re.I):
            return True
    return False


def display_name(trial):
    """Protocol number first, acronym second, for NRG, SWOG, and ECOG-ACRIN."""
    blob = " ".join([trial.get("name") or "", *(trial.get("protocols") or [])])
    chosen = None
    network = None
    nrg = [f"{match.group(1).upper()}{match.group(2)}" for match in NRG_ID.finditer(blob)]
    if nrg and _lead_allows(trial, "NRG"):
        chosen = f"NRG-{nrg[0]}"
        network = "NRG"
    if chosen is None:
        swog = _ids(SWOG_ID, blob)
        if swog and _lead_allows(trial, "SWOG"):
            chosen = f"S{swog[0]}"
            network = "SWOG"
    if chosen is None:
        ecog = [item.upper() for item in ECOG_EA.findall(blob)] or [f"E{item}" for item in ECOG_E.findall(blob)]
        if ecog and _lead_allows(trial, "ECOG"):
            chosen = ecog[0].upper()
            network = "ECOG"
    if chosen is None and _lead_allows(trial, "NRG"):
        rtog = RTOG_ID.findall(blob)
        if rtog:
            chosen = f"RTOG-{rtog[0]}"
            network = "RTOG"
    if not chosen:
        return trial.get("name")
    acronym = _acronym(trial.get("name") or "", chosen)
    if acronym and network == "RTOG" and _same(acronym, chosen):
        acronym = ""
    return f"{chosen} ({acronym})" if acronym else chosen


def disease_sites(trial):
    text = " ".join([
        trial.get("title") or "",
        trial.get("population") or "",
        trial.get("ctsu_disease") or "",
        " ".join(trial.get("conditions") or []),
    ])
    sites = []
    for label, pattern in DISEASE_RULES:
        if pattern.search(text):
            sites.append(label)
    if not any(label in sites for label in ("NSCLC", "SCLC")) and re.search(r"\blung\b", text, re.I):
        sites.append("Lung, other")
    anatomic = {
        "Breast", "GU", "NSCLC", "SCLC", "Lung, other", "Head and neck", "CNS", "GI",
        "Gynecologic", "Lymphoma", "Hematologic", "Sarcoma", "Skin",
    }
    if not (set(sites) & anatomic) and re.search(r"\bsolid tumou?rs?\b", text, re.I):
        if "Mixed histology" not in sites:
            sites.append("Mixed histology")
    burden = ("Oligoprogressive disease", "Oligometastatic disease", "Polymetastatic disease")
    if not any(label in sites for label in burden) and re.search(r"(?<!non[-\s])\bmetast", text, re.I):
        sites.append("Metastatic disease")
    return sites or ["Other"]


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
            elif row.get("status") == "inaccessible":
                paper["access"] = "inaccessible"
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


def keep_prior_details(trials, prior_trials, export_studies=None, library_present=False):
    """Keep protocol briefs across a full rebuild.

    A cloud build has no protocol library, so briefs and patient materials are
    copied from the previous trial file. A brief that is still empty is filled
    from the CTSU export when that export carries one.
    """
    index = {"nct": {}, "isrctn": {}, "protocol": {}}
    if not library_present:
        for old in prior_trials or []:
            if old.get("nct"):
                index["nct"].setdefault(old["nct"], old)
            if old.get("isrctn"):
                index["isrctn"].setdefault(old["isrctn"], old)
            for protocol in old.get("protocols") or []:
                index["protocol"].setdefault(str(protocol).upper(), old)
        for trial in trials:
            old = _prior_match(index, trial)
            if not old:
                continue
            if not trial.get("brief") and old.get("brief"):
                trial["brief"] = old["brief"]
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
        if trial.get("brief"):
            trial["brief_source"] = _brief_source(trial)
            continue
        study = export_by_nct.get(trial.get("nct")) if trial.get("nct") else None
        if study is None:
            for protocol in trial.get("protocols") or []:
                study = export_by_protocol.get(str(protocol).upper())
                if study:
                    break
        copied = _copy_brief((study or {}).get("brief") or {})
        if copied:
            trial["brief"] = copied
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
        trial["disease_sites"] = disease_sites(trial)
        trial["source_tags"] = source_tags(trial)
        trial["brief_source"] = _brief_source(trial)
    return trials
