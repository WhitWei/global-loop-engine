# Global Loop Engine

![PyPI - Python Version](https://img.shields.io/pypi/pyversions/langgraph)
![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)

> **🌍 [中文说明 (Chinese Documentation)](README_zh-CN.md)**

**Global Loop Engine** is a robust, LangGraph-based execution and validation layer designed for LLM coding agents operating in IDE or CLI environments. It acts as an uncompromising gatekeeper, enforcing a strict **"think → execute → critique → refine"** cycle to ensure 100% deterministic code output and completely eliminate LLM hallucination or "cheat-to-pass" behaviors.

## ✨ Core Features

*   **🛡️ Strict Sandbox Validation**: Employs real subprocess verification (e.g., `pytest`, `grep`) to validate LLM outputs. It refuses to accept "mocked" data or verbal claims of success.
*   **🧊 Hot/Cold State Separation**: Keeps context windows extremely small by passing only diffs and execution exit codes (cold state) to the LLM, preventing token explosion on large projects.
*   **🧬 Anti-Hallucination Guardrails**: Features a dedicated `CriticNode` that cross-references assertions with raw logs. If an LLM fabricates test results, the engine instantly intercepts it.
*   **⏱️ Atomic Git Snapshots**: Automatically commits state checkpoints before execution. If the LLM enters a destructive spiral, the engine gracefully rolls back the entire workspace.
*   **⚙️ Dangerous Command Filter**: Uses Regex-based whitelists to instantly block destructive shell commands (`rm -rf /`, `DROP TABLE`, etc.) before they hit your terminal.

## 🏗 Architecture

```mermaid
graph TD
    classDef llm fill:#f9f2f4,stroke:#d67a8b,stroke-width:2px,color:#333;
    classDef validator fill:#e6f7ff,stroke:#69b1ff,stroke-width:2px,color:#333;
    classDef storage fill:#f6ffed,stroke:#95de64,stroke-width:2px,color:#333;

    User([User Request]) --> A[LangGraph Orchestrator]
    
    subgraph Global Loop Engine
        A -->|Dispatch| C[AgentNode<br>Code Generation]:::llm
        C -->|Propose Changes| B{CriticNode<br>Sandbox Validator}:::validator
        B -->|Fake Data / Missing Evidence| C
        B -->|100% Verified| D[StateSaver<br>Git Checkpoint & SQLite]:::storage
    end

    D --> Success([Final Output])
```

## 🚀 Quick Start

### 1. Installation

Choose one of the following methods to install (supports macOS, Windows, and Linux):

#### Method A: Install via PyPI (Recommended)
```bash
pip install global-loop-engine
```

#### Method B: Download Release Asset (Offline / Custom version)
1. Go to the [Releases](https://github.com/WhitWei/global-loop-engine/releases) page.
2. Download the universal wheel package: `global_loop_engine-X.Y.Z-py3-none-any.whl`. (Note: macOS, Windows, and Linux users all download this same universal file).
3. Run the installation:
   ```bash
   pip install global_loop_engine-X.Y.Z-py3-none-any.whl
   ```

#### Method C: Developer Mode (Clone and build)
```bash
git clone https://github.com/WhitWei/global-loop-engine.git
cd global-loop-engine
cp .env.example .env
pip install -e .
```

### 2. Configuration

Open the `.env` file and configure your database path and retry thresholds (defaults are provided out-of-the-box).

### 3. Usage

You can invoke the engine directly via the global CLI command `loop-engine`:

```bash
# Run a task with strict single-pass validation (no retries)
loop-engine --task "Refactor the authentication module" --mode fast

# Run a task with the full LangGraph reflexion loop (auto-retries until success)
loop-engine --task "Implement Token Bucket algorithm and pass tests" --mode loop
```

## 🧠 Architecture Overview

The engine orchestrates agents through a compiled **StateGraph**:

1.  **ComplexityScorerNode**: Evaluates the task difficulty to determine maximum allowed retries.
2.  **SanitizeNode**: Pre-execution security filter blocking malicious shell commands.
3.  **ExecuteNode**: Runs the target code or tests in the shell, capturing `stdout`, `stderr`, and `exit_code`.
4.  **CriticNode**: The "Judge". Parses logs, detects hallucinations, increments retry counters, and routes the state.
5.  **RefineNode**: If tests fail, feeds the exact error signatures back to the LLM to patch the code.

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to set up your local development environment, run tests, and submit pull requests.

## 📄 License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
