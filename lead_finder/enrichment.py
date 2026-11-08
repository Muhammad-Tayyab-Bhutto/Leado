"""
Lead Enrichment Providers
"""
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

from .config import (
    UA, CLEARBIT_KEY, HUNTER_KEY, LINKEDIN_TOKEN, CRUNCHBASE_KEY,
    SERVICE_KEYWORDS, domain_of
)
from .http_utils import (
    http_get, get_robots_txt, can_fetch, extract_emails, extract_phone_numbers,
    extract_social_links, clean_text, detect_technologies, extract_company_info
)
from .database import cache_enrichment, get_cached_enrichment, log_discovery


class EnrichmentProvider:
    """Base class for enrichment providers"""
    
    def __init__(self):
        self.robots_cache = {}
    
    def enrich(self, domain_or_company, data=None):
        """Enrich a lead with additional data"""
        raise NotImplementedError


class WebEnricher(EnrichmentProvider):
    """Enrich leads by scraping their website"""
    
    def __init__(self):
        super().__init__()
        self.robots_cache = {}
    
    def enrich(self, domain_or_company, data=None):
        """Enrich by scraping the company's website"""
        if not domain_or_company:
            return {}
        
        # Determine URL
        if domain_or_company.startswith("http"):
            url = domain_or_company
        elif "." in domain_or_company:
            url = f"https://{domain_or_company}"
        else:
            # It's a company name, try to find website
            search_url = f"https://www.google.com/search?q={urllib.parse.quote(domain_or_company)}+official+site"
            try:
                html = http_get(search_url, timeout=20)
                if html:
                    url_match = re.search(r'href="(/url\?[^"]+)"', html)
                    if url_match:
                        url = urllib.parse.unquote(url_match.group(1).split("=")[1])
                    else:
                        return {"error": "Could not find website"}
                else:
                    return {"error": "Could not find website"}
            except Exception:
                return {"error": "Could not find website"}
        
        # Extract domain
        domain = domain_of(url)
        if not domain:
            return {"error": "Could not determine domain"}
        
        # Check cache first
        cached = get_cached_enrichment(domain)
        if cached:
            return cached
        
        result = {}
        
        try:
            # Check robots.txt
            if not can_fetch(url, self.robots_cache):
                result["blocked_by_robots"] = True
                return result
            
            # Fetch homepage
            html = http_get(url, timeout=30)
            if not html:
                return {"error": "Could not fetch website"}
            
            # Extract information
            info = extract_company_info(html, url)
            result.update(info)
            
            # Try to find contact page
            contact_url = urllib.parse.urljoin(url, "/contact")
            contact_html = http_get(contact_url, timeout=20)
            if contact_html:
                contact_info = extract_company_info(contact_html, contact_url)
                if "emails" in contact_info:
                    result.setdefault("emails", []).extend(contact_info.get("emails", []))
                if "phones" in contact_info:
                    result.setdefault("phones", []).extend(contact_info.get("phones", []))
                if "address" in contact_info and not result.get("address"):
                    result["address"] = contact_info["address"]
            
            # Try to find about page
            about_url = urllib.parse.urljoin(url, "/about")
            about_html = http_get(about_url, timeout=20)
            if about_html:
                about_info = extract_company_info(about_html, about_url)
                if "description" in about_info and not result.get("description"):
                    result["description"] = about_info["description"]
                if "company" in about_info and not result.get("company"):
                    result["company"] = about_info["company"]
            
            # Cache result
            cache_enrichment(domain, result, "web_scrape")
            
            return result
            
        except Exception as e:
            return {"error": str(e)}


class ClearbitEnricher(EnrichmentProvider):
    """Enrich leads using Clearbit API"""
    
    def enrich(self, domain_or_company, data=None):
        """Enrich using Clearbit"""
        if not CLEARBIT_KEY:
            return {"error": "Clearbit API key not configured"}
        
        if not domain_or_company:
            return {}
        
        # Clearbit works best with domains
        if "." not in domain_or_company:
            return {"error": "Clearbit requires a domain"}
        
        domain = domain_of(domain_or_company)
        
        # Check cache
        cached = get_cached_enrichment(domain)
        if cached:
            return cached
        
        try:
            url = f"https://company.clearbit.com/v2/companies/find?domain={domain}"
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {CLEARBIT_KEY}",
                    "User-Agent": UA
                }
            )
            
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.load(r)
                
                # Cache result
                expires = datetime.now(timezone.utc) + timedelta(days=30)
                cache_enrichment(domain, result, "clearbit")
                
                return result
                
        except Exception as e:
            return {"error": str(e)}


class HunterEnricher(EnrichmentProvider):
    """Enrich leads using Hunter.io API for email finding"""
    
    def enrich(self, domain_or_company, data=None):
        """Find emails using Hunter.io"""
        if not HUNTER_KEY:
            return {"error": "Hunter API key not configured"}
        
        if not domain_or_company:
            return {}
        
        domain = domain_of(domain_or_company) if "." in domain_or_company else None
        if not domain:
            return {"error": "Hunter requires a domain"}
        
        try:
            url = f"https://api.hunter.io/v2/domain-search?domain={domain}&api_key={HUNTER_KEY}"
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.load(r)
                
                # Cache result
                cache_enrichment(domain, result, "hunter")
                
                return result
                
        except Exception as e:
            return {"error": str(e)}


class CrunchbaseEnricher(EnrichmentProvider):
    """Enrich leads using Crunchbase API"""
    
    def enrich(self, domain_or_company, data=None):
        """Enrich using Crunchbase"""
        if not CRUNCHBASE_KEY:
            return {"error": "Crunchbase API key not configured"}
        
        if not domain_or_company:
            return {}
        
        try:
            search_url = f"https://api.crunchbase.com/graphql/v4"
            query = """
            query {
                organizations(query: {field: "name", operator: CONTAINS, value: "%s"}, first: 5) {
                    edges {
                        node {
                            name
                            website
                            shortDescription
                            industries {
                                name
                            }
                            locations {
                                name
                            }
                            fundingRounds {
                                announcedOn
                                raisedAmountUsd
                            }
                            employeeCountRange {
                                minValue
                                maxValue
                            }
                        }
                    }
                }
            }
            """ % domain_or_company
            
            req = urllib.request.Request(
                search_url,
                data=json.dumps({"query": query}).encode(),
                headers={
                    "X-Cb-User-Key": CRUNCHBASE_KEY,
                    "Content-Type": "application/json",
                    "User-Agent": UA
                }
            )
            
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.load(r)
                cache_enrichment(domain_of(domain_or_company) or domain_or_company, result, "crunchbase")
                return result
                
        except Exception as e:
            return {"error": str(e)}


class LinkedInEnricher(EnrichmentProvider):
    """Enrich leads using LinkedIn API"""
    
    def enrich(self, domain_or_company, data=None):
        """Enrich using LinkedIn"""
        if not LINKEDIN_TOKEN:
            return {"error": "LinkedIn API token not configured"}
        
        if not domain_or_company:
            return {}
        
        try:
            search_url = f"https://api.linkedin.com/v2/organizationalEntityShareStatistics?q=organizationalEntity&organizationalEntity=urn:li:organizationCompanyNameSearch:({urllib.parse.quote(domain_or_company)})&count=5"
            
            req = urllib.request.Request(
                search_url,
                headers={
                    "Authorization": f"Bearer {LINKEDIN_TOKEN}",
                    "User-Agent": UA
                }
            )
            
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.load(r)
                cache_enrichment(domain_of(domain_or_company) or domain_or_company, result, "linkedin")
                return result
                
        except Exception as e:
            return {"error": str(e)}


# Registry of all enrichers
ALL_ENRICHERS = [
    WebEnricher(),
    ClearbitEnricher(),
    HunterEnricher(),
    CrunchbaseEnricher(),
    LinkedInEnricher()
]


# Import for compatibility
import re
