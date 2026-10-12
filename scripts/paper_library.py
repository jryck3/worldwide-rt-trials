"""Local paper library for the Worldwide tracker.

Manuscripts stay in sources/manuscripts/, which is gitignored. This module
records what is on file, what still needs Jeff's access, and what could not
be retrieved. Other projects do not copy these files.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANUSCRIPTS = ROOT / "sources" / "manuscripts"
ACCESS = ROOT / "config" / "paper_access.json"
STAMP = ROOT / "data" / "paper_library.json"
PUBS = ROOT / "config" / "publications.json"

STUB = re.compile(
    r"does not allow downloading of the full text|"
    r"publisher will not release|"
    r"<body>\s*</body>",
    re.I,
)
MIN_XML = 8000
MIN_PDF = 20000


def _read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def load_access():
    data = _read_json(ACCESS, {})
    return data if isinstance(data, dict) else {}


def save_access(data):
    ACCESS.parent.mkdir(parents=True, exist_ok=True)
    ACCESS.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def local_paths(pmid):
    pmid = str(pmid or "")
    if not pmid.isdigit():
        return []
    found = []
    for suffix in (".pdf", ".xml"):
        path = MANUSCRIPTS / f"{pmid}{suffix}"
        if path.is_file():
            found.append(path)
    return found


def is_full_text(path):
    if not path or not path.is_file():
        return False
    size = path.stat().st_size
    if path.suffix.lower() == ".pdf":
        if size < MIN_PDF:
            return False
        head = path.read_bytes()[:8]
        return head.startswith(b"%PDF")
    if path.suffix.lower() == ".xml":
        if size < MIN_XML:
            return False
        text = path.read_text(errors="ignore")[:4000]
        return not STUB.search(text)
    return size > MIN_PDF


def current_file(pmid):
    for path in local_paths(pmid):
        if is_full_text(path):
            return path
    return None


def cited_papers():
    """Unique PMIDs from publications.json, with the first trial that cites each."""
    pubs = _read_json(PUBS, {})
    papers = {}
    for trial_id, record in pubs.items():
        for paper in record.get("papers") or []:
            pmid = str(paper.get("pmid") or "")
            if not pmid.isdigit() or pmid in papers:
                continue
            papers[pmid] = {
                "pmid": pmid,
                "trial_id": trial_id,
                "role": paper.get("role") or "paper",
                "title": paper.get("title") or "",
                "authors": paper.get("authors") or "",
                "journal": paper.get("journal") or "",
                "year": str(paper.get("year") or "")[:4],
                "doi": paper.get("doi") or "",
                "pmcid": paper.get("pmcid") or "",
                "pubmed": paper.get("pubmed") or f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "pmc": paper.get("pmc") or "",
                "doi_url": paper.get("doi_url") or "",
            }
    return papers


def status_of(paper, access=None):
    access = access if access is not None else load_access()
    pmid = paper["pmid"]
    if current_file(pmid):
        return "on_file"
    row = (access.get("papers") or {}).get(pmid) or {}
    return row.get("status") or "needs_access"


def inventory():
    access = load_access()
    papers = cited_papers()
    counts = {"on_file": 0, "needs_access": 0, "inaccessible": 0, "oa_available": 0}
    rows = []
    for paper in papers.values():
        status = status_of(paper, access)
        if status == "oa_available":
            status = "needs_access"
        counts[status] = counts.get(status, 0) + 1
        if paper.get("pmcid") and status != "on_file":
            counts["oa_available"] += 1
        rows.append({**paper, "status": status, "local": bool(current_file(paper["pmid"]))})
    return {"counts": counts, "papers": rows}


def library_stamp():
    inv = inventory()
    access = load_access()
    asked = (access.get("asked_at") or "")[:10]
    payload = {
        "as_of": asked or datetime.now(timezone.utc).date().isoformat(),
        "on_file": inv["counts"].get("on_file", 0),
        "needs_access": inv["counts"].get("needs_access", 0),
        "inaccessible": inv["counts"].get("inaccessible", 0),
        "cited": len(inv["papers"]),
    }
    raw = json.dumps(payload, sort_keys=True).encode()
    payload["hash"] = hashlib.sha256(raw).hexdigest()[:16]
    return payload


def write_stamp():
    stamp = library_stamp()
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.write_text(json.dumps(stamp, indent=1) + "\n")
    return stamp
