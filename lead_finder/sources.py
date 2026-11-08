"""
Lead Discovery Sources
"""
import re
import time
import urllib.parse
from datetime import datetime, timezone

from .config import (
    UA, RATE_LIMIT_DELAY, SERVICE_KEYWORDS, INDUSTRY_KEYWORDS,
    EXCLUDED_DOMAINS, EXCLUDED_PATTERNS, GENERIC_EMAILS, domain_of
)
from .http_utils import http_get, can_fetch, extract_emails, extract_phone_numbers, clean_text
from .database import log_discovery
from .stealth import (
    ua_manager, proxy_manager, rate_limiter, header_manager, 
    behavior_simulator, captcha_detector, human_like_delay
)
from .captcha import solver_manager, detect_captcha
from .javascript import js_renderer, render_page


class LeadSource:
    """Base class for lead discovery sources"""
    
    def __init__(self):
        self.source_type = "base"
        self.robots_cache = {}
        self.user_agent = None
        self.use_stealth = True
    
    def _get_headers(self, url: str = None) -> dict:
        """Get headers with stealth"""
        if self.user_agent:
            return header_manager.get_headers_for_url(url, self.user_agent)
        return header_manager.get_headers_for_url(url)
    
    def _get_user_agent(self) -> str:
        """Get a user agent for this request"""
        if self.user_agent:
            return self.user_agent
        return ua_manager.get_random_agent()
    
    def _apply_rate_limiting(self):
        """Apply rate limiting before request"""
        if self.use_stealth:
            rate_limiter.wait()
    
    def _apply_human_delay(self):
        """Apply human-like delay"""
        if self.use_stealth:
            time.sleep(human_like_delay(0.5))
    
    def _check_captcha(self, html: str, url: str, headers: dict = None, status_code: int = None):
        """Check for CAPTCHA and attempt to solve"""
        if self.use_stealth:
            detection = detect_captcha(html, headers, url, status_code)
            if detection["detected"]:
                success, result = solver_manager.solve(html, url, headers, status_code)
                if success:
                    return True
                log_discovery(self.source_type, url, "captcha", details=f"CAPTCHA: {detection['type']}")
        return False
    
    def _get_proxy(self):
        """Get proxy URL if configured"""
        if self.use_stealth and proxy_manager.enabled:
            return proxy_manager.get_proxy_url()
        return None
    
    def discover(self, keywords=None, limit=100, filters=None):
        """Discover leads from this source"""
        raise NotImplementedError
    
    def get_name(self):
        return self.source_type


class GoogleSearchSource(LeadSource):
    """Discover leads from Google search results"""
    
    def __init__(self):
        super().__init__()
        self.source_type = "google_search"
        self.base_url = "https://www.google.com"
        self.user_agent = ua_manager.get_agent_for_browser("chrome")
        
    def _make_search_url(self, query, start=0):
        """Create a Google search URL"""
        params = {
            "q": query,
            "start": start,
            "num": 10,
            "hl": "en"
        }
        return f"https://www.google.com/search?{urllib.parse.urlencode(params)}"
    
    def _parse_search_results(self, html, base_url):
        """Parse Google search results page"""
        results = []
        
        if not html:
            return results
        
        # Find all result containers - Google's structure changes frequently
        # Try multiple patterns
        patterns = [
            r'<div[^>]*class="[^"]*g[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*rc[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*srg[^"]*"[^>]*>(.*?)</div>'
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, html, re.DOTALL):
                result_html = match.group(1)
                
                # Extract URL
                url_match = re.search(r'href="(/url\?[^"]+)"', result_html)
                if url_match:
                    url_path = url_match.group(1)
                    url = urllib.parse.unquote(url_path.split("=")[1] if "=" in url_path else url_path)
                    if url and urllib.parse.urlparse(url).scheme:
                        
                        # Extract title
                        title_match = re.search(r'<h3[^>]*>([^<]+)</h3>', result_html)
                        title = clean_text(title_match.group(1)) if title_match else None
                        
                        # Extract snippet
                        snippet_match = re.search(r'<div[^>]*class="[^"]*VwiC3b[^"]*"[^>]*>([^<]+)</div>', result_html)
                        if not snippet_match:
                            snippet_match = re.search(r'<p[^>]*>([^<]+)</p>', result_html)
                        snippet = clean_text(snippet_match.group(1)) if snippet_match else None
                        
                        results.append({
                            "url": url,
                            "title": title,
                            "snippet": snippet,
                            "source": "google"
                        })
                
                if len(results) >= 20:  # Limit per page
                    break
            
            if len(results) >= 20:
                break
        
        return results
    
    def discover(self, keywords=None, limit=100, filters=None):
        """Discover leads from Google search"""
        if not keywords:
            # Use a selection of service keywords
            keywords = []
            for service_kws in list(SERVICE_KEYWORDS.values())[:3]:
                keywords.extend(service_kws[:2])
            keywords = keywords[:5]
        
        if isinstance(keywords, str):
            keywords = [keywords]
        
        all_results = []
        seen_urls = set()
        
        for keyword in keywords[:5]:
            for start in range(0, limit, 10):
                if len(all_results) >= limit:
                    break
                
                search_url = self._make_search_url(keyword, start)
                
                try:
                    # Apply rate limiting
                    self._apply_rate_limiting()
                    
                    # Get with stealth
                    headers = self._get_headers(search_url)
                    
                    # Try with JavaScript rendering for better results
                    html, metadata = render_page(search_url, timeout=30)
                    
                    # Check for CAPTCHA
                    self._check_captcha(html, search_url, headers)
                    
                    if not html:
                        # Fallback to regular HTTP GET
                        html = http_get(search_url, timeout=30, headers=headers)
                    
                    results = self._parse_search_results(html, search_url)
                    for result in results:
                        if not result.get("url"):
                            continue
                        
                        if result["url"] in seen_urls:
                            continue
                        
                        domain = domain_of(result["url"])
                        
                        # Skip excluded domains
                        if any(domain.endswith(p) for p in EXCLUDED_PATTERNS):
                            continue
                        if domain in EXCLUDED_DOMAINS:
                            continue
                        
                        seen_urls.add(result["url"])
                        all_results.append(result)
                        
                        log_discovery(
                            self.source_type, 
                            search_url, 
                            "found",
                            domain=domain,
                            company=result.get("title"),
                            details=f"Keyword: {keyword}"
                        )
                        
                        if len(all_results) >= limit:
                            break
                    
                    # Apply human-like delay
                    self._apply_human_delay()
                    
                except Exception as e:
                    log_discovery(self.source_type, search_url, "error", details=str(e))
                    continue
        
        return all_results


class JobBoardSource(LeadSource):
    """Discover leads from job postings - companies hiring for specific skills"""
    
    JOB_BOARDS = [
        {
            "name": "Indeed",
            "url": "https://www.indeed.com",
            "search_path": "/jobs?q=",
            "requires_auth": False
        },
        {
            "name": "AngelList",
            "url": "https://angel.co",
            "search_path": "/jobs?search=",
            "requires_auth": False
        },
        {
            "name": "We Work Remotely",
            "url": "https://weworkremotely.com",
            "search_path": "/remote-jobs?search=",
            "requires_auth": False
        }
    ]
    
    JOB_KEYWORDS = {
        "web_app": ["web developer", "frontend developer", "full stack developer", "SaaS developer"],
        "saas": ["SaaS developer", "software engineer", "product engineer"],
        "mobile": ["mobile developer", "iOS developer", "Android developer", "React Native"],
        "ai_agents": ["AI engineer", "machine learning", "LLM", "prompt engineer"],
        "automation": ["automation engineer", "DevOps", "workflow automation"],
        "devops": ["DevOps engineer", "cloud engineer", "infrastructure"]
    }
    
    def __init__(self):
        super().__init__()
        self.source_type = "job_board"
    
    def discover(self, keywords=None, limit=100, filters=None):
        """Discover leads from job postings"""
        if not keywords:
            keywords = [k for kw_list in self.JOB_KEYWORDS.values() for k in kw_list]
        
        all_results = []
        seen_companies = set()
        
        for board in self.JOB_BOARDS:
            for keyword in keywords[:5]:
                search_url = board["url"] + board["search_path"] + keyword.replace(" ", "+")
                
                try:
                    html = http_get(search_url, timeout=30)
                    if not html:
                        continue
                    
                    # Parse job listings
                    for match in re.finditer(r'(?:company|employer)[^>]*>([^<]{3,100})<', html, re.IGNORECASE):
                        company = clean_text(match.group(1))
                        
                        if not company or company in seen_companies:
                            continue
                        
                        seen_companies.add(company)
                        
                        # Try to find company website
                        website = None
                        website_match = re.search(r'www\.([^\s<>"]+\.[a-z]{2,})', html[match.start():match.start() + 500])
                        if website_match:
                            website = "https://" + website_match.group(1)
                        
                        all_results.append({
                            "url": search_url,
                            "title": company,
                            "source": board["name"],
                            "job_keyword": keyword,
                            "company": company,
                            "website": website,
                            "intent_signal": "hiring"
                        })
                        
                        log_discovery(
                            self.source_type,
                            search_url,
                            "found",
                            domain=domain_of(website) if website else None,
                            company=company,
                            details=f"Job board: {board['name']}, Keyword: {keyword}"
                        )
                        
                        if len(all_results) >= limit:
                            break
                    
                    time.sleep(RATE_LIMIT_DELAY * 2)
                    
                except Exception as e:
                    log_discovery(self.source_type, search_url, "error", details=str(e))
                    continue
                
                if len(all_results) >= limit:
                    break
            
            if len(all_results) >= limit:
                break
        
        return all_results


class DirectorySource(LeadSource):
    """Discover leads from business directories"""
    
    DIRECTORIES = [
        {
            "name": "Clutch",
            "url": "https://clutch.co",
            "search_path": "/providers?service=",
        },
        {
            "name": "GoodFirms",
            "url": "https://www.goodfirms.co",
            "search_path": "/search?search=&service=",
        }
    ]
    
    def __init__(self):
        super().__init__()
        self.source_type = "directory"
    
    def discover(self, keywords=None, limit=100, filters=None):
        """Discover leads from business directories"""
        if not keywords:
            keywords = list(SERVICE_KEYWORDS.keys())
        
        all_results = []
        seen_domains = set()
        
        for directory in self.DIRECTORIES:
            for keyword in keywords[:3]:
                search_url = directory["url"] + directory["search_path"] + keyword
                
                try:
                    html = http_get(search_url, timeout=30)
                    if not html:
                        continue
                    
                    # Try to extract company names and links
                    for match in re.finditer(r'href="(/[^"]+)"[^>]*class="[^"]*(company|profile)[^"]*"', html, re.IGNORECASE):
                        link = match.group(1)
                        full_url = directory["url"] + link
                        
                        # Extract company name from nearby text
                        name_match = re.search(r'>([^<]{3,80})<', html[match.end():match.end() + 100])
                        company = clean_text(name_match.group(1)) if name_match else None
                        
                        domain = domain_of(full_url)
                        if domain in seen_domains or domain in EXCLUDED_DOMAINS:
                            continue
                        
                        seen_domains.add(domain)
                        all_results.append({
                            "url": full_url,
                            "title": company or keyword,
                            "source": directory["name"],
                            "directory": directory["name"]
                        })
                        
                        log_discovery(
                            self.source_type,
                            search_url,
                            "found",
                            domain=domain,
                            company=company,
                            details=f"Directory: {directory['name']}"
                        )
                        
                        if len(all_results) >= limit:
                            break
                    
                    time.sleep(RATE_LIMIT_DELAY * 2)
                    
                except Exception as e:
                    log_discovery(self.source_type, search_url, "error", details=str(e))
                    continue
                
                if len(all_results) >= limit:
                    break
            
            if len(all_results) >= limit:
                break
        
        return all_results


class GitHubSource(LeadSource):
    """Discover leads from GitHub"""
    
    def __init__(self):
        super().__init__()
        self.source_type = "github"
    
    def discover(self, keywords=None, limit=100, filters=None):
        """Discover leads from GitHub"""
        if not keywords:
            keywords = ["SaaS", "AI", "automation", "CMS", "ERP", "CRM"]
        
        all_results = []
        seen_repos = set()
        
        for keyword in keywords[:5]:
            search_url = f"https://github.com/search?q={keyword}+in:readme&type=repositories"
            
            try:
                html = http_get(search_url, timeout=30)
                if not html:
                    continue
                
                # Extract repository links
                for match in re.finditer(r'href="(/[^/"]+/[^/"]+)"', html):
                    repo_path = match.group(1)
                    if repo_path in seen_repos:
                        continue
                    
                    seen_repos.add(repo_path)
                    full_url = "https://github.com" + repo_path
                    
                    # Extract owner from nearby text
                    owner_match = re.search(r'>([^<]{3,50})</a>', html[max(0, match.start() - 100):match.start()])
                    owner = clean_text(owner_match.group(1)) if owner_match else None
                    
                    # Check if this looks like a company
                    if owner and len(owner.split()) <= 2 and owner[0].isupper():
                        # Try to find website
                        website = None
                        website_match = re.search(r'www\.([^\s<>"]+\.[a-z]{2,})', html[match.start():match.start() + 500])
                        if website_match:
                            website = "https://" + website_match.group(1)
                        
                        domain = domain_of(website) if website else None
                        
                        if domain and domain not in EXCLUDED_DOMAINS:
                            all_results.append({
                                "url": full_url,
                                "title": owner,
                                "source": "github",
                                "repository": full_url,
                                "website": website,
                                "intent_signal": "using_tech"
                            })
                            
                            log_discovery(
                                self.source_type,
                                search_url,
                                "found",
                                domain=domain,
                                company=owner,
                                details=f"Keyword: {keyword}"
                            )
                        
                        if len(all_results) >= limit:
                            break
                    
                    if len(all_results) >= limit:
                        break
                
                time.sleep(RATE_LIMIT_DELAY * 2)
                
            except Exception as e:
                log_discovery(self.source_type, search_url, "error", details=str(e))
                continue
        
        return all_results


class NewsSource(LeadSource):
    """Discover leads from news"""
    
    NEWS_SOURCES = [
        {
            "name": "TechCrunch",
            "url": "https://techcrunch.com",
            "search_path": "/search/",
        }
    ]
    
    NEWS_KEYWORDS = [
        "raised", "funding", "series a", "acquired", "launched",
        "hiring", "expanding", "new product"
    ]
    
    def __init__(self):
        super().__init__()
        self.source_type = "news"
    
    def discover(self, keywords=None, limit=100, filters=None):
        """Discover leads from news"""
        if not keywords:
            keywords = self.NEWS_KEYWORDS + [k for kw_list in list(SERVICE_KEYWORDS.values())[:3] for k in kw_list[:2]]
        
        all_results = []
        seen_companies = set()
        
        for source in self.NEWS_SOURCES:
            for keyword in keywords[:5]:
                search_url = source["url"] + source["search_path"] + keyword.replace(" ", "+")
                
                try:
                    html = http_get(search_url, timeout=30)
                    if not html:
                        continue
                    
                    # Extract article links and company names
                    for link_match in re.finditer(r'href="(/[^"]+)"', html):
                        link = link_match.group(1)
                        full_url = source["url"] + link
                        
                        # Extract company name
                        company_match = re.search(r'<[^>]+class="[^"]*(byline|company)[^"]*"[^>]*>([^<]+)</[^>]+>', html[link_match.end():link_match.end() + 300], re.IGNORECASE)
                        company = clean_text(company_match.group(2)) if company_match else None
                        
                        if company and company not in seen_companies:
                            seen_companies.add(company)
                            
                            all_results.append({
                                "url": full_url,
                                "title": company,
                                "source": source["name"],
                                "news_keyword": keyword,
                                "intent_signal": "in_news"
                            })
                            
                            log_discovery(
                                self.source_type,
                                search_url,
                                "found",
                                company=company,
                                details=f"News source: {source['name']}, Keyword: {keyword}"
                            )
                            
                            if len(all_results) >= limit:
                                break
                        
                        if len(all_results) >= limit:
                            break
                    
                    time.sleep(RATE_LIMIT_DELAY * 2)
                    
                except Exception as e:
                    log_discovery(self.source_type, search_url, "error", details=str(e))
                    continue
            
            if len(all_results) >= limit:
                break
        
        return all_results


# Registry of all sources
ALL_SOURCES = [
    GoogleSearchSource(),
    JobBoardSource(),
    DirectorySource(),
    GitHubSource(),
    NewsSource()
]
