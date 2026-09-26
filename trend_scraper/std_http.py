"""std_http.py — the HTTP helper the audio sources use.

Uses `requests` when present and falls back to urllib when it is not, so a slim
container image (where requests isn't installed) degrades to a slower fetch
instead of raising ModuleNotFoundError inside a request handler. That failure
mode shipped once already: /v1/song returned 500 "No module named 'requests'".
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0 Safari/537.36")

try:                                    # preferred
    import requests as _requests
except Exception:                       # noqa: BLE001
    _requests = None


class Response:
    __slots__ = ("status_code", "text", "_body")

    def __init__(self, status_code: int, text: str, body: bytes = b""):
        self.status_code = status_code
        self.text = text
        self._body = body

    def json(self):
        try:
            return json.loads(self.text)
        except Exception:               # noqa: BLE001
            return {}

    @property
    def content(self) -> bytes:
        return self._body


def get(url: str, params: dict | None = None, timeout: int = 60,
        headers: dict | None = None) -> Response:
    """GET returning a requests-like Response. Never raises on HTTP status."""
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urllib.parse.urlencode(params)}"

    if _requests is not None:
        r = _requests.get(url, headers=hdrs, timeout=timeout)
        return Response(r.status_code, r.text, r.content)

    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return Response(resp.status, body.decode("utf-8", "replace"), body)
    except urllib.error.HTTPError as exc:      # 4xx/5xx still return a response
        body = exc.read() or b""
        return Response(exc.code, body.decode("utf-8", "replace"), body)
    except Exception as exc:                   # noqa: BLE001
        return Response(0, f'{{"error": "{exc}"}}')


def available() -> bool:
    """True when the fast path (requests) is installed."""
    return _requests is not None