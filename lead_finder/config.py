"""
Lead Finder Configuration
"""
import os
import urllib.parse
from dotenv import load_dotenv

load_dotenv()


def domain_of(url):
    """Extract domain from URL"""
    h = urllib.parse.urlparse(url if "//" in url else "//"+url).netloc.lower()
    return h[4:] if h.startswith("www.") else h

# User-Agent for all HTTP requests
UA = "SoftoraLeadFinder/2.0 (+https://softorasystems.com; contact@softorasystems.com)"

# Rate limiting configuration
RATE_LIMIT_DELAY = float(os.environ.get("FINDER_DELAY", "2.0"))
MAX_CONCURRENT_REQUESTS = int(os.environ.get("FINDER_CONCURRENT", "10"))
DAILY_LIMIT = int(os.environ.get("FINDER_DAILY_LIMIT", "1000"))

# API keys (optional, but recommended for better results)
SERPAPI_KEY = os.environ.get("SERPAPI_API_KEY")
CLEARBIT_KEY = os.environ.get("CLEARBIT_API_KEY")
HUNTER_KEY = os.environ.get("HUNTER_API_KEY")
LINKEDIN_TOKEN = os.environ.get("LINKEDIN_API_TOKEN")
CRUNCHBASE_KEY = os.environ.get("CRUNCHBASE_API_KEY")

# Service keywords for different discovery methods
SERVICE_KEYWORDS = {
    "web_app": ["web application development", "custom web app", "web platform", "SaaS development",
                "website development", "web development company", "hire web developers"],
    "saas": ["SaaS development", "software as a service", "SaaS platform", "SaaS company",
             "build a SaaS", "SaaS startup", "SaaS product development"],
    "mobile": ["mobile app development", "iOS development", "Android development", "cross-platform app",
               "mobile application", "app developers for hire"],
    "ai_agents": ["AI agent development", "custom AI agents", "AI automation", "intelligent agents",
                  "AI workflow automation", "LLM applications"],
    "automation": ["workflow automation", "process automation", "business automation", "RPA",
                   "automate business processes", "custom automation"],
    "erp_crm": ["ERP system", "CRM development", "custom ERP", "CRM implementation",
                "enterprise software", "business management software"],
    "cms": ["CMS development", "content management system", "custom CMS", "headless CMS",
            "WordPress alternative", "web content management"],
    "technical_seo": ["technical SEO", "SEO audit", "SEO optimization", "search engine optimization",
                      "website SEO", "SEO services"],
    "aeo": ["answer engine optimization", "AEO", "AI search optimization", "search engine for AI",
            "optimize for AI search"],
    "geo": ["GEO", "Google Extended Optimization", "AI search ranking", "AI-era SEO"],
    "seo_content": ["SEO content", "content marketing", "SEO writing", "blog SEO",
                     "content strategy", "SEO copywriting"],
    "cro": ["conversion rate optimization", "CRO", "landing page optimization", "A/B testing",
            "improve conversions", "conversion optimization"],
    "devops": ["DevOps services", "CI/CD pipeline", "cloud infrastructure", "AWS DevOps",
               "DevOps consulting", "infrastructure as code"]
}

# Industry keywords for targeting
INDUSTRY_KEYWORDS = {
    "technology": ["software company", "tech startup", "SaaS company", "IT services"],
    "finance": ["fintech", "bank", "financial services", "investment", "payments"],
    "healthcare": ["healthtech", "medical software", "healthcare IT", "telemedicine"],
    "ecommerce": ["ecommerce", "online store", "retail", "shopping platform", "DTC"],
    "education": ["edtech", "online learning", "education platform", "LMS"],
    "marketing": ["martech", "advertising", "digital marketing", "agency"],
    "real_estate": ["proptech", "real estate software", "property management", "rental platform"],
    "logistics": ["supply chain", "shipping", "logistics software", "freight"],
    "manufacturing": ["industry 4.0", "factory software", "manufacturing automation"],
    "media": ["publishing", "news", "content platform", "social media"]
}

# Job title keywords for finding decision makers
DECISION_MAKER_TITLES = [
    "founder", "co-founder", "ceo", "cto", "cpo", "cmo", "coo", "vp", "director",
    "head of", "manager", "lead", "principal", "senior", "engineering manager",
    "product manager", "tech lead", "software architect", "growth manager",
    "marketing director", "it director", "digital transformation", "innovation"
]

# Excluded domains and patterns
EXCLUDED_DOMAINS = {
    "example.com", "test.com", "demo.com", "localhost",
    "github.com", "gitlab.com", "bitbucket.org",
    "facebook.com", "twitter.com", "linkedin.com",
    "youtube.com", "instagram.com", "tiktok.com",
    "google.com", "bing.com", "yahoo.com",
    "wikipedia.org", "stackoverflow.com", "reddit.com",
    "amazon.com", "ebay.com", "etsy.com"
}

EXCLUDED_PATTERNS = [
    r'\.gov$', r'\.edu$',
    r'blog\.', r'news\.', r'forum\.', r'community\.',
    r'\.wordpress\.com$', r'\.wixsite\.com$', r'\.squarespace\.com$'
]

# Email patterns to exclude
GENERIC_EMAILS = ["info@", "contact@", "hello@", "sales@", "enquiries@", "inquiries@", "office@"]
