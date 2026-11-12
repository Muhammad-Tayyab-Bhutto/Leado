"""
CAPTCHA / bot-challenge DETECTION for polite crawling.

Dealwright never solves or bypasses CAPTCHAs. When a site presents a challenge
(or answers 403/429/503) the crawler logs it, backs off and skips the site.
The old third-party solver integrations were removed on purpose.
"""
import os
import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .politeness import rate_limiter
from .database import log_discovery


@dataclass
class CaptchaConfig:
    """Kept for API compatibility. Solving is always disabled."""
    enabled: bool = False
    service_priority: List[str] = field(default_factory=list)


class EnhancedCaptchaDetector:
    """Enhanced CAPTCHA detection with more patterns"""
    
    def __init__(self):
        self.patterns = {
            "recaptcha": {
                "patterns": [
                    r'grecaptcha\.execute\(',
                    r'grecaptcha\.render\(',
                    r'google\.com/recaptcha',
                    r'recaptcha/api\.js',
                    r'sitekey',
                    r'data-sitekey',
                    r'class=["\']g-recaptcha["\']',
                    r'rc-anchor',
                    r'recaptcha-token',
                    r'grecaptcha',
                    r'Recaptcha',
                    r'RECAPTCHA'
                ],
                "solvers": []
            },
            "recaptcha_v2": {
                "patterns": [
                    r'recaptcha/api\.js\?render=',
                    r'grecaptcha\.execute\(', r'size:.*?invisible',
                    r'data-size=["\']invisible["\']'
                ],
                "solvers": []
            },
            "recaptcha_v3": {
                "patterns": [
                    r'recaptcha/api\.js\?render=',
                    r'grecaptcha\.execute\(',
                    r'data-action='
                ],
                "solvers": []
            },
            "hcaptcha": {
                "patterns": [
                    r'hcaptcha\.com',
                    r'hCaptcha',
                    r'HCAPTCHA',
                    r'data-sitekey',
                    r'new HCaptcha\(',
                    r'hcaptcha-loader',
                    r'class=["\']h-captcha["\']'
                ],
                "solvers": []
            },
            "funcaptcha": {
                "patterns": [
                    r'funcaptcha\.com',
                    r'FunCaptcha',
                    r'FUNCAPTCHA',
                    r'new FunCaptcha\(',
                    r'data-pkey',
                    r'funcaptcha-api',
                    r'class=["\']fc-["\']'
                ],
                "solvers": []
            },
            "geetest": {
                "patterns": [
                    r'geetest\.com',
                    r'Geetest',
                    r'GEETEST',
                    r'new Geetest\(',
                    r'captcha_id',
                    r'geetest_api',
                    r'class=["\']geetest_["\']'
                ],
                "solvers": []
            },
            "text_captcha": {
                "patterns": [
                    r'captcha',
                    r'CAPTCHA',
                    r'verify you are human',
                    r'verify human',
                    r'are you human',
                    r'bot detection',
                    r'robot verification',
                    r'enter the characters',
                    r'type the text',
                    r'captcha-image',
                    r'img.*captcha',
                    r'<img[^>]+captcha[^>]+>',
                    r'<div[^>]+captcha[^>]+>'
                ],
                "solvers": []
            },
            "math_captcha": {
                "patterns": [
                    r'solve this',
                    r'what is',
                    r'= \?',
                    r'calculate',
                    r'math question',
                    r'\d+ [\+\-\*/\^] \d+ ='
                ],
                "solvers": []
            },
            "image_captcha": {
                "patterns": [
                    r'<img[^>]+captcha[^>]*src=["\']([^"\']+)["\']',
                    r'captcha-image',
                    r'captcha_img'
                ],
                "solvers": []
            },
            "cloudflare": {
                "patterns": [
                    r'cloudflare',
                    r'cf-ray',
                    r'5xx error',
                    r'please turn on javascript',
                    r'checking your browser',
                    r'ddos protection',
                    r'cf-',
                    r'__cfduid'
                ],
                "solvers": []
            },
            "akamai": {
                "patterns": [
                    r'akamai',
                    r'bot manager',
                    r'akamai-bot-manager',
                    r'BM_'
                ],
                "solvers": []
            },
            "incapsula": {
                "patterns": [
                    r'incapsula',
                    r'incap_ses',
                    r'incap',
                    r'INCAPSULA'
                ],
                "solvers": []
            },
            "perimeterx": {
                "patterns": [
                    r'perimeterx',
                    r'pxhd',
                    r'PERIMETERX',
                    r'_px'
                ],
                "solvers": []
            },
            "shape": {
                "patterns": [
                    r'shape',
                    r'botwall',
                    r'SHAPE',
                    r'shape-security'
                ],
                "solvers": []
            }
        }
    
    def detect(self, html: str = None, headers: Dict[str, str] = None,
               url: str = None, status_code: int = None) -> Dict[str, Any]:
        """Detect CAPTCHAs with enhanced patterns"""
        result = {
            "detected": False,
            "types": [],
            "type": None,
            "confidence": 0.0,
            "indicators": [],
            "solvers": [],
            "sitekey": None,
            "pkey": None,
            "captcha_id": None,
            "image_url": None,
            "action": None,
            "data": {}
        }
        
        # First, check status code
        if status_code and status_code in [403, 429, 503]:
            result["detected"] = True
            result["types"].append("rate_limited")
            result["confidence"] = 0.8
            result["indicators"].append(f"HTTP {status_code}")
            result["solvers"].append("back_off_and_skip")
        
        # Check headers
        if headers:
            for header, values in [
                ("Server", ["Cloudflare", "Akamai", "Incapsula", "PerimeterX"]),
                ("X-Captcha", ["true", "yes", "1"]),
                ("X-Robot", ["true", "yes", "1"]),
            ]:
                if header in headers:
                    header_value = headers[header].lower()
                    for value in values:
                        if value.lower() in header_value:
                            result["detected"] = True
                            result["types"].append(header.lower())
                            result["confidence"] = max(result["confidence"], 0.7)
                            result["indicators"].append(f"Header: {header}={header_value}")
                            if header == "Server":
                                pass
        
        # Check HTML for CAPTCHA patterns
        if html:
            html_lower = html.lower()
            
            for captcha_type, captcha_info in self.patterns.items():
                for pattern in captcha_info["patterns"]:
                    if re.search(pattern, html_lower):
                        result["detected"] = True
                        if captcha_type not in result["types"]:
                            result["types"].append(captcha_type)
                        result["confidence"] = max(result["confidence"], min(1.0, result["confidence"] + 0.1))
                        result["indicators"].append(f"HTML: {captcha_type}")
                        result["solvers"].extend(captcha_info["solvers"])
                
                # Extract additional data
                if captcha_type == "recaptcha":
                    sitekey_match = re.search(r'data-sitekey=["\']([^"\']+)["\']', html, re.IGNORECASE)
                    if sitekey_match:
                        result["sitekey"] = sitekey_match.group(1)
                        result["data"]["sitekey"] = sitekey_match.group(1)
                    
                    action_match = re.search(r'data-action=["\']([^"\']+)["\']', html, re.IGNORECASE)
                    if action_match:
                        result["action"] = action_match.group(1)
                        result["data"]["action"] = action_match.group(1)
                
                elif captcha_type == "hcaptcha":
                    sitekey_match = re.search(r'data-sitekey=["\']([^"\']+)["\']', html, re.IGNORECASE)
                    if sitekey_match:
                        result["sitekey"] = sitekey_match.group(1)
                        result["data"]["sitekey"] = sitekey_match.group(1)
                
                elif captcha_type == "funcaptcha":
                    pkey_match = re.search(r'data-pkey=["\']([^"\']+)["\']', html, re.IGNORECASE)
                    if pkey_match:
                        result["pkey"] = pkey_match.group(1)
                        result["data"]["pkey"] = pkey_match.group(1)
                
                elif captcha_type in ["text_captcha", "image_captcha"]:
                    img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
                    if img_match:
                        img_url = img_match.group(1)
                        if any(captcha_word in img_url.lower() for captcha_word in ['captcha', 'verify', 'bot']):
                            result["image_url"] = img_url
                            result["data"]["image_url"] = img_url
        
        # Check URL
        if url:
            url_lower = url.lower()
            for captcha_type, captcha_info in self.patterns.items():
                for pattern in captcha_info["patterns"]:
                    if re.search(pattern, url_lower):
                        result["detected"] = True
                        if captcha_type not in result["types"]:
                            result["types"].append(captcha_type)
                        result["confidence"] = max(result["confidence"], 0.6)
                        result["indicators"].append(f"URL: {captcha_type}")
        
        # Deduplicate
        result["types"] = list(set(result["types"]))
        result["solvers"] = list(set(result["solvers"]))
        result["indicators"] = list(set(result["indicators"]))
        
        # Set primary type
        if result["types"]:
            # Prefer more specific types
            priority_types = ["recaptcha_v3", "recaptcha_v2", "recaptcha", "hcaptcha", "funcaptcha", "geetest"]
            for priority in priority_types:
                if priority in result["types"]:
                    result["type"] = priority
                    break
            if not result["type"]:
                result["type"] = result["types"][0]
        
        # Cap confidence at 1.0
        result["confidence"] = min(1.0, result["confidence"])
        
        return result
    
    def get_solver_priority(self, captcha_type: str, config: CaptchaConfig = None) -> List[str]:
        """Get solver priority for a specific CAPTCHA type"""
        if not config:
            config = CaptchaConfig()
        
        if captcha_type in self.patterns:
            solvers = self.patterns[captcha_type]["solvers"]
            # Reorder based on config priority
            priority = {s: i for i, s in enumerate(config.service_priority)}
            return sorted(solvers, key=lambda s: priority.get(s, len(config.service_priority)))
        
        return config.service_priority


class CaptchaSolverManager:
    """Never solves. Records the challenge, backs off, and tells the caller to skip the site."""
    def __init__(self, config: CaptchaConfig = None):
        self.config = config or CaptchaConfig()
        self.detector = EnhancedCaptchaDetector()
        self.solvers: Dict[str, Any] = {}
        self.stats = {"challenges_seen": 0, "sites_skipped": 0}

    def solve(self, html: str = None, url: str = None, headers: Dict[str, str] = None,
              status_code: int = None, timeout: float = None) -> Tuple[bool, Dict[str, Any]]:
        d = self.detector.detect(html, headers, url, status_code)
        if d.get("detected"):
            self.stats["challenges_seen"] += 1; self.stats["sites_skipped"] += 1
            rate_limiter.record(False)
            try: log_discovery("crawler", url or "", "skipped", details=f"bot challenge ({d.get('type')}); not bypassing")
            except Exception: pass
        return False, {"skipped": True, "reason": "Bot challenge detected; Dealwright does not bypass CAPTCHAs.", "detection": d}

    def solve_with_specific_solver(self, solver_name: str, *a, **k) -> Tuple[bool, Dict[str, Any]]:
        return self.solve(*a, **k)

    def get_stats(self) -> Dict[str, Any]:
        return {"policy": "detect-and-skip (no solving)", **self.stats}


detector = EnhancedCaptchaDetector()
solver_manager = CaptchaSolverManager()


def detect_captcha(html: str = None, headers: Dict[str, str] = None,
                   url: str = None, status_code: int = None) -> Dict[str, Any]:
    return detector.detect(html, headers, url, status_code)


def solve_captcha(*a, **k) -> Tuple[bool, Dict[str, Any]]:
    return solver_manager.solve(*a, **k)


def solve_with_solver(solver_name: str, *a, **k) -> Tuple[bool, Dict[str, Any]]:
    return solver_manager.solve(*a, **k)


def get_solvers() -> List[str]:
    return []


def get_captcha_stats() -> Dict[str, Any]:
    return solver_manager.get_stats()
