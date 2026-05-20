# AGENTS.md

## Required Context

Always read `../landingpage/AGENTS.md` before doing any work in this repository.

If the task involves commit messages, also read `../landingpage/.agent/skills/commit/SKILL.md` before answering.

Before answering those tasks, explicitly state:

Checked: AGENTS.md, .agent/skills/commit/SKILL.md

Do not skip these steps.

## Purpose

This file is the local working guide for the `agent-monitoring` repository.

Use it to understand:

- what this repository owns
- how the project is currently structured
- which runtime commands are expected
- where MCP, service, repository, agent, schema, and database boundaries live
- how monitoring workflows should preserve the deterministic-code / LLM boundary

This file is intentionally practical. It is not the full architecture source of
truth. For broader direction, also read:

- `README.md`
- `infra/scripts/README.md`
- `../mcp-log-server/AGENTS.md` when a task touches MCP workflow bootstrap,
  MCP tool contracts, JWT auth, log collection artifacts, or workflow skills

## Short Project Summary

This repository is the standalone monitoring worker for log and sitemap
analysis. It does not own log collection itself. It prepares application state,
loads workflow bootstrap data from the MCP server, persists monitoring run
records, and will later run the agent loop that calls deterministic MCP tools
and LLM providers.

Important boundary:

- deterministic code gathers facts
- the LLM interprets facts
- database models store monitoring run state and MCP artifacts

Do not replace deterministic MCP collection/filtering/grouping behavior with
free-form LLM behavior.

## Current Repository Structure

Top-level and important paths:

- `src/main.py`
  Typer commands. Keep these thin: parse options, open lifecycle when needed,
  call services, and format CLI output.
- `src/scripts.py`
  Console-script entrypoints. Keep command registration here, not business
  behavior.
- `src/services.py`
  Business orchestration for monitoring workflows.
- `src/agents.py`
  Agent boundary. This is where MCP workflow bootstrap and future LLM/tool loops
  belong.
- `src/mcp.py`
  HTTP JSON-RPC MCP client behavior only. Pydantic contracts belong in
  `src/schemas.py`.
- `src/schemas.py`
  Pydantic contracts for MCP payloads, JSON-RPC envelopes, and service results.
- `src/protocols.py`
  Protocols for boundary interfaces used by services and agents.
- `src/repositories.py`
  Database access boundaries. Keep Tortoise ORM calls out of command handlers.
- `src/db/models.py`
  Tortoise models, managers, and querysets.
- `src/db/config.py`
  Database URL and Tortoise config.
- `src/db/lifecycle.py`
  Database initialization/shutdown and lifespan context.
- `src/db/cli.py`
  Migration and test command wrappers.
- `src/settings.py` and `src/conf.py`
  Django-style environment-backed settings.
- `src/tests/`
  Pytest suite. Prefer focused tests around services, repositories, schemas, and
  CLI behavior.

## Workflow Model

Mirror the MCP and landingpage workflow pattern:

- Typer commands are entrypoints only.
- Services prepare application state and coordinate repositories/agents.
- Repositories own database reads and writes.
- Agents own workflow execution decisions.
- MCP owns deterministic collection, snapshot, filtering, grouping, and
  workflow-bootstrap tools.
- Pydantic schemas define contracts at boundaries.

For log analysis, the MCP workflow starts with `analyze_daily_log_bundle`. That
tool returns the workflow prompt, mandatory/optional skill inventory, and
visible deterministic tool inventory. Do not inline all skill content into this
app by default; skills should remain MCP resources or shared external skills
unless there is a clear reason to copy them.

## Runtime Commands

Use Docker Compose as the normal local runtime for DB-backed monitoring jobs:

```bash
docker compose run --rm monitoring-app log_analysis
docker compose run --rm monitoring-app sitemap-analysis
docker compose run --rm monitoring-app check-mcp
```

Host-side `uv run` commands are developer shortcuts. They require the same
runtime variables from `.env`, Doppler, or the shell, especially database
settings and `MCP_WORKFLOW_JWT`.

Useful host-side commands:

```bash
uv run makemigrations add_monitoring_models
uv run migrate
uv run pytest
uv run ruff check src
uv run mypy --explicit-package-bases src
```

## External Skills

This repo may also use the shared local skill library at:

- [antigravity-awesome-skills](../antigravity-awesome-skills)

Use it as the first external skill source when a task needs:

- architecture review
- Python testing patterns
- code review guidance
- README/documentation authoring
- other reusable engineering workflows not already local to this repository

Do not copy skills into this repository by default. Prefer linking to and using
the shared skill set in place unless the user explicitly asks for a local copy.

## Typing

Type code wherever the type is easy and makes the contract clearer. This includes
local variables, not only function signatures.

Prefer simple annotations for function arguments, return values, local variables,
fixtures, test helpers, services, repositories, and external-boundary methods.
When assigning an important value from a service, repository, MCP/client call,
parser, factory, or schema conversion, annotate the variable if the type is
obvious.

Avoid complicated typing constructs or type-only object layers unless they
clarify a real boundary. Do not use weak names such as `object` or `Any` when a
simple concrete project type is available.

## Implementation Guidance

- Prefer documented behavior over assumptions.
- If docs and code disagree, code is the current truth; update docs after
  confirming behavior.
- If a proposed implementation seems risky, inconsistent, or likely incorrect,
  say so clearly and discuss it. Push back when needed.
- Keep command handlers thin.
- Keep Tortoise calls inside repositories, managers, or querysets.
- Keep MCP HTTP/JSON-RPC transport details inside `src/mcp.py`.
- Keep Pydantic payload contracts in `src/schemas.py`.
- Keep boundary protocols in `src/protocols.py`.
- Keep business workflow orchestration in services.
- Keep real agent behavior in `src/agents.py`.
- For monitoring work, remember the hard boundary: deterministic code gathers
  facts; the LLM summarizes and interprets.

## Testing

Use focused validation for the files you touch, and broaden when behavior crosses
boundaries.

Common validation commands:

```bash
uv run pytest src/tests -q
uv run ruff check src
uv run black --check src
uv run mypy --explicit-package-bases src
```

Run pre-commit before finalizing broad changes:

```bash
uv run pre-commit run --all-files
```
