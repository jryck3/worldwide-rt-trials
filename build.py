#!/usr/bin/env python3
"""Build the worldwide radiation oncology trial tracker. Does not touch the CTSU project."""

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from tracker.acceptance import run as acceptance
from tracker.model import add_unique, apply_accrual, country_limit, set_snapshot_rate, tag_groups
from tracker.present import annotate, keep_prior_details
from tracker.pipelines import cctg, ctgov, ctsu, eortc, krog, rttqa, trog
from tracker.pipelines.ctsu import protocol_library
from tracker.store import TrialStore

ROOT = Path(__file__).resolve().parent
NOTES = ROOT / "config" / "external_notes.json"
sys.path.insert(0, str(ROOT / "scripts"))
try:
    import paper_library as paper_lib
except Exception:  # noqa: BLE001
    paper_lib = None


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
            listed = False
            for country in extra.get("countries_listed") or []:
                add_unique(trial["countries_listed"], country)
                listed = True
            if listed:
                country_limit(trial)
            if extra.get("reported"):
                apply_accrual(trial, "reported-once", reported=extra["reported"])
            for item in extra.get("disagreements") or []:
                if item not in trial["disagreements"]:
                    trial["disagreements"].append(item)


DETAIL_FIELDS = {
    "brief", "patient_materials", "publications", "registry_schema",
    "disagreements", "locations", "registry",
}
PAGE_ROWS = 50


def summary_payload(payload):
    """Keep search fields in the page and write opened-study details as static JSON."""
    shards = {}
    summaries = []
    for index, trial in enumerate(payload.get("trials") or []):
        shard = index // 200
        detail = {"id": trial.get("id")}
        summary = {}
        for key, value in trial.items():
            if key in DETAIL_FIELDS and value:
                detail[key] = value
            else:
                summary[key] = value
        summary["detail_shard"] = shard
        shards.setdefault(shard, []).append(detail)
        summaries.append(summary)
    page = dict(payload)
    page["trials"] = summaries
    page["detail_shards"] = len(shards)
    page["page_rows"] = PAGE_ROWS
    return page, shards


def write_dashboard(payload):
    page, shards = summary_payload(payload)
    template = (ROOT / "dashboard" / "template.html").read_text()
    data = json.dumps(page, ensure_ascii=False).replace("<", "\\u003c")
    html = template.replace("__DATA__", data)
    (ROOT / "dashboard" / "index.html").write_text(html)
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    detail_dir = docs / "details"
    detail_dir.mkdir(exist_ok=True)
    for shard, rows in shards.items():
        (detail_dir / f"{shard}.json").write_text(json.dumps(rows, ensure_ascii=False))
    (docs / "index.html").write_text(html)
    (docs / ".nojekyll").write_text("")
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "trials.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))


def library_stamp():
    lib = protocol_library()
    return lib.library_stamp() if lib else None


def paper_stamp():
    if paper_lib is None:
        return None
    return paper_lib.write_stamp()


def rebuild_briefs():
    """Reattach CTSU briefs and saved schemas to the published trial list without refetching sources."""
    payload = json.loads((ROOT / "data" / "trials.json").read_text())
    for trial in payload["trials"]:
        trial.pop("brief", None)
        trial.pop("registry_schema", None)
    annotate(payload["trials"], ROOT)
    payload["trials"].sort(key=lambda trial: (trial.get("name") or trial.get("title") or "").lower())
    payload["briefs_rebuilt_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload["ctsu_library"] = library_stamp() or payload.get("ctsu_library")
    payload["paper_library"] = paper_stamp()
    write_dashboard(payload)
    failed, text = acceptance(payload["trials"], ROOT / "data" / "acceptance.md")
    print(text)
    print("trials", len(payload["trials"]))
    if failed:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Ignore cached downloads")
    parser.add_argument("--only", default="", help="Comma-separated pipelines")
    parser.add_argument("--allow-partial", action="store_true", help="Allow --only to omit other sources")
    parser.add_argument("--briefs-only", action="store_true", help="Reattach CTSU protocol briefs to data/trials.json and republish")
    args = parser.parse_args()
    if args.briefs_only:
        rebuild_briefs()
        return
    wanted = {part.strip() for part in args.only.split(",") if part.strip()}
    if wanted and not args.allow_partial:
        raise SystemExit("--only would discard sources that are not named. The CTSU sync does not use --only.")
    prior_path = ROOT / "data" / "trials.json"
    saved_payload = {}
    saved_trials = []
    if prior_path.is_file():
        saved_payload = json.loads(prior_path.read_text())
        saved_trials = saved_payload.get("trials") or []
    prior_counts = {}
    for trial in saved_trials:
        name = trial.get("source_pipeline") or ""
        prior_counts[name] = prior_counts.get(name, 0) + 1
    store = TrialStore()
    log = []
    steps = [
        ("ctsu", lambda: ctsu.load(store, log)),
        ("eortc", lambda: eortc.load(store, log, ROOT / "data" / "cache" / "eortc", refresh=args.refresh)),
        ("rttqa", lambda: rttqa.load(store, log, ROOT / "data" / "cache" / "rttqa", refresh=args.refresh)),
        ("trog", lambda: trog.load(store, log, ROOT / "data" / "cache" / "trog", refresh=args.refresh)),
        ("cctg", lambda: cctg.load(store, log, ROOT / "data" / "cache" / "cctg", refresh=args.refresh)),
        ("krog", lambda: krog.load(store, log, ROOT / "data" / "cache" / "krog", refresh=args.refresh)),
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
            store.trials = [trial for trial in store.trials if trial.get("source_pipeline") != name]
            restored = 0
            for trial in saved_trials:
                if trial.get("source_pipeline") == name:
                    store.trials.append(trial)
                    restored += 1
            store.reindex()
            print(f"  {name}: kept {restored} prior rows", flush=True)
            count = restored
        previous = prior_counts.get(name, 0)
        if previous and count < previous * 0.95:
            raise SystemExit(f"{name} fell from {previous} to {count}, more than 5 percent")
    apply_notes(store)
    today = datetime.now(timezone.utc).date()
    for trial in store.trials:
        tag_groups(trial)
        country_limit(trial)
        set_snapshot_rate(trial, today)
        trial.pop("registry", None)
        trial.pop("_keep_phase", None)
    annotate(store.trials, ROOT)
    export_payload, _export_source = ctsu.load_export()
    keep_prior_details(
        store.trials,
        saved_trials,
        export_payload.get("studies"),
        library_present=protocol_library() is not None,
    )
    store.trials.sort(key=lambda trial: (trial.get("name") or trial.get("title") or "").lower())
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {
        "generated_at": generated,
        "purpose": "Phase 2, phase 2/3, and phase 3 interventional trials in which radiotherapy is randomized or protocol-specified. The file also includes every study on the CTSU list. The opening view stays the radiotherapy roles.",
        "counts": {
            "trials": len(store.trials),
            "by_pipeline": {},
            "by_quality": {},
            "by_phase": {},
        },
        "build_log": log,
        "ctsu_library": library_stamp() or saved_payload.get("ctsu_library"),
        "paper_library": paper_stamp(),
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
