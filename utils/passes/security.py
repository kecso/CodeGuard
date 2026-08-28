from __future__ import annotations

from utils.passes.base import PassContext

SECURITY_INSTRUCTIONS = """You are a senior application security reviewer.
Analyze the provided source for exploitable and latent vulnerabilities.

Look for (non-exhaustive, expand when the code warrants it):
- Injection (SQL, command, template, LDAP, header)
- Authn/authz gaps, insecure session handling, CSRF
- Secret leakage, hardcoded credentials, weak cryptography
- Path traversal, SSRF, unsafe deserialization
- XSS and dangerous HTML/JS sinks
- Insecure defaults, missing validation, overly broad CORS

Output Markdown with these sections:
1. Findings — each finding MUST include severity (critical/high/medium/low/info), file path, approximate location, evidence, impact, and a concrete fix.
2. Residual risk — what you could not confirm from this chunk.
3. Suggested tests — specific tests that would catch each finding.

If nothing notable is present, say so explicitly rather than inventing issues.
Do not provide exploit payloads or step-by-step attack instructions.
"""


class SecurityPass:
    id = "security"
    title = "Security Vulnerabilities"

    def build_prompt(self, code: str, context: PassContext) -> str:
        return (
            f"{SECURITY_INSTRUCTIONS}\n"
            f"Repository: {context.repo_name}\n"
            f"Chunk: {context.chunk_index} of {context.chunk_count}\n\n"
            f"Source:\n{code}\n"
        )
