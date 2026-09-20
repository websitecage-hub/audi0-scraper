"""Fetchers wrapping Scrapling: a fast static HTTP fetcher and a stealth browser fetcher."""
from __future__ import annotations

import logging
import time

from scrapling import Fetcher, StealthyFetcher

log = logging.getLogger("trend.audio.fetcher")


class HTTPFetcher:
    """Fast curl_cffi-based fetcher (browser impersonation, no JS).

    Good for static blog/aggregator pages. 403/blocked pages are the caller's problem
    (the engine falls back to the stealth browser or Wayback).
    """

    def __init__(self, impersonate: str = "chrome", timeout: int = 45, delay: float = 0.6):
        self.impersonate = impersonate
        self.timeout = timeout
        self.delay = delay

    def fetch(self, url: str, retries: int = 2) -> tuple[int | None, bytes]:
        last_status, last_body = None, b""
        for attempt in range(retries + 1):
            try:
                resp = Fetcher.get(url, impersonate=self.impersonate, timeout=self.timeout)
                body = resp.body
                last_status, last_body = resp.status, body
                if resp.status and 200 <= resp.status < 300:
                    return resp.status, body
            except Exception as exc:  # network / TLS / parse errors
                log.debug("HTTP fetch error %s (attempt %d): %s", url, attempt, exc)
            time.sleep(self.delay)
        return last_status, last_body


class StealthFetcher:
    """Stealthy headless browser (patchright) for JS-rendered pages and bot-walled sites.

    Slower than HTTPFetcher but passes most anti-bot (Cloudflare, searches...).
    """

    def __init__(self, headless: bool = True, network_idle: bool = True, delay: float = 1.0):
        self.headless = headless
        self.network_idle = network_idle
        self.delay = delay

    def fetch(self, url: str, retries: int = 2) -> tuple[int | None, bytes]:
        last_status, last_body = None, b""
        for attempt in range(retries + 1):
            try:
                resp = StealthyFetcher.fetch(
                    url, headless=self.headless, network_idle=self.network_idle
                )
                last_status, last_body = resp.status, resp.body
                if resp.status and 200 <= resp.status < 300:
                    return resp.status, resp.body
            except Exception as exc:
                log.debug("Stealth fetch error %s (attempt %d): %s", url, attempt, exc)
            time.sleep(self.delay)
        return last_status, last_body
