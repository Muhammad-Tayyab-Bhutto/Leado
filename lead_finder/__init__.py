"""
Softora Lead Finder: AUTOMATIC multi-source lead discovery and enrichment system.

This package provides COMPLETE automation for finding, researching, and importing leads
into your CRM. It surpasses Grok, Apollo, and similar tools by:

1. MULTI-SOURCE DISCOVERY: Finds leads from search engines, directories, job boards,
   GitHub, social media, and more - ALL AUTOMATICALLY
2. INTENT-BASED TARGETING: Identifies companies actively looking for your services
3. DEEP ENRICHMENT: Auto-populates 50+ data points per lead
4. REAL-TIME MONITORING: Tracks website changes, funding events, hiring sprees
5. SMART PRIORITIZATION: ML-powered scoring beyond simple firmographics
6. CONTINUOUS OPERATION: Runs 24/7 with proper rate limiting and compliance

USAGE:
    # Run a one-time discovery campaign
    python -m lead_finder discover --keywords "SaaS development,AI agents" --limit 100
    
    # Start continuous monitoring
    python -m lead_finder monitor --interval 3600
    
    # Enrich existing leads
    python -m lead_finder enrich --days 7
    
    # Show statistics
    python -m lead_finder stats
    
    # Diagnose setup
    python -m lead_finder doctor
"""

import os
import sys

# Ensure parent directory is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .processor import LeadProcessor, init_all
from .monitor import LeadMonitor
from .cli import main

__all__ = ['main', 'LeadProcessor', 'LeadMonitor', 'init_all']

# For convenience, make main available at module level
if __name__ == "__main__":
    main()
