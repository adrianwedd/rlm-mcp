# RLM (Recursive Language Model) Skill

## When to Use RLM

Activate RLM processing when ANY of the following apply:

1. **Context exceeds threshold**: Total input > 100K tokens (~400K chars)
2. **Multi-document reasoning**: Task requires synthesizing 5+ documents
3. **Aggregation tasks**: Questions about "all", "every", "count of", "list all pairs"
4. **Information-dense queries**: Answer depends on most/all of the input (not needle-in-haystack)
5. **Explicit request**: User asks to "analyze this codebase", "process these logs"

## Subcall Model Recommendations (Advisory)

The MCP server does not enforce model selection. These are recommendations:

| Root Model | Subcall Model | Bulk/Map Model | Use Case |
|------------|---------------|----------------|----------|
| Opus 4.5 | Sonnet 4.5 | Haiku 4.5 | Complex synthesis, deep analysis |
| Sonnet 4.5 | Haiku 4.5 | Haiku 4.5 | Standard workflows |
| Haiku 4.5 | Haiku 4.5 | Haiku 4.5 | Cost-sensitive, simple aggregation |

**Critical**: Haiku is for bulk passes only. Never invoke Haiku per-line — batch chunks.

## Workflow Pattern

1. **Initialize**: `rlm.session.create` with appropriate config
2. **Load**: `rlm.docs.load` documents
3. **Probe**: `rlm.docs.peek` at structure (first lines, format detection)
4. **Search**: `rlm.search.query` to find relevant sections (lazy-builds BM25)
5. **Chunk**: `rlm.chunk.create` with appropriate strategy
6. **Process**: `rlm.span.get` + client subcalls on spans
7. **Store**: `rlm.artifact.store` results with span provenance
8. **Synthesize**: Aggregate artifacts into final answer
9. **Close**: `rlm.session.close`

## Chunking Strategy Selection

| Content Type | Strategy | Params | Rationale |
|--------------|----------|--------|-----------|
| Source code | `delimiter` | `"\ndef \|\nclass "` | Preserve semantic units |
| Logs | `lines` | `line_count: 100, overlap: 10` | Temporal locality |
| Markdown | `delimiter` | `"\n## "` | Section boundaries |
| JSON/JSONL | `lines` | `line_count: 1` | Record-level processing |
| Plain text | `fixed` | `chunk_size: 50000, overlap: 500` | Balanced chunks |

## Cost Guardrails

- **Max tool calls per session**: 500 (default), warn at 400
- **Max chars per response**: 50K (server-enforced)
- **Max chars per peek**: 10K (server-enforced)
- **Batch aggressively**: Prefer 10 docs per subcall over 1 doc per subcall
- **Cache reuse**: Check artifacts before re-querying same span
- **Use span provenance**: Every artifact should trace back to its source span

## Anti-patterns to Avoid

❌ One subcall per line (Qwen3-Coder's failure mode — thousands of calls)
❌ Loading entire context into single subcall
❌ Ignoring cached artifacts from prior analysis
❌ Returning raw subcall outputs without synthesis
❌ Forgetting to close session
❌ Ignoring `truncated: true` in responses
