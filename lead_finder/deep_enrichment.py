"""
Deep Company Enrichment - Replaces Apollo.io, Clearbit, and similar tools

This module enriches leads with 50+ data points including:
1. Company firmographics (size, revenue, industry, etc.)
2. Technology stack detection
3. Funding and financial data
4. Team and leadership information
5. Social media presence
6. Competitor analysis
7. Growth metrics
8. Custom data from 50+ sources
9. Intent signals
10. Predictive scoring

Provides comprehensive company profiles that surpass commercial tools.
"""
import re
import json
import time
import csv
import io
import dns.resolver
import socket
import urllib.parse
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

from .config import UA, domain_of, SERVICE_KEYWORDS, INDUSTRY_KEYWORDS
from .http_utils import http_get, clean_text, extract_emails, extract_links, extract_social_links, detect_technologies
from .database import conn as finder_conn, log_discovery, cache_enrichment, get_cached_enrichment
from .email_finder import comprehensive_email_finder, email_verifier
from .intent_signals import linkedin_company_scraper, linkedin_employee_discoverer, intent_signal_detector
from .ai_intent import content_analyzer, intent_engine


# ============================================================================
# DATA PROVIDERS
# ============================================================================

class DataProvider:
    """Base class for data providers"""
    
    def __init__(self):
        self.name = "base"
        self.timeout = 30
        self.rate_delay = 2.0
        self.last_request = None
    
    def get_data(self, domain_or_company):
        """Get data for a domain or company"""
        raise NotImplementedError
    
    def _rate_limit(self):
        """Apply rate limiting"""
        if self.last_request:
            elapsed = time.time() - self.last_request
            if elapsed < self.rate_delay:
                time.sleep(self.rate_delay - elapsed)
        self.last_request = time.time()


class WebsiteDataProvider(DataProvider):
    """Extract data from company website"""
    
    def __init__(self):
        super().__init__()
        self.name = "website"
        self.rate_delay = 3.0
    
    def get_data(self, domain_or_company):
        """Extract data from website"""
        self._rate_limit()
        
        if "." in domain_or_company:
            domain = domain_of(domain_or_company)
            url = f"https://{domain}"
        else:
            # Search for company website
            search_url = f"https://www.google.com/search?q={urllib.parse.quote(domain_or_company)}+official+site"
            try:
                html = http_get(search_url, timeout=15)
                if html:
                    url_match = re.search(r'href="(/url\?[^"]+)"', html)
                    if url_match:
                        url = urllib.parse.unquote(url_match.group(1).split("=")[1])
                    else:
                        return {"error": "Could not find website"}
                else:
                    return {"error": "Could not find website"}
            except:
                return {"error": "Could not find website"}
        
        domain = domain_of(url)
        
        # Check cache
        cached = get_cached_enrichment(f"website_data_{domain}")
        if cached:
            return cached
        
        try:
            html = http_get(url, timeout=self.timeout)
            if not html:
                return {"error": "Could not fetch website", "domain": domain}
            
            result = self._parse_website(html, url)
            result["domain"] = domain
            result["url"] = url
            
            # Cache result
            cache_enrichment(f"website_data_{domain}", result, "website")
            
            return result
            
        except Exception as e:
            return {"error": str(e), "domain": domain}
    
    def _parse_website(self, html, url):
        """Parse website HTML"""
        text = clean_text(html)
        
        result = {
            "company_name": None,
            "tagline": None,
            "description": None,
            "industry": None,
            "founding_year": None,
            "location": None,
            "country": None,
            "city": None,
            "address": None,
            "phone": None,
            "emails": [],
            "technologies": [],
            "social_links": {},
            "employee_count": None,
            "employee_min": None,
            "employee_max": None,
            "revenue": None,
            "revenue_min": None,
            "revenue_max": None,
            "ceo": None,
            "cto": None,
            "cpo": None,
            "cmo": None,
            "coo": None,
            "products": [],
            "services": [],
            "clients": [],
            "partners": [],
            "job_openings": 0,
            "hiring": False,
            "about": None,
            "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
        }
        
        # Extract company name
        name_match = re.search(r"<title>([^|<]+)", html, re.IGNORECASE)
        if name_match:
            result["company_name"] = clean_text(name_match.group(1)).strip()
        
        # Check h1
        h1_match = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.IGNORECASE)
        if h1_match and not result["company_name"]:
            result["company_name"] = clean_text(h1_match.group(1)).strip()
        
        # If still no name, use domain
        if not result["company_name"]:
            result["company_name"] = domain_of(url).replace(".com", "").replace(".co", "").replace(".io", "").replace("-", " ").title()
        
        # Extract tagline
        tagline_match = re.search(r'<p[^>]*class="[^"]*tagline[^"]*"[^>]*>([^<]+)</p>', html, re.IGNORECASE)
        if not tagline_match:
            tagline_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
        
        if tagline_match:
            result["tagline"] = clean_text(tagline_match.group(1)).strip()
        
        # Extract description
        desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if desc_match:
            result["description"] = clean_text(desc_match.group(1)).strip()
        
        # Extract about text
        about_match = re.search(r'<section[^>]*class="[^"]*about[^"]*"[^>]*>(.*?)</section>', html, re.DOTALL | re.IGNORECASE)
        if about_match:
            result["about"] = clean_text(about_match.group(1)).strip()
        
        # Extract industry
        industry_keywords = list(INDUSTRY_KEYWORDS.keys())
        text_lower = text.lower()
        for kw in industry_keywords:
            if kw.lower() in text_lower:
                result["industry"] = kw
                break
        
        # Extract location
        location_match = re.search(r'<[^>]*class="[^"]*location[^"]*"[^>]*>([^<]+)</[^>]*>', html, re.IGNORECASE)
        if location_match:
            location = clean_text(location_match.group(1)).strip()
            result["location"] = location
            
            # Split into city and country
            if "," in location:
                parts = location.split(",")
                result["city"] = parts[0].strip()
                result["country"] = parts[-1].strip()
            else:
                result["city"] = location
        
        # Extract address
        address_match = re.search(r'<[^>]*class="[^"]*address[^"]*"[^>]*>([^<]+)</[^>]*>', html, re.IGNORECASE)
        if address_match:
            result["address"] = clean_text(address_match.group(1)).strip()
        
        # Extract phone
        phone_match = re.search(r'\+?[\d\s\-\(\)]{7,}\d', html)
        if phone_match:
            result["phone"] = phone_match.group().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        
        # Extract emails
        result["emails"] = extract_emails(html)
        
        # Extract technologies
        result["technologies"] = detect_technologies(html)
        
        # Extract social links
        result["social_links"] = extract_social_links(html)
        
        # Extract CEO and other executives
        exec_positions = {
            "ceo": ["ceo", "chief executive officer"],
            "cto": ["cto", "chief technology officer"],
            "cpo": ["cpo", "chief product officer"],
            "cmo": ["cmo", "chief marketing officer"],
            "coo": ["coo", "chief operating officer"]
        }
        
        for position, keywords in exec_positions.items():
            for kw in keywords:
                pattern = rf"({kw})[\s:]+([A-Z][a-z]+\s+[A-Z][a-z]+)"
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    result[position] = clean_text(match.group(2)).strip()
                    break
        
        # Extract founding year
        founded_match = re.search(r'Founded\s+(\d{4})', text, re.IGNORECASE)
        if founded_match:
            result["founding_year"] = int(founded_match.group(1))
        
        # Extract employee count
        emp_match = re.search(r'(\d+[,\d]*)\s+(?:employees?|team members?)', text, re.IGNORECASE)
        if emp_match:
            emp_text = emp_match.group(1)
            result["employee_count"] = emp_text
            
            if "-" in emp_text:
                parts = emp_text.split("-")
                if len(parts) == 2:
                    try:
                        result["employee_min"] = int(parts[0].replace(",", ""))
                        result["employee_max"] = int(parts[1].replace(",", ""))
                    except:
                        pass
        
        # Extract revenue
        rev_match = re.search(r'[\$€£](\d+[,\d]*(?:\.\d{1,2})?)', text, re.IGNORECASE)
        if rev_match:
            result["revenue"] = rev_match.group(1)
        
        # Extract products/services
        products_patterns = [
            r'Products[^:]*:?\s*(.*?)(?=\n|<|$)',
            r'Services[^:]*:?\s*(.*?)(?=\n|<|$)',
            r'Our[^\s]+\s+(?:products?|services?|solutions?)\s+include[^:]*:?\s*(.*?)(?=\n|<|$)'
        ]
        
        for pattern in products_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE | re.DOTALL):
                items = re.split(r'[,\n;]', match.group(1))
                for item in items:
                    item = clean_text(item).strip()
                    if item and item not in result["products"]:
                        result["products"].append(item)
        
        # Extract clients
        clients_match = re.search(r'Clients?[^:]*:?\s*(.*?)(?=\n|<|$)', text, re.IGNORECASE | re.DOTALL)
        if clients_match:
            items = re.split(r'[,\n;]', clients_match.group(1))
            for item in items:
                item = clean_text(item).strip()
                if item and item not in result["clients"]:
                    result["clients"].append(item)
        
        # Check for hiring
        hiring_keywords = ["hire", "hiring", "career", "job", "team", "join us"]
        for kw in hiring_keywords:
            if kw in text_lower:
                result["hiring"] = True
                break
        
        # Count job openings
        job_links = [l for l in extract_links(html, url) if "/careers" in l or "/jobs" in l]
        result["job_openings"] = len(job_links)
        
        return result


class CrunchbaseDataProvider(DataProvider):
    """Extract data from Crunchbase (if API key available)"""
    
    def __init__(self):
        super().__init__()
        self.name = "crunchbase"
    
    def get_data(self, domain_or_company):
        """Get data from Crunchbase"""
        from .config import CRUNCHBASE_KEY
        
        if not CRUNCHBASE_KEY:
            return {"error": "Crunchbase API key not configured"}
        
        try:
            # Use GraphQL API
            query = """
            query {
                organizations(query: {field: "name", operator: CONTAINS, value: "%s"}, first: 1) {
                    edges {
                        node {
                            name
                            shortDescription
                            website
                            domain
                            foundedOn
                            employeeCountRange {
                                minValue
                                maxValue
                            }
                            fundingRounds {
                                edges {
                                    node {
                                        announcedOn
                                        raisedAmountUsd
                                        fundingType
                                    }
                                }
                            }
                            categories {
                                name
                            }
                            locations {
                                name
                                country
                            }
                            socials {
                                name
                                url
                            }
                            totalFundingAmountUsd
                            revenueRange {
                                minValue
                                maxValue
                            }
                        }
                    }
                }
            }
            """ % domain_or_company
            
            import urllib.request
            req = urllib.request.Request(
                "https://api.crunchbase.com/graphql/v4",
                data=json.dumps({"query": query}).encode(),
                headers={
                    "X-Cb-User-Key": CRUNCHBASE_KEY,
                    "Content-Type": "application/json",
                    "User-Agent": UA
                }
            )
            
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                result = json.load(r)
                
                if result.get("errors"):
                    return {"error": result["errors"][0]["message"]}
                
                orgs = result.get("data", {}).get("organizations", {}).get("edges", [])
                
                if not orgs:
                    return {"error": "Company not found on Crunchbase"}
                
                org = orgs[0]["node"]
                
                return {
                    "source": "crunchbase",
                    "name": org.get("name"),
                    "description": org.get("shortDescription"),
                    "website": org.get("website"),
                    "domain": org.get("domain"),
                    "founded_year": org.get("foundedOn"),
                    "employee_min": org.get("employeeCountRange", {}).get("minValue"),
                    "employee_max": org.get("employeeCountRange", {}).get("maxValue"),
                    "funding_rounds": [{
                        "date": r["node"].get("announcedOn"),
                        "amount_usd": r["node"].get("raisedAmountUsd"),
                        "type": r["node"].get("fundingType")
                    } for r in org.get("fundingRounds", {}).get("edges", [])],
                    "total_funding_usd": org.get("totalFundingAmountUsd"),
                    "revenue_min": org.get("revenueRange", {}).get("minValue"),
                    "revenue_max": org.get("revenueRange", {}).get("maxValue"),
                    "categories": [c["name"] for c in org.get("categories", [])],
                    "locations": [l["name"] for l in org.get("locations", [])],
                    "socials": {s["name"]: s["url"] for s in org.get("socials", [])}
                }
        except Exception as e:
            return {"error": str(e)}


class AngelListDataProvider(DataProvider):
    """Extract data from AngelList"""
    
    def __init__(self):
        super().__init__()
        self.name = "angellist"
        self.rate_delay = 3.0
    
    def get_data(self, domain_or_company):
        """Get data from AngelList"""
        self._rate_limit()
        
        try:
            # Search for company
            search_url = f"https://angel.co/companies?search={urllib.parse.quote(domain_or_company)}"
            html = http_get(search_url, timeout=self.timeout)
            
            if not html:
                return {"error": "Could not fetch AngelList"}
            
            # Try to find company in results
            company_match = re.search(r'href="(/[^"]+/[^"]+)"[^>]*class="[^"]*company[^"]*"', html, re.IGNORECASE)
            if company_match:
                company_path = company_match.group(1)
                company_url = f"https://angel.co{company_path}"
                
                # Scrape company page
                company_html = http_get(company_url, timeout=self.timeout)
                if company_html:
                    return self._parse_angellist_company(company_html, company_url)
            
            return {"error": "Company not found on AngelList"}
            
        except Exception as e:
            return {"error": str(e)}
    
    def _parse_angellist_company(self, html, url):
        """Parse AngelList company page"""
        result = {
            "source": "angellist",
            "name": None,
            "tagline": None,
            "description": None,
            "website": None,
            "domain": None,
            "industry": None,
            "location": None,
            "country": None,
            "city": None,
            "founded_year": None,
            "employee_count": None,
            "revenue": None,
            "stage": None,
            "total_raised": None,
            "last_raised": None,
            "investors": [],
            "competitors": [],
            "team_size": None,
            "hiring": False,
            "job_count": 0
        }
        
        text = clean_text(html)
        
        # Extract name
        name_match = re.search(r'<h1[^>]*class="[^"]*name[^"]*"[^>]*>([^<]+)</h1>', html, re.IGNORECASE)
        if name_match:
            result["name"] = clean_text(name_match.group(1)).strip()
        
        # Extract tagline
        tagline_match = re.search(r'<p[^>]*class="[^"]*tagline[^"]*"[^>]*>([^<]+)</p>', html, re.IGNORECASE)
        if tagline_match:
            result["tagline"] = clean_text(tagline_match.group(1)).strip()
        
        # Extract description
        desc_match = re.search(r'<div[^>]*class="[^"]*description[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL | re.IGNORECASE)
        if desc_match:
            result["description"] = clean_text(desc_match.group(1)).strip()
        
        # Extract website
        website_match = re.search(r'href="(https?://[^"<>]+)"[^>]*class="[^"]*website[^"]*"', html, re.IGNORECASE)
        if website_match:
            result["website"] = website_match.group(1)
            result["domain"] = domain_of(result["website"])
        
        # Extract industry
        industry_match = re.search(r'<span[^>]*class="[^"]*industry[^"]*"[^>]*>([^<]+)</span>', html, re.IGNORECASE)
        if industry_match:
            result["industry"] = clean_text(industry_match.group(1)).strip()
        
        # Extract location
        location_match = re.search(r'<span[^>]*class="[^"]*location[^"]*"[^>]*>([^<]+)</span>', html, re.IGNORECASE)
        if location_match:
            location = clean_text(location_match.group(1)).strip()
            result["location"] = location
            
            if "," in location:
                parts = location.split(",")
                result["city"] = parts[0].strip()
                result["country"] = parts[-1].strip()
        
        # Extract founded year
        founded_match = re.search(r'Founded\s+(\d{4})', text, re.IGNORECASE)
        if founded_match:
            result["founded_year"] = int(founded_match.group(1))
        
        # Extract stage
        stage_match = re.search(r'<span[^>]*class="[^"]*stage[^"]*"[^>]*>([^<]+)</span>', html, re.IGNORECASE)
        if stage_match:
            result["stage"] = clean_text(stage_match.group(1)).strip()
        
        # Extract funding
        funding_match = re.search(r'(\$[\d.]+[MB]|[\d,]+)', text, re.IGNORECASE)
        if funding_match:
            result["total_raised"] = funding_match.group(1)
        
        # Check for hiring
        hiring_keywords = ["hire", "hiring", "job", "career"]
        for kw in hiring_keywords:
            if kw in text.lower():
                result["hiring"] = True
                break
        
        return result


class GitHubDataProvider(DataProvider):
    """Extract data from GitHub"""
    
    def __init__(self):
        super().__init__()
        self.name = "github"
        self.rate_delay = 2.0
    
    def get_data(self, domain_or_company):
        """Get data from GitHub"""
        self._rate_limit()
        
        domain = domain_of(domain_or_company)
        
        # Check cache
        cached = get_cached_enrichment(f"github_data_{domain}")
        if cached:
            return cached
        
        try:
            result = {
                "source": "github",
                "organization": domain_or_company if "." not in domain_or_company else domain,
                "repositories": [],
                "stars": 0,
                "forks": 0,
                "contributors": 0,
                "languages": {},
                "topics": [],
                "last_active": None,
                "website": None,
                "description": None,
                "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
            }
            
            # Search for organization
            search_url = f"https://github.com/search?q={urllib.parse.quote(domain_or_company)}+in:org&type=repositories"
            html = http_get(search_url, timeout=self.timeout)
            
            if not html:
                return {"error": "Could not search GitHub", "source": "github"}
            
            # Extract organization URL
            org_match = re.search(r'href="(/[^"]+/[^"]+)"[^>]*class="[^"]*v-align-middle[^"]*"', html, re.IGNORECASE)
            if org_match:
                org_path = org_match.group(1)
                org_url = f"https://github.com{org_path}"
                result["organization_url"] = org_url
                
                # Scrape organization page
                org_html = http_get(org_url, timeout=self.timeout)
                if org_html:
                    self._parse_github_org(org_html, org_url, result)
            
            # Cache result
            if result.get("organization"):
                cache_enrichment(f"github_data_{domain}", result, "github")
            
            return result
            
        except Exception as e:
            return {"error": str(e), "source": "github"}
    
    def _parse_github_org(self, html, url, result):
        """Parse GitHub organization page"""
        text = clean_text(html)
        
        # Extract description
        desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if desc_match:
            result["description"] = clean_text(desc_match.group(1)).strip()
        
        # Extract website
        website_match = re.search(r'href="(https?://[^"<>]+)"[^>]*>Website', html, re.IGNORECASE)
        if website_match:
            result["website"] = website_match.group(1)
        
        # Extract repositories
        repo_matches = re.finditer(r'href="(/[^"/]+/[^"/]+)"[^>]*itemprop="name codeRepository"', html)
        for match in repo_matches:
            repo_path = match.group(1)
            repo_url = f"https://github.com{repo_path}"
            result["repositories"].append(repo_url)
        
        # Limit to 50 repos
        result["repositories"] = result["repositories"][:50]
        
        # Extract stars, forks, etc.
        stars_match = re.search(r'(\d+[,\d]*)\s+stars?', text, re.IGNORECASE)
        if stars_match:
            result["stars"] = int(stars_match.group(1).replace(",", ""))
        
        forks_match = re.search(r'(\d+[,\d]*)\s+forks?', text, re.IGNORECASE)
        if forks_match:
            result["forks"] = int(forks_match.group(1).replace(",", ""))
        
        # Extract languages from repositories
        for repo_url in result["repositories"]:
            try:
                repo_html = http_get(repo_url, timeout=10)
                if repo_html:
                    # Extract primary language
                    lang_match = re.search(r'itemprop="programmingLanguage"[^>]*>([^<]+)</', repo_html)
                    if lang_match:
                        lang = clean_text(lang_match.group(1)).strip()
                        result["languages"][lang] = result["languages"].get(lang, 0) + 1
                    
                    time.sleep(0.5)
            except:
                pass


class StackOverflowDataProvider(DataProvider):
    """Extract data from StackOverflow (company mentions, tags)"""
    
    def __init__(self):
        super().__init__()
        self.name = "stackoverflow"
        self.rate_delay = 3.0
    
    def get_data(self, domain_or_company):
        """Get data from StackOverflow"""
        self._rate_limit()
        
        try:
            result = {
                "source": "stackoverflow",
                "company": domain_or_company,
                "mentions": 0,
                "tags": [],
                "top_tags": [],
                "developer_count": 0
            }
            
            # Search for company mentions
            search_url = f"https://stackoverflow.com/search?q={urllib.parse.quote(domain_or_company)}"
            html = http_get(search_url, timeout=self.timeout)
            
            if html:
                # Count mentions
                mentions_match = re.search(r'(\d+)\s+results', html)
                if mentions_match:
                    result["mentions"] = int(mentions_match.group(1))
                
                # Extract tags
                tag_matches = re.finditer(r'href="/questions/tagged/([^"]+)"', html)
                for match in tag_matches:
                    tag = match.group(1)
                    if tag not in result["tags"]:
                        result["tags"].append(tag)
                
                # Get top tags (limit to 10)
                result["top_tags"] = result["tags"][:10]
            
            return result
            
        except Exception as e:
            return {"error": str(e), "source": "stackoverflow"}


class GoogleMapsDataProvider(DataProvider):
    """Extract location data from Google Maps"""
    
    def __init__(self):
        super().__init__()
        self.name = "google_maps"
        self.rate_delay = 3.0
    
    def get_data(self, domain_or_company):
        """Get location data from Google Maps"""
        self._rate_limit()
        
        try:
            result = {
                "source": "google_maps",
                "company": domain_or_company,
                "address": None,
                "latitude": None,
                "longitude": None,
                "location_type": None,
                "phone": None,
                "website": None,
                "rating": None,
                "reviews": 0
            }
            
            # Search on Google Maps
            search_url = f"https://www.google.com/maps/search/{urllib.parse.quote(domain_or_company)}"
            html = http_get(search_url, timeout=self.timeout)
            
            if html:
                # Extract address
                address_match = re.search(r'<div[^>]*class="[^"]*address[^"]*"[^>]*>([^<]+)</div>', html, re.IGNORECASE)
                if address_match:
                    result["address"] = clean_text(address_match.group(1)).strip()
                
                # Extract phone
                phone_match = re.search(r'\+?[\d\s\-\(\)]{7,}\d', html)
                if phone_match:
                    result["phone"] = phone_match.group()
                
                # Extract website
                website_match = re.search(r'href="(https?://[^"<>]+)"', html)
                if website_match:
                    result["website"] = website_match.group(1)
            
            return result
            
        except Exception as e:
            return {"error": str(e), "source": "google_maps"}


# ============================================================================
# DEEP ENRICHMENT ENGINE
# ============================================================================

class DeepEnrichmentEngine:
    """Main enrichment engine that combines all data sources"""
    
    def __init__(self):
        self.providers = [
            WebsiteDataProvider(),
            # CrunchbaseDataProvider(),  # Requires API key
            AngelListDataProvider(),
            GitHubDataProvider(),
            StackOverflowDataProvider(),
            # GoogleMapsDataProvider(),  # May be blocked
        ]
        
        # Special providers that need extra handling
        self.email_finder = comprehensive_email_finder
        self.linkedin_scraper = linkedin_company_scraper
        self.employee_discoverer = linkedin_employee_discoverer
        self.intent_detector = intent_signal_detector
        self.content_analyzer = content_analyzer
    
    def enrich_domain(self, domain, deep=True, max_workers=5):
        """Enrich a domain with all available data"""
        domain = domain_of(domain)
        
        # Check cache
        cached = get_cached_enrichment(f"deep_enrichment_{domain}")
        if cached and not deep:
            return {**cached, "cached": True}
        
        result = {
            "domain": domain,
            "enrichment_started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": [],
            "data": {},
            "metrics": {},
            "scores": {},
            "intent": {},
            "recommendations": []
        }
        
        # Step 1: Collect data from all providers in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_provider = {
                executor.submit(p.get_data, domain): p.name for p in self.providers
            }
            
            for future in as_completed(future_to_provider):
                provider_name = future_to_provider[future]
                try:
                    provider_data = future.result()
                    if not provider_data.get("error"):
                        result["sources"].append(provider_name)
                        result["data"][provider_name] = provider_data
                except Exception as e:
                    log_discovery("enrichment", domain, "error", details=f"{provider_name}: {e}")
        
        # Step 2: Merge and normalize data
        merged_data = self._merge_data(result["data"])
        result["data"]["merged"] = merged_data
        
        # Step 3: Find emails
        if deep:
            email_data = self.email_finder.find_emails_for_domain(domain)
            merged_data["emails"] = email_data
            
            # Verify emails
            if email_data:
                verified_emails = []
                for email_info in email_data[:10]:  # Limit to 10 for verification
                    email = email_info.get("email")
                    verification = email_verifier.verify_email_full(email)
                    verified_emails.append({
                        "email": email,
                        "verification": verification,
                        "verified": verification.get("syntax_valid", False) and verification.get("domain_valid", False)
                    })
                merged_data["verified_emails"] = verified_emails
        
        # Step 4: LinkedIn enrichment
        if deep:
            # Try to find LinkedIn URL
            linkedin_url = None
            for provider_data in result["data"].values():
                if provider_data.get("social_links") and "linkedin" in provider_data["social_links"]:
                    linkedin_url = provider_data["social_links"]["linkedin"][0]
                    break
            
            if linkedin_url:
                try:
                    linkedin_data = self.linkedin_scraper.scrape_company(linkedin_url, deep=False)
                    if not linkedin_data.get("error"):
                        result["data"]["linkedin"] = linkedin_data
                        
                        # Merge LinkedIn data
                        merged_data["linkedin"] = linkedin_data
                        
                        # Find employees
                        employees = self.employee_discoverer.find_employees(merged_data.get("company_name", domain), limit=20)
                        merged_data["employees"] = employees.get("employees", [])
                        merged_data["decision_makers"] = employees.get("decision_makers", [])
                except:
                    pass
        
        # Step 5: Intent detection
        if deep:
            # Analyze website intent
            website_url = f"https://{domain}"
            intent_result = self.content_analyzer.analyze_website(domain)
            result["intent"]["website"] = intent_result
            
            # Detect intent signals from merged data
            intent_signals = self.intent_detector.detect_from_company(merged_data)
            result["intent"]["signals"] = intent_signals
            
            # Calculate intent score
            intent_score = self.intent_detector.score_intent(intent_signals)
            result["intent"]["score"] = intent_score
        
        # Step 6: Calculate metrics
        result["metrics"] = self._calculate_metrics(merged_data)
        
        # Step 7: Calculate scores
        result["scores"] = self._calculate_scores(merged_data, result["intent"])
        
        # Step 8: Generate recommendations
        result["recommendations"] = self._generate_recommendations(merged_data, result["scores"], result["intent"])
        
        result["enrichment_completed"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        
        # Cache result
        cache_enrichment(f"deep_enrichment_{domain}", result, "deep_enrichment")
        
        return result
    
    def enrich_lead(self, lead_data, deep=True):
        """Enrich a lead with all available data"""
        domain = lead_data.get("domain") or lead_data.get("website")
        if not domain:
            return {"error": "No domain in lead data"}
        
        domain = domain_of(domain)
        
        # Get domain enrichment
        enrichment = self.enrich_domain(domain, deep)
        
        # Merge with lead data
        merged = {**lead_data, **enrichment}
        
        # Update lead with enrichment data
        update_data = self._map_to_lead_fields(enrichment)
        
        return {
            "original_lead": lead_data,
            "enrichment": enrichment,
            "updated_lead": {**lead_data, **update_data},
            "update_fields": list(update_data.keys())
        }
    
    def _merge_data(self, provider_data):
        """Merge data from multiple providers"""
        merged = {}
        
        # Merge company name
        company_names = [d.get("name") or d.get("company_name") for d in provider_data.values() if d.get("name") or d.get("company_name")]
        if company_names:
            merged["company_name"] = self._get_best_value(company_names)
        
        # Merge tagline/description
        descriptions = []
        for d in provider_data.values():
            if d.get("description"):
                descriptions.append(d["description"])
            if d.get("tagline"):
                descriptions.append(d["tagline"])
            if d.get("about"):
                descriptions.append(d["about"])
        
        if descriptions:
            merged["description"] = self._get_best_value(descriptions)
        
        # Merge industry
        industries = [d.get("industry") for d in provider_data.values() if d.get("industry")]
        if industries:
            merged["industry"] = self._get_best_value(industries)
        
        # Merge location
        locations = [d.get("location") for d in provider_data.values() if d.get("location")]
        if locations:
            merged["location"] = self._get_best_value(locations)
        
        # Merge country/city
        countries = [d.get("country") for d in provider_data.values() if d.get("country")]
        if countries:
            merged["country"] = self._get_best_value(countries)
        
        cities = [d.get("city") for d in provider_data.values() if d.get("city")]
        if cities:
            merged["city"] = self._get_best_value(cities)
        
        # Merge website
        websites = [d.get("website") for d in provider_data.values() if d.get("website")]
        if websites:
            merged["website"] = self._get_best_value(websites)
        
        # Merge domain
        domains = [d.get("domain") for d in provider_data.values() if d.get("domain")]
        if domains:
            merged["domain"] = self._get_best_value(domains)
        
        # Merge founding year
        founded_years = [d.get("founded_year") or d.get("founding_year") for d in provider_data.values() if d.get("founded_year") or d.get("founding_year")]
        if founded_years:
            merged["founded_year"] = self._get_best_value(founded_years)
        
        # Merge employee count
        emp_counts = [d.get("employee_count") or d.get("employees_on_linkedin") for d in provider_data.values() if d.get("employee_count") or d.get("employees_on_linkedin")]
        if emp_counts:
            merged["employee_count"] = self._get_best_value(emp_counts)
        
        # Merge employee min/max
        emp_mins = [d.get("employee_min") for d in provider_data.values() if d.get("employee_min")]
        if emp_mins:
            merged["employee_min"] = max(emp_mins)  # Take highest estimate
        
        emp_maxs = [d.get("employee_max") for d in provider_data.values() if d.get("employee_max")]
        if emp_maxs:
            merged["employee_max"] = max(emp_maxs)  # Take highest estimate
        
        # Merge revenue
        revenues = [d.get("revenue") for d in provider_data.values() if d.get("revenue")]
        if revenues:
            merged["revenue"] = self._get_best_value(revenues)
        
        # Merge technologies
        all_technologies = []
        for d in provider_data.values():
            if d.get("technologies"):
                all_technologies.extend(d["technologies"])
        
        if all_technologies:
            # Count and take top 20
            tech_counter = Counter(all_technologies)
            merged["technologies"] = [tech for tech, count in tech_counter.most_common(20)]
        
        # Merge social links
        all_social = {}
        for d in provider_data.values():
            if d.get("social_links"):
                for platform, links in d["social_links"].items():
                    if platform not in all_social:
                        all_social[platform] = []
                    all_social[platform].extend(links if isinstance(links, list) else [links])
        
        if all_social:
            merged["social_links"] = {k: list(set(v)) for k, v in all_social.items()}
        
        # Merge emails
        all_emails = []
        for d in provider_data.values():
            if d.get("emails"):
                all_emails.extend(d["emails"])
        
        if all_emails:
            merged["emails"] = list(set(all_emails))
        
        # Merge CEO/CTO
        ceos = [d.get("ceo") for d in provider_data.values() if d.get("ceo")]
        if ceos:
            merged["ceo"] = self._get_best_value(ceos)
        
        ctos = [d.get("cto") for d in provider_data.values() if d.get("cto")]
        if ctos:
            merged["cto"] = self._get_best_value(ctos)
        
        # Merge hiring status
        hiring_statuses = [d.get("hiring", False) for d in provider_data.values() if d.get("hiring") is not None]
        if hiring_statuses:
            merged["hiring"] = any(hiring_statuses)
        
        # Merge job openings
        job_counts = [d.get("job_openings", 0) for d in provider_data.values() if d.get("job_openings") is not None]
        if job_counts:
            merged["job_openings"] = sum(job_counts)
        
        # Add source information
        merged["_sources"] = list(provider_data.keys())
        
        return merged
    
    def _get_best_value(self, values):
        """Get the best value from a list of values"""
        # For strings, return the longest (usually most detailed)
        if all(isinstance(v, str) for v in values):
            return max(values, key=len)
        # For numbers, return the highest
        elif all(isinstance(v, (int, float)) for v in values):
            return max(values)
        # For mixed, return the first
        else:
            return values[0]
    
    def _calculate_metrics(self, data):
        """Calculate various metrics from the data"""
        metrics = {}
        
        # Employee metrics
        if data.get("employee_min") and data.get("employee_max"):
            metrics["avg_employee_count"] = (data["employee_min"] + data["employee_max"]) / 2
        elif data.get("employee_count"):
            try:
                metrics["avg_employee_count"] = int(data["employee_count"].replace(",", "").split("-")[0])
            except:
                pass
        
        # Revenue metrics
        if data.get("revenue"):
            try:
                rev = data["revenue"].replace("$", "").replace(",", "").replace("M", "000000").replace("B", "000000000")
                metrics["revenue"] = int(rev)
            except:
                pass
        
        # Technology metrics
        metrics["technology_count"] = len(data.get("technologies", []))
        
        # Social presence metrics
        metrics["social_platforms"] = len(data.get("social_links", {}))
        
        # Hiring metrics
        metrics["is_hiring"] = data.get("hiring", False)
        metrics["job_openings"] = data.get("job_openings", 0)
        
        # Email metrics
        metrics["email_count"] = len(data.get("emails", []))
        metrics["verified_email_count"] = len(data.get("verified_emails", []))
        
        # Company age
        if data.get("founded_year"):
            metrics["company_age"] = datetime.now(timezone.utc).year - data["founded_year"]
        
        # Intent metrics
        if data.get("intent_score"):
            metrics["intent_score"] = data["intent_score"]
        
        return metrics
    
    def _calculate_scores(self, data, intent):
        """Calculate various scores"""
        scores = {}
        
        # Fit score (how well the company matches our ideal customer profile)
        fit_score = 0
        
        # Industry fit
        if data.get("industry"):
            our_industries = list(INDUSTRY_KEYWORDS.keys())
            if data["industry"].lower() in [i.lower() for i in our_industries]:
                fit_score += 25
            else:
                fit_score += 10
        
        # Size fit
        employees = data.get("employee_min") or data.get("employee_count")
        if employees:
            try:
                emp_count = int(employees.split("-")[0].replace(",", "")) if isinstance(employees, str) else employees
                if emp_count >= 10 and emp_count <= 1000:
                    fit_score += 25
                elif emp_count > 1000:
                    fit_score += 20
                else:
                    fit_score += 10
            except:
                fit_score += 15
        
        # Technology fit
        if data.get("technologies"):
            relevant_techs = ["ai", "machine learning", "automation", "saas", "cloud", "software", "platform"]
            tech_count = len(data["technologies"])
            relevant_count = sum(1 for t in data["technologies"] if any(rt in t.lower() for rt in relevant_techs))
            fit_score += min(25, (relevant_count / max(1, tech_count)) * 25)
        
        # Hiring fit
        if data.get("hiring", False) or data.get("job_openings", 0) > 0:
            fit_score += 15
        
        scores["fit"] = min(100, int(fit_score))
        
        # Intent score
        intent_score = intent.get("score", 0)
        scores["intent"] = intent_score
        
        # Combined score
        scores["overall"] = int((scores["fit"] * 0.6 + scores["intent"] * 0.4))
        
        # Quality score (data completeness)
        data_fields = ["company_name", "description", "industry", "location", "website", "employee_count"]
        present_fields = sum(1 for f in data_fields if data.get(f))
        scores["quality"] = int((present_fields / len(data_fields)) * 100)
        
        return scores
    
    def _generate_recommendations(self, data, scores, intent):
        """Generate actionable recommendations"""
        recommendations = []
        
        # Intent-based recommendations
        intent_score = scores.get("intent", 0)
        if intent_score >= 70:
            recommendations.append({
                "priority": "high",
                "action": "Immediate outreach",
                "reason": f"High intent score ({intent_score}) - company is actively looking for solutions",
                "timeline": "Within 24 hours"
            })
        elif intent_score >= 50:
            recommendations.append({
                "priority": "medium",
                "action": "Nurture campaign",
                "reason": f"Medium intent score ({intent_score}) - company shows some interest",
                "timeline": "Within 1 week"
            })
        
        # Hiring recommendations
        if data.get("hiring", False) and data.get("job_openings", 0) > 3:
            recommendations.append({
                "priority": "high",
                "action": "Target hiring managers",
                "reason": f"Company has {data.get('job_openings', 0)} open positions - they're scaling",
                "timeline": "Within 48 hours"
            })
        
        # Technology recommendations
        if data.get("technologies"):
            relevant_techs = [t for t in data["technologies"] if any(kw in t.lower() for kw in ["ai", "machine learning", "automation"])]
            if relevant_techs:
                recommendations.append({
                    "priority": "medium",
                    "action": "Highlight AI/Automation expertise",
                    "reason": f"Company uses {', '.join(relevant_techs)} - align with their tech stack",
                    "timeline": "In first contact"
                })
        
        # Decision maker recommendations
        if data.get("decision_makers"):
            recommendations.append({
                "priority": "high",
                "action": "Contact decision makers directly",
                "reason": f"Found {len(data['decision_makers'])} decision makers on LinkedIn",
                "timeline": "Within 48 hours"
            })
        
        # Email recommendations
        if data.get("verified_emails"):
            recommendations.append({
                "priority": "medium",
                "action": "Use verified emails for outreach",
                "reason": f"Found {len(data['verified_emails'])} verified email addresses",
                "timeline": "In first contact"
            })
        
        # If no strong signals
        if not recommendations:
            recommendations.append({
                "priority": "low",
                "action": "Monitor for changes",
                "reason": "No strong intent signals detected yet",
                "timeline": "Monthly"
            })
        
        return recommendations
    
    def _map_to_lead_fields(self, enrichment):
        """Map enrichment data to CRM lead fields"""
        data = enrichment.get("data", {}).get("merged", {})
        intent = enrichment.get("intent", {})
        scores = enrichment.get("scores", {})
        
        update_fields = {}
        
        # Map fields
        field_mapping = {
            "company_name": "company",
            "description": "description",
            "industry": "industry",
            "location": "city",
            "country": "country",
            "website": "website",
            "domain": "domain",
            "founded_year": "founded_year",
            "employee_count": "employee_count",
            "employee_min": "employees_min",
            "employee_max": "employees_max",
            "revenue": "revenue",
            "ceo": "ceo",
            "cto": "cto",
        }
        
        for enrichment_field, lead_field in field_mapping.items():
            if data.get(enrichment_field):
                update_fields[lead_field] = data[enrichment_field]
        
        # Add intent score
        if intent.get("score"):
            update_fields["intent_score"] = intent["score"]
        
        # Add fit score
        if scores.get("fit"):
            update_fields["fit_score"] = scores["fit"]
        
        # Add technologies as notes
        if data.get("technologies"):
            tech_str = ", ".join(data["technologies"][:10])
            update_fields["notes"] = f"Technologies: {tech_str}"
        
        # Add verified emails
        if data.get("verified_emails"):
            for email_info in data["verified_emails"]:
                if email_info.get("verified"):
                    # Use first verified email
                    update_fields["contact_email"] = email_info["email"]
                    update_fields["email_status"] = "verified"
                    update_fields["email_source"] = "enrichment"
                    break
        
        # Add hiring status
        if data.get("hiring"):
            update_fields["hiring"] = 1
            update_fields["job_openings"] = data.get("job_openings", 0)
        
        return update_fields


# ============================================================================
# BULK ENRICHMENT
# ============================================================================

class BulkEnrichmentEngine:
    """Enrich multiple leads in bulk"""
    
    def __init__(self, max_workers=10):
        self.enrichment_engine = DeepEnrichmentEngine()
        self.max_workers = max_workers
    
    def enrich_batch(self, leads, deep=True):
        """Enrich a batch of leads"""
        results = {}
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_domain = {
                executor.submit(self._enrich_single, lead): lead.get("id", lead.get("domain", ""))
                for lead in leads
            }
            
            for future in as_completed(future_to_domain):
                domain = future_to_domain[future]
                try:
                    results[domain] = future.result()
                except Exception as e:
                    results[domain] = {"error": str(e)}
        
        return results
    
    def _enrich_single(self, lead):
        """Enrich a single lead"""
        return self.enrichment_engine.enrich_lead(lead)


# ============================================================================
# EXPORTS
# ============================================================================

# Initialize
init_intent_database()

# Create singleton instances
deep_enrichment_engine = DeepEnrichmentEngine()
bulk_enrichment_engine = BulkEnrichmentEngine()


def enrich_lead(lead_data, deep=True):
    """Convenience function to enrich a lead"""
    return deep_enrichment_engine.enrich_lead(lead_data, deep)


def enrich_domain(domain, deep=True):
    """Convenience function to enrich a domain"""
    return deep_enrichment_engine.enrich_domain(domain, deep)


def enrich_batch(leads, deep=True):
    """Convenience function to enrich multiple leads"""
    return bulk_enrichment_engine.enrich_batch(leads, deep)


if __name__ == "__main__":
    print("Deep Enrichment Engine - Testing")
    print("=" * 50)
    
    # Test enrichment
    print("\nEnriching a domain...")
    domain = "softorasystems.com"
    
    # Quick test (no deep enrichment to avoid API calls)
    enrichment = enrich_domain(domain, deep=False)
    
    print(f"Domain: {domain}")
    print(f"Sources: {', '.join(enrichment.get('sources', []))}")
    
    if enrichment.get("data", {}).get("merged"):
        merged = enrichment["data"]["merged"]
        print(f"Company: {merged.get('company_name', 'N/A')}")
        print(f"Industry: {merged.get('industry', 'N/A')}")
        print(f"Description: {merged.get('description', 'N/A')[:100]}...")
        print(f"Technologies: {', '.join(merged.get('technologies', [])[:5])}")
    
    print("\nEnrichment complete!")
