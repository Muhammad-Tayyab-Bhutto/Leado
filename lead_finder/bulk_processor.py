"""
Bulk Processing Engine - Advanced Lead Processing at Scale

This module provides high-performance bulk processing capabilities:
1. Parallel lead discovery from multiple sources
2. Batch enrichment with smart rate limiting
3. Bulk scoring and prioritization
4. Mass import with deduplication
5. Background job processing
6. Progress tracking and reporting
7. Error handling and retries

Replaces bulk features in Apollo.io, Instantly.ai, and similar tools.
"""
import os
import sys
import json
import time
import queue
import threading
import multiprocessing as mp
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
from app import conn, now, domain_of, SERVICES, SERVICE_TO_PACKAGE, COLS, upsert
from .config import SERVICE_KEYWORDS, GENERIC_EMAILS, RATE_LIMIT_DELAY, DAILY_LIMIT
from .sources import ALL_SOURCES
from .enrichment import ALL_ENRICHERS
from .deep_enrichment import WebsiteDataProvider, CompanyDataProvider, SocialDataProvider
from .email_finder import comprehensive_email_finder, email_verifier, bulk_email_finder
from .linkedin_scraper import linkedin_company_scraper, linkedin_employee_discoverer
from .ai_intent import content_analyzer, intent_engine
from .advanced_scoring import scoring_engine, MultiDimensionalScorer
from .database import conn as finder_conn, log_discovery, update_leads_imported, init_db


# ============================================================================
# JOB STATUS AND TRACKING
# ============================================================================

class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(Enum):
    DISCOVERY = "discovery"
    ENRICHMENT = "enrichment"
    SCORING = "scoring"
    IMPORT = "import"
    FULL_PIPELINE = "full_pipeline"


@dataclass
class BulkJob:
    """Represents a bulk processing job"""
    id: str
    job_type: str
    status: str = JobStatus.PENDING.value
    created_at: str = field(default_factory=lambda: now())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    total_items: int = 0
    processed_items: int = 0
    succeeded_items: int = 0
    failed_items: int = 0
    config: Dict[str, Any] = field(default_factory=dict)
    results: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    progress: float = 0.0
    
    def to_dict(self):
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class JobQueue:
    """Manages bulk processing jobs"""
    
    def __init__(self):
        self.jobs: Dict[str, BulkJob] = {}
        self.queue = queue.Queue()
        self.lock = threading.Lock()
        self._init_db()
    
    def _init_db(self):
        """Initialize job tracking in database"""
        init_db()
        with finder_conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS bulk_jobs (
                    id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    total_items INTEGER DEFAULT 0,
                    processed_items INTEGER DEFAULT 0,
                    succeeded_items INTEGER DEFAULT 0,
                    failed_items INTEGER DEFAULT 0,
                    config TEXT DEFAULT '{}',
                    results TEXT DEFAULT '[]',
                    errors TEXT DEFAULT '[]',
                    progress REAL DEFAULT 0.0
                )
            """)
    
    def create_job(self, job_type: str, config: Dict[str, Any], total_items: int = 0) -> BulkJob:
        """Create a new bulk job"""
        import uuid
        job_id = str(uuid.uuid4())
        job = BulkJob(
            id=job_id,
            job_type=job_type,
            config=config,
            total_items=total_items,
            status=JobStatus.PENDING.value
        )
        
        with finder_conn() as c:
            c.execute(
                """INSERT INTO bulk_jobs (id, job_type, status, created_at, total_items, config) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (job_id, job_type, JobStatus.PENDING.value, job.created_at, total_items, json.dumps(config))
            )
        
        with self.lock:
            self.jobs[job_id] = job
            self.queue.put(job)
        
        return job
    
    def get_job(self, job_id: str) -> Optional[BulkJob]:
        """Get a job by ID"""
        with self.lock:
            return self.jobs.get(job_id)
    
    def update_job(self, job: BulkJob):
        """Update job status and progress"""
        with finder_conn() as c:
            c.execute(
                """UPDATE bulk_jobs SET 
                    status=?, started_at=?, completed_at=?, 
                    processed_items=?, succeeded_items=?, failed_items=?, 
                    progress=?, results=?, errors=? 
                    WHERE id=?""",
                (
                    job.status,
                    job.started_at,
                    job.completed_at,
                    job.processed_items,
                    job.succeeded_items,
                    job.failed_items,
                    job.progress,
                    json.dumps(job.results),
                    json.dumps(job.errors),
                    job.id
                )
            )
        
        with self.lock:
            if job.id in self.jobs:
                self.jobs[job.id] = job
    
    def list_jobs(self, job_type: Optional[str] = None, status: Optional[str] = None) -> List[BulkJob]:
        """List all jobs with optional filters"""
        with finder_conn() as c:
            query = "SELECT * FROM bulk_jobs"
            params = []
            if job_type:
                query += " WHERE job_type=?"
                params.append(job_type)
            if status:
                query += " AND status=?" if job_type else " WHERE status=?"
                params.append(status)
            query += " ORDER BY created_at DESC"
            rows = c.execute(query, params).fetchall()
        
        return [BulkJob.from_dict(dict(row)) for row in rows]
    
    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job"""
        job = self.get_job(job_id)
        if job and job.status == JobStatus.RUNNING.value:
            job.status = JobStatus.CANCELLED.value
            job.completed_at = now()
            self.update_job(job)
            return True
        return False


# Global job queue instance
job_queue = JobQueue()


# ============================================================================
# BULK PROCESSING ENGINE
# ============================================================================

class BulkProcessor:
    """
    High-performance bulk processing engine with:
    - Parallel processing with ThreadPoolExecutor
    - Smart rate limiting
    - Progress tracking
    - Error handling and retries
    - Memory management
    """
    
    def __init__(self, max_workers: int = 10, rate_delay: float = RATE_LIMIT_DELAY, daily_limit: int = DAILY_LIMIT):
        self.max_workers = max_workers
        self.rate_delay = rate_delay
        self.daily_limit = daily_limit
        self.requests_today = 0
        self.last_request_time = None
        self.lock = threading.Lock()
        self.running = True
        
        # Initialize rate tracking
        self._init_rate_tracking()
    
    def _init_rate_tracking(self):
        """Initialize daily request tracking"""
        with finder_conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS daily_requests (
                    day TEXT PRIMARY KEY,
                    count INTEGER DEFAULT 0
                )
            """)
            # Reset if new day
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            c.execute("INSERT OR IGNORE INTO daily_requests (day, count) VALUES (?, 0)", (today,))
    
    def _check_rate_limit(self):
        """Check and enforce rate limits"""
        with self.lock:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            
            # Get current count
            with finder_conn() as c:
                row = c.execute("SELECT count FROM daily_requests WHERE day=?", (today,)).fetchone()
                current_count = row[0] if row else 0
            
            if current_count >= self.daily_limit:
                raise RuntimeError(f"Daily limit of {self.daily_limit} requests reached")
            
            # Apply delay between requests
            if self.last_request_time:
                elapsed = time.time() - self.last_request_time
                if elapsed < self.rate_delay:
                    time.sleep(self.rate_delay - elapsed)
            
            # Increment count
            with finder_conn() as c:
                c.execute("UPDATE daily_requests SET count=count+1 WHERE day=?", (today,))
            
            self.last_request_time = time.time()
            self.requests_today = current_count + 1
    
    def _safe_execute(self, func, *args, **kwargs):
        """Execute a function with rate limiting and error handling"""
        try:
            self._check_rate_limit()
            result = func(*args, **kwargs)
            return result, None
        except Exception as e:
            return None, str(e)
    
    def process_in_parallel(self, items: List[Any], processor_func, batch_size: int = 100) -> Tuple[List[Any], List[Dict[str, Any]]]:
        """
        Process items in parallel using ThreadPoolExecutor
        
        Args:
            items: List of items to process
            processor_func: Function to process each item
            batch_size: Maximum number of items per batch
        
        Returns:
            Tuple of (successful results, errors)
        """
        results = []
        errors = []
        
        # Process in batches to manage memory
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit all tasks
                future_to_item = {executor.submit(self._safe_execute, processor_func, item): item for item in batch}
                
                # Process completed futures
                for future in as_completed(future_to_item):
                    item = future_to_item[future]
                    try:
                        result, error = future.result()
                        if error:
                            errors.append({
                                "item": item,
                                "error": error,
                                "timestamp": now()
                            })
                        else:
                            results.append(result)
                    except Exception as e:
                        errors.append({
                            "item": item,
                            "error": str(e),
                            "timestamp": now()
                        })
            
            # Small delay between batches
            time.sleep(0.1)
        
        return results, errors
    
    def discover_bulk(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Discover leads from all sources in parallel
        
        Args:
            config: Configuration dictionary with:
                - keywords: Optional list of keywords
                - limit_per_source: Number of leads per source
                - sources: Optional list of specific sources to use
                - enrich: Whether to enrich discovered leads
                - deep_enrich: Whether to use deep enrichment
        
        Returns:
            Dictionary with discovered leads, statistics, and errors
        """
        from .processor import LeadProcessor
        
        processor = LeadProcessor()
        
        # Get sources to use
        sources = config.get("sources")
        if sources:
            discovery_sources = [s for s in ALL_SOURCES if s.source_type in sources]
        else:
            discovery_sources = ALL_SOURCES
        
        limit_per_source = config.get("limit_per_source", 50)
        keywords = config.get("keywords")
        
        all_leads = []
        source_stats = {}
        errors = []
        
        for source in discovery_sources:
            try:
                start_time = time.time()
                results = source.discover(
                    keywords=keywords,
                    limit=limit_per_source,
                    filters=config.get("filters")
                )
                elapsed = time.time() - start_time
                
                source_stats[source.source_type] = {
                    "leads": len(results),
                    "time": round(elapsed, 2),
                    "success": True
                }
                all_leads.extend(results)
                
                # Log discovery
                log_discovery(source.source_type, "", "completed", details=f"Found {len(results)} leads")
                
                # Respect rate limits
                time.sleep(2.0)
                
            except Exception as e:
                error_msg = str(e)
                errors.append({
                    "source": source.source_type,
                    "error": error_msg,
                    "timestamp": now()
                })
                source_stats[source.source_type] = {
                    "leads": 0,
                    "time": 0,
                    "success": False,
                    "error": error_msg
                }
                log_discovery(source.source_type, "", "error", details=error_msg)
        
        # Optionally enrich leads
        if config.get("enrich", True):
            enriched_leads, enrich_errors = self.enrich_bulk(all_leads, deep=config.get("deep_enrich", False))
            all_leads = enriched_leads
            errors.extend(enrich_errors)
        
        return {
            "total_discovered": len(all_leads),
            "sources": source_stats,
            "leads": all_leads,
            "errors": errors,
            "completed_at": now()
        }
    
    def enrich_bulk(self, leads: List[Dict[str, Any]], deep: bool = False) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Enrich a batch of leads with additional data
        
        Args:
            leads: List of lead dictionaries
            deep: Whether to use deep enrichment (all providers)
        
        Returns:
            Tuple of (enriched leads, errors)
        """
        def enrich_lead_wrapper(lead):
            """Wrapper for enriching a single lead"""
            from .processor import LeadProcessor
            processor = LeadProcessor()
            enriched = processor.enrich_lead(lead, deep=deep)
            return enriched if enriched else lead
        
        results, errors = self.process_in_parallel(
            leads,
            enrich_lead_wrapper,
            batch_size=50
        )
        
        return results, errors
    
    def score_bulk(self, leads: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Score a batch of leads using advanced scoring engine
        
        Args:
            leads: List of lead dictionaries
        
        Returns:
            Tuple of (scored leads, errors)
        """
        def score_lead_wrapper(lead):
            """Wrapper for scoring a single lead"""
            try:
                scorer = MultiDimensionalScorer()
                score_result = scorer.score(lead)
                lead["score"] = score_result.get("score", 0)
                lead["score_reason"] = score_result.get("explanation", "")
                return lead
            except Exception as e:
                return None, str(e)
        
        results, errors = self.process_in_parallel(
            leads,
            lambda l: score_lead_wrapper(l)[0],
            batch_size=100
        )
        
        # Handle errors separately
        scored_leads = []
        score_errors = []
        for i, lead in enumerate(leads):
            if i < len(results) and results[i]:
                scored_leads.append(results[i])
            else:
                score_errors.append({
                    "lead": lead.get("domain", "unknown"),
                    "error": "Failed to score",
                    "timestamp": now()
                })
        
        return scored_leads, score_errors
    
    def import_bulk(self, leads: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Import a batch of leads into the CRM database
        
        Args:
            leads: List of lead dictionaries
        
        Returns:
            Dictionary with import statistics
        """
        from .processor import LeadProcessor
        
        processor = LeadProcessor()
        
        imported = 0
        updated = 0
        errors = []
        duplicates = 0
        
        for lead in leads:
            try:
                # Validate and process lead
                processed_lead = processor.process_raw_lead(lead)
                if not processed_lead:
                    errors.append({
                        "lead": lead.get("domain", "unknown"),
                        "error": "Could not process lead",
                        "timestamp": now()
                    })
                    continue
                
                # Check for duplicate
                domain = processed_lead.get("domain")
                if domain:
                    with app.conn() as c:
                        existing = c.execute("SELECT id FROM leads WHERE domain=?", (domain,)).fetchone()
                        if existing:
                            # Update existing lead
                            cols = [k for k in processed_lead if k in COLS]
                            c.execute(
                                f"UPDATE leads SET {','.join(f'{k}=?' for k in cols)} WHERE id=?",
                                [processed_lead[k] for k in cols] + [existing[0]]
                            )
                            log_discovery("import", domain, "updated", company=processed_lead.get("company"))
                            updated += 1
                            duplicates += 1
                            continue
                
                # Import new lead
                app.upsert(processed_lead)
                log_discovery("import", domain, "imported", company=processed_lead.get("company"))
                update_leads_imported()
                imported += 1
                
            except Exception as e:
                errors.append({
                    "lead": lead.get("domain", "unknown"),
                    "error": str(e),
                    "timestamp": now()
                })
                log_discovery("import", lead.get("domain", ""), "error", details=str(e))
        
        return {
            "imported": imported,
            "updated": updated,
            "duplicates": duplicates,
            "errors": errors,
            "total": len(leads)
        }
    
    def full_pipeline(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run the complete pipeline: discovery -> enrichment -> scoring -> import
        
        Args:
            config: Configuration dictionary
        
        Returns:
            Dictionary with complete pipeline results
        """
        pipeline_stats = {
            "started_at": now(),
            "config": config,
            "stages": {},
            "total_errors": []
        }
        
        try:
            # Stage 1: Discovery
            stage_start = time.time()
            discovery_result = self.discover_bulk(config)
            pipeline_stats["stages"]["discovery"] = {
                "duration": round(time.time() - stage_start, 2),
                "leads": discovery_result["total_discovered"],
                "sources": discovery_result["sources"]
            }
            pipeline_stats["total_errors"].extend(discovery_result.get("errors", []))
            
            if not discovery_result["leads"]:
                return {
                    **pipeline_stats,
                    "completed_at": now(),
                    "status": "completed",
                    "final_leads": 0,
                    "imported": 0
                }
            
            # Stage 2: Enrichment
            stage_start = time.time()
            enriched_leads, enrich_errors = self.enrich_bulk(
                discovery_result["leads"],
                deep=config.get("deep_enrich", False)
            )
            pipeline_stats["stages"]["enrichment"] = {
                "duration": round(time.time() - stage_start, 2),
                "leads": len(enriched_leads)
            }
            pipeline_stats["total_errors"].extend(enrich_errors)
            
            # Stage 3: Scoring
            stage_start = time.time()
            scored_leads, score_errors = self.score_bulk(enriched_leads)
            pipeline_stats["stages"]["scoring"] = {
                "duration": round(time.time() - stage_start, 2),
                "leads": len(scored_leads)
            }
            pipeline_stats["total_errors"].extend(score_errors)
            
            # Stage 4: Import
            stage_start = time.time()
            import_result = self.import_bulk(scored_leads)
            pipeline_stats["stages"]["import"] = {
                "duration": round(time.time() - stage_start, 2),
                **import_result
            }
            pipeline_stats["total_errors"].extend(import_result.get("errors", []))
            
            return {
                **pipeline_stats,
                "completed_at": now(),
                "status": "completed",
                "final_leads": len(scored_leads),
                "imported": import_result["imported"],
                "updated": import_result["updated"]
            }
            
        except Exception as e:
            return {
                **pipeline_stats,
                "completed_at": now(),
                "status": "failed",
                "error": str(e)
            }


# ============================================================================
# BACKGROUND JOB PROCESSOR
# ============================================================================

class BackgroundJobProcessor:
    """
    Processes bulk jobs in the background
    """
    
    def __init__(self):
        self.processor = BulkProcessor()
        self.running = False
        self.thread = None
    
    def start(self):
        """Start the background processor"""
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._process_queue, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop the background processor"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
    
    def _process_queue(self):
        """Process jobs from the queue"""
        while self.running:
            try:
                job = job_queue.queue.get(timeout=1)
                self._process_job(job)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Error processing job: {e}", file=sys.stderr)
    
    def _process_job(self, job: BulkJob):
        """Process a single job"""
        try:
            # Update job status
            job.status = JobStatus.RUNNING.value
            job.started_at = now()
            job_queue.update_job(job)
            
            # Process based on job type
            if job.job_type == JobType.DISCOVERY.value:
                result = self.processor.discover_bulk(job.config)
                job.succeeded_items = result["total_discovered"]
                job.failed_items = len(result.get("errors", []))
                job.results = result["leads"]
                job.errors = result.get("errors", [])
            
            elif job.job_type == JobType.ENRICHMENT.value:
                # Get leads to enrich
                days = job.config.get("days", 7)
                limit = job.config.get("limit", 100)
                deep = job.config.get("deep", False)
                
                from datetime import datetime, timezone, timedelta
                cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
                
                with app.conn() as c:
                    leads_to_enrich = c.execute(
                        "SELECT * FROM leads WHERE description IS NULL AND discovered_at >= ? ORDER BY id LIMIT ?",
                        (cutoff, limit)
                    ).fetchall()
                
                leads_list = [dict(row) for row in leads_to_enrich]
                enriched, errors = self.processor.enrich_bulk(leads_list, deep=deep)
                
                # Update enriched leads
                for lead in enriched:
                    update_data = {}
                    if "description" in lead:
                        update_data["description"] = lead["description"][:500]
                    if "industry" in lead:
                        update_data["industry"] = lead["industry"]
                    if "country" in lead:
                        update_data["country"] = lead["country"]
                    if "city" in lead:
                        update_data["city"] = lead["city"]
                    
                    if update_data:
                        domain = lead.get("domain")
                        with app.conn() as c:
                            c.execute(
                                f"UPDATE leads SET {','.join(f'{k}=?' for k in update_data)} WHERE domain=?",
                                [update_data[k] for k in update_data] + [domain]
                            )
                
                job.succeeded_items = len(enriched)
                job.failed_items = len(errors)
                job.errors = errors
            
            elif job.job_type == JobType.SCORING.value:
                # Get leads to score
                limit = job.config.get("limit", 100)
                with app.conn() as c:
                    leads_to_score = c.execute(
                        "SELECT * FROM leads WHERE score IS NULL OR score=0 ORDER BY id LIMIT ?",
                        (limit,)
                    ).fetchall()
                
                leads_list = [dict(row) for row in leads_to_score]
                scored, errors = self.processor.score_bulk(leads_list)
                
                # Update scored leads
                for lead in scored:
                    with app.conn() as c:
                        c.execute(
                            "UPDATE leads SET score=?, score_reason=? WHERE domain=?",
                            (lead.get("score", 0), lead.get("score_reason", ""), lead.get("domain"))
                        )
                
                job.succeeded_items = len(scored)
                job.failed_items = len(errors)
                job.errors = errors
            
            elif job.job_type == JobType.IMPORT.value:
                # Import from external source
                result = self.processor.import_bulk(job.config.get("leads", []))
                job.succeeded_items = result["imported"] + result["updated"]
                job.failed_items = len(result.get("errors", []))
                job.results = [{"imported": result["imported"], "updated": result["updated"]}]
                job.errors = result.get("errors", [])
            
            elif job.job_type == JobType.FULL_PIPELINE.value:
                result = self.processor.full_pipeline(job.config)
                job.succeeded_items = result.get("imported", 0)
                job.failed_items = len(result.get("total_errors", []))
                job.results = [result]
                job.errors = result.get("total_errors", [])
            
            # Mark job as completed
            job.status = JobStatus.COMPLETED.value
            job.completed_at = now()
            job.progress = 100.0
            job.processed_items = job.succeeded_items + job.failed_items
            
            job_queue.update_job(job)
            
        except Exception as e:
            job.status = JobStatus.FAILED.value
            job.completed_at = now()
            job.errors.append({
                "error": str(e),
                "timestamp": now()
            })
            job_queue.update_job(job)


# ============================================================================
# BULK PROCESSING FUNCTIONS
# ============================================================================

class BulkProcessingEngine:
    """
    Main bulk processing engine interface
    """
    
    def __init__(self):
        self.processor = BulkProcessor()
        self.background_processor = BackgroundJobProcessor()
    
    def start_background(self):
        """Start background processing"""
        self.background_processor.start()
    
    def stop_background(self):
        """Stop background processing"""
        self.background_processor.stop()
    
    def run_discovery(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Run bulk discovery"""
        return self.processor.discover_bulk(config)
    
    def run_enrichment(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Run bulk enrichment"""
        days = config.get("days", 7)
        limit = config.get("limit", 100)
        deep = config.get("deep", False)
        
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        
        with app.conn() as c:
            leads_to_enrich = c.execute(
                "SELECT * FROM leads WHERE description IS NULL AND discovered_at >= ? ORDER BY id LIMIT ?",
                (cutoff, limit)
            ).fetchall()
        
        leads_list = [dict(row) for row in leads_to_enrich]
        enriched, errors = self.processor.enrich_bulk(leads_list, deep=deep)
        
        # Update enriched leads
        for lead in enriched:
            update_data = {}
            if "description" in lead:
                update_data["description"] = lead["description"][:500]
            if "industry" in lead:
                update_data["industry"] = lead["industry"]
            if "country" in lead:
                update_data["country"] = lead["country"]
            if "city" in lead:
                update_data["city"] = lead["city"]
            
            if update_data:
                domain = lead.get("domain")
                with app.conn() as c:
                    c.execute(
                        f"UPDATE leads SET {','.join(f'{k}=?' for k in update_data)} WHERE domain=?",
                        [update_data[k] for k in update_data] + [domain]
                    )
        
        return {
            "enriched": len(enriched),
            "total": len(leads_list),
            "errors": errors
        }
    
    def run_scoring(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Run bulk scoring"""
        limit = config.get("limit", 100)
        with app.conn() as c:
            leads_to_score = c.execute(
                "SELECT * FROM leads WHERE score IS NULL OR score=0 ORDER BY id LIMIT ?",
                (limit,)
            ).fetchall()
        
        leads_list = [dict(row) for row in leads_to_score]
        scored, errors = self.processor.score_bulk(leads_list)
        
        # Update scored leads
        for lead in scored:
            with app.conn() as c:
                c.execute(
                    "UPDATE leads SET score=?, score_reason=? WHERE domain=?",
                    (lead.get("score", 0), lead.get("score_reason", ""), lead.get("domain"))
                )
        
        return {
            "scored": len(scored),
            "total": len(leads_list),
            "errors": errors
        }
    
    def run_full_pipeline(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Run the complete pipeline"""
        return self.processor.full_pipeline(config)
    
    def create_job(self, job_type: str, config: Dict[str, Any]) -> BulkJob:
        """Create a new bulk job"""
        return job_queue.create_job(job_type, config)
    
    def get_job_status(self, job_id: str) -> Optional[BulkJob]:
        """Get job status"""
        return job_queue.get_job(job_id)
    
    def list_jobs(self, job_type: Optional[str] = None, status: Optional[str] = None) -> List[BulkJob]:
        """List all jobs"""
        return job_queue.list_jobs(job_type, status)
    
    def cancel_job(self, job_id: str) -> bool:
        """Cancel a job"""
        return job_queue.cancel_job(job_id)


# Global engine instance
bulk_engine = BulkProcessingEngine()


# ============================================================================
# CLI FUNCTIONS
# ============================================================================

def run_bulk_discovery(keywords=None, limit_per_source=50, enrich=True, deep_enrich=False):
    """CLI function to run bulk discovery"""
    config = {
        "keywords": keywords,
        "limit_per_source": limit_per_source,
        "enrich": enrich,
        "deep_enrich": deep_enrich
    }
    
    print(f"Starting bulk discovery with config: {config}")
    print(f"Using {len([s for s in ALL_SOURCES if keywords is None or any(kw in str(s) for kw in (keywords or []))])} sources")
    
    result = bulk_engine.run_discovery(config)
    
    print(f"\nDiscovery Results:")
    print(f"  Total discovered: {result['total_discovered']}")
    print(f"  Errors: {len(result.get('errors', []))}")
    
    if result.get('sources'):
        print(f"\nSource Statistics:")
        for source, stats in result['sources'].items():
            print(f"  {source}: {stats['leads']} leads ({'✓' if stats['success'] else '✗'}) in {stats['time']}s")
    
    if result.get('errors'):
        print(f"\nErrors:")
        for error in result['errors'][:10]:  # Show first 10 errors
            print(f"  - {error.get('source', 'unknown')}: {error['error']}")
    
    return result


def run_bulk_enrichment(days=7, limit=100, deep=False):
    """CLI function to run bulk enrichment"""
    config = {
        "days": days,
        "limit": limit,
        "deep": deep
    }
    
    print(f"Starting bulk enrichment with config: {config}")
    
    result = bulk_engine.run_enrichment(config)
    
    print(f"\nEnrichment Results:")
    print(f"  Enriched: {result['enriched']}")
    print(f"  Total: {result['total']}")
    print(f"  Errors: {len(result.get('errors', []))}")
    
    if result.get('errors'):
        print(f"\nErrors:")
        for error in result['errors'][:10]:
            print(f"  - {error}")
    
    return result


def run_bulk_scoring(limit=100):
    """CLI function to run bulk scoring"""
    config = {"limit": limit}
    
    print(f"Starting bulk scoring with config: {config}")
    
    result = bulk_engine.run_scoring(config)
    
    print(f"\nScoring Results:")
    print(f"  Scored: {result['scored']}")
    print(f"  Total: {result['total']}")
    print(f"  Errors: {len(result.get('errors', []))}")
    
    return result


def run_full_pipeline(keywords=None, limit_per_source=50, enrich=True, deep_enrich=False):
    """CLI function to run the full pipeline"""
    config = {
        "keywords": keywords,
        "limit_per_source": limit_per_source,
        "enrich": enrich,
        "deep_enrich": deep_enrich
    }
    
    print(f"Starting full pipeline with config: {config}")
    print("This will run: Discovery -> Enrichment -> Scoring -> Import")
    
    result = bulk_engine.run_full_pipeline(config)
    
    print(f"\nPipeline Results:")
    print(f"  Status: {result.get('status', 'unknown')}")
    print(f"  Final leads: {result.get('final_leads', 0)}")
    print(f"  Imported: {result.get('imported', 0)}")
    print(f"  Total errors: {len(result.get('total_errors', []))}")
    
    if result.get('stages'):
        print(f"\nStage Statistics:")
        for stage, stats in result['stages'].items():
            print(f"  {stage}: {stats.get('duration', 0)}s, {stats.get('leads', 0)} leads")
    
    return result


if __name__ == "__main__":
    # Initialize
    app.init()
    init_db()
    
    # Parse arguments
    import argparse
    parser = argparse.ArgumentParser(description="Bulk Processing Engine")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Discovery command
    disc_parser = subparsers.add_parser("discover", help="Run bulk discovery")
    disc_parser.add_argument("--keywords", nargs="*", help="Keywords to search for")
    disc_parser.add_argument("--limit", type=int, default=50, help="Leads per source")
    disc_parser.add_argument("--no-enrich", action="store_true", help="Skip enrichment")
    disc_parser.add_argument("--deep", action="store_true", help="Use deep enrichment")
    
    # Enrichment command
    enrich_parser = subparsers.add_parser("enrich", help="Run bulk enrichment")
    enrich_parser.add_argument("--days", type=int, default=7, help="Look back N days")
    enrich_parser.add_argument("--limit", type=int, default=100, help="Maximum leads to enrich")
    enrich_parser.add_argument("--deep", action="store_true", help="Use deep enrichment")
    
    # Scoring command
    score_parser = subparsers.add_parser("score", help="Run bulk scoring")
    score_parser.add_argument("--limit", type=int, default=100, help="Maximum leads to score")
    
    # Pipeline command
    pipe_parser = subparsers.add_parser("pipeline", help="Run full pipeline")
    pipe_parser.add_argument("--keywords", nargs="*", help="Keywords to search for")
    pipe_parser.add_argument("--limit", type=int, default=50, help="Leads per source")
    pipe_parser.add_argument("--no-enrich", action="store_true", help="Skip enrichment")
    pipe_parser.add_argument("--deep", action="store_true", help="Use deep enrichment")
    
    args = parser.parse_args()
    
    if args.command == "discover":
        run_bulk_discovery(
            keywords=args.keywords,
            limit_per_source=args.limit,
            enrich=not args.no_enrich,
            deep_enrich=args.deep
        )
    elif args.command == "enrich":
        run_bulk_enrichment(
            days=args.days,
            limit=args.limit,
            deep=args.deep
        )
    elif args.command == "score":
        run_bulk_scoring(limit=args.limit)
    elif args.command == "pipeline":
        run_full_pipeline(
            keywords=args.keywords,
            limit_per_source=args.limit,
            enrich=not args.no_enrich,
            deep_enrich=args.deep
        )
    else:
        parser.print_help()
