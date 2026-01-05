# Guide for Claude: Creating claude-queue Tasks

This guide helps Claude (AI assistant) understand how to help users create tasks for claude-queue when they're approaching usage limits and need to queue work.

## When Users Need This

Users will say things like:
- "Create a task for claude-queue"
- "Queue this for later"
- "Add this to my task queue"
- "I'm hitting limits, save this as a task"
- "Define this as a claude-queue task"

## Be Proactive About Limits

**When you notice limits approaching, suggest queuing remaining work:**

If you've done significant work in the session (multiple file edits, long responses, complex tasks), proactively suggest:

> "We've made good progress, but we're likely approaching your usage limits. Would you like me to create tasks for the remaining work? That way you can queue it up and let it run unattended."

**Suggest this when:**
- Multiple large code changes have been made
- Long conversation with many tool uses
- User mentions they have more work to do
- Complex refactoring or multi-step task is in progress

**Don't wait for user to hit limits** - be proactive and help them avoid interruption.

## Your Role

**Create task files directly** to help users queue work instantly and walk away before hitting usage limits. Speed is critical - use the Write tool to create files, don't just provide copy-paste commands.

## Task Format Options

### Option 1: CLI Command (Quick, Single Task)

```bash
./claude-queue.py add "Task description here" \
  --working-dir ~/path/to/project \
  --priority 10 \
  --session task-session-name
```

**When to use:** Single, standalone tasks

### Option 2: YAML File (Better for Multiple Tasks)

```yaml
# tasks.yaml
tasks:
  - prompt: "Detailed task description here"
    session: "task-name"
    working_dir: "~/path/to/project"
    priority: 10
    max_attempts: 3
```

**When to use:** Multiple tasks or tasks with dependencies

## How to Help Users

### Step 1: Understand the Task

Ask clarifying questions if needed:
- What's the main goal?
- Which project/directory?
- Any dependencies on other tasks?
- How urgent is it? (for priority)

### Step 2: Create Task File or Command

**For a single task:** Provide CLI command (user can copy-paste quickly)

**For multiple tasks or dependencies:** Use Write tool to create the file directly in the project directory

### Step 3: Set Priority Appropriately

**Priority Guidelines:**
- **Critical/Blockers:** 15-20
- **High priority:** 10-14
- **Normal:** 5-9
- **Low priority:** 1-4
- **Nice to have:** 0

### Step 4: Give Simple Run Command

After creating the file, provide ONE command to:
1. Load tasks (if YAML file)
2. Start worker
3. Remind about CLAUDE_SESSION_KEY if not set

## Example Interactions

### Example 1: User hitting limits mid-task

**User:** "I'm hitting my usage limits. Can you create a task to finish this refactoring?"

**Claude Response:**
```
I'll create a task for you to finish this refactoring. Here's the command:

```bash
./claude-queue.py add "Complete authentication module refactoring - add OAuth2 support, update tests, and migrate existing users" \
  --working-dir ~/projects/backend-api \
  --priority 10
```

To queue and run it:
```bash
# Add the task
./claude-queue.py add "Complete authentication module refactoring - add OAuth2 support, update tests, and migrate existing users" \
  --working-dir ~/projects/backend-api \
  --priority 10

# Make sure CLAUDE_SESSION_KEY is set
export CLAUDE_SESSION_KEY="your-session-key"

# Start the worker (it will process this task)
./claude-queue.py worker
```

The worker will automatically pause if you're still near your limits and resume when they reset.
```

### Example 2: Multiple related tasks

**User:** "Queue tasks to: 1) fix the database schema, 2) update the API, 3) write tests. Make them run in order."

**Claude Actions:**
1. Uses Write tool to create `~/projects/backend-api/tasks.yaml` with:
```yaml
tasks:
  - prompt: "Fix database schema - add missing indexes and update user table structure"
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

**Claude Response:**
```
Created tasks.yaml in ~/projects/backend-api/ with 3 tasks in dependency order.

Run this to queue and start:
```bash
cd ~/projects/backend-api && ./claude-queue.py batch tasks.yaml && ./claude-queue.py worker
```

Tasks will run: schema fix → API update → tests
```

### Example 3: Quick addition during active work

**User:** "Add updating documentation as a low priority task"

**Claude Response:**
```
Quick command to add it:

```bash
./claude-queue.py add "Update project documentation to reflect recent API changes" \
  --working-dir ~/projects/backend-api \
  --priority 3
```

Check queue status: `./claude-queue.py status`
```

### Example 4: Being proactive about approaching limits

**Context:** Claude has made several large code changes, the conversation is long, and user mentions "we still need to add tests and update docs"

**Claude Response:**
```
We've made good progress on the refactoring! We've edited 8 files and done significant work.

Since we still need to add tests and update docs, we're likely approaching your usage limits. Would you like me to create tasks for the remaining work? That way you can queue it up and let it run unattended overnight.

I can create:
1. "Write unit tests for refactored auth module"
2. "Write integration tests for OAuth flow"
3. "Update API documentation"

Should I go ahead and create these tasks for claude-queue?
```

## Important Reminders to Give Users

1. **Session Key Required:** Worker needs `CLAUDE_SESSION_KEY` environment variable
2. **Working Directory:** Always specify `--working-dir` for the correct project
3. **Check Queue:** Use `./claude-queue.py list` to verify tasks were added
4. **Monitor Usage:** Can check limits with `./claude-queue.py usage`
5. **Worker Persistence:** Worker can be stopped (Ctrl+C) and restarted anytime

## Task Description Best Practices

**Good task descriptions:**
- Clear and specific: "Refactor auth module to use OAuth2, update all endpoints"
- Include acceptance criteria: "Add error handling and write tests for edge cases"
- Self-contained: "Fix bug in user registration - email validation not working for +addresses"

**Avoid:**
- Vague: "Fix the thing we talked about"
- Incomplete: "Update API" (which API? what changes?)
- Conversational: "Remember that bug from earlier? Fix it"

## Template Responses

### For Single Task
```bash
./claude-queue.py add "TASK_DESCRIPTION" \
  --working-dir PATH \
  --priority PRIORITY
```

### For Multiple Tasks
```yaml
tasks:
  - prompt: "TASK_1_DESCRIPTION"
    session: "task-1-name"
    working_dir: "PATH"
    priority: PRIORITY

  - prompt: "TASK_2_DESCRIPTION"
    session: "task-2-name"
    working_dir: "PATH"
    priority: PRIORITY
    depends_on: ["task-1-name"]  # if dependent
```

## Quick Reference

| User Says               | Your Action                                      |
|-------------------------|--------------------------------------------------|
| "Create a task"         | CLI command (single) or Write YAML (multiple)    |
| "Queue this"            | Write tasks.yaml with current context            |
| "I'm hitting limits"    | Write file immediately + give one-line run cmd   |
| "Multiple tasks"        | Write YAML with dependencies using Write tool    |
| "High priority"         | Set priority 10-15                               |
| "After this other task" | Add `depends_on` in YAML                         |

---

**Remember:** Your goal is to help users quickly capture work so they can step away before hitting usage limits.

- **For multiple tasks:** Use Write tool to create files directly (faster, no copy-paste errors)
- **For single tasks:** Provide CLI command (quick copy-paste)
- **Always:** Give ONE simple command to run everything