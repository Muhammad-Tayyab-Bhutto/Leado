"""
Advanced Email Finder & Verifier - Replaces Hunter.io, Snov.io, and similar tools

This module provides:
1. Email pattern discovery from websites
2. Email verification (SMTP, DNS, syntax)
3. Professional email guessing with validation
4. Bulk email finding for multiple domains
5. Email deliverability scoring
6. Integration with multiple verification APIs
"""
import re
import json
import dns.resolver
import socket
import smtplib
import urllib.parse
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import UA, domain_of, GENERIC_EMAILS, EXCLUDED_DOMAINS
from .http_utils import http_get, clean_text, extract_emails
from .database import cache_enrichment, get_cached_enrichment, log_discovery


# ============================================================================
# EMAIL VERIFICATION UTILITIES
# ============================================================================

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

class EmailVerifier:
    """Advanced email verification with multiple methods"""
    
    def __init__(self, max_workers=10):
        self.max_workers = max_workers
        self.dns_cache = {}
        self.smtp_cache = {}
    
    def verify_syntax(self, email):
        """Verify email syntax"""
        if not email or not isinstance(email, str):
            return False, "No email provided"
        
        email = email.strip().lower()
        
        # Basic syntax check
        if not EMAIL_RE.match(email):
            return False, "Invalid email format"
        
        # Check for disposable domains
        disposable_domains = {
            "mailinator.com", "tempmail.com", "guerrillamail.com", "10minutemail.com",
            "throwawaymail.com", "fakeinbox.com", "temp-mail.org", "yopmail.com",
            "trashmail.com", "maildrop.cc", "getnada.com", "tempinbox.com"
        }
        
        domain = email.split("@")[1]
        if domain in disposable_domains or any(d in domain for d in ["mailinator", "temp", "throwaway"]):
            return False, "Disposable email domain"
        
        # Check for generic email patterns
        local_part = email.split("@")[0]
        if any(local_part.startswith(g) for g in GENERIC_EMAILS):
            return False, "Generic email address (info@, contact@, etc.)"
        
        # Check length
        if len(email) > 254:
            return False, "Email too long"
        if len(local_part) > 64:
            return False, "Local part too long"
        if len(domain) > 253:
            return False, "Domain too long"
        
        # Check for consecutive dots
        if ".." in email:
            return False, "Consecutive dots in email"
        
        # Check for special characters at start/end
        if local_part.startswith(".") or local_part.endswith("."):
            return False, "Email starts or ends with dot"
        
        return True, "Syntax valid"
    
    def verify_dns_mx(self, domain):
        """Verify domain has MX records"""
        if domain in self.dns_cache:
            return self.dns_cache[domain]
        
        try:
            # Check MX records
            mx_records = dns.resolver.resolve(domain, "MX")
            if mx_records:
                mx_preferences = [(r.preference, str(r.exchange)) for r in mx_records]
                self.dns_cache[domain] = {"has_mx": True, "mx_records": mx_preferences}
                return self.dns_cache[domain]
            
            # If no MX, check for A record
            a_records = dns.resolver.resolve(domain, "A")
            if a_records:
                self.dns_cache[domain] = {"has_mx": False, "has_a": True, "mx_records": []}
                return self.dns_cache[domain]
            
            self.dns_cache[domain] = {"has_mx": False, "has_a": False, "mx_records": []}
            return self.dns_cache[domain]
            
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            self.dns_cache[domain] = {"has_mx": False, "has_a": False, "mx_records": []}
            return self.dns_cache[domain]
        except Exception as e:
            self.dns_cache[domain] = {"error": str(e)}
            return self.dns_cache[domain]
    
    def verify_smtp(self, email, timeout=10):
        """Verify email via SMTP (without sending email)"""
        domain = email.split("@")[1]
        local_part = email.split("@")[0]
        
        if domain in self.smtp_cache:
            return self.smtp_cache[domain]
        
        # Get MX records
        dns_result = self.verify_dns_mx(domain)
        if not dns_result.get("has_mx") and not dns_result.get("has_a"):
            return {"smtp_valid": False, "reason": "No MX or A records"}
        
        if dns_result.get("has_mx"):
            mx_hosts = sorted([r[1] for r in dns_result["mx_records"]])
        else:
            # Use A record
            try:
                a_records = dns.resolver.resolve(domain, "A")
                mx_hosts = [str(r) for r in a_records]
            except:
                mx_hosts = [domain]
        
        result = {"smtp_valid": False, "mx_hosts_tested": [], "reason": ""}
        
        for mx_host in mx_hosts[:3]:  # Test up to 3 MX servers
            try:
                # Connect to SMTP server
                with smtplib.SMTP(mx_host, timeout=timeout) as server:
                    server.set_debuglevel(0)
                    
                    # EHLO/HELO
                    server.ehlo()
                    
                    # Check if server supports VRFY
                    if hasattr(server, "verify"):
                        try:
                            code, msg = server.verify(email)
                            if code == 252:
                                result["smtp_valid"] = True
                                result["reason"] = "SMTP server confirmed"
                                result["mx_hosts_tested"].append({"host": mx_host, "response": "verified"})
                                break
                        except:
                            pass
                    
                    # Try MAIL FROM and RCPT TO
                    server.mail("")
                    code, msg = server.rcpt(email)
                    
                    if code in (250, 251):
                        result["smtp_valid"] = True
                        result["reason"] = "SMTP server accepted recipient"
                        result["mx_hosts_tested"].append({"host": mx_host, "response": "accepted"})
                        break
                    elif code == 550:
                        result["reason"] = "Mailbox does not exist"
                        result["mx_hosts_tested"].append({"host": mx_host, "response": "rejected"})
                        break
                    
                    result["mx_hosts_tested"].append({"host": mx_host, "response": f"code {code}"})
                    
            except (smtplib.SMTPConnectError, smtplib.SMTPException, socket.timeout, socket.gaierror) as e:
                result["mx_hosts_tested"].append({"host": mx_host, "response": f"connection failed: {str(e)[:50]}"})
                continue
            except Exception as e:
                result["mx_hosts_tested"].append({"host": mx_host, "response": f"error: {str(e)[:50]}"})
                continue
        
        self.smtp_cache[domain] = result
        return result
    
    def verify_email_full(self, email, verify_smtp=True, timeout=10):
        """Full email verification with all methods"""
        result = {
            "email": email,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "syntax_valid": False,
            "syntax_reason": "",
            "domain_valid": False,
            "dns_mx": None,
            "smtp_valid": None,
            "overall_score": 0,
            "status": "invalid",
            "recommendation": "Do not use"
        }
        
        # Step 1: Syntax check
        syntax_valid, syntax_reason = self.verify_syntax(email)
        result["syntax_valid"] = syntax_valid
        result["syntax_reason"] = syntax_reason
        
        if not syntax_valid:
            result["status"] = "invalid"
            result["overall_score"] = 10
            result["recommendation"] = "Invalid email - do not use"
            return result
        
        # Step 2: DNS check
        domain = email.split("@")[1]
        dns_result = self.verify_dns_mx(domain)
        result["dns_mx"] = dns_result
        
        if dns_result.get("has_mx") or dns_result.get("has_a"):
            result["domain_valid"] = True
            result["overall_score"] += 30
        else:
            result["domain_valid"] = False
            result["status"] = "domain_invalid"
            result["recommendation"] = "Domain has no mail servers"
            return result
        
        # Step 3: SMTP verification
        if verify_smtp:
            smtp_result = self.verify_smtp(email, timeout)
            result["smtp_valid"] = smtp_result
            
            if smtp_result.get("smtp_valid"):
                result["overall_score"] += 50
                result["status"] = "valid"
                result["recommendation"] = "Valid email - safe to use"
            else:
                result["overall_score"] += 10
                result["status"] = "unknown"
                result["recommendation"] = "SMTP check inconclusive"
        else:
            result["overall_score"] += 30
            result["status"] = "dns_valid"
            result["recommendation"] = "DNS valid - likely exists"
        
        # Score interpretation
        if result["overall_score"] >= 80:
            result["confidence"] = "High (>80%)"
        elif result["overall_score"] >= 60:
            result["confidence"] = "Medium (60-80%)"
        elif result["overall_score"] >= 40:
            result["confidence"] = "Low (40-60%)"
        else:
            result["confidence"] = "Very Low (<40%)"
        
        return result
    
    def verify_batch(self, emails, verify_smtp=True, timeout=10):
        """Verify a batch of emails in parallel"""
        results = {}
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_email = {
                executor.submit(self.verify_email_full, email, verify_smtp, timeout): email
                for email in emails
            }
            
            for future in as_completed(future_to_email):
                email = future_to_email[future]
                try:
                    results[email] = future.result()
                except Exception as e:
                    results[email] = {
                        "email": email,
                        "error": str(e),
                        "status": "error",
                        "overall_score": 0
                    }
        
        return results


# ============================================================================
# EMAIL PATTERN DISCOVERER
# ============================================================================

class EmailPatternDiscoverer:
    """Discover email patterns from websites"""
    
    def __init__(self):
        self.common_patterns = [
            # Standard patterns
            "{first}.{last}@{domain}",
            "{first}{last}@{domain}",
            "{first}_{last}@{domain}",
            "{first}-{last}@{domain}",
            "{first}@{domain}",
            "{last}@{domain}",
            "{first}.{last_initial}@{domain}",
            "{first_initial}.{last}@{domain}",
            "{first}{last_initial}@{domain}",
            
            # Common corporate patterns
            "{first}.{last}@company.{domain}",
            "{first}{last}@company.{domain}",
            "{first}@{domain}",
            "{last}@{domain}",
            
            # Specific patterns for different countries
            "{last}.{first}@{domain}",  # European style
        ]
        self.stop_words = ["test", "admin", "support", "info", "contact", "hello", "sales", "noreply"]
    
    def extract_names_from_website(self, domain, url=None):
        """Extract names from a company's website"""
        if not url:
            url = f"https://{domain}"
        
        names = set()
        
        try:
            # Try homepage
            html = http_get(url, timeout=30)
            if html:
                names.update(self._extract_names_from_html(html))
            
            # Try about page
            about_url = f"https://{domain}/about"
            about_html = http_get(about_url, timeout=20)
            if about_html:
                names.update(self._extract_names_from_html(about_html))
            
            # Try team page
            team_url = f"https://{domain}/team"
            team_html = http_get(team_url, timeout=20)
            if team_html:
                names.update(self._extract_names_from_html(team_html))
            
            # Try contact page
            contact_url = f"https://{domain}/contact"
            contact_html = http_get(contact_url, timeout=20)
            if contact_html:
                names.update(self._extract_names_from_html(contact_html))
            
        except Exception as e:
            log_discovery("email_pattern", domain, "error", details=str(e))
        
        return sorted(list(names))
    
    def _extract_names_from_html(self, html):
        """Extract potential names from HTML"""
        names = set()
        
        # Look for patterns that look like names
        # Title case words that appear together
        text = clean_text(html)
        
        # Look for "First Last" patterns
        name_patterns = [
            r'\b[A-Z][a-z]+\s+[A-Z][a-z]+\b',  # First Last
            r'\b[A-Z][a-z]+\s+[A-Z]\.\s+[A-Z][a-z]+\b',  # First M. Last
            r'\b[A-Z][a-z]+\s+[A-Z][a-z]+\s+[A-Z][a-z]+\b',  # First Middle Last
        ]
        
        for pattern in name_patterns:
            for match in re.finditer(pattern, text):
                name = match.group().strip()
                # Exclude common non-name words
                if not any(word in name.lower() for word in ["the", "and", "or", "for", "with", "this", "that", "are", "was", "been"]):
                    names.add(name)
        
        # Look for names in class names that might contain them
        # "person-name", "team-member", etc.
        for match in re.finditer(r'class="[^"]*person[^"]*"[^>]*>([^<]+)<', html, re.IGNORECASE):
            name = clean_text(match.group(1))
            if name and len(name.split()) >= 2:
                names.add(name)
        
        for match in re.finditer(r'class="[^"]*name[^"]*"[^>]*>([^<]+)<', html, re.IGNORECASE):
            name = clean_text(match.group(1))
            if name and len(name.split()) >= 2:
                names.add(name)
        
        return names
    
    def guess_emails(self, domain, names=None, limit=20):
        """Generate possible email addresses from names and domain"""
        if not names:
            names = self.extract_names_from_website(domain)
        
        if not names:
            return []
        
        guessed_emails = []
        seen = set()
        
        for name in names[:10]:  # Limit to first 10 names
            parts = name.split()
            
            if len(parts) == 1:
                # Single name - try common patterns
                first = parts[0]
                last = ""
                first_initial = first[0].lower() if first else ""
                last_initial = ""
            elif len(parts) == 2:
                first = parts[0]
                last = parts[1]
                first_initial = first[0].lower() if first else ""
                last_initial = last[0].lower() if last else ""
            else:
                first = parts[0]
                last = parts[-1]
                first_initial = first[0].lower() if first else ""
                last_initial = last[0].lower() if last else ""
            
            # Generate patterns
            patterns_to_try = [
                ("{first}.{last}@{domain}", 0.9),
                ("{first}{last}@{domain}", 0.8),
                ("{first}_{last}@{domain}", 0.8),
                ("{first}-{last}@{domain}", 0.8),
                ("{first}@{domain}", 0.7),
                ("{last}@{domain}", 0.7),
                ("{first}.{last_initial}@{domain}", 0.85),
                ("{first_initial}.{last}@{domain}", 0.85),
                ("{first}{last_initial}@{domain}", 0.8),
                ("{last}.{first}@{domain}", 0.7),  # European
                ("{last}{first}@{domain}", 0.7),
                ("{first_initial}{last}@{domain}", 0.75),
            ]
            
            for pattern, confidence in patterns_to_try:
                try:
                    email = pattern.format(
                        first=first.lower(),
                        last=last.lower(),
                        first_initial=first_initial,
                        last_initial=last_initial,
                        domain=domain
                    )
                    
                    # Skip generic emails
                    if any(email.startswith(g) for g in GENERIC_EMAILS):
                        continue
                    
                    # Skip if local part is too short
                    local_part = email.split("@")[0]
                    if len(local_part) < 3:
                        continue
                    
                    # Check syntax
                    if not EMAIL_RE.match(email):
                        continue
                    
                    if email not in seen:
                        seen.add(email)
                        guessed_emails.append({
                            "email": email,
                            "pattern": pattern,
                            "name": name,
                            "confidence": confidence * 100,
                            "verified": False
                        })
                        
                        if len(guessed_emails) >= limit:
                            break
                except:
                    continue
            
            if len(guessed_emails) >= limit:
                break
        
        # Sort by confidence
        guessed_emails.sort(key=lambda x: x["confidence"], reverse=True)
        
        return guessed_emails[:limit]
    
    def find_emails_from_website(self, domain, url=None):
        """Find all emails from a website"""
        if not url:
            url = f"https://{domain}"
        
        emails = []
        
        try:
            html = http_get(url, timeout=30)
            if html:
                found_emails = extract_emails(html)
                for email in found_emails:
                    emails.append({
                        "email": email,
                        "source": "website_homepage",
                        "url": url,
                        "confidence": 100
                    })
            
            # Check contact page
            contact_url = f"https://{domain}/contact"
            try:
                contact_html = http_get(contact_url, timeout=20)
                if contact_html:
                    found_emails = extract_emails(contact_html)
                    for email in found_emails:
                        if not any(e["email"] == email for e in emails):
                            emails.append({
                                "email": email,
                                "source": "website_contact",
                                "url": contact_url,
                                "confidence": 100
                            })
            except:
                pass
            
            # Check about page
            about_url = f"https://{domain}/about"
            try:
                about_html = http_get(about_url, timeout=20)
                if about_html:
                    found_emails = extract_emails(about_html)
                    for email in found_emails:
                        if not any(e["email"] == email for e in emails):
                            emails.append({
                                "email": email,
                                "source": "website_about",
                                "url": about_url,
                                "confidence": 100
                            })
            except:
                pass
            
        except Exception as e:
            log_discovery("email_find", domain, "error", details=str(e))
        
        return emails


# ============================================================================
# COMPREHENSIVE EMAIL FINDER
# ============================================================================

class ComprehensiveEmailFinder:
    """Comprehensive email finder combining all methods"""
    
    def __init__(self, max_workers=10):
        self.verifier = EmailVerifier(max_workers)
        self.pattern_discoverer = EmailPatternDiscoverer()
        self.max_workers = max_workers
    
    def find_emails_for_domain(self, domain, verify=True, guess=True, limit=50):
        """Find all emails for a domain"""
        results = []
        
        # Step 1: Extract emails from website
        website_emails = self.pattern_discoverer.find_emails_from_website(domain)
        results.extend(website_emails)
        
        # Step 2: Guess emails from names
        if guess:
            guessed_emails = self.pattern_discoverer.guess_emails(domain, limit=limit)
            results.extend([
                {
                    "email": e["email"],
                    "source": "pattern_guess",
                    "url": f"https://{domain}",
                    "confidence": e["confidence"],
                    "name": e["name"],
                    "pattern": e["pattern"]
                }
                for e in guessed_emails
            ])
        
        # Step 3: Deduplicate
        seen_emails = set()
        unique_results = []
        
        for r in results:
            email = r["email"].lower()
            if email not in seen_emails:
                seen_emails.add(email)
                unique_results.append(r)
        
        results = unique_results[:limit * 2]  # Allow more for verification
        
        # Step 4: Verify emails
        if verify:
            emails_to_verify = [r["email"] for r in results]
            verification_results = self.verifier.verify_batch(emails_to_verify, verify_smtp=False)
            
            for r in results:
                email = r["email"].lower()
                if email in verification_results:
                    r["verification"] = verification_results[email]
                    r["verified"] = r["verification"].get("syntax_valid", False) and r["verification"].get("domain_valid", False)
                    r["verification_score"] = r["verification"].get("overall_score", 0)
        
        # Sort by verification score and confidence
        results.sort(key=lambda x: (
            x.get("verified", False) * 1000 + 
            x.get("verification_score", 0) + 
            x.get("confidence", 0)
        ), reverse=True)
        
        return results[:limit]
    
    def find_emails_for_lead(self, lead_data):
        """Find emails for a specific lead"""
        domain = lead_data.get("domain") or lead_data.get("website")
        if not domain:
            return {"error": "No domain provided"}
        
        domain = domain_of(domain)
        
        # Check cache first
        cached = get_cached_enrichment(f"emails_{domain}")
        if cached:
            return {"emails": cached, "cached": True}
        
        # Find emails
        emails = self.find_emails_for_domain(domain)
        
        # Cache results
        cache_enrichment(f"emails_{domain}", emails, "email_finder")
        
        return {"emails": emails, "cached": False, "domain": domain}
    
    def verify_and_filter_emails(self, emails, min_confidence=50):
        """Verify and filter a list of emails"""
        verification = self.verifier.verify_batch(emails)
        
        valid_emails = []
        for email, result in verification.items():
            if result.get("overall_score", 0) >= min_confidence:
                valid_emails.append({
                    "email": email,
                    "verification": result,
                    "score": result.get("overall_score", 0)
                })
        
        # Sort by score
        valid_emails.sort(key=lambda x: x["score"], reverse=True)
        
        return valid_emails
    
    def bulk_find_emails(self, domains, limit_per_domain=10):
        """Find emails for multiple domains in bulk"""
        results = {}
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_domain = {
                executor.submit(self.find_emails_for_domain, domain, True, True, limit_per_domain): domain
                for domain in domains
            }
            
            for future in as_completed(future_to_domain):
                domain = future_to_domain[future]
                try:
                    results[domain] = future.result()
                except Exception as e:
                    results[domain] = {"error": str(e)}
        
        return results


# ============================================================================
# API INTEGRATION (for paid email verification services)
# ============================================================================

class EmailVerificationAPI:
    """Integration with paid email verification APIs"""
    
    def __init__(self):
        self.providers = {
            "hunter": self._verify_hunter,
            "clearbit": self._verify_clearbit,
            "snov": self._verify_snov,
            "zero_bounce": self._verify_zerobounce,
            "never_bounce": self._verify_neverbounce,
        }
    
    def _verify_hunter(self, email, api_key=None):
        """Verify email using Hunter.io API"""
        if not api_key:
            from .config import HUNTER_KEY
            api_key = HUNTER_KEY
        
        if not api_key:
            return {"error": "Hunter.io API key not configured"}
        
        try:
            import urllib.request
            import json
            
            url = f"https://api.hunter.io/v2/email-verifier?email={email}&api_key={api_key}"
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.load(r)
                return {
                    "source": "hunter",
                    "data": result,
                    "status": result.get("data", {}).get("status"),
                    "score": result.get("data", {}).get("score", 0) * 100,
                    "deliverable": result.get("data", {}).get("deliverable", False),
                    "accept_all": result.get("data", {}).get("accept_all", False)
                }
        except Exception as e:
            return {"error": str(e), "source": "hunter"}
    
    def _verify_clearbit(self, email, api_key=None):
        """Verify email using Clearbit API"""
        if not api_key:
            from .config import CLEARBIT_KEY
            api_key = CLEARBIT_KEY
        
        if not api_key:
            return {"error": "Clearbit API key not configured"}
        
        try:
            import urllib.request
            import json
            
            # Extract domain
            domain = email.split("@")[1]
            url = f"https://company.clearbit.com/v2/companies/find?domain={domain}"
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": UA
                }
            )
            
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.load(r)
                return {
                    "source": "clearbit",
                    "data": result,
                    "company": result.get("name"),
                    "domain": result.get("domain")
                }
        except Exception as e:
            return {"error": str(e), "source": "clearbit"}
    
    def _verify_snov(self, email, api_key=None):
        """Verify email using Snov.io API"""
        # Placeholder for Snov.io integration
        return {"error": "Snov.io integration not yet implemented", "source": "snov"}
    
    def _verify_zerobounce(self, email, api_key=None):
        """Verify email using ZeroBounce API"""
        # Placeholder for ZeroBounce integration
        return {"error": "ZeroBounce integration not yet implemented", "source": "zerobounce"}
    
    def _verify_neverbounce(self, email, api_key=None):
        """Verify email using NeverBounce API"""
        # Placeholder for NeverBounce integration
        return {"error": "NeverBounce integration not yet implemented", "source": "neverbounce"}
    
    def verify_with_api(self, email, service="hunter", api_key=None):
        """Verify email using a specific API service"""
        if service in self.providers:
            return self.providers[service](email, api_key)
        else:
            return {"error": f"Unknown verification service: {service}"}
    
    def verify_batch_with_api(self, emails, service="hunter", api_key=None):
        """Verify a batch of emails using a specific API service"""
        results = {}
        
        for email in emails:
            results[email] = self.verify_with_api(email, service, api_key)
        
        return results


# ============================================================================
# EMAIL CONFIDENCE SCORING
# ============================================================================

class EmailConfidenceScorer:
    """Score email confidence based on multiple factors"""
    
    def __init__(self):
        self.weights = {
            "syntax_valid": 10,
            "domain_mx": 20,
            "smtp_verified": 40,
            "found_on_website": 30,
            "name_match": 20,
            "title_match": 15,
            "company_domain_match": 25,
            "not_generic": 10,
            "api_verified": 35,
        }
    
    def score_email(self, email_data):
        """Calculate confidence score for an email"""
        score = 0
        factors = {}
        
        email = email_data.get("email", "")
        
        # Syntax valid
        valid, reason = EmailVerifier().verify_syntax(email)
        factors["syntax_valid"] = (valid, reason)
        if valid:
            score += self.weights["syntax_valid"]
        
        # Domain has MX
        domain = email.split("@")[1] if "@" in email else ""
        if domain:
            dns_result = EmailVerifier().verify_dns_mx(domain)
            has_mx = dns_result.get("has_mx", False)
            factors["domain_mx"] = (has_mx, "Has MX records" if has_mx else "No MX records")
            if has_mx:
                score += self.weights["domain_mx"]
        
        # SMTP verified
        smtp_verified = email_data.get("smtp_verified", False)
        factors["smtp_verified"] = (smtp_verified, "SMTP verified" if smtp_verified else "Not SMTP verified")
        if smtp_verified:
            score += self.weights["smtp_verified"]
        
        # Found on website
        found_on_website = email_data.get("source") in ["website_homepage", "website_contact", "website_about"]
        factors["found_on_website"] = (found_on_website, "Found on website" if found_on_website else "Not found on website")
        if found_on_website:
            score += self.weights["found_on_website"]
        
        # Name match
        name = email_data.get("name", "")
        local_part = email.split("@")[0] if "@" in email else ""
        name_match = bool(name and self._check_name_match(name, local_part))
        factors["name_match"] = (name_match, f"Name matches: {name}" if name_match else "No name match")
        if name_match:
            score += self.weights["name_match"]
        
        # Title match
        title = email_data.get("title", "")
        title_match = bool(title and any(word in local_part.lower() for word in title.lower().split()))
        factors["title_match"] = (title_match, f"Title matches: {title}" if title_match else "No title match")
        if title_match:
            score += self.weights["title_match"]
        
        # Company domain match
        company_domain = email_data.get("company_domain", "")
        domain_match = bool(domain and company_domain and domain == company_domain)
        factors["company_domain_match"] = (domain_match, "Domain matches company" if domain_match else "Domain mismatch")
        if domain_match:
            score += self.weights["company_domain_match"]
        
        # Not generic
        not_generic = not any(local_part.startswith(g) for g in GENERIC_EMAILS)
        factors["not_generic"] = (not_generic, "Not generic" if not_generic else "Generic email")
        if not_generic:
            score += self.weights["not_generic"]
        
        # API verified
        api_verified = email_data.get("api_verified", False)
        factors["api_verified"] = (api_verified, "API verified" if api_verified else "Not API verified")
        if api_verified:
            score += self.weights["api_verified"]
        
        # Normalize score to 0-100
        max_possible = sum(self.weights.values())
        normalized_score = min(100, int((score / max_possible) * 100))
        
        return {
            "score": normalized_score,
            "raw_score": score,
            "max_score": max_possible,
            "factors": factors,
            "recommendation": self._get_recommendation(normalized_score)
        }
    
    def _check_name_match(self, name, local_part):
        """Check if name matches local part of email"""
        name_parts = name.lower().split()
        local_parts = local_part.lower().split(".") + local_part.lower().split("_") + [local_part.lower()]
        
        for name_part in name_parts:
            if len(name_part) > 2:  # Ignore very short parts
                for local_part in local_parts:
                    if name_part in local_part or local_part in name_part:
                        return True
        
        return False
    
    def _get_recommendation(self, score):
        """Get recommendation based on score"""
        if score >= 80:
            return "High confidence - use immediately"
        elif score >= 60:
            return "Medium confidence - verify before use"
        elif score >= 40:
            return "Low confidence - manual verification required"
        else:
            return "Very low confidence - do not use"


# ============================================================================
# EXPORTS
# ============================================================================

# Create singleton instances
email_verifier = EmailVerifier()
email_pattern_discoverer = EmailPatternDiscoverer()
comprehensive_email_finder = ComprehensiveEmailFinder()
email_verification_api = EmailVerificationAPI()
email_confidence_scorer = EmailConfidenceScorer()


def find_emails(domain, limit=20):
    """Convenience function to find emails for a domain"""
    return comprehensive_email_finder.find_emails_for_domain(domain, limit=limit)


def verify_email(email):
    """Convenience function to verify an email"""
    return email_verifier.verify_email_full(email)


if __name__ == "__main__":
    # Test the email finder
    print("Testing Email Finder...")
    finder = ComprehensiveEmailFinder()
    
    # Test with a known domain
    domain = "softorasystems.com"
    print(f"\nFinding emails for {domain}...")
    results = finder.find_emails_for_domain(domain, limit=10)
    
    print(f"Found {len(results)} potential emails:")
    for r in results[:5]:
        print(f"  {r['email']} - Confidence: {r.get('confidence', 0)}, Verified: {r.get('verified', False)}")
    
    # Test verification
    print("\nVerifying an email...")
    test_email = "contact@softorasystems.com"
    verification = email_verifier.verify_email_full(test_email)
    print(f"  {test_email}: Score={verification['overall_score']}, Status={verification['status']}")
