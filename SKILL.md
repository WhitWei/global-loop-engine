---
name: global-loop-engine
description: 全局 Loop Engineering 引擎，强制 Agent 执行时使用基于 LangGraph 的反思和验证循环机制。
---

# 全局 Loop Engineering (Global Loop Engine)

此 Skill 强制 Agent 在执行任何核心开发或复杂任务时，必须通过内部循环和严格校验后才可提交结果，遵循“热冷分离”原则并执行硬核校验。

## 核心节点机制
1. **复杂性评分 (ComplexityScorerNode)**：评估当前任务的代码逻辑复杂度和受影响文件范围。
2. **成本估算 (CostEstimatorNode)**：根据复杂度和循环执行预估执行成本（Token / 时间）。
3. **严重验证器 (CriticNode)**：
   - **硬核校验**：基于子进程 (`subprocess`) 调用真实测试框架（如 `pytest`）、静态分析（如 `pylint` / `grep`）。
   - **热冷分离**：严格保存并分离运行时结果（如 `Exit Code`）与代码状态快照（如 `Git Diff`），以供客观评估判断是否能够通过验收。

## 执行准则
当此 Skill 生效时，Agent 不得直接输出"我完成了"，必须首先运行配套的引擎：
```bash
# 方式一（推荐）：通过 CLI 入口
loop-engine --task "<当前任务描述>"

# 方式二：直接调用脚本
python scripts/loop_engine.py --task "<当前任务描述>"
```
引擎若给出错误（非 `0` exit code）或处于尚未满足验收退出条件时，必须根据反馈继续修复代码，直到验证通过（退出节点）才可进行汇报。
