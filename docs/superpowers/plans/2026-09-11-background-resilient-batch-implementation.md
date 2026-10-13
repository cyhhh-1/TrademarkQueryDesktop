# 商标查询助手后台稳定执行 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让批量商标查询在不抢前台的情况下遍历多页、逐项容错、自动重试并始终输出结果。

**Architecture:** 保留真实 Edge + UIA 登录会话，新增仅限本机的 DevTools 渲染层截图和真实页码导航；任务引擎改为首轮加一次重试的容错队列，并把执行结果持续写入工作簿。Tauri 前端增加用户暂停、原生窗口控制权限和系统完成通知。

**Tech Stack:** Python 3.12、pywinauto、Pillow、openpyxl、Vue 3、TypeScript、Tauri 2、Rust、Vitest、pytest

**Spec:** `docs/superpowers/specs/2026-09-11-background-resilient-batch-design.md`

## Global Constraints

- 不修改 `D:/4.29/BuddyAgent_2026.8.12`。
- 任务运行期间不自动暂停整批任务。
- 单条失败首轮跳过并在末尾自动重试一次。
- 查询期间不得调用前台聚焦操作。
- 每条处理后保存工作副本和检查点。
- 完成后不关闭 Edge，并发送 Windows 通知。

---

### Task 1: 锁定回归行为

**Files:**
- Modify: `worker/tests/test_task_engine.py`
- Modify: `worker/tests/test_uia_adapter.py`
- Modify: `worker/tests/test_cnipa_parser.py`
- Modify: `apps/desktop/src/tests/App.test.ts`
- Create: `apps/desktop/src-tauri/tests/window_capabilities.rs`

**Interfaces:**
- Consumes: 现有 `TaskEngine`、`UIAPagePort`、`CnipaClient` 和 Vue 事件模型。
- Produces: 首轮跳过/二轮重试、多页导航、无前台聚焦、手动暂停和窗口权限的失败测试。

- [ ] **Step 1: 写任务引擎失败测试**

覆盖三行中第二行首轮抛错、第三行仍执行、第二行末尾重试成功，以及最终失败仍生成结果的场景。

- [ ] **Step 2: 写 UIA 与分页失败测试**

用模拟分页控件覆盖数字、`>`、`>>`，并断言截图路径不调用 `window.set_focus()`。

- [ ] **Step 3: 写前端和 Tauri 权限失败测试**

断言暂停按钮发出 `pause` 命令，完成事件触发通知，并断言能力文件包含 minimize、toggle-maximize、close 和 start-dragging。

- [ ] **Step 4: 运行定向测试确认 RED**

Run: `worker/.venv/Scripts/python.exe -m pytest worker/tests/test_task_engine.py worker/tests/test_uia_adapter.py worker/tests/test_cnipa_parser.py -q`

Run: `npm test -- --run` in `apps/desktop`

Expected: 新增测试因缺少容错队列、后台捕获、暂停命令和窗口权限而失败。

### Task 2: 后台标签和渲染层截图

**Files:**
- Create: `worker/src/trademark_worker/browser/capture.py`
- Modify: `worker/src/trademark_worker/browser/uia.py`
- Modify: `worker/tests/test_uia_adapter.py`

**Interfaces:**
- Consumes: Edge HWND、UIA 元素矩形和滚动接口。
- Produces: `BackgroundPageCapture.capture(url_hint) -> Image.Image` 与不抢焦点的标签选择/截图实现。

- [x] **Step 1: 实现本机 DevTools 后台页面捕获**

使用随机回环端口连接专用 Edge，从页面渲染层获取整页 PNG；不启用 WebDriver，并校验空图、黑图和无效数据。

- [x] **Step 2: 移除所有 `set_focus` 调用**

任务开始时自动最小化专用 Edge；整页和结果行截图按 UIA 文档坐标与渲染图比例裁剪。

- [ ] **Step 3: 加固标签定位**

标签切换后等待目标 URL/标题稳定；瞬时空 URL 返回 `WAITING_SITE`，仅实际 SSO URL识别为登录页。

- [ ] **Step 4: 运行 UIA 测试确认 GREEN**

Run: `worker/.venv/Scripts/python.exe -m pytest worker/tests/test_browser_state.py worker/tests/test_uia_adapter.py -q`

Expected: PASS。

### Task 3: 真实页码导航

**Files:**
- Modify: `worker/src/trademark_worker/browser/uia.py`
- Modify: `worker/src/trademark_worker/cnipa/client.py`
- Modify: `worker/tests/test_uia_adapter.py`
- Modify: `worker/tests/test_cnipa_parser.py`

**Interfaces:**
- Consumes: 结果页 UIA 文本和分页控件。
- Produces: `current_page() -> int`、可靠的 `total_pages()` 与 `go_to_page(page_number)`。

- [ ] **Step 1: 从页面文本解析当前页和总页数**

支持 `页数：1 / 5` 的空格和全角标点变体。

- [ ] **Step 2: 实现数字与箭头翻页**

优先目标数字，不可见时匹配 `>`/`<`/`>>`/`<<` 及可访问名称，并在点击后验证页码和表格指纹。

- [ ] **Step 3: 移除仅依赖 `_page_number` 的状态**

搜索、打开详情及截图返回结果页后均重新读取真实当前页。

- [ ] **Step 4: 运行分页测试确认 GREEN**

Run: `worker/.venv/Scripts/python.exe -m pytest worker/tests/test_cnipa_parser.py worker/tests/test_uia_adapter.py -q`

Expected: PASS。

### Task 4: 容错批处理与执行记录

**Files:**
- Modify: `worker/src/trademark_worker/models.py`
- Modify: `worker/src/trademark_worker/task_engine.py`
- Modify: `worker/src/trademark_worker/checkpoint.py`
- Modify: `worker/src/trademark_worker/workbook/writer.py`
- Modify: `worker/tests/test_task_engine.py`
- Modify: `worker/tests/test_workbook_writer.py`

**Interfaces:**
- Consumes: `LookupJob` 列表和每条查询异常。
- Produces: 两轮队列、尝试次数、最终失败结果和“查询执行记录”工作表。

- [ ] **Step 1: 实现首轮和重试轮**

首轮异常加入重试队列并继续；第二轮异常生成 `failed` 结果，不发自动暂停事件。

- [ ] **Step 2: 每条结果即时落盘**

成功写原模板列；成功、未找到和失败全部更新执行记录工作表，再原子保存检查点。

- [ ] **Step 3: 增加协作式用户暂停**

`TaskEngine.pause()` 设置标志，安全边界发出 `paused` 且 reason 为 `USER_REQUESTED`；`resume` 继续未完成队列。

- [ ] **Step 4: 运行引擎和工作簿测试确认 GREEN**

Run: `worker/.venv/Scripts/python.exe -m pytest worker/tests/test_task_engine.py worker/tests/test_workbook_writer.py -q`

Expected: PASS。

### Task 5: 运行时命令与浏览器生命周期

**Files:**
- Modify: `worker/src/trademark_worker/runtime.py`
- Modify: `worker/tests/test_runtime_browser.py`
- Modify: `worker/tests/test_worker_commands.py`

**Interfaces:**
- Consumes: 前端 `pause`、`resume`、`cancel` 命令。
- Produces: 不自动关闭 Edge 的持久 Worker 和可恢复用户暂停。

- [ ] **Step 1: 接入 pause 命令**

运行任务时仅用户命令能够进入暂停状态。

- [ ] **Step 2: 验证任务完成不终止 Edge**

增加多任务连续运行和任务完成后浏览器 PID 仍存在的测试。

- [ ] **Step 3: 运行命令测试确认 GREEN**

Run: `worker/.venv/Scripts/python.exe -m pytest worker/tests/test_runtime_browser.py worker/tests/test_worker_commands.py -q`

Expected: PASS。

### Task 6: 客户端任务控制和通知

**Files:**
- Modify: `apps/desktop/src/domain/types.ts`
- Modify: `apps/desktop/src/domain/taskState.ts`
- Modify: `apps/desktop/src/composables/useTrademarkTask.ts`
- Modify: `apps/desktop/src/components/TaskActions.vue`
- Modify: `apps/desktop/src/components/TaskProgress.vue`
- Modify: `apps/desktop/src/App.vue`
- Modify: `apps/desktop/src/services/desktop.ts`
- Modify: `apps/desktop/src/tests/App.test.ts`
- Modify: `apps/desktop/src/tests/taskState.test.ts`

**Interfaces:**
- Consumes: Worker 的 attempt、deferred、completed 事件。
- Produces: 用户暂停/继续、轮次与翻页进度、完成通知。

- [ ] **Step 1: 更新状态模型和进度文案**

显示首轮/重试、当前页、失败和最终完成状态，不再展示运行中重新检测。

- [ ] **Step 2: 接入暂停/继续按钮**

运行时启用暂停，用户暂停后启用继续；系统异常不会进入暂停界面。

- [ ] **Step 3: 接入非抢焦点 Windows 通知**

由 Tauri 后端在完成、部分失败和取消事件时显示系统通知，前端窗口保持原激活状态。

- [ ] **Step 4: 运行前端测试确认 GREEN**

Run: `npm test -- --run` in `apps/desktop`

Expected: PASS。

### Task 7: 原生窗口修复

**Files:**
- Modify: `apps/desktop/src/components/AppHeader.vue`
- Modify: `apps/desktop/src/styles/app.css`
- Modify: `apps/desktop/src-tauri/capabilities/default.json`
- Modify: `apps/desktop/src-tauri/tests/window_capabilities.rs`

**Interfaces:**
- Consumes: Tauri 2 window API。
- Produces: 可拖动标题栏和可用的最小化、最大化、关闭按钮。

- [ ] **Step 1: 放行窗口命令**

加入 `core:window:allow-minimize`、`core:window:allow-toggle-maximize`、`core:window:allow-close` 和 `core:window:allow-start-dragging`。

- [ ] **Step 2: 明确实现拖动和双击最大化**

标题栏主区域 pointerdown 调用 `startDragging()`，双击调用 `toggleMaximize()`；控件区域阻止拖动。

- [ ] **Step 3: 运行 Rust 与前端测试确认 GREEN**

Run: `cargo test` in `apps/desktop/src-tauri`

Run: `npm test -- --run` in `apps/desktop`

Expected: PASS。

### Task 8: 全量验证与 0.1.5 交付

**Files:**
- Modify: version files currently containing project version `0.1.4`
- Modify: `README.md`
- Modify: `.learnings/LEARNINGS.md`
- Generate: `release/商标查询助手_0.1.5_x64-setup.exe`
- Generate: `release/商标查询助手_0.1.5_release.zip`

**Interfaces:**
- Consumes: 全部实现和测试。
- Produces: 安装版、便携发布包和验证记录。

- [ ] **Step 1: 运行全量自动化验证**

Run: `worker/.venv/Scripts/python.exe -m pytest worker/tests -q`

Run: `npm test -- --run && npm run build` in `apps/desktop`

Run: `cargo test` in `apps/desktop/src-tauri`

- [ ] **Step 2: 运行真实后台和多页验收**

使用临时多行 XLSX 和至少五页结果的商标，验证窗口被遮挡时不抢焦点、完成后 Edge 仍运行、结果工作簿可打开且执行记录完整。

- [ ] **Step 3: 运行安装版窗口验收**

安装版点击最小化、最大化、关闭并拖动标题栏，使用 Win32 窗口状态和坐标确认动作实际生效。

- [ ] **Step 4: 更新版本并打包**

将项目自有版本从 `0.1.4` 更新为 `0.1.5`，运行现有 Worker 构建、Tauri NSIS 和发布打包脚本。

- [ ] **Step 5: 校验交付物**

运行 `scripts/verify-deliverables.py`、smoke test 和打包 Worker 测试，确认安装包与 ZIP 哈希和大小非零。
