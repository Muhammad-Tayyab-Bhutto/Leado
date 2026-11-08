"""
Stealth Techniques for Web Scraping

This module provides techniques to make web scraping more stealthy:
1. User-Agent rotation
2. Proxy rotation
3. Request throttling
4. Session management
5. Human-like behavior patterns
6. CAPTCHA detection and handling
"""
import os
import re
import time
import random
import json
import threading
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .config import UA
from .database import log_discovery


# ============================================================================
# USER AGENT MANAGEMENT
# ============================================================================

# List of user agents to rotate through
USER_AGENTS = [
    # Desktop - Chrome
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    
    # Desktop - Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:119.0) Gecko/20100101 Firefox/119.0",
    
    # Desktop - Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    
    # Mobile - Android
    "Mozilla/5.0 (Linux; Android 13; SM-S901B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-A525F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    
    # Mobile - iOS
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    
    # Custom bot (default)
    UA,
]


class UserAgentManager:
    """Manage user agent rotation for stealth"""
    
    def __init__(self):
        self.agents = USER_AGENTS
        self.current_index = 0
        self.lock = threading.Lock()
    
    def get_agent(self) -> str:
        """Get the next user agent in rotation (thread-safe)"""
        with self.lock:
            agent = self.agents[self.current_index % len(self.agents)]
            self.current_index += 1
            return agent
    
    def get_random_agent(self) -> str:
        """Get a random user agent"""
        return random.choice(self.agents)
    
    def get_agent_for_browser(self, browser: str = "chrome") -> str:
        """Get a user agent for a specific browser type"""
        browser_agents = {
            "chrome": [a for a in self.agents if "Chrome" in a],
            "firefox": [a for a in self.agents if "Firefox" in a],
            "safari": [a for a in self.agents if "Safari" in a and "Chrome" not in a],
            "mobile": [a for a in self.agents if "Mobile" in a or "Android" in a or "iPhone" in a or "iPad" in a],
            "desktop": [a for a in self.agents if "Mobile" not in a and "Android" not in a]
        }
        
        if browser in browser_agents and browser_agents[browser]:
            return random.choice(browser_agents[browser])
        return self.get_random_agent()
    
    def reset(self):
        """Reset the rotation"""
        with self.lock:
            self.current_index = 0


# Global user agent manager
ua_manager = UserAgentManager()


# ============================================================================
# PROXY MANAGEMENT
# ============================================================================

@dataclass
class Proxy:
    """Represents a proxy server"""
    address: str
    port: int = None
    protocol: str = "http"  # http, https, socks5
    username: str = None
    password: str = None
    country: str = None
    is_working: bool = True
    response_time: float = 0.0
    error_count: int = 0
    
    def get_url(self) -> str:
        """Get proxy URL"""
        if self.port:
            return f"{self.protocol}://{self.address}:{self.port}"
        return f"{self.protocol}://{self.address}"
    
    def get_auth_url(self) -> str:
        """Get proxy URL with authentication"""
        if self.username and self.password:
            return f"{self.protocol}://{self.username}:{self.password}@{self.address}:{self.port}"
        return self.get_url()


@dataclass
class ProxyConfig:
    """Configuration for proxy management"""
    enabled: bool = False
    rotation_strategy: str = "round-robin"  # round-robin, random, fastest
    max_errors: int = 3
    timeout: float = 10.0
    proxies: List[Proxy] = field(default_factory=list)
    current_index: int = 0
    
    def get_proxy(self) -> Optional[Proxy]:
        """Get the next proxy based on rotation strategy"""
        if not self.enabled or not self.proxies:
            return None
        
        # Filter working proxies
        working_proxies = [p for p in self.proxies if p.is_working]
        if not working_proxies:
            return None
        
        if self.rotation_strategy == "random":
            return random.choice(working_proxies)
        elif self.rotation_strategy == "fastest":
            return min(working_proxies, key=lambda p: p.response_time)
        else:  # round-robin
            proxy = working_proxies[self.current_index % len(working_proxies)]
            self.current_index += 1
            return proxy
    
    def get_proxy_url(self) -> Optional[str]:
        """Get proxy URL for requests"""
        proxy = self.get_proxy()
        if proxy:
            return proxy.get_auth_url() if proxy.username else proxy.get_url()
        return None
    
    def get_proxy_dict(self) -> Optional[Dict[str, str]]:
        """Get proxy dict for requests library"""
        proxy = self.get_proxy()
        if proxy:
            url = proxy.get_auth_url() if proxy.username else proxy.get_url()
            return {
                "http": url,
                "https": url
            }
        return None
    
    def add_proxy(self, address: str, port: int = None, protocol: str = "http",
                  username: str = None, password: str = None, country: str = None):
        """Add a proxy to the pool"""
        proxy = Proxy(
            address=address,
            port=port,
            protocol=protocol,
            username=username,
            password=password,
            country=country
        )
        self.proxies.append(proxy)
    
    def load_from_file(self, file_path: str):
        """Load proxies from a text file (one per line)"""
        try:
            with open(file_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    # Parse proxy line (format: protocol://user:pass@host:port)
                    parts = line.split('://')
                    if len(parts) == 2:
                        protocol = parts[0].lower()
                        rest = parts[1]
                        
                        # Check for authentication
                        if '@' in rest:
                            auth, host_port = rest.split('@', 1)
                            if ':' in auth:
                                username, password = auth.split(':', 1)
                            else:
                                username = auth
                                password = None
                        else:
                            username = None
                            password = None
                            host_port = rest
                        
                        # Parse host:port
                        if ':' in host_port:
                            host, port = host_port.split(':', 1)
                            port = int(port) if port.isdigit() else None
                        else:
                            host = host_port
                            port = None
                        
                        self.add_proxy(
                            address=host,
                            port=port,
                            protocol=protocol,
                            username=username,
                            password=password
                        )
                    else:
                        # Simple format: host:port
                        if ':' in line:
                            host, port = line.split(':', 1)
                            self.add_proxy(address=host, port=int(port) if port.isdigit() else None)
                        else:
                            self.add_proxy(address=line)
            
            self.enabled = len(self.proxies) > 0
            log_discovery("proxy", "", "loaded", details=f"Loaded {len(self.proxies)} proxies")
        except Exception as e:
            log_discovery("proxy", "", "error", details=f"Failed to load proxies: {e}")
    
    def mark_proxy_failed(self, proxy: Proxy):
        """Mark a proxy as failed"""
        proxy.is_working = False
        log_discovery("proxy", proxy.get_url(), "failed", details="Proxy marked as failed")
    
    def mark_proxy_working(self, proxy: Proxy, response_time: float = 0.0):
        """Mark a proxy as working"""
        proxy.is_working = True
        proxy.response_time = response_time
        proxy.error_count = 0


# Global proxy manager
proxy_manager = ProxyConfig()


# ============================================================================
# RATE LIMITING
# ============================================================================

class RateLimiter:
    """Advanced rate limiting with adaptive delays"""
    
    def __init__(self,
                 requests_per_minute: int = 30,
                 min_delay: float = 1.0,
                 max_delay: float = 5.0,
                 adaptive: bool = True):
        self.requests_per_minute = requests_per_minute
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.adaptive = adaptive
        self.last_request_time = None
        self.request_history = []  # Timestamps of recent requests
        self.error_count = 0
        self.success_count = 0
        self.current_delay = min_delay
    
    def wait(self):
        """Wait for the appropriate delay before next request"""
        now = time.time()
        
        # Clean old history (older than 60 seconds)
        self.request_history = [t for t in self.request_history if now - t < 60]
        
        # Calculate adaptive delay
        if self.adaptive and len(self.request_history) > 0:
            total_requests = len(self.request_history)
            if total_requests > 5:
                recent_errors = sum(1 for t in self.request_history[-5:] if isinstance(t, tuple) and not t[1])
                error_rate = recent_errors / 5
                
                if error_rate > 0.2:  # More than 20% errors
                    self.current_delay = min(self.current_delay * 1.5, self.max_delay)
                elif error_rate < 0.05:  # Less than 5% errors
                    self.current_delay = max(self.current_delay * 0.9, self.min_delay)
        
        # Enforce requests per minute limit
        if len(self.request_history) >= self.requests_per_minute:
            oldest = min(self.request_history)
            elapsed = now - oldest
            min_required_delay = 60.0 / self.requests_per_minute
            if elapsed < min_required_delay:
                time.sleep(min_required_delay - elapsed)
        
        # Apply current delay
        if self.last_request_time:
            elapsed = now - self.last_request_time
            if elapsed < self.current_delay:
                time.sleep(self.current_delay - elapsed)
        
        self.last_request_time = time.time()
    
    def record(self, success: bool):
        """Record a request result"""
        if success:
            self.success_count += 1
            self.error_count = max(0, self.error_count - 1)
        else:
            self.error_count += 1
        
        self.request_history.append((self.last_request_time, success))
    
    def get_stats(self) -> Dict[str, Any]:
        """Get rate limiter statistics"""
        total = self.success_count + self.error_count
        return {
            "current_delay": self.current_delay,
            "min_delay": self.min_delay,
            "max_delay": self.max_delay,
            "requests_per_minute": self.requests_per_minute,
            "total_requests": total,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "success_rate": self.success_count / max(1, total)
        }


# Global rate limiter
rate_limiter = RateLimiter()


# ============================================================================
# REQUEST HEADERS MANAGER
# ============================================================================

class HeaderManager:
    """Generate realistic request headers"""
    
    def __init__(self):
        self.referrers = [
            "https://www.google.com/",
            "https://www.google.com/search",
            "https://www.bing.com/",
            "https://www.bing.com/search",
            "https://duckduckgo.com/",
            "https://yahoo.com/",
            "https://www.facebook.com/",
            "https://www.linkedin.com/",
            "https://twitter.com/",
        ]
        self.languages = [
            "en-US,en;q=0.9",
            "en-US,en;q=0.8",
            "en-US,en-GB;q=0.9,en;q=0.8",
            "en-US,en;q=0.5",
        ]
        self.encodings = [
            "gzip, deflate, br",
            "gzip, deflate",
            "deflate, gzip",
        ]
    
    def get_headers(self, user_agent: str = None, referrer: str = None) -> Dict[str, str]:
        """Generate realistic request headers"""
        ua = user_agent or ua_manager.get_random_agent()
        
        # Check if it's a mobile user agent
        is_mobile = "Mobile" in ua or "Android" in ua or "iPhone" in ua or "iPad" in ua
        
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": random.choice(self.languages),
            "Accept-Encoding": random.choice(self.encodings),
            "Referer": referrer or random.choice(self.referrers),
            "Connection": "keep-alive",
            "DNT": "1",
        }
        
        if is_mobile:
            headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
            headers["Save-Data"] = "on"
        
        return headers
    
    def get_headers_for_url(self, url: str, user_agent: str = None) -> Dict[str, str]:
        """Generate headers appropriate for a specific URL"""
        headers = self.get_headers(user_agent)
        
        # Set appropriate Referer
        if url:
            parsed = urllib.parse.urlparse(url)
            if parsed.netloc:
                # Use a search engine referer for external sites
                headers["Referer"] = f"https://www.google.com/search?q=site:{parsed.netloc}"
        
        return headers


# Global header manager
header_manager = HeaderManager()


# ============================================================================
# BEHAVIOR SIMULATOR
# ============================================================================

class BehaviorSimulator:
    """Simulate human-like behavior patterns"""
    
    def __init__(self):
        self.mouse_movements = []
        self.click_patterns = []
        self.scroll_patterns = []
        self.typing_patterns = []
    
    def random_delay(self, base: float = 0.5, variance: float = 0.5) -> float:
        """Generate a random delay that simulates human behavior"""
        return random.uniform(max(0, base - variance), base + variance)
    
    def mouse_move(self, x: int = None, y: int = None) -> Dict[str, Any]:
        """Simulate a mouse movement"""
        if x is None:
            x = random.randint(100, 800)
        if y is None:
            y = random.randint(100, 600)
        
        movement = {
            "x": x,
            "y": y,
            "timestamp": time.time(),
            "duration": self.random_delay(0.1, 0.05)
        }
        self.mouse_movements.append(movement)
        return movement
    
    def click(self, x: int = None, y: int = None) -> Dict[str, Any]:
        """Simulate a mouse click"""
        if x is None or y is None:
            # Click on a random element
            x = random.randint(100, 800)
            y = random.randint(100, 600)
        
        # Move mouse to position first
        self.mouse_move(x, y)
        
        click = {
            "x": x,
            "y": y,
            "timestamp": time.time(),
            "button": "left",
            "duration": self.random_delay(0.05, 0.02)
        }
        self.click_patterns.append(click)
        return click
    
    def scroll(self, direction: str = "down", amount: int = None) -> Dict[str, Any]:
        """Simulate scrolling"""
        if amount is None:
            amount = random.randint(100, 500)
        
        scroll = {
            "direction": direction,
            "amount": amount,
            "timestamp": time.time(),
            "duration": self.random_delay(0.3, 0.2)
        }
        self.scroll_patterns.append(scroll)
        return scroll
    
    def type_text(self, text: str, input_field: str = None) -> Dict[str, Any]:
        """Simulate typing text"""
        typing = {
            "text": text,
            "field": input_field,
            "timestamp": time.time(),
            "duration": len(text) * self.random_delay(0.05, 0.03)
        }
        self.typing_patterns.append(typing)
        return typing
    
    def random_interaction(self) -> Dict[str, Any]:
        """Generate a random human-like interaction"""
        interaction_type = random.choice(["move", "scroll", "click", "type"])
        
        if interaction_type == "move":
            return self.mouse_move()
        elif interaction_type == "scroll":
            return self.scroll("down")
        elif interaction_type == "click":
            return self.click()
        else:  # type
            # Type a random search term
            search_terms = ["company", "product", "service", "solution", "pricing"]
            return self.type_text(random.choice(search_terms))


# Global behavior simulator
behavior_simulator = BehaviorSimulator()


# ============================================================================
# CAPTCHA DETECTION
# ============================================================================

class CaptchaDetector:
    """Detect CAPTCHAs on web pages"""
    
    def __init__(self):
        self.captcha_indicators = [
            # reCAPTCHA
            r'recaptcha',
            r'grecaptcha',
            r'recaptcha/api',
            r'google.com/recaptcha',
            r'sitekey',
            r'data-sitekey',
            
            # hCaptcha
            r'hcaptcha',
            r'hCaptcha',
            r'data-sitekey',
            r'hcaptcha.com',
            
            # FunCaptcha
            r'funcaptcha',
            r'FunCaptcha',
            
            # Simple CAPTCHA
            r'captcha',
            r'CAPTCHA',
            r'verify you are human',
            r'verify human',
            r'are you human',
            r'bot detection',
            r'robot verification',
            
            # Common CAPTCHA elements
            r'captcha-image',
            r'captcha_img',
            r'captchaImage',
            r'img.*captcha',
            
            # Cloudflare
            r'cloudflare',
            r'cf-ray',
            r'5xx error',
            
            # Akamai
            r'akamai',
            r'bot manager',
            
            # Incapsula
            r'incapsula',
            r'incap_ses',
            
            # PerimeterX
            r'perimeterx',
            r'pxhd',
            
            # Shape Security
            r'shape',
            r'botwall',
        ]
    
    def detect(self, html: str = None, headers: Dict[str, str] = None, 
               url: str = None, status_code: int = None) -> Dict[str, Any]:
        """
        Detect if a page has a CAPTCHA
        
        Returns a dictionary with detection results
        """
        result = {
            "detected": False,
            "type": None,
            "confidence": 0.0,
            "indicators": []
        }
        
        # Check status code
        if status_code and status_code in [403, 429, 503]:
            result["detected"] = True
            result["type"] = "rate_limited"
            result["confidence"] = 0.8
            result["indicators"].append(f"HTTP {status_code}")
        
        # Check headers
        if headers:
            header_indicators = [
                ("Server", ["Cloudflare", "Akamai", "Incapsula"]),
                ("X-Captcha", ["true", "yes", "1"]),
                ("X-Robot", ["true", "yes", "1"]),
                ("X-Bot", ["true", "yes", "1"]),
            ]
            
            for header, values in header_indicators:
                if header in headers:
                    header_value = headers[header].lower()
                    for value in values:
                        if value.lower() in header_value:
                            result["detected"] = True
                            result["type"] = "header_based"
                            result["confidence"] = max(result["confidence"], 0.7)
                            result["indicators"].append(f"Header: {header}={header_value}")
        
        # Check HTML
        if html:
            html_lower = html.lower()
            for indicator in self.captcha_indicators:
                if re.search(indicator, html_lower):
                    result["detected"] = True
                    result["type"] = "html_based"
                    result["confidence"] = max(result["confidence"], min(1.0, result["confidence"] + 0.15))
                    result["indicators"].append(f"HTML: {indicator}")
        
        # Check URL
        if url:
            url_lower = url.lower()
            for indicator in ["captcha", "verify", "bot", "robot"]:
                if indicator in url_lower:
                    result["detected"] = True
                    result["type"] = "url_based"
                    result["confidence"] = max(result["confidence"], 0.6)
                    result["indicators"].append(f"URL: {indicator}")
        
        # Cap confidence at 1.0
        result["confidence"] = min(1.0, result["confidence"])
        
        return result


# Global CAPTCHA detector
captcha_detector = CaptchaDetector()


# ============================================================================
# STEALTH UTILITIES
# ============================================================================

def get_random_user_agent() -> str:
    """Get a random user agent"""
    return ua_manager.get_random_agent()


def get_random_proxy() -> Optional[str]:
    """Get a random proxy URL"""
    proxy = proxy_manager.get_proxy()
    if proxy:
        return proxy.get_auth_url() if proxy.username else proxy.get_url()
    return None


def get_random_delay(min_delay: float = 1.0, max_delay: float = 5.0) -> float:
    """Get a random delay between requests"""
    return random.uniform(min_delay, max_delay)


def human_like_delay(base: float = 1.0) -> float:
    """Generate a human-like delay with variance"""
    return behavior_simulator.random_delay(base, base * 0.5)


def load_proxies_from_file(file_path: str):
    """Load proxies from a file"""
    proxy_manager.load_from_file(file_path)
    return proxy_manager.proxies


def enable_proxies(enabled: bool = True):
    """Enable or disable proxy rotation"""
    proxy_manager.enabled = enabled


def get_stealth_headers(url: str = None, user_agent: str = None) -> Dict[str, str]:
    """Get headers optimized for stealth"""
    return header_manager.get_headers_for_url(url, user_agent)
