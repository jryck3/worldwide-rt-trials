#!/usr/bin/env python3
"""Search PubMed and Europe PMC for every trial in the worldwide RT tracker.

Writes one cache file per trial under .tmp/literature/cache/. Safe to rerun.
Does not edit the dashboard or download paywalled manuscripts.
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tracker.http_client import FetchError, fetch  # noqa: E402

LIT = ROOT / ".tmp" / "literature"
CACHE = LIT / "cache"
SLICES = LIT / "slices"
N_SLICES = 8
PUBMED = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:120]


def compact(trial):
    brief = trial.get("brief") or {}
    return {
        "id": trial.get("id"),
        "nct": trial.get("nct"),
        "isrctn": trial.get("isrctn"),
        "eudract": trial.get("eudract"),
        "name": trial.get("name"),
        "title": trial.get("title"),
        "protocols": trial.get("protocols") or [],
        "has_schema": bool(brief.get("schema")),
        "schema_source": brief.get("schema_source") or "",
    }


def flags(title, abstract):
    blob = f"{title or ''} {abstract or ''}".lower()
    protocol = any(phrase in blob for phrase in ("study protocol", "protocol for a", "study design and protocol", ": study protocol"))
    randomized = "random" in blob
    return protocol, randomized


def paper(pmid="", pmcid="", doi="", title="", journal="", year="", authors="", abstract="", open_access=False, source="", query=""):
    protocol_like, mentions_randomization = flags(title, abstract)
    return {
        "pmid": str(pmid or ""),
        "pmcid": str(pmcid or "").replace("PMC", "PMC") if pmcid else "",
        "doi": doi or "",
        "title": re.sub(r"\s+", " ", title or "").strip(),
        "journal": journal or "",
        "year": str(year or ""),
        "authors": authors or "",
        "abstract": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", abstract or "")).strip()[:2500],
        "open_access": bool(open_access or pmcid),
        "source": source,
        "query": query,
        "protocol_like": protocol_like,
        "mentions_randomization": mentions_randomization,
    }


def dedupe(rows):
    seen = set()
    out = []
    for row in rows:
        key = row.get("pmid") or row.get("doi") or row.get("pmcid") or row.get("title", "")[:180]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def get(url, pause):
    time.sleep(pause)
    try:
        body, status = fetch(url, timeout=90, headers={"Accept": "application/json"})
    except FetchError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    if "entrez" in url and ("<PubmedArticle" in body or body.lstrip().startswith("<?xml") or "<eSearchResult" in body or body.lstrip().startswith("<")):
        return body, None
    try:
        return json.loads(body), None
    except json.JSONDecodeError:
        return body, None


def pubmed_batch(ncts):
    term = " OR ".join(f"{nct}[All Fields]" for nct in ncts)
    search_url = PUBMED + "/esearch.fcgi?" + urlencode({
        "db": "pubmed", "retmode": "json", "retmax": "250", "term": term, "tool": "worldwide-rt-trials",
    })
    data, err = get(search_url, 0.45)
    if err or not isinstance(data, dict):
        return {}, err or "pubmed search failed"
    result = data.get("esearchresult") or {}
    if result.get("ERROR"):
        return {}, str(result.get("ERROR"))
    ids = result.get("idlist") or []
    if not ids:
        return {nct: [] for nct in ncts}, None
    fetch_url = PUBMED + "/efetch.fcgi?" + urlencode({
        "db": "pubmed", "retmode": "xml", "rettype": "abstract", "id": ",".join(ids), "tool": "worldwide-rt-trials",
    })
    xml, err = get(fetch_url, 0.45)
    if err or not isinstance(xml, str):
        return {nct: [] for nct in ncts}, err or "pubmed fetch failed"
    found = {nct: [] for nct in ncts}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return found, f"pubmed xml: {exc}"
    for article in root.findall(".//PubmedArticle"):
        raw = ET.tostring(article, encoding="unicode")
        pmid = text_of(article, ".//PMID")
        title = text_of(article, ".//ArticleTitle")
        journal = text_of(article, ".//Journal/Title")
        year = text_of(article, ".//PubDate/Year") or text_of(article, ".//PubDate/MedlineDate")[:4]
        abstract = " ".join(node.text or "" for node in article.findall(".//AbstractText"))
        authors = ", ".join(
            " ".join(filter(None, [text_of(author, "LastName"), text_of(author, "Initials")]))
            for author in article.findall(".//Author")[:6]
        )
        doi = ""
        pmcid = ""
        for node in article.findall(".//ArticleId"):
            kind = node.attrib.get("IdType")
            if kind == "doi":
                doi = (node.text or "").strip()
            elif kind == "pmc":
                pmcid = (node.text or "").strip()
        row = paper(pmid, pmcid, doi, title, journal, year, authors, abstract, bool(pmcid), "pubmed", term[:180])
        for nct in ncts:
            if nct in raw:
                found[nct].append(row)
    return found, None


def text_of(node, path):
    found = node.find(path)
    return "".join(found.itertext()).strip() if found is not None else ""


def epmc_search(query, limit=200):
    out = []
    cursor = "*"
    seen = set()
    err_last = None
    while len(out) < limit and cursor not in seen:
        seen.add(cursor)
        url = EPMC + "?" + urlencode({
            "query": query, "format": "json", "pageSize": "100", "resultType": "core",
            "cursorMark": cursor, "synonym": "false",
        })
        data, err = get(url, 0.15)
        if err or not isinstance(data, dict):
            err_last = err or "europe pmc failed"
            break
        rows = ((data.get("resultList") or {}).get("result")) or []
        if isinstance(rows, dict):
            rows = [rows]
        if not rows:
            break
        for row in rows:
            pmcid = row.get("pmcid") or ""
            out.append(paper(
                row.get("pmid"), pmcid, row.get("doi"), row.get("title"),
                row.get("journalTitle"), row.get("pubYear"), row.get("authorString"),
                row.get("abstractText"), str(row.get("isOpenAccess")).lower() == "y" or bool(pmcid),
                "europepmc", query,
            ))
        nxt = data.get("nextCursorMark")
        hit_count = int(data.get("hitCount") or 0)
        if not nxt or nxt == cursor or len(out) >= hit_count:
            break
        cursor = nxt
    return out[:limit], err_last


def creative_queries(trial):
    queries = []
    name = (trial.get("name") or "").strip()
    if name and len(name) <= 48 and not name.lower().startswith(("a study", "a phase", "a randomized", "a randomised", "an open")):
        queries.append(f'"{name}" AND (radiotherapy OR radiation OR SABR OR SBRT)')
    for protocol in trial.get("protocols") or []:
        token = protocol.strip()
        if re.search(r"\d", token) and 4 <= len(token) <= 40:
            queries.append(f'"{token}" AND (protocol OR randomised OR randomized OR radiotherapy)')
    isrctn = trial.get("isrctn")
    if isrctn:
        queries.append(isrctn)
    eudract = trial.get("eudract")
    if eudract:
        queries.append(f'"{eudract}"')
    # Preserve order while dropping duplicates.
    seen = set()
    unique = []
    for query in queries:
        if query not in seen:
            seen.add(query)
            unique.append(query)
    return unique[:3]


def search_trial(trial, pubmed_hits, pubmed_error):
    queries = []
    papers = list(pubmed_hits or [])
    errors = []
    if pubmed_error:
        errors.append(pubmed_error)
    nct = trial.get("nct")
    if nct:
        queries.append(nct)
        rows, err = epmc_search(nct)
        papers.extend(rows)
        if err:
            errors.append(err)
        if rows and not any(row["protocol_like"] for row in rows):
            extra, err = epmc_search(f'{nct} AND ("study protocol" OR "protocol for a")', limit=40)
            papers.extend(extra)
            queries.append(nct + " protocol")
            if err:
                errors.append(err)
    if not papers:
        for query in creative_queries(trial):
            queries.append(query)
            rows, err = epmc_search(query)
            papers.extend(rows)
            if err:
                errors.append(err)
            if papers:
                break
        if trial.get("nct") is None and trial.get("isrctn"):
            term = trial["isrctn"] + "[All Fields]"
            url = PUBMED + "/esearch.fcgi?" + urlencode({
                "db": "pubmed", "retmode": "json", "retmax": "20", "term": term, "tool": "worldwide-rt-trials",
            })
            data, err = get(url, 0.45)
            queries.append("pubmed:" + term)
            if err:
                errors.append(err)
            if isinstance(data, dict):
                ids = ((data.get("esearchresult") or {}).get("idlist")) or []
                if ids:
                    fetch_url = PUBMED + "/efetch.fcgi?" + urlencode({
                        "db": "pubmed", "retmode": "xml", "rettype": "abstract", "id": ",".join(ids), "tool": "worldwide-rt-trials",
                    })
                    xml, err = get(fetch_url, 0.45)
                    if err:
                        errors.append(err)
                    elif isinstance(xml, str):
                        try:
                            root = ET.fromstring(xml)
                        except ET.ParseError as exc:
                            errors.append(str(exc))
                        else:
                            for article in root.findall(".//PubmedArticle"):
                                papers.append(paper(
                                    text_of(article, ".//PMID"),
                                    next((n.text or "" for n in article.findall(".//ArticleId") if n.attrib.get("IdType") == "pmc"), ""),
                                    next((n.text or "" for n in article.findall(".//ArticleId") if n.attrib.get("IdType") == "doi"), ""),
                                    text_of(article, ".//ArticleTitle"),
                                    text_of(article, ".//Journal/Title"),
                                    text_of(article, ".//PubDate/Year"),
                                    "",
                                    " ".join(node.text or "" for node in article.findall(".//AbstractText")),
                                    False,
                                    "pubmed",
                                    term,
                                ))
    return {
        "id": trial["id"],
        "nct": nct,
        "queries": queries,
        "papers": dedupe(papers),
        "errors": errors,
        "searched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def write_status(done, total, errors):
    (LIT / "status.json").write_text(json.dumps({
        "done": done, "total": total, "errors": errors,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=1))


def main():
    LIT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    SLICES.mkdir(parents=True, exist_ok=True)
    trials = [compact(t) for t in json.loads((ROOT / "data" / "trials.json").read_text())["trials"]]
    size = math.ceil(len(trials) / N_SLICES)
    slices = [trials[i:i + size] for i in range(0, len(trials), size)]
    for index, group in enumerate(slices):
        (SLICES / f"{index:02d}.json").write_text(json.dumps(group, indent=1))
    done = 0
    errors = 0
    for index, group in enumerate(slices):
        nct_rows = [row for row in group if row.get("nct")]
        other_rows = [row for row in group if not row.get("nct")]
        for start in range(0, len(nct_rows), 20):
            batch = nct_rows[start:start + 20]
            pending = [row for row in batch if not (CACHE / f"{safe_name(row['id'])}.json").exists()]
            pubmed_hits = {}
            pubmed_error = None
            if pending:
                pubmed_hits, pubmed_error = pubmed_batch([row["nct"] for row in pending])
                if pubmed_error:
                    errors += 1
            for row in batch:
                path = CACHE / f"{safe_name(row['id'])}.json"
                if path.exists():
                    done += 1
                    continue
                record = search_trial(row, pubmed_hits.get(row["nct"]) or [], pubmed_error if row in pending else None)
                if record["errors"]:
                    errors += 1
                path.write_text(json.dumps(record, indent=1))
                done += 1
                if done % 25 == 0:
                    write_status(done, len(trials), errors)
                    print(f"{done}/{len(trials)} errors={errors}", flush=True)
        for row in other_rows:
            path = CACHE / f"{safe_name(row['id'])}.json"
            if path.exists():
                done += 1
                continue
            record = search_trial(row, [], None)
            if record["errors"]:
                errors += 1
            path.write_text(json.dumps(record, indent=1))
            done += 1
            if done % 25 == 0:
                write_status(done, len(trials), errors)
                print(f"{done}/{len(trials)} errors={errors}", flush=True)
        (SLICES / f"{index:02d}.done").write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        print(f"slice {index:02d} complete", flush=True)
        write_status(done, len(trials), errors)
    print(f"finished {done}/{len(trials)} errors={errors}", flush=True)


if __name__ == "__main__":
    main()
