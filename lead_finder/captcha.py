"""
CAPTCHA Solving Integration for Web Scraping

This module provides:
1. CAPTCHA detection (reCAPTCHA, hCaptcha, FunCaptcha, etc.)
2. Multiple CAPTCHA solving strategies
3. Integration with CAPTCHA solving services
4. Automatic retry with CAPTCHA handling
5. 2Captcha integration
6. Anti-Captcha integration
7. Local ML-based solving (simple CAPTCHAs)
8. Manual solving fallback
"""
import os
import re
import json
import time
import random
import base64
import urllib.parse
import urllib.request
from typing import List, Dict, Any, Optional, Tuple, Callable
from dataclasses import dataclass, field

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .config import UA
from .stealth import captcha_detector, ua_manager, proxy_manager, rate_limiter
from .database import log_discovery
from .http_utils import http_get, http_post, clean_text


# ============================================================================
# CAPTCHA SOLVER CONFIGURATION
# ============================================================================

@dataclass
class CaptchaConfig:
    """Configuration for CAPTCHA solving"""
    enabled: bool = True
    
    # API keys for CAPTCHA solving services
    twocaptcha_key: str = None
    anticaptcha_key: str = None
    capsolver_key: str = None
    
    # Priority order for solving services
    service_priority: List[str] = field(default_factory=lambda: [
        "twocaptcha", "anticaptcha", "capsolver"
    ])
    
    # Retry settings
    max_retries: int = 3
    retry_delay: float = 5.0
    
    # Local solving (for simple CAPTCHAs)
    enable_local_ocr: bool = False
    local_ocr_model: str = "tesseract"  # or "easyocr"
    
    # Manual solving
    enable_manual_solving: bool = True
    manual_solve_url: str = "http://localhost:8080/captcha"  # URL to send CAPTCHA for manual solving
    
    # Timeouts
    solve_timeout: float = 120.0  # 2 minutes max
    
    # Rates
    price_per_captcha: float = 0.001  # Average price per CAPTCHA in USD


# ============================================================================
# CAPTCHA DETECTION ENHANCEMENT
# ============================================================================

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
                "solvers": ["twocaptcha", "anticaptcha", "capsolver", "2captcha"]
            },
            "recaptcha_v2": {
                "patterns": [
                    r'recaptcha/api\.js\?render=',
                    r'grecaptcha\.execute\(', r'size:.*?invisible',
                    r'data-size=["\']invisible["\']'
                ],
                "solvers": ["twocaptcha", "anticaptcha", "capsolver"]
            },
            "recaptcha_v3": {
                "patterns": [
                    r'recaptcha/api\.js\?render=',
                    r'grecaptcha\.execute\(',
                    r'data-action='
                ],
                "solvers": ["twocaptcha", "anticaptcha", "capsolver"]
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
                "solvers": ["twocaptcha", "anticaptcha", "capsolver", "2captcha"]
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
                "solvers": ["twocaptcha", "anticaptcha", "capsolver", "2captcha"]
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
                "solvers": ["twocaptcha", "anticaptcha", "capsolver"]
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
                "solvers": ["local_ocr", "twocaptcha", "anticaptcha", "manual"]
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
                "solvers": ["local_math", "manual"]
            },
            "image_captcha": {
                "patterns": [
                    r'<img[^>]+captcha[^>]*src=["\']([^"\']+)["\']',
                    r'captcha-image',
                    r'captcha_img'
                ],
                "solvers": ["twocaptcha", "anticaptcha", "local_ocr", "manual"]
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
                "solvers": ["cloudflare_bypass"]
            },
            "akamai": {
                "patterns": [
                    r'akamai',
                    r'bot manager',
                    r'akamai-bot-manager',
                    r'BM_'
                ],
                "solvers": ["akamai_bypass"]
            },
            "incapsula": {
                "patterns": [
                    r'incapsula',
                    r'incap_ses',
                    r'incap',
                    r'INCAPSULA'
                ],
                "solvers": ["incapsula_bypass"]
            },
            "perimeterx": {
                "patterns": [
                    r'perimeterx',
                    r'pxhd',
                    r'PERIMETERX',
                    r'_px'
                ],
                "solvers": ["perimeterx_bypass"]
            },
            "shape": {
                "patterns": [
                    r'shape',
                    r'botwall',
                    r'SHAPE',
                    r'shape-security'
                ],
                "solvers": ["shape_bypass"]
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
            result["solvers"].extend(["rate_limit_wait", "proxy_rotation"])
        
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
                                result["solvers"].append(f"{value.lower()}_bypass")
        
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


# ============================================================================
# CAPTCHA SOLVER BASE
# ============================================================================

class CaptchaSolver:
    """Base class for CAPTCHA solvers"""
    
    def __init__(self, config: CaptchaConfig = None):
        self.config = config or CaptchaConfig()
        self.detector = EnhancedCaptchaDetector()
        self.solve_attempts = 0
        self.success_count = 0
        self.error_count = 0
    
    def can_solve(self, captcha_type: str) -> bool:
        """Check if this solver can solve a specific CAPTCHA type"""
        return False
    
    def solve(self, html: str = None, url: str = None, headers: Dict[str, str] = None,
              status_code: int = None, timeout: float = None) -> Tuple[bool, Dict[str, Any]]:
        """
        Solve a CAPTCHA
        
        Returns: (success, result_dict)
        """
        raise NotImplementedError
    
    def get_stats(self) -> Dict[str, Any]:
        """Get solver statistics"""
        return {
            "attempts": self.solve_attempts,
            "success": self.success_count,
            "errors": self.error_count,
            "success_rate": self.success_count / max(1, self.solve_attempts)
        }
    
    def _increment_attempt(self):
        """Increment attempt counter"""
        self.solve_attempts += 1
    
    def _record_success(self):
        """Record a successful solve"""
        self.success_count += 1
    
    def _record_error(self):
        """Record an error"""
        self.error_count += 1


# ============================================================================
# 2CAPTCHA SOLVER
# ============================================================================

class TwoCaptchaSolver(CaptchaSolver):
    """2Captcha integration"""
    
    API_URL = "https://2captcha.com"
    
    def __init__(self, config: CaptchaConfig = None):
        super().__init__(config)
        self.api_key = config.twocaptcha_key if config else os.environ.get("TWOCAPTCHA_API_KEY")
    
    def can_solve(self, captcha_type: str) -> bool:
        return captcha_type in ["recaptcha", "recaptcha_v2", "recaptcha_v3", 
                               "hcaptcha", "funcaptcha", "geetest", 
                               "text_captcha", "image_captcha"]
    
    def solve(self, html: str = None, url: str = None, headers: Dict[str, str] = None,
              status_code: int = None, timeout: float = None) -> Tuple[bool, Dict[str, Any]]:
        timeout = timeout or self.config.solve_timeout
        
        self._increment_attempt()
        
        if not self.api_key:
            self._record_error()
            return False, {"error": "2Captcha API key not configured"}
        
        # Detect CAPTCHA type
        detection = self.detector.detect(html, headers, url, status_code)
        if not detection["detected"]:
            self._record_error()
            return False, {"error": "No CAPTCHA detected"}
        
        captcha_type = detection["type"]
        
        # Prepare CAPTCHA data
        if captcha_type == "recaptcha_v2":
            if not detection["sitekey"]:
                self._record_error()
                return False, {"error": "reCAPTCHA v2 sitekey not found"}
            
            # Submit to 2Captcha
            submit_url = f"{self.API_URL}/in.php"
            data = {
                "key": self.api_key,
                "method": "userrecaptcha",
                "googlekey": detection["sitekey"],
                "pageurl": url,
                "json": 1
            }
            
            # If it's invisible reCAPTCHA
            if detection.get("action"):
                data["method"] = "userrecaptcha"
                data["action"] = detection["action"]
            
            result = self._submit_and_poll(submit_url, data, timeout)
            return result
        
        elif captcha_type == "recaptcha_v3":
            if not detection["sitekey"]:
                self._record_error()
                return False, {"error": "reCAPTCHA v3 sitekey not found"}
            
            submit_url = f"{self.API_URL}/in.php"
            data = {
                "key": self.api_key,
                "method": "userrecaptcha",
                "googlekey": detection["sitekey"],
                "pageurl": url,
                "action": detection.get("action", ""),
                "json": 1
            }
            
            result = self._submit_and_poll(submit_url, data, timeout)
            return result
        
        elif captcha_type == "hcaptcha":
            if not detection["sitekey"]:
                self._record_error()
                return False, {"error": "hCaptcha sitekey not found"}
            
            submit_url = f"{self.API_URL}/in.php"
            data = {
                "key": self.api_key,
                "method": "hcaptcha",
                "sitekey": detection["sitekey"],
                "pageurl": url,
                "json": 1
            }
            
            result = self._submit_and_poll(submit_url, data, timeout)
            return result
        
        elif captcha_type == "funcaptcha":
            if not detection["pkey"]:
                self._record_error()
                return False, {"error": "FunCaptcha pkey not found"}
            
            submit_url = f"{self.API_URL}/in.php"
            data = {
                "key": self.api_key,
                "method": "funcaptcha",
                "publickey": detection["pkey"],
                "pageurl": url,
                "surl": detection.get("surl", ""),
                "json": 1
            }
            
            result = self._submit_and_poll(submit_url, data, timeout)
            return result
        
        elif captcha_type == "text_captcha":
            if not detection.get("image_url"):
                self._record_error()
                return False, {"error": "CAPTCHA image URL not found"}
            
            # Download CAPTCHA image
            try:
                import urllib.request
                with urllib.request.urlopen(detection["image_url"]) as response:
                    image_data = response.read()
            except Exception as e:
                self._record_error()
                return False, {"error": f"Failed to download CAPTCHA image: {e}"}
            
            submit_url = f"{self.API_URL}/in.php"
            data = {
                "key": self.api_key,
                "method": "base64",
                "body": base64.b64encode(image_data).decode('utf-8'),
                "json": 1
            }
            
            result = self._submit_and_poll(submit_url, data, timeout)
            return result
        
        else:
            self._record_error()
            return False, {"error": f"Unsupported CAPTCHA type: {captcha_type}"}
    
    def _submit_and_poll(self, submit_url: str, data: dict, timeout: float) -> Tuple[bool, Dict[str, Any]]:
        """Submit CAPTCHA to 2Captcha and poll for result"""
        import json
        
        start_time = time.time()
        
        try:
            # Submit CAPTCHA
            response_data = http_post(submit_url, data=data)
            result = json.loads(response_data)
            
            if result.get("status") != 1:
                self._record_error()
                return False, {"error": result.get("error_text", "Unknown error")}
            
            captcha_id = result.get("request")
            if not captcha_id:
                self._record_error()
                return False, {"error": "No CAPTCHA ID returned"}
            
            # Poll for result
            poll_url = f"{self.API_URL}/res.php"
            while time.time() - start_time < timeout:
                time.sleep(5)  # Wait 5 seconds between polls
                
                poll_data = {
                    "key": self.api_key,
                    "action": "get",
                    "id": captcha_id,
                    "json": 1
                }
                
                poll_response = http_post(poll_url, data=poll_data)
                poll_result = json.loads(poll_response)
                
                if poll_result.get("status") == 1:
                    self._record_success()
                    return True, {
                        "solution": poll_result.get("request"),
                        "captcha_id": captcha_id,
                        "response": poll_result
                    }
                elif poll_result.get("request") == "CAPCHA_NOT_READY":
                    continue
                else:
                    self._record_error()
                    return False, {"error": poll_result.get("error_text", "Polling error")}
            
            self._record_error()
            return False, {"error": "CAPTCHA solving timeout"}
            
        except Exception as e:
            self._record_error()
            return False, {"error": f"2Captcha error: {e}"}


# ============================================================================
# ANTI-CAPTCHA SOLVER
# ============================================================================

class AntiCaptchaSolver(CaptchaSolver):
    """Anti-Captcha integration"""
    
    API_URL = "https://api.anti-captcha.com"
    
    def __init__(self, config: CaptchaConfig = None):
        super().__init__(config)
        self.api_key = config.anticaptcha_key if config else os.environ.get("ANTICAPTCHA_API_KEY")
    
    def can_solve(self, captcha_type: str) -> bool:
        return captcha_type in ["recaptcha", "recaptcha_v2", "recaptcha_v3", 
                               "hcaptcha", "funcaptcha", "geetest", 
                               "text_captcha", "image_captcha"]
    
    def solve(self, html: str = None, url: str = None, headers: Dict[str, str] = None,
              status_code: int = None, timeout: float = None) -> Tuple[bool, Dict[str, Any]]:
        timeout = timeout or self.config.solve_timeout
        
        self._increment_attempt()
        
        if not self.api_key:
            self._record_error()
            return False, {"error": "Anti-Captcha API key not configured"}
        
        # Detect CAPTCHA type
        detection = self.detector.detect(html, headers, url, status_code)
        if not detection["detected"]:
            self._record_error()
            return False, {"error": "No CAPTCHA detected"}
        
        captcha_type = detection["type"]
        
        # For now, use similar logic as 2Captcha
        # In a real implementation, we'd use Anti-Captcha's specific API
        # This is a placeholder
        return False, {"error": "Anti-Captcha solver not fully implemented"}


# ============================================================================
# CAPSOLVER SOLVER
# ============================================================================

class CapsolverSolver(CaptchaSolver):
    """Capsolver integration"""
    
    API_URL = "https://api.capsolver.com"
    
    def __init__(self, config: CaptchaConfig = None):
        super().__init__(config)
        self.api_key = config.capsolver_key if config else os.environ.get("CAPSOLVER_API_KEY")
    
    def can_solve(self, captcha_type: str) -> bool:
        return captcha_type in ["recaptcha", "recaptcha_v2", "recaptcha_v3", 
                               "hcaptcha", "funcaptcha", "geetest", 
                               "text_captcha", "image_captcha"]
    
    def solve(self, html: str = None, url: str = None, headers: Dict[str, str] = None,
              status_code: int = None, timeout: float = None) -> Tuple[bool, Dict[str, Any]]:
        timeout = timeout or self.config.solve_timeout
        
        self._increment_attempt()
        
        if not self.api_key:
            self._record_error()
            return False, {"error": "Capsolver API key not configured"}
        
        # Detect CAPTCHA type
        detection = self.detector.detect(html, headers, url, status_code)
        if not detection["detected"]:
            self._record_error()
            return False, {"error": "No CAPTCHA detected"}
        
        captcha_type = detection["type"]
        
        # For now, use similar logic as 2Captcha
        # In a real implementation, we'd use Capsolver's specific API
        # This is a placeholder
        return False, {"error": "Capsolver solver not fully implemented"}


# ============================================================================
# LOCAL OCR SOLVER (for simple CAPTCHAs)
# ============================================================================

class LocalOCRSolver(CaptchaSolver):
    """Local OCR-based CAPTCHA solver for simple text CAPTCHAs"""
    
    def __init__(self, config: CaptchaConfig = None):
        super().__init__(config)
        self.ocr_model = config.local_ocr_model if config else "tesseract"
    
    def can_solve(self, captcha_type: str) -> bool:
        return captcha_type in ["text_captcha", "image_captcha"]
    
    def solve(self, html: str = None, url: str = None, headers: Dict[str, str] = None,
              status_code: int = None, timeout: float = None) -> Tuple[bool, Dict[str, Any]]:
        timeout = timeout or self.config.solve_timeout
        
        self._increment_attempt()
        
        # Detect CAPTCHA
        detection = self.detector.detect(html, headers, url, status_code)
        if not detection["detected"]:
            self._record_error()
            return False, {"error": "No CAPTCHA detected"}
        
        captcha_type = detection["type"]
        
        if captcha_type not in ["text_captcha", "image_captcha"]:
            self._record_error()
            return False, {"error": f"Unsupported CAPTCHA type: {captcha_type}"}
        
        image_url = detection.get("image_url")
        if not image_url:
            # Try to find image in HTML
            img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if img_match:
                image_url = img_match.group(1)
        
        if not image_url:
            self._record_error()
            return False, {"error": "No CAPTCHA image URL found"}
        
        # Try to download and solve with OCR
        try:
            # Download image
            import urllib.request
            with urllib.request.urlopen(image_url, timeout=10) as response:
                image_data = response.read()
            
            # Save to temp file
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                f.write(image_data)
                temp_file = f.name
            
            # Use OCR to extract text
            if self.ocr_model == "tesseract":
                try:
                    import pytesseract
                    text = pytesseract.image_to_string(temp_file)
                    text = clean_text(text)
                    
                    # Clean up the text
                    text = re.sub(r'[^a-zA-Z0-9]', '', text)
                    
                    if text:
                        self._record_success()
                        return True, {
                            "solution": text,
                            "method": "tesseract_ocr",
                            "confidence": 0.7
                        }
                except ImportError:
                    self._record_error()
                    return False, {"error": "Tesseract OCR not installed (pytesseract)"}
            
            elif self.ocr_model == "easyocr":
                try:
                    import easyocr
                    reader = easyocr.Reader(['en'])
                    results = reader.readtext(temp_file)
                    text = ' '.join([r[1] for r in results])
                    text = clean_text(text)
                    text = re.sub(r'[^a-zA-Z0-9]', '', text)
                    
                    if text:
                        self._record_success()
                        return True, {
                            "solution": text,
                            "method": "easyocr",
                            "confidence": 0.8
                        }
                except ImportError:
                    self._record_error()
                    return False, {"error": "EasyOCR not installed"}
            
            # Clean up
            os.unlink(temp_file)
            
            self._record_error()
            return False, {"error": "OCR failed to extract text"}
            
        except Exception as e:
            self._record_error()
            return False, {"error": f"OCR error: {e}"}


# ============================================================================
# LOCAL MATH SOLVER
# ============================================================================

class LocalMathSolver(CaptchaSolver):
    """Local math CAPTCHA solver"""
    
    def __init__(self, config: CaptchaConfig = None):
        super().__init__(config)
    
    def can_solve(self, captcha_type: str) -> bool:
        return captcha_type in ["math_captcha"]
    
    def solve(self, html: str = None, url: str = None, headers: Dict[str, str] = None,
              status_code: int = None, timeout: float = None) -> Tuple[bool, Dict[str, Any]]:
        self._increment_attempt()
        
        # Detect CAPTCHA
        detection = self.detector.detect(html, headers, url, status_code)
        if not detection["detected"]:
            self._record_error()
            return False, {"error": "No CAPTCHA detected"}
        
        captcha_type = detection["type"]
        
        if captcha_type != "math_captcha":
            self._record_error()
            return False, {"error": f"Unsupported CAPTCHA type: {captcha_type}"}
        
        # Try to extract math problem from HTML
        math_patterns = [
            r'(\d+)\s*[\+\-\*/\^]\s*(\d+)\s*=\s*\?',
            r'what is (\d+) ([\+\-\*/\^]) (\d+)',
            r'solve this: (\d+) ([\+\-\*/\^]) (\d+)',
            r'(\d+)\+(\d+)=',
            r'(\d+)-(\d+)=',
            r'(\d+)x(\d+)=',
            r'(\d+)/(\d+)='
        ]
        
        for pattern in math_patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                try:
                    if pattern == r'(\d+)\s*[\+\-\*/\^]\s*(\d+)\s*=\s*\?':
                        num1 = int(match.group(1))
                        operator = match.group(0).split()[1]
                        num2 = int(match.group(2))
                    else:
                        parts = match.groups()
                        num1 = int(parts[0])
                        operator = parts[1] if len(parts) > 1 else '+'
                        num2 = int(parts[2]) if len(parts) > 2 else 0
                    
                    # Calculate result
                    if operator == '+':
                        result = num1 + num2
                    elif operator == '-':
                        result = num1 - num2
                    elif operator == '*' or operator == 'x':
                        result = num1 * num2
                    elif operator == '/':
                        result = num1 // num2
                    elif operator == '^':
                        result = num1 ** num2
                    else:
                        continue
                    
                    self._record_success()
                    return True, {
                        "solution": str(result),
                        "method": "math_solver",
                        "problem": f"{num1} {operator} {num2}",
                        "confidence": 0.95
                    }
                except:
                    continue
        
        self._record_error()
        return False, {"error": "Could not extract math problem"}


# ============================================================================
# MANUAL SOLVER
# ============================================================================

class ManualSolver(CaptchaSolver):
    """Manual CAPTCHA solver - sends CAPTCHA to a local server for manual solving"""
    
    def __init__(self, config: CaptchaConfig = None):
        super().__init__(config)
        self.manual_solve_url = config.manual_solve_url if config else "http://localhost:8080/captcha"
    
    def can_solve(self, captcha_type: str) -> bool:
        return True  # Can handle any type
    
    def solve(self, html: str = None, url: str = None, headers: Dict[str, str] = None,
              status_code: int = None, timeout: float = None) -> Tuple[bool, Dict[str, Any]]:
        timeout = timeout or self.config.solve_timeout
        
        self._increment_attempt()
        
        # Detect CAPTCHA
        detection = self.detector.detect(html, headers, url, status_code)
        if not detection["detected"]:
            self._record_error()
            return False, {"error": "No CAPTCHA detected"}
        
        # Send to manual solving endpoint
        try:
            data = {
                "url": url,
                "html": html,
                "headers": headers,
                "status_code": status_code,
                "captcha_type": detection["type"],
                "detection": detection
            }
            
            response = http_post(self.manual_solve_url, json=data, timeout=timeout)
            
            if response:
                result = json.loads(response)
                if result.get("solution"):
                    self._record_success()
                    return True, {
                        "solution": result["solution"],
                        "method": "manual",
                        "solved_by": result.get("solved_by", "user")
                    }
            
            self._record_error()
            return False, {"error": "Manual solving endpoint did not return a solution"}
            
        except Exception as e:
            self._record_error()
            return False, {"error": f"Manual solving error: {e}"}


# ============================================================================
# BYPASS SOLVERS (for WAF/rate limiting)
# ============================================================================

class RateLimitBypassSolver(CaptchaSolver):
    """Bypass rate limiting by waiting or rotating proxies"""
    
    def __init__(self, config: CaptchaConfig = None):
        super().__init__(config)
    
    def can_solve(self, captcha_type: str) -> bool:
        return captcha_type in ["rate_limited", "cloudflare", "akamai", "incapsula", "perimeterx", "shape"]
    
    def solve(self, html: str = None, url: str = None, headers: Dict[str, str] = None,
              status_code: int = None, timeout: float = None) -> Tuple[bool, Dict[str, Any]]:
        self._increment_attempt()
        
        if status_code == 429:  # Too Many Requests
            wait_time = int(headers.get("Retry-After", 60)) if headers else 60
            time.sleep(wait_time)
            self._record_success()
            return True, {
                "solution": "waited_and_retry",
                "wait_time": wait_time,
                "method": "retry_after_wait"
            }
        
        elif status_code in [403, 503]:  # Forbidden/Service Unavailable
            # Rotate proxy
            from .stealth import proxy_manager
            if proxy_manager.enabled and len(proxy_manager.proxies) > 0:
                proxy_manager.current_index = (proxy_manager.current_index + 1) % len(proxy_manager.proxies)
                self._record_success()
                return True, {
                    "solution": "proxy_rotated",
                    "method": "proxy_rotation"
                }
        
        self._record_error()
        return False, {"error": "Could not bypass rate limiting"}


# ============================================================================
# CAPTCHA SOLVER MANAGER
# ============================================================================

class CaptchaSolverManager:
    """Manage multiple CAPTCHA solvers and coordinate solving"""
    
    def __init__(self, config: CaptchaConfig = None):
        self.config = config or CaptchaConfig()
        self.solvers = {}
        self.detector = EnhancedCaptchaDetector()
        self._init_solvers()
        
        self.total_attempts = 0
        self.success_count = 0
        self.error_count = 0
    
    def _init_solvers(self):
        """Initialize all configured solvers"""
        if self.config.enable_local_ocr:
            self.solvers["local_ocr"] = LocalOCRSolver(self.config)
        
        if self.config.twocaptcha_key or os.environ.get("TWOCAPTCHA_API_KEY"):
            self.solvers["twocaptcha"] = TwoCaptchaSolver(self.config)
        
        if self.config.anticaptcha_key or os.environ.get("ANTICAPTCHA_API_KEY"):
            self.solvers["anticaptcha"] = AntiCaptchaSolver(self.config)
        
        if self.config.capsolver_key or os.environ.get("CAPSOLVER_API_KEY"):
            self.solvers["capsolver"] = CapsolverSolver(self.config)
        
        self.solvers["local_math"] = LocalMathSolver(self.config)
        self.solvers["manual"] = ManualSolver(self.config)
        self.solvers["rate_limit_bypass"] = RateLimitBypassSolver(self.config)
    
    def get_available_solvers(self, captcha_type: str = None) -> List[str]:
        """Get list of available solvers for a CAPTCHA type"""
        if captcha_type:
            detection = self.detector.detect()
            detection["types"] = [captcha_type]
            solvers = list(set(detection.get("solvers", [])))
            available = [s for s in solvers if s in self.solvers]
            
            # Also add bypass solvers for rate limiting
            if captcha_type in ["rate_limited", "cloudflare", "akamai", "incapsula"]:
                available.append("rate_limit_bypass")
            
            return available
        
        return list(self.solvers.keys())
    
    def solve(self, html: str = None, url: str = None, headers: Dict[str, str] = None,
              status_code: int = None, timeout: float = None) -> Tuple[bool, Dict[str, Any]]:
        """Automatically solve a CAPTCHA using the best available solver"""
        timeout = timeout or self.config.solve_timeout
        start_time = time.time()
        
        self.total_attempts += 1
        
        # Detect CAPTCHA
        detection = self.detector.detect(html, headers, url, status_code)
        
        if not detection["detected"]:
            self.error_count += 1
            return False, {"error": "No CAPTCHA detected"}
        
        captcha_type = detection["type"]
        
        # Get solver priority
        solver_names = self.detector.get_solver_priority(captcha_type, self.config)
        
        # Try each solver in priority order
        for solver_name in solver_names:
            if time.time() - start_time > timeout:
                self.error_count += 1
                return False, {"error": "CAPTCHA solving timeout"}
            
            if solver_name in self.solvers:
                solver = self.solvers[solver_name]
                
                try:
                    success, result = solver.solve(html, url, headers, status_code, 
                                                   timeout - (time.time() - start_time))
                    
                    if success:
                        self.success_count += 1
                        result["solver"] = solver_name
                        result["captcha_type"] = captcha_type
                        return True, result
                    
                    # Log the error but continue to next solver
                    log_discovery("captcha", url or "", "error", 
                                 details=f"Solver {solver_name} failed: {result.get('error', 'Unknown')}")
                    
                except Exception as e:
                    log_discovery("captcha", url or "", "error", 
                                 details=f"Solver {solver_name} exception: {e}")
        
        self.error_count += 1
        return False, {
            "error": "All solvers failed",
            "tried_solvers": solver_names,
            "captcha_type": captcha_type
        }
    
    def solve_with_specific_solver(self, solver_name: str, html: str = None, url: str = None,
                                   headers: Dict[str, str] = None, status_code: int = None,
                                   timeout: float = None) -> Tuple[bool, Dict[str, Any]]:
        """Solve a CAPTCHA using a specific solver"""
        if solver_name not in self.solvers:
            return False, {"error": f"Solver {solver_name} not available"}
        
        solver = self.solvers[solver_name]
        return solver.solve(html, url, headers, status_code, timeout)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics for all solvers"""
        stats = {
            "total": {
                "attempts": self.total_attempts,
                "success": self.success_count,
                "errors": self.error_count,
                "success_rate": self.success_count / max(1, self.total_attempts)
            },
            "solvers": {}
        }
        
        for name, solver in self.solvers.items():
            stats["solvers"][name] = solver.get_stats()
        
        return stats
    
    def reset_stats(self):
        """Reset all statistics"""
        self.total_attempts = 0
        self.success_count = 0
        self.error_count = 0
        for solver in self.solvers.values():
            solver.solve_attempts = 0
            solver.success_count = 0
            solver.error_count = 0


# ============================================================================
# CAPTCHA MIDDLEWARE (for integration with HTTP requests)
# ============================================================================

class CaptchaMiddleware:
    """Middleware for automatically handling CAPTCHAs in HTTP requests"""
    
    def __init__(self, solver_manager: CaptchaSolverManager = None):
        self.solver_manager = solver_manager or CaptchaSolverManager()
        self.detector = self.solver_manager.detector
        self.max_retries = 3
    
    def request(self, url: str, method: str = "GET", data: dict = None, headers: dict = None,
                timeout: float = 30, max_retries: int = None) -> Tuple[Optional[str], Dict[str, str], int]:
        """
        Make a request with automatic CAPTCHA handling
        
        Returns: (content, response_headers, status_code)
        """
        import urllib.request
        import urllib.parse
        
        max_retries = max_retries or self.max_retries
        
        for attempt in range(max_retries + 1):
            try:
                # Make the request
                if method.upper() == "GET":
                    req = urllib.request.Request(url, headers=headers or {})
                    with urllib.request.urlopen(req, timeout=timeout) as response:
                        content = response.read().decode('utf-8', 'replace')
                        status_code = response.getcode()
                        response_headers = dict(response.headers)
                else:
                    encoded_data = urllib.parse.urlencode(data).encode() if data else None
                    req = urllib.request.Request(url, data=encoded_data, headers=headers or {}, method=method)
                    with urllib.request.urlopen(req, timeout=timeout) as response:
                        content = response.read().decode('utf-8', 'replace')
                        status_code = response.getcode()
                        response_headers = dict(response.headers)
                
                # Check for CAPTCHA
                detection = self.detector.detect(content, response_headers, url, status_code)
                
                if detection["detected"]:
                    # Try to solve CAPTCHA
                    success, result = self.solver_manager.solve(content, url, response_headers, status_code)
                    
                    if success:
                        # Apply solution and retry
                        # For form-based CAPTCHAs, we'd need to submit the solution
                        # For now, just wait and retry
                        time.sleep(2)
                        continue
                    else:
                        # Could not solve CAPTCHA
                        log_discovery("captcha_middleware", url, "error", 
                                     details=f"Could not solve CAPTCHA: {result.get('error', 'Unknown')}")
                        return None, response_headers, status_code
                
                # No CAPTCHA, return content
                return content, response_headers, status_code
                
            except Exception as e:
                # Check if it's a CAPTCHA-related error
                if isinstance(e, urllib.error.HTTPError):
                    detection = self.detector.detect(None, dict(e.headers), url, e.code)
                    if detection["detected"]:
                        success, result = self.solver_manager.solve(None, url, dict(e.headers), e.code)
                        if success:
                            time.sleep(2)
                            continue
                
                if attempt == max_retries:
                    raise
                
                time.sleep(2 ** attempt)
        
        return None, {}, 0


# ============================================================================
# EXPORT
# ============================================================================

# Global instances
detector = EnhancedCaptchaDetector()
solver_manager = CaptchaSolverManager()


def detect_captcha(html: str = None, headers: Dict[str, str] = None,
                    url: str = None, status_code: int = None) -> Dict[str, Any]:
    """Detect CAPTCHAs on a page"""
    return detector.detect(html, headers, url, status_code)


def solve_captcha(html: str = None, url: str = None, headers: Dict[str, str] = None,
                   status_code: int = None, timeout: float = None) -> Tuple[bool, Dict[str, Any]]:
    """Solve a CAPTCHA automatically"""
    return solver_manager.solve(html, url, headers, status_code, timeout)


def solve_with_solver(solver_name: str, html: str = None, url: str = None,
                      headers: Dict[str, str] = None, status_code: int = None,
                      timeout: float = None) -> Tuple[bool, Dict[str, Any]]:
    """Solve a CAPTCHA with a specific solver"""
    return solver_manager.solve_with_specific_solver(solver_name, html, url, headers, status_code, timeout)


def get_solvers() -> List[str]:
    """Get list of available solvers"""
    return list(solver_manager.solvers.keys())


def get_captcha_stats() -> Dict[str, Any]:
    """Get CAPTCHA solving statistics"""
    return solver_manager.get_stats()


# Individual solver instances for direct use
twocaptcha_solver = None
if os.environ.get("TWOCAPTCHA_API_KEY"):
    twocaptcha_solver = TwoCaptchaSolver()

anticaptcha_solver = None
if os.environ.get("ANTICAPTCHA_API_KEY"):
    anticaptcha_solver = AntiCaptchaSolver()

local_ocr_solver = LocalOCRSolver()
local_math_solver = LocalMathSolver()
manual_solver = ManualSolver()


if __name__ == "__main__":
    # Test CAPTCHA detection
    print("Testing CAPTCHA Detection...")
    
    # Test with a known reCAPTCHA page
    html = """
    <html>
    <body>
    <div class="g-recaptcha" data-sitekey="6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"></div>
    <script src="https://www.google.com/recaptcha/api.js"></script>
    </body>
    </html>
    """
    
    detection = detect_captcha(html=html)
    print(f"Detection result: {detection}")
    
    # Test solver availability
    print(f"\nAvailable solvers: {get_solvers()}")
    
    # Test statistics
    print(f"\nStatistics: {get_captcha_stats()}")
