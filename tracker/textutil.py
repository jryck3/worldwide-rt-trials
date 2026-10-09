"""Shared text and date helpers."""

import html
import re
from datetime import datetime, timezone


def html_to_text(raw):
    raw = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
    raw = re.sub(r"<style[\s\S]*?</style>", " ", raw, flags=re.I)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    raw = re.sub(r"</(p|div|h1|h2|h3|li|tr|td|th)>", "\n", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(text)


def html_lines(raw):
    lines = []
    for line in html_to_text(raw).splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        if clean:
            lines.append(clean)
    return lines


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_iso():
    return datetime.now(timezone.utc).date().isoformat()


def clip(text, limit=500):
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
