# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A task queue system for batching Claude Code tasks with automatic retry and rate-limit handling. Users queue tasks with priorities and dependencies, then run them unattended while the worker monitors Claude Plan usage limits (5-hour session and 7-day weekly quotas).

## Execution Model

This is a **clone-and-run script**, not an installable package. It is invoked either by the user or by Claude Code directly via Bash — never installed as a system command.

Two usage paths:

**1. Via the `queue-task` skill** — the skill has `allowed-tools: Write, Read` only. It cannot run Bash. Its job is to write `tasks.yaml` and provide a one-liner the user (or Claude Code main agent) can execute:
```
queue-task skill → writes tasks.yaml → Claude Code / user runs ./claude-queue.py
```

**2. Directly** — Claude Code (main agent with Bash access) or the user runs `./claude-queue.py` commands directly from the repo root.

`pip install -e ".[dev]"` is only used to get pytest/ruff onto PATH for development. There is no package to install.

## Commands

```bash
# Install all dependencies (creates .venv, installs from uv.lock)
uv sync --extra dev

# Run all tests
uv run pytest tests/ -v

# Run a single test file
uv run pytest tests/test_queue.py -v

# Run a single test
uv run pytest tests/test_queue.py::TestTaskQueue::test_add_task -v

# Run with coverage
uv run pytest tests/ --cov --cov-report=html

# Type check
uv run mypy claude-queue.py

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Update lockfile after changing dependencies
uv lock

# Install pre-commit hooks (one-time setup)
uv run pre-commit install

# Run pre-commit manually on all files
uv run pre-commit run --all-files
```

## Architecture

Everything lives in a single monolithic file: `claude-queue.py` (1384 lines). This is intentional — the tool is cloned and run directly, so a single file is simpler to use and share.

### Core Classes

**`ClaudeUsageChecker`** — Monitors Claude Plan limits via the internal `https://claude.ai/api/organizations/{org_id}/usage` API using a session cookie. Tracks 5-hour and 7-day usage percentages and reset times. Returns a limit-exceeded boolean + reason at a configurable threshold (default 95%).

**`TaskQueue`** — Persistent task management backed by `~/.claude-queue/tasks.json`. Uses `fcntl` file locking (shared read, exclusive write) and an atomic temp-file-then-rename write pattern. `get_next_task()` sorts by priority descending, then respects `depends_on` — a task won't be returned until all its dependencies are COMPLETED.

**`ClaudeWorker`** — Main execution loop. Calls `check_and_wait_for_limits()` before each task, then spawns `claude -p "<prompt>"` as a subprocess (1-hour timeout, optional `cwd`). Rate limit errors are parsed with regex to extract `retry-after` seconds. Other failures use exponential backoff (`60s × 2^min(attempts, 5)`).

**CLI layer** — argparse subcommands: `add`, `batch`, `worker`, `usage`, `status`, `list`, `remove`, `clear`.

### Task Lifecycle

```
queued → running → completed
                 → failed (retried up to max_attempts, then stays failed)
                 → rate_limited (waits retry-after, then returns to queued)
```

### Batch File Loading

`batch` command accepts YAML or JSON files. Uses multi-pass resolution: dependencies referenced by `session_name` are resolved to task IDs across multiple passes. If a pass makes no progress (unresolvable deps), remaining tasks fail with a circular-dependency error.

### Data Storage

- Queue: `~/.claude-queue/tasks.json`
- Task outputs (optional): `~/.claude-queue/outputs/{task_id}.txt`

## Key Files Beyond Source

- `CLAUDE_TASK_GUIDE.md` — Instructions for Claude AI on how to create tasks when usage limits are approaching
- `skills/queue-task/SKILL.md` — Claude Code skill that surfaces this project as a `/queue-task` skill
- `tests/*.yaml` — YAML fixtures for dependency resolution tests