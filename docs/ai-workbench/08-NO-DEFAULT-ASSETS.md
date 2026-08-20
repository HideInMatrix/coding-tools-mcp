# AI Workbench 默认资产移除决策

## 状态

已决定并进入实施。

## 决策

AI Workbench 不再内置任何默认 Skill，也不再内置任何默认 Workflow。

运行时启动后：

- Skill Catalog 只包含用户持久化的 Global Skill。
- Workflow Catalog 只包含 Workspace 中用户创建或导入的 Workflow。
- 不再注入 `project-analysis`、`bug-investigation`、`reverse-engineering`、`code-review`、`spec-development`、`release-validation` 等内置 Skill。
- 不再注入 `project-development` 默认 Workflow。

## 实现约束

1. `build_skill_registry()` 不注册任何 Built-in Skill。
2. `build_workflow_registry()` 不注册任何 Built-in Workflow。
3. 删除用户资产后，不得自动恢复同名默认资产。
4. 测试以“空默认目录 + 用户资产按需加入”为基线。

## 验收条件

- 新 Runtime 的 `skill_list` 在没有用户 Skill 时返回空列表。
- 新 Workspace 的 `workflow_list` 在没有用户 Workflow 时返回空列表。
- Global Skill 和 Workspace Workflow 的增删改查继续正常。
