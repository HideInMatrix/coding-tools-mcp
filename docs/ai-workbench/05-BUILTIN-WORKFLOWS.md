# AI Workbench 默认 Workflow

## 1. 产品定位

Workflow 主要用于保存用户自己的可重复流程。AI Client 本身已经具备规划和多步工作能力，因此系统不再提供多套固定 Built-in Workflow。

默认只保留一套 `project-development`，用于新用户示例和项目开发起点。

结构：

```text
Skill / ModelAction
  ↓ success
Artifact
  ↓ success
Human Approval
```

模型节点输出先保存为当前 Run 的 JSON Artifact，再交给用户确认。这样 Approval 审核的是已经固化、可追溯的结果，而不是一段瞬时聊天文本。

默认 Workflow 的 `metadata` 包含：

- `acceptance[]`：验收条件；
- `example_run.inputs`：示例输入；
- `example_run.expected_states`：预期生命周期；
- `example_run.artifact_id`：预期 Artifact。

---

## 2. Project Development

资源：

```text
Prompt   spec-development
Skill    spec-development
Workflow project-development
Artifact project-delivery-report
```

目标：抽象当前项目已经稳定采用的开发方式，在修改代码前明确需求与设计，按计划实施，执行真实测试并形成验收证据，最终由用户确认。

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

## 3. 其他工程能力

以下能力仍然保留为 Prompt / Skill，但不再作为默认 Workflow：

```text
Project Analysis
Bug Investigation
Legacy Reverse Engineering
Code Review
Release Validation
```

用户可以从这些 Skill 中选择任意组合，构建自己的 Workspace Workflow。

---

## 4. Example Run 生命周期

默认 Workflow 必须通过以下自动化 Contract：

```text
workflow_start
  -> waiting_model

continue model action success
  -> Artifact written
  -> waiting_approval

human approval
  -> succeeded
```

该 Contract 由 `tests/test_workbench_runs.py` 实际执行，而不是只作为文档示例。
