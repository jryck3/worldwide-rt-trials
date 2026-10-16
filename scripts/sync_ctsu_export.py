#!/usr/bin/env python3
"""Apply the published CTSU export without rebuilding other sources.

Does not use build.py --only. A missing or bad export leaves trials.json unchanged.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tracker.pipelines.ctsu import (  # noqa: E402
    STATUS_MAP, _fetch_public, _read_cached,
)
from tracker.present import broad_sites  # noqa: E402

TRIALS = ROOT / "data" / "trials.json"


def _keys(study):
    found = set()
    for value in [study.get("protocol"), study.get("id"), study.get("nct"), *(study.get("aliases") or [])]:
        if value:
            found.add(str(value).upper().replace(" ", ""))
    return found


def _index(trials):
    by_key = {}
    for trial in trials:
        values = [trial.get("nct"), trial.get("id"), *(trial.get("protocols") or [])]
        for value in values:
            if value:
                by_key.setdefault(str(value).upper().replace(" ", ""), trial)
    return by_key


def apply_export(trials, studies):
    by_key = _index(trials)
    added = 0
    updated = 0
    for study in studies:
        match = None
        for key in _keys(study):
            match = by_key.get(key)
            if match:
                break
        status = STATUS_MAP.get(study.get("ctsu_status") or study.get("status"), "unknown")
        if match is None:
            match = {
                "id": study.get("nct") or study.get("protocol"),
                "name": study.get("protocol"),
                "title": study.get("title") or "",
                "nct": study.get("nct") or "",
                "protocols": [study.get("protocol")] if study.get("protocol") else [],
                "source_pipeline": "ctsu",
                "groups": [study.get("lead")] if study.get("lead") else [],
                "accrual": {},
            }
            trials.append(match)
            for key in _keys(study):
                by_key.setdefault(key, match)
            added += 1
        else:
            updated += 1
        accrual = match.setdefault("accrual", {})
        if study.get("accrual") is not None or "accrual" in study:
            accrual["actual"] = study.get("accrual")
            accrual["target"] = study.get("target")
            accrual["quality"] = "snapshot"
            accrual["source"] = "CTSU public export"
        match["status"] = status
        if study.get("registry_status"):
            match["registry_status"] = study["registry_status"]
        if study.get("status_basis"):
            match["status_basis"] = study["status_basis"]
        if study.get("phase"):
            match["phase"] = study.get("phase_normalized") or study.get("phase")
        if study.get("rt_role"):
            match["rt_role"] = study["rt_role"]
        if study.get("cancer_types"):
            match["cancer_types"] = list(study["cancer_types"])
            reviewed = dict(match.get("reviewed") or {})
            reviewed["cancer_types"] = list(study["cancer_types"])
            if study.get("burden"):
                reviewed["burden"] = list(study["burden"])
            match["reviewed"] = reviewed
            match["disease_sites"] = broad_sites(study["cancer_types"]) or broad_sites(study.get("anatomy")) or list(match.get("disease_sites") or ["Other"])
        if study.get("burden"):
            match["disease_burden"] = list(study["burden"])
        if study.get("brief"):
            match["brief"] = study["brief"]
    return added, updated


def main():
    try:
        export = _fetch_public()
    except Exception as exc:
        cached = _read_cached()
        print(f"CTSU export was not applied: {exc}", file=sys.stderr)
        if cached is None:
            return 1
        print("Kept the last verified local trial list.", file=sys.stderr)
        return 1
    payload = json.loads(TRIALS.read_text())
    before = len(payload.get("trials") or [])
    added, updated = apply_export(payload["trials"], export.get("studies") or [])
    if added and before and (before + added) < before * 0.95:
        print("Refusing to write a smaller trial list.", file=sys.stderr)
        return 1
    from build import write_dashboard
    payload["ctsu_export"] = {
        "content_source": "ctsu-public.json",
        "snapshot_fetched_at": export.get("snapshot_fetched_at"),
        "generated_at": export.get("generated_at"),
        "protocol_library_as_of": export.get("protocol_library_as_of"),
        "record_count": export.get("record_count"),
    }
    write_dashboard(payload)
    print(f"CTSU sync updated {updated} and added {added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
