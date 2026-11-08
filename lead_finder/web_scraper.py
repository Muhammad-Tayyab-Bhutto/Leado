"""
Advanced Web Scraper - Core scraping functionality

This module provides:
1. Advanced HTTP requests with retries and rate limiting
2. HTML parsing and content extraction
3. Form submission and login automation
4. Pagination handling
5. Session management
6. Content caching
"""
import os
import re
import json
import time
import random
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple, Callable
from dataclasses import dataclass

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .config import UA
from .http_utils import http_get, clean_text
from .database import conn as finder_conn, log_discovery, cache_scrape, get_cached_scrape


# ============================================================================
# SCRAPER CONFIGURATION
# ============================================================================

@dataclass
class ScraperConfig:
    """Configuration for the web scraper"""
    timeout: int = 30
    max_retries: int = 5
    min_delay: float = 1.0
    max_delay: float = 5.0
    use_cache: bool = True
    cache_ttl: int = 3600  # 1 hour
    user_agent: str = UA
    adaptive_rate: bool = True


# ============================================================================
# CORE SCRAPER
# ============================================================================

class WebScraper:
    """
    Advanced web scraper with:
    - Rate limiting
    - Intelligent retries
    - Content caching
    - Error handling
    - Session management
    """
    
    def __init__(self, config: ScraperConfig = None):
        self.config = config or ScraperConfig()
        self.last_request_time = None
        self.request_count = 0
        self.error_count = 0
        self.session_cookies = {}
    
    def _get_cache_key(self, url: str, method: str = "GET", data: dict = None) -> str:
        """Generate cache key for a request"""
        import hashlib
        data_str = json.dumps(data, sort_keys=True) if data else ""
        return hashlib.sha256(f"{method}:{url}:{data_str}".encode()).hexdigest()
    
    def _get_from_cache(self, url: str, method: str = "GET", data: dict = None) -> Optional[str]:
        """Try to get content from cache"""
        if not self.config.use_cache:
            return None
        
        cache_key = self._get_cache_key(url, method, data)
        cached = get_cached_scrape(cache_key)
        if cached and (datetime.now(timezone.utc) - datetime.fromisoformat(cached["timestamp"])).total_seconds() < self.config.cache_ttl:
            return cached["content"]
        return None
    
    def _save_to_cache(self, url: str, content: str, method: str = "GET", data: dict = None):
        """Save content to cache"""
        if not self.config.use_cache:
            return
        
        cache_key = self._get_cache_key(url, method, data)
        cache_scrape(cache_key, {
            "url": url,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")
        })
    
    def _check_rate_limit(self):
        """Apply rate limiting between requests"""
        if self.last_request_time:
            elapsed = time.time() - self.last_request_time
            # Adaptive delay based on error rate
            current_delay = self.config.min_delay
            if self.config.adaptive_rate and self.request_count > 0:
                error_rate = self.error_count / self.request_count
                if error_rate > 0.2:  # More than 20% errors
                    current_delay = min(self.config.max_delay, self.config.min_delay * (1 + error_rate * 2))
                elif error_rate < 0.05:  # Less than 5% errors
                    current_delay = max(self.config.min_delay * 0.8, self.config.min_delay)
            
            if elapsed < current_delay:
                time.sleep(current_delay - elapsed)
    
    def _make_request(self, url: str, method: str = "GET", data: dict = None, headers: dict = None) -> Tuple[str, Dict[str, str], int]:
        """
        Make an HTTP request with retry logic
        Returns: (content, headers, status_code)
        """
        # Check cache first
        cached_content = self._get_from_cache(url, method, data)
        if cached_content:
            return cached_content, {}, 200
        
        # Prepare headers
        request_headers = {
            "User-Agent": self.config.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive"
        }
        if headers:
            request_headers.update(headers)
        
        # Add cookies if available
        if self.session_cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in self.session_cookies.items())
            request_headers["Cookie"] = cookie_str
        
        attempt = 0
        last_exception = None
        
        while attempt <= self.config.max_retries:
            try:
                # Apply rate limiting
                self._check_rate_limit()
                
                # Make request
                if method.upper() == "GET":
                    req = urllib.request.Request(url, headers=request_headers)
                    with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                        content = response.read().decode('utf-8', 'replace')
                        status_code = response.getcode()
                        response_headers = dict(response.headers)
                elif method.upper() == "POST":
                    req = urllib.request.Request(
                        url,
                        data=urllib.parse.urlencode(data).encode() if data else None,
                        headers=request_headers,
                        method="POST"
                    )
                    with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                        content = response.read().decode('utf-8', 'replace')
                        status_code = response.getcode()
                        response_headers = dict(response.headers)
                else:
                    raise ValueError(f"Unsupported method: {method}")
                
                # Update error/success count
                if status_code >= 400:
                    self.error_count += 1
                else:
                    self.error_count = max(0, self.error_count - 1)
                self.request_count += 1
                self.last_request_time = time.time()
                
                # Update session cookies
                if 'set-cookie' in response_headers:
                    set_cookie = response_headers['set-cookie']
                    if isinstance(set_cookie, list):
                        for cookie in set_cookie:
                            self._update_cookie(cookie)
                    else:
                        self._update_cookie(set_cookie)
                
                # Cache the content
                self._save_to_cache(url, content, method, data)
                
                return content, response_headers, status_code
                
            except Exception as e:
                last_exception = e
                self.error_count += 1
                self.request_count += 1
                self.last_request_time = time.time()
                
                # Don't retry on these errors
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                
                # Check if we should retry
                if attempt < self.config.max_retries:
                    # Exponential backoff
                    delay = min(self.config.max_delay, self.config.min_delay * (2 ** attempt))
                    time.sleep(delay)
                    attempt += 1
                else:
                    break
        
        if last_exception:
            log_discovery("scraper", url, "failed", details=str(last_exception)[:200])
            raise last_exception
        
        raise Exception("Request failed after maximum retries")
    
    def _update_cookie(self, cookie_str: str):
        """Update session cookies from Set-Cookie header"""
        # Parse cookie (simplified - doesn't handle all edge cases)
        if '=' in cookie_str:
            name, value = cookie_str.split('=', 1)
            name = name.strip()
            value = value.split(';')[0].strip()
            self.session_cookies[name] = value
    
    def get(self, url: str, headers: dict = None) -> Tuple[str, Dict[str, str]]:
        """GET request with retry and rate limiting"""
        content, response_headers, _ = self._make_request(url, "GET", headers=headers)
        return content, response_headers
    
    def post(self, url: str, data: dict = None, headers: dict = None) -> Tuple[str, Dict[str, str]]:
        """POST request with retry and rate limiting"""
        content, response_headers, _ = self._make_request(url, "POST", data, headers=headers)
        return content, response_headers
    
    def extract_text(self, html: str) -> str:
        """Extract clean text from HTML using regex"""
        # Remove script and style tags
        text = re.sub(r'(?is)<(script|style|noscript|svg).*?</\1>', ' ', html)
        # Remove all HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # Remove excessive whitespace
        text = re.sub(r'\s+', ' ', text)
        # Unescape HTML entities
        import html as html_module
        text = html_module.unescape(text)
        return clean_text(text)
    
    def extract_links(self, html: str, base_url: str = None) -> List[str]:
        """Extract all links from HTML"""
        pattern = r'href=["\'](https?://[^"\']+|/[^"\']+)["\']'
        matches = re.findall(pattern, html)
        if base_url:
            matches = [urllib.parse.urljoin(base_url, m) for m in matches]
        return list(set(matches))
    
    def extract_emails(self, text: str) -> List[str]:
        """Extract email addresses from text"""
        email_pattern = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
        emails = re.findall(email_pattern, text)
        
        # Filter out generic and invalid emails
        filtered = []
        generic_patterns = ['info@', 'contact@', 'hello@', 'sales@', 'support@', 
                           'admin@', 'webmaster@', 'postmaster@', 'noreply@']
        
        for email in set(emails):
            email_lower = email.lower()
            if not any(email_lower.startswith(p) for p in generic_patterns):
                if len(email) <= 254 and '..' not in email:
                    filtered.append(email)
        
        return filtered
    
    def extract_phones(self, text: str) -> List[str]:
        """Extract phone numbers from text"""
        patterns = [
            r'\+?\d{1,3}[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',
            r'\(\d{3}\)\s?\d{3}[-.]?\d{4}',
            r'\d{3}[-.]?\d{3}[-.]?\d{4}'
        ]
        
        phones = []
        for pattern in patterns:
            phones.extend(re.findall(pattern, text))
        
        # Clean and validate
        cleaned = []
        for phone in set(phones):
            cleaned_phone = re.sub(r'[^\d\+]', '', phone)
            if len(cleaned_phone) >= 7:
                cleaned.append(cleaned_phone)
        
        return cleaned


# ============================================================================
# FORM SUBMISSION
# ============================================================================

class FormSubmitter:
    """Handle form submissions and logins"""
    
    def __init__(self, scraper: WebScraper = None):
        self.scraper = scraper or WebScraper()
    
    def find_forms(self, html: str, base_url: str = None) -> List[Dict[str, Any]]:
        """Find all forms in HTML"""
        forms = []
        
        # Parse with regex (simplified - for full parsing, use lxml or bs4)
        form_pattern = r'<form\s+([^>]*)>(.*?)</form>'
        for match in re.finditer(form_pattern, html, re.DOTALL | re.IGNORECASE):
            form_attrs = match.group(1)
            form_content = match.group(2)
            
            form_data = {
                'action': '',
                'method': 'GET',
                'inputs': []
            }
            
            # Parse action
            action_match = re.search(r'action=["\']([^"\']*)["\']', form_attrs, re.IGNORECASE)
            if action_match:
                form_data['action'] = action_match.group(1)
            
            # Parse method
            method_match = re.search(r'method=["\']([^"\']*)["\']', form_attrs, re.IGNORECASE)
            if method_match:
                form_data['method'] = method_match.group(1).upper()
            
            # Parse inputs
            input_pattern = r'<input\s+([^>]*)>'
            for input_match in re.finditer(input_pattern, form_content, re.IGNORECASE):
                input_attrs = input_match.group(1)
                input_data = {
                    'name': '',
                    'type': 'text',
                    'value': ''
                }
                
                name_match = re.search(r'name=["\']([^"\']*)["\']', input_attrs, re.IGNORECASE)
                if name_match:
                    input_data['name'] = name_match.group(1)
                
                type_match = re.search(r'type=["\']([^"\']*)["\']', input_attrs, re.IGNORECASE)
                if type_match:
                    input_data['type'] = type_match.group(1).lower()
                
                value_match = re.search(r'value=["\']([^"\']*)["\']', input_attrs, re.IGNORECASE)
                if value_match:
                    input_data['value'] = value_match.group(1)
                
                form_data['inputs'].append(input_data)
            
            if base_url:
                form_data['action'] = urllib.parse.urljoin(base_url, form_data['action'])
            
            forms.append(form_data)
        
        return forms
    
    def submit_form(self, form_data: Dict[str, Any], additional_data: Dict[str, str] = None) -> Tuple[str, Dict[str, str]]:
        """Submit a form"""
        payload = {}
        for input_tag in form_data.get('inputs', []):
            if input_tag.get('name') and input_tag.get('type') != 'submit':
                payload[input_tag['name']] = input_tag.get('value', '')
        
        if additional_data:
            payload.update(additional_data)
        
        method = form_data.get('method', 'GET')
        action = form_data.get('action', '')
        
        if method.upper() == 'GET':
            if payload:
                action = action + '?' + urllib.parse.urlencode(payload)
            return self.scraper.get(action)
        else:
            return self.scraper.post(action, data=payload)
    
    def login(self, login_url: str, username: str, password: str,
              username_field: str = "username",
              password_field: str = "password") -> bool:
        """Login to a website"""
        html, _ = self.scraper.get(login_url)
        forms = self.find_forms(html, login_url)
        
        for form in forms:
            input_names = [i.get('name', '') for i in form.get('inputs', [])]
            if username_field in input_names and password_field in input_names:
                login_data = {
                    username_field: username,
                    password_field: password
                }
                
                try:
                    response_html, _ = self.submit_form(form, login_data)
                    
                    # Check for success indicators
                    success_indicators = ['logout', 'sign out', 'log out', 'my account', 'dashboard']
                    if any(ind in response_html.lower() for ind in success_indicators):
                        return True
                except:
                    pass
        
        return False


# ============================================================================
# PAGINATION HANDLER
# ============================================================================

class PaginationHandler:
    """Handle pagination for scraping multiple pages"""
    
    def __init__(self, scraper: WebScraper = None):
        self.scraper = scraper or WebScraper()
    
    def detect_pagination(self, html: str, base_url: str = None) -> Optional[Dict[str, Any]]:
        """Detect pagination controls in HTML"""
        # Look for next page link
        next_patterns = [
            r'<a[^>]*(?:href|data-href)=["\']([^"\']*)["\'].*?(?:>|/)Next[^<]*</a',
            r'<a[^>]*(?:href|data-href)=["\']([^"\']*)["\'].*?>(?:»|&raquo;|&gt;)[^<]*</a',
            r'<a[^>]*(?:href|data-href)=["\']([^"\']*)["\'].*?>Next Page[^<]*</a'
        ]
        
        for pattern in next_patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                href = match.group(1)
                full_url = urllib.parse.urljoin(base_url, href) if base_url else href
                return {'type': 'next_link', 'next_url': full_url, 'has_more': True}
        
        # Look for page numbers
        page_pattern = r'<a[^>]*(?:href|data-href)=["\']([^"\']*\b(?:page|p|pg)=(\d+)[^"\']*)["\']'
        matches = re.findall(page_pattern, html, re.IGNORECASE)
        if matches:
            current_page = None
            next_page = None
            for _, page_num in matches:
                page_num = int(page_num)
                if not next_page or page_num > next_page:
                    next_page = page_num
            
            if next_page:
                return {'type': 'page_numbers', 'next_page': next_page, 'has_more': True}
        
        # Look for "Load More" button
        if re.search(r'Load More|Load more|load_more', html, re.IGNORECASE):
            return {'type': 'load_more', 'has_more': True}
        
        return None
    
    def scrape_paginated(self, start_url: str,
                         extract_callback: Callable[[str, str], Any],
                         max_pages: int = 10,
                         stop_condition: Callable[[Any], bool] = None) -> List[Any]:
        """Scrape multiple pages with pagination"""
        all_data = []
        current_url = start_url
        pages_scraped = 0
        
        while current_url and pages_scraped < max_pages:
            html, _ = self.scraper.get(current_url)
            page_data = extract_callback(html, current_url)
            all_data.extend(page_data if isinstance(page_data, list) else [page_data])
            
            if stop_condition and stop_condition(page_data):
                break
            
            pagination_info = self.detect_pagination(html, current_url)
            if not pagination_info or not pagination_info.get('has_more'):
                break
            
            if pagination_info['type'] == 'next_link':
                current_url = pagination_info.get('next_url')
            elif pagination_info['type'] == 'page_numbers':
                current_page = pagination_info.get('current_page', 1)
                next_page = pagination_info.get('next_page', current_page + 1)
                current_url = self._update_page_in_url(current_url, next_page)
            else:
                break
            
            pages_scraped += 1
            time.sleep(1.0)
        
        return all_data
    
    def _update_page_in_url(self, url: str, page_num: int) -> str:
        """Update page number in URL"""
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        
        for param in ['page', 'p', 'pg']:
            if param in query:
                query[param] = [str(page_num)]
                new_query = urllib.parse.urlencode(query, doseq=True)
                return parsed._replace(query=new_query).geturl()
        
        # If no page parameter, add it
        query['page'] = [str(page_num)]
        new_query = urllib.parse.urlencode(query, doseq=True)
        return parsed._replace(query=new_query).geturl()


# ============================================================================
# EXPORT
# ============================================================================

# Global instances
scraper = WebScraper()
form_submitter = FormSubmitter()
pagination_handler = PaginationHandler()


def get_scraper(config: ScraperConfig = None) -> WebScraper:
    """Get a new scraper instance"""
    return WebScraper(config)


def extract_with_retry(url: str, extract_fn: Callable, max_retries: int = 3) -> Any:
    """Extract data from a URL with retry logic"""
    for attempt in range(max_retries):
        try:
            html, _ = scraper.get(url)
            return extract_fn(html)
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)
    return None
