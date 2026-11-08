"""
HTTP and Scraping Utilities for Lead Finder
"""
import re
import socket
import time
import ipaddress
import urllib.error
import urllib.parse
import urllib.request
from urllib.robotparser import RobotFileParser

from .config import UA, RATE_LIMIT_DELAY, EXCLUDED_DOMAINS, EXCLUDED_PATTERNS


def safe_url(url):
    """Validate a URL is safe to fetch"""
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme: {p.scheme}")
    if not p.hostname:
        raise ValueError("No hostname")
    
    # Check for private/blocked IPs
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror:
        raise ValueError("Host does not resolve")
    
    for ai in infos:
        ip = ipaddress.ip_address(ai[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("Blocked IP address range")
    
    return url


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Custom redirect handler that validates URLs"""
    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        try:
            safe_url(newurl)
        except ValueError:
            return None
        return super().redirect_request(req, fp, code, msg, hdrs, newurl)


_OPENER = urllib.request.build_opener(SafeRedirectHandler)


def http_get(url, timeout=15, max_retries=3, headers=None):
    """Safe HTTP GET with retries and rate limiting"""
    # Import here to avoid circular imports
    from .database import check_daily_limit, get_domain_cooldown, set_domain_cooldown, increment_daily_counter, log_discovery
    from datetime import datetime, timezone
    
    domain = urllib.parse.urlparse(url).netloc


def http_post(url, data=None, json=None, timeout=15, max_retries=3, headers=None):
    """Safe HTTP POST with retries and rate limiting"""
    import json as json_module
    
    # Import here to avoid circular imports
    from .database import check_daily_limit, get_domain_cooldown, set_domain_cooldown, increment_daily_counter, log_discovery
    from datetime import datetime, timezone
    
    domain = urllib.parse.urlparse(url).netloc
    
    # Check daily limit first
    if not check_daily_limit():
        raise RuntimeError("Daily limit reached")
    
    # Check cooldown
    cooldown = get_domain_cooldown(domain)
    if cooldown:
        wait_time = (cooldown - datetime.now(timezone.utc)).total_seconds()
        if wait_time > 0:
            time.sleep(wait_time)
    
    headers = headers or {}
    headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
        "DNT": "1"
    })
    
    for attempt in range(max_retries):
        try:
            safe_url(url)
            
            # Rate limiting delay
            if attempt > 0:
                time.sleep(RATE_LIMIT_DELAY * (attempt + 1))
            
            # Prepare request body
            if json:
                body = json_module.dumps(json).encode('utf-8')
                headers.update({"Content-Type": "application/json"})
            elif data:
                if isinstance(data, dict):
                    body = urllib.parse.urlencode(data).encode('utf-8')
                else:
                    body = str(data).encode('utf-8')
                headers.update({"Content-Type": "application/x-www-form-urlencoded"})
            else:
                body = None
            
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with _OPENER.open(req, timeout=timeout) as r:
                # Check content type
                ct = r.headers.get("Content-Type", "").lower()
                if not any(ct.startswith(prefix) for prefix in ["text/html", "text/plain", "application/json", "application/javascript"]):
                    return None
                
                # Check size
                size = int(r.headers.get("Content-Length", 0))
                if size > 10_000_000:  # 10MB limit
                    return None
                
                from datetime import datetime, timezone
                content = r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
                increment_daily_counter()
                return content
                
        except urllib.error.HTTPError as e:
            if e.code == 429:  # Too Many Requests
                retry_after = int(e.headers.get("Retry-After", 60))
                time.sleep(retry_after)
                set_domain_cooldown(domain, retry_after * 2)
                continue
            elif e.code in (403, 404):
                log_discovery("http", url, "post", result="error", details=f"HTTP {e.code}")
                set_domain_cooldown(domain, 3600)
                return None
            elif e.code == 503:
                time.sleep(10)
                continue
            else:
                log_discovery("http", url, "post", result="error", details=f"HTTP {e.code}")
                return None
                
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            log_discovery("http", url, "post", result="error", details="Connection error")
            return None
        
        except ValueError as e:
            if "blocked" in str(e).lower() or "unsafe" in str(e).lower():
                set_domain_cooldown(domain, 86400)
            return None
    
    return None


def http_get(url, timeout=15, max_retries=3, headers=None):
    """Safe HTTP GET with retries and rate limiting"""
    # Import here to avoid circular imports
    from .database import check_daily_limit, get_domain_cooldown, set_domain_cooldown, increment_daily_counter, log_discovery
    from datetime import datetime, timezone
    
    domain = urllib.parse.urlparse(url).netloc
    
    # Check daily limit first
    if not check_daily_limit():
        raise RuntimeError("Daily limit reached")
    
    # Check cooldown
    cooldown = get_domain_cooldown(domain)
    if cooldown:
        wait_time = (cooldown - datetime.now(timezone.utc)).total_seconds()
        if wait_time > 0:
            time.sleep(wait_time)
    
    headers = headers or {}
    headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.7",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
        "DNT": "1"
    })
    
    for attempt in range(max_retries):
        try:
            safe_url(url)
            
            # Rate limiting delay
            if attempt > 0:
                time.sleep(RATE_LIMIT_DELAY * (attempt + 1))
            
            req = urllib.request.Request(url, headers=headers)
            with _OPENER.open(req, timeout=timeout) as r:
                # Check content type
                ct = r.headers.get("Content-Type", "").lower()
                if not any(ct.startswith(prefix) for prefix in ["text/html", "text/plain", "application/json", "application/xhtml"]):
                    return None
                
                # Check size
                size = int(r.headers.get("Content-Length", 0))
                if size > 10_000_000:  # 10MB limit
                    return None
                
                from datetime import datetime, timezone
                content = r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
                increment_daily_counter()
                return content
                
        except urllib.error.HTTPError as e:
            if e.code == 429:  # Too Many Requests
                retry_after = int(e.headers.get("Retry-After", 60))
                time.sleep(retry_after)
                set_domain_cooldown(domain, retry_after * 2)
                continue
            elif e.code in (403, 404):
                log_discovery("http", url, "fetch", result="error", details=f"HTTP {e.code}")
                set_domain_cooldown(domain, 3600)
                return None
            elif e.code == 503:
                time.sleep(10)
                continue
            else:
                log_discovery("http", url, "fetch", result="error", details=f"HTTP {e.code}")
                return None
                
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            log_discovery("http", url, "fetch", result="error", details="Connection error")
            return None
        
        except ValueError as e:
            if "blocked" in str(e).lower() or "unsafe" in str(e).lower():
                set_domain_cooldown(domain, 86400)
            return None
    
    return None


def get_robots_txt(base_url, robots_cache=None):
    """Get and parse robots.txt"""
    if robots_cache is None:
        robots_cache = {}
    
    robots_url = base_url.rstrip("/") + "/robots.txt"
    
    if robots_url in robots_cache:
        return robots_cache[robots_url]
    
    try:
        content = http_get(robots_url, timeout=10)
        if content:
            rp = RobotFileParser()
            rp.parse(content.splitlines())
            robots_cache[robots_url] = rp
            return rp
    except Exception:
        pass
    
    return None


def can_fetch(url, robots_cache=None):
    """Check if we can fetch a URL based on robots.txt"""
    if robots_cache is None:
        robots_cache = {}
    
    p = urllib.parse.urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    
    rp = get_robots_txt(base, robots_cache)
    if rp:
        return rp.can_fetch(UA, url)
    
    return True


# ============================================================================
# EXTRACTION UTILITIES
# ============================================================================

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?[\d\s\-\(\)]{7,}\d")
LINKEDIN_RE = re.compile(r"linkedin\.com/(company|in|pub)/[^\s\"'<>]+")
TWITTER_RE = re.compile(r"twitter\.com/[^\s\"'<>]+")
GITHUB_RE = re.compile(r"github\.com/[^\s\"'<>]+")


def extract_emails(text):
    """Extract email addresses from text"""
    if not text:
        return []
    emails = set()
    for match in EMAIL_RE.finditer(text):
        email = match.group().lower().rstrip(".")
        if not email.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico")):
            emails.add(email)
    return sorted(list(emails))


def extract_phone_numbers(text):
    """Extract phone numbers from text"""
    if not text:
        return []
    phones = set()
    for match in PHONE_RE.finditer(text):
        phone = match.group().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        phones.add(phone)
    return sorted(list(phones))


def extract_social_links(text, base_url=None):
    """Extract social media links from text"""
    social = {}
    
    for match in LINKEDIN_RE.finditer(text or ""):
        url = match.group()
        if not url.startswith("http"):
            url = "https://" + url
        social.setdefault("linkedin", []).append(url)
    
    for match in TWITTER_RE.finditer(text or ""):
        url = match.group()
        if not url.startswith("http"):
            url = "https://" + url
        social.setdefault("twitter", []).append(url)
    
    for match in GITHUB_RE.finditer(text or ""):
        url = match.group()
        if not url.startswith("http"):
            url = "https://" + url
        social.setdefault("github", []).append(url)
    
    return social


def clean_text(text):
    """Clean and normalize text"""
    if not text:
        return ""
    import html as H
    text = str(text)
    # Remove script and style tags
    text = re.sub(r"(?is)<(script|style|noscript|svg|iframe)\b.*?</\1>", " ", text)
    # Remove all HTML tags
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    # Unescape HTML entities
    text = H.unescape(text)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_links(html, base_url):
    """Extract all links from HTML"""
    if not html:
        return []
    links = set()
    p = urllib.parse.urlparse(base_url)
    base_netloc = p.netloc
    
    for match in re.finditer(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE):
        url = match.group(1)
        if url.startswith("#"):
            continue
        if url.startswith("javascript:"):
            continue
        
        full_url = urllib.parse.urljoin(base_url, url)
        parsed = urllib.parse.urlparse(full_url)
        
        # Only follow same-domain links or known safe domains
        if parsed.netloc == base_netloc or not parsed.netloc:
            links.add(full_url)
    
    return sorted(list(links))


def extract_company_info(html, url):
    """Extract company information from HTML"""
    text = clean_text(html)
    
    info = {
        "domain": None,
        "website": url,
        "company": None,
        "industry": None,
        "country": None,
        "city": None,
        "description": None,
        "founded_year": None,
        "employees_min": None,
        "employees_max": None,
        "revenue_min": None,
        "revenue_max": None,
        "technologies": [],
        "emails": [],
        "phones": [],
        "social": {},
        "address": None,
        "ceo": None,
        "cto": None
    }
    
    try:
        # Extract company name
        # Check title
        title_match = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
        if title_match:
            title = clean_text(title_match.group(1))
            if title and "|" in title:
                info["company"] = title.split("|")[0].strip()
            elif title:
                info["company"] = title
        
        # Check h1
        h1_match = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.IGNORECASE)
        if h1_match and not info["company"]:
            info["company"] = clean_text(h1_match.group(1))
        
        # Domain
        from .config import domain_of
        info["domain"] = domain_of(url) if url else None
        
        # Check meta description
        desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if desc_match:
            info["description"] = clean_text(desc_match.group(1))
        
        # Extract emails
        info["emails"] = extract_emails(text)
        
        # Extract phones
        info["phones"] = extract_phone_numbers(text)
        
        # Extract social links
        info["social"] = extract_social_links(text)
        
        # Try to extract industry
        from .config import INDUSTRY_KEYWORDS
        industry_keywords = list(INDUSTRY_KEYWORDS.keys())
        text_lower = text.lower()
        for keyword in industry_keywords:
            if keyword.lower() in text_lower:
                info["industry"] = keyword
                break
        
        # Try to extract location
        country_patterns = ["pakistan", "usa", "uk", "canada", "australia", "germany", "france", "india", "china", "japan"]
        for country in country_patterns:
            if country.lower() in text_lower:
                info["country"] = country.title()
                break
        
        # Extract CEO/CTO from text
        for title in ["ceo", "founder", "cto"]:
            pattern = rf"({title})[\s:]+([A-Z][a-z]+\s+[A-Z][a-z]+)"
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                info[title.lower()] = match.group(2)
        
        # Extract technologies
        info["technologies"] = detect_technologies(html)
        
        return info
        
    except Exception as e:
        print(f"Error extracting company info: {e}")
        return info


def detect_technologies(html):
    """Detect technologies used on a website"""
    from .config import SERVICE_KEYWORDS
    technologies = set()
    
    tech_patterns = {
        "WordPress": r"wp-content|/wp-|WordPress",
        "Shopify": r"shopify|cdn.shopify",
        "React": r"react\.js|ReactDOM",
        "Angular": r"angular\.js|ng-",
        "Vue": r"vue\.js|data-v-",
        "Node.js": r"Node\.js|Express",
        "Django": r"django|/static/admin/",
        "Ruby on Rails": r"rails\.js|Rails",
        "Laravel": r"laravel\.js|/laravel/",
        "Bootstrap": r"bootstrap\.css|bootstrap\.js",
        "jQuery": r"jquery\.js",
        "AWS": r"amazonaws\.com|aws",
        "Google Analytics": r"google-analytics|ga\.js|gtag",
        "Stripe": r"stripe\.com|Stripe\.js",
        "PayPal": r"paypal\.com|paypalobjects",
        "HubSpot": r"hubspot|hs-script",
        "Salesforce": r"salesforce|force\.com",
        "Google Tag Manager": r"googletagmanager\.com",
        "Hotjar": r"hotjar\.com",
        "Intercom": r"intercom\.com|Intercom",
        "Slack": r"slack\.com",
        "Zendesk": r"zendesk\.com",
        "Mailchimp": r"mailchimp|list-manage\.com",
        "SendGrid": r"sendgrid\.net",
        "Postmark": r"postmarkapp\.com",
        "Heroku": r"herokuapp\.com",
        "Netlify": r"netlify\.app|netlify\.com",
        "Vercel": r"vercel\.app|vercel\.com",
        "Cloudflare": r"cloudflare\.com",
        "NGINX": r"nginx",
        "Apache": r"Apache",
        "MySQL": r"mysql",
        "PostgreSQL": r"postgresql|postgres",
        "MongoDB": r"mongodb|mongolab",
        "Redis": r"redis",
        "Elasticsearch": r"elasticsearch",
        "Docker": r"docker",
        "Kubernetes": r"kubernetes|k8s"
    }
    
    text = (html or "").lower()
    for tech, pattern in tech_patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            technologies.add(tech)
    
    return sorted(list(technologies))
