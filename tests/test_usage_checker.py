"""Unit tests for ClaudeUsageChecker functionality"""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

# Import claude-queue.py as a module
spec = importlib.util.spec_from_file_location(
    "claude_queue",
    Path(__file__).parent.parent / "claude-queue.py"
)
claude_queue = importlib.util.module_from_spec(spec)
sys.modules["claude_queue"] = claude_queue
spec.loader.exec_module(claude_queue)

# Import needed classes
from claude_queue import ClaudeUsageChecker


@pytest.fixture
def mock_usage_data():
    """Sample usage data from Claude API"""
    return {
        "five_hour": {
            "utilization": 45.5,
            "resets_at": "2026-01-04T15:30:00.000000Z"
        },
        "seven_day": {
            "utilization": 72.3,
            "resets_at": "2026-01-08T10:00:00.000000Z"
        }
    }


@pytest.fixture
def mock_org_data():
    """Sample organization data from Claude API"""
    return [
        {
            "uuid": "47fef11f-3b54-4aa9-94a9-615c50ebf881",
            "name": "Test Organization"
        }
    ]


class TestUsageCheckerInit:
    """Test ClaudeUsageChecker initialization"""

    def test_init_with_session_key(self):
        """Test initialization with session key"""
        checker = ClaudeUsageChecker(
            session_key="test-session-key",
            org_id="test-org-id"
        )
        assert checker.session_key == "test-session-key"
        assert checker.org_id == "test-org-id"

    @patch.dict('os.environ', {'CLAUDE_SESSION_KEY': 'env-session-key', 'CLAUDE_ORG_ID': 'env-org-id'})
    def test_init_from_env(self):
        """Test initialization from environment variables"""
        checker = ClaudeUsageChecker()
        assert checker.session_key == "env-session-key"
        assert checker.org_id == "env-org-id"

    def test_init_with_api_url(self):
        """Test initialization with custom API URL"""
        checker = ClaudeUsageChecker(
            session_key="test-key",
            api_url="https://custom-api.example.com/usage"
        )
        assert checker.usage_api_url == "https://custom-api.example.com/usage"

    @patch('requests.Session')
    def test_auto_detect_org_id(self, mock_session_class, mock_org_data):
        """Test automatic organization ID detection"""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_org_data
        mock_session.get.return_value = mock_response
        mock_session_class.return_value = mock_session

        checker = ClaudeUsageChecker(session_key="test-key")

        assert checker.org_id == "47fef11f-3b54-4aa9-94a9-615c50ebf881"
        mock_session.get.assert_called_once()

    @patch('requests.Session')
    def test_auto_detect_org_id_failure(self, mock_session_class):
        """Test organization ID detection failure"""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("401 Unauthorized")
        mock_session.get.return_value = mock_response
        mock_session_class.return_value = mock_session

        with pytest.raises(ValueError, match="Failed to auto-detect organization ID"):
            ClaudeUsageChecker(session_key="test-key")


class TestFetchUsage:
    """Test usage data fetching"""

    def test_fetch_usage_success(self, mock_usage_data):
        """Test successful usage data fetch"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_usage_data
            mock_session.get.return_value = mock_response
            mock_session_class.return_value = mock_session

            checker = ClaudeUsageChecker(
                session_key="test-key",
                org_id="test-org-id"
            )
            result = checker.fetch_usage()

            assert result == mock_usage_data

    def test_fetch_usage_unauthorized(self):
        """Test fetch with invalid session key"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_response = Mock()
            mock_response.status_code = 401
            http_error = requests.exceptions.HTTPError()
            http_error.response = Mock(status_code=401)
            mock_response.raise_for_status.side_effect = http_error
            mock_session.get.return_value = mock_response
            mock_session_class.return_value = mock_session

            checker = ClaudeUsageChecker(
                session_key="invalid-key",
                org_id="test-org-id"
            )

            with pytest.raises(ValueError, match="Authentication failed"):
                checker.fetch_usage()


class TestParseUsage:
    """Test usage data parsing"""

    def test_parse_usage_success(self, mock_usage_data):
        """Test successful usage parsing"""
        checker = ClaudeUsageChecker(
            session_key="test-key",
            org_id="test-org-id"
        )
        result = checker.parse_usage(mock_usage_data)

        # Check the nested structure
        assert 'five_hour' in result
        assert 'seven_day' in result
        assert result['five_hour']['utilization'] == 45.5
        assert result['seven_day']['utilization'] == 72.3
        assert 'time_until_reset' in result['five_hour']
        assert 'time_until_reset' in result['seven_day']

    def test_parse_usage_empty_data(self):
        """Test parsing with empty data"""
        checker = ClaudeUsageChecker(
            session_key="test-key",
            org_id="test-org-id"
        )

        # Empty data should return empty result (no exception)
        result = checker.parse_usage({})
        assert 'raw' in result


class TestLimitChecking:
    """Test usage limit checking"""

    def test_is_limit_exceeded_below_threshold(self):
        """Test when usage is below threshold"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_response = Mock()
            mock_response.json.return_value = {
                "five_hour": {
                    "utilization": 50.0,
                    "resets_at": "2026-01-04T15:30:00.000000Z"
                },
                "seven_day": {
                    "utilization": 60.0,
                    "resets_at": "2026-01-08T10:00:00.000000Z"
                }
            }
            mock_session.get.return_value = mock_response
            mock_session_class.return_value = mock_session

            checker = ClaudeUsageChecker(
                session_key="test-key",
                org_id="test-org-id"
            )

            exceeded, reason = checker.is_limit_exceeded(threshold=95.0)

            assert exceeded is False
            assert reason is None

    def test_is_limit_exceeded_session_limit(self):
        """Test when session limit is exceeded"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_response = Mock()
            mock_response.json.return_value = {
                "five_hour": {
                    "utilization": 96.0,
                    "resets_at": "2026-01-04T15:30:00.000000Z"
                },
                "seven_day": {
                    "utilization": 60.0,
                    "resets_at": "2026-01-08T10:00:00.000000Z"
                }
            }
            mock_session.get.return_value = mock_response
            mock_session_class.return_value = mock_session

            checker = ClaudeUsageChecker(
                session_key="test-key",
                org_id="test-org-id"
            )

            exceeded, reason = checker.is_limit_exceeded(threshold=95.0)

            assert exceeded is True
            assert "5-hour" in reason

    def test_is_limit_exceeded_weekly_limit(self):
        """Test when weekly limit is exceeded"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_response = Mock()
            mock_response.json.return_value = {
                "five_hour": {
                    "utilization": 50.0,
                    "resets_at": "2026-01-04T15:30:00.000000Z"
                },
                "seven_day": {
                    "utilization": 97.0,
                    "resets_at": "2026-01-08T10:00:00.000000Z"
                }
            }
            mock_session.get.return_value = mock_response
            mock_session_class.return_value = mock_session

            checker = ClaudeUsageChecker(
                session_key="test-key",
                org_id="test-org-id"
            )

            exceeded, reason = checker.is_limit_exceeded(threshold=95.0)

            assert exceeded is True
            assert "7-day" in reason

    def test_is_limit_exceeded_both_limits(self):
        """Test when both limits are exceeded"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_response = Mock()
            mock_response.json.return_value = {
                "five_hour": {
                    "utilization": 96.0,
                    "resets_at": "2026-01-04T15:30:00.000000Z"
                },
                "seven_day": {
                    "utilization": 98.0,
                    "resets_at": "2026-01-08T10:00:00.000000Z"
                }
            }
            mock_session.get.return_value = mock_response
            mock_session_class.return_value = mock_session

            checker = ClaudeUsageChecker(
                session_key="test-key",
                org_id="test-org-id"
            )

            exceeded, reason = checker.is_limit_exceeded(threshold=95.0)

            assert exceeded is True
            # Should return first exceeded limit (5-hour)
            assert "5-hour" in reason

    def test_is_limit_exceeded_custom_threshold(self):
        """Test limit checking with custom threshold"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()

            # Mock for first call (95% threshold)
            mock_response1 = Mock()
            mock_response1.json.return_value = {
                "five_hour": {
                    "utilization": 85.0,
                    "resets_at": "2026-01-04T15:30:00.000000Z"
                },
                "seven_day": {
                    "utilization": 60.0,
                    "resets_at": "2026-01-08T10:00:00.000000Z"
                }
            }

            # Mock for second call (80% threshold)
            mock_response2 = Mock()
            mock_response2.json.return_value = {
                "five_hour": {
                    "utilization": 85.0,
                    "resets_at": "2026-01-04T15:30:00.000000Z"
                },
                "seven_day": {
                    "utilization": 60.0,
                    "resets_at": "2026-01-08T10:00:00.000000Z"
                }
            }

            mock_session.get.side_effect = [mock_response1, mock_response2]
            mock_session_class.return_value = mock_session

            checker = ClaudeUsageChecker(
                session_key="test-key",
                org_id="test-org-id"
            )

            # Should not exceed at 95% threshold
            exceeded, _ = checker.is_limit_exceeded(threshold=95.0)
            assert exceeded is False

            # Should exceed at 80% threshold
            exceeded, reason = checker.is_limit_exceeded(threshold=80.0)
            assert exceeded is True
            assert "5-hour" in reason


class TestUsageDisplay:
    """Test usage display functionality"""

    def test_check_usage(self, mock_usage_data):
        """Test check_usage method"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_response = Mock()
            mock_response.json.return_value = mock_usage_data
            mock_session.get.return_value = mock_response
            mock_session_class.return_value = mock_session

            with patch('builtins.print'):
                checker = ClaudeUsageChecker(
                    session_key="test-key",
                    org_id="test-org-id"
                )

                result = checker.check_usage(json_output=False)

                assert 'five_hour' in result
                assert 'seven_day' in result


class TestTimeUntilReset:
    """Test time until reset calculations"""

    def test_time_until_hours(self):
        """Test formatting hours until reset"""
        from datetime import timedelta

        checker = ClaudeUsageChecker(
            session_key="test-key",
            org_id="test-org-id"
        )

        # Create a timestamp 3 hours in the future
        future_time = datetime.now(timezone.utc) + timedelta(hours=3)
        future_timestamp = future_time.strftime("%Y-%m-%dT%H:%M:%S.000000Z")

        result = checker._time_until(future_timestamp)

        # Should contain time information
        assert result is not None
        assert "h" in result or "m" in result

    def test_time_until_already_reset(self):
        """Test time until for past timestamp"""
        checker = ClaudeUsageChecker(
            session_key="test-key",
            org_id="test-org-id"
        )

        # Use a past timestamp
        past_timestamp = "2020-01-01T12:00:00.000000Z"

        result = checker._time_until(past_timestamp)

        assert result == "Already reset"

    def test_parse_timestamp(self):
        """Test timestamp parsing"""
        checker = ClaudeUsageChecker(
            session_key="test-key",
            org_id="test-org-id"
        )

        timestamp = "2026-01-04T15:30:00.000000Z"
        result = checker._parse_timestamp(timestamp)

        # Should return a formatted string
        assert result is not None
        assert isinstance(result, str)
