# v0.2.0 Implementation Guide
**Quick Start for Production Readiness Release**

This guide ties together all the planning documents and gets you started immediately.

## 📋 Document Map

| Document | Purpose | When to Use |
|----------|---------|-------------|
| **EVALUATION_REPORT.md** | Current state assessment | Reference for "why v0.2.0" |
| **SOLUTION_DESIGN.md** | Detailed technical designs | Reference during implementation |
| **SOLUTION_DESIGN_PATCHES.md** | Fixes for edge cases | Apply before starting features |
| **IMPLEMENTATION_CHECKLIST_v0.2.0.md** | Day-by-day task list | Daily work guide |
| **GITHUB_ISSUES_v0.2.0.md** | Ready-to-paste issues | Project setup |
| **CRITICAL_PATH_v0.2.0.md** | Dependency graph & timeline | Track progress, make decisions |
| **This file** | Quick start guide | You are here! |

## 🚀 Get Started (Next 30 Minutes)

### Step 1: Create Branch & Setup (10 min)

```bash
# Create development branch
git checkout -b v0.2.0-dev

# Verify current state
uv run pytest -v  # Should see 43/43 passing

# Create project structure
mkdir -p docs benchmarks
```

### Step 2: Create GitHub Issues (10 min)

```bash
# Open GITHUB_ISSUES_v0.2.0.md
# Copy-paste each issue into GitHub

# Or use GitHub CLI:
gh issue create --title "[EPIC] v0.2.0 Production Readiness" \
  --body "$(cat .github/issue_templates/epic_v0.2.0.md)"

# Create labels
gh label create "v0.2.0" --color "0E8A16"
gh label create "critical" --color "B60205"
# ... (see GITHUB_ISSUES_v0.2.0.md for full list)
```

### Step 3: Read Patches (10 min)

Open `SOLUTION_DESIGN_PATCHES.md` and read:
- Patch #8 (span error bug) - **You'll fix this first**
- Patch #3 (logging tests) - **You'll fix this second**
- Patch #1 (atomic writes) - **Critical for index persistence**

Mark any questions or concerns.

## 📅 Your First Day (Day 1)

**Goal**: Fix critical bugs that would cause test failures

**Time**: 4-6 hours

**Checklist**: See `IMPLEMENTATION_CHECKLIST_v0.2.0.md` → "Day 1"

### Morning: Fix Span Error Bug (2-3 hours)

1. **Read the patch**: `SOLUTION_DESIGN_PATCHES.md` → Patch #8
2. **Open files**:
   - `src/rlm_mcp/models.py`
   - `src/rlm_mcp/tools/chunks.py`
   - Create `src/rlm_mcp/errors.py`

3. **Make changes**:
   ```python
   # models.py - Add field
   class Span(BaseModel):
       # ... existing fields ...
       chunk_index: int | None = None

   # chunks.py - Fix error handling
   if span is None:
       raise SpanNotFoundError(
           span_id=span_id,
           session_id=session_id,
           hint="..."
       )
   ```

4. **Test**:
   ```bash
   uv run pytest tests/test_error_handling.py::test_span_not_found_error_helpful -v
   ```

5. **Commit**:
   ```bash
   git add -A
   git commit -m "Fix span error handling and add chunk_index for better errors

   - Add chunk_index field to Span model
   - Fix _span_get to check span is None before accessing properties
   - Add SpanNotFoundError with helpful context
   - Resolves #2"
   ```

### Afternoon: Fix Logging Tests (2-3 hours)

1. **Read the patch**: `SOLUTION_DESIGN_PATCHES.md` → Patch #3
2. **Open file**: `tests/test_logging.py`
3. **Rewrite tests** to use StringIO handler
4. **Run tests**:
   ```bash
   uv run pytest tests/test_logging.py -v
   ```
5. **Commit**:
   ```bash
   git commit -m "Fix logging tests to parse JSON correctly

   - Use StringIO handler to capture formatted output
   - Test correlation ID cleanup
   - Resolves #3"
   ```

### End of Day: Status Check

- [ ] 2 commits made
- [ ] All tests still passing (43/43)
- [ ] Issues #2 and #3 closed
- [ ] Ready for Day 2 (structured logging)

## 🗓️ Week-by-Week Overview

### Week 1: Foundation (5 days)
- **Day 1**: Bug fixes (you just did this!)
- **Day 2**: Structured logging
- **Day 3**: Session locks
- **Day 4-5**: Index persistence

**End Goal**: Core features implemented, tests passing

### Week 2: Polish (5 days)
- **Day 6**: Integration testing
- **Day 7**: Batch loading (optional)
- **Day 8-9**: Documentation

**End Goal**: Features documented, ready for alpha

### Week 3: Release (4 days)
- **Day 10**: Code review & cleanup
- **Day 11**: Benchmarks
- **Day 12-13**: Alpha testing
- **Day 14**: Release to PyPI

**End Goal**: v0.2.0 published, users installing

## 🎯 Daily Workflow

Each morning:

1. **Check yesterday's commits**
   ```bash
   git log --oneline -5
   ```

2. **Run tests** (should be 100% passing)
   ```bash
   uv run pytest -v
   ```

3. **Open checklist**: `IMPLEMENTATION_CHECKLIST_v0.2.0.md`
4. **Find today's section**: e.g., "Day 2: Structured Logging"
5. **Check off items** as you complete them
6. **Update GitHub issues**: Move cards on project board

Each evening:

1. **Commit your work** (even if incomplete)
   ```bash
   git add -A
   git commit -m "WIP: Day X - Feature Y (N% complete)"
   git push origin v0.2.0-dev
   ```

2. **Update progress** in GitHub issue comments
3. **Note blockers** or questions for tomorrow
4. **Check critical path**: Are you on schedule?

## 🔥 When Things Go Wrong

### Problem: Tests Failing

```bash
# Run specific test
uv run pytest tests/test_foo.py::test_bar -v

# Run with debugging
uv run pytest tests/test_foo.py::test_bar -v -s --pdb

# Check logs (after Day 2)
cat /tmp/rlm-test.log | jq '.session_id' | sort | uniq
```

**Decision**: Fix immediately if critical path item, defer if not.

### Problem: Behind Schedule

**Day 5 checkpoint**: Should have index persistence working

- ✅ **On track**: Tests passing → Continue
- ⚠️ **Slight delay**: Use Day 6 buffer
- ❌ **Major issues**: Cut #7 (batch loading), focus on core

**Day 9 checkpoint**: Should have docs complete

- ✅ **On track**: Continue to alpha
- ⚠️ **Incomplete**: Skip benchmarks, finish docs
- ❌ **Way behind**: Cut #10 (benchmarks), ship with minimal perf data

### Problem: Alpha Users Find Bug

**Severity assessment**:

- **Critical** (crashes, data loss): Fix immediately, delay release
- **Major** (broken feature): Fix within 24h, delay 1-2 days
- **Minor** (UX issue): Document as known issue, fix in v0.2.1

**Process**:
1. Reproduce bug
2. Write failing test
3. Fix bug
4. Verify all tests pass
5. Re-deploy to alpha
6. Get confirmation from alpha user

## 📊 Progress Tracking

### Visual Dashboard (Update Daily)

```
Week 1: Foundation
[████████░░] 80% (Day 4 of 5)

Critical Path:
✅ #2 Fix span error
✅ #3 Fix logging tests
✅ #4 Structured logging
⏳ #5 Session locks (in progress)
⬜ #6 Index persistence (next)

Tests: 47/47 passing (new tests added!)
```

### Key Metrics (Track Daily)

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Tests passing | 100% | ___ | ___ |
| Test count | 58+ | ___ | ___ |
| Commits | ~20-30 | ___ | ___ |
| Issues closed | 11 | ___ | ___ |
| Days elapsed | 14 | ___ | ___ |

### Decision Points (Mark When Reached)

- [ ] Day 5: Index persistence tests pass? (GO/NO-GO)
- [ ] Day 6: Integration tests pass? (GO/NO-GO)
- [ ] Day 9: Docs complete? (GO/NO-GO)
- [ ] Day 13: Alpha approval? (GO/NO-GO)

## 🛠️ Tools & Commands

### Development

```bash
# Run tests
uv run pytest                    # All tests
uv run pytest -v                 # Verbose
uv run pytest -k "test_index"    # Filter by name
uv run pytest --lf               # Last failed

# Type checking
uv run mypy src/

# Linting
uv run ruff check src/
uv run ruff check --fix src/

# Format code
uv run ruff format src/
```

### Debugging

```bash
# Run with print debugging
uv run pytest tests/test_foo.py -v -s

# Drop into debugger on failure
uv run pytest tests/test_foo.py --pdb

# Profile performance
uv run python -m cProfile -s cumtime script.py
```

### Git Workflow

```bash
# Daily workflow
git status
git add src/rlm_mcp/new_file.py tests/test_new.py
git commit -m "Add feature X

- Implement Y
- Add tests for Z
- Resolves #N"
git push origin v0.2.0-dev

# Checkpoint (end of day)
git commit -m "WIP: Day 3 checkpoint - locks 60% complete"
git push

# Undo changes (if needed)
git checkout -- file.py          # Discard changes
git reset HEAD~1                 # Undo last commit (keep changes)
git reset --hard HEAD~1          # Undo last commit (discard changes)
```

### Release Commands

```bash
# Version bump
# Edit pyproject.toml: version = "0.2.0"
git commit -m "Bump version to 0.2.0"

# Tag release
git tag -a v0.2.0 -m "v0.2.0 - Production Readiness Release"
git push origin v0.2.0

# Build and publish
uv build
uv publish

# Verify
pip install rlm-mcp==0.2.0
python -c "import rlm_mcp; print(rlm_mcp.__version__)"
```

## 📚 Reference Information

### File Organization

```
rlm-mcp/
├── src/rlm_mcp/
│   ├── server.py              # Core server (you'll modify heavily)
│   ├── config.py              # Add new config fields
│   ├── logging_config.py      # CREATE (Day 2)
│   ├── errors.py              # CREATE (Day 1)
│   ├── index/
│   │   ├── bm25.py           # Existing
│   │   ├── tokenizers.py     # CREATE (v0.2.1)
│   │   └── persistence.py    # CREATE (Day 4)
│   ├── storage/
│   │   └── database.py       # Add batch methods
│   └── tools/
│       ├── session.py        # Modify (persistence)
│       ├── docs.py           # Modify (batch loading)
│       ├── chunks.py         # Modify (error fix)
│       └── search.py         # Modify (use persistence)
├── tests/
│   ├── test_logging.py       # REWRITE (Day 1)
│   ├── test_concurrency.py   # CREATE (Day 3)
│   ├── test_index_persistence.py  # CREATE (Day 4-5)
│   └── test_batch_loading.py # CREATE (Day 7)
├── docs/
│   └── LOGGING.md            # CREATE (Day 8)
├── benchmarks/
│   └── v0.2.0_benchmarks.py  # CREATE (Day 11)
└── CHANGELOG.md              # CREATE (Day 14)
```

### Test Coverage Targets

| Module | Current | Target | New Tests |
|--------|---------|--------|-----------|
| `server.py` | 85% | 90% | Logging, locks |
| `index/persistence.py` | 0% | 95% | All new |
| `tools/chunks.py` | 90% | 95% | Error cases |
| `tools/docs.py` | 85% | 90% | Batch loading |
| **Overall** | **88%** | **92%** | **+15 tests** |

### Configuration Reference

New fields in `~/.rlm-mcp/config.yaml`:

```yaml
# Existing
data_dir: ~/.rlm-mcp
default_max_tool_calls: 500
tokenizer: unicode

# NEW in v0.2.0
log_level: INFO                    # DEBUG, INFO, WARNING, ERROR
structured_logging: true           # false for human-readable
log_file: null                     # or "/var/log/rlm-mcp.log"
max_concurrent_loads: 20           # Batch loading concurrency
max_file_size_mb: 100             # Reject files larger than this
index_persistence_enabled: true    # Can disable for testing
```

## 🤝 Getting Help

### Internal Resources

1. **Design docs**: Read `SOLUTION_DESIGN.md` for detailed designs
2. **Patches**: Check `SOLUTION_DESIGN_PATCHES.md` for edge cases
3. **Checklist**: Follow `IMPLEMENTATION_CHECKLIST_v0.2.0.md` step-by-step
4. **Critical path**: Consult `CRITICAL_PATH_v0.2.0.md` for dependencies

### If Stuck

1. **Check tests**: Run related tests to understand expected behavior
2. **Read code**: Look at similar existing code (e.g., how docs.py loads files)
3. **Ask specific questions**: "How does X work in Y module?"
4. **Timebox**: Spend max 30 min stuck, then ask for help or skip

### Before Asking for Help

Provide:
1. What you're trying to do (which day/task)
2. What you tried
3. What happened (error messages, test failures)
4. What you expected
5. Relevant code snippet (10-20 lines)

## ✅ Pre-Flight Checklist

Before starting Day 1, verify:

- [ ] Read this guide completely
- [ ] Read `EVALUATION_REPORT.md` (understand current state)
- [ ] Skimmed `SOLUTION_DESIGN.md` (know what you're building)
- [ ] Read Patches #3, #8, #1 (critical edge cases)
- [ ] Created `v0.2.0-dev` branch
- [ ] Created GitHub issues (or ready to create)
- [ ] Environment set up (`uv sync --extra dev`)
- [ ] Tests passing (43/43)
- [ ] Have 4-6 hours available today

## 🎓 Learning Objectives

By the end of v0.2.0, you'll have learned:

- **Structured logging**: JSON logs with correlation IDs
- **Concurrency**: `asyncio.Lock`, race condition prevention
- **Persistence**: Atomic file writes, staleness detection
- **Testing**: Integration tests, concurrency tests, benchmarks
- **Release process**: Versioning, tagging, PyPI publishing

## 🎉 Success Criteria

You'll know v0.2.0 is complete when:

- [ ] All 58 tests passing (100%)
- [ ] No lint or type errors
- [ ] Documentation complete and accurate
- [ ] Migration guide tested
- [ ] Benchmarks meet targets (<100ms index load)
- [ ] Alpha users approve
- [ ] Published to PyPI
- [ ] You can install and use v0.2.0 successfully

---

## Next Steps

**Right now**:
1. Read this guide ✅ (you just did!)
2. Read Patches document (20 min)
3. Create GitHub issues (10 min)
4. Start Day 1: Fix bugs (4-6 hours)

**Tomorrow**:
1. Review yesterday's commits
2. Start Day 2: Structured logging
3. Update GitHub project board

**This week**:
- Complete Week 1 tasks (foundation)
- Daily commits and progress tracking
- Ask questions early and often

---

**Remember**: You have a comprehensive plan. Trust the process, follow the checklist, and you'll ship v0.2.0 successfully!

Good luck! 🚀
