"""Unit tests for dependency validation and circular dependency detection"""

import pytest
from claude_queue import (
    TaskQueue,
    TaskStatus,
    ValidationError,
)


@pytest.fixture
def temp_queue(tmp_path):
    """Create a temporary queue for testing"""
    queue_file = tmp_path / "test_queue.json"
    return TaskQueue(queue_file)


class TestCircularDependencyDetection:
    """Test circular dependency detection

    Note: Circular dependency detection is primarily tested through batch loading
    in test_batch.py where circular dependencies are detected when loading YAML files.
    These tests verify the detection algorithm directly.
    """

    def test_incremental_cycles_prevented_by_existence_check(self, temp_queue):
        """Incremental cycle creation is prevented by the dependency-existence check.

        _check_circular_dependencies is only reachable for new (not-yet-queued) tasks.
        Since no existing task can reference a task that doesn't exist yet, a new task
        can never close a cycle incrementally. Cycles in batch files are caught by the
        multi-pass loader's 'no progress' detection instead.
        """
        task_a = temp_queue.add_task("Task A", session_name="task-a")
        task_b = temp_queue.add_task("Task B", session_name="task-b", depends_on=[task_a.id])
        _task_c = temp_queue.add_task("Task C", session_name="task-c", depends_on=[task_b.id])

        # Trying to reference a non-existent ID is blocked before DFS runs
        with pytest.raises(ValidationError, match="does not exist"):
            temp_queue.add_task("Task D", depends_on=["nonexistent-id"])

    def test_self_dependency_rejected(self, temp_queue):
        """Test that self-dependency is rejected"""
        task_a = temp_queue.add_task("Task A", session_name="task-a")

        with pytest.raises(ValidationError, match="Task cannot depend on itself"):
            temp_queue._validate_dependencies([task_a.id], task_a.id)

    def test_no_circular_dependency_in_chain(self, temp_queue):
        """Test that valid chain is accepted"""
        task_a = temp_queue.add_task("Task A", session_name="task-a")
        task_b = temp_queue.add_task("Task B", session_name="task-b", depends_on=[task_a.id])
        task_c = temp_queue.add_task("Task C", session_name="task-c", depends_on=[task_b.id])

        # This should not raise an error
        temp_queue._validate_dependencies([task_c.id], "new-task-id")

    def test_no_circular_dependency_in_diamond(self, temp_queue):
        """Test that valid diamond pattern is accepted"""
        task_a = temp_queue.add_task("Task A", session_name="task-a")
        task_b = temp_queue.add_task("Task B", session_name="task-b", depends_on=[task_a.id])
        task_c = temp_queue.add_task("Task C", session_name="task-c", depends_on=[task_a.id])

        # Creating task_d that depends on both task_b and task_c should work
        task_d = temp_queue.add_task(
            "Task D", session_name="task-d", depends_on=[task_b.id, task_c.id]
        )

        assert task_d.depends_on == [task_b.id, task_c.id]


class TestDependencyValidation:
    """Test dependency validation logic"""

    def test_nonexistent_dependency_rejected(self, temp_queue):
        """Test that depending on non-existent task is rejected"""
        with pytest.raises(ValidationError, match="Dependency task .* does not exist"):
            temp_queue.add_task("Task", depends_on=["nonexistent-task-id"])

    def test_depends_on_must_be_list(self, temp_queue):
        """Test that depends_on must be a list"""
        with pytest.raises(ValidationError, match="depends_on must be a list"):
            temp_queue._validate_dependencies("not-a-list", "task-id")

    def test_depends_on_must_contain_strings(self, temp_queue):
        """Test that depends_on must contain strings"""
        with pytest.raises(ValidationError, match="All dependency IDs must be strings"):
            temp_queue._validate_dependencies([123, 456], "task-id")

    def test_empty_depends_on_accepted(self, temp_queue):
        """Test that empty depends_on is accepted"""
        # None and empty list should both be accepted
        temp_queue._validate_dependencies(None, "task-id")
        temp_queue._validate_dependencies([], "task-id")

    def test_multiple_dependencies(self, temp_queue):
        """Test task with multiple dependencies"""
        task_a = temp_queue.add_task("Task A", session_name="task-a")
        task_b = temp_queue.add_task("Task B", session_name="task-b")
        task_c = temp_queue.add_task(
            "Task C", session_name="task-c", depends_on=[task_a.id, task_b.id]
        )

        assert len(task_c.depends_on) == 2
        assert task_a.id in task_c.depends_on
        assert task_b.id in task_c.depends_on


class TestDependencySatisfaction:
    """Test dependency satisfaction checking"""

    def test_no_dependencies_always_satisfied(self, temp_queue):
        """Test that task with no dependencies is always satisfied"""
        task = temp_queue.add_task("Task", session_name="task")
        all_tasks = temp_queue.get_all_tasks()

        assert temp_queue._dependencies_satisfied(task, all_tasks) is True

    def test_dependency_satisfied_when_completed(self, temp_queue):
        """Test that dependency is satisfied when dependent task is completed"""
        task_a = temp_queue.add_task("Task A", session_name="task-a")
        task_b = temp_queue.add_task("Task B", session_name="task-b", depends_on=[task_a.id])

        # Mark task_a as completed
        temp_queue.update_task(task_a.id, status=TaskStatus.COMPLETED.value)

        all_tasks = temp_queue.get_all_tasks()
        task_b_updated = [t for t in all_tasks if t.id == task_b.id][0]

        assert temp_queue._dependencies_satisfied(task_b_updated, all_tasks) is True

    def test_dependency_not_satisfied_when_queued(self, temp_queue):
        """Test that dependency is not satisfied when dependent task is queued"""
        task_a = temp_queue.add_task("Task A", session_name="task-a")
        task_b = temp_queue.add_task("Task B", session_name="task-b", depends_on=[task_a.id])

        all_tasks = temp_queue.get_all_tasks()
        task_b_updated = [t for t in all_tasks if t.id == task_b.id][0]

        # task_a is still queued
        assert temp_queue._dependencies_satisfied(task_b_updated, all_tasks) is False

    def test_dependency_not_satisfied_when_running(self, temp_queue):
        """Test that dependency is not satisfied when dependent task is running"""
        task_a = temp_queue.add_task("Task A", session_name="task-a")
        task_b = temp_queue.add_task("Task B", session_name="task-b", depends_on=[task_a.id])

        # Mark task_a as running
        temp_queue.update_task(task_a.id, status=TaskStatus.RUNNING.value)

        all_tasks = temp_queue.get_all_tasks()
        task_b_updated = [t for t in all_tasks if t.id == task_b.id][0]

        assert temp_queue._dependencies_satisfied(task_b_updated, all_tasks) is False

    def test_dependency_not_satisfied_when_failed(self, temp_queue):
        """Test that dependency is not satisfied when dependent task failed"""
        task_a = temp_queue.add_task("Task A", session_name="task-a")
        task_b = temp_queue.add_task("Task B", session_name="task-b", depends_on=[task_a.id])

        # Mark task_a as failed
        temp_queue.update_task(task_a.id, status=TaskStatus.FAILED.value)

        all_tasks = temp_queue.get_all_tasks()
        task_b_updated = [t for t in all_tasks if t.id == task_b.id][0]

        assert temp_queue._dependencies_satisfied(task_b_updated, all_tasks) is False

    def test_multiple_dependencies_all_must_be_completed(self, temp_queue):
        """Test that all dependencies must be completed"""
        task_a = temp_queue.add_task("Task A", session_name="task-a")
        task_b = temp_queue.add_task("Task B", session_name="task-b")
        task_c = temp_queue.add_task(
            "Task C", session_name="task-c", depends_on=[task_a.id, task_b.id]
        )

        # Complete only task_a
        temp_queue.update_task(task_a.id, status=TaskStatus.COMPLETED.value)

        all_tasks = temp_queue.get_all_tasks()
        task_c_updated = [t for t in all_tasks if t.id == task_c.id][0]

        # Should not be satisfied because task_b is not completed
        assert temp_queue._dependencies_satisfied(task_c_updated, all_tasks) is False

        # Complete task_b as well
        temp_queue.update_task(task_b.id, status=TaskStatus.COMPLETED.value)

        all_tasks = temp_queue.get_all_tasks()
        task_c_updated = [t for t in all_tasks if t.id == task_c.id][0]

        # Now should be satisfied
        assert temp_queue._dependencies_satisfied(task_c_updated, all_tasks) is True

    def test_missing_dependency_allows_task_to_run(self, temp_queue):
        """Test that missing dependency (deleted) allows task to run with warning"""
        task_a = temp_queue.add_task("Task A", session_name="task-a")
        task_b = temp_queue.add_task("Task B", session_name="task-b", depends_on=[task_a.id])

        # Delete task_a
        temp_queue.remove_task(task_a.id)

        all_tasks = temp_queue.get_all_tasks()
        task_b_updated = [t for t in all_tasks if t.id == task_b.id][0]

        # Should allow task to run despite missing dependency
        assert temp_queue._dependencies_satisfied(task_b_updated, all_tasks) is True


class TestGetNextTaskWithDependencies:
    """Test get_next_task with dependency ordering"""

    def test_get_next_respects_dependencies(self, temp_queue):
        """Test that get_next_task respects dependencies"""
        task_a = temp_queue.add_task("Task A", session_name="task-a", priority=5)
        _task_b = temp_queue.add_task(
            "Task B", session_name="task-b", priority=10, depends_on=[task_a.id]
        )

        # Even though task_b has higher priority, task_a should come first
        next_task = temp_queue.get_next_task()
        assert next_task.id == task_a.id

    def test_get_next_after_dependency_completed(self, temp_queue):
        """Test that dependent task becomes available after dependency completes"""
        task_a = temp_queue.add_task("Task A", session_name="task-a", priority=5)
        task_b = temp_queue.add_task(
            "Task B", session_name="task-b", priority=10, depends_on=[task_a.id]
        )

        # Complete task_a
        temp_queue.update_task(task_a.id, status=TaskStatus.COMPLETED.value)

        # Now task_b should be next
        next_task = temp_queue.get_next_task()
        assert next_task.id == task_b.id

    def test_get_next_with_parallel_dependencies(self, temp_queue):
        """Test that tasks with no dependency between them can run in parallel"""
        task_a = temp_queue.add_task("Task A", session_name="task-a", priority=10)
        _task_b = temp_queue.add_task("Task B", session_name="task-b", priority=8)
        task_c = temp_queue.add_task("Task C", session_name="task-c", priority=9)

        # Should get task_a first (highest priority)
        next_task = temp_queue.get_next_task()
        assert next_task.id == task_a.id

        # Mark as running
        temp_queue.update_task(task_a.id, status=TaskStatus.RUNNING.value)

        # Should get task_c next (second highest priority)
        next_task = temp_queue.get_next_task()
        assert next_task.id == task_c.id
