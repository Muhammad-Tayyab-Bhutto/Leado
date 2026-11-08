"""
Lead Finder CLI
"""
import os
import sys
import csv
import json
from datetime import datetime, timezone, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .processor import LeadProcessor, init_all
from .monitor import LeadMonitor
from .database import conn as finder_conn, log_discovery
import app


def discover_command(keywords=None, limit=100, output_file=None):
    """CLI command for discovery"""
    init_all()
    
    processor = LeadProcessor()
    config = {
        "keywords": keywords,
        "limit_per_source": limit,
        "enrich": True,
        "deep_enrich": False
    }
    
    print(f"Starting lead discovery with keywords: {keywords or 'default'}")
    print(f"Limit per source: {limit}")
    print()
    
    raw_leads = processor.discover_leads(config)
    print(f"Discovered {len(raw_leads)} potential leads from multiple sources")
    
    # Process and enrich
    processed_leads = []
    for raw_lead in raw_leads:
        processed = processor.process_raw_lead(raw_lead)
        if processed:
            processed_leads.append(processed)
    
    print(f"Processed {len(processed_leads)} leads")
    
    # Import
    result = processor.import_batch(raw_leads, config)
    print(f"Imported {result['imported']} leads, {result['errors']} errors")
    
    # Save to file if requested
    if output_file:
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['domain', 'company', 'website', 'industry', 'country', 'service', 'notes'])
            writer.writeheader()
            for lead in processed_leads:
                writer.writerow({k: lead.get(k, '') for k in writer.fieldnames})
        print(f"Saved leads to {output_file}")
    
    return processed_leads


def enrich_command(days=7, limit=100):
    """CLI command for enriching existing leads"""
    init_all()
    
    processor = LeadProcessor()
    
    # Get leads that need enrichment
    with app.conn() as c:
        leads_to_enrich = c.execute(
            "SELECT id, domain, company, website FROM leads WHERE description IS NULL AND discovered_at >= ? ORDER BY id LIMIT ?",
            ((datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds"), limit)
        ).fetchall()
    
    print(f"Found {len(leads_to_enrich)} leads to enrich")
    
    enriched_count = 0
    for lead in leads_to_enrich:
        lead_id, domain, company, website = lead
        
        try:
            # Enrich the domain
            for enricher in processor.enrichers[:2]:  # Use first 2 enrichers for speed
                result = enricher.enrich(domain or website, {"domain": domain, "company": company, "website": website})
                if result and not result.get("error"):
                    # Update lead in database
                    update_data = {}
                    if "description" in result:
                        update_data["description"] = result["description"][:500]
                    if "industry" in result:
                        update_data["industry"] = result["industry"]
                    if "country" in result:
                        update_data["country"] = result["country"]
                    if "city" in result:
                        update_data["city"] = result["city"]
                    
                    if update_data:
                        with app.conn() as c:
                            c.execute(
                                f"UPDATE leads SET {','.join(f'{k}=?' for k in update_data)} WHERE id=?",
                                [update_data[k] for k in update_data] + [lead_id]
                            )
                        enriched_count += 1
                        break
        except Exception as e:
            print(f"Error enriching {domain}: {e}")
    
    print(f"Enriched {enriched_count} leads")
    return enriched_count


def monitor_command(interval=3600):
    """CLI command for continuous monitoring"""
    init_all()
    
    monitor = LeadMonitor()
    config = {
        "keywords": None,  # Use defaults
        "limit_per_source": 50,
        "enrich": True,
        "deep_enrich": False
    }
    
    print(f"Starting continuous lead monitoring with {interval} second interval")
    print("Press Ctrl+C to stop")
    print()
    
    try:
        monitor.start(mode="discovery", interval=interval, config=config)
    except KeyboardInterrupt:
        print("\nMonitoring stopped")


def stats_command():
    """Show lead finder statistics"""
    init_all()
    
    from datetime import datetime, timezone
    
    with finder_conn() as c:
        # Today's stats
        today = datetime.now(timezone.utc).isoformat()[:10]
        today_row = c.execute("SELECT * FROM daily_stats WHERE day=?", (today,)).fetchone()
        
        print(f"Today ({today}):")
        if today_row:
            print(f"  Requests made: {today_row['requests_made']}")
            print(f"  Leads found: {today_row['leads_found']}")
            print(f"  Leads imported: {today_row['leads_imported']}")
            print(f"  Errors: {today_row['errors']}")
        
        # Overall stats
        total_requests = c.execute("SELECT SUM(requests_made) FROM daily_stats").fetchone()[0] or 0
        total_leads = c.execute("SELECT SUM(leads_found) FROM daily_stats").fetchone()[0] or 0
        total_imported = c.execute("SELECT SUM(leads_imported) FROM daily_stats").fetchone()[0] or 0
        
        print(f"\nOverall:")
        print(f"  Total requests: {total_requests}")
        print(f"  Total leads found: {total_leads}")
        print(f"  Total leads imported: {total_imported}")
        
        # Recent discovery log
        print(f"\nRecent activity:")
        recent = c.execute(
            "SELECT timestamp, source_type, action, company, result FROM discovery_log ORDER BY timestamp DESC LIMIT 10"
        ).fetchall()
        
        for row in recent:
            print(f"  [{row['timestamp'][:19]}] {row['source_type']}: {row['action']} - {row['company'] or 'N/A'} ({row['result']})")
    
    # CRM stats
    with app.conn() as c:
        total_leads = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        qualified = c.execute("SELECT COUNT(*) FROM leads WHERE score >= 60").fetchone()[0]
        
        print(f"\nCRM Stats:")
        print(f"  Total leads: {total_leads}")
        print(f"  Qualified leads (score >= 60): {qualified}")


def doctor_command():
    """Diagnose lead finder setup"""
    init_all()
    
    print("Lead Finder Doctor")
    print("=" * 50)
    
    # Check configuration
    checks = [
        ("SOFTORASYSTEMS_TOKEN", lambda: len(os.environ.get("SOFTORASYSTEMS_TOKEN", "")) >= 16),
        ("SOFTORASYSTEMS_ADDRESS", lambda: bool(os.environ.get("SOFTORASYSTEMS_ADDRESS", ""))),
        ("OPENROUTER_API_KEY", lambda: bool(os.environ.get("OPENROUTER_API_KEY", ""))),
        ("OPENROUTER_MODEL", lambda: bool(os.environ.get("OPENROUTER_MODEL", ""))),
    ]
    
    for name, check in checks:
        status = "OK" if check() else "MISSING"
        print(f"  [{status:8}] {name}")
    
    # Check optional API keys
    optional_checks = [
        ("SERPAPI_API_KEY (optional)", lambda: bool(os.environ.get("SERPAPI_API_KEY", ""))),
        ("CLEARBIT_API_KEY (optional)", lambda: bool(os.environ.get("CLEARBIT_API_KEY", ""))),
        ("HUNTER_API_KEY (optional)", lambda: bool(os.environ.get("HUNTER_API_KEY", ""))),
        ("CRUNCHBASE_API_KEY (optional)", lambda: bool(os.environ.get("CRUNCHBASE_API_KEY", ""))),
    ]
    
    print("\nOptional API Keys:")
    for name, check in optional_checks:
        status = "OK" if check() else "NOT SET"
        print(f"  [{status:8}] {name}")
    
    # Check databases
    print("\nDatabase Check:")
    db_files = [app.DB]
    from .database import LEAD_FINDER_DB
    db_files.append(LEAD_FINDER_DB)
    
    for db_file in db_files:
        exists = os.path.exists(db_file)
        size = os.path.getsize(db_file) if exists else 0
        print(f"  [{'OK' if exists else 'MISSING':8}] {os.path.basename(db_file)} ({size:,} bytes)")
    
    # Check today's stats
    today = datetime.now(timezone.utc).isoformat()[:10]
    with finder_conn() as c:
        row = c.execute("SELECT requests_made, leads_found, leads_imported FROM daily_stats WHERE day=?", (today,)).fetchone()
        if row:
            print(f"\nToday's Activity:")
            print(f"  Requests: {row[0]}, Leads found: {row[1]}, Imported: {row[2]}")
        else:
            print("\nNo activity today yet")
    
    # Check CRM
    with app.conn() as c:
        count = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        print(f"\nCRM: {count} leads")


def main():
    """Main CLI entry point"""
    if len(sys.argv) < 2:
        print("Lead Finder - Automatic Lead Discovery System")
        print("=" * 60)
        print("Usage:")
        print("  python -m lead_finder discover [--keywords KW] [--limit N] [--output FILE]")
        print("  python -m lead_finder enrich [--days N] [--limit N]")
        print("  python -m lead_finder monitor [--interval SECONDS]")
        print("  python -m lead_finder stats")
        print("  python -m lead_finder doctor")
        print("\nExamples:")
        print("  python -m lead_finder discover --keywords 'SaaS development,AI agents' --limit 50")
        print("  python -m lead_finder enrich --days 30 --limit 200")
        print("  python -m lead_finder monitor --interval 7200")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    # Parse arguments
    kwargs = {}
    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg.startswith("--"):
            key = arg[2:].replace("-", "_")
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
                kwargs[key] = sys.argv[i + 1]
                i += 2
            else:
                kwargs[key] = True
                i += 1
        else:
            i += 1
    
    # Convert types
    if "limit" in kwargs:
        kwargs["limit"] = int(kwargs["limit"])
    if "days" in kwargs:
        kwargs["days"] = int(kwargs["days"])
    if "interval" in kwargs:
        kwargs["interval"] = int(kwargs["interval"])
    if "keywords" in kwargs:
        keywords = kwargs["keywords"].split(",") if "," in kwargs["keywords"] else [kwargs["keywords"]]
        kwargs["keywords"] = keywords
    
    # Import datetime and timedelta for enrich command
    from datetime import datetime, timezone, timedelta
    
    # Execute command
    try:
        if command == "discover":
            discover_command(**kwargs)
        elif command == "enrich":
            enrich_command(**kwargs)
        elif command == "monitor":
            monitor_command(**kwargs)
        elif command == "stats":
            stats_command()
        elif command == "doctor":
            doctor_command()
        else:
            print(f"Unknown command: {command}")
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
