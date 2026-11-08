"""
Real-time Monitoring & Webhooks System

This module provides:
1. Continuous website monitoring for changes
2. Lead behavior tracking
3. Email engagement monitoring
4. Intent signal alerts
5. Webhook integrations (Slack, Zapier, etc.)
6. Real-time notifications
7. Scheduled reports

Replaces monitoring features in Apollo.io and similar tools.
"""
import re
import json
import time
import smtplib
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import UA, domain_of
from .http_utils import http_get, clean_text
from .database import conn as finder_conn, log_discovery
import app
from app import conn, now


# ============================================================================
# ALERT MANAGER
# ============================================================================

class AlertManager:
    """Manage alerts and notifications"""
    
    def __init__(self):
        self.webhooks = {}
        self.notification_preferences = {}
        self.load_config()
    
    def load_config(self):
        """Load configuration from database"""
        with finder_conn() as c:
            # Load webhooks
            rows = c.execute("SELECT * FROM monitoring_webhooks").fetchall()
            for row in rows:
                self.webhooks[row["id"]] = dict(row)
            
            # Load notification preferences
            rows = c.execute("SELECT * FROM notification_preferences").fetchall()
            for row in rows:
                self.notification_preferences[row["user_id"]] = dict(row)
    
    def create_alert(self, alert_type, title, message, data=None, severity="medium"):
        """Create an alert"""
        alert = {
            "id": self._generate_id(),
            "type": alert_type,
            "title": title,
            "message": message,
            "data": data or {},
            "severity": severity,
            "status": "new",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
        }
        
        # Save to database
        with finder_conn() as c:
            c.execute(
                "INSERT INTO alerts (id, type, title, message, data, severity, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (alert["id"], alert["type"], alert["title"], alert["message"],
                 json.dumps(alert["data"]), alert["severity"], alert["status"],
                 alert["created_at"], alert["updated_at"])
            )
        
        # Send notifications
        self._send_notifications(alert)
        
        return alert
    
    def _generate_id(self):
        """Generate a unique ID"""
        import random
        return f"alert_{random.randint(10000000, 99999999)}"
    
    def _send_notifications(self, alert):
        """Send notifications for an alert"""
        # Email notifications
        self._send_email_notifications(alert)
        
        # Webhook notifications
        self._send_webhook_notifications(alert)
        
        # Slack notifications
        self._send_slack_notifications(alert)
    
    def _send_email_notifications(self, alert):
        """Send email notifications"""
        # Get users who want email notifications for this alert type
        with finder_conn() as c:
            users = c.execute(
                "SELECT email, name FROM users WHERE email_notifications=1 AND alert_types LIKE ?",
                (f"%{alert['type']}%",)
            ).fetchall()
        
        if not users:
            return
        
        # Send email to each user
        for user in users:
            self._send_email(user["email"], user["name"], alert)
    
    def _send_email(self, email, name, alert):
        """Send alert email"""
        subject = f"[Softora Alert] {alert['severity'].upper()}: {alert['title']}"
        
        body = f"""
Hello {name},

A new alert has been triggered:

Type: {alert['type']}
Severity: {alert['severity'].upper()}
Title: {alert['title']}
Message: {alert['message']}

Time: {alert['created_at']}

Data:
{json.dumps(alert['data'], indent=2)}

View in dashboard: http://your-crm-domain.com/alerts/{alert['id']}

---
This is an automated alert from Softora Lead Engine.
Reply STOP to unsubscribe from email alerts.
"""
        
        try:
            # In production, use proper email configuration
            # This is a simplified version
            env = {
                "SMTP_HOST": "smtp.gmail.com",
                "SMTP_PORT": 587,
                "SMTP_USER": "your-email@gmail.com",
                "SMTP_PASS": "your-password",
                "SMTP_FROM": "alerts@your-crm.com"
            }
            
            import os
            env = {k: os.environ.get(k) for k in env}
            
            if not all(env.values()):
                print(f"SMTP not configured, cannot send email to {email}")
                return
            
            from email.message import EmailMessage
            msg = EmailMessage()
            msg["From"] = env["SMTP_FROM"]
            msg["To"] = email
            msg["Subject"] = subject
            msg.set_content(body)
            
            with smtplib.SMTP(env["SMTP_HOST"], int(env["SMTP_PORT"]), timeout=20) as s:
                s.starttls()
                s.login(env["SMTP_USER"], env["SMTP_PASS"])
                s.send_message(msg)
            
        except Exception as e:
            log_discovery("alert", alert["id"], "error", details=f"Failed to send email: {e}")
    
    def _send_webhook_notifications(self, alert):
        """Send webhook notifications"""
        for webhook_id, webhook in self.webhooks.items():
            if webhook["is_active"] and alert["type"] in webhook.get("alert_types", []):
                try:
                    self._send_webhook(webhook, alert)
                except Exception as e:
                    log_discovery("webhook", webhook_id, "error", details=str(e))
    
    def _send_webhook(self, webhook, alert):
        """Send alert to a webhook"""
        payload = {
            "alert_id": alert["id"],
            "type": alert["type"],
            "title": alert["title"],
            "message": alert["message"],
            "severity": alert["severity"],
            "data": alert["data"],
            "created_at": alert["created_at"]
        }
        
        try:
            req = urllib.request.Request(
                webhook["url"],
                data=json.dumps(payload).encode(),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": UA,
                    "X-Softora-Alert-Type": alert["type"]
                }
            )
            
            with urllib.request.urlopen(req, timeout=10) as r:
                response = r.read().decode()
                log_discovery("webhook", webhook["url"], "sent", details=response[:200])
                
        except urllib.error.HTTPError as e:
            log_discovery("webhook", webhook["url"], "error", details=f"HTTP {e.code}")
        except Exception as e:
            log_discovery("webhook", webhook["url"], "error", details=str(e))
    
    def _send_slack_notifications(self, alert):
        """Send Slack notifications"""
        with finder_conn() as c:
            slack_configs = c.execute("SELECT * FROM slack_configs WHERE is_active=1").fetchall()
        
        for config in slack_configs:
            try:
                self._send_slack_message(config, alert)
            except Exception as e:
                log_discovery("slack", config["id"], "error", details=str(e))
    
    def _send_slack_message(self, config, alert):
        """Send message to Slack"""
        # Build Slack message
        color = {"low": "#36a64f", "medium": "#ffcc00", "high": "#ff0000", "critical": "#ff0000"}.get(alert["severity"], "#36a64f")
        
        attachments = [{
            "color": color,
            "title": alert["title"],
            "text": alert["message"],
            "fields": [
                {"title": "Type", "value": alert["type"], "short": True},
                {"title": "Severity", "value": alert["severity"].upper(), "short": True},
                {"title": "Time", "value": alert["created_at"], "short": True}
            ],
            "footer": "Softora Lead Engine",
            "ts": int(time.time())
        }]
        
        payload = {
            "text": f"🚨 *New Alert: {alert['title']}*",
            "attachments": attachments
        }
        
        try:
            req = urllib.request.Request(
                config["webhook_url"],
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}
            )
            
            with urllib.request.urlopen(req, timeout=10) as r:
                response = r.read().decode()
                log_discovery("slack", config["id"], "sent", details=response[:200])
                
        except Exception as e:
            log_discovery("slack", config["id"], "error", details=str(e))


# ============================================================================
# CHANGE DETECTOR
# ============================================================================

class ChangeDetector:
    """Detect changes in websites, leads, etc."""
    
    def __init__(self):
        self.alert_manager = AlertManager()
        self.last_checked = {}
    
    def check_website_changes(self, domain, depth="shallow"):
        """Check for changes on a website"""
        url = f"https://{domain}"
        
        # Get last checked time and hash
        last_check = self._get_last_check(domain)
        
        try:
            html = http_get(url, timeout=30)
            if not html:
                return {"error": "Could not fetch website", "domain": domain}
            
            current_hash = self._hash_content(html)
            
            if last_check:
                if last_check["hash"] == current_hash:
                    return {
                        "domain": domain,
                        "status": "unchanged",
                        "last_checked": last_check["timestamp"]
                    }
                
                # Content has changed
                changes = self._analyze_changes(last_check["html"], html, domain)
                
                # Update last check
                self._update_last_check(domain, html, current_hash)
                
                # Create alert
                if changes:
                    self.alert_manager.create_alert(
                        "website_change",
                        f"Website Changed: {domain}",
                        f"Website {domain} has changed. {len(changes)} new intent signals detected.",
                        {"domain": domain, "changes": changes},
                        "medium"
                    )
                
                return {
                    "domain": domain,
                    "status": "changed",
                    "changes": changes,
                    "last_checked": datetime.now(timezone.utc).isoformat(timespec="seconds")
                }
            else:
                # First check
                self._update_last_check(domain, html, current_hash)
                return {
                    "domain": domain,
                    "status": "baseline",
                    "last_checked": datetime.now(timezone.utc).isoformat(timespec="seconds")
                }
                
        except Exception as e:
            return {"error": str(e), "domain": domain}
    
    def _get_last_check(self, domain):
        """Get last check data"""
        with finder_conn() as c:
            row = c.execute(
                "SELECT html, content_hash, timestamp FROM website_history WHERE domain=? ORDER BY timestamp DESC LIMIT 1",
                (domain,)
            ).fetchone()
            
            if row:
                return {
                    "html": row[0],
                    "hash": row[1],
                    "timestamp": row[2]
                }
        return None
    
    def _update_last_check(self, domain, html, content_hash):
        """Update last check data"""
        with finder_conn() as c:
            c.execute(
                "INSERT INTO website_history (domain, html, content_hash, timestamp) VALUES (?,?,?,?)",
                (domain, html, content_hash, datetime.now(timezone.utc).isoformat(timespec="seconds"))
            )
    
    def _hash_content(self, content):
        """Create a hash of content"""
        import hashlib
        if not content:
            return ""
        short_content = content[:10000]
        return hashlib.md5(short_content.encode()).hexdigest()
    
    def _analyze_changes(self, old_html, new_html, domain):
        """Analyze changes between two HTML versions"""
        from .ai_intent import content_analyzer
        
        old_text = clean_text(old_html)
        new_text = clean_text(new_html)
        
        # Analyze both
        old_analysis = content_analyzer.analyze_text(old_text)
        new_analysis = content_analyzer.analyze_text(new_text)
        
        # Find new signals
        old_keywords = set(s["keyword"] for s in old_analysis["signals"])
        new_keywords = set(s["keyword"] for s in new_analysis["signals"])
        
        new_signals = list(new_keywords - old_keywords)
        
        # Check for removed keywords
        removed_signals = list(old_keywords - new_keywords)
        
        # Intent score change
        intent_change = new_analysis["intent_score"] - old_analysis["intent_score"]
        
        changes = {
            "intent_score_change": intent_change,
            "new_signals": new_signals,
            "removed_signals": removed_signals,
            "text_length_change": len(new_text) - len(old_text),
            "old_intent_score": old_analysis["intent_score"],
            "new_intent_score": new_analysis["intent_score"]
        }
        
        return changes
    
    def check_lead_changes(self, lead_id):
        """Check for changes in a lead"""
        with conn() as c:
            lead = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
            if not lead:
                return {"error": "Lead not found"}
            
            lead_data = dict(lead)
            
            # Get previous state
            prev = c.execute(
                "SELECT * FROM lead_history WHERE lead_id=? ORDER BY timestamp DESC LIMIT 1",
                (lead_id,)
            ).fetchone()
            
            if prev:
                prev_data = dict(prev)
                changes = self._compare_leads(lead_data, prev_data)
                
                if changes:
                    # Save current state
                    self._save_lead_state(lead_id, lead_data)
                    
                    # Create alert
                    self.alert_manager.create_alert(
                        "lead_change",
                        f"Lead Updated: {lead_data.get('company', 'N/A')}",
                        f"Lead {lead_data.get('company', 'N/A')} has been updated. Changes: {', '.join(changes.keys())}",
                        {"lead_id": lead_id, "changes": changes},
                        "low"
                    )
                    
                    return {"status": "changed", "lead_id": lead_id, "changes": changes}
            else:
                # First check
                self._save_lead_state(lead_id, lead_data)
                return {"status": "baseline", "lead_id": lead_id}
        
        return {"error": "Error checking lead"}
    
    def _compare_leads(self, current, previous):
        """Compare two lead states"""
        changes = {}
        
        important_fields = ["status", "score", "contact_email", "email_status", "service", "problem", "evidence_url"]
        
        for field in important_fields:
            current_val = current.get(field)
            prev_val = previous.get(field)
            
            if current_val != prev_val:
                changes[field] = {
                    "old": prev_val,
                    "new": current_val
                }
        
        return changes
    
    def _save_lead_state(self, lead_id, lead_data):
        """Save lead state to history"""
        with conn() as c:
            c.execute(
                "INSERT INTO lead_history (lead_id, data, timestamp) VALUES (?,?,?)",
                (lead_id, json.dumps(lead_data), now())
            )
    
    def check_email_engagement(self, email_id):
        """Check for email engagement"""
        with conn() as c:
            email = c.execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
            if not email:
                return {"error": "Email not found"}
            
            email_data = dict(email)
            
            # Check if email was opened
            opened = c.execute(
                "SELECT * FROM email_events WHERE email_id=? AND event_type='opened' ORDER BY timestamp DESC LIMIT 1",
                (email_id,)
            ).fetchone()
            
            # Check if links were clicked
            clicked = c.execute(
                "SELECT * FROM email_events WHERE email_id=? AND event_type='clicked' ORDER BY timestamp DESC",
                (email_id,)
            ).fetchall()
            
            # Check if replied
            replied = c.execute(
                "SELECT * FROM email_events WHERE email_id=? AND event_type='replied' ORDER BY timestamp DESC LIMIT 1",
                (email_id,)
            ).fetchone()
            
            engagement = {
                "opened": opened is not None,
                "opened_at": opened["timestamp"] if opened else None,
                "clicked": len(clicked) > 0,
                "clicks": len(clicked),
                "clicked_links": [dict(c) for c in clicked] if clicked else [],
                "replied": replied is not None,
                "replied_at": replied["timestamp"] if replied else None
            }
            
            # Calculate engagement score
            engagement_score = self._calculate_engagement_score(engagement)
            
            # Update lead engagement
            lead_id = email_data.get("lead_id")
            if lead_id:
                with conn() as c:
                    c.execute(
                        "UPDATE leads SET engagement_score=?, last_engagement_at=? WHERE id=?",
                        (engagement_score, now(), lead_id)
                    )
            
            return engagement
    
    def _calculate_engagement_score(self, engagement):
        """Calculate engagement score (0-100)"""
        score = 0
        
        if engagement.get("opened"):
            score += 30
        
        if engagement.get("clicked"):
            score += 30
            score += min(40, engagement.get("clicks", 0) * 10)  # Max 40 from multiple clicks
        
        if engagement.get("replied"):
            score += 40
        
        return min(100, score)
    
    def monitor_intent_signals(self, lead_id):
        """Monitor intent signals for a lead"""
        with conn() as c:
            lead = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
            if not lead:
                return {"error": "Lead not found"}
            
            lead_data = dict(lead)
            domain = lead_data.get("domain") or lead_data.get("website")
            
            if not domain:
                return {"error": "No domain for lead"}
            
            # Check for website changes
            website_check = self.check_website_changes(domain)
            
            # Check for job postings
            job_check = self._check_job_postings(domain)
            
            # Check for news
            news_check = self._check_news(domain)
            
            # Aggregate intent signals
            intent_signals = []
            
            if website_check.get("changes") and website_check["changes"].get("new_signals"):
                for signal in website_check["changes"]["new_signals"]:
                    intent_signals.append({
                        "type": "website",
                        "signal": signal,
                        "source": "website_change"
                    })
            
            if job_check.get("new_jobs"):
                intent_signals.append({
                    "type": "hiring",
                    "count": job_check["new_jobs"],
                    "source": "job_postings"
                })
            
            if news_check.get("relevant_news"):
                for news in news_check["relevant_news"]:
                    intent_signals.append({
                        "type": "news",
                        "title": news.get("title"),
                        "source": news.get("source", "unknown")
                    })
            
            if intent_signals:
                # Update lead intent score
                intent_score = self._calculate_intent_score(intent_signals)
                
                with conn() as c:
                    c.execute(
                        "UPDATE leads SET intent_score=?, last_intent_check=? WHERE id=?",
                        (intent_score, now(), lead_id)
                    )
                
                # Create alert for high intent
                if intent_score >= 70:
                    self.alert_manager.create_alert(
                        "high_intent",
                        f"High Intent: {lead_data.get('company', 'N/A')}",
                        f"Lead {lead_data.get('company', 'N/A')} shows high buying intent (score: {intent_score})",
                        {"lead_id": lead_id, "intent_score": intent_score, "signals": intent_signals},
                        "high"
                    )
            
            return {
                "lead_id": lead_id,
                "domain": domain,
                "website_check": website_check,
                "job_check": job_check,
                "news_check": news_check,
                "intent_signals": intent_signals,
                "intent_score": intent_score if intent_signals else lead_data.get("intent_score", 0)
            }
    
    def _check_job_postings(self, domain):
        """Check for new job postings"""
        try:
            # Search for job pages
            job_urls = [
                f"https://{domain}/careers",
                f"https://{domain}/jobs",
                f"https://{domain}/careers.html",
                f"https://{domain}/jobs.html",
                f"https://{domain}/work-with-us",
            ]
            
            new_jobs = 0
            
            for url in job_urls:
                try:
                    html = http_get(url, timeout=20)
                    if html:
                        # Count job listings
                        job_matches = re.findall(r'(?:job|position|opening)[^>]*>\s*\d+', html, re.IGNORECASE)
                        if job_matches:
                            new_jobs += len(job_matches)
                except:
                    pass
                
                time.sleep(1)
            
            return {"new_jobs": new_jobs}
            
        except Exception as e:
            return {"error": str(e), "domain": domain}
    
    def _check_news(self, domain):
        """Check for news about the company"""
        try:
            company_name = domain.replace(".com", "").replace(".co", "").replace(".io", "").replace("-", " ").title()
            
            # Search Google News
            news_url = f"https://news.google.com/search?q={urllib.parse.quote(company_name)}&hl=en-US&gl=US&ceid=US:en"
            html = http_get(news_url, timeout=20)
            
            if html:
                # Extract news items
                news_items = []
                
                # Look for news articles
                for match in re.finditer(r'<a[^>]*class="[^"]*article[^"]*"[^>]*href="([^"]+)"', html):
                    url = match.group(1)
                    if "http" not in url:
                        url = f"https://news.google.com{url}"
                    
                    # Extract title
                    title_match = re.search(r'>([^<]{20,100})<', html[match.end():match.end() + 200])
                    title = clean_text(title_match.group(1)) if title_match else ""
                    
                    if title and company_name.lower() in title.lower():
                        news_items.append({
                            "title": title,
                            "url": url,
                            "source": "Google News"
                        })
                
                # Filter for relevant news (funding, hiring, growth, etc.)
                relevant_keywords = [
                    "raised", "funding", "series", "investment", "hire", "hiring",
                    "growth", "scale", "expand", "acquir", "launch", "new product"
                ]
                
                relevant_news = []
                for news in news_items:
                    news_lower = news["title"].lower()
                    if any(kw in news_lower for kw in relevant_keywords):
                        relevant_news.append(news)
                
                return {"relevant_news": relevant_news[:5]}
            
            return {"relevant_news": []}
            
        except Exception as e:
            return {"error": str(e), "domain": domain}
    
    def _calculate_intent_score(self, intent_signals):
        """Calculate intent score from signals"""
        score = 0
        
        for signal in intent_signals:
            signal_type = signal.get("type", "")
            count = signal.get("count", 1)
            
            weights = {
                "hiring": 30,
                "website": 20,
                "news": 25,
                "social": 15
            }
            
            weight = weights.get(signal_type, 10)
            score += weight * min(count, 5)  # Cap at 5 per signal type
        
        return min(100, score)


# ============================================================================
# MONITORING SCHEDULER
# ============================================================================

class MonitoringScheduler:
    """Schedule and run monitoring tasks"""
    
    def __init__(self):
        self.change_detector = ChangeDetector()
        self.alert_manager = AlertManager()
        self.running = False
    
    def start(self, interval=3600):
        """Start continuous monitoring"""
        self.running = True
        self.interval = interval
        
        log_discovery("monitor", "", "started", details=f"Interval: {interval}s")
        
        while self.running:
            try:
                self._run_monitoring_cycle()
                time.sleep(self.interval)
            except KeyboardInterrupt:
                self.running = False
                break
            except Exception as e:
                log_discovery("monitor", "", "error", details=str(e))
                time.sleep(300)  # Wait 5 minutes on error
    
    def _run_monitoring_cycle(self):
        """Run one monitoring cycle"""
        log_discovery("monitor", "", "cycle_started")
        
        # Monitor all active leads
        with conn() as c:
            leads = c.execute(
                "SELECT id FROM leads WHERE status NOT IN ('LOST', 'OPTED_OUT', 'WON')"
            ).fetchall()
            
            for lead in leads:
                try:
                    # Check for intent signals
                    self.change_detector.monitor_intent_signals(lead[0])
                    
                    # Check for lead changes
                    self.change_detector.check_lead_changes(lead[0])
                    
                    time.sleep(1)  # Be polite
                    
                except Exception as e:
                    log_discovery("monitor", str(lead[0]), "error", details=str(e))
                    continue
        
        log_discovery("monitor", "", "cycle_completed", details=f"Monitored {len(leads)} leads")
    
    def monitor_website(self, domain, interval=86400):
        """Monitor a specific website continuously"""
        self.running = True
        
        while self.running:
            try:
                result = self.change_detector.check_website_changes(domain)
                log_discovery("monitor", domain, "website_check", details=json.dumps(result, default=str))
                time.sleep(interval)
            except KeyboardInterrupt:
                self.running = False
                break
            except Exception as e:
                log_discovery("monitor", domain, "error", details=str(e))
                time.sleep(300)
    
    def stop(self):
        """Stop monitoring"""
        self.running = False


# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

def init_monitoring_database():
    """Initialize monitoring database tables"""
    with finder_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            data TEXT,
            severity TEXT NOT NULL DEFAULT 'medium',
            status TEXT NOT NULL DEFAULT 'new',
            read_at TEXT,
            resolved_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        
        CREATE TABLE IF NOT EXISTS monitoring_webhooks (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            alert_types TEXT NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        
        CREATE TABLE IF NOT EXISTS notification_preferences (
            id INTEGER PRIMARY KEY,
            user_id TEXT NOT NULL,
            email TEXT NOT NULL,
            name TEXT,
            email_notifications BOOLEAN DEFAULT 1,
            push_notifications BOOLEAN DEFAULT 0,
            alert_types TEXT DEFAULT 'all',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id)
        );
        
        CREATE TABLE IF NOT EXISTS slack_configs (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            webhook_url TEXT NOT NULL,
            channel TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        
        CREATE TABLE IF NOT EXISTS website_history (
            id INTEGER PRIMARY KEY,
            domain TEXT NOT NULL,
            html TEXT,
            content_hash TEXT NOT NULL,
            intent_score INT,
            timestamp TEXT NOT NULL,
            UNIQUE(domain, content_hash)
        );
        
        CREATE TABLE IF NOT EXISTS lead_history (
            id INTEGER PRIMARY KEY,
            lead_id INT NOT NULL,
            data TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        );
        
        CREATE TABLE IF NOT EXISTS email_events (
            id INTEGER PRIMARY KEY,
            email_id INT NOT NULL,
            lead_id INT,
            event_type TEXT NOT NULL,
            data TEXT,
            timestamp TEXT NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            FOREIGN KEY (email_id) REFERENCES emails(id),
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        );
        """)


# ============================================================================
# EXPORTS
# ============================================================================

# Initialize database
init_monitoring_database()

# Create singleton instances
alert_manager = AlertManager()
change_detector = ChangeDetector()
monitoring_scheduler = MonitoringScheduler()


def create_alert(alert_type, title, message, data=None, severity="medium"):
    """Convenience function to create an alert"""
    return alert_manager.create_alert(alert_type, title, message, data, severity)


def check_website_changes(domain):
    """Convenience function to check website changes"""
    return change_detector.check_website_changes(domain)


def monitor_lead(lead_id):
    """Convenience function to monitor a lead"""
    return change_detector.monitor_intent_signals(lead_id)


if __name__ == "__main__":
    print("Monitoring System - Testing")
    print("=" * 50)
    
    # Test alert creation
    print("\n1. Testing alert creation...")
    alert = create_alert(
        "test_alert",
        "Test Alert",
        "This is a test alert to verify the system is working",
        {"test": True},
        "low"
    )
    print(f"   Created alert: {alert['id']}")
    
    # Test website change detection
    print("\n2. Testing website change detection...")
    domain = "softorasystems.com"
    result = check_website_changes(domain)
    print(f"   Domain: {domain}")
    print(f"   Status: {result.get('status', 'error')}")
    
    # Test monitoring
    print("\n3. Testing lead monitoring...")
    # Would need a real lead_id to test
    print("   (Monitoring test requires a valid lead_id)")
