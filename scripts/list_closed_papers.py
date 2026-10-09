#!/usr/bin/env python3
"""List papers whose full text is not available in this workspace.

Writes outputs/full-text-needed.csv and outputs/full-text-needed.json.
A publisher stub or a missing PMC record is not treated as a read paper.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".tmp" / "literature" / "work"
MANU = ROOT / "sources" / "manuscripts"
OUT = ROOT / "outputs"
STUB = "does not allow downloading of the full text"
PMID_IN_XML = re.compile(r'pub-id-type="pmid">(\d+)')


def load_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text())


def remember(papers, pmid, meta):
    pmid = str(pmid or "")
    if not pmid.isdigit():
        return
    current = papers.setdefault(pmid, {})
    for key, value in meta.items():
        if value and not current.get(key):
            current[key] = value


def paper_meta(row, trial=None):
    trial = trial or {}
    title = row.get("title") or ""
    hits = row.get("hits")
    if not title and isinstance(hits, list) and hits and isinstance(hits[0], dict):
        title = hits[0].get("title") or ""
    return {
        "nct": row.get("nct") or trial.get("nct") or row.get("id") or "",
        "name": row.get("name") or trial.get("name") or "",
        "trial": row.get("trial_title") or trial.get("trial_title") or "",
        "title": title,
        "journal": row.get("journal") or "",
        "year": str(row.get("year") or "")[:4],
        "authors": row.get("authors") or "",
    }


def collect_papers():
    papers = {}
    chosen = load_json(WORK / "chosen_01.json") or []
    for row in chosen:
        remember(papers, row.get("pmid"), paper_meta(row))
    for name in ("00-candidates.json", "00-new.json", "00-prot.json"):
        for row in load_json(WORK / name) or []:
            remember(papers, row.get("pmid"), paper_meta(row))
    for row in load_json(WORK / "select_01.json") or []:
        for paper in row.get("papers") or []:
            remember(papers, paper.get("pmid"), paper_meta(paper, row))
    publications = load_json(ROOT / "config" / "publications.json") or {}
    for trial_id, record in publications.items():
        for paper in record.get("papers") or []:
            remember(papers, paper.get("pmid"), {
                "nct": trial_id if str(trial_id).startswith("NCT") else "",
                "name": "",
                "trial": "",
                "title": paper.get("title") or "",
                "journal": paper.get("journal") or "",
                "year": str(paper.get("year") or "")[:4],
                "authors": paper.get("authors") or "",
                "doi": paper.get("doi") or "",
                "pmcid": paper.get("pmcid") or "",
            })
    return papers


def idconv_index():
    by_pmid = {}
    for path in sorted(WORK.glob("idconv_*.json")):
        data = load_json(path) or {}
        for row in data.get("records") or []:
            pmid = str(row.get("pmid") or row.get("requested-id") or "")
            if pmid.isdigit():
                by_pmid[pmid] = row
    for path in sorted(WORK.glob("[0-9]*-idconv.json")):
        data = load_json(path) or {}
        if not isinstance(data, dict) or "records" in data:
            continue
        for pmid, row in data.items():
            pmid = str(pmid)
            if pmid.isdigit() and isinstance(row, dict) and pmid not in by_pmid:
                by_pmid[pmid] = row
    return by_pmid


def load_target_meta(papers):
    for path in sorted(WORK.glob("*-targets.json")):
        data = load_json(path) or {}
        for row in data.get("rows") or []:
            trial = {"nct": row.get("nct") or "", "name": row.get("name") or ""}
            for paper in row.get("matched") or []:
                remember(papers, paper.get("pmid"), paper_meta(paper, trial))


def stub_pmids():
    found = {}
    if not MANU.is_dir():
        return found
    for path in MANU.glob("*.xml"):
        text = path.read_text(errors="ignore")[:4000]
        if STUB not in text:
            continue
        match = PMID_IN_XML.search(text)
        name_match = re.match(r"(\d+)", path.name)
        pmid = match.group(1) if match else (name_match.group(1) if name_match else "")
        if pmid:
            found[pmid] = path.name
    return found


def trial_names():
    payload = load_json(ROOT / "data" / "trials.json") or {}
    names = {}
    for trial in payload.get("trials") or []:
        label = trial.get("name") or ""
        title = trial.get("title") or ""
        for key in (trial.get("id"), trial.get("nct")):
            if key:
                names[key] = (label, title)
    return names


def fill_from_cache(papers, needed):
    cache = ROOT / ".tmp" / "literature" / "cache"
    if not cache.is_dir() or not needed:
        return
    for path in cache.glob("*.json"):
        if not needed:
            break
        try:
            record = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        trial_id = record.get("id") or ""
        nct = record.get("nct") or ""
        for paper in record.get("papers") or []:
            pmid = str(paper.get("pmid") or "")
            if pmid not in needed:
                continue
            remember(papers, pmid, {
                "nct": nct or trial_id,
                "name": "",
                "trial": "",
                "title": paper.get("title") or "",
                "journal": paper.get("journal") or "",
                "year": str(paper.get("year") or "")[:4],
                "authors": paper.get("authors") or "",
            })
            needed.discard(pmid)


def main():
    papers = collect_papers()
    load_target_meta(papers)
    idconv = idconv_index()
    stubs = stub_pmids()
    names = trial_names()
    rows = []
    seen = set()

    def add(pmid, reason, pmcid="", doi=""):
        if pmid in seen:
            return
        seen.add(pmid)
        meta = papers.get(pmid, {})
        nct = meta.get("nct") or ""
        label, trial_title = names.get(nct) or names.get(meta.get("name") or "") or ("", "")
        doi = doi if str(doi).startswith("10.") else (meta.get("doi") or "")
        doi = doi if str(doi).startswith("10.") else ""
        pmcid = pmcid if str(pmcid).startswith("PMC") else (meta.get("pmcid") or "")
        pmcid = pmcid if str(pmcid).startswith("PMC") else ""
        rows.append({
            "trial_id": nct,
            "trial_name": label or meta.get("name") or "",
            "trial_title": trial_title or meta.get("trial") or "",
            "paper_title": meta.get("title") or "",
            "authors": meta.get("authors") or "",
            "journal": meta.get("journal") or "",
            "year": meta.get("year") or "",
            "pmid": pmid,
            "pmcid": pmcid,
            "doi": doi,
            "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "doi_url": f"https://doi.org/{doi}" if doi else "",
            "wvu_url": f"https://wvu.idm.oclc.org/login?url=https://doi.org/{doi}" if doi else "",
            "pmc_url": f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/" if pmcid else "",
            "reason": reason,
        })

    for pmid, row in idconv.items():
        if row.get("status") == "error" or not row.get("pmcid"):
            add(pmid, "No PMC record. There is no open full text to download.", "", row.get("doi") or "")
    for pmid in stubs:
        row = idconv.get(pmid, {})
        add(
            pmid,
            "Publisher will not release the full text in XML. The saved file is front matter only.",
            row.get("pmcid") or "",
            row.get("doi") or "",
        )
    abstract_idconv = load_json(WORK / "abstract_idconv.json") or {}
    abstract_trials = (load_json(WORK / "abstract_pmids.json") or {}).get("trial") or {}
    for pmid, row in abstract_idconv.items():
        if row.get("status") == "error" or not row.get("pmcid"):
            if abstract_trials.get(pmid):
                remember(papers, pmid, {"nct": abstract_trials[pmid]})
            add(pmid, "The tracker note came from the abstract. There is no PMC full text.", "", row.get("doi") or "")
    paywall_dir = ROOT / ".tmp" / "literature" / "paywall"
    paywall_files = sorted(paywall_dir.glob("*.json")) if paywall_dir.is_dir() else []
    for path in paywall_files:
        for row in load_json(path) or []:
            if not isinstance(row, dict):
                continue
            pmid = str(row.get("pmid") or "")
            remember(papers, pmid, {
                "nct": row.get("nct") or "",
                "name": row.get("name") or "",
                "title": row.get("title") or "",
                "journal": row.get("journal") or "",
                "year": str(row.get("year") or "")[:4],
                "doi": row.get("doi") or "",
                "pmcid": row.get("pmcid") or "",
            })
            reason_src = (row.get("reason") or "").lower()
            if "does not allow" in reason_src or "pdf-only" in reason_src or "pdf only" in reason_src:
                reason = "Publisher will not release the full text in XML. The saved file is front matter only."
            elif not pmid.isdigit():
                reason = "No PMID was stored, so there is no open full text to download."
            else:
                reason = "No PMC record. There is no open full text to download."
            if pmid.isdigit():
                add(pmid, reason, row.get("pmcid") or "", row.get("doi") or "")
                continue
            marker = "nopmid:" + (row.get("nct") or row.get("id") or "") + ":" + (row.get("title") or "")
            if marker in seen:
                continue
            seen.add(marker)
            nct = row.get("nct") or ""
            label, trial_title = names.get(nct) or ("", "")
            doi = row.get("doi") or ""
            doi = doi if str(doi).startswith("10.") else ""
            query = nct or row.get("name") or ""
            rows.append({
                "trial_id": nct,
                "trial_name": label or row.get("name") or "",
                "trial_title": trial_title,
                "paper_title": row.get("title") or "",
                "authors": row.get("authors") or "",
                "journal": row.get("journal") or "",
                "year": str(row.get("year") or "")[:4],
                "pmid": "",
                "pmcid": "",
                "doi": doi,
                "pubmed_url": "https://pubmed.ncbi.nlm.nih.gov/?term=" + query.replace(" ", "+") if query else "",
                "doi_url": f"https://doi.org/{doi}" if doi else "",
                "wvu_url": f"https://wvu.idm.oclc.org/login?url=https://doi.org/{doi}" if doi else "",
                "pmc_url": "",
                "reason": reason,
            })
    for path in sorted(WORK.glob("*-download-status.json")):
        data = load_json(path) or {}
        if not isinstance(data, dict):
            continue
        for pmid, item in data.items():
            pmid = str(pmid)
            if not pmid.isdigit() or not isinstance(item, dict) or not item.get("paywalled"):
                continue
            conv = idconv.get(pmid) or {}
            pmcid = item.get("pmcid") or conv.get("pmcid") or ""
            if item.get("reason") == "no pmcid from idconv" or not str(pmcid).startswith("PMC"):
                reason = "No PMC record. There is no open full text to download."
            else:
                reason = "Publisher will not release the full text in XML. The saved file is front matter only."
            add(pmid, reason, pmcid, conv.get("doi") or "")
    previous = {row.get("pmid"): row for row in (load_json(OUT / "full-text-needed.json") or []) if row.get("pmid")}

    missing = {row["pmid"] for row in rows if row["pmid"] and (not row["paper_title"] or not row["trial_id"])}
    fill_from_cache(papers, set(missing))
    by_pmid = {row["pmid"]: row for row in rows}
    for item in rows:
        meta = papers.get(item["pmid"]) or {}
        for key, field in (
            ("title", "paper_title"),
            ("journal", "journal"),
            ("year", "year"),
            ("authors", "authors"),
            ("nct", "trial_id"),
        ):
            if meta.get(key) and not item.get(field):
                item[field] = meta[key]
        if item["trial_id"] and not item["trial_name"]:
            label, title = names.get(item["trial_id"]) or ("", "")
            item["trial_name"] = label
            item["trial_title"] = item["trial_title"] or title
        prior = previous.get(item["pmid"]) or {}
        for field in ("paper_title", "authors", "journal", "year", "doi", "doi_url", "wvu_url", "trial_name", "trial_title", "trial_id"):
            if prior.get(field) and not item.get(field):
                item[field] = prior[field]
        if item.get("doi") and not item.get("doi_url"):
            item["doi_url"] = "https://doi.org/" + item["doi"]
            item["wvu_url"] = "https://wvu.idm.oclc.org/login?url=" + item["doi_url"]

    if not any(row.get("trial_id") == "NCT05448547" for row in rows):
        rows.append({
            "trial_id": "NCT05448547",
            "trial_name": "GrandP/SPCG19",
            "trial_title": "",
            "paper_title": "SPCG 19/GRand-P protocol abstract, BJU International, 2024. No PMID was stored with the tracker note.",
            "authors": "",
            "journal": "BJU International",
            "year": "2024",
            "pmid": "",
            "pmcid": "",
            "doi": "",
            "pubmed_url": "https://pubmed.ncbi.nlm.nih.gov/?term=GRand-P+SPCG",
            "doi_url": "",
            "wvu_url": "",
            "pmc_url": "",
            "reason": "The tracker note came from the abstract. The full text was not available, and no PMID was stored.",
        })

    rows.sort(key=lambda row: (
        0 if row["reason"].startswith("Publisher") else 1,
        (row["trial_name"] or row["trial_id"] or "zzz").lower(),
        row["year"],
        row["pmid"],
    ))
    OUT.mkdir(parents=True, exist_ok=True)
    fields = [
        "trial_id", "trial_name", "trial_title", "paper_title", "authors", "journal", "year",
        "pmid", "pmcid", "doi", "pubmed_url", "doi_url", "wvu_url", "pmc_url", "reason",
    ]
    with (OUT / "full-text-needed.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (OUT / "full-text-needed.json").write_text(json.dumps(rows, indent=1) + "\n")
    publisher = sum(1 for row in rows if row["reason"].startswith("Publisher"))
    print(f"closed {len(rows)} publisher {publisher} no_pmc {len(rows) - publisher}")


if __name__ == "__main__":
    main()
