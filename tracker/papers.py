"""Citation links for the study-details card.

Manuscripts are not stored here. PMC and DOI values are accepted only after
PMID conversion, because search caches have paired a correct PMID with the
wrong PMCID and DOI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MAX_SHOWN = 8


def publications_path(root):
    return Path(root) / "config" / "publications.json"


def load_publications(root):
    path = publications_path(root)
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return data if isinstance(data, dict) else {}


def save_publications(root, data):
    path = publications_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def attach_publications(trials, root):
    saved = load_publications(root)
    if not saved:
        return
    for trial in trials:
        record = saved.get(trial.get("id") or "") or saved.get(trial.get("nct") or "")
        if not record:
            continue
        papers = record.get("papers") or []
        trial["publications"] = papers
        extra = int(record.get("found") or 0) - len(papers)
        if extra > 0:
            trial["publications_more"] = extra
        else:
            trial.pop("publications_more", None)


def _token_ok(token):
    token = (token or "").strip()
    if len(token) < 5 or len(token) > 40 or not re.search(r"\d", token):
        return False
    return bool(re.search(r"[A-Za-z]", token))


def mentions_trial(paper, trial):
    blob = f"{paper.get('title') or ''} {paper.get('abstract') or ''}"
    nct = trial.get("nct") or ""
    isrctn = trial.get("isrctn") or ""
    if nct and nct in blob:
        return True
    if isrctn and isrctn in blob:
        return True
    for protocol in trial.get("protocols") or []:
        if not _token_ok(protocol):
            continue
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(protocol.strip()) + r"(?![A-Za-z0-9])", blob):
            return True
    return False


def is_protocol(paper):
    if paper.get("protocol_like"):
        return True
    blob = f"{paper.get('title') or ''} {paper.get('abstract') or ''}".lower()
    if any(phrase in blob for phrase in ("study protocol", "protocol for a", "trial protocol", "study design and protocol")):
        return True
    return bool(re.search(r"\bwill be randomi[sz]ed\b", blob))


def role_of(paper):
    if is_protocol(paper):
        return "protocol"
    title = (paper.get("title") or "").lower()
    if any(word in title for word in ("results", "outcomes", "outcome", "final analysis", "primary analysis", "survival")):
        return "results"
    return "paper"


def _year(paper):
    raw = str(paper.get("year") or "")[:4]
    return int(raw) if raw.isdigit() else 0


def _first_author(authors):
    text = re.sub(r"\s+", " ", authors or "").strip().rstrip(".")
    if not text:
        return ""
    first = text.split(",")[0].strip()
    if "," in text and first:
        return first + ", et al."
    return first


def select_papers(papers, trial):
    chosen = []
    seen = set()
    for paper in papers or []:
        pmid = str(paper.get("pmid") or "").strip()
        if not pmid.isdigit() or pmid in seen:
            continue
        if not mentions_trial(paper, trial):
            continue
        seen.add(pmid)
        chosen.append(paper)
    chosen.sort(key=lambda paper: (0 if is_protocol(paper) else 1, -_year(paper), str(paper.get("pmid") or "")))
    return chosen


def public_paper(paper, resolved):
    pmid = str(paper.get("pmid") or "").strip()
    hit = resolved.get(pmid) or {}
    pmcid = hit.get("pmcid") or ""
    doi = hit.get("doi") or ""
    row = {
        "role": role_of(paper),
        "title": re.sub(r"\s+", " ", paper.get("title") or "").strip(),
        "authors": _first_author(paper.get("authors") or ""),
        "journal": paper.get("journal") or "",
        "year": str(paper.get("year") or "")[:4],
        "pmid": pmid,
        "pubmed": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }
    if re.fullmatch(r"PMC\d+", pmcid):
        row["pmcid"] = pmcid
        row["pmc"] = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"
    if doi.startswith("10."):
        row["doi"] = doi
        row["doi_url"] = "https://doi.org/" + doi
    return row


def records_for_trial(papers, trial, resolved):
    matched = select_papers(papers, trial)
    shown = [public_paper(paper, resolved) for paper in matched[:MAX_SHOWN]]
    return {"found": len(matched), "papers": shown}


def resolve_pmids(pmids, fetch):
    """Map PMID to PMCID and DOI. fetch(url) returns (body, status)."""
    resolved = {}
    ordered = [str(pmid) for pmid in dict.fromkeys(pmids) if str(pmid).isdigit()]
    for start in range(0, len(ordered), 80):
        batch = ordered[start:start + 80]
        url = (
            "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids="
            + ",".join(batch)
            + "&format=json&tool=worldwide-rt-trials"
        )
        try:
            body, status = fetch(url)
        except Exception as exc:  # noqa: BLE001
            print(f"idconv failed for {len(batch)} pmids: {exc}", flush=True)
            continue
        if status and int(status) >= 400:
            print(f"idconv HTTP {status}", flush=True)
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            print("idconv returned non-json", flush=True)
            continue
        for row in data.get("records") or []:
            pmid = str(row.get("pmid") or "")
            if row.get("status") == "error" or not pmid.isdigit():
                continue
            resolved[pmid] = {"pmcid": row.get("pmcid") or "", "doi": row.get("doi") or ""}
        print(f"idconv {min(start + 80, len(ordered))}/{len(ordered)}", flush=True)
    return resolved
