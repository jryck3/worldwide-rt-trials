#!/usr/bin/env python3
"""Brief merge and source-loss guards."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tracker.present import _merge_brief


def main():
    older = {"version_date": "2026-01-01", "eligibility": ["Reviewed text"]}
    newer = {"version_date": "2026-06-01", "eligibility": ["New text"], "schema": ["New schema"]}
    merged = _merge_brief(older, newer)
    assert merged["eligibility"] == ["New text"]
    pending = {"version_date": "2026-06-01", "eligibility": [{"status": "needs_review", "text": "draft"}]}
    kept = _merge_brief(older, pending)
    assert kept["eligibility"] == ["Reviewed text"]
    assert kept["review_warnings"]
    stale = _merge_brief(newer, older)
    assert stale["eligibility"] == ["New text"]
    print("brief merge ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
