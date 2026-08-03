## SECURITY ANALYSIS — OWASP EXPERTISE

Use this skill after suspicious traffic or security-relevant findings have
already been identified. Focus on security interpretation, severity escalation,
and attack-stage classification rather than raw pattern detection mechanics.

Relevant log-observable indicators include:

- Broken access control: repeated 403s, path traversal, unauthorized admin access
- Injection: SQL, command, or script patterns in URLs
- Security misconfiguration: probing for debug, repository, or secret paths
- Authentication failures: clustered login 401s or account lockouts
- API abuse: crafted object identifiers, unexpected verbs, or rapid bulk requests

Attack stages:

- Reconnaissance: many probes with blocked or missing-resource responses
- Enumeration: repeated variations of similar paths or accounts
- Possible exploitation: unexpected 2xx/3xx responses on protected paths
- Impact: confirmed data exposure, privilege change, or service failure

Access logs prove the requested path, status, and response byte count, not the
response contents. A non-empty 2xx on a sensitive path is a CRITICAL potential
exposure, but do not claim file contents or exfiltration without separate
evidence.

Do not claim successful exploitation from suspicious input alone. When response
or application impact is unclear, request deterministic evidence and report the
uncertainty. A sensitive-path 2xx or confirmed malicious-input impact is
CRITICAL; unresolved auth abuse is WARNING.
