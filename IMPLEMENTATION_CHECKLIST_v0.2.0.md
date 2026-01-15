# v0.2.0 Implementation Checklist
**Production Readiness Release**
**Target**: 3 weeks from start

This checklist is ordered for maximum efficiency and minimal backtracking. Each item blocks the ones that depend on it.

---

## Pre-flight (Day 0)

- [ ] Create `v0.2.0-dev` branch from `main`
- [ ] Set up project board with "Backlog", "In Progress", "Review", "Done" columns
- [ ] Run current test suite to establish baseline (should be 43/43 passing)

---

## Week 1: Foundation + Critical Patches

### Day 1: Fix Critical Bugs (2-4 hours)

**Why first**: Prevents test failures and crashes during development

- [ ] **Patch #8**: Fix span error lookup bug
  - [ ] Add `chunk_index` field to `Span` model
  - [ ] Update `_chunk_create` to populate `chunk_index`
  - [ ] Fix `_span_get` error handling (don't access `span.document_id` when `span is None`)
  - [ ] Update `src/rlm_mcp/errors.py` with `SpanNotFoundError`
  - [ ] Test: `test_span_not_found_error_helpful`
  - [ ] Commit: "Fix span error handling and add chunk_index for better errors"

- [ ] **Patch #3**: Fix logging test strategy
  - [ ] Update `tests/test_logging.py` to capture formatted output
  - [ ] Use `StringIO` + handler instead of `caplog.records`
  - [ ] Test correlation ID cleanup
  - [ ] Commit: "Fix logging tests to parse JSON correctly"

### Day 2: Structured Logging Infrastructure (6-8 hours)

**Why now**: Makes everything else easier to debug

- [ ] Create `src/rlm_mcp/logging_config.py`
  - [ ] Implement `StructuredFormatter` with JSON output
  - [ ] Implement `StructuredLogger` wrapper
  - [ ] Add `correlation_id_var` context variable
  - [ ] Add `configure_logging()` function

- [ ] Update `src/rlm_mcp/config.py`
  - [ ] Add `log_level: str = "INFO"`
  - [ ] Add `structured_logging: bool = True`
  - [ ] Add `log_file: str | None = None`

- [ ] Update `src/rlm_mcp/server.py`
  - [ ] Import and use `StructuredLogger`
  - [ ] Update `tool_handler` decorator to:
    - [ ] Set correlation ID at start
    - [ ] Log operation start with `input_keys`
    - [ ] Log operation completion with `duration_ms`
    - [ ] Log errors with `error_type` and context
    - [ ] Reset correlation ID in `finally`
  - [ ] Add `configure_logging()` call in `run_server()`

- [ ] Write `tests/test_logging.py`
  - [ ] Test JSON log format
  - [ ] Test correlation ID uniqueness
  - [ ] Test correlation ID cleanup
  - [ ] Test session_id in logs
  - [ ] Test duration tracking

- [ ] Update `CLAUDE.md`
  - [ ] Add "Structured Logging" section
  - [ ] Document log format and fields
  - [ ] Add examples of filtering/querying logs

- [ ] Commit: "Add structured logging with correlation IDs"

### Day 3: Per-Session Concurrency Locks (6-8 hours)

**Why now**: Needed before index persistence to avoid race conditions

- [ ] Update `src/rlm_mcp/server.py`
  - [ ] Add `_session_locks: dict[str, asyncio.Lock]`
  - [ ] Add `_lock_manager_lock: asyncio.Lock`
  - [ ] Implement `get_session_lock(session_id) -> Lock`
  - [ ] Implement `release_session_lock(session_id)`
  - [ ] Add docstring explaining single-process limitation

- [ ] Update `src/rlm_mcp/storage/database.py`
  - [ ] Make `increment_tool_calls()` atomic using UPDATE
  - [ ] Add test: `test_atomic_budget_increment`

- [ ] Update `src/rlm_mcp/tools/session.py`
  - [ ] Wrap `_session_close()` body with session lock
  - [ ] Release lock after close completes

- [ ] Update `src/rlm_mcp/server.py` (index methods)
  - [ ] Add lock acquisition to `get_or_build_index()`
  - [ ] Add lock acquisition to `cache_index()`

- [ ] Write `tests/test_concurrency.py`
  - [ ] `test_concurrent_index_builds()` (only builds once)
  - [ ] `test_concurrent_budget_increments()` (accurate count)
  - [ ] `test_session_lock_prevents_race()` (index cache)

- [ ] Update `CLAUDE.md`
  - [ ] Add "Concurrency Model" section
  - [ ] Document single-process limitation (**Patch #2**)
  - [ ] Outline future multi-process options

- [ ] Commit: "Add per-session locks for concurrency safety"

### Day 4-5: Index Persistence (12-16 hours)

**Critical Path**: This is the biggest feature in v0.2.0

- [ ] Create `src/rlm_mcp/index/persistence.py`
  - [ ] Implement `IndexPersistence` class
  - [ ] Implement `save_index()` with **atomic writes** (Patch #1):
    - [ ] Write to temp file
    - [ ] Use `os.replace()` for atomic rename
    - [ ] Include metadata with fingerprints
  - [ ] Implement `load_index()` with corruption handling:
    - [ ] Try to load pickle
    - [ ] Catch `UnpicklingError` and invalidate
    - [ ] Return `None` for corrupted indexes
  - [ ] Implement `compute_doc_fingerprint()`:
    - [ ] Sort docs by ID
    - [ ] Hash concatenated content_hashes
    - [ ] Return SHA256 hex digest
  - [ ] Implement `is_index_stale()`:
    - [ ] Check doc_count
    - [ ] Check doc_fingerprint
    - [ ] Check tokenizer name
  - [ ] Implement `invalidate_index()`:
    - [ ] Delete session directory
    - [ ] Handle missing directories gracefully

- [ ] Update `src/rlm_mcp/server.py`
  - [ ] Add `self.index_persistence = IndexPersistence(...)`
  - [ ] Update `get_or_build_index()`:
    - [ ] Check in-memory cache first
    - [ ] Try loading from disk
    - [ ] Check staleness with fingerprints
    - [ ] Log decisions (cache hit, stale, rebuild)
    - [ ] Use locks (from Day 3)
  - [ ] Add `cache_index()` helper (locked)

- [ ] Update `src/rlm_mcp/tools/session.py`
  - [ ] In `_session_close()`:
    - [ ] Get current tokenizer config
    - [ ] Call `index_persistence.save_index()`
    - [ ] Delete from cache after persisting

- [ ] Update `src/rlm_mcp/tools/docs.py`
  - [ ] In `_docs_load()`:
    - [ ] Invalidate cached index (memory)
    - [ ] Invalidate persisted index (disk)

- [ ] Update `src/rlm_mcp/tools/search.py`
  - [ ] Update `_search_query()` to use `get_or_build_index()`
  - [ ] Cache built index via `cache_index()`

- [ ] Update `src/rlm_mcp/storage/database.py`
  - [ ] Add `count_documents(session_id) -> int`

- [ ] Write `tests/test_index_persistence.py`
  - [ ] `test_index_persists_on_close()`
  - [ ] `test_index_loads_on_restart()`
  - [ ] `test_index_invalidates_on_doc_load()`
  - [ ] `test_atomic_write_prevents_corruption()` (check no .tmp files)
  - [ ] `test_tokenizer_change_invalidates_index()` (**Patch #1**)
  - [ ] `test_doc_edit_invalidates_index()` (fingerprint check)
  - [ ] `test_corrupted_index_rebuilds()` (graceful recovery)
  - [ ] `test_index_load_performance()` (<100ms for typical index)

- [ ] Commit: "Add persistent BM25 index with atomic writes and fingerprinting"

---

## Week 2: Testing & Documentation

### Day 6: Integration Testing (8 hours)

**Why now**: Catch interactions between persistence + locks + logging

- [ ] Run full test suite: `uv run pytest -v`
  - [ ] All existing 43 tests pass
  - [ ] All new tests pass (~15 new tests)
  - [ ] No warnings or deprecations

- [ ] End-to-end workflow tests:
  - [ ] Create session → load docs → search → close → restart → search (uses cached index)
  - [ ] Concurrent sessions don't interfere
  - [ ] Logging produces valid JSON for all operations

- [ ] Performance regression testing:
  - [ ] Run `tests/test_large_corpus.py`
  - [ ] Verify no slowdowns vs v0.1.3 baseline
  - [ ] Index save/load time < 100ms

- [ ] Fix any issues found

- [ ] Commit: "Add integration tests for v0.2.0 features"

### Day 7: Batch Loading + Memory Safety (6-8 hours)

**Optional Quick Win**: This can ship separately as v0.2.1 if needed

- [ ] Update `src/rlm_mcp/config.py`
  - [ ] Add `max_concurrent_loads: int = 20`
  - [ ] Add `max_file_size_mb: int = 100`

- [ ] Update `src/rlm_mcp/storage/database.py`
  - [ ] Add `create_documents_batch(documents: list[Document])`
  - [ ] Use `executemany()` for single transaction

- [ ] Update `src/rlm_mcp/tools/docs.py`
  - [ ] Add `max_concurrent` parameter to `_docs_load()`
  - [ ] Create `asyncio.Semaphore(max_concurrent)`
  - [ ] Wrap loaders with semaphore
  - [ ] Use `create_documents_batch()` for insert

- [ ] Write `tests/test_batch_loading.py`
  - [ ] `test_batch_loading_performance()` (faster than sequential)
  - [ ] `test_partial_batch_failure()` (errors don't fail entire load)
  - [ ] `test_batch_loading_memory_bounded()` (**Patch #6**)
  - [ ] `test_max_concurrent_enforced()` (no more than N at once)

- [ ] Commit: "Add batch document loading with memory safety"

### Day 8-9: Documentation & Migration Guide (12 hours)

**Critical for users**: Clear docs make adoption easier

- [ ] Update `README.md`
  - [ ] Add v0.2.0 features to "Features" section:
    - [x] Persistent BM25 indexes (survives restarts)
    - [x] Concurrent session safety (multi-user ready)
    - [x] Structured logging (JSON format with correlation IDs)
    - [x] Batch document loading (2-3x faster)
  - [ ] Update "Status & Validation" section:
    - [ ] Bump to ~58 tests passing
    - [ ] Add "Production-ready for team environments"
  - [ ] Update "Configuration" section with new fields
  - [ ] Add "Logging" section with JSON example

- [ ] Update `CLAUDE.md`
  - [ ] Add "Logging" section (already done on Day 2)
  - [ ] Add "Concurrency Model" section (already done on Day 3)
  - [ ] Update "Common Commands" with logging config
  - [ ] Add troubleshooting tips for index corruption

- [ ] Create `MIGRATION_v0.1_to_v0.2.md`
  - [ ] Breaking changes: None
  - [ ] New features: Index persistence, structured logging, batch loading
  - [ ] Configuration changes: New fields in config.yaml
  - [ ] Upgrade steps:
    1. Install v0.2.0: `pip install --upgrade rlm-mcp`
    2. Update config.yaml (optional)
    3. Restart server
    4. First search will rebuild index (one-time)
  - [ ] Rollback steps (if needed)

- [ ] Create `docs/LOGGING.md` (detailed logging guide)
  - [ ] Log format specification
  - [ ] Common queries (jq examples)
  - [ ] Filtering by session, operation, correlation_id
  - [ ] Log levels and configuration
  - [ ] Troubleshooting with logs

- [ ] Update `EVALUATION_REPORT.md`
  - [ ] Mark Priority 1 items as complete
  - [ ] Update "Overall Assessment" to "Beta Ready"

- [ ] Commit: "Add v0.2.0 documentation and migration guide"

---

## Week 3: Release Preparation

### Day 10: Code Review & Cleanup (6-8 hours)

**Quality gate**: Last chance to fix issues

- [ ] Self-review entire changeset:
  - [ ] Read every changed file
  - [ ] Check for TODO comments
  - [ ] Verify error messages are helpful
  - [ ] Check for hardcoded values (should be config)

- [ ] Run linters:
  - [ ] `uv run mypy src/` (no type errors)
  - [ ] `uv run ruff check src/` (no lint errors)
  - [ ] Fix any issues

- [ ] Code cleanup:
  - [ ] Remove debug prints
  - [ ] Remove commented-out code
  - [ ] Consolidate duplicate logic
  - [ ] Add missing docstrings

- [ ] Review test coverage:
  - [ ] All new features have tests
  - [ ] All patches have regression tests
  - [ ] Edge cases covered

- [ ] Commit: "Code cleanup and lint fixes for v0.2.0"

### Day 11: Performance Benchmarking (6-8 hours)

**Prove it works**: Quantify improvements

- [ ] Create `benchmarks/v0.2.0_benchmarks.py`
  - [ ] Index persistence benchmark:
    - [ ] Build index for 1M char corpus
    - [ ] Measure save time
    - [ ] Measure load time
    - [ ] Verify <100ms for load
  - [ ] Concurrency benchmark:
    - [ ] 10 concurrent searches
    - [ ] Verify only 1 index build
    - [ ] Measure throughput
  - [ ] Batch loading benchmark:
    - [ ] Load 100 documents sequentially (baseline)
    - [ ] Load 100 documents with batching
    - [ ] Verify 2-3x speedup

- [ ] Run benchmarks and record results:
  - [ ] Create `benchmarks/results_v0.2.0.txt`
  - [ ] Include system specs
  - [ ] Compare to v0.1.3 baseline

- [ ] Add performance notes to README:
  - [ ] Index save: ~Xms for 1M chars
  - [ ] Index load: ~Xms (one-time after restart)
  - [ ] Batch loading: X.Xx faster than sequential

- [ ] Commit: "Add performance benchmarks for v0.2.0"

### Day 12-13: Alpha Testing & Bug Fixes (12 hours)

**Real-world validation**: Catch issues before release

- [ ] Deploy to test environment:
  - [ ] Fresh install from `v0.2.0-dev` branch
  - [ ] Run through real workflows
  - [ ] Test with Claude Code integration

- [ ] Alpha testing checklist:
  - [ ] Create session, load documents
  - [ ] Search works, index persists
  - [ ] Restart server, search again (uses cached index)
  - [ ] Load more docs, index invalidates
  - [ ] Concurrent operations work
  - [ ] Logs are structured JSON
  - [ ] Errors are helpful

- [ ] Fix any bugs found:
  - [ ] Reproduce bug
  - [ ] Write failing test
  - [ ] Fix bug
  - [ ] Verify test passes
  - [ ] Commit with descriptive message

- [ ] Performance validation:
  - [ ] No memory leaks
  - [ ] No slow operations
  - [ ] Index persistence works on restart

- [ ] User acceptance:
  - [ ] Get feedback from 1-2 alpha users
  - [ ] Address critical feedback
  - [ ] Document known issues (if any)

### Day 14: Release (4 hours)

**Ship it**: Final checks and release

- [ ] Pre-release checklist:
  - [ ] All tests passing (should be ~58 tests)
  - [ ] No lint errors
  - [ ] Documentation complete
  - [ ] Migration guide reviewed
  - [ ] Benchmarks recorded

- [ ] Version bump:
  - [ ] Update `pyproject.toml`: `version = "0.2.0"`
  - [ ] Update version in code if needed
  - [ ] Commit: "Bump version to 0.2.0"

- [ ] Create CHANGELOG.md:
  ```markdown
  # Changelog

  ## [0.2.0] - 2026-XX-XX

  ### Added
  - Persistent BM25 indexes (survives server restarts)
  - Concurrent session safety with per-session locks
  - Structured logging with JSON format and correlation IDs
  - Batch document loading (2-3x faster for bulk operations)
  - Atomic index writes to prevent corruption
  - Index staleness detection with content fingerprinting

  ### Changed
  - Tool call budget increment now atomic
  - Index building uses locks to prevent concurrent builds

  ### Fixed
  - Span error messages now include helpful context
  - Logging tests properly parse JSON output
  - Session.create now counts toward tool call budget

  ### Technical
  - 58 tests (up from 43)
  - Performance: Index load <100ms, batch loading 2-3x faster
  - Configuration: Added log_level, structured_logging, max_concurrent_loads
  ```

- [ ] Merge to main:
  - [ ] Create PR: `v0.2.0-dev` → `main`
  - [ ] Review checklist:
    - [ ] All tests pass in CI
    - [ ] Documentation complete
    - [ ] Migration guide clear
  - [ ] Merge PR
  - [ ] Delete `v0.2.0-dev` branch

- [ ] Tag release:
  ```bash
  git tag -a v0.2.0 -m "v0.2.0 - Production Readiness Release"
  git push origin v0.2.0
  ```

- [ ] Publish to PyPI:
  ```bash
  uv build
  uv publish
  ```

- [ ] Verify installation:
  ```bash
  pip install rlm-mcp==0.2.0
  rlm-mcp --version
  ```

- [ ] Announce release:
  - [ ] Update README badge (if applicable)
  - [ ] Post to relevant channels/forums
  - [ ] Notify alpha users

---

## Post-Release

### Immediate (within 24 hours)

- [ ] Monitor for issues:
  - [ ] Check for crash reports
  - [ ] Review user feedback
  - [ ] Watch for installation issues

- [ ] Update documentation site (if exists)

### Week 4 (Optional - Quick Win Sprint)

If you want to ship v0.2.1 quickly with the remaining Patches:

- [ ] **Patch #4**: Unicode tokenization improvements (1 day)
- [ ] **Patch #5**: Better highlight data structure (1 day)
- [ ] **Patch #7**: AST offset optimization (0.5 days)
- [ ] Testing + release (1.5 days)

---

## Success Metrics

Track these throughout the 3 weeks:

### Code Quality
- [ ] Test count: 43 → ~58 (35% increase)
- [ ] Test pass rate: 100%
- [ ] Lint errors: 0
- [ ] Type errors: 0

### Performance
- [ ] Index save time: <100ms for 1M chars
- [ ] Index load time: <100ms
- [ ] Batch loading: 2-3x faster than v0.1.3
- [ ] No memory leaks in long-running tests

### Stability
- [ ] No crashes in 24-hour test run
- [ ] Concurrent operations work correctly
- [ ] Index persistence works across restarts
- [ ] Graceful degradation (corrupted index → rebuild)

### User Experience
- [ ] Error messages helpful and actionable
- [ ] Logs parseable and filterable
- [ ] Migration from v0.1.3 smooth
- [ ] Documentation clear and complete

---

## Contingency Plans

### If falling behind schedule:

1. **Cut scope**: Move batch loading to v0.2.1
   - Priority 1 only: Index persistence + locks + logging
   - Release v0.2.0 with core features
   - Ship v0.2.1 a week later with performance

2. **Extend timeline**: Add 3-5 days if needed
   - Week 1: Foundation (same)
   - Week 2: Testing + docs (same)
   - Week 3 extended: More testing, alpha feedback

3. **Parallel work**: Split if multiple developers
   - Dev A: Index persistence + locks
   - Dev B: Logging + batch loading
   - Both: Integration + testing

### If blocking issues found:

1. **Critical bug**: Fix immediately
   - Write failing test
   - Fix bug
   - Verify all tests pass
   - Continue checklist

2. **Performance issue**: Profile and optimize
   - Use cProfile or memory_profiler
   - Identify bottleneck
   - Optimize hot path
   - Re-benchmark

3. **Design flaw**: Escalate and decide
   - Document issue clearly
   - Propose alternatives
   - Decide: fix now, defer to v0.2.1, or document limitation

---

## Daily Standup Template

Use this to track progress:

**Yesterday**:
- Completed: [list items from checklist]
- Blocked on: [any issues]

**Today**:
- Plan to complete: [next items from checklist]
- Risk areas: [potential problems]

**Metrics**:
- Tests passing: X/Y
- Commits today: N
- Hours spent: H
- On track: Yes/No

---

## Final Quality Gate

Before tagging v0.2.0, verify **all** of these:

- [ ] ✅ All 58 tests passing
- [ ] ✅ No lint errors (`ruff check`)
- [ ] ✅ No type errors (`mypy src/`)
- [ ] ✅ Documentation complete and accurate
- [ ] ✅ Migration guide tested by fresh user
- [ ] ✅ Performance benchmarks meet targets
- [ ] ✅ Changelog accurate and complete
- [ ] ✅ Alpha users report no critical issues
- [ ] ✅ Version number updated everywhere
- [ ] ✅ Git tag created
- [ ] ✅ PyPI package published and installable

---

**Estimated Total Time**: 100-120 hours (3 weeks at 40 hours/week)

**Critical Path**: Patches (#3, #8) → Logging → Locks → Index Persistence → Testing → Docs → Release

**Risk Level**: Low (if checklist followed in order)

**Success Probability**: 0.85+ (with proper execution)
