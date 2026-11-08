"""
Continuous Monitoring for Lead Finder
"""
import os
import sys
import time

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .processor import LeadProcessor
from .database import log_discovery


class LeadMonitor:
    """Monitor for new leads and changes continuously"""
    
    def __init__(self):
        self.processor = LeadProcessor()
        self.running = False
    
    def start_continuous_discovery(self, interval=3600, config=None):
        """Start continuous lead discovery"""
        self.running = True
        config = config or {}
        
        log_discovery("monitor", "", "started", details=f"Interval: {interval}s")
        
        while self.running:
            try:
                # Run discovery
                raw_leads = self.processor.discover_leads(config)
                
                # Import leads
                result = self.processor.import_batch(raw_leads, config)
                
                log_discovery(
                    "monitor", 
                    "", 
                    "cycle_completed",
                    details=f"Discovered: {len(raw_leads)}, Imported: {result['imported']}, Errors: {result['errors']}"
                )
                
                # Sleep until next cycle
                time.sleep(interval)
                
            except KeyboardInterrupt:
                self.running = False
                break
            except Exception as e:
                log_discovery("monitor", "", "error", details=str(e))
                time.sleep(300)  # Wait 5 minutes on error
    
    def monitor_website_changes(self, domains, interval=86400):
        """Monitor specific domains for changes"""
        # Implementation would track website content hashes
        pass
    
    def monitor_competitors(self, competitor_domains, interval=86400):
        """Monitor competitor websites for new clients"""
        # Implementation would check competitor case studies
        pass
    
    def start(self, mode="discovery", interval=3600, config=None):
        """Start the monitor"""
        if mode == "discovery":
            self.start_continuous_discovery(interval, config)
        elif mode == "website":
            self.monitor_website_changes([], interval)
        elif mode == "competitors":
            self.monitor_competitors([], interval)
