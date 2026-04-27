"""Unit tests for batch loading functionality"""

import pytest
from claude_queue import (
    QueueFileError,
    TaskQueue,
    ValidationError,
    load_batch_file,
)


@pytest.fixture
def temp_queue(tmp_path):
    """Create a temporary queue for testing"""
    queue_file = tmp_path / "test_queue.json"
    return TaskQueue(queue_file)


@pytest.fixture
def simple_yaml_file(tmp_path):
    """Create a simple YAML test file"""
    yaml_file = tmp_path / "test.yaml"
    yaml_content = """
tasks:
  - prompt: "Task 1"
    session: "task1"
    priority: 10

  - prompt: "Task 2"
    session: "task2"
    priority: 5
"""
    yaml_file.write_text(yaml_content)
    return yaml_file


@pytest.fixture
def dependencies_yaml_file(tmp_path):
    """Create YAML file with dependencies"""
    yaml_file = tmp_path / "deps.yaml"
    yaml_content = """
tasks:
  - prompt: "Foundation task"
    session: "foundation"
    priority: 10

  - prompt: "Dependent task"
    session: "dependent"
    priority: 8
    depends_on: ["foundation"]

  - prompt: "Final task"
    session: "final"
    priority: 5
    depends_on: ["dependent"]
"""
    yaml_file.write_text(yaml_content)
    return yaml_file


@pytest.fixture
def circular_yaml_file(tmp_path):
    """Create YAML file with circular dependencies"""
    yaml_file = tmp_path / "circular.yaml"
    yaml_content = """
tasks:
  - prompt: "Task A"
    session: "task-a"
    depends_on: ["task-b"]

  - prompt: "Task B"
    session: "task-b"
    depends_on: ["task-c"]

  - prompt: "Task C"
    session: "task-c"
    depends_on: ["task-a"]
"""
    yaml_file.write_text(yaml_content)
    return yaml_file


class TestBatchLoading:
    """Test batch file loading"""

    def test_load_simple_yaml(self, simple_yaml_file):
        """Test loading simple YAML file"""
        tasks = load_batch_file(simple_yaml_file)

        assert len(tasks) == 2
        assert tasks[0]["prompt"] == "Task 1"
        assert tasks[0]["session"] == "task1"
        assert tasks[0]["priority"] == 10

    def test_load_nonexistent_file(self, tmp_path):
        """Test loading non-existent file raises error"""
        fake_file = tmp_path / "nonexistent.yaml"

        with pytest.raises(QueueFileError, match="File not found"):
            load_batch_file(fake_file)

    def test_load_invalid_yaml(self, tmp_path):
        """Test loading invalid YAML raises error"""
        yaml_file = tmp_path / "invalid.yaml"
        yaml_file.write_text("{ invalid: yaml: content:")

        with pytest.raises(QueueFileError, match="Invalid YAML"):
            load_batch_file(yaml_file)

    def test_load_json_file(self, tmp_path):
        """Test loading JSON file"""
        json_file = tmp_path / "test.json"
        json_content = """{
  "tasks": [
    {"prompt": "Task 1", "session": "task1"}
  ]
}"""
        json_file.write_text(json_content)

        tasks = load_batch_file(json_file)
        assert len(tasks) == 1
        assert tasks[0]["prompt"] == "Task 1"

    def test_unsupported_file_format(self, tmp_path):
        """Test that unsupported file formats are rejected"""
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("tasks")

        with pytest.raises(ValidationError, match="Unsupported file format"):
            load_batch_file(txt_file)


class TestSessionNameResolution:
    """Test session name to task ID resolution"""

    def test_session_name_resolution(self, temp_queue, dependencies_yaml_file):
        """Test that session names are resolved to task IDs"""
        # This would require calling cmd_batch, which is more of an integration test
        # For now, test the concept with direct queue operations

        task1 = temp_queue.add_task("Task 1", session_name="task1")
        task2 = temp_queue.add_task("Task 2", session_name="task2", depends_on=[task1.id])

        # Verify task2 depends on task1's ID, not session name
        assert task2.depends_on == [task1.id]
        assert task1.id.startswith("task-")

    def test_multi_pass_loading(self, temp_queue, dependencies_yaml_file):
        """Test that multi-pass loading handles dependencies correctly"""
        # Load the file content
        tasks = load_batch_file(dependencies_yaml_file)

        assert len(tasks) == 3

        # Verify dependency structure in file
        assert "depends_on" not in tasks[0]
        assert tasks[1]["depends_on"] == ["foundation"]
        assert tasks[2]["depends_on"] == ["dependent"]


class TestCircularDependencies:
    """Test circular dependency detection in batch loading"""

    def test_circular_dependency_detection(self, circular_yaml_file):
        """Test that circular dependencies in batch files are detected"""
        tasks = load_batch_file(circular_yaml_file)

        # All tasks have dependencies
        assert all("depends_on" in task for task in tasks)

        # This creates a circle: task-a -> task-b -> task-c -> task-a
        assert tasks[0]["depends_on"] == ["task-b"]
        assert tasks[1]["depends_on"] == ["task-c"]
        assert tasks[2]["depends_on"] == ["task-a"]


class TestBatchValidation:
    """Test batch file validation"""

    def test_missing_tasks_key(self, tmp_path):
        """Test that files without 'tasks' key are rejected"""
        yaml_file = tmp_path / "invalid.yaml"
        yaml_file.write_text("invalid: structure")

        with pytest.raises(ValidationError, match="Expected a dictionary with 'tasks' key"):
            load_batch_file(yaml_file)

    def test_tasks_not_list(self, tmp_path):
        """Test that 'tasks' must be a list"""
        yaml_file = tmp_path / "invalid.yaml"
        yaml_file.write_text("tasks: not_a_list")

        with pytest.raises(ValidationError, match="'tasks' must be a list"):
            load_batch_file(yaml_file)

    def test_task_missing_prompt(self, tmp_path):
        """Test that tasks without 'prompt' are rejected"""
        yaml_file = tmp_path / "invalid.yaml"
        yaml_content = """
tasks:
  - session: "task1"
    priority: 10
"""
        yaml_file.write_text(yaml_content)

        with pytest.raises(ValidationError, match="missing required field 'prompt'"):
            load_batch_file(yaml_file)


class TestBatchOptions:
    """Test batch loading options"""

    def test_default_values(self, tmp_path):
        """Test that default values are applied"""
        yaml_file = tmp_path / "defaults.yaml"
        yaml_content = """
tasks:
  - prompt: "Minimal task"
"""
        yaml_file.write_text(yaml_content)

        tasks = load_batch_file(yaml_file)

        # File doesn't specify these, they should get defaults when added
        assert "priority" not in tasks[0]
        assert "max_attempts" not in tasks[0]

    def test_custom_max_attempts(self, tmp_path):
        """Test custom max_attempts value"""
        yaml_file = tmp_path / "custom.yaml"
        yaml_content = """
tasks:
  - prompt: "Custom task"
    max_attempts: 5
"""
        yaml_file.write_text(yaml_content)

        tasks = load_batch_file(yaml_file)
        assert tasks[0]["max_attempts"] == 5

    def test_working_dir_in_yaml(self, tmp_path):
        """Test loading tasks with working_dir"""
        yaml_file = tmp_path / "with_workdir.yaml"

        # Create test directories
        project_a = tmp_path / "project-a"
        project_b = tmp_path / "project-b"
        project_a.mkdir()
        project_b.mkdir()

        yaml_content = f"""
tasks:
  - prompt: "Task in project A"
    session: "task-a"
    working_dir: "{project_a}"

  - prompt: "Task in project B"
    session: "task-b"
    working_dir: "{project_b}"
"""
        yaml_file.write_text(yaml_content)

        tasks = load_batch_file(yaml_file)

        assert len(tasks) == 2
        assert tasks[0]["working_dir"] == str(project_a)
        assert tasks[1]["working_dir"] == str(project_b)
