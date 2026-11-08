"""
Lead Finder Database Management
"""
import os
import json
import sqlite3
from datetime import datetime, timezone, timedelta


def now():
    """Get current UTC timestamp"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

# Path to lead finder database
LEAD_FINDER_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lead_finder.db")


def conn():
    """Connection to the lead finder's database"""
    c = sqlite3.connect(LEAD_FINDER_DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    """Initialize the lead finder's tracking database"""
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS discovered_sources (
            id INTEGER PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_url TEXT NOT NULL,
            last_scraped TEXT,
            next_scrape TEXT,
            items_found INT DEFAULT 0,
            UNIQUE(source_type, source_url)
        );
        
        CREATE TABLE IF NOT EXISTS discovery_log (
            id INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            source_type TEXT,
            source_url TEXT,
            action TEXT NOT NULL,
            domain TEXT,
            company TEXT,
            result TEXT,
            details TEXT
        );
        
        CREATE TABLE IF NOT EXISTS rate_limiting (
            id INTEGER PRIMARY KEY,
            domain TEXT NOT NULL UNIQUE,
            last_request TEXT NOT NULL,
            request_count INT DEFAULT 0,
            blocked_until TEXT
        );
        
        CREATE TABLE IF NOT EXISTS daily_stats (
            day TEXT PRIMARY KEY,
            requests_made INT DEFAULT 0,
            leads_found INT DEFAULT 0,
            leads_imported INT DEFAULT 0,
            errors INT DEFAULT 0
        );
        
        CREATE TABLE IF NOT EXISTS enrichment_cache (
            domain TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            expires TEXT NOT NULL,
            source TEXT
        );
        
        CREATE TABLE IF NOT EXISTS monitoring_jobs (
            id INTEGER PRIMARY KEY,
            job_type TEXT NOT NULL,
            target TEXT NOT NULL,
            last_run TEXT,
            next_run TEXT,
            status TEXT DEFAULT 'active',
            config TEXT,
            UNIQUE(job_type, target)
        );
        
        CREATE TABLE IF NOT EXISTS company_profiles (
            domain TEXT PRIMARY KEY,
            company_name TEXT,
            website TEXT,
            industry TEXT,
            country TEXT,
            city TEXT,
            employees_min INT,
            employees_max INT,
            revenue_min INT,
            revenue_max INT,
            founded_year INT,
            description TEXT,
            technologies TEXT,
            linkedin_url TEXT,
            crunchbase_url TEXT,
            last_updated TEXT,
            data_sources TEXT
        );
        """)
        
        # Initialize today's stats
        today = now()[:10]
        c.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (today,))


def log_discovery(source_type, source_url, action, domain=None, company=None, result="success", details=""):
    """Log a discovery action"""
    with conn() as c:
        c.execute(
            "INSERT INTO discovery_log (timestamp, source_type, source_url, action, domain, company, result, details) VALUES (?,?,?,?,?,?,?,?)",
            (now(), source_type, source_url, action, domain, company, result, details[:500])
        )


def check_daily_limit():
    """Check if we've hit the daily limit"""
    from .config import DAILY_LIMIT
    today = now()[:10]
    with conn() as c:
        row = c.execute("SELECT requests_made FROM daily_stats WHERE day=?", (today,)).fetchone()
        if row and row[0] >= DAILY_LIMIT:
            return False
        return True


def increment_daily_counter():
    """Increment the daily request counter"""
    today = now()[:10]
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (today,))
        c.execute("UPDATE daily_stats SET requests_made = requests_made + 1 WHERE day=?", (today,))


def get_domain_cooldown(domain):
    """Check if a domain is in cooldown"""
    with conn() as c:
        row = c.execute("SELECT blocked_until FROM rate_limiting WHERE domain=?", (domain,)).fetchone()
        if row and row[0] and row[0] > now():
            return datetime.fromisoformat(row[0])
        return None


def set_domain_cooldown(domain, seconds=3600):
    """Set a cooldown for a domain"""
    blocked_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO rate_limiting (domain, last_request, request_count, blocked_until) VALUES (?,?,?,?)",
            (domain, now(), 0, blocked_until.isoformat(timespec="seconds"))
        )


def update_leads_imported(count=1):
    """Update the count of imported leads for today"""
    today = now()[:10]
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (today,))
        c.execute("UPDATE daily_stats SET leads_imported = leads_imported + ? WHERE day=?", (count, today))


def cache_enrichment(domain, data, source):
    """Cache enrichment data"""
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO enrichment_cache (domain, data, expires, source) VALUES (?,?,?,?)",
            (domain, json.dumps(data), expires.isoformat(timespec="seconds"), source)
        )


def get_cached_enrichment(domain):
    """Get cached enrichment data"""
    with conn() as c:
        row = c.execute("SELECT data FROM enrichment_cache WHERE domain=? AND expires > ?", (domain, now())).fetchone()
        if row:
            return json.loads(row[0])
    return None
