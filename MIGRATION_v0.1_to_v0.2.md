# Migration Guide: v0.1.3 → v0.2.0

Welcome to RLM-MCP v0.2.0. This release brings production-ready features while maintaining complete backwards compatibility with v0.1.3. Your existing code continues to work without changes.

## Summary

**Breaking Changes**: None
**New Features**: Index persistence, concurrent session safety, structured logging, batch loading
**Migration Time**: < 5 minutes
**Downtime Required**: None (rolling restart)

## What's New

### 1. Persistent Indexes (Survives Restarts)

**Before (v0.1.3)**:
- Indexes built on first search
- Lost on server restart
- Rebuilt on next search

**After (v0.2.0)**:
- Indexes automatically saved on session close
- Loaded from disk in <100ms
- Survives server restarts

**What This Means**: Your users can resume sessions after restart without waiting for index rebuilds. First search after restart is fast (~100ms) instead of slow (~1s).

**Action Required**: None. Automatic for all sessions.

### 2. Concurrent Session Safety

**Before (v0.1.3)**:
- Single-user deployments only
- No protection against race conditions

**After (v0.2.0)**:
- Per-session locks prevent races
- Multiple users can run concurrent sessions safely
- Index builds happen once, not multiple times

**What This Means**: You can deploy RLM-MCP for your entire team without race conditions or duplicate work.

**Action Required**: None. Automatic for all operations.

### 3. Structured Logging

**Before (v0.1.3)**:
- Basic print statements
- Hard to filter or analyze

**After (v0.2.0)**:
- JSON structured logs
- Correlation IDs track operations
- Production observability ready

**What This Means**: You can trace user sessions, debug production issues, and monitor performance with standard log analysis tools.

**Action Required**: Optional config to enable (see Configuration section).

### 4. Batch Document Loading

**Before (v0.1.3)**:
- Files loaded sequentially
- Database inserts one-by-one

**After (v0.2.0)**:
- Concurrent file loading (default: 20 at once)
- Batch database inserts
- 2-3x faster for large imports

**What This Means**: Loading 100 files that took 30 seconds now takes 10 seconds. Better memory management with bounded concurrency.

**Action Required**: None. Automatic for all `rlm.docs.load` calls.

## Upgrade Steps

### Step 1: Install v0.2.0

```bash
pip install --upgrade rlm-mcp
```

Or with uv:

```bash
uv pip install --upgrade rlm-mcp
```

### Step 2: Update Configuration (Optional)

If you have a `~/.rlm-mcp/config.yaml`, you can add new options. All are optional with sensible defaults.

```yaml
# Existing settings (unchanged)
data_dir: ~/.rlm-mcp
default_max_tool_calls: 500
default_max_chars_per_response: 50000
default_max_chars_per_peek: 10000

# New settings (optional, these are the defaults)
max_concurrent_loads: 20   # Max concurrent file loads
max_file_size_mb: 100      # Reject files larger than this

# Logging (optional, default is INFO + JSON)
log_level: "INFO"              # DEBUG, INFO, WARNING, ERROR
structured_logging: true       # JSON format
log_file: null                 # Set to path if you want file logging
```

### Step 3: Restart Server

```bash
# Stop old server
pkill rlm-mcp

# Start new server
uv run rlm-mcp
```

Or with systemd:

```bash
sudo systemctl restart rlm-mcp
```

### Step 4: Verify Upgrade

```bash
# Check version (should show v0.2.0)
uv run python -c "import rlm_mcp; print(rlm_mcp.__version__)"

# Run tests to verify
uv run pytest
```

**Expected**: All tests pass. If you see any failures, check `~/.rlm-mcp/` permissions.

## Compatibility Notes

### Backwards Compatibility

✅ **Tool APIs**: All tool signatures unchanged
✅ **Response formats**: Same fields, same structure
✅ **Configuration**: v0.1.3 configs work without changes
✅ **Data storage**: Existing SQLite databases work as-is
✅ **Client code**: No changes required

### New Behavior

#### Index Persistence

- **First startup after upgrade**: No indexes on disk yet, will build on first search (same as v0.1.3)
- **After first session close**: Index saved to `~/.rlm-mcp/indexes/{session_id}/`
- **On restart**: Index loads automatically if found

#### File Size Limits

- **New**: Files larger than 100MB rejected by default
- **Migration**: If you need larger files, increase `max_file_size_mb` in config

#### Structured Logging

- **New**: JSON logs by default (stdout)
- **Migration**: Set `structured_logging: false` for old format

## Performance Impact

### Expected Improvements

| Operation | v0.1.3 | v0.2.0 | Improvement |
|-----------|--------|--------|-------------|
| First search after restart | ~1s (rebuild) | ~100ms (load) | **10x faster** |
| Load 100 files | ~30s | ~10s | **3x faster** |
| Concurrent sessions | Not safe | Safe + fast | **Team-ready** |

### Storage Impact

- **Indexes**: ~1-5MB per session (depends on corpus size)
- **Location**: `~/.rlm-mcp/indexes/{session_id}/`
- **Cleanup**: Deleted automatically on session close if documents change

### Memory Impact

- **Improved**: Bounded semaphores prevent OOM on large batches
- **Expected**: Same or better memory usage than v0.1.3

## Troubleshooting

### Indexes Not Loading After Restart

**Symptom**: Search slow after restart despite closed session.

**Cause**: Session not closed before restart, or index became stale.

**Fix**: Close sessions before restart for index persistence:
```python
await rlm_session_close(session_id=session_id)
```

### File Loading Slower Than Expected

**Symptom**: Batch loading not faster, or even slower.

**Cause**: `max_concurrent_loads` set too high/low for your system.

**Fix**: Tune in config:
```yaml
# For SSDs and fast CPUs
max_concurrent_loads: 50

# For slower systems
max_concurrent_loads: 10
```

### Logs Too Verbose

**Symptom**: JSON logs overwhelming console.

**Fix**: Adjust log level:
```yaml
log_level: "WARNING"  # Only warnings and errors
```

Or route to file:
```yaml
log_file: "/var/log/rlm-mcp.log"
```

### "File too large" Errors

**Symptom**: `ValueError: File too large` for >100MB files.

**Cause**: New file size safety limit.

**Fix**: Increase limit if legitimate:
```yaml
max_file_size_mb: 500  # Allow up to 500MB files
```

## Rollback Procedure

If you need to rollback to v0.1.3:

```bash
# 1. Stop server
pkill rlm-mcp

# 2. Downgrade
pip install rlm-mcp==0.1.3

# 3. Restart
uv run rlm-mcp
```

**Note**: Indexes created by v0.2.0 are ignored by v0.1.3 (no corruption). Data is safe.

## New Features in Detail

### Index Persistence

Indexes are saved on session close with:
- **Atomic writes**: Temp file + rename (crash-safe)
- **Fingerprinting**: Detects stale indexes automatically
- **Corruption recovery**: Gracefully rebuilds if corrupted

See `CLAUDE.md` → "Index Persistence" section for details.

### Concurrency Model

Per-session locks protect:
- Index builds (only one build per session)
- Session close (clean shutdown)
- Budget increments (no lost updates)

**Limitation**: Single-process only. Multi-process deployments need external coordination (Redis, file locks, etc.).

See `CLAUDE.md` → "Concurrency Model" section.

### Structured Logging

Every operation logs:
- **Correlation ID**: Unique UUID for tracing
- **Session ID**: Which session
- **Operation**: Which tool
- **Duration**: How long it took
- **Success**: Whether it succeeded

Query logs with `jq`:
```bash
# All operations for a session
cat rlm.log | jq 'select(.session_id == "session-123")'

# Failed operations
cat rlm.log | jq 'select(.success == false)'

# Slow operations (>1s)
cat rlm.log | jq 'select(.duration_ms > 1000)'
```

See `docs/LOGGING.md` for complete guide.

### Batch Loading

Document loading now:
1. Loads files concurrently (bounded by semaphore)
2. Collects all documents in memory
3. Inserts all in single database transaction

**Memory safety**: Semaphore limits concurrent loads to prevent OOM.

**Partial failure**: Errors in some files don't block others.

## Next Steps

1. **Enable logging**: Set `log_file` in config for production monitoring
2. **Tune concurrency**: Adjust `max_concurrent_loads` for your workload
3. **Test concurrent sessions**: Deploy for multiple team members
4. **Monitor performance**: Use logs to identify slow operations

## Support

- **Issues**: https://github.com/adrianwedd/rlm-mcp/issues
- **Documentation**: See `CLAUDE.md` for developer guide
- **Logging Guide**: See `docs/LOGGING.md` for observability

---

**Welcome to v0.2.0! Your RLM-MCP is now production-ready for team environments.**
