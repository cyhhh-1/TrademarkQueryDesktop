# 商标查询桌面客户端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建可安装的 Windows 本地桌面客户端，让员工选择两类商标 XLSX、在专用 Microsoft Edge 中手动登录、自动完成查询与截图写回，并自行另存结果。

**Architecture:** Vue 3 页面运行在 Tauri 2 桌面壳中，通过 Tauri 命令管理一个持久 Python Worker。Worker 使用 Playwright 经本机 CDP 连接专用可见 Edge，使用 openpyxl 和 Pillow 处理 XLSX 与证据图片，并在每行后原子保存检查点。

**Tech Stack:** Node.js 24、Vue 3.5、TypeScript 7、Vite 8、Vitest 5、Tauri 2、Rust stable MSVC、Python 3.11、Playwright 1.62、openpyxl 3.1.5、Pillow 12.3、PyInstaller 6.22。

**Spec:** `docs/superpowers/specs/2026-09-09-trademark-query-desktop-design.md`

**Visual Candidate:** `docs/design/trademark-query-primary-screen.png`；经用户确认后成为实施阶段的视觉参考。

## Global Constraints

- 所有新代码只能写入 `D:\4.29\TrademarkQueryDesktop`；`D:\4.29\BuddyAgent_2026.8.12` 全程只读。
- 新程序不得在运行时导入、软链接或调用 BuddyAgent 的代码、后端、数据库、MQTT、技能或 SOP。
- 首版完全本地运行，不调用大模型、数据库、对象存储或云端接口。
- 浏览器必须是员工可见的 Microsoft Edge，使用独立 profile；程序不得读取或填写账号、密码和验证码。
- 原始 XLSX 永不覆盖；结果先在任务目录生成并校验，再由员工另存。
- 同一台电脑同一时间只执行一个任务；多个员工通过各自电脑独立并发。
- 每个生产行为先写一个会因缺少该行为而失败的测试，确认 RED 后才写最小实现。
- 配置文件和打包清单使用安装、解析和真实构建命令验证，不为静态文本编写无意义单元测试。
- 所有真实 CNIPA 验收均由人工触发并低频执行；自动测试只使用脱敏 HTML 和合成 XLSX。
- UI 必须忠实实现经用户确认的视觉参考，文本和控件保持代码原生，不把概念图作为页面背景。

## File Map

```text
TrademarkQueryDesktop/
  README.md                              # 员工使用与开发构建说明
  .gitignore                             # 排除运行数据、构建物、真实表格与 Edge profile
  docs/design/                           # 已确认 UI 视觉参考
  docs/superpowers/                      # 设计规格和实施计划
  worker/
    pyproject.toml                       # Python 3.11 依赖、pytest 和包入口
    trademark-worker.spec                # PyInstaller onedir 打包配置
    src/trademark_worker/
      __init__.py                        # 版本号
      __main__.py                        # NDJSON Worker 入口
      protocol.py                        # 命令解析和事件输出
      models.py                          # 领域枚举与 dataclass
      normalization.py                   # 商标和申请人标准化
      selection.py                       # 候选排序与唯一性判定
      paths.py                           # LocalAppData 和任务目录
      checkpoint.py                      # 原子检查点
      task_engine.py                     # 逐行执行、暂停和恢复
      workbook/
        inspector.py                     # 模板识别和任务读取
        writer.py                        # 工作副本、字段和图片写回
      imaging/
        evidence.py                      # 行裁剪、拼接和尺寸换算
      browser/
        state.py                         # URL/DOM/HTTP 状态分类
        edge.py                          # 专用 Edge 启动和 CDP 发现
        session.py                       # Playwright 连接和页面选择
      cnipa/
        selectors.py                     # 页面稳定选择器集合
        parser.py                        # 列表和详情纯数据解析
        client.py                        # 搜索、翻页、点击商标和截图
    tests/
      fixtures/html/                     # 脱敏列表、详情和拦截页
      test_protocol.py
      test_selection.py
      test_workbook_inspector.py
      test_workbook_writer.py
      test_evidence.py
      test_browser_state.py
      test_edge.py
      test_cnipa_parser.py
      test_checkpoint.py
      test_task_engine.py
  apps/desktop/
    package.json                         # Vue/Tauri 依赖和脚本
    vite.config.ts
    tsconfig.json
    index.html
    src/
      main.ts
      App.vue                            # 只做主区域组合
      styles/tokens.css                  # 视觉参考提取的设计变量
      styles/app.css                     # 全局布局、焦点和响应式规则
      domain/types.ts                    # Worker 事件和 UI 类型
      domain/taskState.ts                # 纯状态归约器
      services/desktop.ts                # Tauri 命令封装
      composables/useTrademarkTask.ts    # Worker 事件与页面状态
      components/AppHeader.vue
      components/StepRail.vue
      components/WorkflowSidebar.vue
      components/BrowserStatus.vue
      components/InterruptionBanner.vue
      components/TaskSummary.vue
      components/TaskTable.vue
      components/TaskActions.vue
      tests/taskState.test.ts
      tests/App.test.ts
    src-tauri/
      Cargo.toml
      build.rs
      tauri.conf.json
      capabilities/default.json
      src/lib.rs                         # Tauri 初始化和命令注册
      src/worker.rs                      # Worker 子进程、stdin/stdout 和事件
      src/files.rs                       # 文件选择和安全另存为
      tests/worker_protocol.rs
      icons/                             # Tauri 默认尺寸图标
  scripts/
    build-worker.ps1                     # 创建 onedir Worker
    stage-worker.ps1                     # 复制 Worker 目录到 Tauri resources
    build-desktop.ps1                    # Worker、前端、Tauri 安装包完整构建
    smoke-test.ps1                       # 安装包前离线冒烟检查
```

---

### Task 1: Worker 包、协议握手和本地路径

**Files:**

- Create: `.gitignore`
- Create: `README.md`
- Create: `worker/pyproject.toml`
- Create: `worker/src/trademark_worker/__init__.py`
- Create: `worker/src/trademark_worker/protocol.py`
- Create: `worker/src/trademark_worker/paths.py`
- Create: `worker/src/trademark_worker/__main__.py`
- Test: `worker/tests/test_protocol.py`

**Interfaces:**

- Consumes: stdin 中的一行 JSON 命令。
- Produces: `parse_command(line: str) -> WorkerCommand`、`emit_event(event: WorkerEvent, stream: TextIO) -> None`、`app_data_root(local_app_data: str | None = None) -> Path`。

- [ ] **Step 1: 建立 Python 3.11 虚拟环境并写依赖配置**

`worker/pyproject.toml` 固定运行依赖 `playwright==1.62.0`、`openpyxl==3.1.5`、`Pillow==12.3.0`、`pydantic==2.13.5`，开发依赖固定 `pytest==9.1.1` 和 `pyinstaller==6.22.2`。运行：

```powershell
py -3.11 -m venv worker\.venv
worker\.venv\Scripts\python.exe -m pip install --upgrade pip
worker\.venv\Scripts\python.exe -m pip install -e "worker[dev]"
```

期望：安装退出码为 0，`python -c "import playwright, openpyxl, PIL, pydantic"` 退出码为 0。

- [ ] **Step 2: 写协议和路径失败测试**

```python
def test_hello_event_is_one_utf8_json_line():
    stream = io.StringIO()
    emit_event(WorkerEvent(type="hello", payload={"protocol": 1}), stream)
    assert stream.getvalue() == '{"type":"hello","protocol":1}\n'

def test_rejects_command_without_supported_protocol():
    with pytest.raises(ProtocolError, match="不支持的协议版本"):
        parse_command('{"type":"start","protocol":2}')

def test_app_data_root_uses_dedicated_folder():
    assert app_data_root(r"C:\Local") == Path(r"C:\Local\TrademarkQueryDesktop")
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_protocol.py -q`

Expected: collection 失败，原因是 `trademark_worker.protocol` 和 `trademark_worker.paths` 尚不存在。

- [ ] **Step 4: 写最小协议和路径实现**

```python
PROTOCOL_VERSION = 1

def parse_command(line: str) -> WorkerCommand:
    raw = json.loads(line)
    if raw.get("protocol") != PROTOCOL_VERSION:
        raise ProtocolError("不支持的协议版本")
    return WorkerCommand.model_validate(raw)

def emit_event(event: WorkerEvent, stream: TextIO = sys.stdout) -> None:
    stream.write(json.dumps(event.to_wire(), ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()

def app_data_root(local_app_data: str | None = None) -> Path:
    base = local_app_data or os.environ["LOCALAPPDATA"]
    return Path(base) / "TrademarkQueryDesktop"
```

`__main__.py` 启动后立即发送 `{"type":"hello","protocol":1,"worker_version":"0.1.0"}`，之后逐行读取 stdin。

- [ ] **Step 5: 运行测试和 CLI 冒烟**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_protocol.py -q`

Expected: PASS。

Run: `'{"type":"shutdown","protocol":1}' | worker\.venv\Scripts\python.exe -m trademark_worker`

Expected: 第一行是合法 hello JSON，进程收到 shutdown 后以 0 退出。

- [ ] **Step 6: 提交**

```powershell
git add .gitignore README.md worker
git commit -m "feat: add worker protocol foundation"
```

### Task 2: 领域模型、标准化、候选排序和唯一性判定

**Files:**

- Create: `worker/src/trademark_worker/models.py`
- Create: `worker/src/trademark_worker/normalization.py`
- Create: `worker/src/trademark_worker/selection.py`
- Test: `worker/tests/test_selection.py`

**Interfaces:**

- Produces: `LookupMode`、`BrowserState`、`TaskState`、`LookupJob`、`ResultRecord`、`DetailRecord`、`RowResult`。
- Produces: `normalize_mark(value: str) -> str`、`normalize_applicant(value: str) -> str`、`applicant_field(value: str) -> Literal["zh","en"]`。
- Produces: `application_candidates(records: Sequence[ResultRecord], mark: str, applicant: str) -> list[ResultRecord]`、`decide_uniqueness(records: Sequence[ResultRecord], mark: str) -> UniquenessDecision`。

- [ ] **Step 1: 写精确匹配、中文字段和唯一性失败测试**

```python
def test_application_candidates_are_exact_and_sequence_descending():
    records = [record(1, "抖音月付", "北京字跳网络技术有限公司"),
               record(4, "抖音月付服务", "北京字跳网络技术有限公司"),
               record(3, " 抖音月付 ", "北京字跳网络技术有限公司")]
    assert [r.sequence for r in application_candidates(records, "抖音月付", "北京字跳网络技术有限公司")] == [3, 1]

def test_chinese_applicant_uses_chinese_field():
    assert applicant_field("北京字跳网络技术有限公司") == "zh"
    assert applicant_field("ByteDance Ltd.") == "en"

def test_multiple_normalized_companies_are_not_unique():
    records = [record(1, "美团月付", "北京三快科技有限公司"),
               record(2, "美团月付", "上海三快科技有限公司")]
    decision = decide_uniqueness(records, "美团月付")
    assert decision.status == "否"
    assert decision.applicants == ("北京三快科技有限公司", "上海三快科技有限公司")

def test_missing_applicant_requires_review():
    assert decide_uniqueness([record(1, "美团月付", "")], "美团月付").status == "需人工复核"
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_selection.py -q`

Expected: FAIL，原因是模型和选择函数尚未实现。

- [ ] **Step 3: 写最小模型和规则实现**

```python
def normalize_mark(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()

def normalize_applicant(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()

def applicant_field(value: str) -> Literal["zh", "en"]:
    return "zh" if re.search(r"[\u3400-\u9fff]", value) else "en"

def application_candidates(records, mark, applicant):
    exact = [r for r in records if normalize_mark(r.mark) == normalize_mark(mark)
             and normalize_applicant(r.applicant) == normalize_applicant(applicant)]
    return sorted(exact, key=lambda item: item.sequence, reverse=True)
```

唯一性仅合并 NFKC、空白和大小写相同的名称，不删除企业类型后缀。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_selection.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add worker/src/trademark_worker worker/tests/test_selection.py
git commit -m "feat: add trademark selection rules"
```

### Task 3: XLSX 模板识别、输入保护和任务读取

**Files:**

- Create: `worker/src/trademark_worker/workbook/__init__.py`
- Create: `worker/src/trademark_worker/workbook/inspector.py`
- Test: `worker/tests/test_workbook_inspector.py`

**Interfaces:**

- Produces: `inspect_workbook(path: Path) -> WorkbookInspection`。
- Produces: `prepare_working_copy(source: Path, run_dir: Path) -> Path`。
- Produces: `load_jobs(path: Path, inspection: WorkbookInspection, overwrite: bool) -> list[LookupJob]`。

- [ ] **Step 1: 写合成工作簿失败测试**

```python
def test_detects_application_template_by_headers(tmp_path):
    path = make_xlsx(tmp_path, ["商标名称", "商标申请人", "专用权期限", "截图"],
                     [["抖音月付", "北京字跳网络技术有限公司", None, None]])
    inspection = inspect_workbook(path)
    assert inspection.mode is LookupMode.APPLICATION
    assert inspection.columns == {"mark": 1, "applicant": 2, "term": 3, "screenshot": 4}

def test_detects_uniqueness_template_with_multiline_headers(tmp_path):
    path = make_xlsx(tmp_path,
        ["商标名称", "是否唯一性\n（当申请公司有多家时填写“否”", "商标申请人\n当是否唯一性“否”时此处空白", "商标有效期", "截图"],
        [["美团月付", None, None, None, None]])
    assert inspect_workbook(path).mode is LookupMode.UNIQUENESS

def test_working_copy_does_not_change_source(tmp_path):
    source = make_xlsx(tmp_path, ["商标名称", "商标申请人", "专用权期限", "截图"], [])
    digest = sha256(source.read_bytes()).hexdigest()
    working = prepare_working_copy(source, tmp_path / "run")
    assert working != source
    assert sha256(source.read_bytes()).hexdigest() == digest
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_workbook_inspector.py -q`

Expected: FAIL，原因是 inspector 尚不存在。

- [ ] **Step 3: 实现表头规范化和模板检测**

```python
def normalize_header(value: object) -> str:
    return re.sub(r"[\s（）()“”\"']+", "", unicodedata.normalize("NFKC", str(value or "")))

def inspect_workbook(path: Path) -> WorkbookInspection:
    if path.suffix.lower() != ".xlsx":
        raise WorkbookInputError("只支持 .xlsx 文件")
    wb = load_workbook(path, read_only=False, data_only=False)
    sheet = next((ws for ws in wb.worksheets if any(c.value for c in ws[1])), None)
    if sheet is None:
        raise WorkbookInputError("未找到包含表头的工作表")
    return classify_headers(sheet.title, [cell.value for cell in sheet[1]])
```

模板检测必须覆盖两份真实表头，并在缺少目标列时返回稳定错误码和缺失列中文名称。

- [ ] **Step 4: 运行单测和两份真实输入的只读检测**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_workbook_inspector.py -q`

Expected: PASS。

Run: `worker\.venv\Scripts\python.exe -c "import json; from pathlib import Path; from trademark_worker.workbook.inspector import inspect_workbook; print(json.dumps(inspect_workbook(Path(r'D:\4.29\BuddyAgent_2026.8.12\商标申请.xlsx')).to_wire(), ensure_ascii=False))"`

Expected: `mode=application`、1 条任务、字段列为 A-D。

Run: `worker\.venv\Scripts\python.exe -c "import json; from pathlib import Path; from trademark_worker.workbook.inspector import inspect_workbook; print(json.dumps(inspect_workbook(Path(r'D:\4.29\BuddyAgent_2026.8.12\商标查询是否唯一性.xlsx')).to_wire(), ensure_ascii=False))"`

Expected: `mode=uniqueness`、1 条任务、字段列为 A-E；两个源文件哈希与执行前一致。

- [ ] **Step 5: 提交**

```powershell
git add worker/src/trademark_worker/workbook worker/tests/test_workbook_inspector.py
git commit -m "feat: inspect trademark workbook templates"
```

### Task 4: 证据图片裁剪、拼接和 XLSX 写回

**Files:**

- Create: `worker/src/trademark_worker/imaging/__init__.py`
- Create: `worker/src/trademark_worker/imaging/evidence.py`
- Create: `worker/src/trademark_worker/workbook/writer.py`
- Test: `worker/tests/test_evidence.py`
- Test: `worker/tests/test_workbook_writer.py`

**Interfaces:**

- Produces: `crop_rows(page_png: Path, boxes: Sequence[PixelBox], output_dir: Path) -> list[Path]`。
- Produces: `stack_rows(images: Sequence[Path], output: Path, gap: int = 12) -> Path`。
- Produces: `apply_row_result(working_xlsx: Path, inspection: WorkbookInspection, job: LookupJob, result: RowResult, evidence: Path | None) -> None`。
- Produces: `validate_output(path: Path) -> None`。

- [ ] **Step 1: 写图片行为失败测试**

```python
def test_stack_rows_preserves_order_and_adds_gap(tmp_path):
    red = solid_png(tmp_path / "red.png", (100, 20), "red")
    blue = solid_png(tmp_path / "blue.png", (80, 30), "blue")
    output = stack_rows([red, blue], tmp_path / "stack.png", gap=12)
    image = Image.open(output)
    assert image.size == (100, 62)
    assert image.getpixel((2, 2))[:3] == (255, 0, 0)
    assert image.getpixel((2, 40))[:3] == (0, 0, 255)

def test_crop_rows_uses_css_to_pixel_scale(tmp_path):
    source = coordinate_png(tmp_path / "page.png", 200, 100)
    boxes = [PixelBox(x=10, y=10, width=50, height=20, scale=2)]
    cropped = Image.open(crop_rows(source, boxes, tmp_path / "rows")[0])
    assert cropped.size == (100, 40)
```

- [ ] **Step 2: 运行图片测试并确认 RED**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_evidence.py -q`

Expected: FAIL，原因是 evidence 模块尚不存在。

- [ ] **Step 3: 写最小图片实现并确认 GREEN**

```python
def stack_rows(images, output, gap=12):
    opened = [Image.open(path).convert("RGB") for path in images]
    width = max(image.width for image in opened)
    height = sum(image.height for image in opened) + gap * (len(opened) - 1)
    canvas = Image.new("RGB", (width, height), "white")
    y = 0
    for image in opened:
        canvas.paste(image, (0, y))
        y += image.height + gap
    canvas.save(output, format="PNG", optimize=True)
    return output
```

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_evidence.py -q`

Expected: PASS。

- [ ] **Step 4: 写 XLSX 定向写回失败测试**

```python
def test_application_result_writes_term_and_embeds_detail_image(tmp_path):
    source, inspection, job = application_fixture(tmp_path)
    original_hash = sha256(source.read_bytes()).hexdigest()
    working = prepare_working_copy(source, tmp_path / "run")
    apply_row_result(working, inspection, job,
        RowResult.application_success("北京字跳网络技术有限公司", "2023年08月28日至2033年08月27日"),
        solid_png(tmp_path / "detail.png", (640, 900), "white"))
    wb = load_workbook(working)
    assert wb["Sheet1"]["B2"].value == "北京字跳网络技术有限公司"
    assert wb["Sheet1"]["C2"].value == "2023年08月28日至2033年08月27日"
    assert len(wb["Sheet1"]._images) == 1
    assert sha256(source.read_bytes()).hexdigest() == original_hash

def test_non_unique_result_leaves_applicant_and_term_blank(tmp_path):
    working, inspection, job = uniqueness_fixture(tmp_path)
    apply_row_result(working, inspection, job, RowResult.uniqueness("否"), evidence_png(tmp_path))
    ws = load_workbook(working)["Sheet1"]
    assert ws["B2"].value == "否"
    assert ws["C2"].value is None
    assert ws["D2"].value is None
    assert len(ws._images) == 1
```

- [ ] **Step 5: 运行写回测试并确认 RED**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_workbook_writer.py -q`

Expected: FAIL，原因是 writer 尚不存在。

- [ ] **Step 6: 实现原子保存、图片锚定和输出重开校验**

```python
def validate_output(path: Path) -> None:
    with path.open("rb") as stream:
        if stream.read(2) != b"PK":
            raise WorkbookOutputError("结果文件不是有效的 XLSX")
    load_workbook(path, read_only=False, data_only=False).close()

def atomic_save(workbook, target: Path) -> None:
    temporary = target.with_suffix(".writing.xlsx")
    workbook.save(temporary)
    validate_output(temporary)
    temporary.replace(target)
```

图片插入到对应行截图列，宽度不超过 720 px 等效尺寸，按比例调整图片高度和 Excel 行高；不改动其他工作表、列宽和已有样式。

- [ ] **Step 7: 运行测试并提交**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_evidence.py worker/tests/test_workbook_writer.py -q`

Expected: PASS。

```powershell
git add worker/src/trademark_worker/imaging worker/src/trademark_worker/workbook/writer.py worker/tests
git commit -m "feat: write trademark evidence to workbooks"
```

### Task 5: Edge 启动、CDP 发现和登录状态分类

**Files:**

- Create: `worker/src/trademark_worker/browser/__init__.py`
- Create: `worker/src/trademark_worker/browser/state.py`
- Create: `worker/src/trademark_worker/browser/edge.py`
- Create: `worker/src/trademark_worker/browser/session.py`
- Test: `worker/tests/test_browser_state.py`
- Test: `worker/tests/test_edge.py`

**Interfaces:**

- Produces: `classify_page(snapshot: PageSnapshot) -> BrowserState`。
- Produces: `build_edge_command(edge_exe: Path, profile_dir: Path) -> list[str]`、`open_for_observation(seconds: int) -> int`。
- Produces: `read_cdp_endpoint(profile_dir: Path, timeout_seconds: float) -> str`。
- Produces: `EdgeSession.open_home() -> None`、`EdgeSession.snapshot() -> PageSnapshot`、`EdgeSession.require_ready() -> None`。

- [ ] **Step 1: 写所有登录状态失败测试**

```python
@pytest.mark.parametrize(("snapshot", "expected"), [
    (PageSnapshot("about:blank", None, "", False, False), BrowserState.AUTH_BLOCKED),
    (PageSnapshot("https://sso.cnipa.gov.cn/am/#/login", 200, "登录 验证码", False, True), BrowserState.LOGIN_REQUIRED),
    (PageSnapshot("https://wcjs.sbj.cnipa.gov.cn/", 200, "商标综合查询", False, False), BrowserState.RETURNED_HOME),
    (PageSnapshot("https://wcjs.sbj.cnipa.gov.cn/sgtmi", 200, "检索要素 查询", True, False), BrowserState.READY),
    (PageSnapshot("https://wcjs.sbj.cnipa.gov.cn/", 403, "Forbidden", False, False), BrowserState.WAF_BLOCKED),
    (PageSnapshot("https://wcjs.sbj.cnipa.gov.cn/", 406, "Not Acceptable", False, False), BrowserState.WAF_BLOCKED),
])
def test_classifies_browser_state(snapshot, expected):
    assert classify_page(snapshot) is expected
```

- [ ] **Step 2: 写 Edge 命令失败测试**

```python
def test_edge_command_is_visible_local_and_uses_dedicated_profile(tmp_path):
    command = build_edge_command(Path("msedge.exe"), tmp_path / "profile")
    assert "--headless" not in command
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=0" in command
    assert f"--user-data-dir={tmp_path / 'profile'}" in command
    assert command[-1] == "https://wcjs.sbj.cnipa.gov.cn/"
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_browser_state.py worker/tests/test_edge.py -q`

Expected: FAIL，原因是 browser 模块尚不存在。

- [ ] **Step 4: 实现状态优先级和 CDP 文件读取**

```python
def classify_page(s: PageSnapshot) -> BrowserState:
    if s.url == "about:blank":
        return BrowserState.AUTH_BLOCKED
    if s.http_status in {403, 406}:
        return BrowserState.WAF_BLOCKED
    if "sso.cnipa.gov.cn" in s.url or s.has_captcha:
        return BrowserState.LOGIN_REQUIRED
    if urlparse(s.url).path.rstrip("/") == "/sgtmi" and s.has_search_form:
        return BrowserState.READY
    if s.url.startswith("https://wcjs.sbj.cnipa.gov.cn/"):
        return BrowserState.RETURNED_HOME
    return BrowserState.WAITING_SITE
```

`read_cdp_endpoint` 读取专用 profile 根目录中的 `DevToolsActivePort` 第一行并返回 `http://127.0.0.1:<port>`；超时返回稳定 `EDGE_CDP_TIMEOUT` 错误。

- [ ] **Step 5: 运行测试并进行只打开不操作的 Edge 冒烟**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_browser_state.py worker/tests/test_edge.py -q`

Expected: PASS。

Run: `worker\.venv\Scripts\python.exe -c "from trademark_worker.browser.edge import open_for_observation; raise SystemExit(open_for_observation(15))"`

Expected: Edge 可见并从官网首页打开；Worker 只输出状态事件，不点击 SSO、不填写验证码；出现 `about:blank` 时输出 `AUTH_BLOCKED`。

- [ ] **Step 6: 提交**

```powershell
git add worker/src/trademark_worker/browser worker/tests/test_browser_state.py worker/tests/test_edge.py
git commit -m "feat: manage dedicated Edge session"
```

### Task 6: CNIPA 列表解析、翻页、详情校验和截图

**Files:**

- Create: `worker/src/trademark_worker/cnipa/__init__.py`
- Create: `worker/src/trademark_worker/cnipa/selectors.py`
- Create: `worker/src/trademark_worker/cnipa/parser.py`
- Create: `worker/src/trademark_worker/cnipa/client.py`
- Create: `worker/tests/fixtures/html/application-list.html`
- Create: `worker/tests/fixtures/html/uniqueness-list-page-1.html`
- Create: `worker/tests/fixtures/html/uniqueness-list-page-2.html`
- Create: `worker/tests/fixtures/html/detail-active.html`
- Create: `worker/tests/fixtures/html/detail-pending.html`
- Test: `worker/tests/test_cnipa_parser.py`

**Interfaces:**

- Consumes: `EdgeSession`、`LookupJob`。
- Produces: `PagePort` Protocol、`parse_result_table(rows: Sequence[Sequence[str]]) -> list[ResultRecord]`、`parse_detail(labels: Mapping[str, str]) -> DetailRecord`。
- Produces: `CnipaClient.search(job: LookupJob) -> list[ResultRecord]`、`CnipaClient.open_detail(record: ResultRecord) -> DetailRecord`、`CnipaClient.capture_exact_rows(records, output_dir) -> Path`、`CnipaClient.capture_detail(output) -> Path`。

- [ ] **Step 1: 写列表和详情纯解析失败测试**

```python
def test_parses_complete_result_row():
    rows = [["序号", "申请/注册号", "国际分类", "申请日期", "商标", "申请人名称"],
            ["2", "93296493", "35", "2026年08月03日", "美团", "北京三快科技有限公司"]]
    assert parse_result_table(rows) == [ResultRecord(2, "93296493", "35", "2026年08月03日", "美团", "北京三快科技有限公司")]

def test_active_detail_requires_matching_applicant_and_term():
    detail = parse_detail({"商标": "抖音月付", "申请/注册号": "66227440",
                           "申请人名称（中文）": "北京字跳网络技术有限公司",
                           "专用权期限": "2023年08月28日至2033年08月27日"})
    assert detail.term == "2023年08月28日至2033年08月27日"

def test_pending_detail_has_no_term():
    assert parse_detail({"商标": "抖音月付", "申请/注册号": "1"}).term is None
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_cnipa_parser.py -q`

Expected: FAIL，原因是 cnipa parser 尚不存在。

- [ ] **Step 3: 实现纯解析和稳定选择器**

选择器按以下优先级：关联中文 label、表头文本定位、结构选择器。禁止依赖屏幕绝对坐标。详情页点击必须使用候选记录的商标名称/图样单元格，注册号链接只作为记录身份校验。

```python
SEARCH_LABELS = {
    "mark": ("检索要素",),
    "applicant_zh": ("申请人名称（中文）", "申请人名称(中文)"),
    "applicant_en": ("申请人名称（英文）", "申请人名称(英文)"),
}

def parse_detail(labels):
    return DetailRecord(
        mark=labels.get("商标", "").strip(),
        registration_no=labels.get("申请/注册号", "").strip(),
        applicant=(labels.get("申请人名称（中文）") or labels.get("申请人名称（英文）") or "").strip(),
        term=(labels.get("专用权期限") or "").strip() or None,
    )
```

- [ ] **Step 4: 写客户端分页和点击契约测试**

使用脱敏 HTML 页面和一个 `PagePort` 测试实现，断言：

```python
def test_uniqueness_search_visits_every_page_and_keeps_exact_marks(page_port):
    records = CnipaClient(page_port).search(LookupJob.uniqueness(2, "美团"))
    assert page_port.visited_pages == [1, 2]
    assert [r.registration_no for r in records] == ["93296493", "93296492"]

def test_application_opens_mark_cell_not_registration_link(page_port):
    record = ResultRecord(2, "66227440", "36", "2022年07月27日", "抖音月付", "北京字跳网络技术有限公司")
    CnipaClient(page_port).open_detail(record)
    assert page_port.clicked == (2, "mark_cell")
```

- [ ] **Step 5: 运行契约测试并确认 RED，再实现最小客户端**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_cnipa_parser.py -q`

Expected first run: FAIL，缺少分页和商标单元格点击。

客户端在每次查询前、提交后、翻页后、打开详情后调用 `session.require_ready()` 或对应结果/详情状态校验；验证码、SSO、`about:blank`、403/406 转换为 `NeedsUserAction`，不自动刷新循环。详情证据使用 Playwright `full_page=True`，必须覆盖商标、申请人和专用权期限；唯一性证据使用结果行 `bounding_box()` 和页面截图计算精确裁剪框。

- [ ] **Step 6: 运行测试并提交**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_cnipa_parser.py -q`

Expected: PASS。

```powershell
git add worker/src/trademark_worker/cnipa worker/tests/fixtures/html worker/tests/test_cnipa_parser.py
git commit -m "feat: automate CNIPA result and detail pages"
```

### Task 7: 检查点和两种逐行任务引擎

**Files:**

- Create: `worker/src/trademark_worker/checkpoint.py`
- Create: `worker/src/trademark_worker/task_engine.py`
- Test: `worker/tests/test_checkpoint.py`
- Test: `worker/tests/test_task_engine.py`

**Interfaces:**

- Produces: `CheckpointStore.load() -> Checkpoint | None`、`CheckpointStore.save(checkpoint: Checkpoint) -> None`。
- Produces: `TaskEngine.run(request: TaskRequest, events: EventSink) -> TaskSummary`、`TaskEngine.resume(run_id: str, events: EventSink) -> TaskSummary`、`TaskEngine.cancel() -> None`。

- [ ] **Step 1: 写原子检查点和输入指纹失败测试**

```python
def test_checkpoint_round_trip_and_no_partial_file(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoint.json")
    store.save(Checkpoint(run_id="r1", input_sha256="abc", completed_rows=(2,)))
    assert store.load().completed_rows == (2,)
    assert not (tmp_path / "checkpoint.json.writing").exists()

def test_resume_rejects_different_input_hash(tmp_path):
    store = saved_checkpoint(tmp_path, input_sha256="abc")
    with pytest.raises(ResumeError, match="输入文件与检查点不一致"):
        store.require_input("def")
```

- [ ] **Step 2: 运行检查点测试并确认 RED，写最小实现**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_checkpoint.py -q`

Expected: FAIL，缺少 CheckpointStore。

```python
def save(self, checkpoint: Checkpoint) -> None:
    temporary = self.path.with_suffix(".json.writing")
    temporary.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
    os.replace(temporary, self.path)
```

- [ ] **Step 3: 写模式一引擎失败测试**

```python
def test_application_tries_later_sequence_first_until_term_exists(engine_fixture):
    summary = engine_fixture.run_application(details={4: detail(term=None), 2: detail(term="2023至2033")})
    assert engine_fixture.client.opened_sequences == [4, 2]
    assert summary.items[0].status == "success"
    assert summary.items[0].term == "2023至2033"
```

- [ ] **Step 4: 写模式二引擎失败测试**

```python
def test_uniqueness_stacks_every_exact_row_and_blanks_non_unique_fields(engine_fixture):
    summary = engine_fixture.run_uniqueness(records=[record(1, "美团", "甲公司"), record(2, "美团", "乙公司")])
    result = summary.items[0]
    assert result.uniqueness == "否"
    assert result.applicant is None
    assert result.term is None
    assert engine_fixture.images.stacked_sequences == [1, 2]
```

- [ ] **Step 5: 运行引擎测试并确认 RED，再实现逐行流程**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_task_engine.py -q`

Expected first run: FAIL，缺少 TaskEngine 行为。

实现顺序：开始行事件、登录状态校验、查询、候选筛选、详情或全部同名裁剪、写回、输出重开校验、检查点、完成行事件。`NeedsUserAction` 必须先保存当前安全检查点，再发送 paused 事件。

- [ ] **Step 6: 运行全部 Worker 测试并提交**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests -q`

Expected: PASS，0 failed。

```powershell
git add worker/src/trademark_worker/checkpoint.py worker/src/trademark_worker/task_engine.py worker/tests
git commit -m "feat: run resumable trademark tasks"
```

### Task 8: 持久 Worker 命令循环和离线端到端测试

**Files:**

- Modify: `worker/src/trademark_worker/protocol.py`
- Modify: `worker/src/trademark_worker/__main__.py`
- Create: `worker/tests/test_worker_commands.py`
- Create: `worker/tests/test_offline_e2e.py`

**Interfaces:**

- Consumes commands: `inspect_file`、`open_browser`、`browser_status`、`start`、`resume`、`cancel`、`shutdown`。
- Emits events: `hello`、`file_inspected`、`browser_state`、`task_state`、`row_progress`、`paused`、`completed`、`error`。

- [ ] **Step 1: 写命令生命周期失败测试**

```python
def test_worker_emits_inspection_then_completion(worker_process, application_xlsx):
    assert worker_process.read_event()["type"] == "hello"
    worker_process.send({"type": "inspect_file", "protocol": 1, "path": str(application_xlsx)})
    assert worker_process.read_event()["type"] == "file_inspected"
    worker_process.send({"type": "shutdown", "protocol": 1})
    assert worker_process.wait() == 0

def test_bad_command_becomes_structured_error(worker_process):
    worker_process.send({"type": "unknown", "protocol": 1})
    event = worker_process.read_event()
    assert event["type"] == "error"
    assert event["code"] == "UNKNOWN_COMMAND"
```

- [ ] **Step 2: 运行并确认 RED，再实现命令路由**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests/test_worker_commands.py -q`

Expected first run: FAIL，命令循环尚未路由。

路由器只捕获已知业务异常并输出稳定错误码；未处理异常写脱敏 stderr，并输出 `WORKER_INTERNAL_ERROR`，不得把 Cookie 或页面 HTML 放入错误消息。

- [ ] **Step 3: 写离线端到端失败测试**

```python
def test_offline_application_workbook_finishes_without_touching_source(offline_engine, real_shape_fixture):
    source = real_shape_fixture("application")
    before = sha256(source.read_bytes()).hexdigest()
    output = offline_engine.complete_application(source)
    wb = load_workbook(output)
    assert wb["Sheet1"]["C2"].value == "2023年08月28日至2033年08月27日"
    assert len(wb["Sheet1"]._images) == 1
    assert sha256(source.read_bytes()).hexdigest() == before
```

- [ ] **Step 4: 运行全部 Worker 测试并提交**

Run: `worker\.venv\Scripts\python.exe -m pytest worker/tests -q`

Expected: PASS，所有命令、两类表格和恢复测试通过。

```powershell
git add worker/src/trademark_worker worker/tests
git commit -m "feat: expose persistent worker command loop"
```

### Task 9: Vue 状态模型和视觉参考主界面

**Files:**

- Create: `apps/desktop/package.json`
- Create: `apps/desktop/vite.config.ts`
- Create: `apps/desktop/tsconfig.json`
- Create: `apps/desktop/index.html`
- Create: `apps/desktop/src/main.ts`
- Create: `apps/desktop/src/domain/types.ts`
- Create: `apps/desktop/src/domain/taskState.ts`
- Create: `apps/desktop/src/services/desktop.ts`
- Create: `apps/desktop/src/styles/tokens.css`
- Create: `apps/desktop/src/styles/app.css`
- Create: `apps/desktop/src/components/*.vue`
- Create: `apps/desktop/src/App.vue`
- Test: `apps/desktop/src/tests/taskState.test.ts`
- Test: `apps/desktop/src/tests/App.test.ts`

**Interfaces:**

- Produces: `reduceWorkerEvent(state: UiState, event: WorkerEvent) -> UiState`。
- Produces: `DesktopService` 接口和 `DESKTOP_SERVICE_KEY` 注入键，测试使用内存实现，Task 10 将其连接到 Tauri 命令。
- Produces: `useTrademarkTask()`，公开 `selectFile`、`openBrowser`、`start`、`resume`、`cancel`、`saveAs`。
- UI 组件只接收 typed props 和 emits，不直接调用 Tauri。

- [ ] **Step 1: 安装前端依赖并验证配置可解析**

`package.json` 固定 Vue 3.5.42、Vite 8.2.2、Vitest 5.0.0、TypeScript 7.0.2、`@vue/test-utils` 2.5.0、jsdom 30.0.1、Tauri CLI 2.11.4 和 API 2.11.1。

Run: `npm --prefix apps/desktop install`

Expected: 退出码 0，并生成 `package-lock.json`。

- [ ] **Step 2: 写状态归约器失败测试**

```typescript
it('登录失效时暂停并保留已完成行', () => {
  const running = stateWithRows([{ row: 2, status: 'success' }, { row: 3, status: 'running' }])
  const next = reduceWorkerEvent(running, { type: 'paused', reason: 'AUTH_BLOCKED', recoverable: true })
  expect(next.taskState).toBe('paused_user_action')
  expect(next.rows[0].status).toBe('success')
  expect(next.alert).toContain('重新登录')
})
```

- [ ] **Step 3: 运行并确认 RED，再实现纯状态模型**

Run: `npm --prefix apps/desktop test -- --run src/tests/taskState.test.ts`

Expected first run: FAIL，缺少 `taskState.ts`。

```typescript
export function reduceWorkerEvent(state: UiState, event: WorkerEvent): UiState {
  if (event.type === 'paused') {
    return { ...state, taskState: 'paused_user_action', alert: pauseMessage(event.reason) }
  }
  if (event.type === 'row_progress') {
    return { ...state, rows: replaceRow(state.rows, event.row) }
  }
  return state
}
```

- [ ] **Step 4: 写完整主屏行为失败测试**

```typescript
it('文件和浏览器就绪后启用开始查询', async () => {
  const wrapper = mount(App, { global: { provide: { desktopService: readyFakeService() } } })
  await wrapper.get('[data-testid="select-file"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('商标申请查询')
  expect(wrapper.get('[data-testid="start-task"]').attributes('disabled')).toBeUndefined()
})

it('暂停状态显示内联提示并启用重新检测', async () => {
  const wrapper = mount(App, { global: { provide: { desktopService: pausedFakeService() } } })
  expect(wrapper.text()).toContain('登录已失效，任务已暂停。请在 Edge 中重新登录后继续。')
  expect(wrapper.get('[data-testid="recheck-browser"]').isVisible()).toBe(true)
})
```

- [ ] **Step 5: 运行并确认 RED，再按视觉参考实现组件和样式**

Run: `npm --prefix apps/desktop test -- --run src/tests/App.test.ts`

Expected first run: FAIL，App 和组件尚未实现。

设计变量固定：真白 `#FFFFFF`、页面线框 `#D9E1EA`、正文 `#10213A`、次要文字 `#5E6C80`、主蓝 `#0B73E0`、成功 `#12A45A`、提醒 `#F4A000`、圆角 `8px`。主屏使用顶部步骤轨道、左侧 332px 工作流栏、右侧弹性任务表格，不使用营销标题或卡片网格。

- [ ] **Step 6: 运行前端测试、类型检查和生产构建**

Run: `npm --prefix apps/desktop test -- --run`

Expected: PASS。

Run: `npm --prefix apps/desktop run build`

Expected: `vue-tsc --noEmit` 与 Vite build 均退出 0。

- [ ] **Step 7: 提交**

```powershell
git add apps/desktop
git commit -m "feat: build trademark desktop interface"
```

### Task 10: Tauri 壳、Worker 进程桥和本地文件命令

**Files:**

- Create: `apps/desktop/src-tauri/Cargo.toml`
- Create: `apps/desktop/src-tauri/build.rs`
- Create: `apps/desktop/src-tauri/tauri.conf.json`
- Create: `apps/desktop/src-tauri/capabilities/default.json`
- Create: `apps/desktop/src-tauri/src/lib.rs`
- Create: `apps/desktop/src-tauri/src/worker.rs`
- Create: `apps/desktop/src-tauri/src/files.rs`
- Create: `apps/desktop/src-tauri/tests/worker_protocol.rs`
- Modify: `apps/desktop/src/services/desktop.ts`

**Interfaces:**

- Tauri commands: `worker_start()`、`worker_send(command: WorkerCommand)`、`worker_stop()`、`pick_xlsx()`、`save_output_as(source: String)`、`open_task_folder(path: String)`、`clear_login_session()`。
- Emits: Tauri event `worker-event`，payload 为 Worker NDJSON 对象。

- [ ] **Step 1: 安装 Windows Rust 构建前置**

当前机器已有 WebView2 和 Edge，但缺少 Rust 与 MSVC Build Tools。用以下命令安装 Rust stable MSVC 和 Visual Studio 2022 Build Tools 的“Desktop development with C++”组件：

```powershell
winget install --exact --id Rustlang.Rustup --accept-package-agreements --accept-source-agreements
winget install --exact --id Microsoft.VisualStudio.2022.BuildTools --accept-package-agreements --accept-source-agreements --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

安装完成并重开终端后运行：

```powershell
rustc --version
cargo --version
```

Expected: 两条命令退出码 0，host 为 `x86_64-pc-windows-msvc`。

- [ ] **Step 2: 写 Rust NDJSON 分帧失败测试**

```rust
#[test]
fn splits_multiple_worker_events_without_losing_utf8() {
    let mut decoder = LineDecoder::default();
    let events = decoder.push("{\"type\":\"hello\"}\n{\"message\":\"已暂停\"}\n".as_bytes());
    assert_eq!(events.len(), 2);
    assert_eq!(events[1]["message"], "已暂停");
}
```

- [ ] **Step 3: 运行并确认 RED，再实现 Worker 进程管理**

Run: `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml`

Expected first run: FAIL，缺少 `LineDecoder` 和 Worker 管理器。

开发模式启动 `worker/.venv/Scripts/python.exe -m trademark_worker`；发布模式从 `app.path().resource_dir()/worker/trademark-worker.exe` 启动 PyInstaller onedir。stdin 使用单独互斥锁，stdout 在线程中逐行解析并通过 `AppHandle.emit("worker-event", value)` 发送。应用退出时发送 shutdown，超时后只终止本应用创建的 Worker。

- [ ] **Step 4: 写安全另存为失败测试**

```rust
#[test]
fn refuses_to_save_over_original_input() {
    let input = PathBuf::from(r"C:\work\商标申请.xlsx");
    assert!(validate_save_target(&input, &input).is_err());
}
```

`save_output_as` 使用系统保存对话框，目标存在时由原生对话框确认；复制到同目录临时文件并原子替换，拒绝等于原始输入路径的目标。

`clear_login_session` 仅在本应用启动的专用 Edge 已退出后生效；删除前将目标 `resolve()` 并验证它严格等于 `%LOCALAPPDATA%\TrademarkQueryDesktop\edge-profile`，界面必须先显示二次确认，禁止接受前端传入的任意目录。

- [ ] **Step 5: 连接 Vue service 并运行全套构建**

Run: `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml`

Expected: PASS。

Run: `npm --prefix apps/desktop run build`

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add apps/desktop
git commit -m "feat: connect Tauri shell to local worker"
```

### Task 11: PyInstaller onedir、Tauri resources 和 Windows 安装包

**Files:**

- Create: `worker/trademark-worker.spec`
- Create: `scripts/build-worker.ps1`
- Create: `scripts/stage-worker.ps1`
- Create: `scripts/build-desktop.ps1`
- Create: `scripts/smoke-test.ps1`
- Modify: `apps/desktop/src-tauri/tauri.conf.json`
- Modify: `README.md`

**Interfaces:**

- `build-worker.ps1` 产出 `worker/dist/trademark-worker/trademark-worker.exe` 及 `_internal`。
- `stage-worker.ps1` 产出 `apps/desktop/src-tauri/resources/worker/` 完整 onedir。
- `build-desktop.ps1` 产出 Tauri Windows 安装包。

- [ ] **Step 1: 写并运行 Worker 打包脚本**

`trademark-worker.spec` 使用 `console=True` 保留 stdin/stdout 协议，收集 Playwright Python 包但不收集 Chromium 浏览器。Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-worker.ps1
```

Expected: onedir 目录存在，直接管道发送 shutdown 后 worker 退出 0。

- [ ] **Step 2: 写并运行资源分发脚本**

脚本验证源路径和目标路径都位于新项目内，然后将完整 onedir 复制到 `src-tauri/resources/worker`。Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stage-worker.ps1
apps\desktop\src-tauri\resources\worker\trademark-worker.exe --version
```

Expected: 输出 `0.1.0`。

- [ ] **Step 3: 配置 Tauri resources 并构建安装包**

`tauri.conf.json` 的 `bundle.resources` 包含 `resources/worker/**/*`，产品名为“商标查询助手”，identifier 为 `cn.local.trademarkquerydesktop`，窗口初始尺寸为 1440x900，最小尺寸为 1100x720。

Run: `powershell -ExecutionPolicy Bypass -File scripts\build-desktop.ps1`

Expected: Worker、Vue、Rust、Tauri 四段构建均退出 0，并在 `apps/desktop/src-tauri/target/release/bundle/` 生成 Windows 安装包。

- [ ] **Step 4: 在无 Python PATH 依赖模式执行冒烟**

Run: `powershell -ExecutionPolicy Bypass -File scripts\smoke-test.ps1`

Expected: 校验安装资源中的 Worker 能握手、两种合成 XLSX 能离线完成、输入哈希不变、结果能重新打开且含图片。

- [ ] **Step 5: 提交**

```powershell
git add worker/trademark-worker.spec scripts apps/desktop/src-tauri/tauri.conf.json README.md
git commit -m "build: package standalone Windows desktop app"
```

### Task 12: 浏览器视觉验收、真实 XLSX 验收和交付记录

**Files:**

- Create: `docs/verification/visual-fidelity.md`
- Create: `docs/verification/real-workbook-acceptance.md`
- Modify: `README.md`

**Interfaces:**

- Consumes: 已构建桌面应用、`docs/design/trademark-query-primary-screen.png`、两份用户 XLSX。
- Produces: 视觉差异清单、真实查询结果路径、每项通过/失败证据和安装包路径。

- [ ] **Step 1: 启动桌面客户端并走核心交互**

Run: `npm --prefix apps/desktop run tauri dev`

验证：选择文件、模板识别、打开 Edge、浏览器状态变化、开始、暂停、重新检测、继续、取消、另存为按钮均改变真实本地状态，不存在空按钮。

- [ ] **Step 2: 捕获 1440x900 主屏并与概念图逐项比较**

使用 Browser/IAB 检查可用界面；如果 Tauri 原生 WebView 无法由 Browser/IAB 捕获，使用 Playwright 对同一 Vue dev URL 捕获 1440x900 截图，并在验收记录写明回退原因。

必须使用 `view_image` 同时检查概念图和最新截图，记录至少：文案、两栏布局、步骤轨道、字体层级、真白背景与蓝色、提醒条、表格密度、按钮状态八项。所有可修复偏差在继续前修复。

- [ ] **Step 3: 检查响应式和可访问性**

验证 1100x720 最小窗口无主要内容裁剪，键盘 Tab 顺序覆盖主要按钮，焦点可见，`prefers-reduced-motion` 下无非必要动画，禁用按钮具有 `disabled` 属性而不只是灰色外观。

- [ ] **Step 4: 运行模式一真实整表验收**

选择 `D:\4.29\BuddyAgent_2026.8.12\商标申请.xlsx` 的只读副本，从 CNIPA 首页开始，由员工手动登录。完整跑完所有行，核对：

- 只填写检索要素和正确语言的申请人字段；
- 精确同名且申请人匹配；
- 序号从大到小；
- 点击商标名称/图样进入详情；
- 无期限继续下一个候选；
- 申请人、期限和完整详情截图写入对应行；
- 源文件哈希不变。

- [ ] **Step 5: 运行模式二真实整表验收**

选择 `D:\4.29\BuddyAgent_2026.8.12\商标查询是否唯一性.xlsx` 的只读副本，核对：

- 遍历全部分页；
- 每条完全同名结果都单独裁剪；
- 所有裁剪按页面顺序纵向拼接；
- 多家公司写“否”，申请人和有效期留空；
- 同一公司写“是”，按模板要求补充详情；
- 无论“是”或“否”都插入拼接图；
- 源文件哈希不变。

- [ ] **Step 6: 验证登录过期和恢复**

查询中使会话进入 SSO、验证码或 `about:blank` 中一种可复现场景。确认任务先保存检查点再暂停；员工从官网首页恢复并登录后，程序只重做当前未完成行，已完成行不重复。

- [ ] **Step 7: 运行最终证据命令**

```powershell
worker\.venv\Scripts\python.exe -m pytest worker/tests -q
npm --prefix apps/desktop test -- --run
npm --prefix apps/desktop run build
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
powershell -ExecutionPolicy Bypass -File scripts\smoke-test.ps1
powershell -ExecutionPolicy Bypass -File scripts\build-desktop.ps1
git status --short
```

Expected: 所有测试和构建退出 0；`git status --short` 只显示验收记录的预期修改，不包含 BuddyAgent 文件。

- [ ] **Step 8: 更新使用说明并提交**

README 必须写明员工操作：启动应用、选择 XLSX、点击打开商标网站、手动登录、等待“可查询”、开始、登录过期时重新登录、完成后另存、清除登录会话和打开本地证据目录。

```powershell
git add README.md docs/verification
git commit -m "docs: record desktop acceptance results"
```
