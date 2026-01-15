# Logging Guide

RLM-MCP produces structured JSON logs designed for production observability. Every operation gets a unique correlation ID, enabling you to trace the entire journey of a request through your system.

## Quick Start

### Enable JSON Logging

In `~/.rlm-mcp/config.yaml`:

```yaml
log_level: "INFO"
structured_logging: true
log_file: "/var/log/rlm-mcp.log"
```

Restart the server, and logs flow as newline-delimited JSON.

### Your First Query

```bash
# Get recent operations
tail -f /var/log/rlm-mcp.log | jq .

# Filter by session
cat /var/log/rlm-mcp.log | jq 'select(.session_id == "your-session-id")'

# Find errors
cat /var/log/rlm-mcp.log | jq 'select(.level == "ERROR")'
```

## Log Format Specification

### Standard Fields

Every log entry contains:

```json
{
  "timestamp": "2026-01-15T10:30:45.123456Z",
  "level": "INFO",
  "logger": "rlm_mcp.server",
  "message": "Human-readable description"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | ISO 8601 | When the event occurred (UTC) |
| `level` | string | DEBUG, INFO, WARNING, ERROR |
| `logger` | string | Which module logged (e.g., `rlm_mcp.server`) |
| `message` | string | Human-readable event description |

### Operation Fields

Tool operations add:

```json
{
  "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "session_id": "session-123",
  "operation": "rlm.session.create",
  "duration_ms": 42,
  "success": true
}
```

| Field | Type | Present When | Description |
|-------|------|--------------|-------------|
| `correlation_id` | UUID | All operations | Unique ID for this tool call |
| `session_id` | string | Has session | Which session (null for session.create) |
| `operation` | string | Tool calls | Canonical tool name (e.g., `rlm.search.query`) |
| `duration_ms` | int | Completed ops | Execution time in milliseconds |
| `success` | bool | Completed ops | Whether operation succeeded |

### Context Fields

Additional context varies by operation:

```json
{
  "doc_count": 15,
  "query": "search term",
  "result_count": 5,
  "input_keys": ["session_id", "query"],
  "error": "Session not found: xyz",
  "error_type": "ValueError"
}
```

## Common Queries

### By Session

Track all operations for a session:

```bash
cat /var/log/rlm-mcp.log | jq 'select(.session_id == "session-abc123")'
```

See the session lifecycle:
```bash
cat /var/log/rlm-mcp.log \
  | jq 'select(.session_id == "session-abc123") | {
      timestamp,
      operation,
      duration_ms,
      success
    }'
```

### By Operation Type

All searches:
```bash
cat /var/log/rlm-mcp.log | jq 'select(.operation == "rlm.search.query")'
```

All document loads:
```bash
cat /var/log/rlm-mcp.log | jq 'select(.operation == "rlm.docs.load")'
```

### By Correlation ID

Follow a specific operation through all its log entries:

```bash
CORRELATION_ID="a1b2c3d4-e5f6-7890-abcd-ef1234567890"
cat /var/log/rlm-mcp.log | jq --arg id "$CORRELATION_ID" 'select(.correlation_id == $id)'
```

This shows:
- Operation start
- Any intermediate logs
- Operation completion or error

### Errors and Warnings

All errors:
```bash
cat /var/log/rlm-mcp.log | jq 'select(.level == "ERROR")'
```

Failed operations:
```bash
cat /var/log/rlm-mcp.log | jq 'select(.success == false)'
```

Errors with context:
```bash
cat /var/log/rlm-mcp.log \
  | jq 'select(.level == "ERROR") | {
      timestamp,
      operation,
      error,
      error_type,
      session_id
    }'
```

### Performance Analysis

Slow operations (>1 second):
```bash
cat /var/log/rlm-mcp.log | jq 'select(.duration_ms > 1000)'
```

Average duration by operation:
```bash
cat /var/log/rlm-mcp.log \
  | jq -s 'group_by(.operation)
    | map({
        operation: .[0].operation,
        avg_ms: (map(.duration_ms) | add / length)
      })'
```

95th percentile latency:
```bash
cat /var/log/rlm-mcp.log \
  | jq -s 'map(select(.duration_ms))
    | sort_by(.duration_ms)
    | .[length * 0.95 | floor]'
```

## Log Levels

### DEBUG

Detailed internal state for development:

```json
{
  "level": "DEBUG",
  "message": "No persisted index found for session xyz",
  "session_id": "xyz"
}
```

**When to use**: Development, troubleshooting specific issues

**Performance impact**: Verbose, may affect performance

### INFO (Default)

Normal operations and important events:

```json
{
  "level": "INFO",
  "message": "Index built successfully",
  "session_id": "xyz",
  "doc_count": 150
}
```

**When to use**: Production default

**What you see**:
- Operation start/complete
- Index builds
- Session lifecycle events

### WARNING

Recoverable issues that should be investigated:

```json
{
  "level": "WARNING",
  "message": "Failed to persist index: disk full",
  "session_id": "xyz"
}
```

**When to use**: Always monitor warnings in production

**Common warnings**:
- Index persistence failures (disk full, permissions)
- Partial batch failures (some files couldn't load)
- Deprecated feature usage

### ERROR

Operations that failed:

```json
{
  "level": "ERROR",
  "message": "Failed rlm.docs.load: File not found",
  "session_id": "xyz",
  "error": "File not found: /path/to/file.txt",
  "error_type": "FileNotFoundError"
}
```

**When to use**: Always alert on errors in production

**Common errors**:
- Invalid session IDs
- Budget exceeded
- File not found
- Database corruption

## Production Monitoring

### Log Aggregation

Ship logs to your aggregation system:

**Elasticsearch/Kibana**:
```bash
tail -f /var/log/rlm-mcp.log | while read line; do
  curl -X POST "localhost:9200/rlm-logs/_doc" \
    -H 'Content-Type: application/json' \
    -d "$line"
done
```

**Datadog**:
```yaml
# datadog-agent config
logs:
  - type: file
    path: /var/log/rlm-mcp.log
    service: rlm-mcp
    source: python
    sourcecategory: rlm
```

**Splunk**:
```bash
# Monitor as JSON
./splunk add monitor /var/log/rlm-mcp.log \
  -sourcetype _json
```

### Alerting Rules

#### Error Rate

Alert if error rate exceeds 5%:

```bash
# Calculate error rate in last hour
ERROR_COUNT=$(cat /var/log/rlm-mcp.log \
  | jq -s 'map(select(.level == "ERROR")) | length')

TOTAL_COUNT=$(cat /var/log/rlm-mcp.log \
  | jq -s 'map(select(.level)) | length')

ERROR_RATE=$(echo "scale=2; $ERROR_COUNT / $TOTAL_COUNT * 100" | bc)

if (( $(echo "$ERROR_RATE > 5" | bc -l) )); then
  echo "ALERT: Error rate is $ERROR_RATE%"
fi
```

#### Slow Operations

Alert if 95th percentile latency exceeds 2 seconds:

```bash
P95=$(cat /var/log/rlm-mcp.log \
  | jq -s 'map(select(.duration_ms))
    | sort_by(.duration_ms)
    | .[length * 0.95 | floor].duration_ms')

if (( P95 > 2000 )); then
  echo "ALERT: P95 latency is ${P95}ms"
fi
```

### Dashboards

Key metrics to track:

1. **Request Rate**: Operations per minute
2. **Error Rate**: % of failed operations
3. **Latency**: P50, P95, P99 by operation type
4. **Active Sessions**: Concurrent sessions
5. **Index Cache**: Hit rate, load times

Example Grafana query (using Loki):
```logql
# Operations per minute
rate({job="rlm-mcp"} | json | operation != "" [1m])

# Error rate
sum(rate({job="rlm-mcp"} | json | level="ERROR" [5m]))
  /
sum(rate({job="rlm-mcp"} | json | level != "" [5m]))

# P95 latency
histogram_quantile(0.95,
  sum(rate({job="rlm-mcp"} | json | duration_ms > 0 [5m]))
  by (operation)
)
```

## Troubleshooting with Logs

### "Why is this session slow?"

```bash
# Get all operations for session
cat /var/log/rlm-mcp.log \
  | jq 'select(.session_id == "session-slow")
    | {timestamp, operation, duration_ms}' \
  | sort_by(.duration_ms) \
  | reverse
```

Look for:
- Operations with high `duration_ms`
- `index_built: true` (index building is slow first time)
- Large `doc_count` or `result_count`

### "What happened to session XYZ?"

```bash
# Get session timeline
cat /var/log/rlm-mcp.log \
  | jq 'select(.session_id == "session-xyz")
    | {timestamp, level, message, operation, success}' \
  | less
```

Look for:
- Last successful operation
- Any ERROR level entries
- Whether session was closed

### "Which operations are failing?"

```bash
# Group errors by operation
cat /var/log/rlm-mcp.log \
  | jq -s 'map(select(.success == false))
    | group_by(.operation)
    | map({operation: .[0].operation, count: length})'
```

### "Is the index persisting?"

```bash
# Check for persistence logs
cat /var/log/rlm-mcp.log \
  | jq 'select(.message | contains("persist"))'
```

Look for:
- "Index persisted successfully"
- "Index cache hit (disk)"
- Warnings about persistence failures

## Log Rotation

Configure log rotation to prevent disk filling:

`/etc/logrotate.d/rlm-mcp`:
```
/var/log/rlm-mcp.log {
    daily
    rotate 30
    compress
    delaycompress
    notifempty
    create 0644 rlm rlm
    sharedscripts
    postrotate
        # Reload logger if needed
    endscript
}
```

Test rotation:
```bash
sudo logrotate -f /etc/logrotate.d/rlm-mcp
```

## Performance Considerations

### Log Volume

At INFO level, expect:
- ~10 log entries per operation (start, intermediate, complete)
- ~100-500 bytes per entry
- ~1-5KB per operation

For 1000 operations/hour:
- ~5MB/hour uncompressed
- ~120MB/day uncompressed
- ~5-10MB/day compressed

### Log Impact

Logging overhead:
- Console logging: ~0.1-0.5ms per entry
- File logging: ~0.01-0.1ms per entry
- Structured formatting: ~0.05ms per entry

**Impact**: Negligible (<1%) for most workloads.

### Tuning

Reduce log volume:
```yaml
log_level: "WARNING"  # Only warnings and errors
```

Reduce log detail:
```yaml
structured_logging: false  # Human-readable format (less verbose)
```

## Human-Readable Format

For development, use human-readable logs:

```yaml
structured_logging: false
```

Output:
```
2026-01-15 10:30:45,123 INFO [rlm_mcp.server] Starting rlm.session.create
2026-01-15 10:30:45,165 INFO [rlm_mcp.server] Completed rlm.session.create (42ms)
```

**Pros**: Easier to read during development
**Cons**: Harder to query, parse, and analyze

## Summary

### Quick Reference

```bash
# Tail logs with pretty-printing
tail -f /var/log/rlm-mcp.log | jq .

# Filter by session
jq 'select(.session_id == "xyz")'

# Filter by operation
jq 'select(.operation == "rlm.search.query")'

# Filter by level
jq 'select(.level == "ERROR")'

# Get slow operations
jq 'select(.duration_ms > 1000)'

# Follow correlation ID
jq --arg id "$ID" 'select(.correlation_id == $id)'
```

### Best Practices

1. **Use correlation IDs**: Track operations end-to-end
2. **Monitor error rate**: Alert if >5% in production
3. **Watch latency**: Track P95/P99 by operation
4. **Rotate logs**: Prevent disk filling
5. **Aggregate**: Ship to centralized logging system

### Further Reading

- `CLAUDE.md` → "Structured Logging" section
- `MIGRATION_v0.1_to_v0.2.md` → "Structured Logging" section
- `README.md` → "Logging (v0.2.0)" section

---

**Happy logging! May your correlation IDs always trace true.**
