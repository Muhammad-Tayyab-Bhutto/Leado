"""
Advanced LinkedIn Scraper & Analyzer - Replaces Apollo.io and similar tools

This module provides:
1. LinkedIn profile scraping (without API)
2. Company page extraction
3. Employee discovery
4. Decision maker identification
5. Company analysis (growth, tech stack, etc.)
6. Intent signal detection
7. Relationship mapping
8. Bulk LinkedIn operations

NOTE: This uses web scraping which may violate LinkedIn's ToS.
Use responsibly and with proper rate limiting.
"""
import re
import json
import time
import csv
import io
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin, quote, unquote

from .config import UA, RATE_LIMIT_DELAY, DECISION_MAKER_TITLES, domain_of
from .http_utils import http_get, clean_text, extract_emails, extract_links
from .database import log_discovery, cache_enrichment, get_cached_enrichment


# ============================================================================
# LINKEDIN URL UTILITIES
# ============================================================================

class LinkedInURL:
    """Utilities for working with LinkedIn URLs"""
    
    @staticmethod
    def normalize_url(url):
        """Normalize LinkedIn URL"""
        if not url:
            return None
        
        url = url.strip().rstrip("/")
        
        # Add https if missing
        if not url.startswith("http"):
            url = f"https://{url}"
        
        # Remove www
        url = url.replace("https://www.linkedin.com", "https://linkedin.com")
        
        # Handle redirect URLs
        if "/in/" in url or "/company/" in url or "/pub/" in url:
            parsed = urlparse(url)
            path = parsed.path
            
            # Extract clean path
            if path.startswith("/in/"):
                clean_path = f"/in/{path.split('/in/')[1].split('/')[0]}"
            elif path.startswith("/company/"):
                clean_path = f"/company/{path.split('/company/')[1].split('/')[0]}"
            elif path.startswith("/pub/"):
                clean_path = f"/pub/{path.split('/pub/')[1].split('/')[0]}"
            else:
                clean_path = path
            
            return f"https://linkedin.com{clean_path}"
        
        return url
    
    @staticmethod
    def is_profile_url(url):
        """Check if URL is a LinkedIn profile"""
        if not url:
            return False
        
        parsed = urlparse(url)
        return "linkedin.com" in parsed.netloc and any(
            path in parsed.path for path in ["/in/", "/pub/", "/profile/"]
        )
    
    @staticmethod
    def is_company_url(url):
        """Check if URL is a LinkedIn company page"""
        if not url:
            return False
        
        parsed = urlparse(url)
        return "linkedin.com" in parsed.netloc and "/company/" in parsed.path
    
    @staticmethod
    def is_job_url(url):
        """Check if URL is a LinkedIn job posting"""
        if not url:
            return False
        
        parsed = urlparse(url)
        return "linkedin.com" in parsed.netloc and "/jobs/" in parsed.path
    
    @staticmethod
    def get_profile_id(url):
        """Extract profile ID from LinkedIn URL"""
        if not url:
            return None
        
        parsed = urlparse(url)
        path = parsed.path
        
        # Try to extract ID
        for prefix in ["/in/", "/pub/", "/profile/view?id="]:
            if prefix in path:
                parts = path.split(prefix)
                if len(parts) > 1:
                    id_part = parts[1].split("/")[0].split("&")[0]
                    if id_part:
                        return id_part
        
        return None
    
    @staticmethod
    def get_company_id(url):
        """Extract company ID from LinkedIn URL"""
        if not url:
            return None
        
        parsed = urlparse(url)
        path = parsed.path
        
        if "/company/" in path:
            parts = path.split("/company/")
            if len(parts) > 1:
                id_part = parts[1].split("/")[0]
                if id_part and not id_part.startswith("view/id:"):
                    return id_part
        
        # Handle numeric IDs
        if "company/" in url:
            match = re.search(r'/company/\d+', url)
            if match:
                return match.group().replace("/company/", "")
        
        return None


# ============================================================================
# LINKEDIN SEARCH
# ============================================================================

class LinkedInSearcher:
    """Search LinkedIn for companies and people"""
    
    def __init__(self):
        self.session_cookies = None
        self.last_search_time = None
        self.search_delay = 5.0  # Be very polite with LinkedIn
    
    def set_cookies(self, cookies):
        """Set session cookies for authenticated searches"""
        self.session_cookies = cookies
    
    def search_people(self, query, company=None, title=None, location=None, limit=20):
        """Search for people on LinkedIn"""
        # Build search URL
        params = [f"keywords={quote(query)}"]
        
        if company:
            params.append(f"company={quote(company)}")
        if title:
            params.append(f"title={quote(title)}")
        if location:
            params.append(f"location={quote(location)}")
        
        search_url = f"https://www.linkedin.com/search/results/people/?{'&'.join(params)}"
        
        return self._search(search_url, "people", limit)
    
    def search_companies(self, query, location=None, industry=None, size=None, limit=20):
        """Search for companies on LinkedIn"""
        params = [f"keywords={quote(query)}"]
        
        if location:
            params.append(f"location={quote(location)}")
        if industry:
            params.append(f"industry={quote(industry)}")
        if size:
            params.append(f"companySize={quote(size)}")
        
        search_url = f"https://www.linkedin.com/search/results/companies/?{'&'.join(params)}"
        
        return self._search(search_url, "companies", limit)
    
    def search_content(self, query, company=None, author=None, limit=20):
        """Search for content/posts on LinkedIn"""
        params = [f"keywords={quote(query)}"]
        
        if company:
            params.append(f"company={quote(company)}")
        if author:
            params.append(f"author={quote(author)}")
        
        search_url = f"https://www.linkedin.com/search/results/content/?{'&'.join(params)}"
        
        return self._search(search_url, "content", limit)
    
    def _search(self, url, search_type, limit):
        """Internal search method"""
        # Rate limiting
        if self.last_search_time:
            elapsed = time.time() - self.last_search_time
            if elapsed < self.search_delay:
                time.sleep(self.search_delay - elapsed)
        
        self.last_search_time = time.time()
        
        try:
            html = http_get(url, timeout=30)
            if not html:
                return []
            
            if search_type == "people":
                return self._parse_people_results(html)
            elif search_type == "companies":
                return self._parse_company_results(html)
            elif search_type == "content":
                return self._parse_content_results(html)
            else:
                return []
                
        except Exception as e:
            log_discovery("linkedin_search", url, "error", details=str(e))
            return []
    
    def _parse_people_results(self, html):
        """Parse people search results"""
        results = []
        
        # Look for result containers
        # LinkedIn changes its structure frequently
        patterns = [
            # New LinkedIn design
            r'<li[^>]*class="[^"]*reusable-search__result-container[^"]*"[^>]*>(.*?)</li>',
            r'<li[^>]*class="[^"]*search-result[^"]*"[^>]*>(.*?)</li>',
            # Old design
            r'<li[^>]*class="[^"]*profile-card[^"]*"[^>]*>(.*?)</li>',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, html, re.DOTALL):
                result_html = match.group(1)
                
                # Extract name
                name_match = re.search(r'<span[^>]*class="[^"]*name[^"]*"[^>]*>([^<]+)</span>', result_html, re.IGNORECASE)
                if not name_match:
                    name_match = re.search(r'aria-label="([^"]+)"', result_html)
                
                name = clean_text(name_match.group(1)) if name_match else "Unknown"
                
                # Extract title
                title_match = re.search(r'<span[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</span>', result_html, re.IGNORECASE)
                if not title_match:
                    title_match = re.search(r'<p[^>]*class="[^"]*subtitle[^"]*"[^>]*>([^<]+)</p>', result_html, re.IGNORECASE)
                
                title = clean_text(title_match.group(1)) if title_match else ""
                
                # Extract company
                company_match = re.search(r'<span[^>]*class="[^"]*company[^"]*"[^>]*>([^<]+)</span>', result_html, re.IGNORECASE)
                company = clean_text(company_match.group(1)) if company_match else ""
                
                # Extract profile URL
                url_match = re.search(r'href="(/in/[^"]+)"', result_html)
                if url_match:
                    profile_url = f"https://www.linkedin.com{url_match.group(1)}"
                else:
                    url_match = re.search(r'href="(/pub/[^"]+)"', result_html)
                    profile_url = f"https://www.linkedin.com{url_match.group(1)}" if url_match else None
                
                # Extract location
                location_match = re.search(r'<span[^>]*class="[^"]*location[^"]*"[^>]*>([^<]+)</span>', result_html, re.IGNORECASE)
                location = clean_text(location_match.group(1)) if location_match else ""
                
                # Extract photo
                photo_match = re.search(r'<img[^>]*src="([^"]+)"', result_html)
                photo = photo_match.group(1) if photo_match else None
                
                results.append({
                    "type": "person",
                    "name": name,
                    "title": title,
                    "company": company,
                    "location": location,
                    "profile_url": profile_url,
                    "photo": photo,
                    "source": "linkedin_search"
                })
        
        return results
    
    def _parse_company_results(self, html):
        """Parse company search results"""
        results = []
        
        patterns = [
            r'<li[^>]*class="[^"]*reusable-search__result-container[^"]*"[^>]*>(.*?)</li>',
            r'<li[^>]*class="[^"]*search-result[^"]*"[^>]*>(.*?)</li>',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, html, re.DOTALL):
                result_html = match.group(1)
                
                # Extract company name
                name_match = re.search(r'<span[^>]*class="[^"]*name[^"]*"[^>]*>([^<]+)</span>', result_html, re.IGNORECASE)
                if not name_match:
                    name_match = re.search(r'aria-label="([^"]+)"', result_html)
                
                name = clean_text(name_match.group(1)) if name_match else "Unknown"
                
                # Extract tagline
                tagline_match = re.search(r'<p[^>]*class="[^"]*tagline[^"]*"[^>]*>([^<]+)</p>', result_html, re.IGNORECASE)
                tagline = clean_text(tagline_match.group(1)) if tagline_match else ""
                
                # Extract company URL
                url_match = re.search(r'href="(/company/[^"]+)"', result_html)
                if url_match:
                    company_url = f"https://www.linkedin.com{url_match.group(1)}"
                else:
                    company_url = None
                
                # Extract logo
                logo_match = re.search(r'<img[^>]*src="([^"]+)"', result_html)
                logo = logo_match.group(1) if logo_match else None
                
                # Extract employees
                employees_match = re.search(r'(\d+[,\d]*)\s+(?:employees?|followers?)', result_html, re.IGNORECASE)
                employees = employees_match.group(1) if employees_match else None
                
                # Extract industry
                industry_match = re.search(r'<span[^>]*class="[^"]*industry[^"]*"[^>]*>([^<]+)</span>', result_html, re.IGNORECASE)
                industry = clean_text(industry_match.group(1)) if industry_match else ""
                
                results.append({
                    "type": "company",
                    "name": name,
                    "tagline": tagline,
                    "company_url": company_url,
                    "logo": logo,
                    "employees": employees,
                    "industry": industry,
                    "source": "linkedin_search"
                })
        
        return results
    
    def _parse_content_results(self, html):
        """Parse content search results"""
        results = []
        
        patterns = [
            r'<div[^>]*class="[^"]*feed-shared-update-v2[^"]*"[^>]*>(.*?)</div>',
            r'<article[^>]*class="[^"]*post[^"]*"[^>]*>(.*?)</article>',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, html, re.DOTALL):
                result_html = match.group(1)
                
                # Extract author
                author_match = re.search(r'<span[^>]*class="[^"]*author[^"]*"[^>]*>([^<]+)</span>', result_html, re.IGNORECASE)
                author = clean_text(author_match.group(1)) if author_match else ""
                
                # Extract content
                content_match = re.search(r'<div[^>]*class="[^"]*content[^"]*"[^>]*>([^<]+)</div>', result_html, re.IGNORECASE)
                content = clean_text(content_match.group(1)) if content_match else ""
                
                # Extract post URL
                url_match = re.search(r'href="(/feed/update/[^"]+)"', result_html)
                post_url = f"https://www.linkedin.com{url_match.group(1)}" if url_match else None
                
                results.append({
                    "type": "content",
                    "author": author,
                    "content": content,
                    "post_url": post_url,
                    "source": "linkedin_search"
                })
        
        return results


# ============================================================================
# PROFILE SCRAPER
# ============================================================================

class LinkedInProfileScraper:
    """Scrape LinkedIn profile pages"""
    
    def __init__(self):
        self.url_utils = LinkedInURL()
        self.rate_delay = RATE_LIMIT_DELAY * 2  # Be extra polite
        self.last_request_time = None
    
    def scrape_profile(self, profile_url, deep=True):
        """Scrape a LinkedIn profile"""
        profile_url = self.url_utils.normalize_url(profile_url)
        if not profile_url:
            return {"error": "Invalid profile URL"}
        
        # Rate limiting
        if self.last_request_time:
            elapsed = time.time() - self.last_request_time
            if elapsed < self.rate_delay:
                time.sleep(self.rate_delay - elapsed)
        
        self.last_request_time = time.time()
        
        # Check cache
        profile_id = self.url_utils.get_profile_id(profile_url)
        if profile_id:
            cached = get_cached_enrichment(f"linkedin_profile_{profile_id}")
            if cached:
                return {**cached, "cached": True}
        
        try:
            html = http_get(profile_url, timeout=30)
            if not html:
                return {"error": "Could not fetch profile"}
            
            profile_data = self._parse_profile_html(html, profile_url)
            
            if deep and profile_data.get("company"):
                # Also scrape company page
                company_url = self._find_company_url(html, profile_data.get("company"))
                if company_url:
                    company_data = LinkedInCompanyScraper().scrape_company(company_url, deep=False)
                    profile_data["company_data"] = company_data
            
            # Cache result
            if profile_id:
                cache_enrichment(f"linkedin_profile_{profile_id}", profile_data, "linkedin_scraper")
            
            return {**profile_data, "cached": False}
            
        except Exception as e:
            log_discovery("linkedin_profile", profile_url, "error", details=str(e))
            return {"error": str(e), "profile_url": profile_url}
    
    def _parse_profile_html(self, html, url):
        """Parse profile HTML"""
        data = {
            "type": "profile",
            "profile_url": url,
            "name": None,
            "headline": None,
            "current_title": None,
            "current_company": None,
            "company": None,
            "location": None,
            "industry": None,
            "summary": None,
            "experience": [],
            "education": [],
            "skills": [],
            "endorsements": 0,
            "recommendations": 0,
            "connections": 0,
            "photo": None,
            "emails": [],
            "phone": None,
            "websites": [],
            "social_links": {},
            "is_decision_maker": False,
            "decision_maker_score": 0,
            "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
        }
        
        text = clean_text(html)
        
        # Extract name
        name_match = re.search(r'<h1[^>]*class="[^"]*(top-card|profile-topcard)[^"]*"[^>]*>([^<]+)</h1>', html, re.IGNORECASE)
        if not name_match:
            name_match = re.search(r'<title>([^<]+)</title>', html)
        
        if name_match:
            data["name"] = clean_text(name_match.group(1)).strip()
        
        # Extract headline
        headline_match = re.search(r'<div[^>]*class="[^"]*headline[^"]*"[^>]*>([^<]+)</div>', html, re.IGNORECASE)
        if headline_match:
            data["headline"] = clean_text(headline_match.group(1)).strip()
        
        # Extract current position
        current_match = re.search(r'<div[^>]*class="[^"]*(current-position|experience-item)[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL | re.IGNORECASE)
        if current_match:
            current_html = current_match.group(1)
            title_match = re.search(r'<span[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</span>', current_html, re.IGNORECASE)
            company_match = re.search(r'<span[^>]*class="[^"]*company[^"]*"[^>]*>([^<]+)</span>', current_html, re.IGNORECASE)
            
            if title_match:
                data["current_title"] = clean_text(title_match.group(1)).strip()
            if company_match:
                company = clean_text(company_match.group(1)).strip()
                data["current_company"] = company
                data["company"] = company
        
        # Extract location
        location_match = re.search(r'<li[^>]*class="[^"]*location[^"]*"[^>]*>([^<]+)</li>', html, re.IGNORECASE)
        if not location_match:
            location_match = re.search(r'<span[^>]*class="[^"]*locality[^"]*"[^>]*>([^<]+)</span>', html, re.IGNORECASE)
        
        if location_match:
            data["location"] = clean_text(location_match.group(1)).strip()
        
        # Extract industry
        industry_match = re.search(r'<li[^>]*class="[^"]*industry[^"]*"[^>]*>([^<]+)</li>', html, re.IGNORECASE)
        if industry_match:
            data["industry"] = clean_text(industry_match.group(1)).strip()
        
        # Extract summary
        summary_match = re.search(r'<section[^>]*class="[^"]*summary[^"]*"[^>]*>(.*?)</section>', html, re.DOTALL | re.IGNORECASE)
        if not summary_match:
            summary_match = re.search(r'<p[^>]*class="[^"]*about-summary[^"]*"[^>]*>([^<]+)</p>', html, re.IGNORECASE)
        
        if summary_match:
            data["summary"] = clean_text(summary_match.group(1)).strip()
        
        # Extract experience
        data["experience"] = self._extract_experience(html)
        
        # Extract education
        data["education"] = self._extract_education(html)
        
        # Extract skills
        data["skills"] = self._extract_skills(html)
        
        # Extract endorsements
        endorsement_match = re.search(r'(\d+[,\d]*)\s+endorsements?', html, re.IGNORECASE)
        if endorsement_match:
            data["endorsements"] = int(endorsement_match.group(1).replace(",", ""))
        
        # Extract recommendations
        recommendation_match = re.search(r'(\d+[,\d]*)\s+recommendations?', html, re.IGNORECASE)
        if recommendation_match:
            data["recommendations"] = int(recommendation_match.group(1).replace(",", ""))
        
        # Extract connections
        connection_match = re.search(r'(\d+[,\d]*)\s+connections?', html, re.IGNORECASE)
        if connection_match:
            data["connections"] = int(connection_match.group(1).replace(",", ""))
        
        # Extract photo
        photo_match = re.search(r'<img[^>]*class="[^"]*(profile-picture|photo)[^"]*"[^>]*src="([^"]+)"', html, re.IGNORECASE)
        if photo_match:
            data["photo"] = photo_match.group(2)
        
        # Extract contact info
        emails = extract_emails(html)
        if emails:
            data["emails"] = emails
        
        phone_match = re.search(r'\+?[\d\s\-\(\)]{7,}\d', html)
        if phone_match:
            data["phone"] = phone_match.group()
        
        # Extract websites
        data["websites"] = self._extract_websites(html)
        
        # Extract social links
        data["social_links"] = extract_social_links(html)
        
        # Determine if decision maker
        data["is_decision_maker"] = self._is_decision_maker(data)
        data["decision_maker_score"] = self._calculate_decision_maker_score(data)
        
        return data
    
    def _extract_experience(self, html):
        """Extract experience from profile"""
        experience = []
        
        # Look for experience section
        patterns = [
            r'<section[^>]*class="[^"]*experience[^"]*"[^>]*>(.*?)</section>',
            r'<div[^>]*id="[^"]*experience[^"]*"[^>]*>(.*?)</div>',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            if match:
                exp_section = match.group(1)
                
                # Extract individual experience items
                item_patterns = [
                    r'<li[^>]*class="[^"]*position[^"]*"[^>]*>(.*?)</li>',
                    r'<div[^>]*class="[^"]*experience-item[^"]*"[^>]*>(.*?)</div>',
                ]
                
                for item_pattern in item_patterns:
                    for item_match in re.finditer(item_pattern, exp_section, re.DOTALL | re.IGNORECASE):
                        item_html = item_match.group(1)
                        
                        title_match = re.search(r'<span[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</span>', item_html, re.IGNORECASE)
                        company_match = re.search(r'<span[^>]*class="[^"]*company[^"]*"[^>]*>([^<]+)</span>', item_html, re.IGNORECASE)
                        date_match = re.search(r'<span[^>]*class="[^"]*date[^"]*"[^>]*>([^<]+)</span>', item_html, re.IGNORECASE)
                        
                        title = clean_text(title_match.group(1)) if title_match else ""
                        company = clean_text(company_match.group(1)) if company_match else ""
                        dates = clean_text(date_match.group(1)) if date_match else ""
                        
                        if title or company:
                            experience.append({
                                "title": title,
                                "company": company,
                                "dates": dates
                            })
                        
                        if len(experience) >= 20:  # Limit
                            break
                
                if experience:
                    break
        
        return experience
    
    def _extract_education(self, html):
        """Extract education from profile"""
        education = []
        
        patterns = [
            r'<section[^>]*class="[^"]*education[^"]*"[^>]*>(.*?)</section>',
            r'<div[^>]*id="[^"]*education[^"]*"[^>]*>(.*?)</div>',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            if match:
                edu_section = match.group(1)
                
                item_patterns = [
                    r'<li[^>]*class="[^"]*school[^"]*"[^>]*>(.*?)</li>',
                    r'<div[^>]*class="[^"]*education-item[^"]*"[^>]*>(.*?)</div>',
                ]
                
                for item_pattern in item_patterns:
                    for item_match in re.finditer(item_pattern, edu_section, re.DOTALL | re.IGNORECASE):
                        item_html = item_match.group(1)
                        
                        school_match = re.search(r'<span[^>]*class="[^"]*school[^"]*"[^>]*>([^<]+)</span>', item_html, re.IGNORECASE)
                        degree_match = re.search(r'<span[^>]*class="[^"]*degree[^"]*"[^>]*>([^<]+)</span>', item_html, re.IGNORECASE)
                        
                        school = clean_text(school_match.group(1)) if school_match else ""
                        degree = clean_text(degree_match.group(1)) if degree_match else ""
                        
                        if school:
                            education.append({
                                "school": school,
                                "degree": degree
                            })
                        
                        if len(education) >= 10:
                            break
                
                if education:
                    break
        
        return education
    
    def _extract_skills(self, html):
        """Extract skills from profile"""
        skills = []
        
        patterns = [
            r'<section[^>]*class="[^"]*skills[^"]*"[^>]*>(.*?)</section>',
            r'<div[^>]*id="[^"]*skills[^"]*"[^>]*>(.*?)</div>',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            if match:
                skills_section = match.group(1)
                
                # Extract skill names
                for skill_match in re.finditer(r'<span[^>]*class="[^"]*skill[^"]*"[^>]*>([^<]+)</span>', skills_section, re.IGNORECASE):
                    skill = clean_text(skill_match.group(1)).strip()
                    if skill and skill not in skills:
                        skills.append(skill)
                
                if skills:
                    break
        
        return skills
    
    def _extract_websites(self, html):
        """Extract websites from profile"""
        websites = []
        
        # Look for website links
        for match in re.finditer(r'href="(https?://[^"<>]+)"', html):
            url = match.group(1)
            if any(domain in url for domain in ["linkedin.com", "facebook.com", "twitter.com", "instagram.com"]):
                continue
            if url not in websites:
                websites.append(url)
        
        return websites[:10]  # Limit
    
    def _find_company_url(self, html, company_name):
        """Find company URL from profile"""
        if not company_name:
            return None
        
        # Look for company link
        for match in re.finditer(r'href="(/company/[^"]+)"', html):
            url = match.group(1)
            if company_name.lower() in url.lower():
                return f"https://www.linkedin.com{url}"
        
        return None
    
    def _is_decision_maker(self, profile_data):
        """Check if profile is a decision maker"""
        title = profile_data.get("current_title", "").lower()
        headline = profile_data.get("headline", "").lower()
        
        # Check for decision maker titles
        for dm_title in DECISION_MAKER_TITLES:
            if dm_title.lower() in title or dm_title.lower() in headline:
                return True
        
        # Check for founder, executive keywords
        keywords = ["founder", "ceo", "cto", "cpo", "cmo", "coo", "vp", "director", "head of", "manager"]
        for kw in keywords:
            if kw in title or kw in headline:
                return True
        
        return False
    
    def _calculate_decision_maker_score(self, profile_data):
        """Calculate decision maker score (0-100)"""
        score = 0
        title = profile_data.get("current_title", "").lower()
        headline = profile_data.get("headline", "").lower()
        connections = profile_data.get("connections", 0)
        
        # Title importance
        title_weights = {
            "founder": 40,
            "co-founder": 40,
            "ceo": 35,
            "cto": 35,
            "cpo": 30,
            "cmo": 30,
            "coo": 30,
            "vp": 25,
            "director": 20,
            "head of": 20,
            "manager": 15,
            "lead": 15,
            "senior": 10,
            "principal": 10,
        }
        
        for keyword, weight in title_weights.items():
            if keyword in title or keyword in headline:
                score += weight
                break  # Only count highest title
        
        # Connection count (more connections = more influence)
        if connections >= 1000:
            score += 15
        elif connections >= 500:
            score += 10
        elif connections >= 100:
            score += 5
        
        # Industry keywords in headline
        industry_keywords = ["technology", "software", "digital", "innovation", "growth", "strategy"]
        for kw in industry_keywords:
            if kw in headline:
                score += 5
        
        # Has company data
        if profile_data.get("company"):
            score += 10
        
        # Cap at 100
        return min(100, score)


# ============================================================================
# COMPANY SCRAPER
# ============================================================================

class LinkedInCompanyScraper:
    """Scrape LinkedIn company pages"""
    
    def __init__(self):
        self.url_utils = LinkedInURL()
        self.rate_delay = RATE_LIMIT_DELAY * 2
        self.last_request_time = None
    
    def scrape_company(self, company_url, deep=True):
        """Scrape a LinkedIn company page"""
        company_url = self.url_utils.normalize_url(company_url)
        if not company_url:
            return {"error": "Invalid company URL"}
        
        # Rate limiting
        if self.last_request_time:
            elapsed = time.time() - self.last_request_time
            if elapsed < self.rate_delay:
                time.sleep(self.rate_delay - elapsed)
        
        self.last_request_time = time.time()
        
        # Check cache
        company_id = self.url_utils.get_company_id(company_url)
        if company_id:
            cached = get_cached_enrichment(f"linkedin_company_{company_id}")
            if cached:
                return {**cached, "cached": True}
        
        try:
            html = http_get(company_url, timeout=30)
            if not html:
                return {"error": "Could not fetch company page"}
            
            company_data = self._parse_company_html(html, company_url)
            
            # Cache result
            if company_id:
                cache_enrichment(f"linkedin_company_{company_id}", company_data, "linkedin_scraper")
            
            return {**company_data, "cached": False}
            
        except Exception as e:
            log_discovery("linkedin_company", company_url, "error", details=str(e))
            return {"error": str(e), "company_url": company_url}
    
    def _parse_company_html(self, html, url):
        """Parse company HTML"""
        data = {
            "type": "company",
            "company_url": url,
            "name": None,
            "tagline": None,
            "description": None,
            "industry": None,
            "location": None,
            "country": None,
            "city": None,
            "founded_year": None,
            "company_size": None,
            "employee_count": None,
            "employee_min": None,
            "employee_max": None,
            "website": None,
            "domain": None,
            "specialties": [],
            "company_type": None,
            "founding_year": None,
            "ceo": None,
            "ceo_profile": None,
            "cto": None,
            "cto_profile": None,
            "revenue": None,
            "parent_company": None,
            "subsidiaries": [],
            "competitors": [],
            "similar_companies": [],
            "logo": None,
            "banner": None,
            "employee_count_range": None,
            "followers_count": 0,
            "employees_on_linkedin": 0,
            "job_openings": 0,
            "hiring": False,
            "recent_jobs": [],
            "social_links": {},
            "technologies": [],
            "products": [],
            "intent_signals": [],
            "growth_score": 0,
            "financials": {},
            "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
        }
        
        text = clean_text(html)
        
        # Extract name
        name_match = re.search(r'<h1[^>]*class="[^"]*(top-card|company-topcard)[^"]*"[^>]*>([^<]+)</h1>', html, re.IGNORECASE)
        if not name_match:
            name_match = re.search(r'<title>([^|<]+)', html)
        
        if name_match:
            data["name"] = clean_text(name_match.group(1)).strip()
        
        # Extract tagline
        tagline_match = re.search(r'<p[^>]*class="[^"]*tagline[^"]*"[^>]*>([^<]+)</p>', html, re.IGNORECASE)
        if tagline_match:
            data["tagline"] = clean_text(tagline_match.group(1)).strip()
        
        # Extract description
        desc_match = re.search(r'<section[^>]*class="[^"]*description[^"]*"[^>]*>(.*?)</section>', html, re.DOTALL | re.IGNORECASE)
        if not desc_match:
            desc_match = re.search(r'<p[^>]*class="[^"]*about-us[^"]*"[^>]*>([^<]+)</p>', html, re.IGNORECASE)
        
        if desc_match:
            data["description"] = clean_text(desc_match.group(1)).strip()
        
        # Extract industry
        industry_match = re.search(r'<li[^>]*class="[^"]*industry[^"]*"[^>]*>([^<]+)</li>', html, re.IGNORECASE)
        if not industry_match:
            industry_match = re.search(r'<span[^>]*class="[^"]*industry[^"]*"[^>]*>([^<]+)</span>', html, re.IGNORECASE)
        
        if industry_match:
            data["industry"] = clean_text(industry_match.group(1)).strip()
        
        # Extract location
        location_match = re.search(r'<li[^>]*class="[^"]*location[^"]*"[^>]*>([^<]+)</li>', html, re.IGNORECASE)
        if location_match:
            location = clean_text(location_match.group(1)).strip()
            data["location"] = location
            
            # Split into city and country
            parts = location.split(",")
            if len(parts) >= 2:
                data["city"] = parts[0].strip()
                data["country"] = parts[-1].strip()
            elif len(parts) == 1:
                data["city"] = parts[0].strip()
        
        # Extract company size
        size_match = re.search(r'<li[^>]*class="[^"]*company-size[^"]*"[^>]*>([^<]+)</li>', html, re.IGNORECASE)
        if not size_match:
            size_match = re.search(r'(\d+[,\d]*)\s+followers?', html, re.IGNORECASE)
        
        if size_match:
            size_text = clean_text(size_match.group(1)).strip()
            data["company_size"] = size_text
            data["employee_count_range"] = size_text
            
            # Try to extract numeric range
            if "-" in size_text:
                parts = size_text.split("-")
                if len(parts) == 2:
                    try:
                        data["employee_min"] = int(parts[0].replace(",", ""))
                        data["employee_max"] = int(parts[1].replace(",", ""))
                    except:
                        pass
        
        # Extract website
        website_match = re.search(r'href="(https?://[^"<>]+)"[^>]*class="[^"]*website[^"]*"', html, re.IGNORECASE)
        if not website_match:
            website_match = re.search(r'<dt[^>]*>Website[^<]*</dt>.*?<dd[^>]*>([^<]+)</dd>', html, re.DOTALL | re.IGNORECASE)
        
        if website_match:
            website = website_match.group(1).strip()
            data["website"] = website
            data["domain"] = domain_of(website)
        
        # Extract founded year
        founded_match = re.search(r'Founded\s+(\d{4})', html, re.IGNORECASE)
        if founded_match:
            data["founded_year"] = int(founded_match.group(1))
            data["founding_year"] = int(founded_match.group(1))
        
        # Extract CEO
        ceo_match = re.search(r'CEO[^:]*:?\s*<[^>]+>([^<]+)</[^>]+>', html, re.IGNORECASE)
        if not ceo_match:
            ceo_match = re.search(r'<span[^>]*class="[^"]*ceo[^"]*"[^>]*>([^<]+)</span>', html, re.IGNORECASE)
        
        if ceo_match:
            data["ceo"] = clean_text(ceo_match.group(1)).strip()
        
        # Extract CTO
        cto_match = re.search(r'CTO[^:]*:?\s*<[^>]+>([^<]+)</[^>]+>', html, re.IGNORECASE)
        if cto_match:
            data["cto"] = clean_text(cto_match.group(1)).strip()
        
        # Extract specialties
        specialties_match = re.search(r'<section[^>]*class="[^"]*specialties[^"]*"[^>]*>(.*?)</section>', html, re.DOTALL | re.IGNORECASE)
        if specialties_match:
            for li_match in re.finditer(r'<li[^>]*>([^<]+)</li>', specialties_match.group(1)):
                specialty = clean_text(li_match.group(1)).strip()
                if specialty:
                    data["specialties"].append(specialty)
        
        # Extract company type
        type_match = re.search(r'<li[^>]*class="[^"]*company-type[^"]*"[^>]*>([^<]+)</li>', html, re.IGNORECASE)
        if type_match:
            data["company_type"] = clean_text(type_match.group(1)).strip()
        
        # Extract logo
        logo_match = re.search(r'<img[^>]*class="[^"]*logo[^"]*"[^>]*src="([^"]+)"', html, re.IGNORECASE)
        if logo_match:
            data["logo"] = logo_match.group(1)
        
        # Extract banner
        banner_match = re.search(r'<img[^>]*class="[^"]*banner[^"]*"[^>]*src="([^"]+)"', html, re.IGNORECASE)
        if banner_match:
            data["banner"] = banner_match.group(1)
        
        # Extract followers count
        followers_match = re.search(r'(\d+[,\d]*)\s+followers?', html, re.IGNORECASE)
        if followers_match:
            data["followers_count"] = int(followers_match.group(1).replace(",", ""))
        
        # Extract employees on LinkedIn
        employees_match = re.search(r'(\d+[,\d]*)\s+employees? on LinkedIn', html, re.IGNORECASE)
        if employees_match:
            data["employees_on_linkedin"] = int(employees_match.group(1).replace(",", ""))
        
        # Extract job openings
        jobs_match = re.search(r'(\d+)\s+jobs?', html, re.IGNORECASE)
        if jobs_match:
            data["job_openings"] = int(jobs_match.group(1))
            data["hiring"] = data["job_openings"] > 0
        
        # Extract recent jobs
        data["recent_jobs"] = self._extract_recent_jobs(html)
        
        # Extract social links
        data["social_links"] = extract_social_links(html)
        
        # Detect technologies from website if available
        if data.get("website"):
            from .http_utils import detect_technologies, http_get
            try:
                website_html = http_get(data["website"], timeout=20)
                if website_html:
                    data["technologies"] = detect_technologies(website_html)
            except:
                pass
        
        # Detect intent signals
        data["intent_signals"] = self._detect_intent_signals(data, html)
        
        # Calculate growth score
        data["growth_score"] = self._calculate_growth_score(data)
        
        return data
    
    def _extract_recent_jobs(self, html):
        """Extract recent job postings"""
        jobs = []
        
        # Look for jobs section
        patterns = [
            r'<section[^>]*class="[^"]*jobs[^"]*"[^>]*>(.*?)</section>',
            r'<div[^>]*id="[^"]*jobs[^"]*"[^>]*>(.*?)</div>',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            if match:
                jobs_section = match.group(1)
                
                for job_match in re.finditer(r'<a[^>]*href="(/jobs/view/[^"]+)"[^>]*>([^<]+)</a>', jobs_section):
                    job_url = f"https://www.linkedin.com{job_match.group(1)}"
                    job_title = clean_text(job_match.group(2)).strip()
                    
                    jobs.append({
                        "title": job_title,
                        "url": job_url
                    })
                
                if jobs:
                    break
        
        return jobs[:10]  # Limit
    
    def _detect_intent_signals(self, company_data, html):
        """Detect intent signals from company data"""
        signals = []
        
        # Hiring signal
        if company_data.get("hiring") or company_data.get("job_openings", 0) > 0:
            signals.append({
                "type": "hiring",
                "description": f"Company has {company_data.get('job_openings', 0)} open positions",
                "strength": "strong" if company_data.get("job_openings", 0) > 5 else "medium",
                "category": "growth"
            })
        
        # Job titles signal
        if company_data.get("recent_jobs"):
            for job in company_data["recent_jobs"]:
                job_title = job.get("title", "").lower()
                
                # Check for tech hiring
                if any(kw in job_title for kw in ["developer", "engineer", "software", "programmer"]):
                    signals.append({
                        "type": "tech_hiring",
                        "description": f"Hiring for: {job['title']}",
                        "strength": "strong",
                        "category": "tech_stack"
                    })
                    break
        
        # Growth signals
        description = company_data.get("description", "").lower()
        tagline = company_data.get("tagline", "").lower()
        
        growth_keywords = [
            ("expanding", "medium"),
            ("growing", "medium"),
            ("scaling", "strong"),
            ("global", "strong"),
            ("international", "strong"),
            ("new market", "strong"),
            ("rapid growth", "strong"),
            ("hire", "medium"),
            ("recruit", "medium"),
            ("talent", "medium"),
        ]
        
        for keyword, strength in growth_keywords:
            if keyword in description or keyword in tagline:
                signals.append({
                    "type": "growth_keyword",
                    "description": f"Mentions '{keyword}' in description",
                    "strength": strength,
                    "category": "growth"
                })
                break
        
        # Funding signals
        funding_keywords = [
            ("raised", "strong"),
            ("series", "strong"),
            ("funding", "medium"),
            ("investment", "medium"),
            ("funded", "medium"),
            ("venture", "strong"),
            ("capital", "strong"),
        ]
        
        for keyword, strength in funding_keywords:
            if keyword in description or keyword in tagline:
                signals.append({
                    "type": "funding",
                    "description": f"Mentions '{keyword}' - potential funding",
                    "strength": strength,
                    "category": "financial"
                })
                break
        
        # Technology signals
        if company_data.get("technologies"):
            for tech in company_data["technologies"][:5]:
                signals.append({
                    "type": "technology",
                    "description": f"Uses {tech}",
                    "strength": "medium",
                    "category": "tech_stack"
                })
        
        # If no signals, add default
        if not signals:
            signals.append({
                "type": "no_signals",
                "description": "No strong intent signals detected",
                "strength": "none",
                "category": "general"
            })
        
        return signals
    
    def _calculate_growth_score(self, company_data):
        """Calculate growth score (0-100)"""
        score = 0
        
        # Hiring
        if company_data.get("hiring"):
            score += 30
            score += min(30, company_data.get("job_openings", 0) * 2)  # Max 30 from job count
        
        # Employee count
        employees = company_data.get("employees_on_linkedin", 0)
        if employees >= 1000:
            score += 15
        elif employees >= 500:
            score += 10
        elif employees >= 100:
            score += 5
        
        # Followers
        followers = company_data.get("followers_count", 0)
        if followers >= 10000:
            score += 10
        elif followers >= 5000:
            score += 7
        elif followers >= 1000:
            score += 5
        
        # Intent signals
        intent_signals = company_data.get("intent_signals", [])
        for signal in intent_signals:
            if signal.get("strength") == "strong":
                score += 5
            elif signal.get("strength") == "medium":
                score += 3
        
        # Company size
        size = company_data.get("employee_count_range", "")
        if size:
            if "10,000+" in size:
                score += 5
            elif "1,000-" in size:
                score += 5
        
        # Cap at 100
        return min(100, score)


# ============================================================================
# EMPLOYEE DISCOVERER
# ============================================================================

class LinkedInEmployeeDiscoverer:
    """Discover employees of a company on LinkedIn"""
    
    def __init__(self):
        self.searcher = LinkedInSearcher()
        self.profile_scraper = LinkedInProfileScraper()
        self.company_scraper = LinkedInCompanyScraper()
    
    def find_employees(self, company_name, company_url=None, limit=50, filters=None):
        """Find employees of a company"""
        if not company_url:
            # Try to find company URL first
            results = self.searcher.search_companies(company_name, limit=1)
            if results:
                company_url = results[0].get("company_url")
        
        if not company_url:
            return {"error": f"Could not find company: {company_name}"}
        
        # Scrape company page to get company details
        company_data = self.company_scraper.scrape_company(company_url, deep=False)
        
        # Search for employees
        search_results = self.searcher.search_people(
            query=f"past:\"{company_data.get('name', company_name)}\" OR current:\"{company_data.get('name', company_name)}\"",
            limit=limit
        )
        
        employees = []
        for result in search_results:
            if result.get("company") and company_name.lower() in result.get("company", "").lower():
                employees.append(result)
        
        # Enrich with full profile data
        enriched_employees = []
        for emp in employees[:min(limit, 20)]:  # Limit to 20 for enrichment
            profile_data = self.profile_scraper.scrape_profile(emp.get("profile_url"), deep=False)
            
            employee = {
                "name": emp.get("name"),
                "title": emp.get("title"),
                "profile_url": emp.get("profile_url"),
                "photo": emp.get("photo"),
                "location": emp.get("location"),
                "is_decision_maker": profile_data.get("is_decision_maker", False),
                "decision_maker_score": profile_data.get("decision_maker_score", 0),
                "headline": profile_data.get("headline"),
                "current_company": profile_data.get("current_company"),
                "summary": profile_data.get("summary", "")[:200] + "...",
                "connections": profile_data.get("connections", 0)
            }
            
            # Extract emails if available
            emails = profile_data.get("emails", [])
            if emails:
                employee["emails"] = emails
            
            enriched_employees.append(employee)
        
        return {
            "company": company_data.get("name", company_name),
            "company_url": company_url,
            "employees": enriched_employees,
            "total_employees_on_linkedin": company_data.get("employees_on_linkedin", 0),
            "discovered": len(employees),
            "decision_makers": [e for e in enriched_employees if e.get("is_decision_maker")]
        }
    
    def find_decision_makers(self, company_name, company_url=None, limit=20):
        """Find decision makers in a company"""
        result = self.find_employees(company_name, company_url, limit * 3)  # Search more to find decision makers
        
        decision_makers = result.get("decision_makers", [])
        
        # Sort by decision maker score
        decision_makers.sort(key=lambda x: x.get("decision_maker_score", 0), reverse=True)
        
        return {
            "company": result.get("company"),
            "company_url": result.get("company_url"),
            "decision_makers": decision_makers[:limit],
            "total_decision_makers": len(decision_makers)
        }
    
    def find_contact_info(self, company_name, company_url=None):
        """Find contact information for a company"""
        result = self.find_employees(company_name, company_url, limit=100)
        
        contacts = []
        for emp in result.get("employees", []):
            if emp.get("emails"):
                for email in emp["emails"]:
                    contacts.append({
                        "name": emp.get("name"),
                        "title": emp.get("title"),
                        "email": email,
                        "profile_url": emp.get("profile_url"),
                        "is_decision_maker": emp.get("is_decision_maker", False)
                    })
        
        # Deduplicate
        seen_emails = set()
        unique_contacts = []
        
        for c in contacts:
            if c["email"] not in seen_emails:
                seen_emails.add(c["email"])
                unique_contacts.append(c)
        
        # Sort by decision maker status
        unique_contacts.sort(key=lambda x: x.get("is_decision_maker", False), reverse=True)
        
        return {
            "company": result.get("company"),
            "company_url": result.get("company_url"),
            "contacts": unique_contacts[:50],
            "total_contacts": len(unique_contacts)
        }


# ============================================================================
# INTENT SIGNAL DETECTOR
# ============================================================================

class IntentSignalDetector:
    """Detect intent signals from LinkedIn data"""
    
    def __init__(self):
        self.hiring_keywords = [
            "hire", "hiring", "recruit", "talent", "career", "join us",
            "we're hiring", "team growth", "expanding", "scaling"
        ]
        
        self.growth_keywords = [
            "growth", "scale", "expand", "new market", "rapid",
            "hypergrowth", "accelerate", "global", "international"
        ]
        
        self.technology_keywords = [
            "ai", "machine learning", "automation", "digital transformation",
            "cloud", "saas", "platform", "software", "modernize", "upgrade"
        ]
        
        self.funding_keywords = [
            "raised", "series", "funding", "investment", "venture",
            "capital", "backed", "funded"
        ]
        
        self.problem_keywords = [
            "problem", "challenge", "issue", "pain point", "struggle",
            "difficulty", "need", "looking for", "want", "require"
        ]
    
    def detect_from_text(self, text):
        """Detect intent signals from text"""
        text = text.lower()
        signals = []
        
        # Hiring signals
        for kw in self.hiring_keywords:
            if kw in text:
                signals.append({
                    "type": "hiring",
                    "keyword": kw,
                    "strength": "strong" if any(strong_kw in text for strong_kw in ["urgent", "immediate", "now"]) else "medium",
                    "context": self._get_context(text, kw)
                })
                break
        
        # Growth signals
        for kw in self.growth_keywords:
            if kw in text:
                signals.append({
                    "type": "growth",
                    "keyword": kw,
                    "strength": "strong" if any(strong_kw in text for strong_kw in ["rapid", "hyper"]) else "medium",
                    "context": self._get_context(text, kw)
                })
                break
        
        # Technology signals
        for kw in self.technology_keywords:
            if kw in text:
                signals.append({
                    "type": "technology",
                    "keyword": kw,
                    "strength": "strong" if any(strong_kw in text for strong_kw in ["ai", "machine learning", "automation"]) else "medium",
                    "context": self._get_context(text, kw)
                })
        
        # Funding signals
        for kw in self.funding_keywords:
            if kw in text:
                signals.append({
                    "type": "funding",
                    "keyword": kw,
                    "strength": "strong" if any(strong_kw in text for strong_kw in ["raised", "series"]) else "medium",
                    "context": self._get_context(text, kw)
                })
                break
        
        # Problem signals
        for kw in self.problem_keywords:
            if kw in text:
                signals.append({
                    "type": "problem",
                    "keyword": kw,
                    "strength": "medium",
                    "context": self._get_context(text, kw)
                })
                break
        
        return signals
    
    def detect_from_company(self, company_data):
        """Detect intent signals from company data"""
        signals = []
        
        # Check hiring
        if company_data.get("hiring") or company_data.get("job_openings", 0) > 0:
            signals.append({
                "type": "hiring",
                "description": f"Company has {company_data.get('job_openings', 0)} open positions",
                "strength": "strong" if company_data.get("job_openings", 0) > 5 else "medium",
                "category": "growth"
            })
        
        # Check job titles
        if company_data.get("recent_jobs"):
            for job in company_data["recent_jobs"]:
                job_title = job.get("title", "").lower()
                
                if any(kw in job_title for kw in self.technology_keywords):
                    signals.append({
                        "type": "tech_hiring",
                        "description": f"Hiring for tech role: {job['title']}",
                        "strength": "strong",
                        "category": "technology"
                    })
        
        # Check description and tagline
        description = company_data.get("description", "")
        tagline = company_data.get("tagline", "")
        
        text_signals = self.detect_from_text(f"{description} {tagline}")
        signals.extend(text_signals)
        
        return signals
    
    def detect_from_profile(self, profile_data):
        """Detect intent signals from profile data"""
        signals = []
        
        # Check headline
        headline = profile_data.get("headline", "")
        headline_signals = self.detect_from_text(headline)
        for s in headline_signals:
            s["source"] = "headline"
        signals.extend(headline_signals)
        
        # Check summary
        summary = profile_data.get("summary", "")
        summary_signals = self.detect_from_text(summary)
        for s in summary_signals:
            s["source"] = "summary"
        signals.extend(summary_signals)
        
        # Check experience
        for exp in profile_data.get("experience", []):
            exp_text = f"{exp.get('title', '')} {exp.get('company', '')} {exp.get('dates', '')}"
            exp_signals = self.detect_from_text(exp_text)
            for s in exp_signals:
                s["source"] = "experience"
            signals.extend(exp_signals)
        
        # Decision maker signal
        if profile_data.get("is_decision_maker"):
            signals.append({
                "type": "decision_maker",
                "description": f"{profile_data.get('name', 'This person')} is a decision maker",
                "strength": "strong",
                "category": "authority",
                "source": "profile_analysis"
            })
        
        return signals
    
    def _get_context(self, text, keyword):
        """Get context around a keyword"""
        idx = text.find(keyword)
        if idx == -1:
            return ""
        
        start = max(0, idx - 50)
        end = min(len(text), idx + len(keyword) + 50)
        return text[start:end]
    
    def score_intent(self, signals):
        """Score intent based on signals"""
        score = 0
        weights = {"hiring": 30, "growth": 25, "technology": 20, "funding": 25, "decision_maker": 20}
        
        for signal in signals:
            signal_type = signal.get("type", "")
            strength = signal.get("strength", "medium")
            
            base_score = weights.get(signal_type, 10)
            
            if strength == "strong":
                score += base_score
            elif strength == "medium":
                score += base_score * 0.7
            else:
                score += base_score * 0.3
        
        # Cap at 100
        return min(100, int(score))


# ============================================================================
# EXPORTS
# ============================================================================

# Create singleton instances
linkedin_searcher = LinkedInSearcher()
linkedin_profile_scraper = LinkedInProfileScraper()
linkedin_company_scraper = LinkedInCompanyScraper()
linkedin_employee_discoverer = LinkedInEmployeeDiscoverer()
intent_signal_detector = IntentSignalDetector()


def scrape_linkedin_profile(url):
    """Convenience function to scrape a LinkedIn profile"""
    return linkedin_profile_scraper.scrape_profile(url)


def scrape_linkedin_company(url):
    """Convenience function to scrape a LinkedIn company"""
    return linkedin_company_scraper.scrape_company(url)


def find_linkedin_employees(company_name, limit=20):
    """Convenience function to find employees"""
    return linkedin_employee_discoverer.find_employees(company_name, limit=limit)


def find_linkedin_decision_makers(company_name, limit=10):
    """Convenience function to find decision makers"""
    return linkedin_employee_discoverer.find_decision_makers(company_name, limit=limit)


if __name__ == "__main__":
    print("LinkedIn Scraper - Testing")
    print("=" * 50)
    
    # Test search
    print("\n1. Testing company search...")
    searcher = LinkedInSearcher()
    companies = searcher.search_companies("Softora", limit=3)
    print(f"   Found {len(companies)} companies")
    for c in companies:
        print(f"   - {c.get('name', 'N/A')}")
    
    # Test profile scraping
    if companies:
        print("\n2. Testing profile scraping...")
        # Would need a real profile URL to test
        print("   (Profile scraping test requires a valid LinkedIn profile URL)")
    
    # Test intent detection
    print("\n3. Testing intent detection...")
    detector = IntentSignalDetector()
    signals = detector.detect_from_text("We are rapidly scaling our AI platform and hiring for multiple engineering positions")
    print(f"   Found {len(signals)} intent signals:")
    for s in signals:
        print(f"   - {s['type']}: {s['keyword']} ({s['strength']})")
    
    score = detector.score_intent(signals)
    print(f"   Intent score: {score}/100")
