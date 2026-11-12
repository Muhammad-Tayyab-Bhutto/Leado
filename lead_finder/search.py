"""
Advanced Search Engine Scraping

This module provides:
1. Google search scraping with multiple methods
2. Bing search scraping
3. Yahoo search scraping
4. DuckDuckGo search scraping
5. Advanced query construction
6. Search result parsing
7. SERP (Search Engine Results Page) analysis
8. Search API integration (SerpAPI, Google Custom Search)
9. Organic results extraction
10. Paid results extraction
11. Featured snippets extraction
12. Knowledge panel extraction
"""
import os
import re
import json
import time
import random
import urllib.parse
import urllib.request
from typing import List, Dict, Any, Optional, Tuple, Callable
from dataclasses import dataclass, field

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .config import UA, SERPAPI_KEY, RATE_LIMIT_DELAY, domain_of
from .politeness import ua_manager, proxy_manager, rate_limiter, header_manager, behavior_simulator
from .database import log_discovery
from .http_utils import http_get, http_post, clean_text
from .javascript import render_page, js_renderer
from .captcha import detect_captcha, solver_manager


# ============================================================================
# SEARCH ENGINE CONFIGURATION
# ============================================================================

@dataclass
class SearchConfig:
    """Configuration for search engine scraping"""
    user_agent: str = None
    
    # Rate limiting
    delay_between_requests: float = 2.0
    max_requests_per_minute: int = 20
    
    # Results
    results_per_page: int = 10
    max_pages: int = 5
    
    # Proxies
    use_proxies: bool = False
    
    # API keys
    serpapi_key: str = None
    google_custom_search_key: str = None
    google_custom_search_cx: str = None
    
    # Query settings
    safe_search: bool = False
    language: str = "en"
    region: str = "us"
    
    # Timeouts
    timeout: float = 30.0
    
    # Result filtering
    exclude_domains: List[str] = field(default_factory=list)
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)


# ============================================================================
# SEARCH QUERY BUILDER
# ============================================================================

class SearchQueryBuilder:
    """Build advanced search queries"""
    
    OPERATORS = {
        "exact_phrase": '"{}"',
        "or": '{} OR {}',
        "and": '{} AND {}',
        "not": '-{}',
        "site": 'site:{}',
        "filetype": 'filetype:{}',
        "intitle": 'intitle:{}',
        "inurl": 'inurl:{}',
        "intext": 'intext:{}',
        "define": 'define:{}',
        "cache": 'cache:{}',
        "link": 'link:{}',
        "related": 'related:{}',
        "info": 'info:{}',
        "time_range": '{}:{}',  # e.g., "after:2024-01-01"
        "before": 'before:{}',
        "after": 'after:{}'
    }
    
    def __init__(self):
        pass
    
    def build(self, query: str, **kwargs) -> str:
        """Build a search query with operators"""
        parts = [query]
        
        for key, value in kwargs.items():
            if key in self.OPERATORS and value:
                if isinstance(value, list):
                    for v in value:
                        parts.append(self.OPERATORS[key].format(v))
                else:
                    parts.append(self.OPERATORS[key].format(value))
        
        return " ".join(parts)
    
    def build_boolean(self, terms: List[str], operator: str = "AND") -> str:
        """Build a boolean query"""
        op = self.OPERATORS.get(f"_{operator.lower()}", f" {operator} ")
        return op.join(terms)
    
    def build_complex(self, must_include: List[str] = None, must_not_include: List[str] = None,
                     exact_phrases: List[str] = None, sites: List[str] = None,
                     filetypes: List[str] = None) -> str:
        """Build a complex search query"""
        parts = []
        
        if must_include:
            for term in must_include:
                parts.append(f'"{term}"')
        
        if exact_phrases:
            for phrase in exact_phrases:
                parts.append(f'"{phrase}"')
        
        if must_not_include:
            for term in must_not_include:
                parts.append(f'-{term}')
        
        if sites:
            for site in sites:
                parts.append(f'site:{site}')
        
        if filetypes:
            for filetype in filetypes:
                parts.append(f'filetype:{filetype}')
        
        return " ".join(parts)
    
    def build_b2b_query(self, service: str, industry: str = None, location: str = None,
                       company_size: str = None) -> str:
        """Build a B2B lead finding query"""
        parts = [f'"{service}"']
        
        if industry:
            parts.append(f'"{industry}"')
        
        if location:
            parts.append(f'"{location}" OR "{location} based"')
        
        # Exclude generic results
        parts.append('-job -jobs -careers -hiring -recruitment')
        parts.append('-github -gitlab -stackoverflow -wikipedia')
        parts.append('-pdf -doc -xls -ppt')
        
        return " ".join(parts)
    
    def build_competitor_query(self, competitor: str, keyword: str) -> str:
        """Build a query to find sites similar to a competitor"""
        return f'related:{competitor} "{keyword}"'
    
    def build_intent_query(self, keyword: str, intent: str = "commercial") -> str:
        """Build a query targeting specific user intent"""
        if intent == "commercial":
            return f'"{keyword}" (buy OR purchase OR hire OR service OR company OR agency)'
        elif intent == "informational":
            return f'"{keyword}" (what is OR how to OR guide OR tutorial OR definition)'
        elif intent == "navigational":
            return f'"{keyword}" site:*.com'
        else:
            return keyword


# ============================================================================
# SEARCH RESULT PARSER
# ============================================================================

class SearchResultParser:
    """Parse search results from different engines"""
    
    def __init__(self):
        self.query_builder = SearchQueryBuilder()
    
    def parse(self, html: str, engine: str = "google", url: str = None) -> List[Dict[str, Any]]:
        """Parse search results based on engine"""
        if engine == "google":
            return self.parse_google(html, url)
        elif engine == "bing":
            return self.parse_bing(html, url)
        elif engine == "yahoo":
            return self.parse_yahoo(html, url)
        elif engine == "duckduckgo":
            return self.parse_duckduckgo(html, url)
        else:
            return []
    
    def parse_google(self, html: str, url: str = None) -> List[Dict[str, Any]]:
        """Parse Google search results"""
        results = []
        
        if not html:
            return results
        
        # Check for CAPTCHA
        captcha_detection = detect_captcha(html=html, url=url)
        if captcha_detection["detected"]:
            log_discovery("search", url, "captcha_detected", 
                         details=f"CAPTCHA type: {captcha_detection['type']}")
            return results
        
        # Try to find result containers
        # Google changes their structure frequently, so we use multiple patterns
        result_patterns = [
            # Modern Google (2023+)
            (r'<div[^>]*class="[^"]*g[^"]*"[^>]*>(.*?)</div>', 'g-container'),
            (r'<div[^>]*class="[^"]*rc[^"]*"[^>]*>(.*?)</div>', 'rc-container'),
            
            # Organic results
            (r'<div[^>]*class="[^"]*tF2Cxc[^"]*"[^>]*>(.*?)</div>', 'organic-link'),
            (r'<div[^>]*class="[^"]*yuRUbf[^"]*"[^>]*>(.*?)</div>', 'organic-result'),
            
            # Titles
            (r'<h3[^>]*class="[^"]*LC20lb[^"]*"[^>]*>([^<]+)</h3>', 'title'),
            (r'<h3[^>]*>([^<]+)</h3>', 'title-fallback'),
            
            # URLs
            (r'<a[^>]*href="/url\?q=([^&]+)', 'url-encoded'),
            (r'href="(/url\?[^"]+)"', 'url-full'),
        ]
        
        # First, extract all URLs
        all_urls = set()
        url_pattern = r'href="(/url\?[^"]+)"'
        for match in re.finditer(url_pattern, html):
            url_path = match.group(1)
            # Decode URL
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url_path).query)
            if 'q' in parsed:
                actual_url = parsed['q'][0]
                all_urls.add(urllib.parse.unquote(actual_url))
        
        # Extract titles and snippets
        titles = []
        title_pattern = r'<h3[^>]*>([^<]{1,200})</h3>'
        for match in re.finditer(title_pattern, html):
            titles.append(clean_text(match.group(1)))
        
        snippets = []
        snippet_pattern = r'<div[^>]*class="[^"]*VwiC3b[^"]*"[^>]*>([^<]{1,500})</div>'
        for match in re.finditer(snippet_pattern, html):
            snippets.append(clean_text(match.group(1)))
        
        # Also try alternative snippet patterns
        if not snippets:
            snippet_pattern = r'<p[^>]*>([^<]{1,500})</p>'
            for match in re.finditer(snippet_pattern, html):
                text = clean_text(match.group(1))
                if len(text) > 10 and text not in snippets:
                    snippets.append(text)
        
        # Build results
        for i, url in enumerate(all_urls):
            result = {
                "position": i + 1,
                "url": url,
                "domain": domain_of(url),
                "title": titles[i] if i < len(titles) else "",
                "snippet": snippets[i] if i < len(snippets) else "",
                "engine": "google",
                "type": "organic"
            }
            
            # Extract additional data
            result = self._extract_additional_data(result, html, url)
            results.append(result)
        
        return results
    
    def parse_bing(self, html: str, url: str = None) -> List[Dict[str, Any]]:
        """Parse Bing search results"""
        results = []
        
        if not html:
            return results
        
        # Check for CAPTCHA
        if "captcha" in html.lower():
            log_discovery("search", url, "captcha_detected", details="Bing CAPTCHA")
            return results
        
        # Extract URLs
        all_urls = set()
        url_pattern = r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>'
        
        for match in re.finditer(url_pattern, html, re.DOTALL):
            result_html = match.group(1)
            
            # Extract URL
            link_match = re.search(r'href="([^"]+)"', result_html)
            if link_match:
                url_path = link_match.group(1)
                all_urls.add(urllib.parse.unquote(url_path))
        
        # Extract titles
        titles = []
        title_pattern = r'<h2[^>]*>([^<]+)</h2>'
        for match in re.finditer(title_pattern, html):
            titles.append(clean_text(match.group(1)))
        
        # Extract snippets
        snippets = []
        snippet_pattern = r'<p[^>]*>([^<]+)</p>'
        for match in re.finditer(snippet_pattern, html):
            snippets.append(clean_text(match.group(1)))
        
        # Build results
        for i, url in enumerate(all_urls):
            result = {
                "position": i + 1,
                "url": url,
                "domain": domain_of(url),
                "title": titles[i] if i < len(titles) else "",
                "snippet": snippets[i] if i < len(snippets) else "",
                "engine": "bing",
                "type": "organic"
            }
            
            result = self._extract_additional_data(result, html, url)
            results.append(result)
        
        return results
    
    def parse_yahoo(self, html: str, url: str = None) -> List[Dict[str, Any]]:
        """Parse Yahoo search results"""
        results = []
        
        if not html:
            return results
        
        # Extract URLs
        all_urls = set()
        url_pattern = r'<a[^>]*class="[^"]*ac-algo[^"]*"[^>]*href="([^"]+)"'
        
        for match in re.finditer(url_pattern, html):
            url_path = match.group(1)
            all_urls.add(urllib.parse.unquote(url_path))
        
        # Extract titles
        titles = []
        title_pattern = r'<h3[^>]*>([^<]+)</h3>'
        for match in re.finditer(title_pattern, html):
            titles.append(clean_text(match.group(1)))
        
        # Extract snippets
        snippets = []
        snippet_pattern = r'<p[^>]*>([^<]+)</p>'
        for match in re.finditer(snippet_pattern, html):
            snippets.append(clean_text(match.group(1)))
        
        # Build results
        for i, url in enumerate(all_urls):
            result = {
                "position": i + 1,
                "url": url,
                "domain": domain_of(url),
                "title": titles[i] if i < len(titles) else "",
                "snippet": snippets[i] if i < len(snippets) else "",
                "engine": "yahoo",
                "type": "organic"
            }
            
            result = self._extract_additional_data(result, html, url)
            results.append(result)
        
        return results
    
    def parse_duckduckgo(self, html: str, url: str = None) -> List[Dict[str, Any]]:
        """Parse DuckDuckGo search results"""
        results = []
        
        if not html:
            return results
        
        # Check for CAPTCHA
        if "captcha" in html.lower() or "robot" in html.lower():
            log_discovery("search", url, "captcha_detected", details="DuckDuckGo CAPTCHA")
            return results
        
        # Extract result containers
        container_pattern = r'<div[^>]*class="[^"]*result[^"]*"[^>]*>(.*?)</div>'
        
        for i, match in enumerate(re.finditer(container_pattern, html, re.DOTALL)):
            result_html = match.group(1)
            
            # Extract URL
            url_match = re.search(r'href="([^"]+)"', result_html)
            if not url_match:
                continue
            
            url_path = url_match.group(1)
            
            # Extract title
            title_match = re.search(r'<h2[^>]*>([^<]+)</h2>', result_html)
            title = clean_text(title_match.group(1)) if title_match else ""
            
            # Extract snippet
            snippet_match = re.search(r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>([^<]+)</a>', result_html)
            snippet = clean_text(snippet_match.group(1)) if snippet_match else ""
            
            if not snippet:
                snippet_match = re.search(r'<p[^>]*>([^<]+)</p>', result_html)
                snippet = clean_text(snippet_match.group(1)) if snippet_match else ""
            
            result = {
                "position": i + 1,
                "url": url_path,
                "domain": domain_of(url_path),
                "title": title,
                "snippet": snippet,
                "engine": "duckduckgo",
                "type": "organic"
            }
            
            result = self._extract_additional_data(result, html, url_path)
            results.append(result)
        
        return results
    
    def _extract_additional_data(self, result: Dict[str, Any], html: str, url: str) -> Dict[str, Any]:
        """Extract additional data like featured snippets, ads, etc."""
        # Check if this is an ad
        if "ad" in html.lower() or "sponsored" in html.lower():
            result["type"] = "ad"
        
        # Check for featured snippets
        featured_patterns = [
            r'featured snippet',
            r'featured result',
            r'answer box',
            r'position zero'
        ]
        
        for pattern in featured_patterns:
            if re.search(pattern, html, re.IGNORECASE):
                result["is_featured"] = True
                break
        
        # Extract ranking signals
        result["ranking_signals"] = self._extract_ranking_signals(html, result)
        
        return result
    
    def _extract_ranking_signals(self, html: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """Extract potential ranking signals from the page"""
        signals = {}
        
        # Check for HTTPS
        signals["https"] = result["url"].startswith("https://")
        
        # Check for common SEO elements
        signals["has_meta_description"] = "meta name=\"description\"" in html
        signals["has_schema"] = "schema.org" in html or "itemprop" in html
        signals["has_open_graph"] = "og:" in html
        signals["has_json_ld"] = "<script type=\"application/ld+json\">" in html
        
        # Check for structured data
        signals["has_breadcrumbs"] = "BreadcrumbList" in html
        signals["has_faq"] = "FAQPage" in html or "faq" in html.lower()
        signals["has_reviews"] = "AggregateRating" in html or "Review" in html
        
        # Check for content quality indicators
        signals["estimated_word_count"] = len(html.split()) if html else 0
        signals["has_internal_links"] = html.count('href="/') if html else 0
        
        return signals


# ============================================================================
# SEARCH ENGINE CLIENT
# ============================================================================

class SearchEngineClient:
    """Client for interacting with search engines"""
    
    ENGINES = {
        "google": {
            "base_url": "https://www.google.com",
            "search_path": "/search",
            "params": {"hl": "en", "num": 10}
        },
        "bing": {
            "base_url": "https://www.bing.com",
            "search_path": "/search",
            "params": {}
        },
        "yahoo": {
            "base_url": "https://search.yahoo.com",
            "search_path": "/search",
            "params": {}
        },
        "duckduckgo": {
            "base_url": "https://duckduckgo.com",
            "search_path": "/",
            "params": {}
        }
    }
    
    def __init__(self, config: SearchConfig = None):
        self.config = config or SearchConfig()
        self.parser = SearchResultParser()
        self.query_builder = SearchQueryBuilder()
        self.user_agent = self.config.user_agent or ua_manager.get_random_agent()
    
    def search(self, query: str, engine: str = "google", num_results: int = 10,
               start: int = 0, **kwargs) -> List[Dict[str, Any]]:
        """Perform a search and return results"""
        # Build the search URL
        search_url = self._build_search_url(query, engine, num_results, start, **kwargs)
        
        # Get page content
        html, headers, status_code = self._fetch_page(search_url)
        
        if not html:
            return []
        
        # Check for CAPTCHA
        captcha_detection = detect_captcha(html=html, headers=headers, url=search_url, 
                                          status_code=status_code)
        
        if captcha_detection["detected"]:
            # Try to solve CAPTCHA
            success, result = solver_manager.solve(html, search_url, headers, status_code)
            if success:
                # Wait and retry
                time.sleep(5)
                html, headers, status_code = self._fetch_page(search_url)
                if not html:
                    return []
        
        # Parse results
        results = self.parser.parse(html, engine, search_url)
        
        # Apply filters
        results = self._apply_filters(results)
        
        # Apply rate limiting
        rate_limiter.wait()
        
        return results
    
    def _build_search_url(self, query: str, engine: str, num_results: int, start: int,
                         **kwargs) -> str:
        """Build the search URL for a specific engine"""
        if engine not in self.ENGINES:
            raise ValueError(f"Unsupported engine: {engine}")
        
        engine_config = self.ENGINES[engine]
        
        # Prepare parameters
        params = {
            "q": query,
            "start": start,
            "num": num_results
        }
        
        # Add engine-specific params
        params.update(engine_config.get("params", {}))
        
        # Add additional params from kwargs
        params.update(kwargs)
        
        # Remove None values
        params = {k: v for k, v in params.items() if v is not None}
        
        # Build URL
        query_string = urllib.parse.urlencode(params)
        return f"{engine_config['base_url']}{engine_config['search_path']}?{query_string}"
    
    def _fetch_page(self, url: str) -> Tuple[str, Dict[str, str], int]:
        """Fetch a page with rate limiting and retries"""
        import urllib.request
        
        headers = header_manager.get_headers(self.user_agent)
        
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                content = response.read().decode('utf-8', 'replace')
                status_code = response.getcode()
                response_headers = dict(response.headers)
                return content, response_headers, status_code
        except urllib.error.HTTPError as e:
            return None, dict(e.headers), e.code
        except Exception as e:
            log_discovery("search", url, "error", details=str(e))
            return None, {}, 0
    
    def _apply_filters(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply configured filters to results"""
        filtered = []
        
        for result in results:
            url = result.get("url", "")
            domain = result.get("domain", "")
            
            # Check domain exclusions
            if self.config.exclude_domains and domain in self.config.exclude_domains:
                continue
            
            # Check domain patterns
            excluded = False
            for pattern in self.config.exclude_patterns:
                if re.search(pattern, url, re.IGNORECASE):
                    excluded = True
                    break
            
            if excluded:
                continue
            
            # Check include patterns
            if self.config.include_patterns:
                included = False
                for pattern in self.config.include_patterns:
                    if re.search(pattern, url, re.IGNORECASE) or \
                       re.search(pattern, domain, re.IGNORECASE):
                        included = True
                        break
                
                if not included:
                    continue
            
            filtered.append(result)
        
        return filtered
    
    def search_multiple_engines(self, query: str, engines: List[str] = None,
                                num_results: int = 10) -> Dict[str, List[Dict[str, Any]]]:
        """Search across multiple engines"""
        if engines is None:
            engines = ["google", "bing", "duckduckgo"]
        
        results = {}
        
        for engine in engines:
            try:
                engine_results = self.search(query, engine=engine, num_results=num_results)
                results[engine] = engine_results
            except Exception as e:
                log_discovery("search", query, "error", 
                             details=f"Engine {engine} failed: {e}")
                results[engine] = []
        
        return results
    
    def paginated_search(self, query: str, engine: str = "google", 
                        total_results: int = 100, **kwargs) -> List[Dict[str, Any]]:
        """Perform a paginated search to get more results"""
        all_results = []
        num_results = min(self.config.results_per_page, total_results)
        max_pages = min(self.config.max_pages, (total_results + num_results - 1) // num_results)
        
        for page in range(max_pages):
            start = page * num_results
            results = self.search(query, engine=engine, num_results=num_results, 
                                 start=start, **kwargs)
            
            if not results:
                break
            
            all_results.extend(results)
            
            # Respect rate limiting
            time.sleep(self.config.delay_between_requests)
        
        return all_results[:total_results]


# ============================================================================
# SERPAPI CLIENT (Paid API)
# ============================================================================

class SerpAPICLIENT:
    """Client for SerpAPI - a paid search API"""
    
    API_URL = "https://serpapi.com/search"
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or SERPAPI_KEY or os.environ.get("SERPAPI_API_KEY")
    
    def search(self, query: str, engine: str = "google", num_results: int = 10,
               **kwargs) -> Dict[str, Any]:
        """Perform a search using SerpAPI"""
        if not self.api_key:
            raise ValueError("SerpAPI key not configured")
        
        # Prepare parameters
        params = {
            "q": query,
            "engine": engine,
            "num": num_results,
            "api_key": self.api_key
        }
        
        # Add additional params
        params.update(kwargs)
        
        # Remove None values
        params = {k: v for k, v in params.items() if v is not None}
        
        # Make request
        try:
            response = http_post(self.API_URL, data=params)
            return json.loads(response)
        except Exception as e:
            log_discovery("serpapi", query, "error", details=str(e))
            return {"error": str(e)}
    
    def get_results(self, query: str, engine: str = "google", **kwargs) -> List[Dict[str, Any]]:
        """Get search results from SerpAPI"""
        result = self.search(query, engine, **kwargs)
        
        if "error" in result:
            return []
        
        # Extract results based on engine
        if engine == "google":
            return self._extract_google_results(result)
        elif engine == "bing":
            return self._extract_bing_results(result)
        elif engine == "yahoo":
            return self._extract_yahoo_results(result)
        else:
            return []
    
    def _extract_google_results(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract Google results from SerpAPI response"""
        results = []
        
        if "organic_results" in data:
            for i, result in enumerate(data["organic_results"]):
                results.append({
                    "position": i + 1,
                    "url": result.get("link"),
                    "domain": domain_of(result.get("link", "")),
                    "title": result.get("title"),
                    "snippet": result.get("snippet"),
                    "engine": "google",
                    "type": "organic",
                    "metadata": {
                        "displayed_link": result.get("displayed_link"),
                        "date": result.get("date"),
                        "cached_page_link": result.get("cached_page_link")
                    }
                })
        
        if "ads" in data:
            for i, result in enumerate(data["ads"]):
                results.append({
                    "position": i + 1,
                    "url": result.get("link"),
                    "domain": domain_of(result.get("link", "")),
                    "title": result.get("title"),
                    "snippet": result.get("description"),
                    "engine": "google",
                    "type": "ad",
                    "metadata": {
                        "displayed_link": result.get("displayed_link"),
                        "tracking_link": result.get("tracking_link")
                    }
                })
        
        return results
    
    def _extract_bing_results(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract Bing results from SerpAPI response"""
        results = []
        
        if "organic_results" in data:
            for i, result in enumerate(data["organic_results"]):
                results.append({
                    "position": i + 1,
                    "url": result.get("link"),
                    "domain": domain_of(result.get("link", "")),
                    "title": result.get("title"),
                    "snippet": result.get("snippet"),
                    "engine": "bing",
                    "type": "organic"
                })
        
        return results
    
    def _extract_yahoo_results(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract Yahoo results from SerpAPI response"""
        results = []
        
        if "organic_results" in data:
            for i, result in enumerate(data["organic_results"]):
                results.append({
                    "position": i + 1,
                    "url": result.get("link"),
                    "domain": domain_of(result.get("link", "")),
                    "title": result.get("title"),
                    "snippet": result.get("snippet"),
                    "engine": "yahoo",
                    "type": "organic"
                })
        
        return results


# ============================================================================
# ADVANCED SEARCH STRATEGIES
# ============================================================================

class AdvancedSearch:
    """Advanced search strategies for lead generation"""
    
    def __init__(self):
        self.client = SearchEngineClient()
        self.serpapi = SerpAPICLIENT() if SERPAPI_KEY or os.environ.get("SERPAPI_API_KEY") else None
        self.query_builder = SearchQueryBuilder()
    
    def discover_leads(self, keywords: List[str] = None, industries: List[str] = None,
                      locations: List[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Discover leads using advanced search strategies"""
        if not keywords:
            from .config import SERVICE_KEYWORDS
            keywords = [k for kw_list in list(SERVICE_KEYWORDS.values())[:5] for k in kw_list[:3]]
        
        all_results = []
        seen_domains = set()
        
        for keyword in keywords[:10]:
            for industry in industries[:3] or ["technology", "finance", "healthcare"]:
                for location in locations[:3] or ["USA", "UK", "Canada"]:
                    # Build B2B query
                    query = self.query_builder.build_b2b_query(
                        service=keyword,
                        industry=industry,
                        location=location
                    )
                    
                    # Search
                    try:
                        results = self.client.paginated_search(
                            query, 
                            engine="google",
                            total_results=10
                        )
                        
                        for result in results:
                            domain = result.get("domain", "")
                            if domain in seen_domains or not domain:
                                continue
                            
                            seen_domains.add(domain)
                            
                            # Extract lead information
                            lead = self._result_to_lead(result, keyword, industry, location)
                            all_results.append(lead)
                            
                            if len(all_results) >= limit:
                                break
                        
                        if len(all_results) >= limit:
                            break
                    except Exception as e:
                        log_discovery("advanced_search", query, "error", details=str(e))
                        continue
                    
                    if len(all_results) >= limit:
                        break
                
                if len(all_results) >= limit:
                    break
            
            if len(all_results) >= limit:
                break
        
        return all_results
    
    def _result_to_lead(self, result: Dict[str, Any], keyword: str, industry: str, 
                       location: str) -> Dict[str, Any]:
        """Convert a search result to a lead"""
        return {
            "source": "search",
            "source_url": result.get("url"),
            "company": result.get("title"),
            "domain": result.get("domain"),
            "website": result.get("url"),
            "snippet": result.get("snippet"),
            "engine": result.get("engine"),
            "position": result.get("position"),
            "intent_signals": [keyword, industry, location],
            "last_researched": time.time(),
            "type": "discovered"
        }
    
    def find_competitors(self, keyword: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Find competitors for a keyword"""
        query = self.query_builder.build_competitor_query(keyword, keyword)
        results = self.client.paginated_search(query, engine="google", total_results=limit)
        
        return [self._result_to_competitor(r, keyword) for r in results]
    
    def _result_to_competitor(self, result: Dict[str, Any], keyword: str) -> Dict[str, Any]:
        """Convert a search result to a competitor"""
        return {
            "name": result.get("title"),
            "url": result.get("url"),
            "domain": result.get("domain"),
            "snippet": result.get("snippet"),
            "keyword": keyword,
            "position": result.get("position"),
            "engine": result.get("engine")
        }
    
    def extract_serp_features(self, keyword: str, engine: str = "google") -> Dict[str, Any]:
        """Extract SERP features for a keyword"""
        results = self.client.search(keyword, engine=engine, num_results=10)
        
        features = {
            "keyword": keyword,
            "engine": engine,
            "organic_results": [],
            "ads": [],
            "featured_snippet": None,
            "knowledge_panel": None,
            "related_questions": [],
            "people_also_ask": [],
            "people_also_search": []
        }
        
        for result in results:
            if result.get("type") == "organic":
                features["organic_results"].append(result)
            elif result.get("type") == "ad":
                features["ads"].append(result)
        
        return features
    
    def search_with_intent(self, keyword: str, intent: str = "commercial", 
                          limit: int = 50) -> List[Dict[str, Any]]:
        """Search with specific user intent"""
        query = self.query_builder.build_intent_query(keyword, intent)
        results = self.client.paginated_search(query, engine="google", total_results=limit)
        
        return [self._result_to_lead(r, keyword, None, None) for r in results]
    
    def find_question_keywords(self, keyword: str, limit: int = 50) -> List[str]:
        """Find question-based keywords related to a topic"""
        query = f'"{keyword}" (what is OR how to OR how do OR how can OR best OR top OR review)'
        results = self.client.search(query, engine="google", num_results=limit)
        
        questions = []
        for result in results:
            snippet = result.get("snippet", "")
            # Extract questions from snippets
            question_patterns = [
                r'What is [^\?]+\??',
                r'How to [^\?]+\??',
                r'How do [^\?]+\??',
                r'How can [^\?]+\??',
                r'What are [^\?]+\??',
                r'Which [^\?]+\??',
                r'Why [^\?]+\??',
                r'When [^\?]+\??',
                r'Where [^\?]+\??',
                r'Who [^\?]+\??'
            ]
            
            for pattern in question_patterns:
                matches = re.findall(pattern, snippet, re.IGNORECASE)
                for match in matches:
                    question = match.strip()
                    if question and question not in questions:
                        questions.append(question)
        
        return questions[:limit]
    
    def analyze_search_volume(self, keywords: List[str]) -> Dict[str, int]:
        """Analyze search volume for keywords (placeholder - would need API)"""
        # This would require a keyword research API like Ahrefs, SEMrush, etc.
        # For now, return placeholder data
        return {keyword: random.randint(100, 10000) for keyword in keywords}


# ============================================================================
# EXPORT
# ============================================================================

# Global instances
search_client = SearchEngineClient()
search_parser = SearchResultParser()
query_builder = SearchQueryBuilder()
advanced_search = AdvancedSearch()

# Try to initialize SerpAPI if key is available
serpapi_client = None
if SERPAPI_KEY or os.environ.get("SERPAPI_API_KEY"):
    serpapi_client = SerpAPICLIENT()


def search(query: str, engine: str = "google", num_results: int = 10, **kwargs) -> List[Dict[str, Any]]:
    """Perform a search using the default client"""
    return search_client.search(query, engine, num_results, **kwargs)


def paginated_search(query: str, engine: str = "google", total_results: int = 100, **kwargs) -> List[Dict[str, Any]]:
    """Perform a paginated search"""
    return search_client.paginated_search(query, engine, total_results, **kwargs)


def multi_engine_search(query: str, engines: List[str] = None, num_results: int = 10) -> Dict[str, List[Dict[str, Any]]]:
    """Search across multiple engines"""
    return search_client.search_multiple_engines(query, engines, num_results)


def build_query(query: str, **kwargs) -> str:
    """Build a search query with operators"""
    return query_builder.build(query, **kwargs)


def discover_leads(keywords: List[str] = None, industries: List[str] = None,
                  locations: List[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Discover leads using advanced search"""
    return advanced_search.discover_leads(keywords, industries, locations, limit)


if __name__ == "__main__":
    # Test the search functionality
    print("Testing Search Engine Scraping...")
    
    # Test query building
    query = build_query("web development", exact_phrase=["custom web app"], 
                       site=["softorasystems.com"])
    print(f"Built query: {query}")
    
    # Test B2B query building
    b2b_query = query_builder.build_b2b_query(
        service="SaaS development",
        industry="technology",
        location="USA"
    )
    print(f"B2B query: {b2b_query}")
    
    # Test search
    print("\nPerforming test search...")
    try:
        results = search("web development company", num_results=5)
        print(f"Found {len(results)} results")
        for i, result in enumerate(results[:3]):
            print(f"  {i+1}. {result.get('title', 'No title')} - {result.get('url', 'No URL')}")
    except Exception as e:
        print(f"Search error: {e}")
