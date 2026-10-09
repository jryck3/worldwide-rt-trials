"""Read study-schema pages from protocol PDFs already on file.

The CTSU project is read only. Extracted schemas are short renditions, not copies
of the protocol. Larvol poster thumbnails are not used as the source.
"""

import re
from pathlib import Path

from .pipelines.ctsu import CTSU_ROOT

SCHEMA_HEADING = re.compile(
    r"^(?:(?:study|treatment|experimental design)\s+)?schema\b"
    r"(?:\s+page\s+\d+\s+of\s+\d+)?"
    r"(?:\s*\([^)]{0,48}\))*"
    r"\s*$",
    re.I,
)
ARM_LINE = re.compile(r"^(Arm\s*[A-Z]?\d+[a-z]?)\b[:\-]?\s*(.*)$", re.I)
STEP_LINE = re.compile(r"^(Step\s+\d+[a-z]?)\b[:\-]?\s*(.*)$", re.I)
NEXT_SECTION = re.compile(r"^\d+\.0\s+[A-Z]")
SAMPLE = re.compile(r"^N\s*=\s*[\d,]+", re.I)


def _norm(value):
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _lines(text):
    kept = []
    for raw in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or len(line) == 1:
            continue
        if re.fullmatch(r"\d{1,3}", line):
            continue
        upper = line.upper()
        if upper in {"CONFIDENTIAL", "CONFIDENTIAL CONFIDENTIAL"}:
            continue
        if "CONFIDENTIAL" in upper and len(line) < 48:
            continue
        if re.match(r"^PROTOCOL\s*[–\-]\s*UPDATE", line, re.I):
            continue
        if re.match(r"^(CCTG/NCTN TRIAL:|ECOG-ACRIN|Cancer Research Group|Version Date:|NRG Oncology)\b", line, re.I):
            continue
        if line.count(".") > 8:
            continue
        kept.append(line)
    return kept


def _schema_page_indexes(reader):
    found = []
    limit = min(len(reader.pages), 30)
    for index in range(limit):
        lines = _lines(reader.pages[index].extract_text() or "")
        for line in lines:
            if SCHEMA_HEADING.match(line):
                found.append((index, line))
                break
    if not found:
        return []
    post = [item for item in found if re.search(r"post", item[1], re.I)]
    chosen = post or found
    # The later page is the current amendment when both a pre and a post schema exist.
    return [chosen[-1][0]]


def _clip(text, limit=700):
    text = re.sub(r"\s+", " ", text or "").strip(" -;")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0]


def _strat_item(line):
    low = line.lower().lstrip("•-* ").strip()
    if low.startswith(("participants", "patients", "restaging", "eligible", "key inclusion", "men with", "women with")):
        return False
    if "versus" in low or " vs " in low or " vs." in low:
        return len(line) < 200
    return len(line) < 100


def parse_schema_text(text):
    lines = _lines(text)
    body = []
    started = False
    for line in lines:
        if not started:
            if SCHEMA_HEADING.match(line):
                started = True
            continue
        if NEXT_SECTION.match(line):
            break
        body.append(line)
    if not body:
        return []
    design, strat, entry, arms, notes = [], [], [], [], []
    mode = "design"
    current = None
    for line in body:
        arm = ARM_LINE.match(line)
        step = STEP_LINE.match(line)
        if re.match(r"^Stratification\b", line, re.I):
            mode = "strat"
            rest = re.sub(r"^Stratification(?:\s+Factors)?\s*:?\s*", "", line, flags=re.I).strip()
            if rest:
                strat.append(rest.lstrip("•-* ").strip())
            continue
        if step:
            mode = "step"
            current = {"label": step.group(1).title(), "text": _clip(step.group(2))}
            arms.append(current)
            continue
        if arm:
            mode = "arm"
            current = {"label": re.sub(r"\s+", " ", arm.group(1)).title(), "text": _clip(arm.group(2))}
            arms.append(current)
            continue
        if SAMPLE.match(line):
            notes.append(line.rstrip("."))
            continue
        if line.startswith("*"):
            notes.append(line.lstrip("* ").strip())
            continue
        if mode == "strat" and not _strat_item(line):
            mode = "entry"
        if mode == "design":
            design.append(line)
        elif mode == "strat":
            strat.append(line.lstrip("•-* ").strip())
        elif mode == "entry":
            entry.append(line)
        elif current is not None:
            current["text"] = _clip((current.get("text") or "") + " " + line)
    steps = []
    if design:
        steps.append({"label": "Design", "text": _clip(" ".join(design))})
    if strat:
        steps.append({"label": "Stratify", "text": _clip(" ".join(item for item in strat if item))})
    if entry:
        steps.append({"label": "Entry", "text": _clip(" ".join(entry))})
    if arms:
        step = {"label": "Randomize", "arms": []}
        for arm in arms:
            item = {"label": arm["label"]}
            if arm.get("text"):
                item["text"] = arm["text"]
            step["arms"].append(item)
        if notes:
            step["note"] = _clip(" ".join(notes))
        steps.append(step)
    elif notes:
        steps.append({"label": "Sample size", "text": _clip(" ".join(notes))})
    blob = " ".join(step.get("text") or "" for step in steps)
    blob += " ".join(arm.get("text") or "" for step in steps for arm in step.get("arms") or [])
    if len(blob) < 80 and not any(step.get("arms") for step in steps):
        return []
    return steps


def _pdf_reader(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        return PdfReader(str(path))
    except Exception:
        return None


def schema_from_pdf(path):
    try:
        reader = _pdf_reader(path)
        if reader is None:
            return []
        indexes = _schema_page_indexes(reader)
        if not indexes:
            return []
        text = "\n".join(reader.pages[index].extract_text() or "" for index in indexes)
        return parse_schema_text(text)
    except Exception:
        return []


def _protocol_dirs():
    base = CTSU_ROOT / "protocols"
    if not base.is_dir():
        return {}
    found = {}
    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        pdfs = sorted(path for path in folder.glob("*.pdf") if path.is_file())
        if not pdfs:
            continue
        found[_norm(folder.name)] = pdfs[-1]
    return found


def _trial_keys(trial):
    keys = set()
    for protocol in trial.get("protocols") or []:
        keys.add(_norm(protocol))
        for match in re.finditer(r"[A-Z]{1,6}[-.\s]?\d{2,5}", protocol.upper()):
            keys.add(_norm(match.group(0)))
    return {key for key in keys if len(key) >= 4}


def match_protocol_pdf(trial, folders=None):
    folders = folders if folders is not None else _protocol_dirs()
    keys = _trial_keys(trial)
    for key in keys:
        if key in folders:
            return folders[key]
    for key in keys:
        for folder_key, path in folders.items():
            if len(key) >= 5 and (folder_key.endswith(key) or key.endswith(folder_key)):
                return path
    return None


def version_from_name(path):
    match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    return match.group(1) if match else ""


BRIEF_SECTIONS = ("eligibility", "pretreatment", "during", "followup", "calendar", "specimens", "amendments", "submission")


def _apply_record(trial, record):
    brief = trial.setdefault("brief", {})
    applied = False
    if record.get("schema") and not brief.get("schema"):
        brief["schema"] = record["schema"]
        brief["schema_source"] = record.get("schema_source")
        applied = True
    for key in BRIEF_SECTIONS:
        if record.get(key) and not brief.get(key):
            brief[key] = record[key]
            applied = True
    return applied


def apply_saved_schemas(trials, saved):
    for trial in trials:
        keys = [trial.get("nct"), trial.get("id"), *_trial_keys(trial)]
        for key in keys:
            record = saved.get(key or "")
            if record and (record.get("schema") or any(record.get(section) for section in BRIEF_SECTIONS)):
                _apply_record(trial, record)
                break


def schemas_from_local_protocols(trials):
    """Fill schema from a local protocol PDF when the trial does not already have one."""
    folders = _protocol_dirs()
    filled = {}
    for trial in trials:
        if (trial.get("brief") or {}).get("schema"):
            continue
        path = match_protocol_pdf(trial, folders)
        if path is None:
            continue
        steps = schema_from_pdf(path)
        if not steps:
            continue
        version = version_from_name(path)
        source = f"Protocol on file ({path.parent.name}" + (f", version {version}" if version else "") + "). Read from the schema page. Not a Larvol figure."
        record = {"schema": steps, "schema_source": source}
        _apply_record(trial, record)
        for key in [trial.get("nct"), trial.get("id"), path.parent.name, *_trial_keys(trial)]:
            if key:
                filled[key] = record
    return filled
