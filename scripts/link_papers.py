#!/usr/bin/env python3
"""Turn the local literature cache into study-detail paper links.

Reads .tmp/literature/cache. Does not search again and does not trust cached
PMCID or DOI values. PMID conversion fills those links. Writes
config/publications.json and regenerates the dashboard from data/trials.json.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from build import write_dashboard  # noqa: E402
from tracker.http_client import fetch  # noqa: E402
from tracker.papers import (  # noqa: E402
    MAX_SHOWN,
    load_publications,
    records_for_trial,
    resolve_pmids,
    save_publications,
    select_papers,
)

CACHE = ROOT / ".tmp" / "literature" / "cache"


def trial_index(payload):
    found = {}
    for trial in payload["trials"]:
        found[trial.get("id")] = trial
        if trial.get("nct"):
            found.setdefault(trial["nct"], trial)
    return found


def fetch_idconv(url):
    time.sleep(0.34)
    return fetch(url, timeout=90, headers={"Accept": "application/json"})


def main():
    payload = json.loads((ROOT / "data" / "trials.json").read_text())
    trials = trial_index(payload)
    saved = load_publications(ROOT)
    caches = []
    pmids = []
    if not CACHE.is_dir():
        print("no literature cache")
        return
    for path in sorted(CACHE.glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        trial = trials.get(record.get("id"))
        if trial is None:
            continue
        matched = select_papers(record.get("papers") or [], trial)
        caches.append((record, trial))
        pmids.extend(paper.get("pmid") for paper in matched[:MAX_SHOWN])
    print(f"cache files {len(caches)} pmids to resolve {len(set(pmids))}", flush=True)
    resolved = resolve_pmids(pmids, fetch_idconv)
    linked = 0
    for record, trial in caches:
        built = records_for_trial(record.get("papers") or [], trial, resolved)
        key = trial.get("id")
        previous = saved.get(key) or {}
        saved[key] = {
            "checked_at": record.get("searched_at") or previous.get("checked_at") or "",
            "found": built["found"],
            "papers": built["papers"],
        }
        if record.get("errors"):
            saved[key]["errors"] = record["errors"][:4]
        if built["papers"]:
            linked += 1
    save_publications(ROOT, saved)
    for trial in payload["trials"]:
        record = saved.get(trial.get("id") or "")
        if not record:
            continue
        trial["publications"] = record.get("papers") or []
        extra = int(record.get("found") or 0) - len(trial["publications"])
        if extra > 0:
            trial["publications_more"] = extra
        else:
            trial.pop("publications_more", None)
    write_dashboard(payload)
    print(f"linked {linked} trials", flush=True)


if __name__ == "__main__":
    main()
