"""Apply saved schemas and brief sections from config/protocol_schemas.json.

Those records were read from open manuscripts and protocol papers. CTSU
protocol schemas come from the CTSU protocol briefs, not from here, and this
module does not read protocol PDFs.
"""

import re


def _norm(value):
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _trial_keys(trial):
    keys = set()
    for protocol in trial.get("protocols") or []:
        keys.add(_norm(protocol))
        for match in re.finditer(r"[A-Z]{1,6}[-.\s]?\d{2,5}", protocol.upper()):
            keys.add(_norm(match.group(0)))
    return {key for key in keys if len(key) >= 4}


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
