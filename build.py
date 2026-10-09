#!/usr/bin/env python3
"""Build the worldwide radiation oncology trial tracker. Does not touch the CTSU project."""

import argparse
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

from tracker.acceptance import run as acceptance
from tracker.model import add_unique, apply_accrual, country_limit, set_snapshot_rate, tag_groups
from tracker.present import annotate
from tracker.pipelines import cctg, ctgov, ctsu, eortc, krog, rttqa, trog
from tracker.store import TrialStore

ROOT = Path(__file__).resolve().parent
NOTES = ROOT / "config" / "external_notes.json"


def apply_notes(store):
    payload = json.loads(NOTES.read_text())
    for trial in store.trials:
        for key in (trial.get("nct"), trial.get("isrctn")):
            extra = payload.get(key or "")
            if not extra:
                continue
            for group in extra.get("groups") or []:
                add_unique(trial["groups"], group)
            for note in extra.get("notes") or []:
                add_unique(trial["accrual"]["notes"], note)
            if extra.get("reported"):
                apply_accrual(trial, "reported-once", reported=extra["reported"])
            for item in extra.get("disagreements") or []:
                if item not in trial["disagreements"]:
                    trial["disagreements"].append(item)


def write_dashboard(payload):
    template = (ROOT / "dashboard" / "template.html").read_text()
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    html = template.replace("__DATA__", data)
    (ROOT / "dashboard" / "index.html").write_text(html)
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "trials.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Ignore cached downloads")
    parser.add_argument("--only", default="", help="Comma-separated pipelines")
    args = parser.parse_args()
    wanted = {part.strip() for part in args.only.split(",") if part.strip()}
    store = TrialStore()
    log = []
    steps = [
        ("ctsu", lambda: ctsu.load(store, log)),
        ("eortc", lambda: eortc.load(store, log, ROOT / "data" / "cache" / "eortc")),
        ("rttqa", lambda: rttqa.load(store, log, ROOT / "data" / "cache" / "rttqa")),
        ("trog", lambda: trog.load(store, log, ROOT / "data" / "cache" / "trog")),
        ("cctg", lambda: cctg.load(store, log, ROOT / "data" / "cache" / "cctg")),
        ("krog", lambda: krog.load(store, log, ROOT / "data" / "cache" / "krog")),
        ("ctgov", lambda: ctgov.load(store, log, ROOT / "data" / "cache" / "ctgov", refresh=args.refresh)),
    ]
    for name, fn in steps:
        if wanted and name not in wanted:
            continue
        print(f"running {name}", flush=True)
        try:
            count = fn()
            print(f"  {name}: {count}", flush=True)
        except Exception:
            traceback.print_exc()
            log.append({"pipeline": name, "error": traceback.format_exc()})
    apply_notes(store)
    today = datetime.now(timezone.utc).date()
    for trial in store.trials:
        tag_groups(trial)
        country_limit(trial)
        set_snapshot_rate(trial, today)
        trial.pop("registry", None)
        trial.pop("_keep_phase", None)
    annotate(store.trials, ROOT)
    store.trials.sort(key=lambda trial: (trial.get("name") or trial.get("title") or "").lower())
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {
        "generated_at": generated,
        "purpose": "Phase 2, phase 2/3, and phase 3 interventional trials in which radiotherapy is randomized or protocol-specified.",
        "counts": {
            "trials": len(store.trials),
            "by_pipeline": {},
            "by_quality": {},
            "by_phase": {},
        },
        "build_log": log,
        "trials": store.trials,
    }
    for trial in store.trials:
        payload["counts"]["by_pipeline"][trial.get("source_pipeline")] = payload["counts"]["by_pipeline"].get(trial.get("source_pipeline"), 0) + 1
        quality = (trial.get("accrual") or {}).get("quality")
        payload["counts"]["by_quality"][quality] = payload["counts"]["by_quality"].get(quality, 0) + 1
        payload["counts"]["by_phase"][trial.get("phase")] = payload["counts"]["by_phase"].get(trial.get("phase"), 0) + 1
    write_dashboard(payload)
    failed, text = acceptance(store.trials, ROOT / "data" / "acceptance.md")
    (ROOT / "data" / "build_log.json").write_text(json.dumps(log, indent=1))
    print(text)
    print("trials", len(store.trials))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
