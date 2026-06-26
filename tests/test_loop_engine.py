import sys
from unittest.mock import patch
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
