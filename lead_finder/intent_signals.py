"""Buying-intent signal detection from public company text (no LinkedIn scraping).

LinkedIn scraping was removed: it violates LinkedIn's User Agreement. The
company/employee "scraper" objects remain as disabled stubs so callers degrade
gracefully; contacts come from the prospect's own website or manual entry.
"""
import re
from datetime import datetime, timezone, timedelta


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


class _Disabled:
    """LinkedIn data collection is intentionally disabled."""
    def scrape_company(self, *a, **k): return {"error": "LinkedIn scraping disabled (ToS compliance)"}
    def scrape_profile(self, *a, **k): return {"error": "LinkedIn scraping disabled (ToS compliance)"}
    def find_employees(self, *a, **k): return {"employees": [], "decision_makers": []}
    def find_decision_makers(self, *a, **k): return {"employees": [], "decision_makers": []}
    def find_contact_info(self, *a, **k): return {}


linkedin_company_scraper = _Disabled()
linkedin_employee_discoverer = _Disabled()
intent_signal_detector = IntentSignalDetector()
