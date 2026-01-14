"""GitHub export functionality.

Exports sessions to GitHub branches. Never touches main directly.
Branch naming: rlm/session/{timestamp}-{session_id[:8]}
"""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from rlm_mcp.models import Artifact, Document, TraceEntry
    from rlm_mcp.storage.blobs import BlobStore


async def export_to_github(
    repo: str,
    branch: str,
    path: str,
    manifest: dict[str, Any],
    artifacts: list["Artifact"],
    traces: list["TraceEntry"],
    documents: list["Document"] | None = None,
    blobs: "BlobStore" | None = None,
) -> dict[str, Any]:
    """Export session to GitHub repository.
    
    Args:
        repo: Repository (owner/repo)
        branch: Target branch name
        path: Export path within repo
        manifest: Session manifest
        artifacts: Artifacts to export
        traces: Traces to export
        documents: Optional documents (if include_docs=True)
        blobs: Blob store (required if documents provided)
        
    Returns:
        Dict with commit_sha and files_exported
    """
    # TODO: Implement actual GitHub API integration
    # For now, this is a stub that would use PyGithub
    
    from github import Github
    import os
    
    # Get GitHub token from environment
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable not set")
    
    g = Github(token)
    
    # Parse repo
    if "/" in repo:
        owner, repo_name = repo.split("/", 1)
    else:
        raise ValueError(f"Invalid repo format: {repo}. Expected 'owner/repo'")
    
    repository = g.get_repo(f"{owner}/{repo_name}")
    
    # Get or create branch
    default_branch = repository.default_branch
    try:
        ref = repository.get_git_ref(f"heads/{branch}")
    except Exception:
        # Create branch from default
        default_ref = repository.get_git_ref(f"heads/{default_branch}")
        repository.create_git_ref(f"refs/heads/{branch}", default_ref.object.sha)
    
    # Prepare files to commit
    files_to_commit = []
    
    # Manifest
    files_to_commit.append({
        "path": f"{path}/manifest.json",
        "content": json.dumps(manifest, indent=2),
    })
    
    # Artifacts
    for artifact in artifacts:
        files_to_commit.append({
            "path": f"{path}/artifacts/{artifact.id}.json",
            "content": json.dumps({
                "artifact_id": artifact.id,
                "span_id": artifact.span_id,
                "type": artifact.type,
                "content": artifact.content,
                "provenance": artifact.provenance.model_dump() if artifact.provenance else None,
                "created_at": artifact.created_at.isoformat(),
            }, indent=2),
        })
    
    # Traces
    trace_lines = []
    for trace in traces:
        trace_lines.append(json.dumps({
            "ts": trace.timestamp.isoformat(),
            "op": trace.operation,
            "in": trace.input,
            "out": trace.output,
            "ms": trace.duration_ms,
        }))
    
    files_to_commit.append({
        "path": f"{path}/traces/trace.jsonl",
        "content": "\n".join(trace_lines),
    })
    
    # Documents (if included)
    if documents and blobs:
        for doc in documents:
            # Metadata
            files_to_commit.append({
                "path": f"{path}/docs/{doc.id}.meta.json",
                "content": json.dumps({
                    "doc_id": doc.id,
                    "content_hash": doc.content_hash,
                    "source": doc.source.model_dump(),
                    "length_chars": doc.length_chars,
                    "metadata": doc.metadata,
                }, indent=2),
            })
            
            # Content
            content = blobs.get(doc.content_hash)
            if content:
                files_to_commit.append({
                    "path": f"{path}/docs/{doc.id}.txt",
                    "content": content,
                })
    
    # Commit all files
    # Note: In production, use create_git_tree + create_git_commit for atomic commits
    commit_sha = ""
    for file_info in files_to_commit:
        try:
            contents = repository.get_contents(file_info["path"], ref=branch)
            repository.update_file(
                file_info["path"],
                f"Update {file_info['path']}",
                file_info["content"],
                contents.sha,
                branch=branch,
            )
        except Exception:
            result = repository.create_file(
                file_info["path"],
                f"Create {file_info['path']}",
                file_info["content"],
                branch=branch,
            )
            commit_sha = result["commit"].sha
    
    return {
        "commit_sha": commit_sha,
        "files_exported": len(files_to_commit),
    }
