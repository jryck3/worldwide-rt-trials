"""KROG protocol board. Enrolled and target counts are a snapshot. Enrollment is in Korea."""

import json
import re
from concurrent.futures import ThreadPoolExecutor

from ..classify import classify_text
from ..http_client import fetch
from ..model import blank_trial, norm_nct
from ..textutil import today_iso, utc_now

BOARD = "http://www.krog.or.kr/ing/bbs/board.php?bo_table=protocols"


def _phase(title):
    if re.search(r"2\s*/\s*3|II\s*/\s*III", title, re.I):
        return "2/3"
    if re.search(r"제\s*3|3\s*상|III", title):
        return "3"
    if re.search(r"제\s*2|2\s*상|II", title):
        return "2"
    if re.search(r"제\s*1|1\s*상", title):
        return "1"
    return None


def _pages(cache_dir, refresh=False):
    cache = cache_dir / "board.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    seen = {}
    for suffix in ("", "&sca=진행", "&sca=종료"):
        for page in range(1, 8):
            url = f"{BOARD}&page={page}{suffix}"
            try:
                html, status = fetch(url, timeout=40)
            except Exception:
                break
            if status >= 400 or "data-table" not in html:
                break
            found = 0
            for table in re.findall(r'<table data-table="([^"]+)"([\s\S]*?)</table>', html):
                label, body = table
                found += 1
                wr = re.search(r"wr_id=(\d+)", body)
                title = ""
                cells = re.findall(r"<td[^>]*>([\s\S]*?)</td>", body)
                texts = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell)).strip() for cell in cells]
                texts = [t for t in texts if t]
                if len(texts) >= 2:
                    title = texts[1]
                accrual = re.search(r"(\d+)\s*명\s*/\s*(\d+)\s*명", body)
                key = wr.group(1) if wr else label
                row = seen.setdefault(key, {
                    "wr_id": wr.group(1) if wr else None,
                    "label": html_unescape(label),
                    "title": title,
                    "enrolled": int(accrual.group(1)) if accrual else None,
                    "target": int(accrual.group(2)) if accrual else None,
                    "lists": [],
                })
                kind = "closed" if "종료" in suffix else "open" if "진행" in suffix else "board"
                if kind not in row["lists"]:
                    row["lists"].append(kind)
            if found == 0:
                break
    cache.write_text(json.dumps(list(seen.values()), ensure_ascii=False))
    return list(seen.values())


def html_unescape(text):
    return text.replace("&amp;", "&").replace("&#039;", "'")


def _nct(wr_id, cache_dir, refresh=False):
    path = cache_dir / "details" / f"{wr_id}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    if refresh or not path.exists():
        html, _status = fetch(f"{BOARD}&wr_id={wr_id}", timeout=40)
        path.write_text(html)
    return norm_nct(path.read_text(errors="replace"))


def load(store, log, cache_dir, refresh=False):
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows = _pages(cache_dir, refresh=refresh)
    kept = []
    for row in rows:
        phase = _phase(row.get("title") or "")
        title = row.get("title") or ""
        retrospective = "후향" in title or ("관찰" in title and "무작위" not in title)
        if phase not in {"2", "2/3", "3"} and not (phase is None and "무작위" in title and not retrospective):
            continue
        if retrospective and phase not in {"2", "2/3", "3"}:
            continue
        kept.append(row)

    def attach(row):
        if row.get("wr_id"):
            try:
                row["nct"] = _nct(row["wr_id"], cache_dir, refresh=refresh)
            except Exception:
                row["nct"] = None
        return row

    with ThreadPoolExecutor(max_workers=4) as pool:
        kept = list(pool.map(attach, kept))

    for row in kept:
        label = row.get("label") or ""
        found = re.search(r"KROG\s*(\d{2})\s*[- ]\s*(\d{2})", label, re.I)
        if found:
            protocol = f"KROG {found.group(1)}-{found.group(2)}"
        else:
            protocol = re.sub(r"\s+", " ", re.sub(r"^\[완료\]\s*", "", label)).strip()
        title = row.get("title") or protocol
        phase = _phase(title)
        if "[완료]" in label or "closed" in row.get("lists", []):
            status = "completed" if "[완료]" in label else "closed to accrual"
        else:
            status = "recruiting"
        role, evidence, modality = classify_text(title, randomized="무작위" in title)
        if role is None:
            role, evidence, modality = "RT-focused", ["KROG protocol"], "external beam"
        target = row.get("target")
        if target == 0:
            target = None
        record = blank_trial()
        record["name"] = protocol
        record["title"] = title
        record["nct"] = row.get("nct")
        record["protocols"] = [protocol]
        record["groups"] = ["KROG"]
        record["lead_group"] = "KROG"
        record["phase"] = phase
        record["phase_code"] = phase
        record["status"] = status
        record["status_source"] = "KROG protocol board"
        record["status_checked"] = today_iso()
        record["population"] = title
        record["rt_role"] = role
        record["rt_evidence"] = evidence
        record["modality"] = modality or "external beam"
        record["countries_listed"] = ["Korea"]
        record["countries_open"] = ["Korea"] if status == "recruiting" else []
        record["country_limit"] = "KROG enrollment is in Korea."
        record["accrual"] = {
            "quality": "snapshot" if row.get("enrolled") is not None else "status-only",
            "actual": row.get("enrolled"),
            "target": target,
            "enrollment_type": None,
            "as_of": today_iso(),
            "source": "KROG protocol board",
            "source_url": f"{BOARD}&wr_id={row.get('wr_id')}" if row.get("wr_id") else BOARD,
            "reported": None,
            "rate_per_month": None,
            "notes": ["Enrollment figures on the KROG board are for Korea."],
        }
        record["urls"]["krog"] = record["accrual"]["source_url"]
        if record["nct"]:
            record["urls"]["ctgov"] = f"https://clinicaltrials.gov/study/{record['nct']}"
        record["sources"] = [{"pipeline": "krog", "url": BOARD, "checked": today_iso()}]
        record["checked_at"] = utc_now()
        record["sponsor"] = "Korean Radiation Oncology Group"
        store.upsert(record, "krog")
    log.append({"pipeline": "krog", "board_rows": len(rows), "kept": len(kept)})
    return len(kept)
