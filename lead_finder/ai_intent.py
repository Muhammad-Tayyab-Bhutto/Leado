"""
AI-Powered Intent Detection System

This module uses AI to detect buying intent and identify high-value leads.
It analyzes:
- Website content and changes
- Job postings
- Social media activity
- Company growth signals
- Technology stack evolution
- Funding and expansion news
- Decision maker movements

Replaces Apollo.io's intent data and similar features.
"""
import re
import json
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import UA, SERVICE_KEYWORDS, INDUSTRY_KEYWORDS, domain_of
from .http_utils import http_get, clean_text, extract_links, detect_technologies
from .database import conn as finder_conn, log_discovery, cache_enrichment, get_cached_enrichment


# ============================================================================
# CONTENT ANALYZER
# ============================================================================

class ContentAnalyzer:
    """Analyze website content for intent signals"""
    
    def __init__(self):
        self.keyword_weights = {
            # Strong intent keywords
            "request for proposal": 10,
            "rfp": 10,
            "vendor selection": 9,
            "looking for": 8,
            "need a": 8,
            "require": 8,
            "we need": 8,
            "looking to": 7,
            "interested in": 7,
            "evaluate": 7,
            "comparing": 7,
            "implement": 7,
            "build": 7,
            "create": 7,
            "develop": 7,
            "migrate": 7,
            "upgrade": 7,
            "modernize": 7,
            "redesign": 7,
            
            # Technology keywords
            "ai": 6,
            "machine learning": 6,
            "automation": 6,
            "digital transformation": 6,
            "cloud": 5,
            "saas": 5,
            "software": 5,
            "platform": 5,
            "solution": 5,
            "tool": 5,
            "system": 5,
            
            # Growth keywords
            "growth": 6,
            "scale": 6,
            "expand": 6,
            "new market": 6,
            "rapid": 5,
            "global": 5,
            "international": 5,
            
            # Pain points
            "problem": 6,
            "challenge": 6,
            "issue": 6,
            "struggle": 6,
            "difficulty": 6,
            "pain point": 7,
            "bottleneck": 6,
            "inefficient": 6,
            "slow": 5,
            "outdated": 6,
            "legacy": 6,
            
            # Urgency keywords
            "urgent": 8,
            "immediate": 8,
            "asap": 8,
            "now": 7,
            "soon": 6,
            "quickly": 6,
        }
        
        self.negative_keywords = {
            "not", "never", "no longer", "stopped", "cancelled", "discontinued"
        }
    
    def analyze_text(self, text, context=None):
        """Analyze text for intent signals"""
        if not text:
            return {"intent_score": 0, "signals": [], "sentiment": "neutral"}
        
        text_lower = text.lower()
        signals = []
        total_score = 0
        
        # Check for negative sentiment
        has_negative = any(neg in text_lower for neg in self.negative_keywords)
        
        # Find all keywords
        for keyword, weight in self.keyword_weights.items():
            if keyword in text_lower:
                # Check if it's negated
                negated = False
                if has_negative:
                    # Check if negative word appears before keyword
                    for neg in self.negative_keywords:
                        neg_idx = text_lower.find(neg)
                        kw_idx = text_lower.find(keyword)
                        if neg_idx != -1 and kw_idx != -1 and neg_idx < kw_idx:
                            negated = True
                            break
                
                if not negated:
                    # Calculate context score based on proximity
                    context_score = weight
                    
                    # Get context
                    idx = text_lower.find(keyword)
                    start = max(0, idx - 50)
                    end = min(len(text_lower), idx + len(keyword) + 50)
                    context = text[start:end].strip()
                    
                    signals.append({
                        "keyword": keyword,
                        "score": context_score,
                        "context": context,
                        "type": self._classify_keyword(keyword),
                        "strength": "strong" if context_score >= 8 else "medium" if context_score >= 6 else "weak"
                    })
                    
                    total_score += context_score
        
        # Calculate intent score (0-100)
        intent_score = min(100, total_score * 2)  # Scale up
        
        # Determine sentiment
        if intent_score >= 70:
            sentiment = "strong_intent"
        elif intent_score >= 40:
            sentiment = "medium_intent"
        elif intent_score >= 20:
            sentiment = "weak_intent"
        else:
            sentiment = "no_intent"
        
        return {
            "intent_score": intent_score,
            "signals": signals,
            "sentiment": sentiment,
            "signal_count": len(signals),
            "text_length": len(text)
        }
    
    def _classify_keyword(self, keyword):
        """Classify keyword into intent type"""
        purchase_keywords = ["rfp", "request for proposal", "vendor", "looking for", "need", "require", "evaluate", "comparing"]
        technology_keywords = ["ai", "machine learning", "automation", "digital transformation", "cloud", "saas", "software", "platform"]
        growth_keywords = ["growth", "scale", "expand", "new market", "rapid", "global"]
        pain_keywords = ["problem", "challenge", "issue", "struggle", "pain point", "bottleneck", "inefficient", "outdated", "legacy"]
        urgency_keywords = ["urgent", "immediate", "asap", "now", "soon"]
        
        keyword_lower = keyword.lower()
        
        if any(kw in keyword_lower for kw in purchase_keywords):
            return "purchase_intent"
        elif any(kw in keyword_lower for kw in technology_keywords):
            return "technology_focus"
        elif any(kw in keyword_lower for kw in growth_keywords):
            return "growth_indicator"
        elif any(kw in keyword_lower for kw in pain_keywords):
            return "pain_point"
        elif any(kw in keyword_lower for kw in urgency_keywords):
            return "urgency"
        else:
            return "general"
    
    def analyze_page(self, url, timeout=30):
        """Analyze a web page for intent signals"""
        try:
            html = http_get(url, timeout=timeout)
            if not html:
                return {"error": "Could not fetch page", "url": url}
            
            text = clean_text(html)
            
            # Analyze main content
            result = self.analyze_text(text, context=f"URL: {url}")
            result["url"] = url
            result["domain"] = domain_of(url)
            
            # Extract page title
            title_match = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
            if title_match:
                result["title"] = clean_text(title_match.group(1))
            
            # Extract meta description
            desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if desc_match:
                result["description"] = clean_text(desc_match.group(1))
            
            # Detect technologies
            result["technologies"] = detect_technologies(html)
            
            return result
            
        except Exception as e:
            return {"error": str(e), "url": url}
    
    def analyze_website(self, domain, pages=None, timeout=30):
        """Analyze entire website for intent signals"""
        if not pages:
            pages = [
                f"https://{domain}",
                f"https://{domain}/about",
                f"https://{domain}/services",
                f"https://{domain}/products",
                f"https://{domain}/careers",
                f"https://{domain}/jobs",
                f"https://{domain}/contact",
                f"https://{domain}/blog",
                f"https://{domain}/news",
            ]
        
        results = []
        
        for page_url in pages:
            try:
                page_result = self.analyze_page(page_url, timeout)
                if not page_result.get("error"):
                    results.append(page_result)
            except:
                pass
            
            time.sleep(1)  # Be polite
        
        if not results:
            return {"error": "Could not analyze any pages", "domain": domain}
        
        # Aggregate results
        total_intent_score = sum(r.get("intent_score", 0) for r in results)
        avg_intent_score = total_intent_score / len(results)
        
        all_signals = []
        for r in results:
            all_signals.extend(r.get("signals", []))
        
        # Count signals by type
        signal_types = Counter(s.get("type") for s in all_signals)
        
        # Get unique technologies
        all_technologies = set()
        for r in results:
            all_technologies.update(r.get("technologies", []))
        
        # Determine overall sentiment
        if avg_intent_score >= 70:
            sentiment = "strong_intent"
        elif avg_intent_score >= 40:
            sentiment = "medium_intent"
        elif avg_intent_score >= 20:
            sentiment = "weak_intent"
        else:
            sentiment = "no_intent"
        
        return {
            "domain": domain,
            "pages_analyzed": len(results),
            "avg_intent_score": round(avg_intent_score, 1),
            "total_intent_score": round(total_intent_score, 1),
            "overall_sentiment": sentiment,
            "signal_types": dict(signal_types),
            "all_signals": all_signals,
            "technologies": sorted(list(all_technologies)),
            "page_results": results
        }


# ============================================================================
# JOB POSTING ANALYZER
# ============================================================================

class JobPostingAnalyzer:
    """Analyze job postings for intent signals"""
    
    def __init__(self):
        self.content_analyzer = ContentAnalyzer()
        self.service_keywords = SERVICE_KEYWORDS
    
    def analyze_job_posting(self, job_data):
        """Analyze a single job posting"""
        if isinstance(job_data, str):
            job_data = {"title": job_data}
        
        title = job_data.get("title", "")
        description = job_data.get("description", "")
        company = job_data.get("company", "")
        location = job_data.get("location", "")
        
        # Analyze title and description
        title_analysis = self.content_analyzer.analyze_text(title)
        desc_analysis = self.content_analyzer.analyze_text(description)
        
        # Combined score
        combined_score = (title_analysis["intent_score"] * 0.4 + desc_analysis["intent_score"] * 0.6)
        
        # Detect relevant services
        relevant_services = self._detect_relevant_services(title, description)
        
        # Detect technologies
        technologies = detect_technologies(description)
        
        # Determine seniority
        seniority = self._detect_seniority(title, description)
        
        # Determine if hiring for our services
        is_relevant = bool(relevant_services)
        
        return {
            "title": title,
            "company": company,
            "location": location,
            "intent_score": round(combined_score, 1),
            "title_intent": title_analysis,
            "description_intent": desc_analysis,
            "relevant_services": relevant_services,
            "technologies": technologies,
            "seniority": seniority,
            "is_relevant": is_relevant,
            "relevance_score": len(relevant_services) * 20,
            "overall_score": round(combined_score + (len(relevant_services) * 10), 1)
        }
    
    def _detect_relevant_services(self, title, description):
        """Detect which of our services are mentioned"""
        text = f"{title} {description}".lower()
        relevant = []
        
        for service, keywords in self.service_keywords.items():
            for keyword in keywords:
                if keyword.lower() in text:
                    if service not in relevant:
                        relevant.append(service)
                    break
        
        return relevant
    
    def _detect_seniority(self, title, description):
        """Detect seniority level from job posting"""
        text = f"{title} {description}".lower()
        
        seniority_keywords = {
            "intern": 1,
            "junior": 2,
            "mid": 3,
            "senior": 4,
            "lead": 5,
            "principal": 5,
            "manager": 5,
            "director": 6,
            "vp": 7,
            "head of": 7,
            "cto": 8,
            "ceo": 9,
        }
        
        for keyword, level in seniority_keywords.items():
            if keyword in text:
                return list(seniority_keywords.keys())[level - 1]
        
        return "unknown"
    
    def analyze_multiple_jobs(self, jobs):
        """Analyze multiple job postings"""
        results = []
        
        for job in jobs:
            result = self.analyze_job_posting(job)
            results.append(result)
        
        # Aggregate
        total_relevant = sum(1 for r in results if r["is_relevant"])
        avg_intent = sum(r["intent_score"] for r in results) / len(results) if results else 0
        
        # Count by seniority
        seniority_counts = Counter(r["seniority"] for r in results)
        
        # Count by service
        service_counts = Counter()
        for r in results:
            for service in r["relevant_services"]:
                service_counts[service] += 1
        
        return {
            "total_jobs": len(results),
            "relevant_jobs": total_relevant,
            "avg_intent_score": round(avg_intent, 1),
            "seniority_distribution": dict(seniority_counts),
            "service_demand": dict(service_counts),
            "job_details": results
        }


# ============================================================================
# NEWS & BLOG ANALYZER
# ============================================================================

class NewsAnalyzer:
    """Analyze news and blog posts for intent signals"""
    
    def __init__(self):
        self.content_analyzer = ContentAnalyzer()
    
    def analyze_article(self, article_data):
        """Analyze a news article or blog post"""
        if isinstance(article_data, str):
            article_data = {"url": article_data}
        
        url = article_data.get("url")
        title = article_data.get("title", "")
        content = article_data.get("content", "")
        
        # Try to fetch content if not provided
        if content == "" and url:
            try:
                html = http_get(url, timeout=30)
                if html:
                    content = clean_text(html)
            except:
                pass
        
        # Analyze
        result = self.content_analyzer.analyze_text(f"{title} {content}")
        
        # Classify article type
        article_type = self._classify_article(title, content)
        
        return {
            "url": url,
            "title": title,
            "intent_score": result["intent_score"],
            "signals": result["signals"],
            "sentiment": result["sentiment"],
            "article_type": article_type,
            "content_length": len(content)
        }
    
    def _classify_article(self, title, content):
        """Classify article into type"""
        text = f"{title} {content}".lower()
        
        if any(kw in text for kw in ["raised", "funding", "series", "investment", "venture"]):
            return "funding"
        elif any(kw in text for kw in ["acquir", "bought", "purchase", "merge", "acquisition"]):
            return "acquisition"
        elif any(kw in text for kw in ["hire", "hiring", "recruit", "talent"]):
            return "hiring"
        elif any(kw in text for kw in ["launch", "releas", "new product", "announc"]):
            return "product_launch"
        elif any(kw in text for kw in ["growth", "scale", "expand", "new market"]):
            return "growth"
        elif any(kw in text for kw in ["partnership", "collaboration", "joint"]):
            return "partnership"
        else:
            return "general"
    
    def analyze_feed(self, feed_url, limit=10):
        """Analyze an RSS feed or news page"""
        results = []
        
        # Try to fetch and parse feed
        try:
            html = http_get(feed_url, timeout=30)
            if html:
                # Extract article links
                links = extract_links(html, feed_url)
                
                for link in links[:limit]:
                    # Check if it looks like an article
                    if any(ext in link.lower() for ext in ["/blog/", "/news/", "/article/", "?p=", "?id="]):
                        try:
                            article_html = http_get(link, timeout=20)
                            if article_html:
                                title_match = re.search(r"<title>([^<]+)</title>", article_html, re.IGNORECASE)
                                title = clean_text(title_match.group(1)) if title_match else ""
                                
                                content = clean_text(article_html)
                                
                                result = self.analyze_article({
                                    "url": link,
                                    "title": title,
                                    "content": content
                                })
                                results.append(result)
                        except:
                            pass
                        
                        time.sleep(1)  # Be polite
        except:
            pass
        
        return results


# ============================================================================
# SOCIAL MEDIA ANALYZER
# ============================================================================

class SocialMediaAnalyzer:
    """Analyze social media for intent signals"""
    
    def __init__(self):
        self.content_analyzer = ContentAnalyzer()
    
    def analyze_post(self, post_data):
        """Analyze a social media post"""
        text = post_data.get("text", "")
        platform = post_data.get("platform", "unknown")
        author = post_data.get("author", "")
        
        result = self.content_analyzer.analyze_text(text)
        
        # Classify post type
        post_type = self._classify_post(text)
        
        return {
            "platform": platform,
            "author": author,
            "intent_score": result["intent_score"],
            "signals": result["signals"],
            "sentiment": result["sentiment"],
            "post_type": post_type
        }
    
    def _classify_post(self, text):
        """Classify social media post"""
        text_lower = text.lower()
        
        if any(kw in text_lower for kw in ["hire", "hiring", "job", "career"]):
            return "hiring"
        elif any(kw in text_lower for kw in ["announc", "launch", "new"]):
            return "announcement"
        elif any(kw in text_lower for kw in ["thank", "appreciat", "congrat"]):
            return "engagement"
        elif any(kw in text_lower for kw in ["question", "ask", "wonder", "anyone"]):
            return "question"
        elif any(kw in text_lower for kw in ["look for", "need", "recommend"]):
            return "request"
        else:
            return "general"


# ============================================================================
# WEBSITE CHANGE MONITOR
# ============================================================================

class WebsiteChangeMonitor:
    """Monitor websites for changes that indicate intent"""
    
    def __init__(self):
        self.content_analyzer = ContentAnalyzer()
    
    def check_for_changes(self, url, previous_content=None, timeout=30):
        """Check if a website has changed"""
        try:
            current_content = http_get(url, timeout)
            if not current_content:
                return {"error": "Could not fetch page", "url": url}
            
            if not previous_content:
                # First time seeing this page
                return {
                    "url": url,
                    "status": "new",
                    "content_hash": self._hash_content(current_content),
                    "content_length": len(current_content),
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")
                }
            
            # Calculate hash
            current_hash = self._hash_content(current_content)
            previous_hash = self._hash_content(previous_content)
            
            if current_hash == previous_hash:
                return {
                    "url": url,
                    "status": "unchanged",
                    "content_hash": current_hash
                }
            
            # Analyze changes
            changes = self._analyze_changes(previous_content, current_content, url)
            
            return {
                "url": url,
                "status": "changed",
                "content_hash": current_hash,
                "previous_hash": previous_hash,
                "changes": changes,
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")
            }
            
        except Exception as e:
            return {"error": str(e), "url": url}
    
    def _hash_content(self, content):
        """Create a hash of content for comparison"""
        import hashlib
        
        if not content:
            return ""
        
        # Use first 10000 chars for hashing (to save space)
        short_content = content[:10000]
        return hashlib.md5(short_content.encode()).hexdigest()
    
    def _analyze_changes(self, old_content, new_content, url):
        """Analyze what changed between two versions"""
        old_text = clean_text(old_content)
        new_text = clean_text(new_content)
        
        changes = {
            "old_length": len(old_text),
            "new_length": len(new_text),
            "length_diff": len(new_text) - len(old_text),
            "added_keywords": [],
            "removed_keywords": [],
            "intent_change": None
        }
        
        # Analyze both versions
        old_analysis = self.content_analyzer.analyze_text(old_text)
        new_analysis = self.content_analyzer.analyze_text(new_text)
        
        changes["old_intent"] = old_analysis
        changes["new_intent"] = new_analysis
        changes["intent_diff"] = new_analysis["intent_score"] - old_analysis["intent_score"]
        
        # Detect added keywords
        old_keywords = set(s["keyword"] for s in old_analysis["signals"])
        new_keywords = set(s["keyword"] for s in new_analysis["signals"])
        
        changes["added_keywords"] = list(new_keywords - old_keywords)
        changes["removed_keywords"] = list(old_keywords - new_keywords)
        
        # Determine intent change
        if changes["intent_diff"] > 20:
            changes["intent_change"] = "increased"
        elif changes["intent_diff"] < -20:
            changes["intent_change"] = "decreased"
        else:
            changes["intent_change"] = "stable"
        
        return changes
    
    def monitor_website(self, domain, check_pages=None, timeout=30):
        """Monitor a website for changes"""
        if not check_pages:
            check_pages = [
                f"https://{domain}",
                f"https://{domain}/about",
                f"https://{domain}/careers",
                f"https://{domain}/jobs",
                f"https://{domain}/contact",
            ]
        
        results = []
        
        for page_url in check_pages:
            # Load previous content from database
            previous_content = self._load_previous_content(page_url)
            
            result = self.check_for_changes(page_url, previous_content, timeout)
            results.append(result)
            
            # Save current content
            if result.get("content_hash"):
                self._save_content(page_url, result["content_hash"])
            
            time.sleep(2)  # Be polite
        
        return results
    
    def _load_previous_content(self, url):
        """Load previous content hash from database"""
        with finder_conn() as c:
            row = c.execute(
                "SELECT content_hash FROM website_history WHERE url=? ORDER BY timestamp DESC LIMIT 1",
                (url,)
            ).fetchone()
            
            if row and row[0]:
                # Return hash as placeholder for content
                return row[0]
        
        return None
    
    def _save_content(self, url, content_hash):
        """Save content hash to database"""
        with finder_conn() as c:
            c.execute(
                "INSERT INTO website_history (url, content_hash, timestamp) VALUES (?, ?, ?)",
                (url, content_hash, datetime.now(timezone.utc).isoformat(timespec="seconds"))
            )


# ============================================================================
# INTENT SCORING ENGINE
# ============================================================================

class IntentScoringEngine:
    """Comprehensive intent scoring engine"""
    
    def __init__(self):
        self.content_analyzer = ContentAnalyzer()
        self.job_analyzer = JobPostingAnalyzer()
        self.news_analyzer = NewsAnalyzer()
        self.change_monitor = WebsiteChangeMonitor()
    
    def score_lead(self, lead_data, analyze_website=False):
        """Calculate comprehensive intent score for a lead"""
        scores = {}
        weights = {
            "website": 0.3,
            "job_postings": 0.3,
            "news": 0.2,
            "linkedin": 0.2
        }
        
        total_score = 0
        total_weight = 0
        signals = []
        
        # Website analysis
        domain = lead_data.get("domain") or lead_data.get("website")
        if domain and analyze_website:
            website_result = self.content_analyzer.analyze_website(domain)
            if not website_result.get("error"):
                website_score = website_result["avg_intent_score"]
                scores["website"] = website_score
                total_score += website_score * weights["website"]
                total_weight += weights["website"]
                signals.extend(website_result.get("all_signals", []))
        
        # Job postings analysis
        if lead_data.get("job_postings"):
            job_result = self.job_analyzer.analyze_multiple_jobs(lead_data["job_postings"])
            scores["job_postings"] = job_result["avg_intent_score"] * (1 + (job_result["relevant_jobs"] / max(1, job_result["total_jobs"])))
            total_score += scores["job_postings"] * weights["job_postings"]
            total_weight += weights["job_postings"]
        
        # News analysis
        if lead_data.get("news_items"):
            news_scores = []
            for news_item in lead_data["news_items"]:
                news_result = self.news_analyzer.analyze_article(news_item)
                news_scores.append(news_result["intent_score"])
            
            avg_news_score = sum(news_scores) / len(news_scores) if news_scores else 0
            scores["news"] = avg_news_score
            total_score += avg_news_score * weights["news"]
            total_weight += weights["news"]
        
        # LinkedIn analysis
        if lead_data.get("linkedin_data"):
            # Analyze company description
            desc = lead_data["linkedin_data"].get("description", "")
            linkedin_result = self.content_analyzer.analyze_text(desc)
            scores["linkedin"] = linkedin_result["intent_score"]
            total_score += scores["linkedin"] * weights["linkedin"]
            total_weight += weights["linkedin"]
            signals.extend(linkedin_result.get("signals", []))
        
        # Calculate final score
        if total_weight > 0:
            final_score = (total_score / total_weight) * 100
        else:
            final_score = 0
        
        # Add decision maker bonus
        if lead_data.get("is_decision_maker"):
            final_score = min(100, final_score + 15)
        
        # Add hiring bonus
        if lead_data.get("hiring", False) or lead_data.get("job_openings", 0) > 0:
            hiring_bonus = min(20, lead_data.get("job_openings", 0) * 2)
            final_score = min(100, final_score + hiring_bonus)
        
        # Add growth bonus
        growth_score = lead_data.get("growth_score", 0)
        if growth_score > 70:
            final_score = min(100, final_score + 10)
        elif growth_score > 50:
            final_score = min(100, final_score + 5)
        
        # Determine intent level
        if final_score >= 80:
            intent_level = "hot"
        elif final_score >= 60:
            intent_level = "warm"
        elif final_score >= 40:
            intent_level = "cool"
        else:
            intent_level = "cold"
        
        return {
            "final_score": round(final_score, 1),
            "intent_level": intent_level,
            "component_scores": scores,
            "signals": signals,
            "recommendation": self._get_recommendation(final_score, intent_level)
        }
    
    def score_batch(self, leads, analyze_website=False):
        """Score a batch of leads"""
        results = {}
        
        for lead_id, lead_data in leads.items():
            results[lead_id] = self.score_lead(lead_data, analyze_website)
        
        return results
    
    def _get_recommendation(self, score, intent_level):
        """Get action recommendation based on intent score"""
        if intent_level == "hot":
            return {
                "priority": "immediate",
                "action": "Contact within 24 hours",
                "message": "This lead shows strong buying intent. Reach out immediately with a personalized message."
            }
        elif intent_level == "warm":
            return {
                "priority": "high",
                "action": "Contact within 1 week",
                "message": "This lead shows moderate intent. Nurture with valuable content and follow up within a week."
            }
        elif intent_level == "cool":
            return {
                "priority": "medium",
                "action": "Add to nurture campaign",
                "message": "This lead shows some interest. Add to your email nurture sequence and check back in 2-4 weeks."
            }
        else:
            return {
                "priority": "low",
                "action": "Monitor only",
                "message": "This lead shows little intent. Monitor for changes but don't prioritize outreach."
            }


# ============================================================================
# CHANGE DETECTION INITIALIZATION
# ============================================================================

def init_intent_database():
    """Initialize intent-related database tables"""
    with finder_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS website_history (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            intent_score INT,
            UNIQUE(url, content_hash)
        );
        
        CREATE TABLE IF NOT EXISTS intent_signals (
            id INTEGER PRIMARY KEY,
            lead_id INT,
            domain TEXT,
            signal_type TEXT NOT NULL,
            keyword TEXT,
            context TEXT,
            score INT NOT NULL,
            strength TEXT,
            source TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        );
        
        CREATE TABLE IF NOT EXISTS intent_scores (
            id INTEGER PRIMARY KEY,
            lead_id INT UNIQUE,
            domain TEXT,
            overall_score REAL NOT NULL,
            intent_level TEXT NOT NULL,
            component_scores TEXT,
            last_updated TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        );
        
        CREATE TABLE IF NOT EXISTS job_postings (
            id INTEGER PRIMARY KEY,
            company_domain TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            url TEXT,
            posted_date TEXT,
            intent_score REAL,
            relevance_score REAL,
            detected_at TEXT NOT NULL
        );
        """)


# ============================================================================
# EXPORTS
# ============================================================================

# Initialize database
init_intent_database()

# Create singleton instances
content_analyzer = ContentAnalyzer()
job_analyzer = JobPostingAnalyzer()
news_analyzer = NewsAnalyzer()
social_analyzer = SocialMediaAnalyzer()
change_monitor = WebsiteChangeMonitor()
intent_engine = IntentScoringEngine()


def analyze_website_intent(domain):
    """Convenience function to analyze website intent"""
    return content_analyzer.analyze_website(domain)


def score_lead_intent(lead_data):
    """Convenience function to score lead intent"""
    return intent_engine.score_lead(lead_data)


if __name__ == "__main__":
    print("AI Intent Detection System - Testing")
    print("=" * 50)
    
    # Test content analyzer
    print("\n1. Testing content analyzer...")
    analyzer = ContentAnalyzer()
    
    test_text = """
    We are looking for a SaaS development partner to help us build 
    our next-generation AI platform. We need a team that can deliver 
    quickly and has experience with machine learning and automation.
    Our current system is outdated and we need to modernize it ASAP.
    """
    
    result = analyzer.analyze_text(test_text)
    print(f"   Intent score: {result['intent_score']}")
    print(f"   Sentiment: {result['sentiment']}")
    print(f"   Signals found: {len(result['signals'])}")
    for signal in result['signals'][:3]:
        print(f"   - {signal['keyword']} ({signal['type']}): {signal['score']}")
    
    # Test scoring engine
    print("\n2. Testing scoring engine...")
    lead_data = {
        "domain": "example.com",
        "company": "Example Corp",
        "description": "We need to modernize our software platform and are looking for a development partner.",
        "hiring": True,
        "job_openings": 5,
        "growth_score": 80,
        "is_decision_maker": True
    }
    
    score_result = intent_engine.score_lead(lead_data)
    print(f"   Overall intent score: {score_result['final_score']}")
    print(f"   Intent level: {score_result['intent_level']}")
    print(f"   Recommendation: {score_result['recommendation']['action']}")
