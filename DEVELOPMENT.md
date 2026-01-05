# Development Guide

## Testing

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov --cov-report=html
```

**Test files:**
- `tests/test_queue.py` - Core functionality tests
- `tests/test_batch.py` - Batch loading tests
- `tests/test_usage_checker.py` - Usage limit monitoring tests
- `tests/*.yaml` - Integration test files

## Project Structure

```
claude-queue/
├── claude-queue.py          # Main application
├── tests/                   # Test suite
│   ├── test_queue.py        # Core queue tests
│   ├── test_batch.py        # Batch loading tests
│   ├── test_usage_checker.py # Usage monitoring tests
│   └── *.yaml               # Test fixtures
├── pyproject.toml           # Dependencies and metadata
└── README.md                # User documentation
```

## Running Tests

The test suite uses pytest and includes comprehensive coverage of:
- Task queue operations
- Priority scheduling
- Dependency resolution
- Batch file loading
- Usage limit monitoring
- Error handling and validation

All tests use temporary files and mocking to avoid side effects.