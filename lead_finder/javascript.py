"""
JavaScript Rendering and Browser Automation for Web Scraping

This module provides:
1. Headless browser simulation for JavaScript-rendered pages
2. SPA (Single Page Application) content extraction
3. Dynamic content interaction
4. WebSocket monitoring
5. Shadow DOM traversal
6. Iframe handling
"""
import os
import re
import json
import time
import random
import threading
from typing import List, Dict, Any, Optional, Tuple, Callable, Set
from dataclasses import dataclass, field

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .config import UA
from .stealth import ua_manager, proxy_manager, rate_limiter, header_manager, behavior_simulator
from .database import log_discovery
from .http_utils import http_get, clean_text


# ============================================================================
# JAVASCRIPT RENDERING ENGINE CONFIGURATION
# ============================================================================

@dataclass
class JSConfig:
    """Configuration for JavaScript rendering"""
    enable_javascript: bool = True
    max_wait_time: float = 10.0  # Max time to wait for JS to render
    min_wait_time: float = 1.0   # Minimum time to wait
    page_load_timeout: float = 30.0
    viewport_width: int = 1920
    viewport_height: int = 1080
    user_agent: str = None
    headless: bool = True
    disable_images: bool = True  # Improve performance
    block_ads: bool = True
    enable_cookies: bool = True
    custom_headers: Dict[str, str] = field(default_factory=dict)


# ============================================================================
# BROWSER SIMULATION ENGINE (Pure Python - No Selenium/Playwright)
# ============================================================================

class JavaScriptRenderer:
    """
    Simulates JavaScript rendering using a combination of:
    - HTTP requests with JavaScript execution tracking
    - HTML parsing with DOM simulation
    - XHR/fetch request interception
    - Dynamic content detection
    """
    
    def __init__(self, config: JSConfig = None):
        self.config = config or JSConfig()
        self.session_cookies = {}
        self.loaded_urls: Set[str] = set()
        self.xhr_requests: List[Dict[str, Any]] = []
        self.evaluated_scripts: List[str] = []
        self.dom_state: Dict[str, Any] = {}
        self.wait_strategies = [
            self._wait_for_timeout,
            self._wait_for_content_change,
            self._wait_for_network_idle
        ]
    
    def _get_user_agent(self) -> str:
        """Get user agent for this session"""
        return self.config.user_agent or ua_manager.get_random_agent()
    
    def _get_headers(self, url: str = None) -> Dict[str, str]:
        """Get headers for requests"""
        headers = header_manager.get_headers(self._get_user_agent())
        
        # Add cookie header if we have cookies
        if self.session_cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in self.session_cookies.items())
            headers["Cookie"] = cookie_str
        
        # Add custom headers
        headers.update(self.config.custom_headers)
        
        # Set Referer if we have a loaded URL
        if self.loaded_urls:
            headers["Referer"] = list(self.loaded_urls)[-1]
        
        return headers
    
    def _make_request(self, url: str, method: str = "GET", data: dict = None) -> Tuple[str, Dict[str, str], int]:
        """Make an HTTP request with full control"""
        import urllib.request
        import urllib.parse
        
        headers = self._get_headers(url)
        
        try:
            if method.upper() == "GET":
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.config.page_load_timeout) as response:
                    content = response.read().decode('utf-8', 'replace')
                    status_code = response.getcode()
                    response_headers = dict(response.headers)
            else:
                encoded_data = urllib.parse.urlencode(data).encode() if data else None
                req = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=self.config.page_load_timeout) as response:
                    content = response.read().decode('utf-8', 'replace')
                    status_code = response.getcode()
                    response_headers = dict(response.headers)
            
            # Update cookies from Set-Cookie header
            if 'set-cookie' in response_headers:
                set_cookie = response_headers['set-cookie']
                if isinstance(set_cookie, list):
                    for cookie in set_cookie:
                        self._update_cookie(cookie)
                else:
                    self._update_cookie(set_cookie)
            
            # Track loaded URLs
            self.loaded_urls.add(url)
            
            return content, response_headers, status_code
            
        except Exception as e:
            log_discovery("js_renderer", url, "error", details=f"Request failed: {e}")
            raise
    
    def _update_cookie(self, cookie_str: str):
        """Update session cookies from Set-Cookie header"""
        if '=' in cookie_str:
            name, value = cookie_str.split('=', 1)
            name = name.strip()
            value = value.split(';')[0].strip()
            self.session_cookies[name] = value
    
    def _wait_for_timeout(self, html: str, url: str) -> float:
        """Wait for a fixed timeout"""
        return self.config.max_wait_time * 0.5  # Wait half the max time
    
    def _wait_for_content_change(self, html: str, url: str) -> float:
        """Wait for content to change (simulated)"""
        # Check if this is a common SPA framework
        if any(framework in html.lower() for framework in ['react', 'angular', 'vue', 'svelte']):
            # SPAs typically need more time
            return self.config.max_wait_time * 0.8
        return self.config.min_wait_time
    
    def _wait_for_network_idle(self, html: str, url: str) -> float:
        """Wait for network to be idle (simulated)"""
        # For now, use a random delay
        return random.uniform(self.config.min_wait_time, self.config.max_wait_time * 0.3)
    
    def _extract_script_urls(self, html: str) -> List[str]:
        """Extract JavaScript file URLs from HTML"""
        urls = []
        
        # Find script tags with src attribute
        pattern = r'<script[^>]*src=["\'](https?://[^"\']+)["\']'
        for match in re.finditer(pattern, html, re.IGNORECASE):
            url = match.group(1)
            urls.append(url)
        
        return urls
    
    def _execute_javascript_simulation(self, html: str, url: str) -> str:
        """
        Simulate JavaScript execution by:
        1. Loading external scripts
        2. Following XHR/fetch requests
        3. Applying simple DOM transformations
        """
        import urllib.parse
        
        modified_html = html
        base_url = url
        
        # Step 1: Load external JavaScript files
        script_urls = self._extract_script_urls(html)
        for script_url in script_urls[:10]:  # Limit to 10 scripts
            try:
                # Only load scripts from the same domain
                script_domain = urllib.parse.urlparse(script_url).netloc
                page_domain = urllib.parse.urlparse(url).netloc
                
                if script_domain == page_domain or script_domain.endswith(page_domain):
                    js_content, _, _ = self._make_request(script_url)
                    self.evaluated_scripts.append(script_url)
                    
                    # Look for document.write or DOM manipulation
                    if 'document.write' in js_content or 'innerHTML' in js_content:
                        # Try to extract any dynamic content
                        content_match = re.search(r'(?i)(document\.write|innerHTML)\s*=\s*["\']([^"\']+)["\']', js_content)
                        if content_match:
                            # This is a simplified approach
                            modified_html = modified_html + f"<!-- Dynamic content from {script_url}: {content_match.group(2)} -->"
            except Exception:
                continue  # Skip failed script loads
        
        # Step 2: Look for XHR/fetch URLs in the HTML
        xhr_patterns = [
            r'fetch\(["\'](https?://[^"\']+)["\']',
            r'XMLHttpRequest.*?open\([^)]*?["\'](https?://[^"\']+)["\']',
            r'\$\.ajax\([^)]*?url:\s*["\'](https?://[^"\']+)["\']',
            r'axios\.[^)]*?\([^)]*?["\'](https?://[^"\']+)["\']'
        ]
        
        for pattern in xhr_patterns:
            for match in re.finditer(pattern, html, re.IGNORECASE):
                xhr_url = match.group(1)
                self.xhr_requests.append({
                    'url': xhr_url,
                    'method': 'GET',
                    'source': url
                })
        
        # Step 3: Look for JSON data embedded in the page
        json_pattern = r'<script[^>]*type=["\']application/json["\']>([\s\S]*?)</script>'
        for match in re.finditer(json_pattern, html):
            json_data = match.group(1)
            try:
                data = json.loads(json_data)
                # Extract any useful data and inject it into the HTML
                if isinstance(data, dict):
                    # This could be React hydration data or similar
                    modified_html = self._inject_json_data(modified_html, data)
            except:
                continue
        
        # Step 4: Look for common SPA data attributes
        spa_data_patterns = [
            (r'data-react-props=["\']([^"\']+)["\']', 'react-props'),
            (r'data-vue-props=["\']([^"\']+)["\']', 'vue-props'),
            (r'data-angular-data=["\']([^"\']+)["\']', 'angular-data')
        ]
        
        for pattern, data_type in spa_data_patterns:
            for match in re.finditer(pattern, modified_html):
                try:
                    data = json.loads(match.group(1).replace('&quot;', '"'))
                    modified_html = self._inject_spa_data(modified_html, data, data_type)
                except:
                    continue
        
        return modified_html
    
    def _inject_json_data(self, html: str, data: dict) -> str:
        """Inject JSON data into HTML for easier parsing"""
        # Create a div with the data as JSON
        json_str = json.dumps(data, indent=2)
        injection = f'<div class="json-data" style="display:none;">{json_str}</div>'
        return html + injection
    
    def _inject_spa_data(self, html: str, data: dict, data_type: str) -> str:
        """Inject SPA framework data into HTML"""
        json_str = json.dumps(data, indent=2)
        injection = f'<div class="{data_type}" style="display:none;">{json_str}</div>'
        return html + injection
    
    def _detect_dynamic_content(self, html: str) -> bool:
        """Detect if a page likely has dynamic content"""
        indicators = [
            r'React\.createElement',
            r'angular\.module',
            r'new Vue\(',
            r'ReactDOM\.render',
            r'document\.getElementById',
            r'fetch\(',
            r'XMLHttpRequest',
            r'axios\.',
            r'\.innerHTML\s*=',
            r'\.appendChild\(',
            r'setTimeout\(',
            r'setInterval\('
        ]
        
        for indicator in indicators:
            if re.search(indicator, html, re.IGNORECASE):
                return True
        return False
    
    def render(self, url: str, wait_for: str = None, wait_timeout: float = None) -> Tuple[str, Dict[str, Any]]:
        """
        Render a page with JavaScript support (simulated)
        
        Returns: (rendered_html, metadata)
        """
        wait_timeout = wait_timeout or self.config.max_wait_time
        
        # Rate limiting
        rate_limiter.wait()
        
        # Step 1: Make initial request
        content, headers, status_code = self._make_request(url)
        
        # Check for redirects
        if status_code in [301, 302, 303, 307, 308]:
            if 'location' in headers:
                return self.render(headers['location'], wait_for, wait_timeout)
        
        # Step 2: Check if page has dynamic content
        has_dynamic = self._detect_dynamic_content(content)
        
        # Step 3: Simulate JavaScript rendering
        if has_dynamic:
            # Simulate waiting
            wait_time = 0
            for strategy in self.wait_strategies:
                wait_time = max(wait_time, strategy(content, url))
            
            actual_wait = min(wait_time, wait_timeout)
            if actual_wait > 0:
                time.sleep(actual_wait)
            
            # Simulate JS execution
            content = self._execute_javascript_simulation(content, url)
        
        # Step 4: Load any XHR requests we found
        if self.xhr_requests:
            for xhr in self.xhr_requests[:5]:  # Limit to 5 XHR requests
                try:
                    xhr_content, _, _ = self._make_request(xhr['url'])
                    # Append XHR content to the page
                    content = content + f"<!-- XHR Response from {xhr['url']}: {xhr_content[:500]}... -->"
                except:
                    continue
        
        metadata = {
            "url": url,
            "final_url": url,
            "status_code": status_code,
            "has_dynamic_content": has_dynamic,
            "wait_time": actual_wait if has_dynamic else 0,
            "scripts_loaded": len(self.evaluated_scripts),
            "xhr_requests": len(self.xhr_requests),
            "headers": headers
        }
        
        return content, metadata
    
    def render_and_extract(self, url: str, extract_fn: Callable, wait_for: str = None) -> Any:
        """
        Render a page and extract data using a callback function
        
        Args:
            url: URL to render
            extract_fn: Function that takes HTML and returns extracted data
            wait_for: Optional element to wait for
            
        Returns: Result of extract_fn
        """
        html, metadata = self.render(url, wait_for)
        
        # Simulate human-like behavior
        behavior_simulator.random_interaction()
        
        return extract_fn(html, metadata)
    
    def extract_json_from_page(self, url: str) -> List[Dict[str, Any]]:
        """Extract all JSON data from a page"""
        html, _ = self.render(url)
        
        results = []
        
        # Extract JSON from script tags
        json_pattern = r'<script[^>]*(?:type=["\']application/json["\']|id=["\'][^"\']*json[^"\']*["\'])[^>]*>([\s\S]*?)</script>'
        for match in re.finditer(json_pattern, html):
            try:
                data = json.loads(match.group(1))
                results.append(data)
            except:
                continue
        
        # Extract JSON from data attributes
        data_attr_pattern = r'data-[a-z-]+=["\']([^"\']+)["\']'
        for match in re.finditer(data_attr_pattern, html):
            try:
                data = json.loads(match.group(1).replace('&quot;', '"'))
                results.append(data)
            except:
                continue
        
        return results
    
    def click(self, url: str, selector: str = None, xpath: str = None) -> Tuple[str, Dict[str, Any]]:
        """
        Simulate a click on a page (simplified)
        
        Args:
            url: URL to navigate to first
            selector: CSS selector to click
            xpath: XPath to click
            
        Returns: (new_html, metadata)
        """
        html, metadata = self.render(url)
        
        # Simulate click by following links
        if selector or xpath:
            # Try to find the link
            if selector:
                # Simple CSS selector simulation
                link_pattern = fr'<a[^>]*(?:{selector.replace(" ", ".*")})[^>]*href=["\']([^"\']+)["\']'
                match = re.search(link_pattern, html, re.IGNORECASE)
                if match:
                    new_url = match.group(1)
                    if not new_url.startswith('http'):
                        parsed = urllib.parse.urlparse(url)
                        new_url = parsed.scheme + '://' + parsed.netloc + new_url
                    return self.render(new_url)
            
            if xpath:
                # Simple XPath simulation (very basic)
                if 'a' in xpath.lower() and 'href' in xpath.lower():
                    link_pattern = r'<a[^>]*href=["\']([^"\']+)["\']'
                    match = re.search(link_pattern, html, re.IGNORECASE)
                    if match:
                        new_url = match.group(1)
                        if not new_url.startswith('http'):
                            parsed = urllib.parse.urlparse(url)
                            new_url = parsed.scheme + '://' + parsed.netloc + new_url
                        return self.render(new_url)
        
        return html, metadata
    
    def type_text(self, url: str, selector: str, text: str) -> Tuple[str, Dict[str, Any]]:
        """Simulate typing text into a form field"""
        html, metadata = self.render(url)
        
        # For now, just return the page - form submission would need to be handled separately
        # In a full implementation, we'd track form state
        behavior_simulator.type_text(text, selector)
        
        return html, metadata
    
    def wait_for_selector(self, url: str, selector: str, timeout: float = None) -> bool:
        """
        Wait for a selector to appear on the page
        
        Note: This is a simplified simulation. In a real browser, we'd actively check.
        """
        html, _ = self.render(url, wait_timeout=timeout)
        
        # Check if selector exists in HTML
        selector_pattern = selector.replace(" ", ".*")
        # Convert selector to regex pattern
        pattern_str = selector_pattern.replace("#", "[id=\\\"|#]").replace(".", "[class=\\\"|\\\\.]")
        pattern = f'<{pattern_str}[^>]*>'
        
        return bool(re.search(pattern, html, re.IGNORECASE))
    
    def get_screenshot(self, url: str) -> bytes:
        """
        Simulate getting a screenshot (returns placeholder)
        
        In a full implementation with a real browser, this would capture the screen.
        """
        # For now, return a placeholder
        return b"SCREENSHOT_PLACEHOLDER"


# ============================================================================
# SHADOW DOM HANDLER
# ============================================================================

class ShadowDOMHandler:
    """Handle Shadow DOM content extraction"""
    
    def __init__(self):
        self.shadow_roots = {}
    
    def extract_shadow_content(self, html: str, parent_selector: str = None) -> List[Dict[str, Any]]:
        """Extract content from Shadow DOM"""
        results = []
        
        # Look for shadow root elements
        shadow_selectors = [
            r'<[^>]+\s+[a-z-]+\s+[^>]*>',  # Elements with custom element names
            r'customElements\.define\(',
            r'attachShadow\(',
            r'shadowRoot'
        ]
        
        for pattern in shadow_selectors:
            matches = list(re.finditer(pattern, html, re.IGNORECASE))
            for match in matches:
                results.append({
                    'selector': parent_selector,
                    'content': match.group(0),
                    'type': 'shadow_root'
                })
        
        return results
    
    def flatten_shadow_dom(self, html: str) -> str:
        """Flatten Shadow DOM into regular HTML (simulated)"""
        # This is a simplified approach - in reality, we'd need a full DOM parser
        return html


# ============================================================================
# IFRAME HANDLER
# ============================================================================

class IframeHandler:
    """Handle iframe content extraction"""
    
    def __init__(self, renderer: JavaScriptRenderer = None):
        self.renderer = renderer or JavaScriptRenderer()
    
    def extract_iframe_content(self, html: str, base_url: str) -> Dict[str, str]:
        """Extract content from iframes"""
        import urllib.parse
        
        iframe_contents = {}
        
        # Find iframe tags
        iframe_pattern = r'<iframe[^>]*src=["\'](https?://[^"\']+)["\'][^>]*>'
        for match in re.finditer(iframe_pattern, html):
            iframe_url = match.group(1)
            
            # Resolve relative URLs
            if not iframe_url.startswith('http'):
                iframe_url = urllib.parse.urljoin(base_url, iframe_url)
            
            try:
                # Render the iframe content
                iframe_html, _ = self.renderer.render(iframe_url)
                iframe_contents[iframe_url] = iframe_html
            except Exception as e:
                log_discovery("iframe", iframe_url, "error", details=str(e))
                iframe_contents[iframe_url] = f"<!-- Error loading iframe: {e} -->"
        
        return iframe_contents
    
    def get_all_iframe_data(self, url: str) -> Dict[str, Any]:
        """Get all data from iframes on a page"""
        html, metadata = self.renderer.render(url)
        iframes = self.extract_iframe_content(html, url)
        
        return {
            'page': html,
            'iframes': iframes,
            'metadata': metadata
        }


# ============================================================================
# WEBSOCKET MONITOR
# ============================================================================

class WebSocketMonitor:
    """Monitor WebSocket connections (simulated)"""
    
    def __init__(self):
        self.websocket_urls: List[str] = []
        self.messages: List[Dict[str, Any]] = []
    
    def detect_websocket_urls(self, html: str, base_url: str) -> List[str]:
        """Detect WebSocket URLs in HTML"""
        import urllib.parse
        
        urls = []
        
        # Look for WebSocket connections in scripts
        ws_patterns = [
            r'new WebSocket\(["\'](wss?://[^"\']+)["\']',
            r'WebSocket\(["\'](wss?://[^"\']+)["\']',
            r'ws://[^\s\'\"<>]+',
            r'wss://[^\s\'\"<>]+'
        ]
        
        for pattern in ws_patterns:
            for match in re.finditer(pattern, html):
                ws_url = match.group(1) if 'wss?' in pattern else match.group(0)
                if not ws_url.startswith('ws'):
                    ws_url = 'wss://' + ws_url
                urls.append(ws_url)
        
        return urls
    
    def monitor_websocket(self, url: str, callback: Callable = None):
        """
        Monitor WebSocket connection (simulated)
        
        In a full implementation, we'd use a real WebSocket client.
        """
        # For now, just detect and log
        self.websocket_urls.append(url)
        if callback:
            callback({'type': 'connected', 'url': url})


# ============================================================================
# SPA FRAMEWORK DETECTOR
# ============================================================================

class SPAFrameworkDetector:
    """Detect and extract data from SPA frameworks"""
    
    FRAMEWORK_INDICATORS = {
        'react': [
            r'React\.createElement',
            r'ReactDOM\.render',
            r'data-reactroot',
            r'react-dom',
            r'React\.createContext'
        ],
        'angular': [
            r'ng-',
            r'angular\.',
            r'data-ng-',
            r'\\*ng',
            r'Angular'
        ],
        'vue': [
            r'data-v-',
            r'new Vue\(',
            r'Vue\.component',
            r'vue\.js',
            r'v-bind:',
            r'v-on:'
        ],
        'svelte': [
            r'Svelte',
            r'svelte',
            r'data-svelte',
            r'new Svelte'
        ],
        'nextjs': [
            r'__NEXT_DATA__',
            r'next/dist',
            r'Next.js'
        ],
        'nuxt': [
            r'__NUXT__',
            r'nuxt',
            r'Nuxt.js'
        ]
    }
    
    def detect_framework(self, html: str) -> Dict[str, Any]:
        """Detect which SPA framework is being used"""
        results = {}
        
        for framework, indicators in self.FRAMEWORK_INDICATORS.items():
            for indicator in indicators:
                if re.search(indicator, html, re.IGNORECASE):
                    results[framework] = True
                    break
        
        return results
    
    def extract_nextjs_data(self, html: str) -> Optional[Dict[str, Any]]:
        """Extract data from Next.js applications"""
        # Look for __NEXT_DATA__ JSON
        pattern = r'<script[^>]*id=["\']__NEXT_DATA__["\']>([\s\S]*?)</script>'
        match = re.search(pattern, html)
        
        if match:
            try:
                return json.loads(match.group(1))
            except:
                return None
        return None
    
    def extract_nuxt_data(self, html: str) -> Optional[Dict[str, Any]]:
        """Extract data from Nuxt.js applications"""
        # Look for __NUXT__ JSON
        pattern = r'window\.__NUXT__\s*=\s*\{([^}]+)\}'
        match = re.search(pattern, html)
        
        if match:
            try:
                # This is a simplified parsing - real Nuxt data is more complex
                data_str = match.group(1)
                # Try to extract JSON from it
                data_str = data_str.replace(':', '":"').replace(',', '","')
                return json.loads('{' + data_str + '}')
            except:
                return None
        return None
    
    def extract_react_props(self, html: str) -> List[Dict[str, Any]]:
        """Extract React component props from HTML"""
        results = []
        
        # Look for data-react-props
        pattern = r'data-react-props=["\']([^"\']+)["\']'
        for match in re.finditer(pattern, html):
            try:
                data = json.loads(match.group(1).replace('&quot;', '"'))
                results.append(data)
            except:
                continue
        
        return results
    
    def extract_vue_data(self, html: str) -> List[Dict[str, Any]]:
        """Extract Vue.js data from HTML"""
        results = []
        
        # Look for Vue data in script tags or inline
        patterns = [
            r'<script[^>]*>([\s\S]*?new Vue\([\s\S]*?\{[\s\S]*?\})[\s\S]*?)</script>',
            r'data:\s*\{([^}]+)\}'
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, html):
                try:
                    data_str = match.group(1)
                    # Try to parse as JSON
                    if data_str.startswith('{') and data_str.endswith('}'):
                        data = json.loads(data_str)
                        results.append(data)
                    elif ':' in data_str:
                        # Try to fix the JSON
                        fixed = data_str.replace(':', '":"').replace(',', '","')
                        data = json.loads('{' + fixed + '}')
                        results.append(data)
                except:
                    continue
        
        return results


# ============================================================================
# LAZY LOADING HANDLER
# ============================================================================

class LazyLoadingHandler:
    """Handle lazy-loaded content"""
    
    def __init__(self, renderer: JavaScriptRenderer = None):
        self.renderer = renderer or JavaScriptRenderer()
    
    def detect_lazy_loading(self, html: str) -> List[Dict[str, Any]]:
        """Detect lazy-loaded elements"""
        lazy_elements = []
        
        # Look for common lazy loading patterns
        patterns = [
            (r'<img[^>]*loading=["\']lazy["\']', 'img'),
            (r'<img[^>]*data-src=["\']([^"\']+)["\']', 'img'),
            (r'<img[^>]*data-lazy=["\']([^"\']+)["\']', 'img'),
            (r'<div[^>]*data-load=["\']([^"\']+)["\']', 'div'),
            (r'lazyload', 'generic'),
            (r'IntersectionObserver', 'observer')
        ]
        
        for pattern, element_type in patterns:
            for match in re.finditer(pattern, html, re.IGNORECASE):
                lazy_elements.append({
                    'type': element_type,
                    'content': match.group(0),
                    'pattern': pattern
                })
        
        return lazy_elements
    
    def load_lazy_content(self, url: str) -> Tuple[str, List[Dict[str, Any]]]:
        """Load all lazy content on a page"""
        html, metadata = self.renderer.render(url)
        lazy_elements = self.detect_lazy_loading(html)
        
        # For lazy images with data-src, replace src with data-src
        modified_html = re.sub(
            r'(<img[^>]*)src=["\']([^"\']+)["\']([^>]*data-src=["\']([^"\']+)["\'])',
            r'\1src="\4"\3',
            html
        )
        
        return modified_html, lazy_elements


# ============================================================================
# DYNAMIC CONTENT TRACKER
# ============================================================================

class DynamicContentTracker:
    """Track and extract dynamic content changes"""
    
    def __init__(self):
        self.html_history = []
        self.content_hashes = set()
    
    def track_changes(self, html: str, url: str) -> Dict[str, Any]:
        """Track changes in HTML content"""
        import hashlib
        
        content_hash = hashlib.sha256(html.encode()).hexdigest()
        
        if content_hash in self.content_hashes:
            return {'changed': False, 'hash': content_hash}
        
        self.content_hashes.add(content_hash)
        self.html_history.append({'url': url, 'html': html, 'hash': content_hash, 'time': time.time()})
        
        # Keep only last 5 versions
        if len(self.html_history) > 5:
            self.html_history.pop(0)
        
        return {'changed': True, 'hash': content_hash, 'history_size': len(self.html_history)}
    
    def get_diff(self, old_html: str, new_html: str) -> List[Dict[str, Any]]:
        """Get differences between two HTML versions"""
        # Simple word-based diff
        old_words = old_html.split()
        new_words = new_html.split()
        
        diffs = []
        
        # Find added and removed elements
        old_set = set(old_words)
        new_set = set(new_words)
        
        added = new_set - old_set
        removed = old_set - new_set
        
        if added:
            diffs.append({'type': 'added', 'content': ' '.join(list(added)[:10])})
        if removed:
            diffs.append({'type': 'removed', 'content': ' '.join(list(removed)[:10])})
        
        return diffs


# ============================================================================
# EXPORT
# ============================================================================

# Global instances
js_renderer = JavaScriptRenderer()
shadow_dom_handler = ShadowDOMHandler()
iframe_handler = IframeHandler()
websocket_monitor = WebSocketMonitor()
spa_detector = SPAFrameworkDetector()
lazy_loader = LazyLoadingHandler()
dynamic_tracker = DynamicContentTracker()


def render_page(url: str, wait_for: str = None, timeout: float = None) -> Tuple[str, Dict[str, Any]]:
    """Render a page with JavaScript support"""
    return js_renderer.render(url, wait_for, timeout)


def render_and_extract(url: str, extract_fn: Callable) -> Any:
    """Render a page and extract data"""
    return js_renderer.render_and_extract(url, extract_fn)


def detect_framework(html: str) -> Dict[str, Any]:
    """Detect SPA framework"""
    return spa_detector.detect_framework(html)


def extract_spa_data(html: str) -> Dict[str, Any]:
    """Extract data from SPA frameworks"""
    data = {}
    
    nextjs_data = spa_detector.extract_nextjs_data(html)
    if nextjs_data:
        data['nextjs'] = nextjs_data
    
    nuxt_data = spa_detector.extract_nuxt_data(html)
    if nuxt_data:
        data['nuxt'] = nuxt_data
    
    react_props = spa_detector.extract_react_props(html)
    if react_props:
        data['react_props'] = react_props
    
    vue_data = spa_detector.extract_vue_data(html)
    if vue_data:
        data['vue'] = vue_data
    
    return data


def get_framework() -> Dict[str, bool]:
    """Detect framework from current page"""
    # This would need a URL parameter in a real implementation
    return {}


if __name__ == "__main__":
    # Test the JavaScript renderer
    print("Testing JavaScript Renderer...")
    
    # Test with a simple URL
    try:
        html, metadata = render_page("https://example.com")
        print(f"Successfully rendered: {metadata.get('url')}")
        print(f"Status: {metadata.get('status_code')}")
        print(f"Dynamic content: {metadata.get('has_dynamic_content')}")
    except Exception as e:
        print(f"Error: {e}")
