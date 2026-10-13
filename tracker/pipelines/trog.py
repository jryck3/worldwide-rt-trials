"""TROG trial pages. Current accrual is a snapshot only when the page fills it in."""

import json
import re
from concurrent.futures import ThreadPoolExecutor

from ..classify import classify_text, phase_language
from ..http_client import fetch
from ..model import blank_trial, norm_nct
from ..textutil import clip, html_lines, today_iso, utc_now

LISTS = [
    "https://trog.com.au/our-research/open-trials-recruiting/",
    "https://trog.com.au/our-research/open-trials-in-follow-up/",
    "https://trog.com.au/trials/",
]


def _links(html):
    found = re.findall(r"https://trog\.com\.au/trials/[a-z0-9\-]+/?", html)
    unique = []
    for url in found:
        url = url if url.endswith("/") else url + "/"
        if url not in unique and "/trials/page/" not in url:
            unique.append(url)
    return unique


def _number_after(lines, label):
    for index, line in enumerate(lines):
        if line.lower() == label.lower():
            for nxt in lines[index + 1:index + 5]:
                if re.fullmatch(r"[\d,]+", nxt):
                    return int(nxt.replace(",", ""))
                if nxt.lower() != label.lower():
                    return None
    return None


def _status(html):
    badges = re.findall(r'elementor-button-text">([^<]+)</span>', html)
    for badge in badges:
        low = badge.strip().lower()
        if low in {"log in", "cqms", "donate", "subscribe"} or "donat" in low:
            continue
        if "not yet" in low:
            return "not yet recruiting"
        if "recruit" in low:
            return "recruiting"
        if "follow" in low:
            return "closed to accrual"
        if low.startswith("closed") or low in {"complete", "completed"}:
            return "completed"
    return "unknown"


def _parse(url, html):
    lines = html_lines(html)
    title_tag = re.search(r"<title>([^<]+)</title>", html, re.I)
    name = re.sub(r"\s+-\s+TROG Cancer Research.*$", "", title_tag.group(1) if title_tag else "", flags=re.I).strip()
    heading = re.search(r"<h3[^>]*>\s*([^<]+?)\s*</h3>", html, re.I)
    trial_title = " ".join(heading.group(1).split()) if heading else name or url
    nct = None
    anzctr = None
    link = re.search(r"https://clinicaltrials\.gov/study/(NCT\d{8})", html)
    if link:
        nct = link.group(1)
    else:
        post = re.search(r'id="post-\d+"([\s\S]+)', html)
        nct = norm_nct((post.group(1) if post else "")[:80000])
    anz = re.search(r"https://www\.anzctr\.org\.au/\S+", html)
    if anz:
        anzctr = anz.group(0).split('"')[0]
    target = _number_after(lines, "Accrual Target")
    current = _number_after(lines, "Current Accrual")
    text = " ".join(lines)
    phase = phase_language(text)
    role, evidence, modality = classify_text(name + " " + text[:800], randomized=True)
    if role is None:
        role, evidence, modality = "RT-focused", ["TROG trial"], "external beam"
    protocols = []
    for match in re.finditer(r"TROG\s*\d{2}\.\d{2}", name + " " + trial_title):
        protocols.append(re.sub(r"\s+", " ", match.group(0)).upper().replace("TROG ", "TROG "))
    trial = blank_trial()
    trial["name"] = name or trial_title
    trial["title"] = trial_title or name
    trial["nct"] = nct
    trial["protocols"] = protocols or [name]
    trial["groups"] = ["TROG"]
    trial["lead_group"] = "TROG"
    trial["phase"] = phase
    trial["phase_code"] = phase
    trial["phase_in_title"] = phase
    trial["status"] = _status(html)
    trial["status_source"] = "TROG"
    trial["status_checked"] = today_iso()
    trial["rt_role"] = role
    trial["rt_evidence"] = evidence
    trial["modality"] = modality or "external beam"
    trial["population"] = clip(trial_title, 400)
    if current is not None:
        quality = "snapshot"
        actual = current
        note = "TROG trial page reports a current accrual."
    elif target is not None:
        quality = "target-only"
        actual = None
        note = "TROG trial page reports an accrual target and no current accrual."
    else:
        quality = "status-only"
        actual = None
        note = "TROG trial page does not report an accrual number."
    trial["accrual"] = {
        "quality": quality,
        "actual": actual,
        "target": target,
        "enrollment_type": None,
        "as_of": today_iso(),
        "source": "TROG trial page",
        "source_url": url,
        "reported": None,
        "rate_per_month": None,
        "notes": [note],
    }
    trial["urls"]["trog"] = url
    if nct:
        trial["urls"]["ctgov"] = f"https://clinicaltrials.gov/study/{nct}"
    elif anzctr:
        trial["urls"]["anzctr"] = anzctr
        trial["accrual"]["notes"].append("No NCT number on the TROG page. ANZCTR link kept.")
    trial["sources"] = [{"pipeline": "trog", "url": url, "checked": today_iso()}]
    trial["checked_at"] = utc_now()
    trial["sponsor"] = "Trans Tasman Radiation Oncology Group"
    return trial


def load(store, log, cache_dir, refresh=False):
    cache_dir.mkdir(parents=True, exist_ok=True)
    index_cache = cache_dir / "index_urls.json"
    if index_cache.exists() and not refresh:
        urls = json.loads(index_cache.read_text())
    else:
        urls = []
        for list_url in LISTS:
            body, _status = fetch(list_url, timeout=60)
            for url in _links(body):
                if url not in urls:
                    urls.append(url)
        index_cache.write_text(json.dumps(urls))
    page_dir = cache_dir / "pages"
    page_dir.mkdir(exist_ok=True)

    def one(url):
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        path = page_dir / f"{slug}.html"
        if refresh or not path.exists():
            body, _status = fetch(url, timeout=60)
            path.write_text(body)
        return _parse(url, path.read_text(errors="replace"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(one, urls))
    for record in records:
        store.upsert(record, "trog")
    log.append({"pipeline": "trog", "pages": len(records)})
    return len(records)
