"""Display names, disease sites, source tags, and protocol details.

Reads the CTSU protocol briefs. Does not write into the CTSU project.
"""

import json
import re
from pathlib import Path

from .pipelines.ctsu import CTSU_ROOT, load_export
from .textutil import clip

NRG_ID = re.compile(r"\bNRG[-\s]?([A-Z]{2})[-\s]?(\d{3,4})\b", re.I)
SWOG_ID = re.compile(r"\bSWOG[-\s]*S?(\d{4})\b|\bS(\d{4})\b", re.I)
ECOG_EA = re.compile(r"\b(EA\d{3,4})\b", re.I)
ECOG_E = re.compile(r"\bE(\d{4})\b", re.I)
RTOG_ID = re.compile(r"\bRTOG[-\s]?(\d{4})\b", re.I)

BRIEF_FIELDS = (
    "version_date", "posted", "amendments", "schema", "eligibility",
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
    ("Oligometastatic", re.compile(r"oligometasta|oligoprogress", re.I)),
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
    for pipe in [trial.get("source_pipeline"), *(trial.get("pipelines") or [])]:
        if pipe in PIPELINE_TAGS and pipe not in tags:
            tags.append(pipe)
    if not tags:
        tags.append(trial.get("source_pipeline") or "ctgov")
    return tags


def _norm_keys(value):
    text = (value or "").strip()
    if not text:
        return []
    keys = {text.upper(), re.sub(r"[^A-Z0-9]", "", text.upper())}
    for match in NRG_ID.finditer(text):
        keys.add(f"NRG-{match.group(1).upper()}{match.group(2)}")
    for match in SWOG_ID.finditer(text):
        number = next(group for group in match.groups() if group)
        keys.add(f"S{number}")
    for match in ECOG_EA.finditer(text):
        keys.add(match.group(1).upper())
    for match in ECOG_E.finditer(text):
        keys.add(f"E{match.group(1)}")
    for match in RTOG_ID.finditer(text):
        keys.add(f"RTOG-{match.group(1)}")
        keys.add(f"RTOG{match.group(1)}")
    return [key for key in keys if key]


def _brief_index():
    path = CTSU_ROOT / "data" / "protocol_briefs.json"
    if not path.exists():
        return {}
    briefs = json.loads(path.read_text())
    index = {}
    for protocol, brief in briefs.items():
        for key in _norm_keys(protocol):
            index.setdefault(key, brief)
    return index


def _copy_brief(brief):
    copied = {}
    for field in BRIEF_FIELDS:
        value = brief.get(field)
        if value:
            copied[field] = value
    return copied


def _attach_briefs(trials, index):
    for trial in trials:
        found = []
        seen = set()
        for protocol in trial.get("protocols") or []:
            for key in _norm_keys(protocol):
                brief = index.get(key)
                if brief and id(brief) not in seen:
                    seen.add(id(brief))
                    found.append(brief)
        if not found:
            continue
        found.sort(key=lambda brief: (bool(brief.get("schema")), len(brief.get("eligibility") or [])), reverse=True)
        copied = _copy_brief(found[0])
        if copied:
            trial["brief"] = copied


def _attach_ctsu_meta(trials):
    payload, _source = load_export()
    by_nct = {}
    by_protocol = {}
    for study in payload.get("studies") or []:
        if study.get("nct"):
            by_nct[study["nct"]] = study
        for key in _norm_keys(study.get("protocol")):
            by_protocol.setdefault(key, study)
    for trial in trials:
        study = by_nct.get(trial.get("nct"))
        if study is None:
            for protocol in trial.get("protocols") or []:
                for key in _norm_keys(protocol):
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
            step = _arm_step(study)
            if step:
                found[nct] = [step]
    for trial in trials:
        steps = found.get(trial.get("nct"))
        if steps and not (trial.get("brief") or {}).get("schema"):
            trial["registry_schema"] = steps


def annotate(trials, root):
    """Fill display fields on an existing trial list. Safe to run more than once."""
    root = Path(root)
    _attach_ctsu_meta(trials)
    _attach_briefs(trials, _brief_index())
    _attach_registry_schema(trials, root / "data" / "cache" / "ctgov")
    for trial in trials:
        trial["name"] = display_name(trial)
        trial["disease_sites"] = disease_sites(trial)
        trial["source_tags"] = source_tags(trial)
    return trials
