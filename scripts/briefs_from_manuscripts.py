#!/usr/bin/env python3
"""Read local manuscripts into the same study brief the CTSU tracker uses.

Writes schema, eligibility, and follow-up only from sentences in the PDF.
Does not replace a brief that was already read from a full text.
Does not invent a calendar, specimen table, or image-submission rule.
"""

from __future__ import annotations

import argparse
import json
import re
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
MANUSCRIPTS = ROOT / "sources" / "manuscripts"
SCHEMAS = ROOT / "config" / "protocol_schemas.json"
NEEDED = ROOT / "outputs" / "full-text-needed.json"

HYPHEN = re.compile(r"(\w)-\s+(?=[a-z])")
SPACE = re.compile(r"\s+")
SENTENCE = re.compile(r"(?<=[.])\s+(?=[A-Z])")
GY = re.compile(r"\d+(?:\.\d+)?\s*Gy\b", re.I)
ELIG = re.compile(
    r"\beligible patients included\b|\binclusion criteria\b|\bexclusion criteria\b|"
    r"\beligibility criteria\b|\bwere eligible if\b|\bkey eligibility\b|"
    r"\bpatients were eligible\b|\bbetween the ages\b",
    re.I,
)
RANDOM = re.compile(r"\brandom", re.I)
FOLLOW = re.compile(r"\bfollow[- ]?up\b|\bfollowed (?:for|up)\b", re.I)
PRIMARY = re.compile(r"\bprimary end ?point\b|\bprimary outcome\b", re.I)
JUNK = re.compile(
    r"reprint|copyright|www\.|https?://|all rights reserved|conflict of interest|"
    r"author contributions|funding\b|acknowledg",
    re.I,
)
SECONDARY = re.compile(
    r"\b(audit|cost|quality assurance|secondary analysis|correlative|biomarker|"
    r"letter|editorial|commentary|review of)\b",
    re.I,
)
FULLTEXT_ALREADY = re.compile(r"full text was read|Read from the full text", re.I)


def clean_text(raw):
    text = raw.replace("\u00ad", "").replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = HYPHEN.sub(r"\1", text)
    text = SPACE.sub(" ", text)
    return text.strip()


def citation(sentence):
    if re.search(r"\bet al\.?", sentence, re.I):
        return True
    if re.search(r"\[\s*\d+", sentence):
        return True
    if sentence.count("(") > 2:
        return True
    return False


def sentences(text):
    parts = SENTENCE.split(text)
    kept = []
    for part in parts:
        sentence = part.strip(" ;")
        if len(sentence) < 80 or len(sentence) > 480:
            continue
        if JUNK.search(sentence) or citation(sentence):
            continue
        if sentence.count(",") > 8:
            continue
        kept.append(sentence)
    return kept


def slice_between(text, start_pattern, stop_pattern, minimum=200):
    match = re.search(start_pattern, text, re.I)
    if not match:
        return ""
    rest = text[match.end():]
    stop = re.search(stop_pattern, rest, re.I)
    chunk = rest[: stop.start()] if stop else rest[:4000]
    return chunk if len(chunk) >= minimum else ""


def pdf_text(path):
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    chunks = []
    for page in reader.pages[:12]:
        chunks.append(page.extract_text() or "")
    return clean_text("\n".join(chunks))


def xml_text(path):
    raw = path.read_text(errors="ignore")
    raw = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
    raw = re.sub(r"<style[\s\S]*?</style>", " ", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return clean_text(raw)


def manuscript_text(path):
    if path.suffix.lower() == ".xml":
        return xml_text(path)
    return pdf_text(path)


def clip(text, limit=420):
    text = SPACE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0]


def cite(row):
    first = (row.get("authors") or "").split(",")[0].strip()
    parts = first.split()
    if len(parts) >= 2 and re.fullmatch(r"[A-Z]{1,4}", parts[-1]):
        surname = parts[0]
    else:
        surname = parts[-1] if parts else ""
    journal = (row.get("journal") or "").split(":")[0].strip().rstrip(".")
    year = str(row.get("year") or "")[:4]
    pmid = str(row.get("pmid") or "")
    who = f"{surname} et al." if surname else "The manuscript"
    bits = " ".join(part for part in (journal, year) if part)
    when = f", {bits}" if bits else ""
    return f"{who}{when} (PMID {pmid}). The full text was read."


def usable(sentence):
    if re.search(r"\b(power to detect|sample size|type I error|hazard ratio)\b", sentence, re.I):
        return False
    if re.search(r"\boriginal reports\b|\bPatients and methods\b|/H\d", sentence, re.I):
        return False
    if len(re.findall(r"\b[A-Za-z]\b", sentence)) > 4:
        return False
    return True


def pick_sentences(rows, pattern, limit):
    chosen = []
    seen = set()
    for sentence in rows:
        sentence = re.sub(r"^(Patients and Methods|Methods)\s+", "", sentence, flags=re.I)
        if not usable(sentence) or not pattern.search(sentence):
            continue
        key = sentence[:80].lower()
        if key in seen:
            continue
        seen.add(key)
        chosen.append(clip(sentence))
        if len(chosen) >= limit:
            break
    return chosen


def brief_from_text(text, row):
    text = re.split(r"\bREFERENCES\b|\bReferences\b", text, maxsplit=1)[0]
    abstract = slice_between(
        text,
        r"\bAbstract\b",
        r"\b(Introduction|Keywords|Key ?points|Background and purpose)\b",
    )
    methods = slice_between(
        text,
        r"\b(Patients and methods|Materials and methods|Methods)\b",
        r"\bResults\b",
    )
    abstract_sentences = sentences(abstract or text[:2500])
    method_sentences = sentences(methods) if methods else []
    own_assignment = re.compile(
        r"\b(were|was) randomly (assigned|allocated)|"
        r"\bpatients were randomi[sz]ed\b|"
        r"\bparticipants were randomi[sz]ed\b|"
        r"\brandomi[sz]ed in a\b",
        re.I,
    )
    design = pick_sentences(
        abstract_sentences,
        re.compile(r"\b(we |this (trial|study)|patients)\b.{0,80}\b(random|phase)\b|\brandomi[sz]ed\b", re.I),
        1,
    )
    arms = pick_sentences(method_sentences or abstract_sentences, own_assignment, 2)
    doses = pick_sentences(method_sentences or abstract_sentences, GY, 2)
    eligibility = pick_sentences(method_sentences or abstract_sentences, ELIG, 4)
    follow = pick_sentences(method_sentences, FOLLOW, 2)
    if not (doses or eligibility or arms):
        return None
    schema = []
    if design:
        schema.append({"label": "Design", "text": design[0]})
    arm_items = []
    for sentence in arms:
        if design and sentence == design[0]:
            continue
        arm_items.append({"label": "Assignment", "text": sentence})
    if arm_items:
        schema.append({"label": "Randomize", "arms": arm_items[:2]})
    for sentence in doses:
        if any(sentence == step.get("text") for step in schema):
            continue
        if any(sentence == arm.get("text") for step in schema for arm in step.get("arms") or []):
            continue
        schema.append({"label": "Radiotherapy", "text": sentence})
        if sum(1 for step in schema if step.get("label") == "Radiotherapy") >= 2:
            break
    if not schema:
        return None
    record = {"schema": schema, "schema_source": cite(row)}
    used_text = {step.get("text") for step in schema}
    used_text.update(arm.get("text") for step in schema for arm in step.get("arms") or [])
    eligibility = [item for item in eligibility if item not in used_text]
    if eligibility:
        record["eligibility"] = eligibility
    if follow:
        record["followup"] = [
            {"item": "Follow-up", "window": sentence, "note": ""} for sentence in follow[:2]
        ]
    return record


def load_targets(schemas):
    needed = json.loads(NEEDED.read_text()) if NEEDED.exists() else []
    pubs = json.loads((ROOT / "config" / "publications.json").read_text())
    for trial_id, record in pubs.items():
        for paper in record.get("papers") or []:
            needed.append({
                "pmid": paper.get("pmid"),
                "trial_id": trial_id if str(trial_id).startswith("NCT") else (paper.get("nct") or trial_id),
                "paper_title": paper.get("title") or "",
                "authors": paper.get("authors") or "",
                "journal": paper.get("journal") or "",
                "year": str(paper.get("year") or "")[:4],
            })
    cited = set()
    for record in schemas.values():
        if isinstance(record, dict):
            cited.update(re.findall(r"PMID\s*(\d+)", record.get("schema_source") or ""))
    grouped = {}
    seen = set()
    for row in needed:
        pmid = str(row.get("pmid") or "")
        trial = row.get("trial_id") or ""
        key = (trial, pmid)
        if key in seen:
            continue
        seen.add(key)
        path = MANUSCRIPTS / f"{pmid}.pdf"
        if not path.exists():
            path = MANUSCRIPTS / f"{pmid}.xml"
        if not pmid.isdigit() or not trial or not path.exists() or path.stat().st_size < 8000:
            continue
        if pmid in cited:
            continue
        existing = schemas.get(trial) if isinstance(schemas.get(trial), dict) else {}
        source = (existing or {}).get("schema_source") or ""
        if FULLTEXT_ALREADY.search(source):
            continue
        grouped.setdefault(trial, []).append(row)
    return grouped


def rank(row):
    title = row.get("paper_title") or ""
    penalty = 2 if SECONDARY.search(title) else 0
    bonus = 0
    if re.search(r"\b(protocol|randomi[sz]ed trial|phase III|phase 3|results)\b", title, re.I):
        bonus = -1
    return (penalty + bonus, -int(str(row.get("year") or "0")[:4] or 0), title)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    schemas = json.loads(SCHEMAS.read_text())
    grouped = load_targets(schemas)
    written = 0
    skipped = 0
    samples = []
    for trial, rows in grouped.items():
        rows = sorted(rows, key=rank)
        record = None
        used = None
        for row in rows:
            if SECONDARY.search(row.get("paper_title") or "") and len(rows) > 1:
                continue
            path = MANUSCRIPTS / f"{row['pmid']}.pdf"
            if not path.exists():
                path = MANUSCRIPTS / f"{row['pmid']}.xml"
            try:
                text = manuscript_text(path)
            except Exception:
                continue
            record = brief_from_text(text, row)
            if record:
                used = row
                break
        if not record:
            skipped += 1
            continue
        schemas[trial] = record
        written += 1
        if len(samples) < 6:
            samples.append((trial, used.get("pmid"), record))
        if args.limit and written >= args.limit:
            break
    print(f"trials {len(grouped)} written {written} skipped {skipped}")
    for trial, pmid, record in samples:
        print("\n==", trial, pmid)
        print(json.dumps(record, indent=1, ensure_ascii=False)[:1200])
    if args.write:
        tmp = SCHEMAS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(schemas, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(SCHEMAS)
        print("saved", SCHEMAS)


if __name__ == "__main__":
    main()
