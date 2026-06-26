import os
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
        "current_test_pass_rate": 0.0,
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

def test_complexity_scorer_node_git_diff():
    # Mock subprocess.run to return custom numstat output
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.stdout = "5\t2\tfile1.py\n10\t0\tfile2.py\n-\t-\tbinary.bin\n"
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        state = {"task_prompt": "Task"}
        result = loop_engine.complexity_scorer_node(state)
        # modified_files = 3
        # lines_changed = (5 + 2) + (10 + 0) + 0 = 17
        # expected complexity = min(3 * 2 + 17 / 50 + 1, 10) = min(6 + 0.34 + 1, 10) = 7
        assert result["complexity_score"] == 7

def test_cost_estimator_node_actual_cost():
    # Test fallback on first run
    state = {"complexity_score": 4}
    result = loop_engine.cost_estimator_node(state)
    assert result["estimated_cost"] == 3.00 # 4 * 0.75
    
    # Test actual cost calculation
    state = {
        "token_usage": {"estimated_llm_cost_usd": 0.05},
        "execution_duration": 120.0
    }
    # compute cost = 120 * 0.0001 = 0.012
    # expected cost = 0.05 + 0.012 = 0.0620
    result = loop_engine.cost_estimator_node(state)
    assert result["estimated_cost"] == 0.0620

def test_critic_node_accumulates_duration_and_tokens():
    # Mock environment variables for token usage
    with patch.dict(os.environ, {
        "LLM_PROMPT_TOKENS": "1000",
        "LLM_COMPLETION_TOKENS": "500",
        "LLM_COST_USD": "0.02"
    }):
        state = {
            "token_usage": {
                "prompt_tokens": 2000,
                "completion_tokens": 1000,
                "estimated_llm_cost_usd": 0.04
            },
            "execution_duration": 45.0
        }
        
        with patch("subprocess.run") as mock_run, \
             patch("subprocess.check_output", return_value="diff"):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "1 passed"
            mock_result.stderr = ""
            mock_run.return_value = mock_result
            
            result = loop_engine.critic_node(state)
            
            # check accumulated token usage
            assert result["token_usage"]["prompt_tokens"] == 3000
            assert result["token_usage"]["completion_tokens"] == 1500
            assert abs(result["token_usage"]["estimated_llm_cost_usd"] - 0.06) < 1e-6
            
            # check accumulated execution duration
            assert result["execution_duration"] > 45.0

def test_export_state_to_json():
    import json
    import tempfile
    import os
    
    state = {
        "task_prompt": "Test export",
        "token_usage": {"estimated_llm_cost_usd": 0.03},
        "execution_duration": 100.0, # 100 * 0.0001 = 0.01 compute cost -> total 0.04
        "is_constitutional": True
    }
    
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_name = tmp.name
        
    try:
        loop_engine.export_state_to_json(state, tmp_name)
        with open(tmp_name, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        assert data["task_prompt"] == "Test export"
        assert data["estimated_cost"] == 0.04
        assert data["status"] == "PASSED"
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)

def test_build_graph():
    # Test building graph with and without checkpointer
    graph = loop_engine.build_graph()
    if loop_engine._LANGGRAPH_AVAILABLE:
        assert graph is not None
        assert graph.nodes is not None
        assert "planner" in graph.nodes
        assert "critic" in graph.nodes
    else:
        assert graph is None

def test_detect_oscillation():
    # Helper to compute a hash
    h1 = loop_engine.compute_state_hash({"git_diff": "diff1"})
    h2 = loop_engine.compute_state_hash({"git_diff": "diff2"})
    
    # 1. No oscillation: current diff is diff2, history has [h1, h2] (current is history[-1])
    state = {
        "code_diffs": {"git_diff": "diff2"},
        "state_hash_history": [h1, h2]
    }
    assert loop_engine.detect_oscillation(state) is False
    
    # 2. Oscillation: current diff is diff1, history has [h1, h2] (current is history[-1] of next state, history[-2] is h1)
    state = {
        "code_diffs": {"git_diff": "diff1"},
        "state_hash_history": [h1, h2]
    }
    assert loop_engine.detect_oscillation(state) is True

def test_check_delta_gain():
    # 1. No change in pass rate (delta < epsilon) -> returns False
    state = {
        "retry_count": 2,
        "last_exit_code": 1,
        "current_test_pass_rate": 1.0,
        "prev_test_pass_rate": 1.0
    }
    assert loop_engine.check_delta_gain(state) is False
    
    # 2. Change in pass rate (delta > epsilon) -> returns True
    state = {
        "retry_count": 2,
        "last_exit_code": 1,
        "current_test_pass_rate": 1.0,
        "prev_test_pass_rate": 0.5
    }
    assert loop_engine.check_delta_gain(state) is True

def test_route_after_critic_branches():
    # 1. Fatal violation
    state = {"fatal_violation": True}
    assert loop_engine.route_after_critic(state) == "fatal"
    
    # 2. Already constitutional
    state = {"is_constitutional": True}
    assert loop_engine.route_after_critic(state) == "end"
    
    # 3. Oscillation
    h1 = loop_engine.compute_state_hash({"git_diff": "diff1"})
    h2 = loop_engine.compute_state_hash({"git_diff": "diff2"})
    state = {
        "is_constitutional": False,
        "code_diffs": {"git_diff": "diff2"},
        "state_hash_history": [h1, h2, h1]
    }
    assert loop_engine.route_after_critic(state) == "human_approval"
    
    # 4. No delta gain progress
    state = {
        "is_constitutional": False,
        "retry_count": 2,
        "last_exit_code": 1,
        "current_test_pass_rate": 1.0,
        "prev_test_pass_rate": 1.0
    }
    assert loop_engine.route_after_critic(state) == "human_approval"
    
    # 5. Retry limit reached
    state = {
        "is_constitutional": False,
        "retry_count": 3,
        "max_retries": 3,
        "last_exit_code": 1,
        "current_test_pass_rate": 1.0,
        "prev_test_pass_rate": 0.5
    }
    assert loop_engine.route_after_critic(state) == "human_approval"
    
    # 6. Standard retry
    state = {
        "is_constitutional": False,
        "retry_count": 2,
        "max_retries": 3,
        "last_exit_code": 1,
        "current_test_pass_rate": 1.0,
        "prev_test_pass_rate": 0.5
    }
    assert loop_engine.route_after_critic(state) == "retry"

def test_rollback_node():
    # Success rollback
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = loop_engine.rollback_node({})
        assert result["code_diffs"] == {}
        assert result["last_exit_code"] == -1

    # Failed rollback
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "git error"
        mock_run.return_value = mock_result
        
        result = loop_engine.rollback_node({})
        assert result["last_exit_code"] == -1
        assert "ROLLBACK_FAILED" in result["critic_feedback"][0]

def test_actor_wrapper_node():
    with patch("global_loop_engine.loop_engine.create_loop_snapshot", return_value="tag"):
        result = loop_engine.actor_wrapper_node({"retry_count": 1})
        assert result["snapshot_counter"] == 1

def test_planner_node():
    with patch("global_loop_engine.loop_engine.compute_test_signature", return_value="dummy_sig"):
        result = loop_engine.planner_node({})
        assert result["test_baseline_signature"] == "dummy_sig"
        assert len(result["execution_plan"]) > 0

def test_state_compressor_node():
    # First iteration, no compressor action
    state = {"retry_count": 0}
    assert loop_engine.state_compressor_node(state) == {}
    
    # Compress history
    state = {
        "retry_count": 1,
        "critic_feedback": ["failed compilation"],
        "code_diffs": {"file.py": "diff"},
        "is_constitutional": False
    }
    result = loop_engine.state_compressor_node(state)
    assert len(result["compressed_history"]) == 1
    assert result["code_diffs"] == {}

def test_main_cli():
    # Test passing execution
    with patch("sys.argv", ["loop-engine", "--task", "Test", "--mode", "fast"]), \
         patch("global_loop_engine.loop_engine.resume_or_start", return_value={"is_constitutional": True, "iteration_count": 1, "last_exit_code": 0}), \
         patch("sys.exit") as mock_exit:
        loop_engine.main()
        mock_exit.assert_called_once_with(0)

    # Test failing execution
    with patch("sys.argv", ["loop-engine", "--task", "Test", "--mode", "fast"]), \
         patch("global_loop_engine.loop_engine.resume_or_start", return_value={"is_constitutional": False, "iteration_count": 2, "last_exit_code": 1}), \
         patch("sys.exit") as mock_exit:
        loop_engine.main()
        mock_exit.assert_called_with(1)

def test_resume_or_start():
    # Test resume_or_start fresh start and recovery
    if not loop_engine._LANGGRAPH_AVAILABLE:
        pytest.skip("LangGraph not available")
        
    with patch("global_loop_engine.loop_engine.CHECKPOINT_DB", "/tmp/dummy_checkpoint.db"), \
         patch("global_loop_engine.loop_engine.SqliteSaver.from_conn_string") as mock_saver:
        # Mock SQLite checkpointer
        mock_conn = MagicMock()
        mock_saver.return_value.__enter__.return_value = mock_conn
        
        # Mock build_graph_with_persistence to return a simple compiled graph
        from langgraph.graph import StateGraph, END
        from langgraph.checkpoint.memory import MemorySaver
        from typing import TypedDict
        
        class SimpleState(TypedDict):
            task_prompt: str
            is_constitutional: bool
            retry_count: int
            iteration_count: int
            last_exit_code: int
            token_usage: dict
            execution_duration: float
            
        builder = StateGraph(SimpleState)
        builder.add_node("planner", lambda s: {"is_constitutional": True})
        builder.set_entry_point("planner")
        builder.add_edge("planner", END)
        graph = builder.compile(checkpointer=MemorySaver())
        
        with patch("global_loop_engine.loop_engine.build_graph_with_persistence", return_value=graph):
            res = loop_engine.resume_or_start("Task", mode="auto", thread_id="t1")
            assert res["is_constitutional"] is True

def test_compute_test_signature_no_dir():
    sig = loop_engine.compute_test_signature("non_existent_directory_xyz")
    assert sig == "NO_TESTS_DIR"

def test_load_token_usage_from_env_errors():
    with patch.dict(os.environ, {"LLM_PROMPT_TOKENS": "invalid"}):
        res = loop_engine.load_token_usage_from_env()
        assert res == {}

def test_parse_pass_rate_various():
    assert loop_engine.parse_pass_rate("") == -1.0
    assert loop_engine.parse_pass_rate("1 failed") == 0.0
    assert loop_engine.parse_pass_rate("1 passed, 1 failed") == 0.5

def test_create_loop_snapshot_warnings():
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "warning"
        mock_run.return_value = mock_result
        
        tag = loop_engine.create_loop_snapshot(1)
        assert tag == "loop-snapshot-retry-1"

def test_critic_node_strict_mode_failures():
    state = {}
    with patch.dict(os.environ, {"CRITIC_COMMAND": "nonexistent_cmd", "STRICT_TEST_REQUIREMENT": "true"}), \
         patch("subprocess.run") as mock_run, \
         patch("subprocess.check_output", return_value="diff"):
        
        # Command exit code 127 (command not found)
        mock_result = MagicMock()
        mock_result.returncode = 127
        mock_result.stdout = ""
        mock_result.stderr = "command not found"
        mock_run.return_value = mock_result
        
        result = loop_engine.critic_node(state)
        assert result["last_exit_code"] == 1
        assert "Validator command not found" in result["validation_output"]

    with patch.dict(os.environ, {"CRITIC_COMMAND": "pytest", "STRICT_TEST_REQUIREMENT": "true"}), \
         patch("subprocess.run") as mock_run, \
         patch("subprocess.check_output", return_value="diff"):
        
        # pytest missing error
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "No module named pytest"
        mock_result.stderr = ""
        mock_run.return_value = mock_result
        
        result = loop_engine.critic_node(state)
        assert result["last_exit_code"] == 1
        assert "pytest is not installed" in result["validation_output"]

def test_create_loop_snapshot_success():
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        tag = loop_engine.create_loop_snapshot(0)
        assert tag == "loop-snapshot-retry-0"

def test_human_approval_node_interactive_eof():
    state = {"retry_count": 2, "critic_feedback": ["Tests failed"]}
    with patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", side_effect=EOFError):
        result = loop_engine.human_approval_node(state)
        assert result["retry_count"] == 99
        assert result["is_constitutional"] is False

def test_compute_test_signature_with_files():
    import tempfile
    import shutil
    
    tmpdir = tempfile.mkdtemp()
    try:
        # Create a file
        filepath = os.path.join(tmpdir, "test_file.py")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("def test_dummy(): pass")
            
        sig = loop_engine.compute_test_signature(tmpdir)
        assert len(sig) == 64
        
        # Signatures should differ if file is modified
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("def test_dummy(): pass\n# comment")
            
        sig2 = loop_engine.compute_test_signature(tmpdir)
        assert sig != sig2
    finally:
        shutil.rmtree(tmpdir)

def test_check_delta_gain_exit_code_2():
    # 1. Current exit code is 2 -> returns True (bypasses DeltaGain halt)
    state = {
        "retry_count": 2,
        "last_exit_code": 2,
        "prev_test_pass_rate": 0.0,
        "current_test_pass_rate": 0.0
    }
    assert loop_engine.check_delta_gain(state) is True

    # 2. Previous exit code was 2 -> returns True (bypasses DeltaGain halt)
    state = {
        "retry_count": 2,
        "last_exit_code": 1,
        "last_error_context": {"prev_exit_code": 2},
        "prev_test_pass_rate": 0.0,
        "current_test_pass_rate": 0.0
    }
    assert loop_engine.check_delta_gain(state) is True


def test_compute_test_signature_excludes_pycache():
    """测试计算测试签名时，会排除缓存目录和编译文件（如 __pycache__, .pytest_cache 以及 .pyc 文件）。"""
    import tempfile
    import shutil
    
    tmpdir = tempfile.mkdtemp()
    try:
        # 1. 创建普通测试文件
        filepath = os.path.join(tmpdir, "test_file.py")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("def test_dummy(): pass")
            
        sig_base = loop_engine.compute_test_signature(tmpdir)
        assert len(sig_base) == 64
        
        # 2. 创建 __pycache__ 目录和里面的 .pyc 文件
        pycache_dir = os.path.join(tmpdir, "__pycache__")
        os.makedirs(pycache_dir, exist_ok=True)
        pyc_file = os.path.join(pycache_dir, "test_file.cpython-310.pyc")
        with open(pyc_file, "wb") as f:
            f.write(b"dummy compiled byte code")
            
        # 3. 创建 .pytest_cache 目录和里面的内容
        pytest_cache_dir = os.path.join(tmpdir, ".pytest_cache")
        pytest_file = os.path.join(pytest_cache_dir, "v/cache/lastfailed")
        os.makedirs(os.path.dirname(pytest_file), exist_ok=True)
        with open(pytest_file, "w", encoding="utf-8") as f:
            f.write('{"test_dummy": true}')
            
        # 4. 创建独立的 .pyc 文件
        pyc_isolated = os.path.join(tmpdir, "test_file.pyc")
        with open(pyc_isolated, "wb") as f:
            f.write(b"another dummy pyc")
            
        sig_with_caches = loop_engine.compute_test_signature(tmpdir)
        # 签名应该不受缓存文件和目录的影响，保持一致
        assert sig_base == sig_with_caches
        
    finally:
        shutil.rmtree(tmpdir)


def test_check_delta_gain_bypass_when_parse_fails():
    """测试当通过率解析失败（返回 -1.0，如非 pytest 的 Go/npm 框架）时，DeltaGain 会自动绕过不进行无进展拦截。"""
    # 1. 当前通过率解析失败为 -1.0 -> 绕过
    state1 = {
        "retry_count": 2,
        "last_exit_code": 1,
        "current_test_pass_rate": -1.0,
        "prev_test_pass_rate": 0.5
    }
    assert loop_engine.check_delta_gain(state1) is True

    # 2. 前一次通过率解析失败为 -1.0 -> 绕过
    state2 = {
        "retry_count": 2,
        "last_exit_code": 1,
        "current_test_pass_rate": 0.5,
        "prev_test_pass_rate": -1.0
    }
    assert loop_engine.check_delta_gain(state2) is True


def test_integrity_guard_prevents_cheat_on_resume():
    """测试在图发生中断挂起后，如果外部智能体试图篡改单测，在 Resume 恢复时能被 test_integrity_guard 正确捕获并熔断。"""
    from langgraph.checkpoint.memory import MemorySaver
    import tempfile
    import shutil

    tmpdir = tempfile.mkdtemp()
    try:
        # 1. 建立测试环境的 tests/ 目录，放入原始测试脚本
        test_file = os.path.join(tmpdir, "test_cheat.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("def test_original(): pass")

        # 2. 我们需要 Mock compute_test_signature 的行为，使之指向我们的临时测试目录
        original_compute = loop_engine.compute_test_signature
        def mock_compute(tests_dir_arg=None):
            return original_compute(tmpdir)

        # 3. 构造并编译图，指定 MemorySaver 以便于在单测中持久化恢复
        checkpointer = MemorySaver()
        graph = loop_engine.build_graph(checkpointer)
        config = {"configurable": {"thread_id": "cheat-test-thread"}}

        initial_state = {
            "task_prompt": "Fix some bug",
            "complexity_score": 0,
            "estimated_cost": 0.0,
            "execution_plan": [],
            "code_diffs": {},
            "last_exit_code": -1,
            "retry_count": 0,
            "max_retries": 2,
            "critic_feedback": [],
            "is_constitutional": False,
            "assembled_context": "",
            "last_error_context": {},
            "compressed_history": [],
            "state_hash_history": [],
            "validation_output": "",
            "prev_test_pass_rate": 0.0,
            "current_test_pass_rate": 0.0,
            "planned_commands": [],
            "test_baseline_signature": "",
            "snapshot_counter": 0,
            "fatal_violation": False,
            "token_usage": {},
            "execution_duration": 0.0,
        }

        # 4. 在 mock 的情况下，流式执行第一阶段，直到在 actor_wrapper 前挂起
        with patch("global_loop_engine.loop_engine.compute_test_signature", side_effect=mock_compute), \
             patch("subprocess.run") as mock_run:
            
            # Mock git commands inside actor_wrapper to avoid running real git in unit test
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            # 第一阶段：流式执行到第一个中断点
            for event in graph.stream(initial_state, config=config):
                pass
            
            # 确认处于挂起状态，且下一个执行节点是 actor_wrapper
            state = graph.get_state(config)
            assert "actor_wrapper" in state.next
            assert state.values.get("test_baseline_signature") != ""
            
            # 5. 挂起后，模拟智能体作弊行为：篡改单测脚本
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("def test_original(): pass\n# CHEAT_MODIFICATION")

            # 6. Resume 恢复执行，观察是否会直接走 FATAL 熔断
            for event in graph.stream(None, config=config):
                pass
            
            # 检查最终的状态值
            final_state = graph.get_state(config).values
            assert final_state.get("fatal_violation") is True
            assert final_state.get("is_constitutional") is False
            assert any("FATAL: tests/ directory signature mismatch" in fb for fb in final_state.get("critic_feedback", []))
            
    finally:
        shutil.rmtree(tmpdir)
