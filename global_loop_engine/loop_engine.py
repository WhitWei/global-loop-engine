"""
Global Loop Engine v2.0 — Security & Data Layer Core (Sprint 1: WO-01~04)
=======================================================================
LangGraph-based agentic loop for IDE/CLI environments.
Enforces "think -> execute -> critique -> refine" on every coding action.

Key design decisions:
- Hot/cold state separation (diffs only, never full source).
- SHA256 test-integrity guard to detect agent "cheat green" (WO-02).
- Regex-based dangerous command whitelist filter (WO-03).
- Git atomic snapshot rollback for deterministic recovery (WO-04).
- State compression and context assembly to prevent token explosion.
- Oscillation detection to detect A -> B -> A cycles.
"""

import os
import sys
import subprocess
import argparse
import logging
import hashlib
import re
import operator
from typing import TypedDict, Annotated
from typing_extensions import NotRequired
from pathlib import Path

# Load .env file before reading constants
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.sqlite import SqliteSaver
    _LANGGRAPH_AVAILABLE = True
except ImportError:
    _LANGGRAPH_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants (configuration separated from code, declared at top level)
# ---------------------------------------------------------------------------
CHECKPOINT_DB: str = os.environ.get(
    "LOOP_ENGINE_DB_PATH",
    os.path.join(Path.home(), ".loop-engine", "loop_checkpoints.db"),
)
MAX_RETRIES: int = int(os.environ.get("MAX_RETRIES", "3"))
EPSILON_PASS_RATE_DELTA: float = float(os.environ.get("EPSILON_PASS_RATE_DELTA", "0.02"))
MAX_FEEDBACK_HISTORY: int = int(os.environ.get("MAX_FEEDBACK_HISTORY", "3"))
MAX_DIFF_LENGTH: int = int(os.environ.get("MAX_DIFF_LENGTH", "500"))
MAX_ERROR_OUTPUT_LENGTH: int = int(os.environ.get("MAX_ERROR_OUTPUT_LENGTH", "500"))
LOGGER_NAME: str = os.environ.get("LOGGER_NAME", "global_loop_engine")
TESTS_DIR_DEFAULT: str = os.environ.get("TESTS_DIR_DEFAULT", "./tests")

# ---- Dangerous command patterns for SanitizeNode (WO-03) ----
DANGEROUS_PATTERNS: list[str] = [
    r"rm\s+-r[fF]\s+[/~]",
    r"rm\s+-r[fF]\s+\*",
    r"DROP\s+TABLE",
    r":\(\)\s*\{.*\}",
    r"curl\b.*\|\s*(ba)?sh",
    r"wget\b.*\|\s*(ba)?sh",
    r"chmod\s+777\s+/",
    r">\s*/etc/",
    r"mkfs\.",
    r"dd\s+if=.*of=/dev/",
    r"git\s+push\s+--force.*origin",
]

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(LOGGER_NAME)

if not _LANGGRAPH_AVAILABLE:
    logger.warning("langgraph not available; run: pip install langgraph")


# =============================================================================
# WO-01: GlobalExecutionState — State Definition with Reducer Semantics
# =============================================================================
class GlobalExecutionState(TypedDict):
    """Global execution state with hot/cold separation and reducer semantics.

    Hot fields (change every iteration): code_diffs, last_exit_code, critic_feedback
    Cold fields (stable across loops): task_prompt, execution_plan
    Control flow: retry_count, max_retries, is_constitutional
    """
    # Core task identification
    task_prompt: str

    # Planning (cold)
    complexity_score: NotRequired[int]
    estimated_cost: NotRequired[float]
    execution_plan: NotRequired[list[str]]

    # Hot state (lightweight, persisted per loop)
    code_diffs: NotRequired[dict]
    last_exit_code: NotRequired[int]

    # Control flow & circuit breakers
    retry_count: NotRequired[int]
    max_retries: NotRequired[int]

    # Critic feedback (Reducer: operator.add appends, never overwrites)
    critic_feedback: NotRequired[Annotated[list[str], operator.add]]
    is_constitutional: NotRequired[bool]

    # Context management
    assembled_context: NotRequired[str]
    last_error_context: NotRequired[dict]
    compressed_history: NotRequired[Annotated[list[str], operator.add]]

    # Oscillation detection
    state_hash_history: NotRequired[Annotated[list[str], operator.add]]

    # Validation metrics
    validation_output: NotRequired[str]
    prev_test_pass_rate: NotRequired[float]

    # WO-01 v2.0: feedback black hole elimination
    planned_commands: NotRequired[list[str]]

    # WO-02: test integrity baseline
    test_baseline_signature: NotRequired[str]

    # C-001 fix: snapshot counter for rollback orchestration
    snapshot_counter: NotRequired[int]

    # S-002 fix: fatal violation flag — skips retry, halts immediately
    fatal_violation: NotRequired[bool]


# =============================================================================
# Helper functions
# =============================================================================

def compute_state_hash(code_diffs: dict) -> str:
    """Compute a short MD5 fingerprint of the current code diffs."""
    content = str(sorted(code_diffs.items())) if code_diffs else "empty"
    return hashlib.md5(content.encode()).hexdigest()[:8]


def _compute_file_tree_hash(directory: str) -> str:
    """WO-02: Compute SHA256 fingerprint of all files in a directory.

    Why SHA256: collision resistance for integrity verification.
    Why include relative paths in hash: prevents file-rename and content-swap
    attacks where an agent moves tests between files to evade detection
    (S-001 fix). Each file's hash contribution is: SHA256(path + '\0' + content).
    """
    sig = hashlib.sha256()
    p = Path(directory)
    if not p.exists():
        return "NO_TESTS_DIR"
    for filepath in sorted(p.rglob("*")):
        if filepath.is_file():
            try:
                with open(filepath, "rb") as fh:
                    content = fh.read()
                # Include relative path + null separator to bind content to filename
                rel_path = str(filepath.relative_to(p))
                sig.update(rel_path.encode("utf-8"))
                sig.update(b"\x00")
                sig.update(content)
            except (PermissionError, OSError) as exc:
                logger.warning("Cannot read file %s: %s", filepath, exc)
                continue
    return sig.hexdigest()


# =============================================================================
# WO-02: TestIntegrityGuardNode — tests/ Tamper-Proof Signature (P0 Security)
# =============================================================================

def compute_test_signature(tests_dir: str | None = None) -> str:
    """Compute SHA256 fingerprint of the tests/ directory.

    Args:
        tests_dir: Test directory path, defaults to TESTS_DIR_DEFAULT.

    Returns:
        64-char hex SHA256 digest, or "NO_TESTS_DIR" if directory not found.
    """
    target = tests_dir or TESTS_DIR_DEFAULT
    return _compute_file_tree_hash(target)


def test_integrity_guard_node(state: GlobalExecutionState) -> dict:
    """Verify tests/ directory not tampered with before CriticNode.

    Compares current SHA256 signature against the baseline stored in
    state["test_baseline_signature"]. Mismatch triggers immediate circuit-break.
    """
    baseline_sig = state.get("test_baseline_signature", "")
    current_sig = compute_test_signature()

    if not baseline_sig:
        logger.info("[TestIntegrityGuard] No baseline signature, skipping")
        return {"test_baseline_signature": current_sig}

    if current_sig != baseline_sig:
        logger.critical(
            "🚨 [TestIntegrityGuard] tests/ directory tampered! "
            "baseline=%s... current=%s...",
            baseline_sig[:16], current_sig[:16],
        )
        return {
            "is_constitutional": False,
            "fatal_violation": True,
            "critic_feedback": [
                "FATAL: tests/ directory signature mismatch — "
                "agent likely modified test cases to cheat green. "
                f"Baseline: {baseline_sig[:16]}..., Current: {current_sig[:16]}..."
            ],
        }

    logger.info("[TestIntegrityGuard] tests/ signature verified: %s...", current_sig[:16])
    return {}


# =============================================================================
# WO-03: SanitizeNode — Dangerous Command Whitelist Filter
# =============================================================================

def sanitize_node(state: GlobalExecutionState) -> dict:
    """Filter dangerous commands before ActorNode execution.

    Iterates planned_commands against DANGEROUS_PATTERNS regexes.
    First match triggers immediate block.
    """
    planned = state.get("planned_commands") or []

    if not planned:
        logger.debug("[SanitizeNode] No commands to check")
        return {}

    for cmd in planned:
        if not isinstance(cmd, str):
            continue
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                logger.critical(
                    "🛡️ [SanitizeNode] Dangerous command blocked: %s (pattern: %s)",
                    cmd, pattern,
                )
                return {
                    "is_constitutional": False,
                    "critic_feedback": [
                        f"BLOCKED: dangerous command [{cmd}] matched pattern "
                        f"[{pattern}], execution blocked."
                    ],
                }

    logger.info("[SanitizeNode] All %d command(s) passed safety check", len(planned))
    return {}


# =============================================================================
# WO-04: Git Atomic Snapshot Rollback (RollbackNode)
# =============================================================================

def create_loop_snapshot(retry_count: int, workdir: str | None = None) -> str:
    """Create a git snapshot before ActorNode execution.

    Runs git add -A then git commit to capture all current state as a
    tagged snapshot. RollbackNode can git reset --hard HEAD~1 to revert.
    """
    tag = f"loop-snapshot-retry-{retry_count}"
    cwd = workdir or "."

    add_result = subprocess.run(
        ["git", "add", "-A"], capture_output=True, text=True, cwd=cwd,
    )
    if add_result.returncode != 0:
        logger.warning("[Snapshot] git add -A warning: %s", add_result.stderr.strip())

    result = subprocess.run(
        ["git", "commit", "--allow-empty", "-m", f"[Loop Engine] Pre-execution snapshot: {tag}"],
        capture_output=True, text=True, cwd=cwd,
    )
    if result.returncode != 0:
        logger.warning("[Snapshot] Snapshot warning: %s", result.stderr.strip())
    else:
        logger.info("[Snapshot] Snapshot created: %s", tag)

    return tag


def rollback_node(state: GlobalExecutionState, workdir: str | None = None) -> dict:
    """Deterministic rollback to the most recent loop snapshot.

    Executes git reset --hard HEAD~1 to restore the working tree.
    """
    cwd = workdir or "."
    logger.info("[RollbackNode] Starting git rollback (reset --hard HEAD~1)...")

    result = subprocess.run(
        ["git", "reset", "--hard", "HEAD~1"],
        capture_output=True, text=True, cwd=cwd,
    )

    if result.returncode == 0:
        logger.info("[RollbackNode] Rollback successful, working tree restored")
        return {"code_diffs": {}, "last_exit_code": -1}
    else:
        logger.error("[RollbackNode] Rollback failed: %s", result.stderr.strip())
        return {
            "last_exit_code": -1,
            "critic_feedback": [f"ROLLBACK_FAILED: {result.stderr.strip()}"],
        }


# =============================================================================
# PlannerNode (WO-02: initializes tests/ baseline)
# =============================================================================

def planner_node(state: GlobalExecutionState) -> dict:
    """Initialize tests/ baseline signature before any code changes."""
    baseline = compute_test_signature()
    logger.info(
        "[PlannerNode] tests/ baseline signature initialized: %s",
        baseline[:16] if baseline != "NO_TESTS_DIR" else "N/A",
    )
    return {
        "test_baseline_signature": baseline,
        "execution_plan": [
            "Planner → ComplexityScorer → CostEstimator → "
            "ContextAssembler → TestIntegrityGuard → Sanitize → Critic → Router"
        ],
    }


# =============================================================================
# Existing nodes (from v1.0, adapted for v2.0)
# =============================================================================

def complexity_scorer_node(state: GlobalExecutionState) -> dict:
    """Assess task complexity based on length and keyword heuristics."""
    task = state.get("task_prompt", "")
    score = min(len(task) // 10 + 1, 10)
    logger.info("[ComplexityScorerNode] Task complexity score: %d", score)
    return {"complexity_score": score}


def cost_estimator_node(state: GlobalExecutionState) -> dict:
    """Estimate execution cost from complexity score."""
    complexity = state.get("complexity_score", 1) or 1
    cost = round(complexity * 0.75, 2)
    logger.info("[CostEstimatorNode] Estimated cost: %.2f", cost)
    return {"estimated_cost": cost}


def context_assembler_node(state: GlobalExecutionState) -> dict:
    """Assemble cold-state history into structured prompt context."""
    assembled_parts = []

    assembled_parts.append(f"## Current Task\n{state['task_prompt']}\n")

    error_ctx = state.get("last_error_context", {})
    if error_ctx:
        assembled_parts.append(
            f"## Last Failure Record\n"
            f"- Failed command: `{error_ctx.get('failed_command', 'N/A')}`\n"
            f"- Error output:\n```\n"
            f"{error_ctx.get('error_output', 'N/A')[:MAX_ERROR_OUTPUT_LENGTH]}"
            f"\n```\n"
            f"- Diff at failure:\n```diff\n"
            f"{error_ctx.get('diff_at_failure', 'N/A')[:MAX_DIFF_LENGTH]}"
            f"\n```\n"
        )

    feedbacks = state.get("critic_feedback", [])
    if feedbacks:
        assembled_parts.append(
            f"## Historical Critic Feedback (total {len(feedbacks)})\n"
            + "\n".join(f"- {f}" for f in feedbacks[-MAX_FEEDBACK_HISTORY:])
            + "\n"
        )

    assembled_context = "\n".join(assembled_parts)
    logger.info("[ContextAssembler] Context assembled: ~%d chars", len(assembled_context))
    return {"assembled_context": assembled_context}


def state_compressor_node(state: GlobalExecutionState) -> dict:
    """Compress accumulated history to prevent token explosion."""
    retry_count = state.get("retry_count", 0)
    feedbacks = state.get("critic_feedback", [])
    diffs = state.get("code_diffs", {})

    if retry_count == 0 or not feedbacks:
        logger.debug("[StateCompressor] First round, no compression needed")
        return {}

    latest_feedback = feedbacks[-1] if feedbacks else "no feedback"
    diff_summary = f"modified {len(diffs)} files" if diffs else "no code changes"
    passed = state.get("is_constitutional", False)

    lesson = (
        f"Attempt {retry_count}: "
        f"Action [{diff_summary}] -> "
        f"Result [{'PASS' if passed else 'FAIL'}] -> "
        f"Lesson [{latest_feedback[:100]}]"
    )

    logger.info("[StateCompressor] Round %d compressed: %s...", retry_count, lesson[:80])
    return {
        "compressed_history": [lesson],
        "code_diffs": {},
    }


def critic_node(state: GlobalExecutionState) -> dict:
    """Hard validation via subprocess (pytest, git diff).

    Only terminal return codes are truth — never trust the model's word.
    """
    logger.info("[CriticNode] Starting hard validation...")

    # 1. Git diff snapshot
    try:
        diff_output = subprocess.check_output(
            ["git", "diff"], stderr=subprocess.STDOUT, text=True
        )
    except subprocess.CalledProcessError as e:
        diff_output = f"Git diff error: {e.output[:500]}"
    except FileNotFoundError:
        diff_output = "Git not found."

    # 2. pytest hard validation
    exit_code = 0
    validation_output = ""
    try:
        probe = subprocess.run(["pytest", "--version"], capture_output=True, text=True)
        if probe.returncode == 0:
            logger.info("[CriticNode] pytest detected, running tests...")
            test_result = subprocess.run(["pytest"], capture_output=True, text=True)
            exit_code = test_result.returncode
            validation_output = test_result.stdout + "\n" + test_result.stderr
        else:
            validation_output = "pytest not available, skipping test execution."
    except FileNotFoundError:
        validation_output = "pytest not installed, skipping test execution."
        exit_code = 0
    except Exception as e:
        exit_code = 1
        validation_output = f"Validation error: {e}"

    is_valid = exit_code == 0
    logger.info("[CriticNode] Validation done. Exit: %d, Pass: %s", exit_code, is_valid)

    last_error_context = {
        "failed_command": "pytest" if exit_code != 0 else "N/A",
        "error_output": validation_output if exit_code != 0 else "",
        "diff_at_failure": diff_output if exit_code != 0 else "",
    }

    current_pass_rate = parse_pass_rate(validation_output)

    return {
        "code_diffs": {"git_diff": diff_output[:MAX_DIFF_LENGTH]},
        "last_exit_code": exit_code,
        "validation_output": validation_output[:MAX_ERROR_OUTPUT_LENGTH],
        "is_constitutional": is_valid,
        "last_error_context": last_error_context,
        "retry_count": (state.get("retry_count", 0) or 0) + 1,
        "state_hash_history": [compute_state_hash({"git_diff": diff_output[:MAX_DIFF_LENGTH]})],
        "prev_test_pass_rate": current_pass_rate,
    }


# =============================================================================
# Oscillation detection
# =============================================================================

def detect_oscillation(state: GlobalExecutionState) -> bool:
    """Detect A->B->A oscillation pattern in state history."""
    current_hash = compute_state_hash(state.get("code_diffs", {}))
    history = state.get("state_hash_history", []) or []
    if len(history) >= 2 and current_hash == history[-2]:
        logger.warning(
            "🔄 [OscillationDetector] Oscillation detected! Hash=%s matches step %d",
            current_hash, len(history) - 1,
        )
        return True
    return False


# =============================================================================
# Router (pure function, no LLM)
# =============================================================================

def parse_pass_rate(validation_output: str) -> float:
    """从 pytest 输出中解析测试通过率"""
    if not validation_output:
        return 0.0
    match = re.search(r"(\d+) passed", validation_output)
    total_str = validation_output
    total_matches = re.findall(r"(\d+) (?:passed|failed|error)", total_str)
    total = sum(int(n) for n in total_matches)
    if match and total > 0:
        passed = int(match.group(1))
        return passed / max(total, 1)
    return 0.0

def check_delta_gain(state: GlobalExecutionState) -> bool:
    """
    Reflexion 论文实践：如果连续两次重试的测试通过率无提升，提前熔断。
    ε = 2%（低于此值视为无进展）
    """
    current_rate = parse_pass_rate(state.get("validation_output", ""))
    prev_rate = state.get("prev_test_pass_rate", 0.0)
    delta = abs(current_rate - prev_rate)
    
    if (state.get("retry_count", 0) or 0) > 0 and delta < EPSILON_PASS_RATE_DELTA:
        logger.warning(
            "⚡ [DeltaGain] 无进展检测：当前通过率=%.1f%%, 上次=%.1f%%, Δ=%.1f%% < ε=%.1f%%",
            current_rate * 100, prev_rate * 100, delta * 100, EPSILON_PASS_RATE_DELTA * 100
        )
        return False  # 触发熔断
    return True

def route_after_critic(state: GlobalExecutionState) -> str:
    """Determine next edge after CriticNode based on state analysis."""
    retry = state.get("retry_count", 0) or 0

    # Priority 0 (S-002): fatal violations → halt immediately, no retry
    if state.get("fatal_violation"):
        logger.critical("[Router] FATAL violation detected — halting immediately, no retry")
        return "fatal"

    if detect_oscillation(state):
        logger.warning("[Router] Oscillation -> human_approval")
        return "human_approval"

    if not check_delta_gain(state):
        logger.warning("[Router] Delta-gain below threshold -> human_approval")
        return "human_approval"

    if state.get("is_constitutional"):
        logger.info("[Router] Passed -> end")
        return "end"

    max_r = state.get("max_retries", MAX_RETRIES) or MAX_RETRIES
    if retry >= max_r:
        logger.warning("[Router] Retry limit (%d/%d) -> human_approval", retry, max_r)
        return "human_approval"

    logger.info("[Router] Failed (%d/%d) -> retry", retry, max_r)
    return "retry"


# =============================================================================
# WO-09: HumanApprovalNode
# =============================================================================

def actor_wrapper_node(state: GlobalExecutionState) -> dict:
    """C-001: Wrapper around ActorNode execution with snapshot + rollback guard.

    Why separate wrapper: keeps the snapshot/rollback logic decoupled from
    the actual code-generation logic. S-001 compliant — file-path-aware hashing.

    Flow:
      1. create_loop_snapshot() — git add -A + git commit --allow-empty
      2. (ActorNode would execute here — placeholder for now)
      3. On return, snapshot_counter is incremented for the next iteration
    """
    retry = state.get("retry_count", 0) or 0
    logger.info("[ActorWrapper] Creating pre-execution snapshot for retry %d...", retry)
    tag = create_loop_snapshot(retry)
    logger.info("[ActorWrapper] Snapshot %s created, ready for Actor execution", tag)
    return {"snapshot_counter": retry}


def human_approval_node(state: GlobalExecutionState) -> dict:
    """
    人机协作节点。在以下情况触发：
    1. retry_count >= 2
    2. 检测到震荡（A↔B 循环）
    3. Δ 增益 < ε（无进展）
    """
    retry = state.get("retry_count", 0)
    feedbacks = state.get("critic_feedback", [])
    last_feedback = feedbacks[-1] if feedbacks else "未知原因"
    
    print("\n" + "="*60)
    print("🤝 [HumanApprovalNode] 需要您的介入！")
    print(f"   已尝试次数: {retry}")
    print(f"   最近失败原因: {last_feedback[:200]}")
    print("="*60)

    # Prevent indefinite hanging in non-interactive environments (CI, agent runners)
    if not sys.stdin.isatty():
        logger.warning(
            "⚠️ [HumanApproval] Non-interactive environment/stdin detected. "
            "Aborting interactive prompt to prevent terminal hanging."
        )
        return {"is_constitutional": False, "retry_count": 99}

    print("\n选项：")
    print("  [y] 继续，让引擎再试一次")
    print("  [n] 放弃，保留当前状态供手动处理")
    print("  [r] 完全重置，从头开始")
    
    try:
        choice = input("\n请输入您的选择 [y/n/r]: ").strip().lower()
    except EOFError:
        choice = "n"
    
    if choice == "y":
        logger.info("[HumanApproval] 用户选择继续，重置重试计数...")
        return {"retry_count": 0, "is_constitutional": False}
    elif choice == "r":
        logger.info("[HumanApproval] 用户选择重置，清空所有状态...")
        return {"retry_count": 0, "code_diffs": {}, "critic_feedback": [], "is_constitutional": False}
    else:
        logger.info("[HumanApproval] 用户选择放弃，引擎挂起。")
        return {"is_constitutional": False, "retry_count": 99}  # 强制结束


# =============================================================================
# Graph construction
# =============================================================================

def build_graph(checkpointer=None):
    """Build and compile the LangGraph workflow.

    Topology (v2.1 — C-001/S-002 fixed):
        Planner -> ComplexityScorer -> CostEstimator -> ContextAssembler
            -> TestIntegrityGuard -> Sanitize -> ActorWrapper -> Critic -> Router
                -> end | fatal | human_approval | retry
        retry -> RollbackNode -> StateCompressor -> ContextAssembler (loop)
        fatal -> FatalHalt (no retry, immediate exit)
    """
    if not _LANGGRAPH_AVAILABLE:
        logger.error("LangGraph not available, cannot build graph.")
        return None

    workflow = StateGraph(GlobalExecutionState)

    workflow.add_node("planner", planner_node)
    workflow.add_node("complexity_scorer", complexity_scorer_node)
    workflow.add_node("cost_estimator", cost_estimator_node)
    workflow.add_node("context_assembler", context_assembler_node)
    workflow.add_node("test_integrity_guard", test_integrity_guard_node)
    workflow.add_node("sanitize", sanitize_node)
    workflow.add_node("actor_wrapper", actor_wrapper_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("rollback", rollback_node)
    workflow.add_node("state_compressor", state_compressor_node)
    workflow.add_node("human_approval", human_approval_node)

    workflow.set_entry_point("planner")

    workflow.add_edge("planner", "complexity_scorer")
    workflow.add_edge("complexity_scorer", "cost_estimator")
    workflow.add_edge("cost_estimator", "context_assembler")
    workflow.add_edge("context_assembler", "test_integrity_guard")
    workflow.add_edge("test_integrity_guard", "sanitize")
    workflow.add_edge("sanitize", "actor_wrapper")
    workflow.add_edge("actor_wrapper", "critic")

    workflow.add_conditional_edges(
        "critic",
        route_after_critic,
        {
            "end": END,
            "fatal": "human_approval",     # S-002: fatal → halt chain → end
            "human_approval": "human_approval",
            "retry": "rollback",            # C-001: retry first rolls back!
        },
    )

    workflow.add_edge("human_approval", END)
    workflow.add_edge("rollback", "state_compressor")    # C-001: rollback then compress
    workflow.add_edge("state_compressor", "context_assembler")
    logger.info("State graph compiled successfully")
    return workflow.compile(checkpointer=checkpointer)

def build_graph_with_persistence(checkpointer=None):
    """构建带持久化检查点的图，支持跨会话恢复"""
    return build_graph(checkpointer=checkpointer)

def resume_or_start(task: str, mode: str = "auto", thread_id: str = "default"):
    """
    尝试从上次中断的检查点恢复，如无记录则全新开始。
    根据 mode 参数执行不同流程（/loop 或 /fast 接管）。
    """


    # Ensure parent directory exists for fresh installs
    os.makedirs(os.path.dirname(CHECKPOINT_DB), exist_ok=True)

    with SqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
        graph = build_graph_with_persistence(checkpointer)
        if graph is None:
            logger.error("Graph build failed")
            sys.exit(1)
            
        config = {"configurable": {"thread_id": thread_id}}
        existing = graph.get_state(config)
        
        if existing and getattr(existing, 'values', None):
            logger.info("[Recovery] 检测到未完成的 Loop (thread_id=%s)，从断点继续...", thread_id)
            final_state = None
            for event in graph.stream(None, config=config):
                for node_name, node_state in event.items():
                    if node_name != "__end__":
                        logger.info("--- Node [%s] completed ---", node_name)
                    final_state = node_state
            return final_state
        else:
            logger.info("[Recovery] 未找到历史状态，全新开始...")
            initial_state: GlobalExecutionState = {
                "task_prompt": task,
                "complexity_score": 0,
                "estimated_cost": 0.0,
                "execution_plan": [],
                "code_diffs": {},
                "last_exit_code": -1,
                "retry_count": 0,
                "max_retries": MAX_RETRIES,
                "critic_feedback": [],
                "is_constitutional": False,
                "assembled_context": "",
                "last_error_context": {},
                "compressed_history": [],
                "state_hash_history": [],
                "validation_output": "",
                "prev_test_pass_rate": 0.0,
                "planned_commands": [],
                "test_baseline_signature": "",
                "snapshot_counter": 0,
                "fatal_violation": False,
            }
            if mode == "loop":
                logger.info("[/loop] 强制 Loop 模式：无视复杂度评分，强制启动完整闭环")
                initial_state["complexity_score"] = 100
            elif mode == "fast":
                logger.info("[/fast] 快速模式：开启单次严格沙盒验证，拒绝任何重试")
                initial_state["max_retries"] = 0
                
            final_state = None
            for event in graph.stream(initial_state, config=config):
                for node_name, node_state in event.items():
                    if node_name != "__end__":
                        logger.info("--- Node [%s] completed ---", node_name)
                    final_state = node_state
            return final_state

# =============================================================================
# CLI entry point
# =============================================================================

def main():
    """Entry point for the `loop-engine` console script."""
    parser = argparse.ArgumentParser(description="Global Loop Engine v2.0")
    parser.add_argument("--task", type=str, required=True, help="Task description")
    parser.add_argument("--mode", choices=["loop", "fast", "auto"], default="auto",
                        help="loop=强制完整闭环; fast=跳过Loop直出; auto=复杂度自动判断")
    args = parser.parse_args()

    logger.info("=" * 50)
    logger.info("Global Loop Engine v2.0 (LangGraph mode)")
    logger.info("=" * 50)

    final_state = resume_or_start(args.task, mode=args.mode)

    logger.info("=" * 50)
    logger.info("Engine Summary")

    if final_state and final_state.get("is_constitutional"):
        logger.info("Status: PASSED")
        exit_code = 0
    else:
        logger.info("Status: FAILED")
        exit_code = 1

    if final_state:
        logger.info("Total iterations: %d", final_state.get("retry_count", 0))
        logger.info("Final exit code: %d", final_state.get("last_exit_code", -1))
        fb = final_state.get("critic_feedback")
        if fb:
            logger.info("Feedback: %s", fb)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
