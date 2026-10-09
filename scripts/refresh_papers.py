#!/usr/bin/env python3
"""Refresh PubMed and Europe PMC links for every trial.

Designed for a weekly GitHub Action. It updates config/publications.json and
the dashboard. It does not commit manuscripts, and it does not call the CTSU
pipeline. A trial searched in the last 6 days is left alone. Trials that
errored are tried again after one day. Oldest checks run first so a timeout
still rotates through the database.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build import write_dashboard  # noqa: E402
from find_trial_papers import pubmed_batch, search_trial  # noqa: E402
from tracker.http_client import fetch  # noqa: E402
from tracker.papers import (  # noqa: E402
    MAX_SHOWN,
    load_publications,
    resolve_pmids,
    save_publications,
    select_papers,
    public_paper,
)

BUDGET_SECONDS = 4.5 * 60 * 60
FRESH = timedelta(days=6)
RETRY_ERROR = timedelta(days=1)


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def needs_search(record, now):
    checked = parse_time((record or {}).get("checked_at"))
    if checked is None:
        return True
    age = now - checked
    if (record or {}).get("errors") and age >= RETRY_ERROR:
        return True
    return age >= FRESH


def apply(payload, saved):
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


def main():
    started = time.time()
    now = datetime.now(timezone.utc)
    payload = json.loads((ROOT / "data" / "trials.json").read_text())
    saved = load_publications(ROOT)
    pending = [trial for trial in payload["trials"] if needs_search(saved.get(trial.get("id") or ""), now)]
    pending.sort(key=lambda trial: (saved.get(trial.get("id") or "") or {}).get("checked_at") or "")
    print(f"due {len(pending)} of {len(payload['trials'])}", flush=True)
    done = 0
    pmids = []
    staged = []
    nct_rows = [trial for trial in pending if trial.get("nct")]
    other_rows = [trial for trial in pending if not trial.get("nct")]

    def consume(batch, pubmed_hits, pubmed_error):
        nonlocal done
        for trial in batch:
            if time.time() - started > BUDGET_SECONDS:
                return False
            record = search_trial(trial, (pubmed_hits or {}).get(trial.get("nct")) or [], pubmed_error)
            staged.append((trial, record))
            for paper in record.get("papers") or []:
                pmid = str(paper.get("pmid") or "")
                if pmid.isdigit():
                    pmids.append(pmid)
            done += 1
            if done % 25 == 0:
                flush(saved, staged, pmids)
                print(f"searched {done}/{len(pending)}", flush=True)
                staged.clear()
                pmids.clear()
        return time.time() - started <= BUDGET_SECONDS

    for start in range(0, len(nct_rows), 20):
        if time.time() - started > BUDGET_SECONDS:
            break
        batch = nct_rows[start:start + 20]
        pubmed_hits, pubmed_error = pubmed_batch([trial["nct"] for trial in batch])
        if not consume(batch, pubmed_hits, pubmed_error):
            break
    else:
        consume(other_rows, {}, None)
    if staged:
        flush(saved, staged, pmids)
    apply(payload, saved)
    write_dashboard(payload)
    print(f"refreshed {done}", flush=True)


def fetch_idconv(url):
    time.sleep(0.34)
    return fetch(url, timeout=90, headers={"Accept": "application/json"})


def flush(saved, staged, _pmids):
    prepared = []
    needed = []
    for trial, record in staged:
        matched = select_papers(record.get("papers") or [], trial)
        prepared.append((trial, record, matched))
        needed.extend(paper.get("pmid") for paper in matched[:MAX_SHOWN])
    resolved = resolve_pmids(needed, fetch_idconv) if needed else {}
    for trial, record, matched in prepared:
        shown = [public_paper(paper, resolved) for paper in matched[:MAX_SHOWN]]
        entry = {
            "checked_at": record.get("searched_at") or "",
            "found": len(matched),
            "papers": shown,
        }
        if record.get("errors"):
            entry["errors"] = record["errors"][:4]
        saved[trial["id"]] = entry
    save_publications(ROOT, saved)


if __name__ == "__main__":
    main()
