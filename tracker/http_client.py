"""HTTP via curl so macOS certificate trust is used."""

import json
import subprocess
import time
from urllib.parse import urlencode

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 (worldwide RT trial tracker; research)"


class FetchError(RuntimeError):
    def __init__(self, url, status, body):
        super().__init__(f"HTTP {status} for {url}")
        self.url = url
        self.status = status
        self.body = body


def fetch(url, timeout=90, headers=None, retries=4):
    cmd = [
        "curl", "-sS", "-L", "--compressed", "--max-time", str(timeout),
        "-A", UA, "-w", "\n__HTTP__%{http_code}",
    ]
    for key, value in (headers or {}).items():
        cmd.extend(["-H", f"{key}: {value}"])
    cmd.append(url)
    last = None
    for attempt in range(retries):
        try:
            raw = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
            continue
        text = raw.decode("utf-8", errors="replace")
        marker = text.rfind("\n__HTTP__")
        if marker < 0:
            body, status = text, 0
        else:
            body, status_s = text[:marker], text[marker + len("\n__HTTP__"):]
            status = int(status_s.strip() or "0")
        if status in (429, 500, 502, 503, 504):
            time.sleep(2.5 * (attempt + 1))
            last = FetchError(url, status, body[:300])
            continue
        if status >= 400:
            raise FetchError(url, status, body[:500])
        return body, status
    if isinstance(last, Exception):
        raise last
    raise RuntimeError(f"fetch failed for {url}")


def fetch_json(url, timeout=90, headers=None):
    extra = {"Accept": "application/json"}
    if headers:
        extra.update(headers)
    body, _status = fetch(url, timeout=timeout, headers=extra)
    return json.loads(body)


def fetch_bytes(url, timeout=90):
    body, _status = fetch(url, timeout=timeout)
    return body


def with_query(url, params):
    return url + ("&" if "?" in url else "?") + urlencode(params, doseq=True)
