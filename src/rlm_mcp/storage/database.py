"""SQLite database operations for RLM-MCP."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from rlm_mcp.models import (
    Artifact,
    Document,
    Session,
    SessionConfig,
    SessionStatus,
    Span,
    TraceEntry,
)


MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Database:
    """Async SQLite database wrapper."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None
    
    async def connect(self) -> None:
        """Open database connection and run migrations."""
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._run_migrations()
    
    async def close(self) -> None:
        """Close database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None
    
    async def _run_migrations(self) -> None:
        """Run pending migrations."""
        assert self._connection is not None
        
        # Get current version
        try:
            async with self._connection.execute(
                "SELECT MAX(version) FROM schema_version"
            ) as cursor:
                row = await cursor.fetchone()
                current_version = row[0] if row and row[0] else 0
        except sqlite3.OperationalError:
            current_version = 0
        
        # Find and run pending migrations
        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for migration_file in migration_files:
            version = int(migration_file.stem.split("_")[0])
            if version > current_version:
                sql = migration_file.read_text()
                await self._connection.executescript(sql)
                await self._connection.commit()
    
    @property
    def conn(self) -> aiosqlite.Connection:
        """Get active connection or raise."""
        if self._connection is None:
            raise RuntimeError("Database not connected")
        return self._connection
    
    # --- Session Operations ---
    
    async def create_session(self, session: Session) -> None:
        """Insert a new session."""
        await self.conn.execute(
            """
            INSERT INTO sessions (id, name, status, config, created_at, tool_calls_used)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                session.name,
                session.status.value,
                session.config.model_dump_json(),
                session.created_at.isoformat(),
                session.tool_calls_used,
            ),
        )
        await self.conn.commit()
    
    async def get_session(self, session_id: str) -> Session | None:
        """Get session by ID."""
        async with self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_session(row)
    
    async def update_session(self, session: Session) -> None:
        """Update session."""
        await self.conn.execute(
            """
            UPDATE sessions 
            SET name = ?, status = ?, config = ?, closed_at = ?, tool_calls_used = ?
            WHERE id = ?
            """,
            (
                session.name,
                session.status.value,
                session.config.model_dump_json(),
                session.closed_at.isoformat() if session.closed_at else None,
                session.tool_calls_used,
                session.id,
            ),
        )
        await self.conn.commit()
    
    async def increment_tool_calls(self, session_id: str) -> int:
        """Increment tool call counter atomically, return new value.

        Uses UPDATE with RETURNING clause for atomicity. This prevents race
        conditions when multiple tool calls happen concurrently on the same session.

        Args:
            session_id: Session identifier

        Returns:
            New tool_calls_used count after increment
        """
        async with self.conn.execute(
            "UPDATE sessions SET tool_calls_used = tool_calls_used + 1 "
            "WHERE id = ? RETURNING tool_calls_used",
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError(f"Session not found: {session_id}")
            await self.conn.commit()
            return row[0]
    
    def _row_to_session(self, row: aiosqlite.Row) -> Session:
        """Convert database row to Session model."""
        return Session(
            id=row["id"],
            name=row["name"],
            status=SessionStatus(row["status"]),
            config=SessionConfig.model_validate_json(row["config"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
            tool_calls_used=row["tool_calls_used"],
        )
    
    # --- Document Operations ---
    
    async def create_document(self, document: Document) -> None:
        """Insert a new document."""
        await self.conn.execute(
            """
            INSERT INTO documents (id, session_id, content_hash, source, length_chars, 
                                   length_tokens_est, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.id,
                document.session_id,
                document.content_hash,
                document.source.model_dump_json(),
                document.length_chars,
                document.length_tokens_est,
                json.dumps(document.metadata),
                document.created_at.isoformat(),
            ),
        )
        await self.conn.commit()
    
    async def get_documents(self, session_id: str, limit: int = 100, offset: int = 0) -> list[Document]:
        """Get documents for a session."""
        async with self.conn.execute(
            "SELECT * FROM documents WHERE session_id = ? LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_document(row) for row in rows]
    
    async def get_document(self, doc_id: str) -> Document | None:
        """Get document by ID."""
        async with self.conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_document(row)
    
    async def count_documents(self, session_id: str) -> int:
        """Count documents in session."""
        async with self.conn.execute(
            "SELECT COUNT(*) FROM documents WHERE session_id = ?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    async def get_session_stats(self, session_id: str) -> dict[str, int]:
        """Get aggregate stats for session."""
        async with self.conn.execute(
            """
            SELECT 
                COALESCE(SUM(length_chars), 0) as total_chars,
                COALESCE(SUM(length_tokens_est), 0) as total_tokens_est
            FROM documents WHERE session_id = ?
            """,
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return {
                "total_chars": row[0] if row else 0,
                "total_tokens_est": row[1] if row else 0,
            }
    
    def _row_to_document(self, row: aiosqlite.Row) -> Document:
        """Convert database row to Document model."""
        from rlm_mcp.models import DocumentSource
        
        return Document(
            id=row["id"],
            session_id=row["session_id"],
            content_hash=row["content_hash"],
            source=DocumentSource.model_validate_json(row["source"]),
            length_chars=row["length_chars"],
            length_tokens_est=row["length_tokens_est"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=datetime.fromisoformat(row["created_at"]),
        )
    
    # --- Span Operations ---
    
    async def create_span(self, span: Span) -> None:
        """Insert a new span."""
        await self.conn.execute(
            """
            INSERT INTO spans (id, document_id, start_offset, end_offset, 
                              content_hash, strategy, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                span.id,
                span.document_id,
                span.start_offset,
                span.end_offset,
                span.content_hash,
                span.strategy.model_dump_json(),
                span.created_at.isoformat(),
            ),
        )
        await self.conn.commit()
    
    async def get_span(self, span_id: str) -> Span | None:
        """Get span by ID."""
        async with self.conn.execute(
            "SELECT * FROM spans WHERE id = ?", (span_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_span(row)
    
    async def get_spans_by_document(self, document_id: str) -> list[Span]:
        """Get all spans for a document."""
        async with self.conn.execute(
            "SELECT * FROM spans WHERE document_id = ? ORDER BY start_offset",
            (document_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_span(row) for row in rows]
    
    async def count_spans(self, session_id: str) -> int:
        """Count spans in session."""
        async with self.conn.execute(
            """
            SELECT COUNT(*) FROM spans s
            JOIN documents d ON s.document_id = d.id
            WHERE d.session_id = ?
            """,
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    async def count_spans_for_document(self, doc_id: str) -> int:
        """Count spans for a document."""
        async with self.conn.execute(
            "SELECT COUNT(*) FROM spans WHERE document_id = ?", (doc_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    def _row_to_span(self, row: aiosqlite.Row) -> Span:
        """Convert database row to Span model."""
        from rlm_mcp.models import ChunkStrategy
        
        return Span(
            id=row["id"],
            document_id=row["document_id"],
            start_offset=row["start_offset"],
            end_offset=row["end_offset"],
            content_hash=row["content_hash"],
            strategy=ChunkStrategy.model_validate_json(row["strategy"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
    
    # --- Artifact Operations ---
    
    async def create_artifact(self, artifact: Artifact) -> None:
        """Insert a new artifact."""
        await self.conn.execute(
            """
            INSERT INTO artifacts (id, session_id, span_id, type, content, provenance, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.id,
                artifact.session_id,
                artifact.span_id,
                artifact.type,
                json.dumps(artifact.content),
                artifact.provenance.model_dump_json() if artifact.provenance else None,
                artifact.created_at.isoformat(),
            ),
        )
        await self.conn.commit()
    
    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        """Get artifact by ID."""
        async with self.conn.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_artifact(row)
    
    async def get_artifacts(
        self, 
        session_id: str, 
        span_id: str | None = None, 
        artifact_type: str | None = None
    ) -> list[Artifact]:
        """Get artifacts with optional filters."""
        query = "SELECT * FROM artifacts WHERE session_id = ?"
        params: list[Any] = [session_id]
        
        if span_id is not None:
            query += " AND span_id = ?"
            params.append(span_id)
        
        if artifact_type is not None:
            query += " AND type = ?"
            params.append(artifact_type)
        
        query += " ORDER BY created_at"
        
        async with self.conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_artifact(row) for row in rows]
    
    async def count_artifacts(self, session_id: str) -> int:
        """Count artifacts in session."""
        async with self.conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE session_id = ?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    def _row_to_artifact(self, row: aiosqlite.Row) -> Artifact:
        """Convert database row to Artifact model."""
        from rlm_mcp.models import ArtifactProvenance
        
        return Artifact(
            id=row["id"],
            session_id=row["session_id"],
            span_id=row["span_id"],
            type=row["type"],
            content=json.loads(row["content"]),
            provenance=ArtifactProvenance.model_validate_json(row["provenance"]) 
                if row["provenance"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )
    
    # --- Trace Operations ---
    
    async def create_trace(self, trace: TraceEntry) -> None:
        """Insert a trace entry."""
        await self.conn.execute(
            """
            INSERT INTO traces (id, session_id, timestamp, operation, input, output, 
                               duration_ms, client_reported)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace.id,
                trace.session_id,
                trace.timestamp.isoformat(),
                trace.operation,
                json.dumps(trace.input),
                json.dumps(trace.output),
                trace.duration_ms,
                json.dumps(trace.client_reported) if trace.client_reported else None,
            ),
        )
        await self.conn.commit()
    
    async def get_traces(self, session_id: str) -> list[TraceEntry]:
        """Get all traces for a session."""
        async with self.conn.execute(
            "SELECT * FROM traces WHERE session_id = ? ORDER BY timestamp",
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_trace(row) for row in rows]
    
    def _row_to_trace(self, row: aiosqlite.Row) -> TraceEntry:
        """Convert database row to TraceEntry model."""
        return TraceEntry(
            id=row["id"],
            session_id=row["session_id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            operation=row["operation"],
            input=json.loads(row["input"]),
            output=json.loads(row["output"]),
            duration_ms=row["duration_ms"],
            client_reported=json.loads(row["client_reported"]) if row["client_reported"] else None,
        )
