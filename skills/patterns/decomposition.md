# Query Decomposition Patterns

## Pattern 1: Map-Reduce

For aggregation queries ("count all X", "list all Y"):

1. `rlm.chunk.create` documents
2. Map: Query each chunk (use Haiku, batch chunks)
3. `rlm.artifact.store` map results with span provenance
4. Reduce: `rlm.artifact.list` + aggregate into final answer (use Sonnet/Opus)

### Example: Count all function definitions

```
1. rlm.docs.load(sources=[{type: "glob", path: "src/**/*.py"}])
2. rlm.chunk.create(doc_id=..., strategy={type: "delimiter", delimiter: "\ndef "})
3. For each chunk batch:
   - Client subcall (Haiku): "Count function definitions in this code"
   - rlm.artifact.store(type="extraction", span_id=..., content={count: N})
4. rlm.artifact.list(type="extraction")
5. Sum all counts → final answer
```

## Pattern 2: Filtered Search

For targeted queries ("find where X does Y"):

1. `rlm.search.query` for candidate locations
2. `rlm.docs.peek` at high-scoring matches
3. Client subcall on promising spans only
4. `rlm.artifact.store` findings with span provenance

### Example: Find authentication handling

```
1. rlm.search.query(query="authentication login password", method="bm25", limit=10)
2. For each match:
   - rlm.docs.peek(doc_id=..., start=match.span.start, end=match.span.end)
   - If relevant, client subcall: "Analyze this authentication code"
   - rlm.artifact.store(type="analysis", span=match.span, content=...)
3. Synthesize findings
```

## Pattern 3: Iterative Refinement

For exploratory queries ("explain how this system works"):

1. `rlm.docs.list` to understand corpus
2. `rlm.search.query` for key terms
3. Deep-dive subcalls on relevant spans
4. `rlm.artifact.store` synthesis as session-level artifact

### Example: System architecture overview

```
1. rlm.docs.list() → understand file structure
2. rlm.search.query(query="main entry init config") → find entry points
3. rlm.docs.peek on top results → understand structure
4. Client subcall (Sonnet): "What are the main components?"
5. For each component:
   - rlm.search.query for component name
   - Deep analysis subcall
   - rlm.artifact.store findings
6. Final synthesis → rlm.artifact.store(span_id=null, type="overview")
```

## Pattern 4: Pairwise Reasoning

For relationship queries ("find all pairs where..."):

1. First pass: Classify each item → `rlm.artifact.store` per span
2. `rlm.artifact.list` to load classifications
3. Compute pairs programmatically from artifacts
4. Verify sample pairs with targeted subcalls
5. `rlm.artifact.store` final pairs as session artifact

### Example: Find imports between modules

```
1. rlm.chunk.create per file (lines strategy)
2. Map (Haiku): Extract import statements from each file
   - rlm.artifact.store(type="imports", content={imports: [...]})
3. rlm.artifact.list(type="imports") → collect all
4. Programmatically compute import graph
5. Verify suspicious edges with targeted peek + subcall
6. Store final graph as session artifact
```

## Common Pitfalls

### Too Many Subcalls
❌ `for line in document: subcall(line)`
✅ `for chunk in chunks(document, size=100): subcall(chunk)`

### Lost Provenance
❌ `result = subcall(content); return result`
✅ `result = subcall(content); artifact.store(span=span, content=result)`

### Ignoring Cache
❌ `results = [subcall(span) for span in spans]`
✅ `cached = artifact.list(span_id=span.id); if not cached: subcall(span)`

### Premature Synthesis
❌ Final answer after 1 search
✅ Multiple passes: search → peek → analyze → verify → synthesize
