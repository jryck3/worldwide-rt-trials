#!/usr/bin/env python3
"""Fill the local paper library from open sources, then write the access log.

Open PMC / Europe PMC full text is downloaded without asking. Publisher and
WVU routes are never used here. Those papers go on the access-needed list so
Jeff can open them. A paper that has already failed a legitimate open route
is logged as inaccessible.

  python3 scripts/collect_papers.py --oa --ask
  python3 scripts/collect_papers.py --ask
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from paper_library import (  # noqa: E402
    ACCESS,
    MANUSCRIPTS,
    STUB,
    cited_papers,
    current_file,
    inventory,
    is_full_text,
    load_access,
    save_access,
    write_stamp,
)
from tracker.http_client import FetchError, fetch, fetch_file  # noqa: E402

EPMC_XML = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
PMC_PDF = "https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
LOG_MD = ROOT / "logs" / "paper-access.md"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def wvu_url(doi):
    if not doi or not str(doi).startswith("10."):
        return ""
    return "https://wvu.idm.oclc.org/login?url=https://doi.org/" + doi


def record(access, paper, status, reason, route=""):
    papers = access.setdefault("papers", {})
    papers[paper["pmid"]] = {
        "pmid": paper["pmid"],
        "trial_id": paper.get("trial_id") or "",
        "role": paper.get("role") or "",
        "title": paper.get("title") or "",
        "journal": paper.get("journal") or "",
        "year": paper.get("year") or "",
        "doi": paper.get("doi") or "",
        "pmcid": paper.get("pmcid") or "",
        "pubmed": paper.get("pubmed") or "",
        "wvu_url": wvu_url(paper.get("doi") or ""),
        "status": status,
        "reason": reason,
        "route": route,
        "checked_at": now(),
    }


def save_xml(pmcid, dest):
    try:
        body, status = fetch(EPMC_XML.format(pmcid=pmcid), timeout=45, retries=2)
    except FetchError as exc:
        return False, f"Europe PMC XML HTTP {exc.status}"
    if status >= 400 or not body or STUB.search(body[:4000]):
        return False, "Europe PMC XML was missing or a publisher stub."
    if "<body" not in body and "<Body" not in body and len(body) < 8000:
        return False, "Europe PMC XML was too short to be a full article."
    dest.write_text(body)
    return is_full_text(dest), "Europe PMC full-text XML"


def save_pdf(pmcid, dest):
    try:
        status, size = fetch_file(PMC_PDF.format(pmcid=pmcid), dest, timeout=45, retries=2)
    except FetchError as exc:
        return False, f"PMC PDF HTTP {exc.status}"
    if size < 20000 or not is_full_text(dest):
        dest.unlink(missing_ok=True)
        return False, "PMC PDF was missing or too short."
    return True, f"PMC PDF HTTP {status}"


def download_oa(paper):
    pmcid = paper.get("pmcid") or ""
    if not pmcid.startswith("PMC"):
        return False, "No PMC record, so there is no open full text to download."
    MANUSCRIPTS.mkdir(parents=True, exist_ok=True)
    xml = MANUSCRIPTS / f"{paper['pmid']}.xml"
    ok, reason = save_xml(pmcid, xml)
    if ok:
        return True, reason
    xml.unlink(missing_ok=True)
    pdf = MANUSCRIPTS / f"{paper['pmid']}.pdf"
    ok, pdf_reason = save_pdf(pmcid, pdf)
    if ok:
        return True, pdf_reason
    return False, f"{reason} {pdf_reason}"


def write_markdown(access):
    papers = list((access.get("papers") or {}).values())
    needed = [p for p in papers if p.get("status") == "needs_access"]
    closed = [p for p in papers if p.get("status") == "inaccessible"]
    LOG_MD.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Worldwide paper access log",
        "",
        f"Checked {access.get('asked_at') or now()}. Open PMC text is downloaded automatically.",
        "Publisher and WVU pages are left for Jeff. This file is the log of what still needs access.",
        "",
        f"Needs access: {len(needed)}. Logged inaccessible: {len(closed)}.",
        "",
    ]
    if needed:
        lines += ["## Needs Jeff to access", ""]
        needed.sort(key=lambda p: (0 if p.get("role") == "protocol" else 1, p.get("trial_id") or "", p.get("pmid") or ""))
        for paper in needed:
            cite = ", ".join(part for part in (paper.get("journal"), paper.get("year")) if part)
            lines.append(f"- PMID {paper['pmid']}. {paper.get('trial_id') or ''} {paper.get('role') or ''}. {paper.get('title') or ''}")
            if cite:
                lines.append(f"  {cite}")
            links = [paper.get("pubmed"), paper.get("wvu_url") or (f"https://doi.org/{paper['doi']}" if paper.get("doi") else "")]
            lines.append("  " + " · ".join(link for link in links if link))
            if paper.get("reason"):
                lines.append(f"  {paper['reason']}")
            lines.append("")
    if closed:
        lines += ["## Inaccessible after an open-source attempt", ""]
        for paper in closed:
            lines.append(f"- PMID {paper['pmid']}. {paper.get('title') or ''} {paper.get('reason') or ''}")
        lines.append("")
    LOG_MD.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--oa", action="store_true", help="Download open PMC / Europe PMC full text")
    parser.add_argument("--ask", action="store_true", help="Write the access-needed log (default if neither flag)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--priority", choices=["all", "protocol", "missing"], default="all")
    args = parser.parse_args()
    if not args.oa and not args.ask:
        args.ask = True

    access = load_access()
    papers = cited_papers()
    if args.priority == "protocol":
        papers = {k: v for k, v in papers.items() if v.get("role") == "protocol"}
    elif args.priority == "missing":
        papers = {k: v for k, v in papers.items() if not current_file(k)}

    downloaded = 0
    failed = 0
    skipped = 0
    queued = 0
    for paper in papers.values():
        if args.limit and downloaded >= args.limit:
            break
        if current_file(paper["pmid"]):
            record(access, paper, "on_file", "Full text is in sources/manuscripts/.", "local")
            skipped += 1
            continue
        if args.oa and paper.get("pmcid"):
            print(f"oa {paper['pmid']} {paper.get('pmcid')}", flush=True)
            time.sleep(0.35)
            try:
                ok, reason = download_oa(paper)
            except Exception as exc:  # noqa: BLE001
                ok, reason = False, str(exc)[:200]
            if ok:
                record(access, paper, "on_file", reason, "pmc")
                downloaded += 1
                if downloaded % 10 == 0:
                    save_access(access)
                    print(f"downloaded {downloaded}", flush=True)
                continue
            record(
                access,
                paper,
                "needs_access",
                "Open PMC/Europe PMC did not yield a full article. Ask Jeff to open the publisher or WVU link. " + reason,
                "pmc",
            )
            failed += 1
            continue
        record(
            access,
            paper,
            "needs_access",
            "No open full text was downloaded. Ask Jeff to open PubMed, the DOI, or the WVU EZProxy link.",
            "",
        )
        queued += 1

    access["asked_at"] = now()
    save_access(access)
    write_markdown(access)
    stamp = write_stamp()
    inv = inventory()
    print(
        json.dumps(
            {
                "downloaded": downloaded,
                "already_local": skipped,
                "inaccessible": failed,
                "needs_access": queued,
                "library": stamp,
                "counts": inv["counts"],
                "access": str(ACCESS.relative_to(ROOT)),
                "log": str(LOG_MD.relative_to(ROOT)),
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
