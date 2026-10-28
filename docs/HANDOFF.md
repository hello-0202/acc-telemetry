# 交接总入口（新会话从这里开始）

> 仓库：`D:\Dev\acc-telemetry-tool\acc-telemetry`（Windows 10/11，Tauri 2 + Vue 3 + FastAPI sidecar）
> 最后更新：2026-10-28 16:2x。读完本文 + 文末「开工前 3 分钟」再动代码。

ACC（Assetto Corsa Competizione）遥测工具：Rust/Tauri 桌面壳 + Python sidecar 读游戏共享内存 →
WebSocket 推帧 → Vue 前端画实时面板 / 圈速分析。当前主线是**把 UI 改造成可主题化**
（用户写一个 HTML 主题包就能替换悬浮窗 / 主面板外观，数据由宿主灌进去）。

## 0. 文档地图

| 文件 | 内容 |
| --- | --- |
| `docs/HANDOFF.md`（本文） | 进度、待验项、下一步任务、开工检查 |
| `docs/ENGINEERING-NOTES.md` | 工程踩坑大全（打包 / 圈次 / 连接层 / 悬浮窗 / ACL / 调试手段） |
| `docs/HANDOFF-P3-theme-packages.md` | P3 那一轮的详细交接，任务 0~7 的**实现决策与原因**（本文的上游） |
| `docs/theme-state-contract.md` | 状态契约可读版（93 字段）+ 第十节主题包格式与宿主协议 |
| `docs/theme-authoring.md` | 写主题的教程 + 症状对照表 |
| `docs/theme-template/` | 可直接拷用的最小主题模板（**别放进 `public/themes/`**） |
| `python-server/contracts/telemetry-state.json` | 状态字段**单一真源** |
| `资料/MAP_RENDERING_NOTES.md` | 赛道图渲染设计笔记 |

## 1. 路线图与进度

P0 契约 ✅ → P1 后端 state 推送 ✅ → P2 主题宿主 + 首个预设主题 ✅ →
P3 用户主题包（任务 0~6）✅ → P4 主面板壳化 ✅ → P5 副屏需求（看板窗口已撤销，改用监测面板全屏）✅ → **P6 文档 + 脚手架 ✅** → 下一步：主题市场 / 收尾遗留 #2。

已完成的能力（都能在设置页看到）：

- 主题宿主 `ThemeHost`：iframe 沙箱 + `contain`/`fluid` 两种适配 + `acc:init`/`acc:state`/`acc:ui` 握手 + 4s 失败回退。
- 引导路径 `dev.html`：滑杆控值、1:1/适应、三种底色、state JSON，写主题不用开游戏。
- 用户主题目录 + `theme` 自定义协议（Windows 实际是 `http://theme.localhost/<id>/`）、热重载（静默 500ms debounce）、
  删除走系统回收站。
- 两处主题槽位：`overlay`（悬浮窗）、`monitor`（主面板），localStorage 持久化，内置「内置面板」可回退。
- 应用自定义命令已过 ACL（`src-tauri/permissions/app.toml`），按窗口分配权限。

> 当前改动（P0~P5 主题化主线、撤销 board 窗口、悬浮窗持久化、预览隔离）已随主题化主线提交（见 `git log`）。
> 接手先 `git log --oneline -8` 看清进度；要回滚用 `git reset --soft HEAD~N`。

## 2. 只能真机验证的（重启应用点一遍）

代码侧 `npm run build`、`cargo check`、无头浏览器 UI 都已通过；下面这些**必须在真机 / 真 Tauri 环境验**：

- [ ] ACL 生效：overlay 窗口调不到 `stop_sidecar`（应报 `command not allowed`），调得到 `set_overlay_size`。
- [ ] 加了 ACL 后主窗口三条链路仍正常：悬浮窗显隐、`Ctrl+Shift+L` 编辑模式、锁定按钮。
- [ ] 编辑模式：拖得动、8 向拉得动、`design` 读数 = 窗口逻辑尺寸 − 14px。
- [ ] 用户主题：放一个新主题到用户目录 → 设置页能看到 → 选中 → 悬浮窗切换成功 → 重启应用后仍是它。
- [ ] 热重载：改用户主题 CSS → 1 秒内生效；改 `theme.json` 的 `design` 连带改窗口尺寸。
- [ ] `themes-changed` 事件链路（浏览器里没有 Tauri 事件，此前测不到）。
- [ ] `theme.localhost/<id>/../../config.json` 之类穿越请求被拒；主题里 `document.cookie` / `localStorage` 读不到宿主数据。
- [ ] `overlay-min/theme.js` 里 `window.__TAURI_INTERNALS__` 的实测值（若为 `object` = IPC 确实进了子 frame，ACL 是唯一防线）。

## 3. 遗留问题 / 已完成项

1. ✅ **悬浮窗位置 / 尺寸持久化**（2026-10-28）：`main.rs::place_overlay()` 现在先读
   `%LOCALAPPDATA%\acc-telemetry\window-state.json` 恢复上次几何，没有才贴右上角；
   悬浮窗拖动 / 拉边缩放结束后由 `OverlayView` 经 `save_overlay_rect` 落盘（物理像素，无 DPI 坑）。
   注意交互：主题若声明 `design`，加载时会按 design 重设尺寸、覆盖保存值；无 design 的主题（可拖拽缩放）保存值长期有效。
2. **主题拿不到赛道几何**：内置面板的实时赛道图来自 `getTrackGeometry()`，不在 `state` 里。
   若将来要做带地图的纯主题（如副屏），得单独推一次 `acc:map`（session 变化时推一次，**别塞进 30Hz 的 state**）。
3. ✅ **主题市场的前置条件——预览隔离**（2026-10-28）：设置页放大预览不再嵌在主窗口 iframe，
   改为打开独立 `WebviewWindow`（`label: theme-preview`，见 `src-tauri/capabilities/preview.json`），
   capability 只列 `core:event:allow-listen`/`unlisten`，**没有任何 app 命令**。
   第三方主题在这里被加载也调不到 `stop_sidecar`/`delete_user_theme` 等。新增 `open_theme_preview` 命令（只授 main）。
   设置页卡片缩略图改为静态占位（不再在主窗口内执行主题 JS），点击才开预览窗口。

## 4. P5 副屏需求 —— board 窗口已撤销（2026-10-28）

原方案：独立 `board` 窗口（副屏只读大屏 + 单独一套 board 主题）。
**用户要求撤销**：副屏不需要单独窗口 / 主题——要的是「监测面板自己全屏后拖到第二块屏」，主题就用监测面板自己的主题（设置里可换）。

最终落地：
- 监测面板**本就有全屏能力**：顶栏「全屏沉浸」按钮（或把窗口拖到副屏后最大化）走 `immersive.js::toggleImmersive`，是**真·OS 级全屏 + 藏外壳**；全屏后渲染的正是 `MonitorShell`，用 `themeChoice.monitor` 这个监测面板主题（含实时赛道图）。主题在「设置 → 主面板主题」切换即可。
- 上一轮加的 `board` 窗口 / `board.html` / `BoardView.vue` / `board-window.js` / `toggle_board` 命令 / `allow-toggle-board` ACL / `board` 主题槽位 / 看板选择器，**已全部删除**（`npm run build` + `cargo check` 验证无残留引用；`themes.js` 的 `MODES` 也已去掉 `board`）。

结论：副屏需求由「监测面板全屏」满足，无需单独主题 / 窗口。下一步见 P6。

**改动已随主题化主线提交（见仓库 `git log`）；P0~P5 + 撤销 board + 悬浮窗持久化 + 预览隔离 都已落地。**

## 5. 开工前 3 分钟

```bash
# 1) 看现状
git status --short && git diff --stat
# 2) 前端能跑（不启 Tauri，只验 UI 时更省事）
npm run dev            # 主界面
# 直接开 /dev.html 看主题调试页
# 3) 类型检查（别用默认 target，见 ENGINEERING-NOTES「cargo check 报 PermissionDenied」）
cd src-tauri && cargo check --target-dir ../.workbuddy/target-check
```

- **改 Python 必须重打包 sidecar**（三处 exe 同步），否则 `tauri dev` 跑的还是旧 exe。命令见
  `ENGINEERING-NOTES.md` 第 2 节。
- **`npm run tauri dev` 前别留运行中的 sidecar**（拷 exe 被占用 → `panic, os error 5`）。
- **别和 `tauri dev` 抢构建**，并发写 `target/debug` 会写坏 `incremental/`。
- **造测试共享内存前必须 `ACC_TELEMETRY_DB=<临时库>`**，否则假圈写进用户真实历史（真事故）。
- 真实库位置：`%LOCALAPPDATA%\acc-telemetry\telemetry.db`。

## 6. 不要做的事

- 不改契约字段的名字 / 单位 / 语义；加字段只**追加**，同步 `contracts/telemetry-state.json` 与
  `docs/theme-state-contract.md`。`state.py::STATE_VERSION` 只有破坏性变更才 +1。
- 不把 PB 逐点曲线塞进 `state`。
- 不给 iframe 加 `allow-same-origin`（临时测试除外，测完立刻改回）。
- 不让主题连 WebSocket 或发 `{"cmd":"start"}`。
- 不动 `acc_telemetry/services/telemetry_service.py` 之外的字段同步规则（两套实现要一起改）。

## 7. 本轮回退（P6 收尾）

- 主题化主线（P0~P5）已提交，board 副屏窗口按用户要求撤销；本轮再补两项遗留：悬浮窗位置 / 尺寸持久化、预览隔离窗口。
- 脚手架：`docs/theme-template/` 是可拷用的主题最小模板（别放进 `public/themes/`），写主题照这个起手即可。
- 下一步方向：主题市场（预览隔离已具备前置条件）、带地图的纯主题（需推 `acc:map`）、悬浮窗持久化的细节打磨（如多显示器记忆）。
