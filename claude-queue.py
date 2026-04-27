#!/usr/bin/env python3
"""
Claude Code Task Queue Manager
Handles batch execution of Claude tasks with automatic retry on rate limits.

Usage:
    # Add tasks
    ./claude-queue.py add "Refactor authentication module" --session auth-refactor
    ./claude-queue.py add "Write tests for API endpoints" --session api-tests
    ./claude-queue.py add "Update documentation" --session docs-update

    # Start worker (requires CLAUDE_SESSION_KEY env var for usage limit checking)
    export CLAUDE_SESSION_KEY="your-session-key"
    ./claude-queue.py worker

    # Check Claude usage limits
    ./claude-queue.py usage

    # Check status
    ./claude-queue.py status

    # List all tasks
    ./claude-queue.py list
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import requests

# Optional YAML support for batch loading
try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("claude-queue")


# Custom exceptions
class QueueError(Exception):
    """Base exception for queue operations"""

    pass


class ValidationError(QueueError):
    """Raised when input validation fails"""

    pass


class QueueFileError(QueueError):
    """Raised when queue file operations fail"""

    pass


class TaskStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"


class ClaudeUsageChecker:
    """Checks Claude usage limits via the claude.ai API"""

    def __init__(
        self, session_key: str | None = None, api_url: str | None = None, org_id: str | None = None
    ):
        """
        Initialize the usage checker.

        Args:
            session_key: Claude session cookie. If not provided, reads from CLAUDE_SESSION_KEY env var.
            api_url: Override full API URL if needed
            org_id: Organization ID. If not provided, will be auto-detected from CLAUDE_ORG_ID env var or API.
        """
        self.session_key = session_key or os.getenv("CLAUDE_SESSION_KEY")

        if not self.session_key:
            raise ValueError(
                "Session key not provided. Set CLAUDE_SESSION_KEY environment variable.\n"
                "Get your session key from browser:\n"
                "1. Go to https://claude.ai/settings/usage\n"
                "2. Open DevTools > Application > Cookies\n"
                "3. Copy the 'sessionKey' cookie value"
            )

        self.session = requests.Session()
        self.session.cookies.set("sessionKey", self.session_key)

        # Add common headers to look like a browser
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": "https://claude.ai/",
            }
        )

        # Determine the API URL
        if api_url:
            self.usage_api_url = api_url
        else:
            # Get org ID from parameter, env var, or auto-detect
            self.org_id = org_id or os.getenv("CLAUDE_ORG_ID")
            if not self.org_id:
                logger.info("Auto-detecting organization ID...")
                self.org_id = self._get_organization_id()

            self.usage_api_url = f"https://claude.ai/api/organizations/{self.org_id}/usage"
            logger.debug(f"Using API URL: {self.usage_api_url}")

    def _get_organization_id(self) -> str:
        """
        Auto-detect organization ID from the Claude API.

        Returns:
            str: Organization ID

        Raises:
            ValueError: If organization ID cannot be determined
        """
        try:
            # Try to get account info which should contain organization details
            response = self.session.get("https://claude.ai/api/organizations", timeout=10)
            response.raise_for_status()

            orgs = response.json()

            # Get the first/default organization
            if isinstance(orgs, list) and len(orgs) > 0:
                org_id = orgs[0].get("uuid") or orgs[0].get("id")
                if org_id:
                    logger.info(f"Detected organization ID: {org_id}")
                    return org_id

            raise ValueError("No organizations found in API response")

        except requests.RequestException as e:
            raise ValueError(
                f"Failed to auto-detect organization ID: {e}\n"
                "Please set CLAUDE_ORG_ID environment variable or pass org_id parameter.\n"
                "Find your org ID from the Network tab at https://claude.ai/settings/usage"
            ) from e

    def fetch_usage(self) -> dict:
        """
        Fetch current usage data from Claude API.

        Returns:
            dict: Usage data with five_hour and seven_day limits

        Raises:
            requests.RequestException: If API request fails
        """
        try:
            response = self.session.get(self.usage_api_url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                raise ValueError(
                    "Authentication failed. Your session key may be expired.\n"
                    "Get a new session key from your browser cookies."
                ) from e
            raise

    def parse_usage(self, data: dict) -> dict:
        """
        Parse usage data into a more readable format.

        Args:
            data: Raw usage data from API

        Returns:
            dict: Parsed usage information
        """
        result = {}

        # Parse 5-hour limit
        if "five_hour" in data and data["five_hour"]:
            five_hour = data["five_hour"]
            result["five_hour"] = {
                "utilization": five_hour.get("utilization", 0),
                "utilization_percent": f"{five_hour.get('utilization', 0):.1f}%",
                "resets_at": five_hour.get("resets_at"),
                "resets_at_local": self._parse_timestamp(five_hour.get("resets_at")),
                "time_until_reset": self._time_until(five_hour.get("resets_at")),
            }

        # Parse 7-day limit
        if "seven_day" in data and data["seven_day"]:
            seven_day = data["seven_day"]
            result["seven_day"] = {
                "utilization": seven_day.get("utilization", 0),
                "utilization_percent": f"{seven_day.get('utilization', 0):.1f}%",
                "resets_at": seven_day.get("resets_at"),
                "resets_at_local": self._parse_timestamp(seven_day.get("resets_at")),
                "time_until_reset": self._time_until(seven_day.get("resets_at")),
            }

        # Include raw data for reference
        result["raw"] = data

        return result

    @staticmethod
    def _parse_timestamp(ts_str: str | None) -> str | None:
        """Convert ISO timestamp to local time string"""
        if not ts_str:
            return None
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            return ts_str

    @staticmethod
    def _time_until(ts_str: str | None) -> str | None:
        """Calculate time remaining until timestamp"""
        if not ts_str:
            return None
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            now = datetime.now(dt.tzinfo)
            delta = dt - now

            if delta.total_seconds() < 0:
                return "Already reset"

            hours = int(delta.total_seconds() // 3600)
            minutes = int((delta.total_seconds() % 3600) // 60)

            if hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"
        except Exception:
            return None

    def check_usage(self, json_output: bool = False) -> dict:
        """
        Check usage and print/return results.

        Args:
            json_output: If True, output as JSON

        Returns:
            dict: Parsed usage data
        """
        usage_data = self.fetch_usage()
        parsed = self.parse_usage(usage_data)

        if json_output:
            print(json.dumps(parsed, indent=2))
        else:
            self._print_usage(parsed)

        return parsed

    @staticmethod
    def _print_limit_section(name: str, data: dict) -> None:
        """Pretty print a single usage limit section"""
        u = data["utilization"]
        status = "🔴 CRITICAL" if u >= 90 else ("🟡 HIGH" if u >= 70 else "🟢 OK")
        print(f"\n{name}: {status}")
        print(f"   Utilization: {data['utilization_percent']}")
        print(f"   Resets in:   {data['time_until_reset']}")
        print(f"   Reset time:  {data['resets_at_local']}")

    @staticmethod
    def _print_usage(parsed: dict):
        """Pretty print usage information"""
        print("\n" + "=" * 60)
        print("Claude Usage Limits")
        print("=" * 60)
        if "five_hour" in parsed:
            ClaudeUsageChecker._print_limit_section("5-Hour Session Limit", parsed["five_hour"])
        if "seven_day" in parsed:
            ClaudeUsageChecker._print_limit_section("7-Day Weekly Limit", parsed["seven_day"])
        print("\n" + "=" * 60 + "\n")

    def is_limit_exceeded(self, threshold: float = 95.0) -> tuple[bool, str | None, dict | None]:
        """
        Check if usage limit is exceeded.

        Args:
            threshold: Utilization threshold percentage (0-100)

        Returns:
            tuple: (is_exceeded, reason, parsed_data)
        """
        try:
            usage_data = self.fetch_usage()
            parsed = self.parse_usage(usage_data)

            # Check 5-hour limit
            if "five_hour" in parsed:
                util = parsed["five_hour"]["utilization"]
                if util >= threshold:
                    return True, f"5-hour limit at {util:.1f}%", parsed

            # Check 7-day limit
            if "seven_day" in parsed:
                util = parsed["seven_day"]["utilization"]
                if util >= threshold:
                    return True, f"7-day limit at {util:.1f}%", parsed

            return False, None, None

        except Exception as e:
            logger.warning(f"Failed to check usage limits: {e}")
            return False, None, None


@dataclass
class Task:
    id: str
    prompt: str
    session_name: str | None
    status: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    last_error: str | None = None
    priority: int = 0  # Higher = higher priority
    depends_on: list[str] | None = None  # List of task IDs this task depends on
    working_dir: str | None = None  # Directory where task should execute

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        return cls(**data)


class TaskQueue:
    """Manages task queue using JSON file storage with file locking"""

    def __init__(self, queue_file: Path):
        self.queue_file = queue_file
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_queue_exists()

    def _ensure_queue_exists(self):
        if not self.queue_file.exists():
            self._save_tasks([])

    @staticmethod
    def _validate_prompt(prompt: str) -> None:
        """Validate task prompt"""
        if not prompt or not prompt.strip():
            raise ValidationError("Prompt cannot be empty")
        if len(prompt) > 10000:
            raise ValidationError("Prompt too long (max 10000 characters)")

    @staticmethod
    def _validate_session_name(session_name: str | None) -> None:
        """Validate session name"""
        if session_name is not None:
            if not session_name.strip():
                raise ValidationError("Session name cannot be empty string")
            if not session_name.replace("-", "").replace("_", "").isalnum():
                raise ValidationError(
                    "Session name must contain only alphanumeric characters, hyphens, and underscores"
                )
            if len(session_name) > 100:
                raise ValidationError("Session name too long (max 100 characters)")

    @staticmethod
    def _validate_priority(priority: int) -> None:
        """Validate priority value"""
        if not isinstance(priority, int):
            raise ValidationError("Priority must be an integer")
        if priority < 0 or priority > 100:
            raise ValidationError("Priority must be between 0 and 100")

    @staticmethod
    def _validate_max_attempts(max_attempts: int) -> None:
        """Validate max_attempts value"""
        if not isinstance(max_attempts, int):
            raise ValidationError("max_attempts must be an integer")
        if max_attempts < 1 or max_attempts > 100:
            raise ValidationError("max_attempts must be between 1 and 100")

    @staticmethod
    def _validate_working_dir(working_dir: str | None) -> None:
        """Validate working_dir exists"""
        if working_dir is not None:
            working_path = Path(working_dir).expanduser().resolve()
            if not working_path.exists():
                raise ValidationError(f"Working directory does not exist: {working_dir}")
            if not working_path.is_dir():
                raise ValidationError(f"Working directory is not a directory: {working_dir}")

    def _validate_dependencies(
        self,
        depends_on: list[str] | None,
        task_id: str | None = None,
        existing_tasks: list[Task] | None = None,
    ) -> None:
        """Validate task dependencies"""
        if not depends_on:
            return

        if not isinstance(depends_on, list):
            raise ValidationError("depends_on must be a list of task IDs")

        if not all(isinstance(dep, str) for dep in depends_on):
            raise ValidationError("All dependency IDs must be strings")

        # Check for self-dependency
        if task_id and task_id in depends_on:
            raise ValidationError("Task cannot depend on itself")

        # Use pre-loaded tasks if provided to avoid a second file read
        if existing_tasks is None:
            existing_tasks = self._load_tasks()
        existing_ids = {task.id for task in existing_tasks}

        for dep_id in depends_on:
            if dep_id not in existing_ids:
                raise ValidationError(f"Dependency task '{dep_id}' does not exist")

        # Check for circular dependencies
        if task_id:
            self._check_circular_dependencies(task_id, depends_on, existing_tasks)

    def _check_circular_dependencies(
        self, task_id: str, depends_on: list[str], all_tasks: list[Task]
    ) -> None:
        """Check for circular dependencies using DFS"""
        task_map = {task.id: task for task in all_tasks}
        visited = set()
        rec_stack = set()

        def has_cycle(current_id: str) -> bool:
            if current_id in rec_stack:
                return True
            if current_id in visited:
                return False

            visited.add(current_id)
            rec_stack.add(current_id)

            # Check dependencies of current task
            if current_id in task_map:
                current_deps = task_map[current_id].depends_on or []
                for dep in current_deps:
                    if has_cycle(dep):
                        return True
            # Check new dependencies we're adding
            elif current_id == task_id:
                for dep in depends_on:
                    if has_cycle(dep):
                        return True

            rec_stack.remove(current_id)
            return False

        if has_cycle(task_id):
            raise ValidationError("Circular dependency detected")

    def _dependencies_satisfied(self, task: Task, all_tasks: list[Task]) -> bool:
        """Check if all dependencies of a task are completed"""
        if not task.depends_on:
            return True

        task_map = {t.id: t for t in all_tasks}

        for dep_id in task.depends_on:
            dep_task = task_map.get(dep_id)
            if not dep_task:
                # Dependency doesn't exist (maybe deleted), allow task to run
                logger.warning(f"Task {task.id} depends on non-existent task {dep_id}")
                continue
            if dep_task.status != TaskStatus.COMPLETED.value:
                return False

        return True

    def _load_tasks(self) -> list[Task]:
        """Load tasks from file with error handling and file locking"""
        try:
            with open(self.queue_file) as f:
                # Acquire shared lock for reading
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                    if not isinstance(data, list):
                        raise QueueFileError("Invalid queue format: expected list")
                    return [Task.from_dict(t) for t in data]
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError:
            logger.warning(f"Queue file not found: {self.queue_file}, creating new queue")
            self._ensure_queue_exists()
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Corrupted queue file: {e}")
            # Backup corrupted file
            backup_path = self.queue_file.with_suffix(".json.backup")
            shutil.copy(self.queue_file, backup_path)
            logger.info(f"Backed up corrupted queue to: {backup_path}")
            raise QueueFileError(f"Corrupted queue file (backed up to {backup_path})") from e
        except Exception as e:
            logger.error(f"Error loading tasks: {e}")
            raise QueueFileError(f"Failed to load tasks: {e}") from e

    def _save_tasks(self, tasks: list[Task]) -> None:
        """Atomically save tasks with file locking"""
        temp_file = None
        try:
            # Write to temporary file first
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self.queue_file.parent,
                prefix=".tasks_tmp_",
                suffix=".json",
                delete=False,
            ) as f:
                temp_file = Path(f.name)
                # Acquire exclusive lock for writing
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    json.dump([t.to_dict() for t in tasks], f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())  # Ensure written to disk
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            # Atomic rename
            temp_file.replace(self.queue_file)
            logger.debug(f"Successfully saved {len(tasks)} tasks")
        except Exception as e:
            logger.error(f"Error saving tasks: {e}")
            if temp_file and temp_file.exists():
                temp_file.unlink()
            raise QueueFileError(f"Failed to save tasks: {e}") from e

    def add_task(
        self,
        prompt: str,
        session_name: str | None = None,
        max_attempts: int = 3,
        priority: int = 0,
        depends_on: list[str] | None = None,
        working_dir: str | None = None,
    ) -> Task:
        # Validate inputs
        self._validate_prompt(prompt)
        self._validate_session_name(session_name)
        self._validate_priority(priority)
        self._validate_max_attempts(max_attempts)
        self._validate_working_dir(working_dir)

        tasks = self._load_tasks()

        # Generate unique ID
        task_id = f"task-{uuid.uuid4().hex[:12]}"

        # Validate dependencies after generating task_id (reuse already-loaded tasks)
        self._validate_dependencies(depends_on, task_id, existing_tasks=tasks)

        task = Task(
            id=task_id,
            prompt=prompt,
            session_name=session_name or task_id,
            status=TaskStatus.QUEUED.value,
            created_at=datetime.now().isoformat(),
            max_attempts=max_attempts,
            priority=priority,
            depends_on=depends_on,
            working_dir=working_dir,
        )

        tasks.append(task)
        self._save_tasks(tasks)

        dep_info = f" (depends on: {', '.join(depends_on)})" if depends_on else ""
        logger.info(f"Added task {task_id} with priority {priority}{dep_info}")

        return task

    def get_next_task(self) -> Task | None:
        """Get next queued task (highest priority first, dependencies satisfied)"""
        tasks = self._load_tasks()

        queued_tasks = [
            t
            for t in tasks
            if t.status in [TaskStatus.QUEUED.value, TaskStatus.RATE_LIMITED.value]
            and t.attempts < t.max_attempts
            and self._dependencies_satisfied(t, tasks)  # Check dependencies
        ]

        if not queued_tasks:
            return None

        # Sort by priority (desc) then created_at (asc)
        queued_tasks.sort(key=lambda t: (-t.priority, t.created_at))
        return queued_tasks[0]

    def update_task(self, task_id: str, **updates) -> None:
        tasks = self._load_tasks()

        for task in tasks:
            if task.id == task_id:
                for key, value in updates.items():
                    setattr(task, key, value)
                break
        else:
            raise QueueError(f"Task {task_id} not found")

        self._save_tasks(tasks)

    def get_all_tasks(self) -> list[Task]:
        return self._load_tasks()

    def remove_task(self, task_id: str):
        tasks = self._load_tasks()
        tasks = [t for t in tasks if t.id != task_id]
        self._save_tasks(tasks)

    def clear_completed(self):
        """Remove completed tasks"""
        tasks = self._load_tasks()
        tasks = [t for t in tasks if t.status != TaskStatus.COMPLETED.value]
        self._save_tasks(tasks)

    def get_stats(self):
        tasks = self._load_tasks()
        counts = Counter(t.status for t in tasks)
        return {
            "total": len(tasks),
            "queued": counts[TaskStatus.QUEUED.value],
            "running": counts[TaskStatus.RUNNING.value],
            "completed": counts[TaskStatus.COMPLETED.value],
            "failed": counts[TaskStatus.FAILED.value],
            "rate_limited": counts[TaskStatus.RATE_LIMITED.value],
        }


class ClaudeWorker:
    """Executes tasks from queue with retry logic"""

    def __init__(
        self,
        queue: TaskQueue,
        base_retry_delay: int = 60,
        usage_checker: ClaudeUsageChecker | None = None,
        save_output: bool = False,
        output_dir: Path | None = None,
        stream_output: bool = False,
        usage_threshold: float = 95.0,
        idle: bool = False,
        idle_interval: int = 30,
    ):
        self.queue = queue
        self.base_retry_delay = base_retry_delay
        self.usage_checker = usage_checker
        self.running = True
        self.save_output = save_output
        self.stream_output = stream_output
        self.usage_threshold = usage_threshold
        self.idle = idle
        self.idle_interval = idle_interval
        self.output_dir = output_dir or (Path.home() / ".claude-queue" / "outputs")

        if self.save_output:
            self.output_dir.mkdir(parents=True, exist_ok=True)

    def parse_rate_limit_info(self, stderr: str) -> dict[str, int | str | None]:
        """
        Parse rate limit information from Claude CLI error output.

        Returns dict with:
        - retry_after: seconds to wait (int or None)
        - error_message: the actual error message
        """
        info = {"retry_after": None, "error_message": stderr[:500]}

        # Try to extract retry-after seconds from error message
        # Common patterns: "retry after X seconds", "wait X seconds", "try again in X seconds"
        retry_patterns = [
            r"retry\s+after\s+(\d+)\s+seconds?",
            r"wait\s+(\d+)\s+seconds?",
            r"try\s+again\s+in\s+(\d+)\s+seconds?",
            r"retry-after:\s*(\d+)",
        ]

        for pattern in retry_patterns:
            match = re.search(pattern, stderr.lower())
            if match:
                info["retry_after"] = int(match.group(1))  # type: ignore[assignment]
                logger.info(f"Found retry-after: {info['retry_after']}s")
                break

        return info  # type: ignore[return-value]

    def calculate_wait_time(
        self, rate_limit_info: dict[str, int | str | None], task: Task
    ) -> int | None:
        """
        Calculate how long to wait before retrying based on retry-after header.

        Returns None if rate limit info cannot be extracted, indicating the task should fail.
        """
        if rate_limit_info["retry_after"] is not None:
            wait_seconds = rate_limit_info["retry_after"]
            logger.info(f"Using retry-after: {wait_seconds}s")
            return max(int(wait_seconds), 1)

        # No rate limit info available
        logger.error("Could not extract retry-after from rate limit error")
        return None

    def check_and_wait_for_limits(self) -> None:
        """Check usage limits and wait if exceeded"""
        if not self.usage_checker:
            return

        while True:
            try:
                exceeded, reason, parsed = self.usage_checker.is_limit_exceeded(
                    threshold=self.usage_threshold
                )

                if not exceeded:
                    return  # Limits are OK, proceed

                # Find which limit is exceeded and when it resets
                reset_info = None
                limit_name = None
                if (
                    parsed
                    and "five_hour" in parsed
                    and parsed["five_hour"]["utilization"] >= self.usage_threshold
                ):
                    reset_info = parsed["five_hour"]
                    limit_name = "5-hour session limit"
                elif (
                    parsed
                    and "seven_day" in parsed
                    and parsed["seven_day"]["utilization"] >= self.usage_threshold
                ):
                    reset_info = parsed["seven_day"]
                    limit_name = "7-day weekly limit"

                if reset_info:
                    reset_timestamp = reset_info.get("resets_at")
                    if reset_timestamp:
                        try:
                            reset_dt = datetime.fromisoformat(
                                reset_timestamp.replace("Z", "+00:00")
                            )
                            now = datetime.now(reset_dt.tzinfo)
                            seconds_until_reset = int((reset_dt - now).total_seconds())

                            if seconds_until_reset <= 0:
                                return

                            print(f"\n{'=' * 60}")
                            print(f"⚠ Usage limit exceeded: {reason}")
                            print(f"{'=' * 60}")
                            print(f"{limit_name}: {reset_info['utilization_percent']}")
                            print(f"Resets in:   {reset_info['time_until_reset']}")
                            print(f"Reset time:  {reset_info['resets_at_local']}")
                            print(f"\nSleeping until reset ({reset_info['time_until_reset']})...")
                            print(f"{'=' * 60}\n")

                            time.sleep(seconds_until_reset + 10)
                            return  # Re-check limits after wake
                        except Exception as e:
                            logger.warning(f"Could not parse reset time: {e}")
                            time.sleep(300)  # Fallback: wait 5 minutes
                    else:
                        logger.warning("Limit exceeded but couldn't determine reset time")
                        time.sleep(300)
                else:
                    logger.warning("Limit exceeded but couldn't determine reset time")
                    time.sleep(60)

            except Exception as e:
                logger.error(f"Error checking usage limits: {e}")
                return

    def execute_task(self, task: Task) -> bool:
        """Execute a single task. Returns True if successful."""

        print(f"\n{'=' * 60}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Executing task: {task.id}")
        print(f"Session: {task.session_name}")
        if task.working_dir:
            print(f"Working dir: {task.working_dir}")
        print(f"Attempt: {task.attempts + 1}/{task.max_attempts}")
        print(
            f"Prompt: {task.prompt[:100]}..."
            if len(task.prompt) > 100
            else f"Prompt: {task.prompt}"
        )
        print(f"{'=' * 60}\n")

        # Update task status
        self.queue.update_task(
            task.id,
            status=TaskStatus.RUNNING.value,
            started_at=datetime.now().isoformat(),
            attempts=task.attempts + 1,
        )

        try:
            # Build Claude command
            cmd = ["claude", "-p", task.prompt]

            # Determine working directory
            cwd = None
            if task.working_dir:
                cwd = Path(task.working_dir).expanduser().resolve()
                logger.info(f"Executing in directory: {cwd}")

            # Execute Claude
            if self.stream_output:
                # Stream stdout to terminal; capture stderr for rate-limit detection
                result = subprocess.run(
                    cmd, stdout=None, stderr=subprocess.PIPE, text=True, timeout=3600, cwd=cwd
                )
                stdout_out, stderr_out = "", result.stderr
            else:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, cwd=cwd)
                stdout_out, stderr_out = result.stdout, result.stderr

            if result.returncode == 0:
                print(f"✓ Task {task.id} completed successfully")

                # Save output if requested
                if self.save_output:
                    output_file = self.output_dir / f"{task.id}.txt"
                    try:
                        with open(output_file, "w") as f:
                            f.write(f"Task: {task.id}\n")
                            f.write(f"Session: {task.session_name}\n")
                            f.write(f"Completed: {datetime.now().isoformat()}\n")
                            f.write("=" * 60 + "\n")
                            f.write(stdout_out)
                        print(f"  Output saved to: {output_file}")
                    except Exception as e:
                        logger.warning(f"Failed to save output: {e}")

                self.queue.update_task(
                    task.id,
                    status=TaskStatus.COMPLETED.value,
                    completed_at=datetime.now().isoformat(),
                )
                return True
            else:
                # Check if rate limited
                if "rate limit" in stderr_out.lower() or "rate_limit" in stderr_out.lower():
                    print(f"⏸ Task {task.id} hit rate limit")

                    # Parse rate limit information from error
                    rate_limit_info = self.parse_rate_limit_info(stderr_out)

                    self.queue.update_task(
                        task.id,
                        status=TaskStatus.RATE_LIMITED.value,
                        last_error=rate_limit_info["error_message"],
                    )
                    return False
                else:
                    print(f"✗ Task {task.id} failed: {stderr_out[:200]}")

                    # Check if max attempts reached
                    if task.attempts + 1 >= task.max_attempts:
                        self.queue.update_task(
                            task.id, status=TaskStatus.FAILED.value, last_error=stderr_out[:500]
                        )
                    else:
                        self.queue.update_task(
                            task.id, status=TaskStatus.QUEUED.value, last_error=stderr_out[:500]
                        )
                    return False

        except subprocess.TimeoutExpired:
            print(f"⏱ Task {task.id} timed out")
            self.queue.update_task(
                task.id, status=TaskStatus.QUEUED.value, last_error="Execution timeout"
            )
            return False

        except Exception as e:
            print(f"✗ Task {task.id} error: {str(e)}")
            self.queue.update_task(
                task.id,
                status=TaskStatus.FAILED.value
                if task.attempts + 1 >= task.max_attempts
                else TaskStatus.QUEUED.value,
                last_error=str(e),
            )
            return False

    def run(self):
        """Main worker loop"""
        print("Claude Code Task Worker Started")
        print("Press Ctrl+C to stop\n")

        # Reset tasks stuck in RUNNING from a previously interrupted worker
        running_tasks = [
            t for t in self.queue.get_all_tasks() if t.status == TaskStatus.RUNNING.value
        ]
        if running_tasks:
            print(f"⚠ Resetting {len(running_tasks)} interrupted task(s) to queued")
            for t in running_tasks:
                self.queue.update_task(t.id, status=TaskStatus.QUEUED.value)

        try:
            while self.running:
                # Check usage limits before getting next task
                self.check_and_wait_for_limits()

                task = self.queue.get_next_task()

                if task is None:
                    if self.idle:
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] No tasks. Polling again in {self.idle_interval}s..."
                        )
                        time.sleep(self.idle_interval)
                        continue
                    print(
                        f"\n[{datetime.now().strftime('%H:%M:%S')}] No tasks in queue. Worker exiting."
                    )
                    break

                success = self.execute_task(task)

                if not success:
                    # Check if task was rate limited and calculate wait time
                    updated_task = None
                    for t in self.queue.get_all_tasks():
                        if t.id == task.id:
                            updated_task = t
                            break

                    if updated_task and updated_task.status == TaskStatus.RATE_LIMITED.value:
                        # Parse rate limit info from last error
                        rate_limit_info = self.parse_rate_limit_info(updated_task.last_error or "")
                        delay = self.calculate_wait_time(rate_limit_info, updated_task)

                        if delay is None:
                            # Could not extract rate limit info - fail the task
                            print(
                                "\n✗ Could not extract retry-after from rate limit error. Marking task as failed.\n"
                            )
                            self.queue.update_task(
                                updated_task.id,
                                status=TaskStatus.FAILED.value,
                                last_error="Rate limited but could not extract retry-after information",
                            )
                        else:
                            # Wait for the specified time
                            print(f"\n⏳ Rate limited. Waiting {delay}s (from retry-after)...\n")
                            time.sleep(delay)
                    else:
                        # Non-rate-limit failure, use exponential backoff
                        delay = self.base_retry_delay * (2 ** min(task.attempts, 5))
                        print(f"\n⏳ Waiting {delay}s before next task...\n")
                        time.sleep(delay)
                else:
                    # Small delay between successful tasks
                    time.sleep(5)

        except KeyboardInterrupt:
            print("\n\n⚠ Worker stopped by user")
            self.running = False


def cmd_add(args, queue: TaskQueue):
    """Add new task to queue"""
    task = queue.add_task(
        prompt=args.prompt,
        session_name=args.session,
        max_attempts=args.max_attempts,
        priority=args.priority,
        working_dir=getattr(args, "working_dir", None),
    )
    print(f"✓ Task added: {task.id}")
    print(f"  Session: {task.session_name}")
    print(f"  Priority: {task.priority}")
    if task.working_dir:
        print(f"  Working dir: {task.working_dir}")
    print(
        f"  Prompt: {task.prompt[:80]}..." if len(task.prompt) > 80 else f"  Prompt: {task.prompt}"
    )


def cmd_worker(args, queue: TaskQueue):
    """Start worker process"""
    # Usage limit checking is mandatory
    try:
        usage_checker = ClaudeUsageChecker(session_key=args.session_key, api_url=args.api_url)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    worker = ClaudeWorker(
        queue,
        base_retry_delay=args.retry_delay,
        usage_checker=usage_checker,
        save_output=args.save_output,
        stream_output=args.stream,
        usage_threshold=args.threshold,
        idle=args.idle,
        idle_interval=args.idle_interval,
    )
    worker.run()


def cmd_status(args, queue: TaskQueue):
    """Show queue status"""
    stats = queue.get_stats()

    print("\nClaude Task Queue Status")
    print("=" * 40)
    print(f"Total tasks:      {stats['total']}")
    print(f"Queued:           {stats['queued']}")
    print(f"Running:          {stats['running']}")
    print(f"Completed:        {stats['completed']}")
    print(f"Failed:           {stats['failed']}")
    print(f"Rate Limited:     {stats['rate_limited']}")
    print("=" * 40)

    # Show next task
    next_task = queue.get_next_task()
    if next_task:
        print(f"\nNext task: {next_task.id}")
        print(f"  Session: {next_task.session_name}")
        print(f"  Priority: {next_task.priority}")
        print(f"  Attempts: {next_task.attempts}/{next_task.max_attempts}")


def cmd_list(args, queue: TaskQueue):
    """List all tasks"""
    tasks = queue.get_all_tasks()

    if not tasks:
        print("No tasks in queue")
        return

    # Filter by status if specified
    if args.status:
        tasks = [t for t in tasks if t.status == args.status]

    # Sort by priority and created_at
    tasks.sort(key=lambda t: (-t.priority, t.created_at))

    print(f"\nTotal tasks: {len(tasks)}\n")

    for task in tasks:
        status_icon = {
            TaskStatus.QUEUED.value: "⏳",
            TaskStatus.RUNNING.value: "▶️",
            TaskStatus.COMPLETED.value: "✓",
            TaskStatus.FAILED.value: "✗",
            TaskStatus.RATE_LIMITED.value: "⏸",
        }.get(task.status, "?")

        print(f"{status_icon} {task.id}")
        print(
            f"   Status: {task.status} | Priority: {task.priority} | Attempts: {task.attempts}/{task.max_attempts}"
        )
        print(f"   Session: {task.session_name}")
        if task.working_dir:
            print(f"   Working dir: {task.working_dir}")
        print(f"   Created: {task.created_at}")

        prompt_preview = task.prompt[:100] + "..." if len(task.prompt) > 100 else task.prompt
        print(f"   Prompt: {prompt_preview}")

        if task.depends_on:
            deps_display = ", ".join(task.depends_on)
            print(f"   Dependencies: {deps_display}")

        if task.last_error:
            error_preview = (
                task.last_error[:80] + "..." if len(task.last_error) > 80 else task.last_error
            )
            print(f"   Last Error: {error_preview}")

        print()


def cmd_remove(args, queue: TaskQueue):
    """Remove task from queue"""
    queue.remove_task(args.task_id)
    print(f"✓ Task {args.task_id} removed")


def cmd_clear(args, queue: TaskQueue):
    """Clear completed tasks"""
    queue.clear_completed()
    print("✓ Completed tasks cleared")


def load_batch_file(file_path: Path) -> list[dict]:
    """Load tasks from YAML or JSON file"""
    if not file_path.exists():
        raise QueueFileError(f"File not found: {file_path}")

    # Determine file type and load
    if file_path.suffix in [".yaml", ".yml"]:
        if not HAS_YAML:
            raise QueueError("PyYAML not installed. Install with: pip install pyyaml")
        try:
            with open(file_path) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise QueueFileError(f"Invalid YAML file: {e}") from e
    elif file_path.suffix == ".json":
        try:
            with open(file_path) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise QueueFileError(f"Invalid JSON file: {e}") from e
    else:
        raise ValidationError(
            f"Unsupported file format: {file_path.suffix}. Use .yaml, .yml, or .json"
        )

    # Extract tasks
    if not isinstance(data, dict) or "tasks" not in data:
        raise ValidationError("Invalid file format. Expected a dictionary with 'tasks' key")

    tasks = data["tasks"]
    if not isinstance(tasks, list):
        raise ValidationError("'tasks' must be a list")

    # Validate each task has required fields
    for i, task in enumerate(tasks, 1):
        if not isinstance(task, dict):
            raise ValidationError(f"Task {i}: must be a dictionary")
        if "prompt" not in task:
            raise ValidationError(f"Task {i}: missing required field 'prompt'")

    return tasks


def cmd_usage(args):
    """Check Claude usage limits"""
    try:
        checker = ClaudeUsageChecker(session_key=args.session_key, api_url=args.api_url)
        checker.check_usage(json_output=args.json)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"API Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_batch(args, queue: TaskQueue):
    """Load tasks from batch file with dependency resolution"""
    # Load tasks from file
    tasks = load_batch_file(args.file)

    logger.info(f"Loaded {len(tasks)} tasks from {args.file}")
    print(f"\nLoaded {len(tasks)} tasks from {args.file}")

    # Dry run - just show what would be added
    if args.dry_run:
        print("\nDRY RUN - Tasks that would be added:\n")
        for i, task_data in enumerate(tasks, 1):
            session = task_data.get("session", "auto-generated")
            priority = task_data.get("priority", 0)
            max_attempts = task_data.get("max_attempts", 3)
            depends_on = task_data.get("depends_on", [])
            prompt = task_data["prompt"]
            prompt_preview = prompt[:80] + "..." if len(prompt) > 80 else prompt

            print(f"{i}. Session: {session}")
            print(f"   Priority: {priority} | Max attempts: {max_attempts}")
            if depends_on:
                print(f"   Depends on: {', '.join(depends_on)}")
            print(f"   Prompt: {prompt_preview}")
            print()
        return

    # Multi-pass approach for dependency resolution:
    # Keep adding tasks iteratively until all are added or no progress is made
    # This handles circular dependencies and complex dependency chains

    added_count = 0
    failed_count = 0
    session_to_id: dict[str, str] = {}  # Map session names to task IDs

    # Separate tasks by dependency status
    remaining_tasks = list(tasks)  # Copy of all tasks to process
    pass_number = 1

    while remaining_tasks:
        print("\n" + "=" * 60)
        print(f"PASS {pass_number}: Processing remaining tasks")
        print("=" * 60 + "\n")

        tasks_added_this_pass = 0
        still_remaining = []

        for task_data in remaining_tasks:
            try:
                # Resolve session names to task IDs for dependencies
                depends_on_sessions = task_data.get("depends_on", [])
                depends_on_ids = []

                if depends_on_sessions:
                    # Check if all dependencies can be resolved
                    can_resolve = True
                    for dep_session in depends_on_sessions:
                        if dep_session in session_to_id:
                            depends_on_ids.append(session_to_id[dep_session])
                        else:
                            # Dependency not yet added, skip this task for now
                            can_resolve = False
                            break

                    if not can_resolve:
                        # Dependencies not ready, try again next pass
                        still_remaining.append(task_data)
                        continue

                # Add the task
                task = queue.add_task(
                    prompt=task_data["prompt"],
                    session_name=task_data.get("session"),
                    priority=task_data.get("priority", 0),
                    max_attempts=task_data.get("max_attempts", 3),
                    depends_on=depends_on_ids if depends_on_ids else None,
                    working_dir=task_data.get("working_dir"),
                )
                session_display = task.session_name or "auto-generated"

                # Store mapping for dependency resolution
                if task_data.get("session"):
                    session_to_id[task_data["session"]] = task.id

                prompt_preview = task.prompt[:50] + "..." if len(task.prompt) > 50 else task.prompt
                dep_display = (
                    f" (depends on: {', '.join(depends_on_sessions)})"
                    if depends_on_sessions
                    else ""
                )
                print(f"✓ Added: {session_display} - {prompt_preview}{dep_display}")
                added_count += 1
                tasks_added_this_pass += 1

            except (ValidationError, QueueError) as e:
                # Task failed validation (e.g., circular dependency)
                session_display = task_data.get("session", "unnamed")
                print(f"✗ Failed: {session_display} - {e}")
                failed_count += 1

        # Check if we made progress
        if tasks_added_this_pass == 0 and still_remaining:
            # No progress made, remaining tasks have unresolvable dependencies
            print(
                f"\n⚠ No progress in pass {pass_number}. Remaining tasks have circular or missing dependencies:"
            )
            for task_data in still_remaining:
                session_display = task_data.get("session", "unnamed")
                deps = ", ".join(task_data.get("depends_on", []))
                print(f"✗ Failed: {session_display} - Cannot resolve dependencies: {deps}")
                failed_count += 1
            break

        remaining_tasks = still_remaining
        pass_number += 1

    # Summary
    print(f"\n{'=' * 60}")
    print(f"Added {added_count} tasks to queue")
    if failed_count > 0:
        print(f"Failed to add {failed_count} tasks")
    print(f"{'=' * 60}")

    # Show how to start worker
    if added_count > 0:
        print("\nStart worker with:")
        print("  ./claude-queue.py worker")


def main():
    parser = argparse.ArgumentParser(
        description="Claude Code Task Queue Manager\n\nFor examples and documentation, see README.md",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )

    # Queue file location
    default_queue = Path.home() / ".claude-queue" / "tasks.json"
    parser.add_argument(
        "--queue-file",
        type=Path,
        default=default_queue,
        help="Queue file location (default: ~/.claude-queue/tasks.json)",
    )

    subparsers = parser.add_subparsers(dest="command", required=False)

    # Add command
    add_parser = subparsers.add_parser("add", help="Add task to queue")
    add_parser.add_argument("prompt", help="Task prompt/description")
    add_parser.add_argument("--session", help="Session name (auto-generated if not provided)")
    add_parser.add_argument(
        "--max-attempts", type=int, default=3, help="Max retry attempts (default: 3)"
    )
    add_parser.add_argument(
        "--priority", type=int, default=0, help="Task priority (higher = first, default: 0)"
    )
    add_parser.add_argument(
        "--working-dir", help="Working directory for task execution (default: current directory)"
    )

    # Worker command
    worker_parser = subparsers.add_parser(
        "worker", help="Start task worker (requires CLAUDE_SESSION_KEY env var)"
    )
    worker_parser.add_argument(
        "--retry-delay", type=int, default=60, help="Base retry delay in seconds (default: 60)"
    )
    worker_parser.add_argument(
        "--session-key",
        help="Claude session cookie for usage checking (or use CLAUDE_SESSION_KEY env var)",
    )
    worker_parser.add_argument("--api-url", help="Override API endpoint URL for usage checking")
    worker_parser.add_argument(
        "--save-output", action="store_true", help="Save task outputs to ~/.claude-queue/outputs/"
    )
    worker_parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream Claude output to terminal in real-time (disables output saving)",
    )
    worker_parser.add_argument(
        "--threshold",
        type=float,
        default=95.0,
        help="Usage percentage at which worker pauses (default: 95.0)",
    )
    worker_parser.add_argument(
        "--idle",
        action="store_true",
        help="Keep worker running when queue is empty, polling for new tasks",
    )
    worker_parser.add_argument(
        "--idle-interval",
        type=int,
        default=30,
        help="Seconds between polls when idle (default: 30)",
    )

    # Status command
    subparsers.add_parser("status", help="Show queue status")

    # List command
    list_parser = subparsers.add_parser("list", help="List all tasks")
    list_parser.add_argument(
        "--status", choices=[s.value for s in TaskStatus], help="Filter by status"
    )

    # Remove command
    remove_parser = subparsers.add_parser("remove", help="Remove task from queue")
    remove_parser.add_argument("task_id", help="Task ID to remove")

    # Clear command
    subparsers.add_parser("clear", help="Clear completed tasks")

    # Batch command
    batch_parser = subparsers.add_parser("batch", help="Load tasks from YAML/JSON file")
    batch_parser.add_argument("file", type=Path, help="Task file (YAML or JSON)")
    batch_parser.add_argument(
        "--dry-run", action="store_true", help="Show tasks without adding them"
    )

    # Usage command
    usage_parser = subparsers.add_parser("usage", help="Check Claude usage limits")
    usage_parser.add_argument(
        "--session-key", help="Claude session cookie (or use CLAUDE_SESSION_KEY env var)"
    )
    usage_parser.add_argument("--json", action="store_true", help="Output as JSON")
    usage_parser.add_argument("--api-url", help="Override API endpoint URL")

    args = parser.parse_args()

    # Show help if no command provided
    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Initialize queue (not needed for usage command)
    queue = TaskQueue(args.queue_file) if args.command != "usage" else None

    # Execute command
    if args.command == "add":
        cmd_add(args, queue)
    elif args.command == "worker":
        cmd_worker(args, queue)
    elif args.command == "status":
        cmd_status(args, queue)
    elif args.command == "list":
        cmd_list(args, queue)
    elif args.command == "remove":
        cmd_remove(args, queue)
    elif args.command == "clear":
        cmd_clear(args, queue)
    elif args.command == "batch":
        cmd_batch(args, queue)
    elif args.command == "usage":
        cmd_usage(args)


if __name__ == "__main__":
    try:
        main()
    except ValidationError as e:
        logger.error(f"Validation error: {e}")
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except QueueFileError as e:
        logger.error(f"Queue file error: {e}")
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
    except QueueError as e:
        logger.error(f"Queue error: {e}")
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.exception("Unexpected error")
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
