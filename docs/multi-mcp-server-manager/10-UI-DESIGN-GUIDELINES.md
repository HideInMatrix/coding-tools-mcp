# UI 设计规范

本文档定义 Coding Tools MCP 桌面端统一 UI 规范。后续新增或重构 Vue 页面、组件、按钮、导航和表单时，必须遵守本规范。

## 1. 基础原则

- 展示层使用 `pywebview + Vue + TypeScript + Vite`。
- 组件优先复用 `shadcn-vue / reka-ui / lucide-vue`。
- 视觉风格采用紧凑、低对比度、轻层级的管理台设计。
- 统一使用 Design Token，不在业务组件中随意硬编码颜色。
- 默认基础字号为 `14px`，页面标题约 `20px`。
- 基础圆角采用 `8px` 左右，常规控件高度为 `32px`。

## 2. Button 按钮规范

所有普通按钮的内容必须上下、左右居中。

统一要求：

```text
display: inline-flex
align-items: center
justify-content: center
gap: 8px
```

对应 Tailwind / UnoCSS 语义：

```text
inline-flex items-center justify-center gap-2
```

### 2.1 图标 + 文字

按钮同时包含图标和文字时：

- 图标与文字必须位于同一行。
- 图标与文字整体水平居中。
- 图标与文字整体垂直居中。
- 图标与文字间距固定使用 `gap-2`，即 `8px`。
- 不允许通过 `margin-left`、`margin-right` 单独制造图标间距。
- 图标建议使用 `14px ~ 16px` 的 Lucide 图标。
- 图标不得被 flex 压缩。

推荐结构：

```vue
<Button size="sm">
  <Plus :size="14" />
  新建
</Button>
```

禁止出现图标、文字因 line-height 或额外 margin 导致上下错位的实现。

### 2.2 原生 button

项目中如果必须使用原生 `<button>`，也必须遵守与 shadcn `<Button>` 相同的对齐规则：

```css
button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
```

因此，原生按钮和组件按钮在内容布局上不得出现两套不同规则。

### 2.3 Primary Action 主操作按钮

页面中的主操作，例如“新建”“创建”“保存并启动”等，必须使用明确的 Primary Action 视觉，不能退化成透明背景或纯文本按钮。

统一要求：

```text
background: primary
color: primary-foreground
border: primary
display: inline-flex
flex-direction: row
align-items: center
justify-content: center
gap: 8px
white-space: nowrap
```

对应 Tailwind / UnoCSS 语义：

```text
inline-flex flex-row items-center justify-center gap-2 whitespace-nowrap
bg-primary text-primary-foreground border border-primary
```

要求：

- 主操作按钮必须有可识别的实色底色。
- 图标与文字必须保持同一行，不允许出现图标在上、文字在下的布局。
- 图标与文字整体必须在按钮内部上下左右居中。
- 图标与文字间距固定为 `gap-2 = 8px`。
- 文字不得因为按钮宽度或样式覆盖发生换行。
- 如果组件库默认样式在实际 WebView 中没有正确生效，必须通过项目级样式保证上述规则，而不能接受视觉退化。

推荐结构：

```vue
<Button size="sm" class="primary-action-button">
  <Plus :size="14" />
  新建
</Button>
```

## 3. Sidebar 菜单规范

Sidebar 菜单是 Button 水平对齐规则的明确例外。

菜单项本身仍保持：

```text
display: flex
align-items: center
gap: 8px
```

但内容必须居左：

```text
justify-content: flex-start
```

当前标准菜单项语义：

```text
group flex h-8 items-center justify-start gap-2 rounded-md px-2.5 text-xs font-normal
transition-colors hover:bg-secondary/55 hover:text-foreground
active:bg-secondary/60 active:text-foreground
```

要求：

- 菜单项高度 `32px`。
- 左右 padding 为 `10px`，对应 `px-2.5`。
- 图标与文字间距固定 `gap-2`。
- 图标与文字垂直居中。
- 整组内容靠左排列。
- 菜单文字使用 `12px / text-xs / font-normal`。
- 图标建议 `16px`。
- hover 使用 `secondary/55`。
- active 使用 `secondary/60`。

## 4. 表单控件规范

- Input、Select、普通 Button 默认高度保持在 `32px` 左右。
- 同一行控件必须保持垂直对齐。
- 表单 label 使用较弱字号和权重，不与页面标题抢层级。
- Focus 状态统一使用 `ring` token，不新增品牌色描边。

## 5. 间距规范

- 图标和按钮文字：`gap-2 = 8px`。
- 紧凑操作按钮之间：通常 `8px`。
- 表单字段间：通常 `12px`。
- Card / Panel 内部 padding：通常 `14px ~ 16px`。
- 页面主要区块间：通常 `20px`。

## 6. 组件实现要求

- 新按钮优先使用 `@/components/ui/button`。
- 不允许在单个页面中重复定义新的按钮布局基础规则。
- `.nav-item` 可以覆盖 `justify-content` 为 `flex-start`，除此之外普通 Button 默认保持居中。
- 新增组件前先检查现有 UI primitive 是否可复用。
- 所有 UI 修改都应先检查本文件，避免形成局部样式规范。

