 > GitHub 源码快照：真实 API Key 已移除，默认值为 YOUR_API_KEY。运行后请在模型设置中填写自己的密钥。安装包、依赖及本地业务数据不包含在仓库中。原文档中关于预置公司密钥的描述适用于内部发行版。

# 商标查询助手

独立的 Windows 本地桌面客户端。员工选择 XLSX 后，程序在本机 Microsoft Edge 中执行国家知识产权局商标查询，并把查询字段和证据截图写入一份新的结果 XLSX。

本项目位于 `D:\4.29\TrademarkQueryDesktop`，与 `BuddyAgent_2026.8.12` 完全隔离；运行时不依赖 BuddyAgent 后端、数据库、MQTT、技能或 SOP，也不会修改输入表格。

维护者交接（架构、发版、排障、未闭环事项）见 [`docs/项目交接文档.md`](docs/项目交接文档.md)。

## 员工使用方法

1. 安装并打开“商标查询助手”。
2. 选择 `商标申请.xlsx` 或 `商标查询是否唯一性.xlsx`。程序会自动识别任务类型并检查表头。
3. 点击“打开商标网站”。程序会打开一份仅供本应用使用的可见 Edge 浏览器。
4. 员工在这个专用 Edge 中自行点击“商标综合查询”，按网站要求登录、输入验证码。账号、密码和验证码都不经过本应用。
5. 登录成功并自动回到商标网站首页后，返回客户端点击“我已登录，检查并进入查询”。状态显示“可以开始”后点击“开始查询”。专用 Edge 会移到屏幕外继续渲染和查询，不抢占前台焦点。
6. 单条查询遇到网站临时错误时会先跳过，首轮结束后自动重试一次；不会自动暂停整批任务。只有员工点击“暂停任务”才会暂停。
7. 全部完成后点击“另存结果”，自行选择结果 XLSX 的保存位置。

若旧版在网站已经登录时仍显示“未连接”，或提示“Edge 后台截图服务未能启动”、登录状态检查超时，请关闭旧版助手并升级到 0.1.29。该版本兼容新版 Edge 隐藏地址栏和标签栏自动化信息的情况，并会在结果接口首次返回 HTTP 403 时等待并刷新一次；遇到商标网站“页面已过期”时，每次等待 1 秒，最多刷新 10 次，仍未恢复才将该行放入末轮重试。任务表同时显示商标申请人，商标申请查询会等待详情页完整显示商标状态。查询期间可以点击“查看查询过程”恢复专用 Edge，最终查询失败或未找到的具体原因仍会写入结果表的截图列。

“商标申请”任务会按同名结果中序号从大到小查找带专用权期限的详情，填写申请人、期限并插入完整详情截图。“唯一性”任务会遍历所有结果页，裁剪全部同名商标行、纵向拼接并写入表格；标准化后的申请人只有一家时填写“是”，存在多家时填写“否”。

## 本地数据与隐私

- 独立 Edge 登录资料：`%LOCALAPPDATA%\TrademarkQueryDesktop\edge-profile`
- 任务断点、临时截图和工作副本：`%LOCALAPPDATA%\TrademarkQueryDesktop`
- 登录凭据由 Edge 和目标网站管理，本应用不读取或保存账号密码。
- 登录前检测使用 Edge 的 Windows UI Automation 可访问性控件。批量查询通过只监听 `127.0.0.1` 随机端口的浏览器页面通道在同一标签页执行，截图读取页面渲染；不使用 WebDriver，也不启用 `--enable-automation`。任务期间专用 Edge 保持在屏幕外而非最小化，因此同事切换其他窗口不会使查询暂停。
- 每位员工在自己的电脑安装运行，因此每人的登录会话彼此隔离，并可同时使用。
- 如果结果表的商标列只有图片、没有可读取文字，Worker 可用本地 OCR 和千问多模态识别。0.1.29 安装包预置公司默认配置，员工可在客户端右上角“模型设置”中直接修改 URL、API Key 和模型名称。结果 XLSX 增加“是否调用大模型”列；只要该行曾发起过大模型识别请求，就填写“是”，否则填写“否”，未查询行留空。
- 模型配置保存在 `%LOCALAPPDATA%\TrademarkQueryDesktop\settings.json`，新安装会自动创建；API Key 在界面中默认遮罩。安装包内置密钥无法对本机用户绝对保密，只应在公司内部受控分发。

一次性复用现有配置（管理员操作，独立应用运行时不会依赖原项目）：

```powershell
worker\.venv\Scripts\python.exe scripts\import-ai-settings.py --source D:\path\to\env_config.json
```

## 项目结构

- `worker/`：Python 3.11 查询 Worker、XLSX 处理和浏览器自动化。
- `apps/desktop/`：Vue 3 + Tauri 2 桌面应用。
- `docs/`：产品设计、实施计划、界面验收记录和维护者交接文档。
- `scripts/`：Windows 构建、资源暂存和冒烟测试脚本。

## 开发与测试

开发环境需要 Python 3.11、Node.js/npm、Rust MSVC 工具链、Visual Studio C++ Build Tools 以及 Microsoft Edge。

```powershell
worker\.venv\Scripts\python.exe -m pytest worker\tests -q
npm --prefix apps\desktop test -- --run
npm --prefix apps\desktop run build
cargo test --manifest-path apps\desktop\src-tauri\Cargo.toml
```

启动桌面开发版：

```powershell
cd apps\desktop
npm run tauri -- dev
```

## 构建安装包

在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build-desktop.ps1
```

脚本会先把 Python Worker 打成独立目录，暂存为 Tauri 资源，执行前端及 Rust 测试，再生成当前用户安装的 NSIS 安装包。输出位置：

`apps\desktop\src-tauri\target\release\bundle\nsis\商标查询助手_0.1.29_x64-setup.exe`

构建后的离线协议与关键流程冒烟测试：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\smoke-test.ps1
```
