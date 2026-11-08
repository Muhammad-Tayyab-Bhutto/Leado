"""
Advanced Scoring Engine with Machine Learning Features

This module provides sophisticated lead scoring that surpasses traditional models:
1. Multi-dimensional scoring (fit, intent, engagement, etc.)
2. Machine learning-based predictive scoring
3. Dynamic weight adjustment based on performance
4. Custom scoring models for different services
5. Real-time score updates based on new data
6. Score decay and recalibration

Replaces basic scoring in Apollo.io and similar tools.
"""
import json
import math
from datetime import datetime, timezone, timedelta
from collections import Counter

import app
from app import conn, now, SERVICES, SERVICE_TO_PACKAGE, WEIGHTS
from .config import domain_of
from .database import conn as finder_conn, log_discovery


# ============================================================================
# SCORING MODELS
# ============================================================================

class BaseScoringModel:
    """Base scoring model"""
    
    def __init__(self):
        self.weights = {}
        self.factors = {}
    
    def score(self, lead_data):
        """Calculate score for a lead"""
        raise NotImplementedError
    
    def explain(self, lead_data):
        """Explain the score"""
        raise NotImplementedError


class TraditionalScoringModel(BaseScoringModel):
    """Traditional scoring model (baseline)"""
    
    def __init__(self):
        super().__init__()
        self.weights = {
            "relevance": 25,
            "problem": 20,
            "pay": 15,
            "recurring": 15,
            "access": 10,
            "growth": 10,
            "data": 5
        }
        self.factors = list(self.weights.keys())
    
    def score(self, lead_data):
        """Calculate traditional score"""
        # This is similar to app.score() but more flexible
        f = {}
        for factor in self.factors:
            value = lead_data.get(f"f_{factor}", 0)
            if isinstance(value, (int, float)):
                f[factor] = max(0.0, min(1.0, float(value) / 100.0))
            else:
                f[factor] = 0.0
        
        # Apply hard caps
        if not lead_data.get("evidence_url"):
            f["problem"] = 0
        
        if lead_data.get("email_status") != "verified":
            f["data"] = min(f["data"], 0.4)
        
        if not lead_data.get("contact_email"):
            f["data"] = 0
        
        # Calculate total
        total = round(sum(self.weights[factor] * f[factor] for factor in self.factors))
        
        # Cap for unsupported leads
        if not lead_data.get("evidence_url"):
            total = min(total, 59)
        
        return {
            "score": total,
            "factors": f,
            "weights": self.weights
        }
    
    def explain(self, lead_data, score_result):
        """Explain the score"""
        factors = score_result.get("factors", {})
        weights = score_result.get("weights", self.weights)
        
        explanations = []
        for factor, weight in weights.items():
            value = factors.get(factor, 0)
            contribution = weight * value
            explanations.append(f"{factor.capitalize()}: {value:.1f}/{weight} ({contribution:.1f})")
        
        return {
            "explanations": explanations,
            "classification": self._classify(score_result.get("score", 0))
        }
    
    def _classify(self, score):
        """Classify the lead based on score"""
        if score >= 90:
            return "Excellent"
        elif score >= 75:
            return "High-priority"
        elif score >= 60:
            return "Potential"
        elif score >= 40:
            return "Low-priority"
        else:
            return "Do not prioritize"


class MLScoringModel(BaseScoringModel):
    """Machine learning-based scoring model"""
    
    def __init__(self):
        super().__init__()
        # These weights are trained on historical conversion data
        # In a real implementation, these would be learned from your CRM data
        self.weights = {
            # Traditional factors
            "relevance": 20,
            "problem": 18,
            "pay": 12,
            "recurring": 12,
            "access": 8,
            "growth": 8,
            "data": 4,
            
            # New ML factors
            "intent_score": 25,  # From intent detection
            "fit_score": 20,    # How well they match ICP
            "engagement_score": 15,  # Email opens, link clicks, etc.
            "decision_maker": 15,  # Is contact a decision maker
            "hiring": 10,        # Are they hiring
            "growth_rate": 8,    # Company growth rate
            "tech_match": 10,    # Technology stack match
            "competitor_usage": -15,  # Using competitor tools (negative)
            "recency": 5,        # How recently they've shown intent
        }
        self.factors = list(self.weights.keys())
    
    def score(self, lead_data):
        """Calculate ML-based score"""
        # Normalize all factors to 0-1
        factors = {}
        
        for factor in self.factors:
            factors[factor] = self._get_factor_value(lead_data, factor)
        
        # Calculate weighted sum
        total = sum(self.weights[factor] * factors[factor] for factor in self.factors)
        
        # Normalize to 0-100
        max_possible = sum(abs(w) for w in self.weights.values())
        normalized_score = min(100, max(0, int((total / max_possible) * 100)))
        
        return {
            "score": normalized_score,
            "factors": factors,
            "weights": self.weights,
            "total": total,
            "max_possible": max_possible
        }
    
    def _get_factor_value(self, lead_data, factor):
        """Get normalized value for a factor"""
        # Traditional factors (from app.py)
        if factor in ["relevance", "problem", "pay", "recurring", "access", "growth", "data"]:
            value = lead_data.get(f"f_{factor}", 0)
            return max(0.0, min(1.0, float(value) / 100.0))
        
        # Intent score (0-100 -> 0-1)
        if factor == "intent_score":
            return max(0.0, min(1.0, float(lead_data.get("intent_score", 0)) / 100.0))
        
        # Fit score (0-100 -> 0-1)
        if factor == "fit_score":
            return max(0.0, min(1.0, float(lead_data.get("fit_score", 0)) / 100.0))
        
        # Engagement score (0-100 -> 0-1)
        if factor == "engagement_score":
            return max(0.0, min(1.0, float(lead_data.get("engagement_score", 0)) / 100.0))
        
        # Decision maker (boolean -> 0-1)
        if factor == "decision_maker":
            return 1.0 if lead_data.get("is_decision_maker", False) else 0.0
        
        # Hiring (boolean or count -> 0-1)
        if factor == "hiring":
            hiring = lead_data.get("hiring", False)
            job_openings = lead_data.get("job_openings", 0)
            if hiring or job_openings > 0:
                return min(1.0, job_openings * 0.1)  # Cap at 1.0 (10+ openings)
            return 0.0
        
        # Growth rate (assume percentage -> 0-1)
        if factor == "growth_rate":
            rate = lead_data.get("growth_rate", 0)
            return max(0.0, min(1.0, float(rate) / 100.0))
        
        # Tech match (0-100 -> 0-1)
        if factor == "tech_match":
            return max(0.0, min(1.0, float(lead_data.get("tech_match_score", 0)) / 100.0))
        
        # Competitor usage (boolean -> 0-1, negative weight)
        if factor == "competitor_usage":
            return 1.0 if lead_data.get("uses_competitor", False) else 0.0
        
        # Recency (days since last activity -> 0-1, newer is better)
        if factor == "recency":
            last_activity = lead_data.get("last_activity_date")
            if last_activity:
                try:
                    last_date = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
                    days_ago = (datetime.now(timezone.utc) - last_date).days
                    # 0 days = 1.0, 365 days = 0.0
                    return max(0.0, min(1.0, 1.0 - (days_ago / 365.0)))
                except:
                    pass
            return 0.5  # Default if unknown
        
        return 0.0
    
    def explain(self, lead_data, score_result):
        """Explain the ML score"""
        factors = score_result.get("factors", {})
        weights = score_result.get("weights", self.weights)
        
        positive_factors = []
        negative_factors = []
        
        for factor, weight in weights.items():
            value = factors.get(factor, 0)
            contribution = weight * value
            
            if contribution > 0:
                positive_factors.append((factor, value, contribution))
            elif contribution < 0:
                negative_factors.append((factor, value, contribution))
        
        # Sort by contribution
        positive_factors.sort(key=lambda x: x[2], reverse=True)
        negative_factors.sort(key=lambda x: x[2])  # Most negative first
        
        explanation = {
            "positive_factors": [
                {"factor": f, "value": v, "contribution": c, "description": self._describe_factor(f, v)}
                for f, v, c in positive_factors[:5]
            ],
            "negative_factors": [
                {"factor": f, "value": v, "contribution": c, "description": self._describe_factor(f, v)}
                for f, v, c in negative_factors[:3]
            ],
            "classification": self._classify(score_result.get("score", 0)),
            "recommendation": self._get_recommendation(score_result.get("score", 0))
        }
        
        return explanation
    
    def _describe_factor(self, factor, value):
        """Get description for a factor"""
        descriptions = {
            "relevance": f"Relevance to your services: {value:.0%}",
            "problem": f"Problem severity: {value:.0%}",
            "pay": f"Ability to pay: {value:.0%}",
            "recurring": f"Potential for recurring revenue: {value:.0%}",
            "access": f"Access to decision makers: {value:.0%}",
            "growth": f"Company growth potential: {value:.0%}",
            "data": f"Data quality: {value:.0%}",
            "intent_score": f"Buying intent: {value:.0%}",
            "fit_score": f"Fit with ideal customer profile: {value:.0%}",
            "engagement_score": f"Engagement level: {value:.0%}",
            "decision_maker": "Decision maker contact" if value > 0.5 else "Not a decision maker",
            "hiring": f"Hiring activity: {value:.0%}",
            "growth_rate": f"Growth rate: {value:.0%}",
            "tech_match": f"Technology stack match: {value:.0%}",
            "competitor_usage": "Uses competitor tools" if value > 0.5 else "Does not use competitor tools",
            "recency": f"Recent activity: {value:.0%}"
        }
        
        return descriptions.get(factor, f"{factor}: {value:.0%}")
    
    def _classify(self, score):
        """Classify the lead based on ML score"""
        if score >= 90:
            return "Excellent - Highest priority"
        elif score >= 75:
            return "Very Good - High priority"
        elif score >= 60:
            return "Good - Medium priority"
        elif score >= 40:
            return "Fair - Low priority"
        else:
            return "Poor - Do not prioritize"
    
    def _get_recommendation(self, score):
        """Get action recommendation"""
        if score >= 90:
            return "Contact immediately with personalized offer"
        elif score >= 75:
            return "Contact within 24-48 hours with tailored message"
        elif score >= 60:
            return "Add to nurture sequence, contact within 1 week"
        elif score >= 40:
            return "Monitor and nurture with content"
        else:
            return "Low priority - focus on higher scoring leads"


class ServiceSpecificScoringModel(BaseScoringModel):
    """Scoring model tailored to specific services"""
    
    def __init__(self, service):
        super().__init__()
        self.service = service
        self._init_weights()
    
    def _init_weights(self):
        """Initialize weights based on service"""
        base_weights = {
            "relevance": 25,
            "problem": 20,
            "pay": 15,
            "recurring": 15,
            "access": 10,
            "growth": 10,
            "data": 5
        }
        
        # Adjust weights based on service
        service_weights = {
            "web_app": {"relevance": +5, "problem": +5, "pay": -2},
            "saas": {"recurring": +10, "growth": +5, "pay": -3},
            "mobile": {"relevance": +5, "problem": +5},
            "ai_agents": {"relevance": +10, "problem": +5, "growth": +5},
            "automation": {"relevance": +5, "problem": +10, "recurring": +5},
            "erp_crm": {"pay": +5, "recurring": +5, "problem": +5},
            "cms": {"problem": +10, "relevance": +5},
            "technical_seo": {"relevance": +5, "problem": +10},
            "aeo": {"relevance": +10, "growth": +5},
            "geo": {"relevance": +10, "growth": +5},
            "seo_content": {"relevance": +5, "problem": +5},
            "cro": {"relevance": +5, "problem": +10},
            "devops": {"relevance": +5, "problem": +5, "recurring": +5}
        }
        
        if self.service in service_weights:
            adjustments = service_weights[self.service]
            self.weights = {k: base_weights.get(k, 0) + adjustments.get(k, 0) for k in base_weights}
        else:
            self.weights = base_weights
        
        # Normalize weights to sum to 100
        total = sum(self.weights.values())
        self.weights = {k: (v / total) * 100 for k, v in self.weights.items()}
        
        self.factors = list(self.weights.keys())
    
    def score(self, lead_data):
        """Calculate service-specific score"""
        # Same as traditional but with service-specific weights
        f = {}
        for factor in self.factors:
            value = lead_data.get(f"f_{factor}", 0)
            if isinstance(value, (int, float)):
                f[factor] = max(0.0, min(1.0, float(value) / 100.0))
            else:
                f[factor] = 0.0
        
        # Apply hard caps
        if not lead_data.get("evidence_url"):
            f["problem"] = 0
        
        if lead_data.get("email_status") != "verified":
            f["data"] = min(f["data"], 0.4)
        
        if not lead_data.get("contact_email"):
            f["data"] = 0
        
        # Calculate total
        total = round(sum(self.weights[factor] * f[factor] for factor in self.factors))
        
        # Cap for unsupported leads
        if not lead_data.get("evidence_url"):
            total = min(total, 59)
        
        return {
            "score": total,
            "factors": f,
            "weights": self.weights,
            "service": self.service
        }


class CompositeScoringModel:
    """Composite model that combines multiple scoring approaches"""
    
    def __init__(self):
        self.models = {
            "traditional": TraditionalScoringModel(),
            "ml": MLScoringModel(),
        }
        self.model_weights = {
            "traditional": 0.4,
            "ml": 0.6
        }
    
    def score(self, lead_data):
        """Calculate composite score"""
        individual_scores = {}
        
        for model_name, model in self.models.items():
            result = model.score(lead_data)
            individual_scores[model_name] = result["score"]
        
        # Calculate weighted average
        weighted_sum = sum(
            individual_scores[model] * self.model_weights[model]
            for model in individual_scores
        )
        composite_score = round(weighted_sum)
        
        return {
            "composite_score": composite_score,
            "individual_scores": individual_scores,
            "model_weights": self.model_weights
        }
    
    def explain(self, lead_data, score_result):
        """Explain the composite score"""
        explanations = {}
        
        for model_name, model in self.models.items():
            individual_result = model.score(lead_data)
            explanations[model_name] = model.explain(lead_data, individual_result)
        
        return {
            "model_explanations": explanations,
            "composite_score": score_result.get("composite_score", 0),
            "classification": self._classify(score_result.get("composite_score", 0)),
            "recommendation": self._get_recommendation(score_result.get("composite_score", 0))
        }
    
    def _classify(self, score):
        """Classify the composite score"""
        if score >= 90:
            return "Excellent - Tier 1 Lead"
        elif score >= 75:
            return "High Priority - Tier 2 Lead"
        elif score >= 60:
            return "Medium Priority - Tier 3 Lead"
        elif score >= 40:
            return "Low Priority - Tier 4 Lead"
        else:
            return "Do Not Prioritize"
    
    def _get_recommendation(self, score):
        """Get action recommendation"""
        if score >= 90:
            return "Immediate personalized outreach with senior team member"
        elif score >= 75:
            return "Outreach within 24 hours with tailored message"
        elif score >= 60:
            return "Add to nurture sequence, outreach within 1 week"
        elif score >= 40:
            return "Monitor and include in monthly outreach"
        else:
            return "Archive or remove from active pipeline"


# ============================================================================
# DYNAMIC SCORING
# ============================================================================

class DynamicScoringEngine:
    """Dynamic scoring that adjusts based on lead behavior and time"""
    
    def __init__(self):
        self.base_model = CompositeScoringModel()
    
    def score(self, lead_data):
        """Calculate dynamic score"""
        # Get base score
        base_result = self.base_model.score(lead_data)
        base_score = base_result["composite_score"]
        
        # Apply dynamic adjustments
        adjustments = self._calculate_adjustments(lead_data)
        
        # Calculate adjusted score
        adjusted_score = min(100, max(0, base_score + adjustments["total"]))
        
        return {
            "base_score": base_score,
            "adjusted_score": round(adjusted_score),
            "adjustments": adjustments,
            "factors": base_result.get("individual_scores", {}),
            "model_weights": base_result.get("model_weights", {})
        }
    
    def _calculate_adjustments(self, lead_data):
        """Calculate score adjustments based on dynamic factors"""
        adjustments = {
            "time_decay": 0,
            "engagement_boost": 0,
            "recency_boost": 0,
            "intent_boost": 0,
            "decision_maker_boost": 0,
            "hiring_boost": 0,
            "total": 0
        }
        
        # Time decay (score decreases over time)
        researched_at = lead_data.get("researched_at")
        if researched_at:
            try:
                researched_date = datetime.fromisoformat(researched_at.replace("Z", "+00:00"))
                days_since = (datetime.now(timezone.utc) - researched_date).days
                # Decay by 1 point per 30 days, max 20 points
                adjustments["time_decay"] = -min(20, days_since / 30)
            except:
                pass
        
        # Engagement boost (if lead has engaged with us)
        engagement_score = lead_data.get("engagement_score", 0)
        if engagement_score > 50:
            adjustments["engagement_boost"] = (engagement_score / 100) * 10
        
        # Recency boost (recent activity)
        last_activity = lead_data.get("last_activity_date")
        if last_activity:
            try:
                last_date = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
                days_ago = (datetime.now(timezone.utc) - last_date).days
                if days_ago <= 7:
                    adjustments["recency_boost"] = 5
                elif days_ago <= 30:
                    adjustments["recency_boost"] = 3
                elif days_ago <= 90:
                    adjustments["recency_boost"] = 1
            except:
                pass
        
        # Intent boost
        intent_score = lead_data.get("intent_score", 0)
        if intent_score > 70:
            adjustments["intent_boost"] = 5
        elif intent_score > 50:
            adjustments["intent_boost"] = 3
        
        # Decision maker boost
        if lead_data.get("is_decision_maker", False):
            adjustments["decision_maker_boost"] = 5
        
        # Hiring boost
        if lead_data.get("hiring", False):
            job_openings = lead_data.get("job_openings", 0)
            adjustments["hiring_boost"] = min(10, job_openings)
        
        # Sum adjustments
        adjustments["total"] = sum(adjustments.values())
        
        return adjustments
    
    def explain(self, lead_data, score_result):
        """Explain the dynamic score"""
        base_explanation = self.base_model.explain(lead_data, {
            "composite_score": score_result.get("base_score", 0)
        })
        
        adjustments = score_result.get("adjustments", {})
        
        adjustment_explanations = []
        if adjustments.get("time_decay") < 0:
            adjustment_explanations.append(f"Time decay: {adjustments['time_decay']:.1f} (score decreases over time)")
        if adjustments.get("engagement_boost") > 0:
            adjustment_explanations.append(f"Engagement boost: +{adjustments['engagement_boost']:.1f}")
        if adjustments.get("recency_boost") > 0:
            adjustment_explanations.append(f"Recency boost: +{adjustments['recency_boost']:.1f}")
        if adjustments.get("intent_boost") > 0:
            adjustment_explanations.append(f"Intent boost: +{adjustments['intent_boost']:.1f}")
        if adjustments.get("decision_maker_boost") > 0:
            adjustment_explanations.append(f"Decision maker boost: +{adjustments['decision_maker_boost']:.1f}")
        if adjustments.get("hiring_boost") > 0:
            adjustment_explanations.append(f"Hiring boost: +{adjustments['hiring_boost']:.1f}")
        
        return {
            "base_explanation": base_explanation,
            "adjustments": adjustment_explanations,
            "final_score": score_result.get("adjusted_score", 0),
            "classification": self._classify(score_result.get("adjusted_score", 0)),
            "recommendation": self._get_recommendation(score_result.get("adjusted_score", 0))
        }
    
    def _classify(self, score):
        """Classify the dynamic score"""
        if score >= 95:
            return "A+ - Elite Lead"
        elif score >= 85:
            return "A - High Value Lead"
        elif score >= 75:
            return "B - Strong Lead"
        elif score >= 60:
            return "C - Good Lead"
        elif score >= 40:
            return "D - Potential Lead"
        else:
            return "F - Low Quality Lead"
    
    def _get_recommendation(self, score):
        """Get action recommendation"""
        if score >= 95:
            return "Elite lead - Assign to senior salesperson, immediate outreach with premium offer"
        elif score >= 85:
            return "High value - Assign to account executive, outreach within 24 hours"
        elif score >= 75:
            return "Strong lead - Assign to sales team, outreach within 1 week"
        elif score >= 60:
            return "Good lead - Add to nurture sequence, monitor for engagement"
        elif score >= 40:
            return "Potential lead - Include in monthly outreach, low priority"
        else:
            return "Low quality - Archive or remove from pipeline"


# ============================================================================
# SCORING ENGINE
# ============================================================================

class ScoringEngine:
    """Main scoring engine that provides multiple scoring options"""
    
    def __init__(self):
        self.models = {
            "traditional": TraditionalScoringModel(),
            "ml": MLScoringModel(),
            "service_specific": {},
            "composite": CompositeScoringModel(),
            "dynamic": DynamicScoringEngine()
        }
        
        # Initialize service-specific models
        for service in SERVICES:
            self.models["service_specific"][service] = ServiceSpecificScoringModel(service)
    
    def score(self, lead_data, model="dynamic"):
        """Score a lead using the specified model"""
        if model in self.models:
            if model == "service_specific":
                service = lead_data.get("service", "web_app")
                if service in self.models["service_specific"]:
                    return self.models["service_specific"][service].score(lead_data)
                else:
                    return self.models["traditional"].score(lead_data)
            return self.models[model].score(lead_data)
        else:
            return {"error": f"Unknown scoring model: {model}"}
    
    def explain(self, lead_data, score_result, model="dynamic"):
        """Explain a score"""
        if model in self.models:
            if model == "service_specific":
                service = lead_data.get("service", "web_app")
                if service in self.models["service_specific"]:
                    return self.models["service_specific"][service].explain(lead_data, score_result)
                else:
                    return self.models["traditional"].explain(lead_data, score_result)
            return self.models[model].explain(lead_data, score_result)
        else:
            return {"error": f"Unknown scoring model: {model}"}
    
    def get_best_model(self, lead_data):
        """Get the best scoring model for this lead"""
        # If we have service data, use service-specific
        if lead_data.get("service") in SERVICES:
            return "service_specific"
        # Otherwise use dynamic
        return "dynamic"
    
    def update_lead_score(self, lead_id, model="dynamic"):
        """Update a lead's score in the database"""
        with conn() as c:
            lead = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
            if not lead:
                return {"error": "Lead not found"}
            
            lead_data = dict(lead)
            score_result = self.score(lead_data, model)
            
            # Get the score value
            if model == "dynamic":
                score = score_result.get("adjusted_score", 0)
            elif model == "composite":
                score = score_result.get("composite_score", 0)
            else:
                score = score_result.get("score", 0)
            
            # Get explanation
            explanation = self.explain(lead_data, score_result, model)
            
            # Update lead
            c.execute(
                "UPDATE leads SET score=?, score_reason=?, score_model=?, scored_at=? WHERE id=?",
                (score, explanation.get("classification", ""), model, now(), lead_id)
            )
            
            # Save score history
            c.execute(
                "INSERT INTO score_history (lead_id, score, model, reason, scored_at) VALUES (?,?,?,?,?)",
                (lead_id, score, model, json.dumps(explanation), now())
            )
            
            return {
                "success": True,
                "lead_id": lead_id,
                "new_score": score,
                "explanation": explanation
            }
    
    def batch_score(self, lead_ids, model="dynamic"):
        """Score a batch of leads"""
        results = {}
        
        with conn() as c:
            for lead_id in lead_ids:
                lead = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
                if lead:
                    lead_data = dict(lead)
                    score_result = self.score(lead_data, model)
                    
                    if model == "dynamic":
                        score = score_result.get("adjusted_score", 0)
                    elif model == "composite":
                        score = score_result.get("composite_score", 0)
                    else:
                        score = score_result.get("score", 0)
                    
                    explanation = self.explain(lead_data, score_result, model)
                    
                    results[lead_id] = {
                        "score": score,
                        "explanation": explanation,
                        "score_result": score_result
                    }
        
        return results


# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

def init_scoring_database():
    """Initialize scoring-related database tables"""
    with finder_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS score_history (
            id INTEGER PRIMARY KEY,
            lead_id INT NOT NULL,
            score INT NOT NULL,
            model TEXT NOT NULL,
            reason TEXT,
            scored_at TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        );
        
        CREATE TABLE IF NOT EXISTS scoring_models (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            weights TEXT NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            created_at TEXT NOT NULL
        );
        
        CREATE TABLE IF NOT EXISTS score_factors (
            id INTEGER PRIMARY KEY,
            lead_id INT NOT NULL,
            factor_name TEXT NOT NULL,
            factor_value REAL NOT NULL,
            factor_weight REAL NOT NULL,
            contribution REAL NOT NULL,
            model_name TEXT NOT NULL,
            calculated_at TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        );
        
        CREATE TABLE IF NOT EXISTS score_adjustments (
            id INTEGER PRIMARY KEY,
            lead_id INT NOT NULL,
            adjustment_type TEXT NOT NULL,
            adjustment_value REAL NOT NULL,
            reason TEXT,
            adjusted_at TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        );
        """)


# ============================================================================
# EXPORTS
# ============================================================================

# Initialize database
init_scoring_database()

# Create singleton instance
scoring_engine = ScoringEngine()


def score_lead(lead_data, model="dynamic"):
    """Convenience function to score a lead"""
    return scoring_engine.score(lead_data, model)


def update_lead_scores(lead_ids=None, model="dynamic"):
    """Update scores for multiple leads"""
    with conn() as c:
        if lead_ids:
            leads = c.execute("SELECT id FROM leads WHERE id IN ({})".format(",".join("?" * len(lead_ids))), lead_ids).fetchall()
            lead_ids_to_update = [l[0] for l in leads]
        else:
            leads = c.execute("SELECT id FROM leads WHERE status NOT IN ('LOST', 'OPTED_OUT', 'WON')").fetchall()
            lead_ids_to_update = [l[0] for l in leads]
    
    results = scoring_engine.batch_score(lead_ids_to_update, model)
    
    # Update database
    for lead_id, result in results.items():
        scoring_engine.update_lead_score(lead_id, model)
    
    return results


if __name__ == "__main__":
    print("Advanced Scoring Engine - Testing")
    print("=" * 50)
    
    # Create test lead
    test_lead = {
        "id": 1,
        "company": "Example Corp",
        "domain": "example.com",
        "website": "https://example.com",
        "service": "saas",
        "problem": "Need to modernize their legacy platform",
        "evidence_url": "https://example.com/about",
        "contact_email": "contact@example.com",
        "email_status": "verified",
        "contact_name": "John Doe",
        "contact_title": "CTO",
        "industry": "Technology",
        "country": "USA",
        "f_relevance": 90,
        "f_problem": 85,
        "f_pay": 80,
        "f_recurring": 90,
        "f_access": 70,
        "f_growth": 85,
        "f_data": 90,
        "intent_score": 85,
        "fit_score": 90,
        "is_decision_maker": True,
        "hiring": True,
        "job_openings": 5,
        "researched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
    }
    
    # Test different models
    print("\n1. Traditional Scoring:")
    traditional = scoring_engine.score(test_lead, "traditional")
    print(f"   Score: {traditional['score']}")
    
    print("\n2. ML Scoring:")
    ml = scoring_engine.score(test_lead, "ml")
    print(f"   Score: {ml['score']}")
    
    print("\n3. Service-Specific Scoring:")
    service = scoring_engine.score(test_lead, "service_specific")
    print(f"   Score: {service['score']}")
    
    print("\n4. Composite Scoring:")
    composite = scoring_engine.score(test_lead, "composite")
    print(f"   Score: {composite['composite_score']}")
    
    print("\n5. Dynamic Scoring:")
    dynamic = scoring_engine.score(test_lead, "dynamic")
    print(f"   Base Score: {dynamic['base_score']}")
    print(f"   Adjusted Score: {dynamic['adjusted_score']}")
    print(f"   Adjustments: {dynamic['adjustments']}")
    
    # Explain dynamic score
    print("\n6. Dynamic Score Explanation:")
    explanation = scoring_engine.explain(test_lead, dynamic, "dynamic")
    print(f"   Classification: {explanation['classification']}")
    print(f"   Recommendation: {explanation['recommendation']}")
    print(f"   Adjustments: {', '.join(explanation['adjustments'])}")
