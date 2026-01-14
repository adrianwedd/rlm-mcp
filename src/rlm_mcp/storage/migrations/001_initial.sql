-- RLM-MCP Initial Schema
-- Version: 001
-- 
-- Identifier semantics:
-- - id (doc_id, span_id): Session-scoped stable identifiers (UUID)
-- - content_hash: Global content-addressed blob store key (SHA256)

-- Sessions
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT,
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'exported')),
    config JSON NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    tool_calls_used INTEGER DEFAULT 0
);

-- Documents
-- doc_id = id (session-scoped stable reference)
-- content_hash = blob store key (global, enables dedup)
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    source JSON NOT NULL,
    length_chars INTEGER NOT NULL,
    length_tokens_est INTEGER NOT NULL,
    metadata JSON,
    created_at TEXT NOT NULL
);

-- Spans (chunks)
-- span_id = id (session-scoped stable reference for artifact provenance)
-- content_hash = hash of span content (integrity/dedup)
CREATE TABLE IF NOT EXISTS spans (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    strategy JSON NOT NULL,
    created_at TEXT NOT NULL
);

-- Artifacts
-- span_id nullable for session-level artifacts
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    span_id TEXT REFERENCES spans(id) ON DELETE SET NULL,
    type TEXT NOT NULL,
    content JSON NOT NULL,
    provenance JSON,
    created_at TEXT NOT NULL
);

-- Traces
CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    operation TEXT NOT NULL,
    input JSON NOT NULL,
    output JSON NOT NULL,
    duration_ms INTEGER NOT NULL,
    client_reported JSON
);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_documents_session ON documents(session_id);
CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash);
CREATE INDEX IF NOT EXISTS idx_spans_document ON spans(document_id);
CREATE INDEX IF NOT EXISTS idx_spans_content_hash ON spans(content_hash);
CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_span ON artifacts(span_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(type);
CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id);
CREATE INDEX IF NOT EXISTS idx_traces_operation ON traces(operation);

-- Record schema version
INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (1, datetime('now'));
