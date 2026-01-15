# GitHub Issues for v0.2.0
**Ready to paste into GitHub Issues**

Copy each issue below and create it in your repository. Use labels to organize by priority and type.

---

## Epic: v0.2.0 Production Readiness

**Issue #1: [EPIC] v0.2.0 Production Readiness Release**

```markdown
## Overview
Production-ready release with persistent indexes, concurrency safety, and structured logging.

**Target**: 3 weeks from start
**Success Criteria**: Beta-ready for multi-user team environments

## Features
- [ ] Persistent BM25 indexes (survives restarts)
- [ ] Concurrent session safety (multi-user ready)
- [ ] Structured logging (JSON format with correlation IDs)
- [ ] Batch document loading (2-3x faster)

## Milestones
- Week 1: Foundation (logging, locks, index persistence)
- Week 2: Testing & documentation
- Week 3: Release prep & alpha testing

## Dependencies
- Blocks: v0.2.1 Performance Release
- Related: #2-#12 (all sub-issues)

## Links
- Design Doc: `SOLUTION_DESIGN.md`
- Patches: `SOLUTION_DESIGN_PATCHES.md`
- Checklist: `IMPLEMENTATION_CHECKLIST_v0.2.0.md`
```

**Labels**: `epic`, `v0.2.0`, `priority:high`

---

## Critical Patches (Week 1, Day 1)

**Issue #2: [BUG] Fix span error handling to prevent crashes**

```markdown
## Problem
In `_span_get()`, when `span is None`, the code tries to access `span.document_id`, which crashes.

## Solution
- Add `chunk_index` field to `Span` model for better error messages
- Fix error handling to check for None before accessing properties
- Use new `SpanNotFoundError` with helpful context

## Files to Change
- `src/rlm_mcp/models.py` - Add `chunk_index: int | None`
- `src/rlm_mcp/tools/chunks.py` - Fix error handling, populate `chunk_index`
- `src/rlm_mcp/errors.py` - Create `SpanNotFoundError`

## Tests
- [ ] `test_span_not_found_error_helpful()` - Verify error message is clear

## Acceptance Criteria
- Accessing non-existent span shows: "Chunk #3 from document 'server.py' not found"
- No crashes when span is None

## Related
- Patch #8 in `SOLUTION_DESIGN_PATCHES.md`
```

**Labels**: `bug`, `critical`, `v0.2.0`, `priority:highest`

---

**Issue #3: [BUG] Fix logging tests to parse JSON correctly**

```markdown
## Problem
Tests use `caplog.records[i].message` which is the raw message, not formatted JSON.
`json.loads(record.message)` will fail.

## Solution
Use `StringIO` handler to capture formatted output instead of raw records.

## Files to Change
- `tests/test_logging.py` - Rewrite to use handler + StringIO

## Tests
- [ ] `test_structured_logs()` - Parse JSON from handler output
- [ ] `test_correlation_ids()` - Verify uniqueness
- [ ] `test_correlation_id_cleanup()` - Verify no leaks

## Acceptance Criteria
- Tests parse actual JSON output
- Tests don't access `.message` directly

## Related
- Patch #3 in `SOLUTION_DESIGN_PATCHES.md`
```

**Labels**: `bug`, `testing`, `v0.2.0`, `priority:high`

---

## Core Features (Week 1)

**Issue #4: [FEATURE] Structured logging with JSON format**

```markdown
## Overview
Add structured logging with JSON output, correlation IDs, and rich context.

## Implementation
- Create `src/rlm_mcp/logging_config.py`
  - `StructuredFormatter` - JSON formatter
  - `StructuredLogger` - Wrapper with extra fields
  - `correlation_id_var` - Context variable for request correlation
  - `configure_logging()` - Setup function

- Update `src/rlm_mcp/server.py`
  - Use `StructuredLogger` in `tool_handler` decorator
  - Set/clear correlation ID per operation
  - Log start, completion, and errors with context

- Update `src/rlm_mcp/config.py`
  - Add `log_level: str = "INFO"`
  - Add `structured_logging: bool = True`
  - Add `log_file: str | None = None`

## Log Format
```json
{
  "timestamp": "2026-01-15T10:30:45.123Z",
  "level": "INFO",
  "logger": "rlm_mcp.tools.search",
  "session_id": "abc-123",
  "operation": "rlm.search.query",
  "correlation_id": "req-456",
  "duration_ms": 234,
  "message": "BM25 search completed",
  "extra": {"query": "...", "match_count": 5}
}
```

## Tests
- [ ] `test_structured_logs()` - JSON format
- [ ] `test_correlation_ids()` - Unique per operation
- [ ] `test_correlation_id_cleanup()` - No leaks
- [ ] `test_session_id_in_logs()` - Session context included

## Documentation
- [ ] Add "Structured Logging" section to `CLAUDE.md`
- [ ] Document log format and filtering examples

## Acceptance Criteria
- All tool calls emit structured JSON logs
- Correlation IDs unique and tracked
- Logs filterable by session, operation, level

## Related
- Depends on: #3 (test fix)
- Design: Priority 1.3 in `SOLUTION_DESIGN.md`
```

**Labels**: `feature`, `v0.2.0`, `priority:high`, `observability`

---

**Issue #5: [FEATURE] Per-session concurrency locks**

```markdown
## Overview
Add `asyncio.Lock` per session to prevent race conditions in index building and budget tracking.

## Implementation
- Update `src/rlm_mcp/server.py`
  - Add `_session_locks: dict[str, asyncio.Lock]`
  - Add `get_session_lock(session_id)` method
  - Add `release_session_lock(session_id)` method
  - Document single-process limitation

- Update `src/rlm_mcp/storage/database.py`
  - Make `increment_tool_calls()` atomic using SQL UPDATE

- Update `src/rlm_mcp/tools/session.py`
  - Wrap `_session_close()` with session lock

- Update index methods:
  - Lock around `get_or_build_index()`
  - Lock around `cache_index()`

## Tests
- [ ] `test_concurrent_index_builds()` - Only builds once
- [ ] `test_concurrent_budget_increments()` - Accurate count
- [ ] `test_session_lock_prevents_race()` - Cache consistency

## Documentation
- [ ] Add "Concurrency Model" section to `CLAUDE.md`
- [ ] Document single-process limitation (Patch #2)
- [ ] Outline future multi-process support options

## Acceptance Criteria
- Concurrent searches only build index once
- Budget tracking accurate under concurrent load
- No race conditions in integration tests

## Related
- Depends on: #4 (logging for debugging)
- Blocks: #6 (index persistence needs locks)
- Design: Priority 1.2 in `SOLUTION_DESIGN.md`
```

**Labels**: `feature`, `v0.2.0`, `priority:high`, `concurrency`

---

**Issue #6: [FEATURE] Persistent BM25 index with atomic writes**

```markdown
## Overview
Persist BM25 indexes to disk so they survive server restarts. Use atomic writes and fingerprinting for data safety.

## Implementation
- Create `src/rlm_mcp/index/persistence.py`
  - `IndexPersistence` class
  - `save_index()` - Atomic write with temp files + `os.replace()`
  - `load_index()` - Load with corruption handling
  - `compute_doc_fingerprint()` - SHA256 of content hashes
  - `is_index_stale()` - Check doc_count, fingerprint, tokenizer
  - `invalidate_index()` - Delete session directory

- Update `src/rlm_mcp/server.py`
  - Add `self.index_persistence = IndexPersistence(...)`
  - Update `get_or_build_index()` to try loading from disk
  - Add staleness checking with fingerprints
  - Use locks from #5

- Update `src/rlm_mcp/tools/session.py`
  - Persist index on `_session_close()`

- Update `src/rlm_mcp/tools/docs.py`
  - Invalidate persisted index on `_docs_load()`

- Update `src/rlm_mcp/storage/database.py`
  - Add `count_documents(session_id)` for staleness check

## Metadata Format
```json
{
  "version": "1.0",
  "created_at": "2026-01-15T10:00:00Z",
  "doc_count": 10,
  "index_type": "bm25",
  "index_schema": 1,
  "tokenizer": "unicode",
  "doc_fingerprint": "sha256_..."
}
```

## Tests
- [ ] `test_index_persists_on_close()` - File created
- [ ] `test_index_loads_on_restart()` - Loads from disk
- [ ] `test_index_invalidates_on_doc_load()` - Invalidated correctly
- [ ] `test_atomic_write_prevents_corruption()` - No temp files left
- [ ] `test_tokenizer_change_invalidates_index()` - Fingerprint works
- [ ] `test_doc_edit_invalidates_index()` - Content hash changes detected
- [ ] `test_corrupted_index_rebuilds()` - Graceful recovery
- [ ] `test_index_load_performance()` - <100ms for typical index

## Acceptance Criteria
- Index persists on session close
- Index loads on server restart (if valid)
- Stale indexes detected and rebuilt
- Corrupted indexes handled gracefully
- Load time <100ms for 1M char corpus

## Related
- Depends on: #5 (needs locks to avoid race conditions)
- Patch #1 in `SOLUTION_DESIGN_PATCHES.md`
- Design: Priority 1.1 in `SOLUTION_DESIGN.md`
```

**Labels**: `feature`, `v0.2.0`, `priority:highest`, `performance`

---

**Issue #7: [FEATURE] Batch document loading with memory safety**

```markdown
## Overview
Load multiple documents in parallel with concurrency limits to prevent memory spikes.

## Implementation
- Update `src/rlm_mcp/config.py`
  - Add `max_concurrent_loads: int = 20`
  - Add `max_file_size_mb: int = 100`

- Update `src/rlm_mcp/storage/database.py`
  - Add `create_documents_batch(documents: list[Document])`
  - Use `executemany()` for single transaction

- Update `src/rlm_mcp/tools/docs.py`
  - Add `max_concurrent` parameter to `_docs_load()`
  - Use `asyncio.Semaphore(max_concurrent)` to limit concurrency
  - Use `create_documents_batch()` for insert

## Tests
- [ ] `test_batch_loading_performance()` - 2-3x faster than sequential
- [ ] `test_partial_batch_failure()` - Errors don't fail entire batch
- [ ] `test_batch_loading_memory_bounded()` - Memory stays reasonable
- [ ] `test_max_concurrent_enforced()` - No more than N concurrent loads

## Acceptance Criteria
- Batch loading 2-3x faster than v0.1.3
- Memory usage bounded (no spikes)
- Partial failures handled gracefully
- Configurable concurrency limit

## Related
- Depends on: #6 (needs persistence complete first)
- Patch #6 in `SOLUTION_DESIGN_PATCHES.md`
- Design: Priority 2.3 in `SOLUTION_DESIGN.md`
```

**Labels**: `feature`, `v0.2.0`, `priority:medium`, `performance`

---

## Testing & Documentation (Week 2)

**Issue #8: [TEST] Integration testing for v0.2.0 features**

```markdown
## Overview
Comprehensive integration tests to verify all v0.2.0 features work together.

## Test Scenarios
- [ ] End-to-end workflow with persistence
  - Create session → load docs → search → close
  - Restart server (clear cache)
  - Search again (loads from disk)

- [ ] Concurrent operations
  - Multiple clients to same session
  - Multiple sessions in parallel
  - No race conditions

- [ ] Logging integration
  - All operations emit structured logs
  - Correlation IDs unique across operations
  - Logs parseable as JSON

- [ ] Performance regression
  - Run `test_large_corpus.py`
  - Verify no slowdowns vs v0.1.3
  - Index operations meet performance targets

## Acceptance Criteria
- All 58 tests passing
- No regressions in existing functionality
- New features work in integration
- Performance targets met

## Related
- Depends on: #4, #5, #6, #7
- Blocks: #11 (release)
```

**Labels**: `testing`, `v0.2.0`, `priority:high`

---

**Issue #9: [DOCS] Documentation for v0.2.0**

```markdown
## Overview
Update all documentation for v0.2.0 features and create migration guide.

## Files to Update
- [ ] `README.md`
  - Add v0.2.0 features to "Features" section
  - Update "Status & Validation" (58 tests, beta ready)
  - Add "Configuration" section with new fields
  - Add "Logging" section with JSON example

- [ ] `CLAUDE.md`
  - Add "Structured Logging" section
  - Add "Concurrency Model" section
  - Update "Common Commands" with logging config
  - Add troubleshooting section

- [ ] Create `MIGRATION_v0.1_to_v0.2.md`
  - Breaking changes (none)
  - New features
  - Configuration changes
  - Upgrade steps
  - Rollback steps

- [ ] Create `docs/LOGGING.md`
  - Log format specification
  - Common queries (jq examples)
  - Filtering by session/operation/correlation_id
  - Log levels and configuration
  - Troubleshooting with logs

- [ ] Update `EVALUATION_REPORT.md`
  - Mark Priority 1 complete
  - Update assessment to "Beta Ready"

## Acceptance Criteria
- All new features documented
- Migration guide clear and tested
- Logging guide has working examples
- README accurate

## Related
- Depends on: #8 (features complete)
- Blocks: #11 (release needs docs)
```

**Labels**: `documentation`, `v0.2.0`, `priority:high`

---

## Release (Week 3)

**Issue #10: [TASK] Performance benchmarking for v0.2.0**

```markdown
## Overview
Benchmark all performance-critical features and record results.

## Benchmarks to Create
- [ ] Index persistence
  - Build index for 1M char corpus
  - Measure save time (target: <100ms)
  - Measure load time (target: <100ms)

- [ ] Concurrency
  - 10 concurrent searches
  - Verify only 1 index build
  - Measure throughput

- [ ] Batch loading
  - Load 100 documents sequentially (baseline)
  - Load 100 documents with batching
  - Verify 2-3x speedup

## Deliverables
- [ ] `benchmarks/v0.2.0_benchmarks.py`
- [ ] `benchmarks/results_v0.2.0.txt` (with system specs)
- [ ] Add performance notes to README

## Acceptance Criteria
- All benchmarks run successfully
- Results meet performance targets
- Results documented

## Related
- Depends on: #8 (features complete)
- Blocks: #11 (release needs benchmarks)
```

**Labels**: `performance`, `v0.2.0`, `priority:medium`

---

**Issue #11: [RELEASE] v0.2.0 Release**

```markdown
## Pre-Release Checklist
- [ ] All 58 tests passing
- [ ] No lint errors (`ruff check`)
- [ ] No type errors (`mypy src/`)
- [ ] Documentation complete (#9)
- [ ] Benchmarks recorded (#10)
- [ ] Migration guide tested
- [ ] Alpha users tested and approved

## Release Steps
- [ ] Update version to 0.2.0 in `pyproject.toml`
- [ ] Create `CHANGELOG.md` with all changes
- [ ] Merge `v0.2.0-dev` → `main`
- [ ] Tag release: `git tag -a v0.2.0 -m "v0.2.0 - Production Readiness"`
- [ ] Push tag: `git push origin v0.2.0`
- [ ] Build package: `uv build`
- [ ] Publish to PyPI: `uv publish`
- [ ] Verify installation: `pip install rlm-mcp==0.2.0`

## Post-Release
- [ ] Monitor for issues (24-48 hours)
- [ ] Update documentation site
- [ ] Announce release
- [ ] Notify alpha users

## Success Metrics
- [ ] Test count: 58 (up from 43)
- [ ] Performance: Index <100ms, batch 2-3x faster
- [ ] No critical issues in first 48 hours

## Related
- Depends on: #2-#10 (all sub-issues)
- Closes: #1 (epic)
```

**Labels**: `release`, `v0.2.0`, `priority:highest`

---

## Optional Quick Wins (for v0.2.1)

**Issue #12: [ENHANCEMENT] Improved Unicode tokenization**

```markdown
## Overview
Enhance tokenization to preserve hyphens/apostrophes and handle accented characters better.

## Implementation
- Update `src/rlm_mcp/index/tokenizers.py`
  - Improve `UnicodeTokenizer` pattern to keep internal punctuation
  - Handle contractions (don't, can't)
  - Handle hyphenated compounds (state-of-the-art)

## Tests
- [ ] `test_unicode_tokenizer_handles_contractions()`
- [ ] `test_unicode_tokenizer_handles_accents()`
- [ ] `test_unicode_tokenizer_strips_leading_trailing()`

## Acceptance Criteria
- "don't" tokenized as single word
- "state-of-the-art" preserved
- Accented characters normalized correctly

## Related
- Patch #4 in `SOLUTION_DESIGN_PATCHES.md`
- For v0.2.1 (not blocking v0.2.0)
```

**Labels**: `enhancement`, `v0.2.1`, `priority:low`, `search`

---

## Issue Labels to Create

Create these labels in GitHub:

| Label | Color | Description |
|-------|-------|-------------|
| `epic` | #3E4B9E | Epic issue tracking |
| `v0.2.0` | #0E8A16 | v0.2.0 milestone |
| `v0.2.1` | #1D76DB | v0.2.1 milestone |
| `bug` | #D73A4A | Something isn't working |
| `critical` | #B60205 | Must fix immediately |
| `feature` | #A2EEEF | New feature |
| `enhancement` | #84B6EB | Improvement to existing feature |
| `testing` | #D4C5F9 | Testing related |
| `documentation` | #0075CA | Documentation |
| `performance` | #FBCA04 | Performance improvement |
| `concurrency` | #F9D0C4 | Concurrency/threading |
| `observability` | #C2E0C6 | Logging/monitoring |
| `search` | #BFD4F2 | Search functionality |
| `release` | #5319E7 | Release related |
| `priority:highest` | #B60205 | Drop everything |
| `priority:high` | #D93F0B | Important |
| `priority:medium` | #FBCA04 | Normal priority |
| `priority:low` | #0E8A16 | Can wait |

---

## Project Board Setup

Create a GitHub Project with these columns:

1. **Backlog** - All issues created but not started
2. **Ready** - Ready to work on (dependencies met)
3. **In Progress** - Currently being worked on
4. **Review** - Code complete, in review
5. **Done** - Merged and closed

**Initial board state**:

| Backlog | Ready | In Progress | Review | Done |
|---------|-------|-------------|--------|------|
| #4-#11  | #2, #3 | - | - | - |

**Move issues as work progresses**:
- #2, #3 → Ready (Day 1)
- #2, #3 → In Progress → Review → Done (Day 1)
- #4 → Ready → In Progress (Day 2)
- etc.

---

## Milestone Setup

Create milestone: **v0.2.0 - Production Readiness**

**Settings**:
- Due date: 3 weeks from start
- Description: "Beta release with persistence, concurrency, and logging"

**Add to milestone**:
- Issues #1-#11

---

## Quick Copy Instructions

1. **Create labels**: Copy label table → Settings → Labels → New labels
2. **Create milestone**: Copy milestone settings → Issues → Milestones → New
3. **Create issues**: Copy each issue markdown → New Issue → Paste → Add labels/milestone
4. **Create project board**: Projects → New → Board → Add columns → Add issues
5. **Start work**: Move #2 and #3 to "In Progress"

---

**Tip**: Use GitHub CLI for bulk issue creation:

```bash
# Create all issues at once
gh issue create --title "[BUG] Fix span error handling" --body "$(cat issue_2.md)" --label bug,critical,v0.2.0

# Or use a script:
for file in issues/*.md; do
  gh issue create --body-file "$file"
done
```
