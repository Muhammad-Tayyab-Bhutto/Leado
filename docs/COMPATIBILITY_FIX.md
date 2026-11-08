# Compatibility Fix: agent.py with crm.py

## Issue

When trying to start the CRM with `python app.py`, the following error occurred:

```
AttributeError: module 'agent' has no attribute 'ensure'
```

## Root Cause

The **enhanced agent.py** renamed the `ensure()` function to `ensure_tables()` for better clarity, but **crm.py** still calls `agent.ensure()` in its migration function.

## Fix Applied

Added the `ensure()` function back to `agent.py` to maintain backward compatibility with `crm.py`:

```python
def ensure():
    """Backward compatible: create original ai_usage table."""
    with conn() as c:
        c.execute("create table if not exists ai_usage(day TEXT PRIMARY KEY, calls INT)")
```

Also updated `ensure_tables()` to call `ensure()` first, avoiding code duplication:

```python
def ensure_tables():
    """Ensure all database tables exist (original + new enhanced tables)."""
    ensure()  # Create original tables first
    with conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS research_cache(url TEXT PRIMARY KEY, content TEXT, fetched_at TEXT, expires_at TEXT)")
        # ... other new tables
```

## Verification

All compatibility tests pass:

```bash
$ python3 -c "
import agent
import crm

# Test all required functions
assert hasattr(agent, 'ensure')
assert hasattr(agent, 'usage')
assert hasattr(agent, 'research')
assert hasattr(agent, 'ai_draft')
assert hasattr(agent, 'classify_reply')

# Test crm.migrate() which uses agent.ensure()
crm.migrate()

print('✓ All compatibility tests passed!')
"
```

**Result:** ✓ All tests passed

## What Changed

### agent.py Changes

1. **Added `ensure()` function** (line ~150)
   - Maintains backward compatibility with crm.py
   - Creates the original `ai_usage` table

2. **Updated `ensure_tables()` function** (line ~155)
   - Now calls `ensure()` first
   - Removed duplicate `ai_usage` table creation
   - Creates new enhanced tables (research_cache, llm_metrics, etc.)

### No Changes Required to crm.py

The `crm.py` file remains **unchanged**. It continues to call `agent.ensure()` as before.

## Functions Available

The enhanced `agent.py` now provides both old and new functions:

### Original Functions (Backward Compatible)
- ✅ `ensure()` - Creates ai_usage table
- ✅ `usage()` - Returns AI usage statistics
- ✅ `research(lead_id)` - Research a lead
- ✅ `ai_draft(lead_id)` - Generate email draft
- ✅ `classify_reply(text)` - Classify email replies
- ✅ `doctor()` - Health check

### New Functions (Enhanced Features)
- ✅ `ensure_tables()` - Creates all tables (original + new)
- ✅ `llm()` - Enhanced LLM with multi-model fallback
- ✅ `gather(website)` - Enhanced page gathering
- ✅ `research_enhanced(lead_id)` - Enhanced research (optional)
- ✅ `ai_draft_enhanced(lead_id)` - Multi-variant drafting (optional)
- ✅ `classify_reply_enhanced(text)` - Enhanced classification (optional)
- ✅ `clear_caches()` - Clear all caches
- ✅ `metrics` - Monitoring metrics object

## Testing

### Quick Test
```bash
python3 -c "import agent; agent.ensure(); print('✓ ensure() works')"
```

### Full Compatibility Test
```bash
python3 -c "
import agent
import crm

# All functions exist
assert hasattr(agent, 'ensure')
assert hasattr(agent, 'usage')
assert hasattr(agent, 'research')
assert hasattr(agent, 'ai_draft')
assert hasattr(agent, 'classify_reply')

# Migration works
crm.migrate()

print('✓ Full compatibility confirmed!')
"
```

### Start CRM
```bash
python app.py
```

## Impact

- **Zero breaking changes** - All existing code continues to work
- **Full backward compatibility** - crm.py works without modification
- **Enhanced features available** - New functions for advanced usage
- **No data loss** - All existing data remains intact

## Files Modified

- ✅ `agent.py` - Added `ensure()` function, updated `ensure_tables()`
- ✅ `crm.py` - No changes required
- ✅ `app.py` - No changes required
- ✅ `leads.db` - No changes required

## Migration Status

✅ **COMPLETE** - All compatibility issues resolved

Your CRM should now start successfully with:
```bash
python app.py
```
