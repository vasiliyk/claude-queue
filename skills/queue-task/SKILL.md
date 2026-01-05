---
name: queue-task
description: Creates claude-queue tasks when usage limits are approaching. Use when the user is running low on Claude usage quota, mentions hitting limits, or when significant work remains and we should queue it for unattended execution. Proactively suggests queuing remaining work during long coding sessions.
allowed-tools: Write, Read
---

# Claude Queue Task Creator

This skill helps users quickly create tasks for claude-queue when approaching Claude Plan usage limits. It enables users to queue remaining work and walk away while tasks run unattended.

## When to Use This Skill

**Proactively suggest** creating tasks when:
- Multiple large code changes have been made in the session
- Long conversation with many tool uses
- User mentions they have more work to do
- Complex refactoring or multi-step task is in progress
- User explicitly says: "queue this", "create tasks", "I'm hitting limits"

**Don't wait for user to hit limits** - be proactive and help them avoid interruption.

## Proactive Suggestion Template

When you notice significant work has been done, say:

> "We've made good progress, but we're likely approaching your usage limits. Would you like me to create tasks for the remaining work? That way you can queue it up and let it run unattended."

List what tasks you could create and ask for confirmation.

## Task Creation Approach

### For Single Tasks
Provide a CLI command:
```bash
./claude-queue.py add "Task description" \
  --working-dir ~/path/to/project \
  --priority 10
```

### For Multiple Tasks or Dependencies
**Use the Write tool** to create a YAML file directly in the project directory. Don't provide copy-paste commands - create the file immediately.

## Task File Format

```yaml
tasks:
  - prompt: "Clear, specific task description"
    session: "task-session-name"
    working_dir: "~/path/to/project"
    priority: 10
    max_attempts: 3
    depends_on: ["other-session-name"]  # optional
```

## Priority Guidelines

- **Critical/Blockers:** 15-20
- **High priority:** 10-14
- **Normal:** 5-9
- **Low priority:** 1-4
- **Nice to have:** 0

## Task Description Best Practices

**Good descriptions:**
- Clear and specific: "Refactor auth module to use OAuth2, update all endpoints"
- Include acceptance criteria: "Add error handling and write tests for edge cases"
- Self-contained: "Fix bug in user registration - email validation not working for +addresses"

**Avoid:**
- Vague: "Fix the thing we talked about"
- Incomplete: "Update API" (which API? what changes?)
- Conversational: "Remember that bug from earlier? Fix it"

## Workflow

### Step 1: Understand the Task
Ask clarifying questions if needed:
- What's the main goal?
- Which project/directory?
- Any dependencies on other tasks?
- How urgent is it?

### Step 2: Create Task File
**For multiple tasks:** Use Write tool to create `tasks.yaml` in the project directory

Example using Write tool:
```yaml
# Write to ~/projects/backend-api/tasks.yaml
tasks:
  - prompt: "Fix database schema - add missing indexes and update user table"
    session: "db-schema-fix"
    working_dir: "~/projects/backend-api"
    priority: 10

  - prompt: "Update API endpoints to use new database schema"
    session: "api-update"
    working_dir: "~/projects/backend-api"
    priority: 8
    depends_on: ["db-schema-fix"]

  - prompt: "Write comprehensive integration tests for updated API"
    session: "api-tests"
    working_dir: "~/projects/backend-api"
    priority: 5
    depends_on: ["api-update"]
```

### Step 3: Provide One Simple Command

After creating the file, give the user ONE command to run:

```bash
cd ~/projects/backend-api && ./claude-queue.py batch tasks.yaml && ./claude-queue.py worker --save-output
```

**Note:** Add `--save-output` to save task outputs to `~/.claude-queue/outputs/` for review later.

## Example Interaction

**User:** "I'm hitting limits. Queue the remaining work."

**Your response:**
1. Use Write tool to create `tasks.yaml` in the project directory
2. Show what was created
3. Provide the one-line command to execute

```
Created tasks.yaml in ~/projects/backend-api/ with 3 tasks in dependency order.

Run this to queue and start:
```bash
cd ~/projects/backend-api && ./claude-queue.py batch tasks.yaml && ./claude-queue.py worker --save-output
```

Tasks will run: schema fix → API update → tests
Outputs will be saved to ~/.claude-queue/outputs/ for review.
```

## Important Reminders

1. **Session Key Required:** Worker needs `CLAUDE_SESSION_KEY` environment variable
2. **Working Directory:** Always specify `--working-dir` for the correct project
3. **Worker Auto-Pauses:** Automatically pauses at 95% usage and resumes when limits reset
4. **Create Files Directly:** Use Write tool for YAML files - don't ask users to copy-paste
5. **One Command:** Always provide a single command to run everything
6. **Save Outputs:** Use `--save-output` flag to save task outputs to `~/.claude-queue/outputs/` for later review

## Quick Reference

| User Says | Your Action |
|-----------|-------------|
| "Create a task" | CLI command (single) or Write YAML (multiple) |
| "Queue this" | Write tasks.yaml with current context |
| "I'm hitting limits" | Write file immediately + give one-line run cmd |
| "Multiple tasks" | Write YAML with dependencies using Write tool |
| "High priority" | Set priority 10-15 |
| "After this other task" | Add `depends_on` in YAML |

## Remember

Your goal is to help users quickly capture work so they can step away before hitting usage limits.

- **For multiple tasks:** Use Write tool to create files directly (faster, no copy-paste errors)
- **For single tasks:** Provide CLI command (quick copy-paste)
- **Always:** Give ONE simple command to run everything
- **Be proactive:** Suggest queuing work before limits are hit