import sys
from unittest.mock import patch, MagicMock
import pytest
from global_loop_engine import loop_engine

def test_complexity_scorer_node():
    state = {"task_prompt": "Refactor auth module"}
    result = loop_engine.complexity_scorer_node(state)
    assert "complexity_score" in result
    assert 1 <= result["complexity_score"] <= 10

def test_cost_estimator_node():
    state = {"complexity_score": 4}
    result = loop_engine.cost_estimator_node(state)
    assert result["estimated_cost"] == 3.00  # 4 * 0.75

def test_sanitize_node_safe():
    state = {"planned_commands": ["pytest", "echo 'hello'"]}
    result = loop_engine.sanitize_node(state)
    assert result == {}  # Empty dict means passed safety check

def test_sanitize_node_dangerous():
    state = {"planned_commands": ["rm -rf /", "git push --force origin"]}
    result = loop_engine.sanitize_node(state)
    assert result["is_constitutional"] is False
    assert any("BLOCKED" in msg for msg in result["critic_feedback"])

def test_test_integrity_guard_node_no_baseline():
    state = {}
    result = loop_engine.test_integrity_guard_node(state)
    assert "test_baseline_signature" in result

def test_test_integrity_guard_node_match():
    # Use a dummy signature
    state = {"test_baseline_signature": "dummy_hash"}
    with patch("global_loop_engine.loop_engine.compute_test_signature", return_value="dummy_hash"):
        result = loop_engine.test_integrity_guard_node(state)
        assert result == {}

def test_test_integrity_guard_node_mismatch():
    state = {"test_baseline_signature": "baseline_hash"}
    with patch("global_loop_engine.loop_engine.compute_test_signature", return_value="current_hash"):
        result = loop_engine.test_integrity_guard_node(state)
        assert result["is_constitutional"] is False
        assert result["fatal_violation"] is True

def test_human_approval_node_non_interactive():
    state = {"retry_count": 2, "critic_feedback": ["Tests failed"]}
    with patch("sys.stdin.isatty", return_value=False):
        result = loop_engine.human_approval_node(state)
        assert result["is_constitutional"] is False
        assert result["retry_count"] == 99

def test_human_approval_node_interactive_continue():
    state = {"retry_count": 2, "critic_feedback": ["Tests failed"]}
    with patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value="y"):
        result = loop_engine.human_approval_node(state)
        assert result["retry_count"] == 0
        assert result["is_constitutional"] is False

def test_human_approval_node_interactive_reset():
    state = {"retry_count": 2, "critic_feedback": ["Tests failed"]}
    with patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value="r"):
        result = loop_engine.human_approval_node(state)
        assert result["retry_count"] == 0
        assert result["code_diffs"] == {}
        assert result["critic_feedback"] == []

def test_route_after_critic_constitutional_exits_immediately():
    # If is_constitutional is True, it should return "end" immediately, even if DeltaGain would fail (Δ = 0)
    state = {
        "is_constitutional": True,
        "retry_count": 0,
        "prev_test_pass_rate": 0.0,
        "validation_output": "0 passed"
    }
    result = loop_engine.route_after_critic(state)
    assert result == "end"

def test_critic_node_increments_iteration_count():
    state = {"iteration_count": 5}
    # Mock subprocess run to avoid executing actual tests during this unit test
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.check_output", return_value="diff"):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "10 passed"
        mock_result.stderr = ""
        mock_run.return_value = mock_result
        
        result = loop_engine.critic_node(state)
        assert result["iteration_count"] == 6
