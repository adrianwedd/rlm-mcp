"""Comprehensive GitHub export tests - secret scanning, branch creation, etc."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from rlm_mcp.server import RLMServer
from rlm_mcp.tools.session import _session_create, _session_close
from rlm_mcp.tools.docs import _docs_load
from rlm_mcp.tools.artifacts import _artifact_store
from rlm_mcp.tools.export import _export_github
from rlm_mcp.export.secrets import scan_for_secrets, has_secrets


@pytest.mark.asyncio
async def test_secret_scanner_catches_patterns():
    """Test that secret scanner catches all common secret patterns."""

    test_cases = [
        ("API key: sk_live_abc123def456", True, "API key"),
        ("Bearer token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", True, "JWT"),
        ("aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", True, "AWS"),
        ("password='hunter2'", True, "password"),
        ("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ", True, "SSH key"),
        ("ghp_1234567890abcdef1234567890abcdef1234", True, "GitHub token"),
        ("Just regular text", False, None),
        ("def calculate_sum(a, b): return a + b", False, None),
    ]

    for content, should_detect, description in test_cases:
        secrets = scan_for_secrets(content)
        detected = has_secrets(content)
        if should_detect:
            assert len(secrets) > 0, f"Failed to detect {description} in: {content[:50]}"
            assert detected, f"has_secrets returned False for {description}"
        else:
            assert len(secrets) == 0, f"False positive for: {content[:50]}"
            assert not detected, f"has_secrets returned True for: {content[:50]}"


@pytest.mark.asyncio
async def test_export_with_secrets_fails_by_default(server: RLMServer):
    """Test that export fails when secrets are detected (default behavior)."""

    session = await _session_create(server, name="secret-test")
    session_id = session["session_id"]

    # Load document with API key (using FAKE_TEST prefix to avoid GitHub scanner)
    doc_with_secret = """
    # Configuration
    API_KEY = "FAKE_TEST_sk_live_1234567890abcdef1234567890abcdef"
    DATABASE_URL = "postgresql://user:password@localhost/db"
    """

    await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": doc_with_secret}]
    )

    # Mock GitHub API
    with patch('rlm_mcp.export.github.Github') as mock_github:
        mock_repo = MagicMock()
        mock_github.return_value.get_repo.return_value = mock_repo

        # Export should fail due to secrets
        with pytest.raises(ValueError, match="secrets found"):
            await _export_github(
                server,
                session_id=session_id,
                repo="test/repo",
                token="fake_token",
                allow_secrets=False
            )


@pytest.mark.asyncio
async def test_export_with_secrets_redacted(server: RLMServer):
    """Test that secrets can be redacted during export."""

    session = await _session_create(server, name="redaction-test")
    session_id = session["session_id"]

    # Load document with secrets (using FAKE_TEST prefix to avoid GitHub scanner)
    doc_with_secret = """
    API_KEY = "FAKE_TEST_sk_live_1234567890abcdef1234567890abcdef"
    ANOTHER_KEY = "FAKE_TEST_sk_test_abcdefghijklmnopqrstuvwxyz123456"
    """

    await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": doc_with_secret}]
    )

    # Mock GitHub API
    with patch('rlm_mcp.export.github.Github') as mock_github:
        mock_repo = MagicMock()
        mock_branch = MagicMock()
        mock_commit = MagicMock()
        mock_commit.sha = "abc123"

        mock_github.return_value.get_repo.return_value = mock_repo
        mock_repo.get_branch.side_effect = Exception("Branch not found")  # New branch
        mock_repo.create_git_ref.return_value = None
        mock_repo.create_file.return_value = {"commit": mock_commit}

        # Export with redaction
        result = await _export_github(
            server,
            session_id=session_id,
            repo="test/repo",
            token="fake_token",
            allow_secrets=False,
            redact_secrets=True
        )

        assert result["secrets_found"] == 2
        assert "redacted" in result["warnings"][0].lower()


@pytest.mark.asyncio
async def test_export_branch_naming(server: RLMServer):
    """Test that export creates branches with correct naming pattern."""

    session = await _session_create(server, name="branch-test")
    session_id = session["session_id"]

    # Load simple document
    await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": "test content"}]
    )

    created_refs = []

    def capture_ref(ref, sha):
        created_refs.append(ref)

    with patch('rlm_mcp.export.github.Github') as mock_github:
        mock_repo = MagicMock()
        mock_commit = MagicMock()
        mock_commit.sha = "abc123"

        mock_github.return_value.get_repo.return_value = mock_repo
        mock_repo.get_branch.side_effect = Exception("Branch not found")
        mock_repo.create_git_ref.side_effect = capture_ref
        mock_repo.create_file.return_value = {"commit": mock_commit}

        result = await _export_github(
            server,
            session_id=session_id,
            repo="test/repo",
            token="fake_token"
        )

        # Verify branch name format: rlm-sessions/{timestamp}-{session_id_prefix}
        assert len(created_refs) > 0
        ref = created_refs[0]
        assert ref.startswith("refs/heads/rlm-sessions/")
        assert session_id[:8] in ref


@pytest.mark.asyncio
async def test_export_idempotency(server: RLMServer):
    """Test that exporting same session twice is idempotent."""

    session = await _session_create(server, name="idempotent-test")
    session_id = session["session_id"]

    await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": "test"}]
    )

    with patch('rlm_mcp.export.github.Github') as mock_github:
        mock_repo = MagicMock()
        mock_commit = MagicMock()
        mock_commit.sha = "abc123"

        mock_github.return_value.get_repo.return_value = mock_repo
        mock_repo.get_branch.side_effect = Exception("Not found")
        mock_repo.create_git_ref.return_value = None
        mock_repo.create_file.return_value = {"commit": mock_commit}

        # First export
        result1 = await _export_github(
            server,
            session_id=session_id,
            repo="test/repo",
            token="fake_token"
        )

        # Second export should succeed (creates new branch with different timestamp)
        result2 = await _export_github(
            server,
            session_id=session_id,
            repo="test/repo",
            token="fake_token"
        )

        assert result1["branch"] != result2["branch"]  # Different timestamps


@pytest.mark.asyncio
async def test_export_includes_artifacts(server: RLMServer):
    """Test that export includes stored artifacts."""

    session = await _session_create(server, name="artifact-export-test")
    session_id = session["session_id"]

    # Load document
    load_result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": "def foo(): pass"}]
    )
    doc_id = load_result["loaded"][0]["doc_id"]

    # Store artifact
    await _artifact_store(
        server,
        session_id=session_id,
        type="summary",
        content={"text": "Function foo does nothing"},
        span={"doc_id": doc_id, "start": 0, "end": 17}
    )

    files_created = []

    def capture_file(path, content, commit_msg, branch):
        files_created.append({"path": path, "content": content})
        return {"commit": MagicMock(sha="abc123")}

    with patch('rlm_mcp.export.github.Github') as mock_github:
        mock_repo = MagicMock()

        mock_github.return_value.get_repo.return_value = mock_repo
        mock_repo.get_branch.side_effect = Exception("Not found")
        mock_repo.create_git_ref.return_value = None
        mock_repo.create_file.side_effect = capture_file

        result = await _export_github(
            server,
            session_id=session_id,
            repo="test/repo",
            token="fake_token"
        )

        # Should create manifest, documents, artifacts, trace files
        assert len(files_created) >= 3
        paths = [f["path"] for f in files_created]

        # Check for expected file types
        assert any("manifest" in p for p in paths)
        assert any("documents" in p or "docs" in p for p in paths)
        assert any("artifacts" in p for p in paths)


@pytest.mark.asyncio
async def test_export_marks_session_as_exported(server: RLMServer):
    """Test that successful export updates session status."""

    session = await _session_create(server, name="status-test")
    session_id = session["session_id"]

    await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": "test"}]
    )

    with patch('rlm_mcp.export.github.Github') as mock_github:
        mock_repo = MagicMock()
        mock_commit = MagicMock()
        mock_commit.sha = "abc123"

        mock_github.return_value.get_repo.return_value = mock_repo
        mock_repo.get_branch.side_effect = Exception("Not found")
        mock_repo.create_git_ref.return_value = None
        mock_repo.create_file.return_value = {"commit": mock_commit}

        await _export_github(
            server,
            session_id=session_id,
            repo="test/repo",
            token="fake_token"
        )

        # Check session status
        db_session = await server.db.get_session(session_id)
        assert db_session is not None
        assert db_session.status.value == "exported"
