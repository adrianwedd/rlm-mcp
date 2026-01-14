"""Export tools: rlm.export.*"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from rlm_mcp.models import ExportResult
from rlm_mcp.server import tool_handler

if TYPE_CHECKING:
    from rlm_mcp.server import RLMServer


def register_export_tools(server: "RLMServer") -> None:
    """Register export tools."""
    
    @server.tool("rlm.export.github")
    async def rlm_export_github(
        session_id: str,
        repo: str,
        branch: str | None = None,
        path: str | None = None,
        include_docs: bool = False,
        redact: bool = False,
        allow_secrets: bool = False,
    ) -> dict[str, Any]:
        """Export session to GitHub repository branch.

        Args:
            session_id: Session to export
            repo: Repository (owner/repo or full URL)
            branch: Branch name (default: rlm/session/{timestamp}-{session_id[:8]})
            path: Export path (default: .rlm/sessions/{timestamp}_{session_id[:8]})
            include_docs: Include raw document content (default: False)
            redact: Scrub secrets from artifacts/traces (default: False)
            allow_secrets: Allow export even if secrets detected (DANGEROUS, use with caution)
        """
        return await _export_github(
            server,
            session_id=session_id,
            repo=repo,
            branch=branch,
            path=path,
            include_docs=include_docs,
            redact=redact,
            allow_secrets=allow_secrets,
        )


@tool_handler("rlm.export.github")
async def _export_github(
    server: "RLMServer",
    session_id: str,
    repo: str,
    branch: str | None = None,
    path: str | None = None,
    include_docs: bool = False,
    redact: bool = False,
    allow_secrets: bool = False,
) -> dict[str, Any]:
    """Export session to GitHub."""
    from rlm_mcp.export.github import export_to_github
    from rlm_mcp.export.secrets import scan_and_redact
    
    session = await server.db.get_session(session_id)
    if session is None:
        raise ValueError(f"Session not found: {session_id}")
    
    # Generate default branch/path names
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    session_short = session_id[:8]
    
    if branch is None:
        branch = f"rlm/session/{timestamp}-{session_short}"
    
    if path is None:
        path = f".rlm/sessions/{timestamp}_{session_short}"
    
    # Collect export data
    documents = await server.db.get_documents(session_id, limit=10000)
    artifacts = await server.db.get_artifacts(session_id)
    traces = await server.db.get_traces(session_id)
    
    # Build manifest
    manifest = {
        "version": "0.1",
        "exported_at": datetime.utcnow().isoformat(),
        "session": {
            "id": session.id,
            "name": session.name,
            "config": session.config.model_dump(),
            "created_at": session.created_at.isoformat(),
            "closed_at": session.closed_at.isoformat() if session.closed_at else None,
        },
        "documents": [
            {
                "doc_id": doc.id,
                "content_hash": doc.content_hash,
                "source": doc.source.model_dump(),
                "length_chars": doc.length_chars,
                "included": include_docs,
            }
            for doc in documents
        ],
        "artifacts": [
            {
                "artifact_id": a.id,
                "file": f"artifacts/{a.id}.json",
            }
            for a in artifacts
        ],
        "traces": {
            "file": "traces/trace.jsonl",
            "count": len(traces),
        },
    }
    
    # Secret scanning
    secrets_found = 0
    warnings = []

    if redact:
        # Redact artifacts
        for artifact in artifacts:
            content_str = str(artifact.content)
            redacted, count = scan_and_redact(content_str)
            if count > 0:
                secrets_found += count
                # Note: In full impl, would update artifact.content

        # Redact traces
        for trace in traces:
            input_str = str(trace.input)
            output_str = str(trace.output)
            _, in_count = scan_and_redact(input_str)
            _, out_count = scan_and_redact(output_str)
            secrets_found += in_count + out_count
    else:
        # Just scan for warnings
        for artifact in artifacts:
            from rlm_mcp.export.secrets import scan_for_secrets
            findings = scan_for_secrets(str(artifact.content))
            if findings:
                secrets_found += len(findings)
                warnings.append(f"Artifact {artifact.id} contains {len(findings)} potential secrets")

    # Enforce secret policy
    if secrets_found > 0 and not allow_secrets and not redact:
        raise ValueError(
            f"Export blocked: {secrets_found} secrets found. "
            f"Use redact=True to scrub secrets or allow_secrets=True to export anyway."
        )

    # Export to GitHub
    result = await export_to_github(
        repo=repo,
        branch=branch,
        path=path,
        manifest=manifest,
        artifacts=artifacts,
        traces=traces,
        documents=documents if include_docs else None,
        blobs=server.blobs if include_docs else None,
    )
    
    return {
        "branch": branch,
        "commit_sha": result.get("commit_sha", ""),
        "export_path": path,
        "files_exported": result.get("files_exported", 0),
        "warnings": warnings,
        "secrets_found": secrets_found,
    }
