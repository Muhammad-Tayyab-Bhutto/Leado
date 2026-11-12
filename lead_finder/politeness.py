"""Polite, transparent crawling primitives.

Replaces the old anti-detection module. The crawler identifies itself honestly,
never rotates user agents or proxies to evade blocks, rate-limits every request,
and treats CAPTCHAs / 403 / 429 as a signal to back off and skip the site.

The public names match the old module so existing callers keep working, but
every "evasion" knob is now a no-op by design.
"""
import os
import random
import re
import threading
import time
from typing import Any, Dict, List, Optional

from .config import UA as _CONFIG_UA

BOT_UA = os.environ.get("CRAWLER_USER_AGENT", _CONFIG_UA)
USER_AGENTS: List[str] = [BOT_UA]


class UserAgentManager:
    """Always returns the same honest, identifiable user agent."""
    def __init__(self, agents: List[str] = None):
        self.agents = [BOT_UA]

    def get_agent(self) -> str: return BOT_UA
    def get_random_agent(self) -> str: return BOT_UA
    def get_agent_for_browser(self, browser: str = "chrome") -> str: return BOT_UA
    def reset(self): pass


ua_manager = UserAgentManager()


class ProxyConfig:
    """Proxy rotation is intentionally disabled: we never hide who is crawling."""
    def __init__(self):
        self.enabled = False
        self.proxies: List[Any] = []
        self.current_index = 0
        self.rotation_strategy = "disabled"

    def get_proxy(self): return None
    def get_proxy_url(self) -> Optional[str]: return None
    def get_proxy_dict(self) -> Optional[Dict[str, str]]: return None
    def add_proxy(self, *a, **k): raise RuntimeError("Proxy rotation is disabled in this build (polite crawling only).")
    def load_from_file(self, file_path: str) -> int: return 0
    def mark_proxy_failed(self, *a, **k): pass
    def mark_proxy_working(self, *a, **k): pass


Proxy = dict
proxy_manager = ProxyConfig()


class RateLimiter:
    """Global politeness delay between outbound requests (default >= 2 s)."""
    def __init__(self, min_interval: float = None):
        self.min_interval = float(min_interval or os.environ.get("CRAWL_MIN_INTERVAL", "2.0"))
        self._last = 0.0
        self._lock = threading.Lock()
        self.requests = self.successes = self.failures = 0
        self.backoff = 0.0

    def wait(self, *_a, **_k):
        with self._lock:
            gap = self.min_interval + self.backoff - (time.time() - self._last)
            if gap > 0: time.sleep(gap)
            self._last = time.time()
            self.requests += 1

    def record(self, success: bool):
        if success:
            self.successes += 1; self.backoff = max(0.0, self.backoff / 2)
        else:
            self.failures += 1; self.backoff = min(60.0, (self.backoff or self.min_interval) * 2)

    def get_stats(self) -> Dict[str, Any]:
        return {"requests": self.requests, "successes": self.successes, "failures": self.failures,
                "min_interval_s": self.min_interval, "backoff_s": round(self.backoff, 2)}


rate_limiter = RateLimiter()


class HeaderManager:
    """Plain, honest request headers. No spoofed browser fingerprints or referrers."""
    def get_headers(self, user_agent: str = None, referrer: str = None) -> Dict[str, str]:
        return {"User-Agent": BOT_UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en"}

    def get_headers_for_url(self, url: str, user_agent: str = None) -> Dict[str, str]:
        return self.get_headers()


header_manager = HeaderManager()


class BehaviorSimulator:
    """Human-behaviour simulation removed; only a polite fixed delay remains."""
    def random_delay(self, base: float = 0.5, variance: float = 0.5) -> float:
        d = base + random.uniform(0, variance); time.sleep(d); return d
    def mouse_move(self, *a, **k) -> Dict[str, Any]: return {"action": "none"}
    def click(self, *a, **k) -> Dict[str, Any]: return {"action": "none"}
    def scroll(self, *a, **k) -> Dict[str, Any]: return {"action": "none"}
    def type_text(self, *a, **k) -> Dict[str, Any]: return {"action": "none"}
    def random_interaction(self) -> Dict[str, Any]: return {"action": "none"}


behavior_simulator = BehaviorSimulator()

_CAPTCHA_RE = re.compile(r"g-recaptcha|recaptcha/api|hcaptcha|cf-challenge|challenge-platform|funcaptcha|arkoselabs|geetest|"
                         r"are you a robot|verify you are human|unusual traffic", re.I)


class CaptchaDetector:
    """Detects bot challenges so the crawler can STOP. It never attempts to solve them."""
    def detect(self, html: str = None, headers: Dict[str, str] = None, url: str = None,
               status_code: int = None) -> Dict[str, Any]:
        hit = bool(status_code in (403, 429, 503)) or bool(html and _CAPTCHA_RE.search(html[:200_000]))
        return {"detected": hit, "type": ("rate_limited" if status_code in (403, 429, 503) else "challenge") if hit else None,
                "types": [], "confidence": 1.0 if hit else 0.0, "indicators": [], "solvers": []}


captcha_detector = CaptchaDetector()


def get_random_user_agent() -> str: return BOT_UA
def get_random_proxy() -> Optional[str]: return None
def get_random_delay(min_delay: float = 1.0, max_delay: float = 5.0) -> float: return random.uniform(min_delay, max_delay)
def human_like_delay(base: float = 1.0) -> float: time.sleep(base); return base
def load_proxies_from_file(file_path: str) -> int: return 0
def enable_proxies(enabled: bool = True): proxy_manager.enabled = False
def get_polite_headers(url: str = None, user_agent: str = None) -> Dict[str, str]: return header_manager.get_headers()
get_stealth_headers = get_polite_headers  # backwards-compatible alias
