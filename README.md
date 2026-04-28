# Claude Queue

Maximize your Claude Plan utilization. Queue multiple tasks with configurable priorities and dependencies, then run
them unattended while tool monitors Claude Plan limits (5-hour session and 7-day weekly quotas), automatically pausing
when approaching capacity and resuming upon reset.

## Disclaimer

This tool accesses Claude.ai internal web endpoints for usage limit monitoring, which may violate Anthropic's Terms of Service.

- Usage is at your own risk and responsibility
- Review [Anthropic's Terms of Service](https://www.anthropic.com/legal/consumer-terms) before using

By using this tool, you acknowledge these risks and accept full responsibility for any consequences.

## Basic Usage

### Prerequisites

Before starting, you need your Claude session key. See [Getting Your Session Key](#getting-your-session-key) for instructions.

### Quick Start - Single Task

```bash
# Add a task with priority and working directory
./claude-queue.py add "Refactor authentication to use OAuth2" \
  --working-dir ~/projects/my-app \
  --priority 10

# Add another task for a different project
./claude-queue.py add "Fix bug in user service" \
  --working-dir ~/projects/another-app \
  --priority 5

# Set your Claude session key (get from claude.ai cookies - see Setup section)
export CLAUDE_SESSION_KEY="KEY"

# Start worker (monitors usage limits and processes tasks)
./claude-queue.py worker
```

**Note:** Each task can specify its own `working_dir` to run in different project directories. If not specified, tasks
run in the current directory.

### Batch Loading - Multiple Tasks

Create a task file with tasks for different projects:

```yaml
# tasks.yaml
tasks:
  - prompt: "Refactor authentication to use OAuth2"
    session: "auth-oauth"
    working_dir: "~/projects/backend-api"
    priority: 10
    max_attempts: 5

  - prompt: "Add unit tests for API endpoints"
    session: "api-tests"
    working_dir: "~/projects/backend-api"
    priority: 8

  - prompt: "Update documentation"
    session: "docs"
    working_dir: "~/projects/documentation"
    priority: 5
```

Load and execute:

```bash
# Load tasks
./claude-queue.py batch tasks.yaml

# Set your Claude session key (required for usage limit monitoring)
export CLAUDE_SESSION_KEY="KEY"

# Start worker (processes tasks by priority across all projects)
./claude-queue.py worker

# Monitor in another terminal
watch -n 5 './claude-queue.py status'
```

## Task Dependencies

Tasks can depend on other tasks - they only run after dependencies complete:

```yaml
tasks:
  # Runs first
  - prompt: "Create database schema"
    session: "db-schema"
    priority: 10

  # Runs after db-schema completes
  - prompt: "Implement user registration"
    session: "user-registration"
    priority: 8
    depends_on: ["db-schema"]

  - prompt: "Implement user login"
    session: "user-login"
    priority: 8
    depends_on: ["db-schema"]

  # Runs after both registration and login complete
  - prompt: "Write integration tests"
    session: "integration-tests"
    priority: 5
    depends_on: ["user-registration", "user-login"]
```

## Core Features

### Usage Limit Monitoring (Primary Rate Limit Defense)
- Proactive monitoring of Claude Plan limits (5-hour session & 7-day weekly quotas)
- Checks limits before starting each task via Claude API
- Worker pauses automatically when utilization reaches 95%
- Auto-resumes when limits reset


### Priority System
- Higher priority = executes first (0-100)
- Tasks with dependencies respect dependency order over priority

### Automatic Retry & Rate Limit Fallback
- Fallback mechanism: If rate limits are hit despite usage monitoring, automatically extracts retry-after timing from error messages
- Tasks wait the exact time specified in retry-after before retrying
- Tasks fail if retry-after information cannot be extracted (prevents infinite retry loops)
- Configurable max attempts per task (default: 3)
- Tasks persist in queue - can restart worker anytime to continue

### Task Status States

| Status         | Meaning                    |
|----------------|----------------------------|
| `queued`       | Waiting to execute         |
| `running`      | Currently executing        |
| `completed`    | Successfully finished      |
| `failed`       | Failed after max attempts  |
| `rate_limited` | Hit rate limit, will retry |

## CLI Reference

### Global flag

| Flag                | Default                      | Description                |
|---------------------|------------------------------|----------------------------|
| `--queue-file PATH` | `~/.claude-queue/tasks.json` | Use a different queue file |

### `add` — Add a task

```bash
./claude-queue.py add "Task description" [flags]
```

| Flag                 | Default           | Description                                                   |
|----------------------|-------------------|---------------------------------------------------------------|
| `--session NAME`     | auto-generated    | Human-readable name for the task                              |
| `--priority N`       | `0`               | Higher number runs first                                      |
| `--max-attempts N`   | `3`               | Max retry attempts before marking failed                      |
| `--working-dir PATH` | current directory | Directory Claude runs in                                      |
| `--timeout N`        | `3600`            | Task execution timeout in seconds; overrides global --timeout |

### `batch` — Load tasks from a file

```bash
./claude-queue.py batch tasks.yaml [--dry-run]
```

| Flag        | Description                                    |
|-------------|------------------------------------------------|
| `--dry-run` | Preview tasks without adding them to the queue |

### `worker` — Process tasks

```bash
export CLAUDE_SESSION_KEY="sk-ant-..."
./claude-queue.py worker [flags]
```

| Flag                | Default               | Description                                                                    |
|---------------------|-----------------------|--------------------------------------------------------------------------------|
| `--session-key KEY` | `$CLAUDE_SESSION_KEY` | Session cookie (alternative to env var)                                        |
| `--api-url URL`     | auto-detected         | Override the usage API endpoint                                                |
| `--threshold N`     | `95.0`                | Pause when utilization reaches this %                                          |
| `--timeout N`       | `3600`                | Default task execution timeout in seconds                                      |
| `--retry-delay N`   | `60`                  | Base delay (seconds) for exponential backoff on failures                       |
| `--save-output`     | off                   | Save task outputs to `~/.claude-queue/outputs/`                                |
| `--stream`          | off                   | Stream Claude output to terminal in real-time (disables output saving)         |
| `--idle [SECONDS]`  | off                   | Keep worker running when queue is empty, polling every N seconds (default: 30) |

### `usage` — Check Claude usage limits

```bash
./claude-queue.py usage [flags]
```

| Flag                | Description                                           |
|---------------------|-------------------------------------------------------|
| `--session-key KEY` | Session cookie (alternative to `$CLAUDE_SESSION_KEY`) |
| `--api-url URL`     | Override the usage API endpoint                       |
| `--json`            | Output as JSON for scripting                          |

### `status` — Show queue statistics

```bash
./claude-queue.py status
```

### `list` — List tasks

```bash
./claude-queue.py list [--status STATUS]
```

| Flag              | Description                                                                  |
|-------------------|------------------------------------------------------------------------------|
| `--status STATUS` | Filter by status: `queued`, `running`, `completed`, `failed`, `rate_limited` |

### `remove` — Remove a task

```bash
./claude-queue.py remove TASK_ID
```

### `clear` — Remove completed tasks

```bash
./claude-queue.py clear
```

### `output` — Print saved output for a task

```bash
./claude-queue.py output TASK_ID
```

Prints the saved output file for a task. Requires the worker to have been run with `--save-output`.

## Checking Usage Limits

The `usage` command displays your current Claude Plan utilization:

```bash
./claude-queue.py usage
```

**Example output:**

```
============================================================
Claude Usage Limits
============================================================

5-Hour Session Limit: 🟢 OK
   Utilization: 45.5%
   Resets in:   2h 15m
   Reset time:  2026-01-04 15:30:00 PST

7-Day Weekly Limit: 🟡 HIGH
   Utilization: 72.3%
   Resets in:   3d 18h
   Reset time:  2026-01-08 10:00:00 PST

============================================================
```

**Status Indicators:**
- 🟢 **OK** - Below 70% utilization
- 🟡 **HIGH** - 70-89% utilization
- 🔴 **CRITICAL** - 90%+ utilization (worker will pause at 95%)

**Understanding the Limits:**
- **5-Hour Session Limit**: Rolling 5-hour window of Claude usage
- **7-Day Weekly Limit**: Rolling 7-day window of Claude usage
- Worker automatically pauses when either limit reaches 95%

**JSON Output:**
```bash
./claude-queue.py usage --json
```

Use JSON output for scripting or monitoring integrations.

## Quick Task Creation with Claude AI

When working with Claude and approaching usage limits, you can quickly create tasks without interrupting your workflow.

### Option 1: Claude Code Skill (Recommended)

The skill option is more efficient than the manual guide because it only loads into context when triggered, rather than being present throughout the entire conversation.

Install the built-in skill for seamless integration:

```bash
# Clone this repository if you haven't already
git clone https://github.com/vasiliyk/claude-queue
cd claude-queue

# Install the skill (copy the entire queue-task directory)
cp -r skills/queue-task ~/.claude/skills/

# Verify installation (should show: ~/.claude/skills/queue-task/SKILL.md)
ls ~/.claude/skills/queue-task/

# Restart Claude Code to load the skill
```

**Important:** The skill must be installed as a directory (`queue-task/`), not as a single file. The correct structure is:
```
~/.claude/skills/queue-task/SKILL.md
```

**After installation:**
- Claude will automatically recognize when you're approaching limits
- Just say: "Queue the remaining work" or "Create tasks for claude-queue"
- Claude creates task files and gives you one command to run
- No need to share files or remember instructions

### Option 2: Manual Guide (Works with claude.ai, web, code)

For claude.ai, web or if you prefer not to install the skill:

1. **Share the guide:** In your Claude conversation, attach or paste the contents of `CLAUDE_TASK_GUIDE.md`
2. **Claude will notice when limits approach** and proactively suggest queuing work - or you can request it: *"I'm hitting limits, create tasks for the remaining work"*
3. **Run the command:** Claude creates task files and gives you one command to execute

### What Claude Does

**Claude will:**
- Proactively suggest queuing work when it notices you're approaching limits
- Create properly formatted task files directly (no copy-paste needed)
- Give you ONE simple command to queue and start the worker

**Example:**
```
You: "I'm hitting limits. Queue the remaining refactoring work."

Claude: [Creates tasks.yaml file using Write tool]
        "Created tasks.yaml with 3 tasks.
         Run: ./claude-queue.py batch tasks.yaml && ./claude-queue.py worker"
```

This lets you walk away from your computer while tasks run unattended in the meantime.

## Setup

### Getting Your Session Key

The worker requires your Claude session key to monitor usage limits:

1. Go to https://claude.ai/settings/usage
2. Open browser DevTools (F12) → Application → Cookies
3. Copy the `sessionKey` cookie value
4. Set it as an environment variable:

```bash
export CLAUDE_SESSION_KEY="sk-ant-..."

# Or add to your shell profile (~/.bashrc, ~/.zshrc, etc.)
echo 'export CLAUDE_SESSION_KEY="sk-ant-..."' >> ~/.bashrc
```

### Running Worker in Background

Use tmux or screen to run the worker in the background:

```bash
# Set session key
export CLAUDE_SESSION_KEY="your-session-key"

# Start tmux session
tmux new -s claude-worker
./claude-queue.py worker
# Detach: Ctrl+B, then D
# Reattach: tmux attach -s claude-worker
```

### Worker tips

**Lower the pause threshold** if you want the worker to stop earlier (e.g. at 80% instead of 95%):

```bash
./claude-queue.py worker --threshold 80
```

**Keep the worker alive** between batches so you can add tasks without restarting:

```bash
./claude-queue.py worker --idle 10
```

**Stream output** to see what Claude is doing in real-time (disables output saving):

```bash
./claude-queue.py worker --stream
```

**Save task outputs** for review or debugging:

```bash
./claude-queue.py worker --save-output
```

Output files are saved to `~/.claude-queue/outputs/{task-id}.txt` and include task metadata plus the full Claude output. Only created for successfully completed tasks.

**Retry delay** controls the base for exponential backoff on non-rate-limit failures (`60s × 2^attempt`). Rate limit retries always use the `retry-after` value from the API instead.

```bash
./claude-queue.py worker --retry-delay 120
```

This is useful for:
- Reviewing completed work later
- Debugging issues with specific tasks
- Keeping a record of changes made
- Sharing results with team members

### JSON Format

```json
{
  "tasks": [
    {
      "prompt": "Refactor authentication",
      "session": "auth-refactor",
      "priority": 10,
      "max_attempts": 5
    }
  ]
}
```

Load with: `./claude-queue.py batch tasks.json`

## Storage

Tasks are stored in `~/.claude-queue/tasks.json`

```bash
# Backup
cp ~/.claude-queue/tasks.json ~/backups/

# Restore
cp ~/backups/tasks.json ~/.claude-queue/

# Start fresh
rm ~/.claude-queue/tasks.json
```

## Example Workflow

```yaml
# morning-tasks.yaml
tasks:
  - prompt: "Review and fix bugs from yesterday"
    session: "bug-fixes"
    priority: 10

  - prompt: "Implement new feature: user preferences"
    session: "user-prefs"
    priority: 8
    depends_on: ["bug-fixes"]

  - prompt: "Write tests for new feature"
    session: "tests"
    priority: 7
    depends_on: ["user-prefs"]

  - prompt: "Update documentation"
    session: "docs"
    priority: 5
    depends_on: ["tests"]
```

```bash
# Set session key for usage monitoring
export CLAUDE_SESSION_KEY="your-session-key"

# Load and run
./claude-queue.py batch morning-tasks.yaml
tmux new -s work './claude-queue.py worker'

# Monitor throughout the day
./claude-queue.py status
./claude-queue.py usage  # Check usage limits

# Add urgent tasks as needed
./claude-queue.py add "Hotfix for production" --priority 20

# End of day cleanup
./claude-queue.py clear
```

## Contributing

For development setup, testing, and project structure information, see [DEVELOPMENT.md](DEVELOPMENT.md).