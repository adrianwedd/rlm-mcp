"""Secret scanning and redaction for export safety.

Scans content for potential secrets before export.
Supports redaction mode to scrub secrets from artifacts/traces.
"""

from __future__ import annotations

import re
from typing import Tuple


# Secret detection patterns
SECRET_PATTERNS: list[tuple[str, str]] = [
    (r'(?i)(api[_-]?key|apikey)\s*[:=]\s*["\']?[\w-]{20,}', 'API Key'),
    (r'(?i)(secret|token|password|passwd|pwd)\s*[:=]\s*["\']?[\w-]{8,}', 'Secret/Password'),
    (r'sk-[a-zA-Z0-9]{20,}', 'OpenAI API Key'),
    (r'sk-ant-[a-zA-Z0-9-]{20,}', 'Anthropic API Key'),
    (r'ghp_[a-zA-Z0-9]{36}', 'GitHub PAT'),
    (r'gho_[a-zA-Z0-9]{36}', 'GitHub OAuth'),
    (r'-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----', 'Private Key'),
    (r'(?i)bearer\s+[a-zA-Z0-9._-]{20,}', 'Bearer Token'),
    (r'AKIA[0-9A-Z]{16}', 'AWS Access Key ID'),
    (r'(?i)aws.{0,20}secret.{0,20}[\'"][0-9a-zA-Z/+]{40}[\'"]', 'AWS Secret Key'),
]

# Compile patterns for performance
_COMPILED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(pattern), name) for pattern, name in SECRET_PATTERNS
]


def scan_for_secrets(content: str) -> list[tuple[str, int, int, str]]:
    """Scan content for potential secrets.
    
    Args:
        content: Text content to scan
        
    Returns:
        List of (matched_text, start_offset, end_offset, pattern_name)
    """
    findings = []
    
    for pattern, name in _COMPILED_PATTERNS:
        for match in pattern.finditer(content):
            findings.append((
                match.group(),
                match.start(),
                match.end(),
                name,
            ))
    
    return findings


def scan_and_redact(content: str) -> tuple[str, int]:
    """Scan content and redact any secrets found.
    
    Args:
        content: Text content to scan and redact
        
    Returns:
        (redacted_content, secrets_found_count)
    """
    findings = scan_for_secrets(content)
    
    if not findings:
        return content, 0
    
    # Sort by position descending to avoid offset issues during replacement
    findings.sort(key=lambda x: -x[1])
    
    redacted = content
    for match_text, start, end, name in findings:
        redaction = f"[REDACTED:{name}]"
        redacted = redacted[:start] + redaction + redacted[end:]
    
    return redacted, len(findings)


def has_secrets(content: str) -> bool:
    """Quick check if content contains any secrets.
    
    Args:
        content: Text content to check
        
    Returns:
        True if any secrets detected
    """
    for pattern, _ in _COMPILED_PATTERNS:
        if pattern.search(content):
            return True
    return False
