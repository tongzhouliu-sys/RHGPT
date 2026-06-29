# RHCLOUD Mobile 端开发规划

## Context

当前 RHCLOUD 前端是单页 Next.js 应用（`app/page.tsx`，641 行），仅适配桌面端。用户需要在现有架构基础上增加移动端 Bottom Tab 导航（Chat / Task / Files / Settings 四个页面），最大化复用现有 `lib/`、组件和样式，后端零改动。

---

## 1. 整体架构：单路由双布局 + 共享 AppContext

**核心原则**：同一路由 `/`，客户端根据视口宽度动态挂载 `<DesktopLayout>` 或 `<MobileLayout>`，所有状态由新建的 `AppContext` 集中管理。

- **为什么不用独立路由**（`/mobile`、`/?tab=chat`）：切换路由会销毁 React 组件树，导致正在运行的 SSE stream 中断。Tab 内导航必须是纯组件内切换，不得触发路由跳转。
- **为什么不纯用 CSS 响应式**：移动端的信息架构（Bottom Tab）与桌面端（单页滚动）有结构性差异，无法用媒体查询表达。
- **静态导出兼容**：`output: "export"` 决定了所有路由判断必须在客户端完成，与现有 localStorage 主题检测模式一致。

---

## 2. 页面结构设计

四个 Tab 均为组件内状态切换，无独立路由：

| Tab | 职责 |
|-----|------|
| **Chat** | 输入问题、创建任务、展示当前配置的模型（只读）、运行中任务摘要、历史记录列表 |
| **Task** | 任务执行详情：Step 列表、每步状态/Provider/Model/Streaming 内容、SSE 实时更新 |
| **Files** | 下载 Merged.md / Steps ZIP / Context JSON，预留历史文件扩展 |
| **Settings** | 配置默认使用的模型（仅影响新任务）、Pipeline 选择、主题切换、Provider 状态 |

**模型选择不在 Chat 页面**，Chat 仅展示只读的模型清单，Settings 负责配置。

---

## 3. Responsive 方案

```
useIsMobile() hook
  ├── server render → false（避免 hydration mismatch）
  ├── client mount → window.innerWidth < 768
  └── resize listener → 实时响应

page.tsx（thin dispatcher）
  ├── isMobile === true  → <MobileLayout>
  └── isMobile === false → <DesktopLayout>（现有逻辑）
```

断点与现有 CSS 保持一致（768px）。不增加 Tailwind、不修改现有媒体查询。

---

## 4. 前端目录规划

```
frontend/app/
├── context/
│   └── AppContext.tsx          ← 新增：共享状态（替代 page.tsx 中的所有 useState/useRef）
├── hooks/
│   └── useIsMobile.ts          ← 新增：视口检测
├── layouts/
│   ├── DesktopLayout.tsx       ← 新增：现有 page.tsx 主体迁移至此
│   └── MobileLayout.tsx        ← 新增：Tab 路由器 + TabBar
├── mobile/
│   └── tabs/
│       ├── ChatTab.tsx         ← 新增
│       ├── TaskTab.tsx         ← 新增
│       ├── FilesTab.tsx        ← 新增
│       └── SettingsTab.tsx     ← 新增
├── components/
│   ├── AgentLogo.tsx           ← 不变（直接复用）
│   ├── ModelProbe.tsx          ← 最小改动：增加 alwaysOpen?: boolean prop
│   ├── AvatarScene.tsx         ← 不变（仅在 DesktopLayout 使用，移动端不渲染）
│   └── shared/
│       ├── NodeCard.tsx        ← 新增：从 page.tsx ~500-610 行提取
│       ├── ProgressBanner.tsx  ← 新增：从 page.tsx ~463-498 行提取
│       ├── ModelPillGrid.tsx   ← 新增：从 page.tsx ~344-391 行提取
│       └── TabBar.tsx          ← 新增：Bottom Tab 导航栏
├── globals.css                 ← 扩展：增加约 80 行 mobile nav CSS
├── layout.tsx                  ← 微改：添加 viewportFit: "cover"
└── page.tsx                    ← 重构：精简为 thin dispatcher（~30 行）

frontend/lib/
├── api.ts    ← 不变
├── sse.ts    ← 不变
├── mock.ts   ← 不变
└── markdown.ts ← 不变
```

**变更汇总**：新增 ~16 个文件，修改 3 个现有文件（page.tsx、globals.css、layout.tsx），lib/ 目录完全不变。

---

## 5. 状态管理方案

**方案**：React Context + useReducer（无第三方库）

`AppContext.tsx` 接管 `page.tsx` 中所有状态，分两个 Context 避免不必要的重渲染：

```
AppSettingsContext（稳定，低频变更）
  ├── theme, pipeline
  ├── availableModels, selectedModels
  └── actions: toggleTheme, toggleModel, selectOnlyAPI, selectAllModels, setPipeline

AppJobContext（volatile，SSE 每帧更新）
  ├── question, phase, jobId
  ├── order, nodes, expandedKeys
  ├── banner, concurrentBusy
  └── actions: setQuestion, run, cancel, toggleExpand

chatHistory（持久化到 localStorage，最多 50 条）
  └── { jobId, question, timestamp, pipeline, finalStatus }
```

- SSE stream（`abortRef` + `streamEvents`）归属于 `AppJobContext`，Tab 切换不会触发 unmount，stream 持续运行。
- Settings 持久化：`selectedModels` → `localStorage.rh_selected_models`，`theme` → `localStorage.rh_theme`（与现有 key 一致）。

---

## 6. 与当前系统集成方式

### lib/ 完全复用，零改动

| 函数 | 调用位置 |
|------|----------|
| `createJob()` | AppJobContext → `run()` |
| `streamEvents()` | AppJobContext → `run()`，stream 生命周期与 Context 绑定 |
| `cancelJob()` | AppJobContext → `cancel()` |
| `getJob()` | AppJobContext → `run()` 轮询降级 |
| `downloadExport()` | FilesTab.tsx 按钮 handler |
| `fetchProviders()` | AppSettingsContext 初始化 |
| `renderMarkdown()` | NodeCard.tsx（共享组件） |

### SSE 生命周期保障

- `visibilitychange` 监听器：App 从后台切回前台且 `phase === "running"` 时，检测 SSE 是否仍活跃，必要时重连（现有 `streamEvents` 已有 5 次重连 + polling 降级）。
- Tab 切换：纯组件内 state 变更，不触发 Context 重新挂载，stream 不中断。

---

## 7. UI/UX 建议

### TabBar

```css
.mobile-shell { display: flex; flex-direction: column; height: 100dvh; }
.mobile-content { flex: 1; overflow-y: auto; }
.mobile-tab-bar {
  flex-shrink: 0;
  padding-bottom: env(safe-area-inset-bottom);
  height: calc(56px + env(safe-area-inset-bottom));
  background: var(--panel-solid);
  border-top: 1px solid var(--border);
  backdrop-filter: blur(20px);
}
```

- 使用 `flex` 布局而非 `position: fixed`，规避 iOS Safari 键盘弹出时的定位 bug。
- `100dvh`（dynamic viewport height）：键盘弹出时内容区自动收缩。
- Tab 图标：Message（Chat）、Timeline（Task，running 时有绿色脉冲点）、Download（Files，done 时有蓝色 badge）、Gear（Settings）。
- 所有可点击区域最小 44px（Apple HIG）。

### 各 Tab 重点

- **Chat**：Textarea 置顶，全宽提交按钮，只读模型图标横向滚动条，运行中任务摘要卡（点击跳转 Task）。
- **Task**：复用 `NodeCard.tsx`，单列布局（已有 768px 媒体查询支持），`ProgressBanner.tsx` 吸顶。
- **Files**：三个大型下载按钮（最小 56px 高），未完成时 disabled。
- **Settings**：`ModelPillGrid.tsx` 全展示，`ModelProbe.tsx` 以 `alwaysOpen` 模式常驻展示（不用 hover popover）。
- **AvatarScene**：移动端不渲染，仅保留在 `DesktopLayout`。

---

## 8. 分阶段开发计划

### Phase 0 — 前置重构（2–3 天，用户无感知）

**目标**：将 `page.tsx` 的状态与渲染逻辑分离，为后续移动端提供基础。

1. 从 `page.tsx` 提取 `NodeCard.tsx`、`ProgressBanner.tsx`、`ModelPillGrid.tsx`
2. 创建 `AppContext.tsx`，将所有 `useState`/`useRef`/`useCallback` 迁移至此
3. `page.tsx` → 只包含 `AppContextProvider` + `DesktopLayout`
4. 创建 `useIsMobile.ts`，`page.tsx` 条件渲染 `<DesktopLayout>` 或占位符
5. 全程用 `NEXT_PUBLIC_USE_MOCK=1` 验证桌面行为不变

**检验标准**：桌面端功能、SSE streaming、导出、主题切换与重构前 100% 一致。

### Phase 1 — 移动端 Shell + Tab 导航（2 天）

1. `TabBar.tsx`：4 个图标 + 标签，active 状态，运行指示点，done badge
2. `MobileLayout.tsx`：`activeTab` state，dvh 布局，4 个 Tab stub
3. CSS：`.mobile-shell`、`.mobile-tab-bar`、`.tab-item`、`.tab-badge`、safe-area 适配

**检验标准**：375px 视口显示 Bottom Tab，1280px 视口显示桌面布局，切换无闪烁。

### Phase 2 — Chat Tab（2–3 天）

1. Textarea + 提交/取消按钮，绑定 AppJobContext
2. 运行中任务摘要卡（Phase badge + AgentLogo + "查看详情"按钮）
3. 只读模型图标条（横向滚动，点击跳转 Settings Tab）
4. 历史记录列表（localStorage chatHistory，最多 50 条）

### Phase 3 — Task Tab（3–4 天）

1. 吸顶 mini header（Job ID、状态 badge、取消按钮）
2. ProgressBanner 共享组件
3. NodeCard relay 时间线（完整功能对齐桌面端，单列布局）
4. 空状态（未提交时的引导提示）

### Phase 4 — Files Tab（1 天）

1. 三个下载按钮：merged / steps / json，调用 `downloadExport()`
2. disabled 状态（job 未完成时）
3. mock 模式提示（与桌面端一致）

### Phase 5 — Settings Tab（1–2 天）

1. Pipeline 选择器（从 Chat 移至此处）
2. ModelPillGrid（复用共享组件）
3. API Only / All Models 快捷按钮
4. 主题切换
5. ModelProbe alwaysOpen 模式（Provider 状态列表常驻展示）

### Phase 6 — 打磨 & QA（2–3 天）

1. iOS safe-area、`env(safe-area-inset-bottom)` 全面验证
2. 软键盘弹出时的布局适配（`100dvh`）
3. `visibilitychange` SSE 重连逻辑
4. 多设备测试：iPhone SE（375px）、iPhone 15 Pro（393px）、Galaxy S23（360px）
5. 浏览器测试：Safari iOS、Chrome Mobile、Samsung Internet

**总工时估算**：约 14–18 个工作日

---

## 9. 风险与注意事项

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| AppContext re-render 性能 | SSE 高频更新导致全树重渲染 | 拆分 AppSettingsContext / AppJobContext，对 TabBar 和 Settings tab 使用 React.memo |
| iOS Safari fixed 定位 bug | 键盘弹出时 TabBar 位置错乱 | 改用 flex 布局（非 `position: fixed`）+ `100dvh` |
| Phase 0 重构回归 | DesktopLayout 行为偏差 | 每次提取后用 mock mode 全量回归测试，分步 PR 合并 |
| SSE 后台 Tab 断连 | 移动浏览器限速后台 tab | `visibilitychange` 监听 + 现有 5 次重连降级逻辑（已在 lib/api.ts 实现） |
| chatHistory 跨 Session 失效 | 旧 jobId 在后端已被清理 | history 仅存元数据（question/timestamp/status），不重新 fetch job 数据 |
| ModelProbe hover vs touch | touch 设备无 mouseEnter | Settings tab 使用 `alwaysOpen` prop 常驻展示，桌面端不变 |

---

## 10. 最终推荐方案

**单路由双布局 + 共享 AppContext**

- `lib/api.ts`、`lib/sse.ts`、`lib/mock.ts`、`lib/markdown.ts` **完全不变**
- 后端 **零改动**
- 新增 npm 依赖 **零个**
- 桌面端现有功能 **100% 保留**
- SSE stream 在 Tab 切换时 **不中断**
- 从 Phase 0 开始可独立合并，任意 Phase 可暂停而不影响桌面端

---

## 验证方案

- `NEXT_PUBLIC_USE_MOCK=1` 模式：无需后端即可验证全部 UI 流程（现有 mock 模拟完整 7 事件序列）
- Phase 0 完成后：桌面端回归（手动 + `npm run build` 无报错）
- 每个 Phase 完成后：在 375px 和 1280px 视口各自验证
- 最终集成测试：连接真实后端，在 iOS Safari 上运行完整任务流程（提交 → SSE 实时更新 → 下载导出）
