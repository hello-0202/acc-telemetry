# 交接：P3 用户主题包（历史存档，任务 0~7 已全部完成）

> ⚠️ **新会话请先读 `docs/HANDOFF.md`**（总入口：当前进度 / 待真机验证项 / 下一步 P5）。
> 本文保留 P3 期间的**实现决策与原因**，排查「为什么这么做」时再回来查。
> 仓库：`D:\Dev\acc-telemetry-tool\acc-telemetry`（Windows，Tauri 2 + Vue 3 + FastAPI sidecar）

---

## 0. 一句话背景

这是一个 ACC（Assetto Corsa Competizione）遥测工具：Rust/Tauri 桌面壳 + Python sidecar 读游戏共享内存 →
WebSocket 推帧 → Vue 前端画实时面板和圈速分析。当前在把「面板 UI」改造成**可主题化**：
用户写一个 HTML 主题包就能替换悬浮窗 / 主面板的外观，数据由宿主灌进去。

## 1. 现在到哪一步了

路线图：P0 契约 ✅ → P1 后端 state 推送 ✅ → P2 主题宿主 + 1 个预设主题 ✅ →
P3 用户主题包 ✅（0~6 全部完成）→ **P4 主面板壳化 ✅** → P5 副屏需求（看板窗口已撤销）→ P6 文档 + 脚手架。

**注意：P0~P4 全部改动还在工作区里，没有 commit。** 上手先 `git status` / `git diff` 看清现状，
再决定是否先提交一个 checkpoint。

P4 已完成（见「任务 7」）。**P5 原计划做看板窗口**，但按用户要求已撤销：副屏需求改由「监测面板全屏（immersive）」满足，未单独做 board 窗口 / board 主题。若日后重做，仍可参考下面的设计笔记。

### 已存在的关键文件

| 位置 | 作用 |
|---|---|
| `python-server/contracts/telemetry-state.json` | 93 个字段的**单一真源**（path/type/unit/source/desc + enums + example） |
| `docs/theme-state-contract.md` | 可读版契约 + 第十节「主题包格式与宿主协议」 |
| `python-server/backend/api/state.py` | `build_state()`：Snapshot + 会话元信息 → 扁平 state；算 `lap.deltaMs` / `car.gearText` / `lap.num` |
| `python-server/backend/api/broadcaster.py` | 推 `{"type":"state"}`：录制中 30Hz，待机 2Hz |
| `python-server/backend/api/routes.py` | `GET /api/state`、`GET /api/contract` |
| `python-server/backend/storage/recorder.py` | `_publish_meta()` 把赛道/车/PB 参考挂到 `bus.runtime["state_meta"]` |
| `src/components/ThemeHost.vue` | iframe 沙箱 + contain 缩放 + postMessage 握手 + 用户主题热重载 |
| `src/components/ThemePreview.vue` / `LazyMount.vue` | 设置页卡片缩略图 / 放大预览（懒挂载） |
| `src/components/ThemeDevView.vue` + `dev.html` | 主题调试页（滑杆控数值、1:1、三种底色、state JSON） |
| `src/telemetry/theme-watch.js` | `themes-changed` 事件单例订阅 |
| `src/telemetry/themes.js` / `theme-choice.js` | 主题清单与校验（唯一一份规则）/ 选择持久化（localStorage） |
| `src/telemetry/theme-url.js` | 主题基址：内置 `/themes/<id>/`，用户 `http://theme.localhost/<id>/` |
| `src/telemetry/mock-state.js` | 演示数据（直接取契约 `example`）+ `baseMockState()` |
| `docs/theme-authoring.md` / `docs/theme-template/` | 主题作者教程 / 可拷用的最小模板 |
| `public/themes/overlay-min/` | 唯一的内置主题（theme.json / index.html / theme.css / theme.js） |
| `src/telemetry/overlay-window.js` | 悬浮窗显隐状态 + `setOverlayLocked()` |
| `src-tauri/src/main.rs` | overlay 窗口命令、全局快捷键、sidecar 生命周期 |
| `src-tauri/capabilities/overlay.json` | overlay 窗口的能力白名单 |

### 技术要点（别重新发明）

- **缩放**：主题按 `theme.json.design` 的像素写死尺寸，宿主用 iframe 固定 design 尺寸 + `transform: scale(k)` 等比缩放居中，
  `k = min(容器宽/design.w, 容器高/design.h)`。不要把 rem/vw 注入主题里。
- **握手**：宿主 `load` 后每 400ms 重发 `acc:init`，主题收到就回 `acc:ready`（幂等，宿主忽略重复）；
  4 秒没 ready 显示「加载失败 + 重试」。
- **数据**：主题**只收** `acc:state`，不连 WebSocket、不发 start/stop。
- 主题可离线预览：双击 `index.html`（`file://`）2 秒后自动跑 mock 数据。

## 2. 三条硬规则（违反就是设计事故）

1. **iframe 必须 `sandbox="allow-scripts"`，不准加 `allow-same-origin`。** 主题拿不到宿主 DOM / localStorage。
2. **主题不碰 WebSocket，不控制录制。** 数据只有一条路：宿主 postMessage 灌进去。
3. **state 消息里绝不放逐点曲线。** PB 参考圈有上千点，只给 `lap.pbMs` / `lap.deltaMs`；
   逐点曲线走一次性的 `pbref` 事件（那是历史图表用的）。

## 3. 本轮任务（按顺序做，每步可独立验收）

### 任务 0 ✅ 已完成：给应用自定义命令加 ACL —— 接用户主题前唯一的安全前提

**结论已经查过，不用再从零调研**：

- Tauri 的 IPC 是靠 initialization script 注入 `window.__TAURI_INTERNALS__` 的，底层是 WebView2 的
  `AddScriptToExecuteOnDocumentCreated`，**对所有 frame 生效**。`sandbox="allow-scripts"` 只隔离 DOM / 存储 /
  同源，**挡不住注入的 IPC 对象** —— 所以"主题放在 sandbox iframe 里就安全"是错的。
- 更糟的是本项目现在**没有 `src-tauri/permissions/` 目录**，等于应用自定义命令完全不过 ACL：
  任何窗口（包括 overlay 里那个主题 iframe）只要能拿到 IPC，就能调 `stop_sidecar` / `set_overlay_size` / `toggle_overlay`。
  （`stop_sidecar` 会直接杀掉 sidecar，用户当场丢数据流。）

**要做的事**：建 app manifest，把命令权限按窗口最小化。

1. 新建 `src-tauri/permissions/app.toml`，给 6 个命令各声明一条权限：
   ```toml
   [[permission]]
   identifier = "allow-stop-sidecar"
   description = "停止 sidecar（安装更新前必须）"
   commands.allow = ["stop_sidecar"]
   # ... set_overlay_size / set_overlay_locked / toggle_overlay / show_overlay / hide_overlay 同理
   ```
2. capability 按窗口分配：
   - `default.json`（label `main`）：`allow-stop-sidecar`、`allow-toggle-overlay`、`allow-set-overlay-locked`
   - `overlay.json`（label `overlay`）：`allow-set-overlay-size`、`allow-set-overlay-locked`
   - `show_overlay` / `hide_overlay` 目前前端没有调用点，先不授；要用时再补。
3. **注意这是"一刀切"**：一旦 `permissions/` 目录存在，**所有**应用命令都受 ACL 管，
   漏授一条的表现是「按钮点了没反应」+ 控制台 `command not allowed`，排查时容易误以为是代码 bug。
   改完必须把这三条链路都点一遍：开始/停止监测（不涉命令）、顶栏悬浮窗显隐、`Ctrl+Shift+L` 编辑 + 锁定按钮。
4. 顺手把 `typeof window.__TAURI_INTERNALS__` 在主题里 `console.log` 一次，把实测结论写进
   `docs/theme-state-contract.md` 第十节（做主题市场 / 分享主题时这就是风险评估依据）。

**验收**：在 overlay 的 devtools 里 `__TAURI_INTERNALS__.invoke('stop_sidecar')` 报 not allowed；
`invoke('set_overlay_size', {width: 400, height: 170})` 仍然有效；拖拽 / 锁定 / 主题按 design 调尺寸三条路都活着。

> 做完这一步，用户主题就**可以**继续留在 iframe 里跑（不用另开窗口），因为越权路径已经被 ACL 掐断。
> 只有当你希望"主题连自己窗口的尺寸都改不了"时，才需要走独立 WebviewWindow 那条重方案。

**落地结果（2026-10-28）**：

- `src-tauri/permissions/app.toml` 声明 6 条权限（`allow-stop-sidecar` / `allow-toggle-overlay` /
  `allow-show-overlay` / `allow-hide-overlay` / `allow-set-overlay-locked` / `allow-set-overlay-size`）。
  应用级权限在 capability 里**不带命名空间**，直接写 `"allow-stop-sidecar"`；构建后会出现在
  `gen/schemas/acl-manifests.json` 的 `__app-acl__` 下（可用来核对有没有生效）。
- 授权分配：`default.json`(main) 给 stop-sidecar / toggle / show / hide / set-locked；
  `overlay.json` 只给 set-overlay-size / set-overlay-locked。
- 双保险：`stop_sidecar` 签名改成 `(app, window: tauri::WebviewWindow) -> Result<(), String>`，
  非 `main` 窗口直接返回 `Err`（防 ACL 以后被改宽）。
- **还没在真机点过**（下面「验收」那几条要手动过一遍：`vite build` + `cargo check` 已通过）。

### 任务 1 ✅ 已完成：主题目录 + `theme://` 协议

- 用户目录：`%LOCALAPPDATA%\acc-telemetry\themes\<id>\`（与 `config.json` / `telemetry.db` 同目录，
  常量在 `python-server/acc_telemetry/config.py::APP_DIR`）。目录不存在就创建。
- 内置目录：打包后的 `dist/themes/`（源码里是 `public/themes/`）。**同名时用户目录优先。**
- Rust 侧注册自定义协议：`register_uri_scheme_protocol("theme", ...)`，把 `theme://localhost/<id>/<file>`
  映射到上述两个根目录之一。要求：
  - **URL 形式按平台不同**：Tauri 2 在 Windows / Android 上自定义协议实际表现为
    `http://theme.localhost/<id>/...`，macOS / Linux 才是 `theme://localhost/...`。
    不要手拼前缀，用 `@tauri-apps/api/core` 的 `convertFileSrc()` 或用 `tauri::Url` 处理，否则 Windows 上必踩。
  - `canonicalize()` 之后校验前缀仍在根目录内（防 `../` 穿越），拒绝越界
  - 扩展名白名单：`.html .css .js .json .svg .png .jpg .webp .woff2 .ttf`
  - 返回正确的 `Content-Type`；未知文件返回 404，不要泄露绝对路径
- `ThemeHost.vue` 的 `src` 从 `/themes/<id>/index.html` 改成上面那个协议地址
  （dev 模式下如果协议不可用，退化为 http 相对路径 —— 两条路都要能跑）。
- **验收**：`theme://localhost/../config.json` 之类的穿越请求被拒；把 `overlay-min` 整个目录复制到用户目录改名后能加载。

**落地结果（2026-10-28）**：

- 协议只服务**用户主题**。内置主题编译进二进制（`frontendDist` 内嵌），磁盘上没有可读目录，
  所以内置继续走站点资源路径 `/themes/<id>/`，`ThemeHost` 新增 `source` prop 决定走哪条。
- `src/telemetry/theme-url.js::themeBaseUrl(id, source)` 统一出地址（Windows → `http://theme.localhost/`，
  其他 → `theme://localhost/`），业务代码不手拼。
- `main.rs::serve_user_theme()`：id 字符集校验 + URL 解码后拒 `..`/`.`/`\\`/NUL + `canonicalize` 后比前缀 +
  扩展名白名单 + `no-store` + `ACAO:*`；未知/越权一律 404。
- 启动时 `create_dir_all(%LOCALAPPDATA%\acc-telemetry\themes)`，用户能找到该往哪儿放。
- **没在真 WebView 里跑过**：`theme.localhost` 的子 frame 加载、`fetch theme.json` 的 CORS 需要实机确认。
  万一 WebView2 不放行自定义协议的 subframe，退路是把用户目录加进 asset 协议 scope 并用 `convertFileSrc()`。

### 任务 2 ✅ 已完成（实现方式和本文原计划不同，见下）：主题清单 + 校验

- 新增 `GET /api/themes`（放 `python-server/backend/api/routes.py`，或新建 `themes.py`），返回：
  ```json
  { "themes": [ { "id": "overlay-min", "name": "极简 HUD", "version": "1.0.0",
                  "author": "acc-telemetry", "desc": "...", "design": {"w":380,"h":150},
                  "fit": "contain", "modes": ["overlay"], "source": "builtin|user",
                  "error": null } ] }
  ```
- 校验规则（不满足则该条 `error` 填原因，**绝不让整个接口 500**）：
  `theme.json` 能被解析；`id` 与目录名一致；`design.w/h` 是 80..8192 的整数；
  `fit ∈ {contain, fluid}`；`modes` 非空且 ⊆ `{overlay, monitor}`；
  同目录存在 `index.html`。
- 排序：用户主题在前，内置在后，各自按 name 排。

**为什么不按原计划做 `GET /api/themes`**：内置主题是 `frontendDist` 内嵌进二进制的，**磁盘上没有可读目录**，
Python 扫不到；而用户目录 Python 能扫、Rust 也能扫。所以改成：

- Rust 命令 `list_user_themes()` 只负责把用户目录里的 `theme.json` 原文 + `has_index` 交出来（不做业务校验）；
- 校验规则集中在 `src/telemetry/themes.js::checkMeta()`，**只写一份**，避免前后端两套规则漂移；
- 内置主题在 `BUILTIN_THEME_IDS` 里显式登记（`public/` 下的文件没法自动枚举）。
- 好处：不用改 Python、不用重打包 sidecar，规则改动只动前端。

### 任务 3 ✅ 已完成（存储位置改了）：选择器 UI + 持久化

- `config.json`（`acc_telemetry/repositories/config_repo.py::DEFAULTS`）加键：
  `"overlayTheme": "overlay-min"`。走现有 `GET/POST /api/config`，前端镜像加到 `src/telemetry/prefs.js`。
- 位置：`SettingsView.vue` 新增「外观 / 主题」分区，卡片列表（名称、作者、design 尺寸、内置/用户标记、
  坏主题显示红色错误原因并被禁用）。选中即写后端配置。
- `OverlayView.vue` 的 `theme="overlay-min"` 改成从 `prefs.overlayTheme` 读；切换后要重新
  `fitToTheme`（主题变了，design 也可能变）。
- **验收**：设置页选中另一个主题 → 悬浮窗**当场重建**并套用新主题的 design 尺寸；
  重启应用后仍是上次选的主题（值落在 `config.json` 的 `overlayTheme`）。

**落地结果（2026-10-28）**：

- **没用 `config.json`**：`POST /api/config` 是白名单收键的（`acc_telemetry/api/config.py`），
  加 `overlayTheme` 要改 Python + 重打包 sidecar。主题选择只是本机界面偏好，所以存 localStorage `acc.overlayTheme`
  （`src/telemetry/theme-choice.js`）。主窗口与悬浮窗同源，共享同一份；切换后主窗口 `emit('overlay-theme-changed')`，
  悬浮窗监听后重读并重建 iframe。
- 设置页新增「悬浮窗主题」分区（`SettingsView.vue`）：卡片列表（名称 / 内置·用户 / design / 作者 / 描述）、
  坏主题标红且「使用」按钮禁用、「打开主题文件夹」+「刷新」、用户主题可删除（二次确认 → 系统回收站）。
- 删除当前正在用的主题 → 自动回退到内置 `overlay-min` 并通知悬浮窗，不会留空白。
- 想在 `config.json` 里存的话：给 `config.py::DEFAULTS`/`_values_to_payload`/`POST` 白名单加两个键，
  再把 `theme-choice.js` 换成走 `getConfig/saveConfig` 即可（接口保持一样）。

**补充（同样 2026-10-28）：卡片缩略预览 + 放大预览弹窗**

- `LazyMount.vue`（进视口才挂 iframe / 设置页切走即销毁）、`ThemePreview.vue`（模拟游戏画面底 + ThemeHost，
  `pointer-events:none`）、`mock-state.js`（演示数据**直接取契约 `example`**，500ms 一帧）。
- 缩略图静态、放大预览才跑定时器；定时器模块级单例，订阅归零即停。缩放沿用 `ThemeHost` 的 contain，不另算。
- 弹窗舞台按主题 `design` 的 `aspect-ratio` 撑开，主题满宽；`Esc` 关闭；`keydown` 要 `stopPropagation`。
- ⚠️ **预览 iframe 在主窗口里，继承 main 的 ACL 授权**（ACL 是按窗口的，不分 frame）。
  所以「只预览可信主题」；将来做主题市场必须先换独立 `WebviewWindow`
  （capability 只留 `core:event:allow-listen`、不给任何 app 命令，数据靠 `emit`）。


### 任务 4 ✅ 已完成（实现和原计划两处不同）：热重载

落地方案：

- Rust 加 `notify = "8"`，`spawn_theme_watcher(app)` 在 `setup` 里起一个线程，递归监听用户主题目录。
- **debounce 用「攒到静默 500ms」**（`recv` 拿第一条，然后 `recv_timeout(500ms)` 一直 drain 到超时），
  而不是 300ms 定时器 —— 编辑器一次保存会连发创建临时文件 / 改名 / 属性变化多条事件，
  定时器方案会在这批事件中间就触发。
- 事件名是 **`themes-changed`**（不是 `theme-changed`），载荷是**受影响主题 id 的数组**：
  `emit("themes-changed", ["my-hud"])`。理由：前端不该知道磁盘布局，只关心"我用的那个变了吗"；
  数组是因为一次可能粘贴进去好几个目录。根目录下的零散文件（不属于任何主题）不触发。
- 前端 `src/telemetry/theme-watch.js`：模块级单例订阅（`onThemesChanged(fn) → 退订函数`），
  多个组件共用一个 `listen`，非 Tauri 环境静默降级。
- `ThemeHost` 只在 `source === "user"` 且 id 命中时 `hotReload()`：先 `loadMeta()`（重读 `design`）再
  `reloadKey++`，另有 300ms 最小间隔兜底。内置主题不监听（编译进二进制，磁盘上没目录）。
- 设置页收到同一事件只做 `reloadThemes()`，列表自动跟上（拷进目录即出现）。

**验收**：改 `theme.css` 保存 → 悬浮窗与设置页缩略图 1 秒内更新；改 `theme.json` 的 `design` → 窗口尺寸跟着变。

### 任务 5 ✅ 已完成（形态改了）：主题调试页

原计划是 `public/theme-dev.html`；实际做成 **Vite 第三入口 `dev.html` + `src/dev.js` + `ThemeDevView.vue`**
（`http://localhost:1420/dev.html`）。改形态的原因：要复用 `ThemeHost` 和 `themes.js` 的校验，
纯静态页拿不到这些，且 `public/` 下的页面在构建后路径行为不一样。

- 顶栏：主题下拉（内置 + 用户）、自动波形、1:1 / 适应、底色（游戏画面 / 纯黑 / 透明格）、design 与缩放读数。
- 左栏：5 个场景预设（怠速 / 直道 / 重刹 / 弯中 / 进站）+ 10 个滑杆 + 进站/有效圈开关。
- 主区：`ThemeHost` 预览；1:1 把画布钉成 `design` 尺寸（超容器可滚），适应铺满。
- 底部：可折叠 state JSON + 复制。
- **数据底座是 `mock-state.js::baseMockState()`**（契约 `example` 的深拷贝，不带波形），不另写 mock。
- 「适应」模式的居中用 `margin:auto` 而不是 `justify-content:center` —— 后者在内容大于容器时会让左侧溢到滚不到的地方。

### 任务 6 ✅ 已完成：文档收尾

- `docs/theme-state-contract.md` 第十节已补「热重载」「主题调试页」两节并刷新「当前限制」。
- 新增 `docs/theme-authoring.md`（面向主题作者：目录结构 → theme.json → 协议 → 字段表 → 三档调试 → 症状对照 → 安全边界）。
- 新增最小模板 `docs/theme-template/`（`theme.json` + `index.html` + `theme.css` + `theme.js`，可直接拷用）。
  **别放进 `public/themes/`** —— 那里会被当成真主题列出来。

### 任务 7 ✅ 已完成（P4）：主面板壳化

`MonitorView`（外壳）→ `MonitorShell`（二选一）→ `MonitorDashboard`（内置面板）/ `ThemeHost mode="monitor"`（主题）。

- 选择存 localStorage `acc.monitorTheme`，`theme-choice.js` 按槽位（overlay/monitor）分开存，
  同窗口内用响应式 `themeChoice[slot]` 同步（不用事件）。
- `BUILTIN_PANEL = { id: '_panel' }` 是「内置面板」占位，不是主题；主题加载失败自动回退到它。
- 设置页两个 `ThemePicker`（按 `modes` 过滤），原来写死在 `SettingsView` 里的一整套主题逻辑搬进了组件。
- **踩到的真 bug（写主题必看）**：`isFinite(null)` 为 true → `null.toFixed()` 抛错 → 真机 idle 帧全 null 时
  主题只渲染到一半（速度恒 0、胎温却正常）。已在 `monitor-grid/theme.js` 用 `num()` + `try/catch` 修掉，
  也写进了 `theme-authoring.md` 第六节第 8 条。

## 4. 遗留 / 已知问题（顺手修，不阻塞）

1. **IPC 会注入 sandbox iframe（原理上确定），但没实测过**。对策是任务 0 的 ACL，不要靠"主题不可信就不接"来规避。
2. **悬浮窗位置 / 尺寸持久化**：据用户说已由另一个 AI 处理，但当前工作区里 `main.rs::place_overlay()`
   仍然只在第一次显示时贴右上角、没有任何 x/y/w/h 存取代码 —— **接手前先确认那份改动在哪儿（别的分支？没合进来？）**，
   别重复实现，也别以为已经好了。
3. **编辑模式（`Ctrl+Shift+L`）刚修过但没实机验证**：无边框窗口没有原生边框，
   已改成自建 8 向热区 + `startResizeDragging`，并加顶栏「调整悬浮窗」按钮兜底。
   接手后先重启应用点一遍，确认拖得动、拉得动、`design` 读数正确（应等于窗口逻辑尺寸 − 14px）。
4. `fit: "fluid"` 已在 P4 实现（iframe 铺满容器、不缩放，主题自己用根字号响应式），看板窗口可直接复用。
5. **主面板主题拿不到赛道几何**：内置面板的实时赛道图来自 `getTrackGeometry()`，不在 state 里。
   P5 若要做带地图的看板主题，得单独推一次 `acc:map`（session 变化时推一次，别塞进 30Hz 的 state）。

## 5. 环境与踩过的坑（务必看）

### 改 Python 必须重打包 sidecar

源码运行和打包运行是两条链路。改 `python-server/` 下任何文件后要重新打包并同步**三处**：

```
# 环境（PyInstaller 装在专用 venv 里）
C:\Users\Lenovo\.workbuddy\binaries\python\envs\acc-telemetry-build\Scripts\python.exe

cd python-server
<venv>\Scripts\python.exe -m PyInstaller telemetry_core.spec --noconfirm \
    --workpath build_new --distpath dist_new
```

- **不要加 `--clean`**；**`--distpath` 必须指向一个当前不存在的目录**（目标 exe 已存在时删不掉，构建会静默失败留下旧 exe）。
- 完成后把新的 `telemetry_core.exe` 覆盖到 `python-server/dist/`、`src-tauri/binaries/telemetry_core-x86_64-pc-windows-msvc.exe`、
  `src-tauri/target/debug/telemetry_core.exe`（用 `Copy-Item -Force` 直接覆盖，**不要先 `Remove-Item`**，会被安全删除垫片拦死）。
- 覆盖前先备份旧的到 `.workbuddy/sidecar-backup/<时间戳>/`。
- **`npm run tauri dev` 之前别留正在运行的 sidecar**，否则复制 exe 报 `os error 5`。
- 新增 Python 模块要确认被 import 链带上（`telemetry_core.spec` 的 `hiddenimports` 是空的，
  全靠 Analysis 静态分析）。新路由文件如果不是在 `server.py` 的 import 链上，打包后会 404 而源码运行正常。

### `cargo check` 报 PermissionDenied

现象：`tauri-build` 在 `lib.rs:163` `remove_file` unwrap 失败（os error 5）。
原因：`src-tauri/target/debug/telemetry_core.exe` 被正在运行的应用占用，而改过
`tauri.conf.json` / `capabilities/` 会触发 build script 重跑并尝试删这个副本。

- 用户可能正开着应用 → **不要去杀他的进程**。用独立 target 目录做纯类型检查：
  `cargo check --target-dir ../.workbuddy/target-check`（约 2 分钟，完事把该目录删掉，占 1GB+）。
- 注意 `.workbuddy/` 下的删除会被安全删除垫片拦截（走回收站失败），用
  `powershell -c "[System.IO.Directory]::Delete($p,$true)"` 直接删。

### 造测试数据绝不能污染真实库

验证记录链路（造共享内存假帧 + WS 发 start）时**必须**先设 `ACC_TELEMETRY_DB=<临时库路径>`。
已经出过一次事故：假帧被写进用户真实库，历史里多出一条「圈速概要走线在 50m 方块里」的假圈。
真实库位置：`%LOCALAPPDATA%\acc-telemetry\telemetry.db`。

### 其它

- 帧只在 `bus.runtime["recording"]` 为真时推；`session` 事件早于落库，真正写库在第一帧遥测（`_persist_session`）。
- 前端 `telemetry.status`：`idle|connecting|streaming|waiting|stopped|error`；`waiting` = 已开监测但没进赛道，**不是错误**。
- 改帧字段要同步两套实现：`backend/` 包（现在跑的）和 `acc_telemetry/services/telemetry_service.py`（另一套同步实现，无测试兜底）。
- 版本号 5 处必须一致：`package.json`、`package-lock.json`（两处）、`src-tauri/Cargo.toml`、
  `src-tauri/tauri.conf.json`（单一真源）、`python-server/acc_telemetry/__init__.py::__version__`。

## 6. 本轮不要做的事

- 不要改契约字段的名字 / 单位 / 语义。要加字段就**追加**，并在 `contracts/telemetry-state.json` 和
  `docs/theme-state-contract.md` 同步；`state.py::STATE_VERSION` 只有破坏性变更才 +1。
- 不要把 PB 逐点曲线塞进 `state`。
- 不要给 iframe 加 `allow-same-origin`（任务 0 的临时测试除外，测完立刻改回）。
- 不要让主题去连 WebSocket 或发 `{"cmd":"start"}`。
- 不要一次把 P5 的看板窗口和「主题市场」一起做：分发主题前必须先按安全边界那一节换成独立
  `WebviewWindow`（capability 只留 `core:event:allow-listen`），顺序不能反。

## 7. 完成标准（自检清单）

代码侧已全部通过（`npm run build`、`cargo check --target-dir ../.workbuddy/target-check`、无头浏览器实测 UI）。
下面带 ⬜ 的是**只能在真机上验的**，接手后重启应用点一遍：

- [x] `npm run build` 通过；`cargo check` 通过（用独立 target 目录）
- [ ] 应用命令已过 ACL：overlay 窗口调不到 `stop_sidecar`，调得到 `set_overlay_size`
- [ ] 加 ACL 后主窗口三条链路仍然正常：悬浮窗显隐、`Ctrl+Shift+L` 编辑、锁定按钮
- [ ] 用户目录放一个新主题 → 设置页能看到、能选中、悬浮窗切换成功，重启应用后仍是它
- [ ] 改用户主题 CSS → 1 秒内热重载（改 `theme.json` 的 `design` 也要连带改窗口尺寸）
- [x] 坏主题（theme.json 语法错 / 缺 index.html）在列表里标红，应用不崩
- [ ] `theme.localhost/<id>/../../config.json` 之类的穿越请求被拒
- [ ] 主题里 `document.cookie` / `localStorage` 拿不到宿主数据（opaque origin）
- [x] `dev.html` 能独立跑 mock 数据调样式（已用无头浏览器验过：场景切换、JSON、1:1、底色）
- [x] `docs/theme-authoring.md` 写完了，`docs/theme-template/` 能直接拷用
- [ ] `overlay-min/theme.js` 里 `__TAURI_INTERNALS__` 的实测值（`object` = IPC 确实进了子 frame，ACL 是唯一防线）
