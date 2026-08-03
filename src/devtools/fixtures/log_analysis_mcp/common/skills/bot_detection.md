## BOT / ATTACK DETECTION

Use this skill to detect suspicious traffic patterns in the logs and describe
what happened. Focus on scanner behavior, repeated probing, clustering, and
timestamp extraction rather than OWASP categorization.

Logs include per-line timestamps (ISO 8601 format, added by `docker compose logs --timestamps`).
When you detect scanning or probing, always extract and report the timestamp of the LAST
suspicious request from the log line.

**Attack indicators**:
- Probing for sensitive files: `/.env`, `/.git/config`, `/wp-admin/`, `/phpMyAdmin/`,
  `/config.php`, `/.htaccess`, `/backup`, `/shell`, `/api/v1/`, `/v1/image/`
- Rapid repeated 404s on non-existent paths (>5 in a short time window)
- Repeated 403 Forbidden on `/admin/` with no Referer (CSRF probe)
- `Method Not Allowed` on `/` — likely non-browser client probing
- `Not Acceptable` responses on `/` — content-type probing bots

**Noise-vs-incident reasoning checklist**:
- Do not call something noise from one warning line. Build the conclusion from
  multiple deterministic facts.
- Strong scanner-noise evidence: probe-shaped paths, blocked or missing-resource
  responses, healthy services, and no follow-through error showing impact.
- Weak or inconclusive evidence: warning text without nearby request context,
  zero-line sources, missing proxy logs, unknown service health, or no check of
  the affected subsystem.
- Use uncertainty in the report. Prefer "very likely scanner noise" or "appears
  consistent with scanner noise" instead of "proven harmless".

When describing probe families, do not imply the monitored app runs a probed
technology unless project context says so.

**When you detect an attack pattern, your finding MUST include**:
  1. What was probed
  2. How many requests
  3. The timestamp of the LAST probe from the log line (format: HH:MM:SS UTC)
