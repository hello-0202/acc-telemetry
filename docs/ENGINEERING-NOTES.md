# 工程踩坑笔记（跨模块）

> 这里只放「不知道就会踩第二次」的结论，按模块分组。文档索引见 `README.md`，
> 状态契约/主题包协议见 `theme-state-contract.md`，写主题见 `theme-authoring.md`，
> 轮胎磨损模型见 `tyre-wear-model.md`，接手任务见 `HANDOFF.md`。

## 1. 架构与数据流

- 运行链路走 `python-server/backend/` 包（collector → bus → recorder/broadcaster），入口 `python-server/server.py`。
  `acc_telemetry/services/telemetry_service.py` 是另一套同步实现；**改帧字段两套都要同步**（无测试兜底）。
- 帧字段在 `backend/collector/reader.py::snapshot_to_frame()`；只在 `bus.runtime["recording"]`（前端发过 `start`）为真时推。
- 一次性事件：`session` / `history` / `pbref` / `lap` / `trackmap` / `warning` / `error` / `stopped`。
  `session` 早于落库发出，真正写库在第一帧遥测（`_persist_session`），成功后补 `{"type":"history","reason":"session"}`
  → 需要「确认库里那行存在」的前端刷新必须听 `history`。
- 会话类型 `graphics.session`（-1 unknown / 0 练习 / 1 排位 / 2 正赛）映射在 `providers/base.py::session_type_name`；
  -1 由 `recorder._process` 调 `SessionRepository.set_type()` 回填（只补 unknown）。
- 状态流（P0 起）：`acc_provider.read()` → `bus.latest_snapshot` → `recorder::_publish_meta()` 挂
  `runtime["state_meta"]` → `state.py::build_state()` → `broadcaster` 推 `{type:"state"}`。
  **组装不查库、不加锁；PB 参考只留内存算 delta，绝不进消息**（上千点会撑爆 30Hz）。

## 2. 打包 sidecar（改后端必做）

- 重建并同步三处：`python-server/dist/`、`src-tauri/binaries/telemetry_core-x86_64-pc-windows-msvc.exe`、
  `src-tauri/target/debug/telemetry_core.exe`。
  venv：`C:\Users\Lenovo\.workbuddy\binaries\python\envs\acc-telemetry-build`
- 命令：`cd python-server && <venv>\Scripts\python.exe -m PyInstaller telemetry_core.spec --noconfirm --workpath build_new --distpath dist_new`
  - **不加 `--clean`**；
  - **`--distpath` 必须指向一个当前不存在的目录**（目标 exe 已存在时删不掉 → Building EXE 前静默失败，留下旧 exe）。
- 覆盖用 PowerShell `Copy-Item -Force`，**不要先 `Remove-Item`**（预删除会被 safe-delete 垫片拦死）。
- 覆盖前备份到 `.workbuddy/sidecar-backup/<时间戳>/`，出事直接覆盖回去。
- `.spec` 的 `datas` 必须带 `contracts/*.json`（`telemetry-state.json` 缺了 → 打包后 `/api/contract` 404）。
- `hiddenimports` 是空的，全靠 Analysis 静态分析 → 新模块必须在 `server.py` 的 import 链上，
  否则源码运行正常、打包后 404。
- `npm run tauri dev` 之前别留正在运行的 sidecar（拷 exe 被占用 → `panic, os error 5`）。
  debug 那份拷不进去时不用杀用户进程：`binaries/` 换了之后 dev 构建会自己刷新 `target/debug/`，用 SHA256 核对即可。
- 改了路径相关代码，可用 venv 里的 `PyInstaller.archive.readers.CArchiveReader(exe).open_embedded_archive('PYZ.pyz')`
  抽出模块 code object 检查 `co_consts` / `co_names`，不必真的再起一个后端进程验证。

## 3. 版本管理

- 5 处必须一致：`package.json`、`package-lock.json`（root 与 `packages.""` 各一处，常漏）、`src-tauri/Cargo.toml`、
  `src-tauri/tauri.conf.json`（**单一真源**）、`python-server/acc_telemetry/__init__.py::__version__`。`Cargo.lock` 自动跟随。
  当前 `1.1.0`。改版本后 sidecar 必须重打包（exe 里烤的是 `__version__`）。
- 发版：改齐五处 → 更新 `CHANGELOG.md` → `git tag vX.Y.Z && git push origin vX.Y.Z`
  → `.github/workflows/release.yml`（tauri-action, windows-latest）自动构建 sidecar、出安装包、建 draft release。

## 4. 赛道地图（`src/components/map/`）

- `model.js::buildTrackGeometry`：弧长等距重采样(3m) → 按米平滑 → 中心差分法线 → 边距受 `0.9×局部转弯半径` 限制防自交。
- 分层绘制 `draw.js::drawMiniMap`：路面 → AI 中心线（热力开时不画）→ 热力 → 本圈/PB 走线 → 起终点 → 弯心 → 比例尺。
- 热力 = 按踏板给路面着色：中心线取 `M.left/right` 中点（**不用 `M.centers`**，点数不同会错位），
  线宽取左右边界距离，用 `#mapRoadClip` 裁到路面。**`clipPath` 必须写在 `#mapZoom` 里面**，否则缩放时热力消失。
- fastlane 的 `left/right` 是「行进方向左右」，世界 x-z 逆时针法线给的是右手边 → **`left = c - n*l`、`right = c + n*r`**。
  装反会让路面偏到 AI 线另一侧（2026-10-23 真 bug）。校验可用历史库里的实跑 `wx/wy` 按 `(x, -z)` 复算比对。
- 弯心 `apex.js::detectApexes` 用**转角累计法**（每点转向角分段、段内取半径最小处）；
  `minTurnDeg≈15° / floorDeg≈0.8° / minGapM≈45m`；起跑线旋转到最长直道中央防跨接缝。弯名表已下线，只显示 T 编号。
- **走线精度（方案 B）**：`samples` 按时间等距抽稀（`lap_processor._downsample`）并保留真实 `p` + `wx/wy`。
  `p` 因此不再单调 → 凡「按 p 查值」先过 `sample_util.py::monotonic_progress()`（前端 `adapt.js::monotonicProgress`），
  已接入 `sample_repo._reference`、`from_track.build_spline_from_samples`、前端 `lapFromDetail`。
- 前端走线用 **`lap.line`**（时间序，每点带 `th/br`），不用 `lap.X/Y`（按 p 等分会压扁绕圈）；
  游标用 `lap.lineMono`（可二分）。**别再按 `p × 点数` 取下标**。老记录 `wx/wy` 为 NULL 时 `line` 回退 `X/Y`。

## 5. 圈次判定（`lap_processor`，历史 bug 最多的地方）

- ACC 过线**先加 `completedLaps`，下一帧才把 `normalizedCarPosition` 绕回 0**；`iLastTime` 保留旧值到下次过线。
  边界判断必须容忍这个错位，否则同圈判两次（历史 bug：L2/L3 同圈速 2:05.225）。
- **过线后 1~2 帧的旧进度是头号陷阱**：`p≈0.9999` 但 `iCurrentTime` 已归零，照抄会把 `_max_progress` 抬回 0.99，
  约 5s 后「进度回绕」再成立 → 误判第二个边界，该圈开头整段丢失（首点固定 `t≈5000ms`）。
  现跳过「过线后 1s 内、估计进度 <0.5 却报 p>0.9」的帧。
- 另两道保护：边界重置时 `_max_progress` 不继承 >0.5 的旧进度；`_finalize` 里同圈速 + 圈计数器未前进 → 丢弃。
  排查重复圈看 `samples` 的 `t_ms` 跨度：几秒却带完整圈速的就是假圈。
- priming（开始监测时车已在圈中 → 等第一次过线再从 t≈0 起算）：「起跑线附近直接 begin」**必须每帧都判**。

## 6. 记录链路（`recorder`）

- `run_worker` 绝不能因单帧异常退出。曾因 `_trim_lead_in` 写错（模块级函数当方法调）`AttributeError` 静默死掉
  —— 前端仍显示「监测中」、帧照推，但一圈都不落库。现在每帧过 `_safe_process()`，外层再兜底。
- 验证必须端到端（造共享内存 + WS 发 `start` + 查库）；只测 `_process` 会漏 `run_worker` 的缓冲回放分支。
- 造段要点：`playerCarID` 且 `carIDs[0]=playerCarID` + `carCoordinates[0]`；字符串字段直接 `s.trackName="monza"`。
- 旧实例占着 8008 时，新 sidecar 只建空库、WS 仍连旧实例 → 查库别认错文件。

## 7. 前端连接层（`useTelemetry` / 自动开始）

- `telemetry.status`：`idle|connecting|streaming|waiting|stopped|error`。
  `waiting` = 已开监测但没进赛道（`GAME_NOT_FOUND` 不置红）；模块级 `wanted` 区分「用户按停后迟到的 GAME_NOT_FOUND」。
- 自动开始由 `MonitorView.onMounted`：**必须先 `await loadPrefs()`** 再按 `prefs.autoStart` 发 `start`。
- 监督循环 `tickSupervisor()`（3s）：`userStarted` + 非 streaming/connecting + `ui.backendOnline===true` +
  `ui.gameRunning!==false` 才发 start；另有 `watch(ui.backendOnline)` 补一次。
  **不要用 `telemetry.session` 当守卫**（游戏退出后 session 仍在 → 再也不续接）。
- 冷启动到 `/health` 可用要 5.8~6.2s，前端必然先失败一次。三道防线缺一条就弹假警告：
  ① `api.js: probeRound()` **并行**探 12 个端口（串行 12×350ms≈9s 会错过刚起来的 8008）；
  ② `App.vue: BOOT_GRACE_MS=10000` 宽限期内 `showNotice` 恒 false，探到即 `endBootGrace()`；
  ③ 自动开始（`{auto:true}`）不在 boot 期发 start，交给监督循环。
- 连接类失败会粘住界面：`connectFailed` + `clearConnectError()` 在 `/health` 成功时清（**只清连接类**）。
- 自动待命时游戏没开不弹提示，靠 `ui.manualStart` 区分（只有亲手点开始为 true）。
- `start()` 复用已开 socket 时，若 `telemetry.session` 已存在不要改状态为 `connecting`（会永远挂「连接中…」）。

## 8. UI 外壳：全屏沉浸

- 全屏与悬浮窗按钮在顶栏右侧 `App.vue: .top-right`（仅 `view==='monitor'` 显示）；面板内 `.mon-action` 只剩「开始监测」。
- 沉浸态隐藏 `.win-bar` / `.topbar` / `.statusbar` / `.mon-top`，右上角改浮动控件组 `.imm-tools`（悬浮窗 + 退出 ESC，默认 `opacity:.42`）。
- 没监测时 `.live-wrap` 是 `v-show` 隐藏的 → 沉浸态一片黑，必须靠 `.imm-empty` 兜底；`.live-off` 占位要加 `!immersive`。
- **整页比例由根字号决定**：窗口态 `html{font-size:clamp(13px,0.45vw+0.55vh+4.3px,22px)}`（1000×680 下正好 13px）；
  沉浸态 `html.immersive{font-size:min(1.3vw,2.28vh)}`（= 13px × `min(vw/1000, vh/570)`，570 是去掉三条外壳后的内容区高度）。
  **不能沿用窗口态那条 clamp**：1920×1080 只到 18.24px（k=1.4），可视面积却涨 1.9 倍 → 面板又小又扁。
- 实测 1920×1080 vs 1000×680：根字号 24.62 vs 13（k=1.894），面板宽 1.921×、hero 高 1.879×。
- `.mon-shell` 必须 `height:100%`（父 `.live-wrap` 是 `display:block`），用 `flex:1` 会塌成内容高度。

## 9. 悬浮窗（`label:"overlay"`）

- 透明 / 无边框 / `alwaysOnTop` / `skipTaskbar` / `focus:false`；`set_ignore_cursor_events(true)` 鼠标穿透；
  `place_overlay()` 首次贴主显示器右上角。
- `Ctrl+Shift+O` 显隐、`Ctrl+Shift+L` 编辑模式（`tauri-plugin-global-shortcut`，常量 `OVERLAY_SHORTCUT`）。
  Rust 命令 `show_overlay` / `hide_overlay` / `toggle_overlay`（返回新可见性）。注册失败 emit `overlay-shortcut-error`。
- 双入口：`overlay.html` → `src/overlay.js` → `OverlayView.vue`；`vite.config.js` 多入口，能力 `capabilities/overlay.json`。
- **编辑模式**：解锁 → 关穿透 + `set_focus`；
  拖动用 `.ov-grab` 覆盖层显式 `startDragging()`（`data-tauri-drag-region` 只认事件 target 自身，点子元素就失效）；
  **缩放不能靠 `resizable:true`** —— 无边框窗口没有 non-client area，Windows 不给原生拉边，
  必须自建 8 向热区 + `startResizeDragging(dir)`（权限 `allow-start-resize-dragging`）。
- 尺寸读数：`innerSize()` 是**物理像素**，显示 design 前必须除以 `scaleFactor`；`set_overlay_size` 用 `LogicalSize`。
  工具条显示 `design = 窗口逻辑尺寸 - 14`（6×2 内边距 + 1×2 边框）。失焦回锁要延迟复核（拖动结束那一下也会掉焦点）。
- 前提：ACC 必须窗口化/无边框（用户已确认「全屏已禁用」）。独占全屏下普通置顶窗口盖不住，需 D3D Present hook。
- **位置 / 尺寸持久化（2026-10-28）**：`place_overlay()` 首次显示先读「配置文件目录」下的
  `window-state.json`（默认安装目录；可在设置里改，见第 12 节）恢复上次几何，没有才贴右上角；`OverlayView` 在 `onMoved`/`onResized` 后 400ms 调 `save_overlay_rect` 落盘（物理像素，避开 DPI 换算坑）。注意：主题若声明 `design`，加载时会按 design 重设尺寸、覆盖保存值；无 design 的主题（可拖拽缩放）保存值长期有效。

## 10. 主题宿主与安全边界

- **Tauri IPC 由 WebView2 `AddScriptToExecuteOnDocumentCreated` 注入，对所有 frame 生效** → iframe `sandbox`
  挡不住主题调应用命令。唯一防线是 ACL：`src-tauri/permissions/app.toml`（9 条），按窗口挂在 `capabilities/*.json`。
  capability 里应用级权限**不带命名空间**（`"allow-stop-sidecar"`），生效与否看 `gen/schemas/acl-manifests.json` 的 `__app-acl__`。
  按窗口授权：main = `stop_sidecar` + 全部悬浮窗命令；overlay = 仅 `set_overlay_size` + `set_overlay_locked`。
  `stop_sidecar(app, window: WebviewWindow)` 非 main 直接 `Err`（双保险）。
- **多悬浮窗（2026-10-28）**：最多 3 个，窗口 label 固定为 `overlay-1/2/3`（capability 用 glob `overlay-*`）。
  1 号在 `tauri.conf.json` 里静态创建，2/3 由 `set_overlay_slots` 按需创建、停用时 `destroy()`。
  **创建窗口只能在非主线程**：同步命令和事件回调都在主线程，`WebviewWindowBuilder::build()` 在 Windows 上会死锁
  （wry#583），所以显隐/编辑快捷键的回调里都 `std::thread::spawn` 后再建；`show/hide/toggle_overlay` 已改成 async 命令。
  状态：`OVERLAY_ENABLED: Option<Vec<u8>>`（None = 还没被前端推过 → 默认只有 1 号）、
  `OVERLAY_PADDED/PAD_OFFSET/PLACED` 按槽位索引；几何存 `window-state.json` 的 `overlays["1"]...`（旧的 `overlay` 键兼容 1 号）。
  主题按槽位存 localStorage `acc.overlayTheme.<n>`（1 号兼容旧键 `acc.overlayTheme`），换主题事件带 `{slot}`。
  **注意**：`readThemeChoice()` 以 localStorage 为准并同步本窗口的 reactive 副本 —— 各悬浮窗是独立 webview，
  只读内存副本会读到旧主题。每个显示的悬浮窗会各自连一条 WS（后端已支持多客户端）。
- **主题模板包（2026-10-28）**：`public/templates/theme-template.zip`（悬浮窗）与
  `theme-template-monitor.zip`（主面板）是「导入主题包」能直接吃的安装包；Rust 用
  `include_bytes!` 编译进二进制，设置页「导出模板」原样写盘。源文件在 `docs/theme-template*/`，
  **改完模板目录要重新打这两个 zip**，否则导出的还是旧内容。
- **主题预览隔离（2026-10-28 已完成）**：设置页放大预览已改为独立 `WebviewWindow`（`label: theme-preview`，
  `capabilities/preview.json` 只列 `core:event:allow-listen`/`unlisten`，**无任何 app 命令**）。
  第三方主题在此加载也调不到 `stop_sidecar`/`delete_user_theme`。由 `open_theme_preview` 命令（只授 main）打开；
  卡片缩略图改静态占位，不再在主窗口内执行主题 JS。做「主题市场」的前置条件已具备。
- **主题地址**：内置主题编译进二进制 → `/themes/<id>/`；用户主题走自定义协议 `theme`，Windows 实际是
  `http://theme.localhost/<id>/`（**手拼 `theme://` 必失败**），统一走 `theme-url.js::themeBaseUrl()`。
- `serve_user_theme()`：id 只允许 `[A-Za-z0-9_-]`；拒绝 `..` / `.` / `\` / NUL；`canonicalize()` 后比前缀；
  14 种扩展名白名单；统一 404（不泄漏存在性）；`no-store` + `ACAO:*`。
- 清单校验只在 `themes.js::checkMeta()` 一份；内置主题靠 `BUILTIN_THEME_IDS` 显式登记（`public/` 不能枚举）。
- 热重载：Rust `notify` 递归监听「主题目录」（默认 `<安装目录>\data\themes`，见第 12 节），debounce = **攒到静默 500ms**（`recv` + `recv_timeout(500)` drain），
  emit `themes-changed`（受影响 id 数组）→ `theme-watch.js::onThemesChanged(fn) → 退订` 单例。
- 握手三个坑：①主题脚本常比宿主 `message` 监听先跑完 → 宿主必须在 **setup 阶段**（不是 `onMounted`）挂监听，
  并在 iframe `load` 后每 400ms 重发 `init`；②主题对每个 `init` 都要回 `ready`（别用一次性 flag）；
  ③主题 CSS 必须写 `html,body{height:100%}`，否则 `.card{height:100%}` 只拿到内容高度。
- `postMessage` 走结构化克隆 → 出口一律 `toRaw` + `structuredClone`。
- 槽位与选择：`theme-choice.js` 按槽位存 localStorage（`acc.overlayTheme` / `acc.monitorTheme`）并导出响应式
  `themeChoice`；**同窗口靠它同步，跨窗口才用 `overlay-theme-changed` 事件**。
- `ThemeHost.fit`：`contain`（固定 design + transform scale）/ `fluid`（铺满、主题自己响应式；
  `acc:init` 多带 `fit`/`box`/`theme`/`background`，切黑白或切悬浮窗背景补发 `acc:ui`）。主题 4s 没 ready → emit `fail` → 回退内置面板。
- **悬浮窗背景（2026-10-28，2026-10-29 修正色值）**：设置页每个悬浮窗槽位可选「透明 / 不透明」（localStorage `acc.overlayBackground.<slot>`）。
  窗口本身一直是 `transparent(true)`，不透明模式由 `OverlayView` 给根节点（`.ov-root.opaque`）铺 `#0b0f14` 纯黑底板实现。
  主题通过 `acc:init.background` / `acc:ui.background` 得知，需要时用 `html[data-bg="opaque"]` 调自己的配色。

  ⚠️ **主题卡片别拿 72% 近黑当"半透明"**（2026-10-29 踩过）。`rgba(11,14,19,.72)` 压在游戏画面上
  就是个实心黑块，用户选「透明」会以为设置没生效。真要能看见画面，卡片背景就得是 `transparent`
  （`speed-gear` 现在就是零底板，靠字号 + 向下软投影保证可读），别靠压暗背景。
  不透明模式现在统一铺 `#0b0f14` 纯黑；主题自带底板时同步用这个值（模板里已改成同一色），不要叠得比底板更黑。
- 缺口：主题拿不到赛道几何（`getTrackGeometry()` 不在 state 里），带地图的纯主题（如副屏）需单独推 `acc:map`。

## 11. 调试技巧

- **别和 `tauri dev` 抢构建**：并发写 `target/debug` 会写坏 `incremental/`，之后编译在 `rmeta/encoder.rs` panic。
  修法：`Remove-Item -Recurse -Force .\src-tauri\target\debug\incremental`。
- `cargo check` 报 PermissionDenied（`tauri-build` 删 `target/debug/telemetry_core.exe` 失败，os error 5）：
  不要杀用户进程，用 `cargo check --target-dir ../.workbuddy/target-check`（约 2 分钟，1GB+，完事用
  `[System.IO.Directory]::Delete($p,$true)` 删）。
- 滚动容器滚轮缩放：`preventDefault()` 挡不住连发滚轮 → 前后把 `scrollTop` 钉回原值（同步 + 一次 rAF），
  容器加 `overscroll-behavior:contain`；**必须用连发滚轮验证**。
  `innerHTML` 整段替换会把祖先 `scrollTop` 夹成 0 → 套 `keepScroll()`。
- 无游戏造共享内存段：`mmap.mmap(-1, size, "Local\\acpmf_physics")`（graphics/static 同理），`graphics.status=2` 为 LIVE。
  **跑之前必须 `ACC_TELEMETRY_DB=<临时库>`** —— 忘了设就直接把假圈写进用户真实历史
  （2026-10-27 真事故：zolder 多出一条走线在 50m 方块里的假 session，被当成「圈速概要路线错了」）。
- 无头验证：`npx vite preview` + Chrome `--headless=new --remote-debugging-port=922x`，node 自带 WebSocket 连 CDP；
  `Emulation.setDeviceMetricsOverride` 控视口，坐标以 `getBoundingClientRect` 为准，别按截图估。
  sandbox OOPIF 子 frame 不在 `Page.getFrameTree` → 查 `/json/list` 的 `type:"iframe"`。
- **本机 `chrome.exe` 实际是 Edge**（`Browser: Edg/155`），page 列表混 `edge://nurturing/` 等内部页
  → 脚本要按 `url.startsWith(BASE)` 挑 target，否则 `setDeviceMetricsOverride` 报 "Target does not support metrics override"。
- **杀进程只按 `CommandLine -like '*<临时目录名>*'` 筛**；`taskkill /F /IM chrome.exe` 会连用户浏览器一起杀（真事故）。
- 垫片行为：只拦「非沙箱」删除（Bash 报 `Sandbox bypassed` 时 `rm` 全失败）；默认沙箱内 `rm` 是真删除。

## 12. 存储位置（数据 / 配置 / 主题，2026-11-08）

- **单一真源 = `src-tauri/src/main.rs` 的 `PathConfig { configDir, dataDir, themesDir }`**，全局静态
  `PATHS: OnceLock<RwLock<PathsState>>`。用 OnceLock 而不是 `app.manage` 是因为 `theme://` 自定义协议的同步闭包
  拿不到 `AppHandle`。Rust 的窗口几何、主题协议、主题列表 / 导入 / 删除 / 热重载监听、sidecar env、前端设置页
  全部读这一份值，**不要再自己拼 `%LOCALAPPDATA%`**。
- 默认布局（release 首次启动）：`<安装目录>\` 放 `config.json` + `window-state.json`，
  `<安装目录>\data\` 放 `telemetry.db`（含 WAL/SHM），`<安装目录>\data\themes\` 放用户主题。
  安装目录 = `current_exe().parent()`，跟随安装页选的地址。
- 三种回退：①开发模式固定 `%LOCALAPPDATA%\acc-telemetry`（避免写进 `target/debug`，`reset_layout()` 也走同一套）；
  ②`writable_dir()` 真建目录 + 写探针文件，装到 `C:\Program Files` 这类只读位置时回退旧布局并在设置页提示；
  ③老用户旧目录里已有 db / config / themes 时默认沿用，**绝不静默换位置**，想搬要点「恢复默认」。
  上次生效的位置不可写（移动盘拔了）也临时回退，但引导文件里的 effective 不动。
- **引导文件是唯一例外，不能放进可改目录**（改完就找不到自己）：固定
  `%LOCALAPPDATA%\acc-telemetry\paths.json`，dev 用 `paths-dev.json` 互不干扰；测试可用
  `ACC_TELEMETRY_BOOTSTRAP_DIR` 指到临时目录。字段 `effective` / `pending` / `note`
  （旧的 `copyOnApply` 已删除，旧文件里多出来的字段会被 serde 忽略）。
- 改路径只写 `pending`，**重启软件才生效**。原因：sidecar 进程持有 SQLite，运行中切库必炸。
  `init_paths()` 在 `.setup()` 最开头、sidecar spawn 之前执行，此刻数据库没被占用，才安全搬
  `telemetry.db` + `-wal` + `-shm`。
- **迁移语义（不是复制）**：`migrate_paths_data()` 分两阶段 ——
  ①先只复制：`config.json` / `window-state.json` / db 三件 / 主题树（跳过 `.trash`、`.import-*`），
  每个文件复制后校验大小一致；②全部复制成功后才 `remove_file` 删旧文件。任何一步失败走
  `rollback_migration()`：删掉**本次新建**的目标文件与目录（预存在的一律不碰），旧位置一个文件不少。
  所以「迁移失败」= 回到动手前，不会留半截数据库。
- 目标已有同名文件时：不覆盖新位置、**也不删旧文件**（`kept` 计数写进 note）。宁可留两份，不丢数据。
- 迁移后清理旧位置只删**空**目录（`prune_empty_dirs` 递归删空、`remove_dir` 非递归），
  且 `removable_old_dir()` 排除配置目录与安装目录 —— 配置目录可能就是安装目录，递归删等于卸载程序。
- 用户可选不留的中间态没有了：pending 一旦存在就只有「迁移」一条路，不再有「仅切换」。
- `restart_app` 命令用于让 pending 立即生效：**必须先 kill sidecar 再 `app.restart()`**。
  `AppHandle::restart()` 在主线程分支里直接 `cleanup_before_exit()` + `exit(0)`，**不会触发
  `RunEvent::Exit`**，我们那条兜底 kill 轮不到，会留下占着 8008 的孤儿后端。
- **透传给 sidecar 的两个 env**（`tauri_plugin_shell::Command::env`，仅当进程环境里没显式设置时才注入，
  保留造测试数据时 `ACC_TELEMETRY_DB=<临时库>` 的隔离能力）：
  `ACC_TELEMETRY_DB=<dataDir>\telemetry.db`、`ACC_TELEMETRY_CONFIG=<configDir>\config.json`。
  Python 侧 `DB_PATH` / `CONFIG_PATH` 都是**模块导入时求值**的常量，运行中改路径无效，必须重启后端。
- Python 侧 `ConfigRepository(db)` 用 `config.config_path_for(db.path)`，不是全局常量：
  有 `ACC_TELEMETRY_CONFIG` 就用它（桌面壳），否则**跟随传进来的 db 路径**。
  别改回全局常量 —— 测试传临时 db 时配置会写进用户真实 config.json（同 2026-10-27 假圈事故一个性质）。
  `server.py::_crash_log_path()` 也跟着 `ACC_TELEMETRY_DB` 走，Program Files 下不会静默丢日志。
- 新增命令（只给 `main`）：`get_data_paths` / `set_data_paths` / `reset_data_paths` / `open_data_path` /
  `restart_app`，`permissions/app.toml` 五条逐一登记，`capabilities/default.json` 授权；
  `overlay` / `preview` capability 不含这些。
  ⚠️ 老问题仍在：**主窗口里的主题 iframe 也能调 Tauri IPC**（`window.label()=="main"` 挡不住子 frame），
  做主题市场前要把预览挪进独立 `WebviewWindow`（只留 `core:event:allow-listen`）。
- 卸载 / 更新安全：Tauri NSIS 卸载段只删登记文件 + 非递归 `RMDir "$INSTDIR"`，
  自动更新走 `/UPDATE` 不卸载旧版，所以 `<安装目录>\data\` 不会被清掉。但仍建议提示用户卸载前自行备份 data。
- 设置页 `SettingsView.vue` 的存储位置全部收进「高级」折叠区，默认收起：只暴露**一个**「存储位置」入口
  （根目录 + 打开 / 更改），下面只读列出三个派生目录（配置文件 / 数据库 / 主题）—— 内部仍是三个独立
  `PathConfig` 字段，界面上只有一处「更改」，点一次三个一起迁移。改根目录时前端按
  `layoutFromRoot()` 派生 `<根>` / `<根>\data` / `<根>\data\themes`，和 Rust `install_layout()` 同一形状，
  **两边形状要一起改**。弹窗主按钮文案「移动数据并重启」，点了就 `set_data_paths` + 立即 `restart_app`；
  重启失败时 pending 已落盘，界面上留「立即重启」按钮兜底。浏览器预览（无 IPC）退回只读展示。
