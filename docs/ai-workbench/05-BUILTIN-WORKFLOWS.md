# AI Workbench 默认资产策略

## 1. 产品定位

Workflow 主要用于保存用户自己的可重复流程。AI Client 本身已经具备规划和多步工作能力，因此系统不再提供固定 Built-in Workflow，也不提供 Built-in Skill。

新 Runtime / 新 Workspace 在用户尚未创建资产时，Skill Catalog 与 Workflow Catalog 均为空。

结构：

```text
Skill / ModelAction
  ↓ success
Artifact
  ↓ success
Human Approval
```

模型节点输出先保存为当前 Run 的 JSON Artifact，再交给用户确认。这样 Approval 审核的是已经固化、可追溯的结果，而不是一段瞬时聊天文本。

历史示例 Workflow 的 `metadata` 曾包含：

- `acceptance[]`：验收条件；
- `example_run.inputs`：示例输入；
- `example_run.expected_states`：预期生命周期；
- `example_run.artifact_id`：预期 Artifact。

---

## 2. 历史默认资产（已移除）

资源：

```text
Prompt   spec-development
Skill    spec-development
Workflow project-development
Artifact project-delivery-report
```

以上资源仅作为历史记录，不再由产品运行时自动注册；需要类似流程时，由用户或 AI 显式创建对应 Skill / Workflow。

示例输入：

```json
{"feature":"next project iteration"}
```

实际方法论：

```text
Requirements
→ Design
→ Tasks
→ Implementation
→ Test & Acceptance
→ Artifact
→ Human Approval
```

验收重点：需求和非目标明确；关键设计先记录再编码；实现有真实测试结果；交付报告明确区分已验证、未验证和剩余风险。

---

## 3. Skill 策略

以下历史工程能力不再作为默认 Skill 自动注入：

```text
Project Analysis
Bug Investigation
Legacy Reverse Engineering
Code Review
Release Validation
```

用户可以自行创建所需 Skill，并按当前 Workspace 的实际需求组合 Workflow。

---

## 4. 测试生命周期

Workflow Engine 仍通过显式测试 fixture 验证以下 Contract：

```text
workflow_start
  -> waiting_model

continue model action success
  -> Artifact written
  -> waiting_approval

human approval
  -> succeeded
```

该 Contract 由 `tests/test_workbench_runs.py` 显式创建测试 Skill / Workflow 后执行，不依赖产品默认资产。
