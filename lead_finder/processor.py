"""
Lead Processing and Import
"""
import os
import sys
import json
import random
from datetime import datetime, timezone, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
from app import conn, now, domain_of, SERVICES, SERVICE_TO_PACKAGE, COLS, upsert
from .config import SERVICE_KEYWORDS, GENERIC_EMAILS
from .sources import ALL_SOURCES
from .enrichment import ALL_ENRICHERS
from .http_utils import extract_emails, clean_text
from .database import log_discovery, update_leads_imported


class LeadProcessor:
    """Process discovered leads and import them into the CRM"""
    
    def __init__(self):
        self.enrichers = ALL_ENRICHERS
        self.discovery_sources = ALL_SOURCES
    
    def discover_leads(self, config):
        """Discover leads from multiple sources"""
        config = config or {}
        all_leads = []
        
        for source in self.discovery_sources:
            try:
                results = source.discover(
                    keywords=config.get("keywords"),
                    limit=config.get("limit_per_source", 50),
                    filters=config.get("filters")
                )
                all_leads.extend(results)
                log_discovery(source.source_type, "", "completed", details=f"Found {len(results)} leads")
                
                # Be polite - sleep between sources
                import time
                time.sleep(2.0)
                
            except Exception as e:
                log_discovery(source.source_type, "", "error", details=str(e))
                continue
        
        return all_leads
    
    def enrich_lead(self, lead_data, deep=True):
        """Enrich a single lead with additional data"""
        if not lead_data:
            return None
        
        # Start with what we have
        enriched = dict(lead_data)
        
        # Determine domain/company
        domain = None
        company = enriched.get("title") or enriched.get("company")
        
        if "url" in enriched and enriched["url"]:
            domain = domain_of(enriched["url"])
        elif "website" in enriched and enriched["website"]:
            domain = domain_of(enriched["website"])
        
        # If we have a domain, enrich it
        if domain:
            for enricher in self.enrichers:
                try:
                    result = enricher.enrich(domain, enriched)
                    if result and not result.get("error"):
                        # Merge results
                        for key, value in result.items():
                            if key not in enriched or not enriched[key]:
                                enriched[key] = value
                            elif isinstance(enriched[key], list):
                                enriched[key].extend(value) if isinstance(value, list) else []
                        
                        # Stop after first successful enrichment if not deep
                        if not deep:
                            break
                except Exception as e:
                    log_discovery("enrichment", domain, "error", details=f"{enricher.__class__.__name__}: {e}")
                    continue
        
        return enriched
    
    def process_raw_lead(self, raw_lead):
        """Process a raw discovered lead into a CRM-ready lead"""
        if not raw_lead:
            return None
        
        # Enrich the lead
        enriched = self.enrich_lead(raw_lead)
        if not enriched:
            enriched = raw_lead
        
        # Map fields to CRM schema
        lead = {
            "domain": None,
            "company": None,
            "website": None,
            "industry": None,
            "country": None,
            "city": None,
            "service": None,
            "problem": None,
            "evidence_url": None,
            "researched_at": now(),
            "contact_name": None,
            "contact_title": None,
            "contact_email": None,
            "email_status": "unknown",
            "email_source": None,
            "linkedin_url": None,
            "f_relevance": 0,
            "f_problem": 0,
            "f_pay": 0,
            "f_recurring": 0,
            "f_access": 0,
            "f_growth": 0,
            "f_data": 0,
            "score": 0,
            "score_reason": "",
            "package": None,
            "est_mrr_low": 0,
            "est_mrr_high": 0,
            "status": "NEW",
            "notes": "",
            "discovered_at": now()
        }
        
        # Map fields
        # Domain and website
        if "url" in enriched and enriched["url"]:
            lead["website"] = enriched["url"]
            lead["domain"] = domain_of(enriched["url"])
        elif "website" in enriched and enriched["website"]:
            lead["website"] = enriched["website"]
            lead["domain"] = domain_of(enriched["website"])
        
        # Company name
        if "title" in enriched and enriched["title"]:
            lead["company"] = enriched["title"]
        if "company" in enriched and enriched["company"]:
            lead["company"] = enriched["company"]
        
        # If still no company, use domain
        if not lead["company"] and lead["domain"]:
            lead["company"] = lead["domain"].replace(".com", "").replace(".co", "").replace(".io", "").title()
        
        # Industry
        if "industry" in enriched and enriched["industry"]:
            lead["industry"] = enriched["industry"]
        
        # Location
        if "country" in enriched and enriched["country"]:
            lead["country"] = enriched["country"]
        if "city" in enriched and enriched["city"]:
            lead["city"] = enriched["city"]
        
        # Contacts
        if "emails" in enriched and enriched["emails"]:
            # Use the first non-generic email
            for email in enriched["emails"]:
                if "@" in email and not any(email.startswith(g) for g in GENERIC_EMAILS):
                    lead["contact_email"] = email
                    lead["email_source"] = "website"
                    break
            
            # If no non-generic email, use first one
            if not lead["contact_email"] and enriched["emails"]:
                lead["contact_email"] = enriched["emails"][0]
                lead["email_source"] = "website"
        
        if "contact_name" in enriched and enriched["contact_name"]:
            lead["contact_name"] = enriched["contact_name"]
        if "contact_title" in enriched and enriched["contact_title"]:
            lead["contact_title"] = enriched["contact_title"]
        
        # LinkedIn
        if "social" in enriched and enriched["social"] and "linkedin" in enriched["social"]:
            if enriched["social"]["linkedin"]:
                lead["linkedin_url"] = enriched["social"]["linkedin"][0]
        
        # Service - try to determine from intent signal or keywords
        if "intent_signal" in enriched:
            signal = enriched["intent_signal"]
            if signal == "hiring":
                # Check job keyword
                if "job_keyword" in enriched:
                    for service, keywords in self._get_job_keywords().items():
                        if any(kw in enriched["job_keyword"].lower() for kw in keywords):
                            lead["service"] = service
                            break
            elif signal == "using_tech":
                # Check technologies for service hints
                if "technologies" in enriched:
                    for tech in enriched["technologies"]:
                        tech_lower = tech.lower()
                        if any(kw in tech_lower for kw in ["wordpress", "drupal", "cms", "shopify"]):
                            lead["service"] = "cms"
                            break
                        elif any(kw in tech_lower for kw in ["seo", "search", "optimization"]):
                            lead["service"] = "technical_seo"
                            break
                        elif any(kw in tech_lower for kw in ["saas", "platform", "software"]):
                            lead["service"] = "saas"
                            break
        
        # If no service detected, use default or based on industry
        if not lead["service"]:
            if lead["industry"]:
                industry_lower = lead["industry"].lower()
                if any(kw in industry_lower for kw in ["tech", "software", "saas", "startup"]):
                    lead["service"] = random.choice(["web_app", "saas", "mobile", "ai_agents"])
                else:
                    lead["service"] = random.choice(["web_app", "saas"])
            else:
                lead["service"] = random.choice(list(SERVICES))
        
        # Set package based on service
        if lead["service"] in SERVICE_TO_PACKAGE:
            lead["package"] = SERVICE_TO_PACKAGE[lead["service"]]
        
        # Problem and evidence - if we have intent signal
        if "intent_signal" in enriched:
            if enriched["intent_signal"] == "hiring":
                lead["problem"] = f"Company is hiring for {enriched.get('job_keyword', 'development')} - indicates need for external development capacity"
                lead["evidence_url"] = enriched.get("url", "")
            elif enriched["intent_signal"] == "in_news":
                lead["problem"] = f"Company mentioned in news for {enriched.get('news_keyword', 'expansion')} - potential growth opportunity"
                lead["evidence_url"] = enriched.get("url", "")
            elif enriched["intent_signal"] == "using_tech":
                lead["problem"] = f"Company using {', '.join(enriched.get('technologies', [])[:3])} - may need modernization or additional features"
                lead["evidence_url"] = enriched.get("url", "")
        
        # Notes
        notes_parts = []
        if "description" in enriched and enriched["description"]:
            notes_parts.append(f"Description: {enriched['description'][:200]}...")
        if "source" in enriched:
            notes_parts.append(f"Source: {enriched['source']}")
        if "intent_signal" in enriched:
            notes_parts.append(f"Signal: {enriched['intent_signal']}")
        
        lead["notes"] = " | ".join(notes_parts) if notes_parts else "Auto-discovered lead"
        
        return lead
    
    def _get_job_keywords(self):
        """Get job keywords for service matching"""
        return {
            "web_app": ["web", "frontend", "backend", "full stack", "developer", "SaaS"],
            "saas": ["SaaS", "software", "platform", "product"],
            "mobile": ["mobile", "iOS", "Android", "React Native"],
            "ai_agents": ["AI", "machine learning", "LLM", "agent"],
            "automation": ["automation", "DevOps", "workflow", "RPA"],
            "erp_crm": ["ERP", "CRM", "enterprise", "business software"],
            "cms": ["CMS", "WordPress", "Drupal", "content management"],
            "technical_seo": ["SEO", "search", "optimization", "technical"],
            "aeo": ["AEO", "answer engine", "AI search"],
            "geo": ["GEO", "Google Extended", "AI ranking"],
            "seo_content": ["SEO", "content", "marketing", "writing"],
            "cro": ["CRO", "conversion", "optimization", "A/B"],
            "devops": ["DevOps", "cloud", "infrastructure", "CI/CD"]
        }
    
    def validate_lead(self, lead):
        """Validate a lead before import"""
        if not lead:
            return False, "No lead data"
        
        if not lead.get("domain"):
            return False, "Missing domain"
        
        if not lead.get("company"):
            return False, "Missing company"
        
        if not lead.get("website"):
            return False, "Missing website"
        
        if lead.get("service") not in SERVICES:
            return False, f"Invalid service: {lead.get('service')}"
        
        return True, "Valid"
    
    def import_lead(self, lead):
        """Import a lead into the CRM database"""
        valid, message = self.validate_lead(lead)
        if not valid:
            log_discovery("import", lead.get("domain", ""), "rejected", details=message)
            return False
        
        try:
            # Check if already exists
            domain = lead["domain"]
            with app.conn() as c:
                existing = c.execute("SELECT id FROM leads WHERE domain=?", (domain,)).fetchone()
                if existing:
                    # Update existing lead
                    cols = [k for k in lead if k in COLS]
                    c.execute(
                        f"UPDATE leads SET {','.join(f'{k}=?' for k in cols)} WHERE id=?",
                        [lead[k] for k in cols] + [existing[0]]
                    )
                    log_discovery("import", domain, "updated", company=lead.get("company"))
                    return True
            
            # Insert new lead
            app.upsert(lead)
            log_discovery("import", domain, "imported", company=lead.get("company"))
            update_leads_imported()
            
            return True
            
        except Exception as e:
            log_discovery("import", lead.get("domain", ""), "error", details=str(e))
            return False
    
    def import_batch(self, raw_leads, config):
        """Import a batch of raw leads"""
        config = config or {}
        imported = 0
        errors = 0
        
        for raw_lead in raw_leads:
            try:
                # Process the raw lead
                processed_lead = self.process_raw_lead(raw_lead)
                if not processed_lead:
                    errors += 1
                    continue
                
                # Enrich if configured
                if config.get("enrich", True):
                    enriched_lead = self.enrich_lead(processed_lead, deep=config.get("deep_enrich", False))
                    if enriched_lead:
                        processed_lead.update(enriched_lead)
                
                # Import
                if self.import_lead(processed_lead):
                    imported += 1
                else:
                    errors += 1
                    
            except Exception as e:
                log_discovery("import", "", "error", details=str(e))
                errors += 1
        
        return {"imported": imported, "errors": errors}


def init_all():
    """Initialize all databases"""
    app.init()
    from .database import init_db
    init_db()
