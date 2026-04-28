"""Unit tests for ClaudeWorker functionality"""

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from claude_queue import (
    ClaudeWorker,
    TaskQueue,
    TaskStatus,
)


@pytest.fixture
def temp_queue(tmp_path):
    """Create a temporary queue for testing"""
    queue_file = tmp_path / "test_queue.json"
    return TaskQueue(queue_file)


@pytest.fixture
def temp_output_dir(tmp_path):
    """Create a temporary output directory"""
    output_dir = tmp_path / "outputs"
    return output_dir


class TestWorkerInitialization:
    """Test ClaudeWorker initialization"""

    def test_init_basic(self, temp_queue):
        """Test basic worker initialization"""
        worker = ClaudeWorker(temp_queue)

        assert worker.queue == temp_queue
        assert worker.base_retry_delay == 60
        assert worker.running is True
        assert worker.save_output is False
        assert worker.usage_checker is None

    def test_init_with_output_saving(self, temp_queue, temp_output_dir):
        """Test worker initialization with output saving enabled"""
        worker = ClaudeWorker(temp_queue, save_output=True, output_dir=temp_output_dir)

        assert worker.save_output is True
        assert worker.output_dir == temp_output_dir
        assert temp_output_dir.exists()  # Should be created

    def test_init_with_custom_retry_delay(self, temp_queue):
        """Test worker initialization with custom retry delay"""
        worker = ClaudeWorker(temp_queue, base_retry_delay=120)

        assert worker.base_retry_delay == 120


class TestRateLimitParsing:
    """Test rate limit information parsing"""

    def test_parse_retry_after_seconds(self, temp_queue):
        """Test parsing 'retry after X seconds' format"""
        worker = ClaudeWorker(temp_queue)

        stderr = "Error: Rate limit exceeded. Please retry after 300 seconds."
        info = worker.parse_rate_limit_info(stderr)

        assert info["retry_after"] == 300
        assert info["error_message"] == stderr

    def test_parse_wait_seconds(self, temp_queue):
        """Test parsing 'wait X seconds' format"""
        worker = ClaudeWorker(temp_queue)

        stderr = "Rate limit hit. Please wait 120 seconds before retrying."
        info = worker.parse_rate_limit_info(stderr)

        assert info["retry_after"] == 120

    def test_parse_retry_after_header(self, temp_queue):
        """Test parsing 'retry-after: X' format"""
        worker = ClaudeWorker(temp_queue)

        stderr = "HTTP 429: Too Many Requests\nretry-after: 180"
        info = worker.parse_rate_limit_info(stderr)

        assert info["retry_after"] == 180

    def test_parse_no_retry_info(self, temp_queue):
        """Test parsing error with no retry information"""
        worker = ClaudeWorker(temp_queue)

        stderr = "Some other error occurred"
        info = worker.parse_rate_limit_info(stderr)

        assert info["retry_after"] is None
        assert info["error_message"] == stderr

    def test_calculate_wait_time_with_retry_after(self, temp_queue):
        """Test wait time calculation with retry-after"""
        worker = ClaudeWorker(temp_queue)
        task = temp_queue.add_task("Test task")

        rate_limit_info = {"retry_after": 300, "error_message": "Rate limited"}
        wait_time = worker.calculate_wait_time(rate_limit_info, task)

        assert wait_time == 300

    def test_calculate_wait_time_without_retry_after(self, temp_queue):
        """Test wait time calculation without retry-after returns None"""
        worker = ClaudeWorker(temp_queue)
        task = temp_queue.add_task("Test task")

        rate_limit_info = {"retry_after": None, "error_message": "Unknown error"}
        wait_time = worker.calculate_wait_time(rate_limit_info, task)

        assert wait_time is None


class TestTaskExecution:
    """Test task execution with mocked subprocess"""

    def test_execute_task_success(self, temp_queue):
        """Test successful task execution"""
        worker = ClaudeWorker(temp_queue)
        task = temp_queue.add_task("Test prompt", session_name="test-session")

        # Mock successful subprocess execution
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Task completed successfully"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            success = worker.execute_task(task)

        assert success is True

        # Verify task status updated
        updated_task = temp_queue.get_all_tasks()[0]
        assert updated_task.status == TaskStatus.COMPLETED.value
        assert updated_task.completed_at is not None

    def test_execute_task_with_working_dir(self, temp_queue, tmp_path):
        """Test task execution with working directory"""
        working_dir = tmp_path / "project"
        working_dir.mkdir()

        worker = ClaudeWorker(temp_queue)
        task = temp_queue.add_task("Test prompt", working_dir=str(working_dir))

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Success"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            worker.execute_task(task)

        # Verify subprocess.run was called with correct cwd
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["cwd"] == working_dir

    def test_execute_task_rate_limited(self, temp_queue):
        """Test task execution with rate limit error"""
        worker = ClaudeWorker(temp_queue)
        task = temp_queue.add_task("Test prompt")

        # Mock rate limit error
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: Rate limit exceeded. Retry after 60 seconds."

        with patch("subprocess.run", return_value=mock_result):
            success = worker.execute_task(task)

        assert success is False

        # Verify task marked as rate limited
        updated_task = temp_queue.get_all_tasks()[0]
        assert updated_task.status == TaskStatus.RATE_LIMITED.value
        assert "Rate limit" in updated_task.last_error

    def test_execute_task_failure(self, temp_queue):
        """Test task execution with non-rate-limit failure"""
        worker = ClaudeWorker(temp_queue)
        task = temp_queue.add_task("Test prompt", max_attempts=3)

        # Mock general failure
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Some other error occurred"

        with patch("subprocess.run", return_value=mock_result):
            success = worker.execute_task(task)

        assert success is False

        # Verify task queued for retry (not failed yet, only 1 attempt)
        updated_task = temp_queue.get_all_tasks()[0]
        assert updated_task.status == TaskStatus.QUEUED.value
        assert updated_task.attempts == 1

    def test_execute_task_max_attempts_reached(self, temp_queue):
        """Test task failure after max attempts"""
        worker = ClaudeWorker(temp_queue)
        task = temp_queue.add_task("Test prompt", max_attempts=2)

        # Update task to be on last attempt
        temp_queue.update_task(task.id, attempts=1)
        task = temp_queue.get_all_tasks()[0]

        # Mock failure
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error occurred"

        with patch("subprocess.run", return_value=mock_result):
            success = worker.execute_task(task)

        assert success is False

        # Verify task marked as failed
        updated_task = temp_queue.get_all_tasks()[0]
        assert updated_task.status == TaskStatus.FAILED.value
        assert updated_task.attempts == 2

    def test_execute_task_timeout(self, temp_queue):
        """Test task execution timeout"""
        worker = ClaudeWorker(temp_queue)
        task = temp_queue.add_task("Test prompt")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", worker.task_timeout)):
            success = worker.execute_task(task)

        assert success is False

        # Verify task queued for retry
        updated_task = temp_queue.get_all_tasks()[0]
        assert updated_task.status == TaskStatus.QUEUED.value
        assert updated_task.last_error == "Execution timeout"


class TestOutputSaving:
    """Test task output saving functionality"""

    def test_save_output_disabled(self, temp_queue):
        """Test that output is not saved when disabled"""
        worker = ClaudeWorker(temp_queue, save_output=False)
        task = temp_queue.add_task("Test prompt")

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Task output"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            worker.execute_task(task)

        # Verify no output file created
        output_file = worker.output_dir / f"{task.id}.txt"
        assert not output_file.exists()

    def test_save_output_enabled(self, temp_queue, temp_output_dir):
        """Test that output is saved when enabled"""
        worker = ClaudeWorker(temp_queue, save_output=True, output_dir=temp_output_dir)
        task = temp_queue.add_task("Test prompt", session_name="test-session")

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Task output content"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            worker.execute_task(task)

        # Verify output file created
        output_file = temp_output_dir / f"{task.id}.txt"
        assert output_file.exists()

        # Verify output file content
        content = output_file.read_text()
        assert f"Task: {task.id}" in content
        assert "Session: test-session" in content
        assert "Completed:" in content
        assert "Task output content" in content

    def test_save_output_only_on_success(self, temp_queue, temp_output_dir):
        """Test that output is only saved for successful tasks"""
        worker = ClaudeWorker(temp_queue, save_output=True, output_dir=temp_output_dir)
        task = temp_queue.add_task("Test prompt")

        # Mock failure
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = "Some output"
        mock_result.stderr = "Error occurred"

        with patch("subprocess.run", return_value=mock_result):
            worker.execute_task(task)

        # Verify no output file created for failed task
        output_file = temp_output_dir / f"{task.id}.txt"
        assert not output_file.exists()

    def test_save_output_failure_handled_gracefully(self, temp_queue, temp_output_dir):
        """Test that output save failures don't break task completion"""
        worker = ClaudeWorker(temp_queue, save_output=True, output_dir=temp_output_dir)
        task = temp_queue.add_task("Test prompt")

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Task output"
        mock_result.stderr = ""

        # Mock file write failure - need to be more specific to only affect output file
        original_open = open

        def selective_open(file, *args, **kwargs):
            # Only fail for output files, let queue file operations succeed
            if temp_output_dir in Path(file).parents or Path(file).parent == temp_output_dir:
                raise OSError("Disk full")
            return original_open(file, *args, **kwargs)

        with patch("subprocess.run", return_value=mock_result):
            with patch("builtins.open", side_effect=selective_open):
                success = worker.execute_task(task)

        # Task should still be marked as completed despite output save failure
        assert success is True
        updated_task = temp_queue.get_all_tasks()[0]
        assert updated_task.status == TaskStatus.COMPLETED.value


class TestTimeoutResolution:
    """Test that execute_task resolves timeout: per-task overrides global default"""

    def test_uses_global_timeout_when_task_has_none(self, temp_queue):
        worker = ClaudeWorker(temp_queue, task_timeout=120)
        task = temp_queue.add_task("Test prompt")  # timeout=None by default

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            worker.execute_task(task)

        assert mock_run.call_args[1]["timeout"] == 120

    def test_per_task_timeout_overrides_global(self, temp_queue):
        worker = ClaudeWorker(temp_queue, task_timeout=3600)
        task = temp_queue.add_task("Test prompt", timeout=30)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            worker.execute_task(task)

        assert mock_run.call_args[1]["timeout"] == 30

    def test_per_task_timeout_overrides_global_stream_mode(self, temp_queue):
        worker = ClaudeWorker(temp_queue, task_timeout=3600, stream_output=True)
        task = temp_queue.add_task("Test prompt", timeout=45)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            worker.execute_task(task)

        assert mock_run.call_args[1]["timeout"] == 45


class TestCmdOutput:
    """Test the `output TASK_ID` command handler"""

    def test_prints_saved_output(self, tmp_path, capsys):
        from claude_queue import cmd_output

        output_dir = tmp_path / "outputs"
        output_dir.mkdir()
        (output_dir / "task-abc123.txt").write_text("Claude output here\n")

        args = Mock()
        args.task_id = "task-abc123"
        cmd_output(args, output_dir)

        assert capsys.readouterr().out == "Claude output here\n"

    def test_missing_file_exits_with_clear_error(self, tmp_path, capsys):
        from claude_queue import cmd_output

        output_dir = tmp_path / "outputs"
        output_dir.mkdir()

        args = Mock()
        args.task_id = "task-nonexistent"

        with pytest.raises(SystemExit) as exc_info:
            cmd_output(args, output_dir)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "task-nonexistent" in err
        assert "--save-output" in err


class TestWorkerRun:
    """Test worker.run() main event loop"""

    def test_run_exits_on_empty_queue(self, temp_queue):
        """Worker exits cleanly when queue is empty"""
        worker = ClaudeWorker(temp_queue)
        worker.run()  # Should return without error

    def test_run_resets_interrupted_running_tasks(self, temp_queue):
        """On startup, tasks stuck in RUNNING are reset to QUEUED"""
        task = temp_queue.add_task("Test task")
        temp_queue.update_task(task.id, status=TaskStatus.RUNNING.value)

        worker = ClaudeWorker(temp_queue)
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            with patch("time.sleep"):
                worker.run()

        updated = temp_queue.get_all_tasks()[0]
        assert updated.status == TaskStatus.COMPLETED.value  # Was reset to QUEUED, then executed

    def test_run_exponential_backoff_on_failure(self, temp_queue):
        """Failed tasks trigger exponential backoff delay"""
        temp_queue.add_task("Test task", max_attempts=2)

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Some error"

        sleep_calls = []
        worker = ClaudeWorker(temp_queue, base_retry_delay=60)
        with patch("subprocess.run", return_value=mock_result):
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                worker.run()

        # Should have slept with exponential backoff (60 * 2^0 = 60 for first failure)
        assert any(s == 60 for s in sleep_calls)

    def test_run_rate_limited_task_waits_retry_after(self, temp_queue):
        """Rate-limited task waits the retry-after duration before continuing"""
        temp_queue.add_task("Test task", max_attempts=2)

        # First call returns rate limit error with retry-after
        rate_limit_result = Mock()
        rate_limit_result.returncode = 1
        rate_limit_result.stdout = ""
        rate_limit_result.stderr = "Rate limit exceeded. Retry after 120 seconds."

        # Second call succeeds
        success_result = Mock()
        success_result.returncode = 0
        success_result.stdout = "Done"
        success_result.stderr = ""

        sleep_calls = []
        worker = ClaudeWorker(temp_queue)
        with patch("subprocess.run", side_effect=[rate_limit_result, success_result]):
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                worker.run()

        assert 120 in sleep_calls
        updated = temp_queue.get_all_tasks()[0]
        assert updated.status == TaskStatus.COMPLETED.value


class TestCheckAndWaitForLimits:
    """Test check_and_wait_for_limits sleep behavior"""

    def _make_checker(
        self, utilization_5h=50.0, utilization_7d=50.0, resets_at="2099-01-01T12:00:00.000000Z"
    ):
        """Build a mocked ClaudeUsageChecker"""
        checker = Mock()
        parsed = {
            "five_hour": {
                "utilization": utilization_5h,
                "utilization_percent": f"{utilization_5h:.1f}%",
                "resets_at": resets_at,
                "resets_at_local": "2099-01-01 12:00:00 UTC",
                "time_until_reset": "1h 0m",
            },
            "seven_day": {
                "utilization": utilization_7d,
                "utilization_percent": f"{utilization_7d:.1f}%",
                "resets_at": resets_at,
                "resets_at_local": "2099-01-01 12:00:00 UTC",
                "time_until_reset": "1h 0m",
            },
        }
        checker.is_limit_exceeded.return_value = (False, None, None)
        return checker, parsed

    def test_no_sleep_when_limit_ok(self, temp_queue):
        """Worker does not sleep when usage is below threshold"""
        checker, _ = self._make_checker(utilization_5h=50.0)
        worker = ClaudeWorker(temp_queue, usage_checker=checker)

        with patch("time.sleep") as mock_sleep:
            worker.check_and_wait_for_limits()

        mock_sleep.assert_not_called()

    def test_sleeps_until_reset_when_limit_exceeded(self, temp_queue):
        """Worker sleeps until reset time when limit is exceeded"""
        from datetime import datetime, timedelta, timezone

        future_reset = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%S.000000Z"
        )
        checker, parsed = self._make_checker(utilization_5h=96.0, resets_at=future_reset)
        checker.is_limit_exceeded.return_value = (True, "5-hour limit at 96.0%", parsed)

        worker = ClaudeWorker(temp_queue, usage_checker=checker)
        sleep_calls = []
        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            worker.check_and_wait_for_limits()

        assert len(sleep_calls) == 1
        # Should sleep roughly 3600 seconds (+10 buffer), within a reasonable range
        assert 3500 < sleep_calls[0] < 3700

    def test_fallback_sleep_when_no_reset_time(self, temp_queue):
        """Worker sleeps 300s fallback when reset time is unavailable"""
        checker, parsed = self._make_checker(utilization_5h=96.0)
        parsed["five_hour"]["resets_at"] = None
        # First call: limit exceeded; second call: limits OK so loop exits
        checker.is_limit_exceeded.side_effect = [
            (True, "5-hour limit at 96.0%", parsed),
            (False, None, None),
        ]

        worker = ClaudeWorker(temp_queue, usage_checker=checker)
        sleep_calls = []
        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            worker.check_and_wait_for_limits()

        assert 300 in sleep_calls

    def test_returns_without_blocking_on_fetch_error(self, temp_queue):
        """Worker does not block if limit check raises an exception"""
        checker = Mock()
        checker.is_limit_exceeded.side_effect = Exception("Network error")

        worker = ClaudeWorker(temp_queue, usage_checker=checker)
        with patch("time.sleep") as mock_sleep:
            worker.check_and_wait_for_limits()  # Should return, not raise

        mock_sleep.assert_not_called()
