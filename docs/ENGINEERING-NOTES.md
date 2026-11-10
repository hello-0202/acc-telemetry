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
  当前 `1.2.0`。改版本后 sidecar 必须重打包（exe 里烤的是 `__version__`）。
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
- **多悬浮窗（2026-10-28，2026-11-10 上限改 5）**：最多 5 个，窗口 label 为 `overlay-1..5`
  （capability 用 glob `overlay-*`，加槽位不用改 capabilities / tauri.conf.json）。
  1 号在 `tauri.conf.json` 里静态创建，2..5 由 `set_overlay_slots` 按需创建、停用时 `destroy()`。
  上限有 **4 处必须同步**：Rust `OVERLAY_SLOTS`、三个定长数组 `OVERLAY_PLACED/PADDED/PAD_OFFSET`
  （`[bool; N]` / `[i32; N]`，忘了改会越界 panic）、前端 `theme-choice.js::OVERLAY_SLOT_MAX`、
  `permissions/app.toml` 描述。`place_overlay()` 每列最多 3 个，4/5 号换到左边一列，避免叠出屏幕。
  **改 Rust 后必须重编译 Tauri**（只 `npm run build` 不会更新 exe）。
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
- **主题预览：2026-11-10 按需求改回内嵌**：两个选择器（overlay / monitor）顶部固定一块实时预览大框，
  卡片去掉 6.2rem 大缩略图、改成「使用 / 预览 / 删除」紧凑按钮；点「预览」就在大框里跑
  `ThemePreview`（演示数据，`mock-state.js`）。⚠️ **这是用户明确选择的取舍**：iframe 重新跑在主窗口里，
  继承主窗口全部 app 命令权限，WebView2 注入的 Tauri IPC 不受 iframe `sandbox` 限制。
  **2026-11-10 第五次微调**：连「独立窗口打开」和「关闭预览」两个按钮也按用户要求去掉了，
  现在预览恒定内嵌、不可手动关闭（`clearPreview()` 只在开关弹窗时重置）。Rust 的
  `open_theme_preview` / `capabilities/preview.json` / `PreviewView.vue` 都还在，只是设置页没有入口；
  也就是说**外部主题现在只能在主窗口 iframe 里预览**，风险比之前更高。
  **做「主题市场」前必须把预览重新挪回隔离窗口**（恢复按钮或直接内嵌隔离窗口），否则不安全。
- **设置项实时进预览（2026-11-10 第六次微调；同日改为滑杆 + 颜色 / 文字色）**：右侧设置面板改
  「背景不透明度 / 背景颜色 / 文字颜色」时，`ThemePicker` 把值透传给 `ThemePreview` 的
  `:background-color` / `:background-opacity` / `:text-color`，预览立刻跟着变
  （`overlayAppearance` 是响应式的），和真机 `OverlayView` 的 rgba 底板同一套
  `overlayColorWithAlpha()`；文字色由主题自己写进 `--text-color`。以后加「影响预览外观」的新设置项，只要从 `activeSettings` 取值并透传进预览，
  设置面板改完预览就跟着变，不用改 UI。同一轮还把预览底板 / 空状态的**斜纹**全部去掉了
  （`ThemePreview .tp-stage` 与 `style.css` 的 `.tpk-stage-empty`），底色改成纯净深灰渐变。
- **主题分类（2026-11-10）**：`src/telemetry/theme-categories.js` 是 UI 侧单一真源（6 类 + 未分类），
  `theme.json` 的 `category` / `tags` 只是可选元数据，Rust `list_user_themes` 不解释它们；
  `themes.js::makeEntry()` 用 `normalizeCategory()` 归一，`checkMeta()` 不校验 —— 老主题不受影响。
  **分组只用于悬浮窗选择器**：`ThemePicker.vue` 里 `mode === 'monitor'` 走 `flat`，不渲染分组标题、
  卡片与预览框也不显示分类，直接一张网格列完（内置面板置顶、坏主题沉底）。
  悬浮窗侧栏第一列的顺序：`THEME_CATEGORIES`（6 类 + 未分类）→「外部主题」→「异常主题」（仅有条目时）。
  **外部主题（`source === 'user'`）统一进「外部主题」，不看它自己的 `category`**；该分类永远可点
  （否则一个外部主题都没有时找不到导入按钮），导入 / 导出 / 打开目录 / 刷新工具也只在它里面出现。
- **多悬浮窗 + 双列二级侧栏（2026-11-10 第三次调整，取代本日早些时候的「单悬浮窗 UI」）**：
  设置页仍只有一行「悬浮窗主题」，但**这一行不显示当前主题名**（多选下没有「当前这一个」）；
  弹窗是「第一列分类 / 第二列该分类主题条目 / 右侧固定大预览框 + 设置面板」，右侧**不再有卡片网格**。
  每个条目有「预览 / 使用·停用 / 删除」：`toggleUse()` 是开关式，启用时找 1~5 里的空闲槽位，
  **先 `saveThemeChoice()` 写该槽位主题 + emit `overlay-theme-changed`，再 `setOverlaySlotEnabled(slot, true)`**
  让 Rust 建窗口（顺序反了新窗口会读到旧主题）；停用把槽位还给池子。上限 = Rust `OVERLAY_SLOTS=5`。
  `overlay-slots.js::read()` 读 localStorage `acc.overlaySlots`（clamp 1~5 / 去重 / 排序，
  无记录默认 `[1]`）；`App.vue` 启动 `pushOverlaySlots()` 把这份列表推给 Rust。
  `themeChoice` 预置 `overlay1..5`，按槽位的主题 / 背景键跟着常量自动生效。
  分类导航 `navItems` + `activeGroupId`：空分类禁用（外部主题除外）、选中分类失效自动回落；
  `selectedKey` 把「选中的主题」「预览框内容」「右侧设置面板的目标」绑成一个，切分类自动选第一个，
  右侧永不莫名空白。
- **悬浮窗设置面板可扩展（2026-11-10；同日改为外观设置）**：`src/telemetry/overlay-settings.js` 的
  `OVERLAY_SETTING_DEFS` 是单一真源，目前有 `backgroundOpacity`（slider 0~100%）、
  `backgroundColor` / `textColor`（color：预设色板 + 系统调色盘），共用按槽位存的
  `acc.overlayAppearance.<slot> = { color, opacity, textColor }` 与 `overlay-appearance-changed`
  事件（旧 `acc.overlayBackground.<slot>` 两档值会迁移）。
  每项声明 `group/id/label/type/min/max/step/unit/presets/hint/appliesTo(theme)/read(slot)/write(slot,value)`，
  `ThemePicker` 按 `OVERLAY_SETTING_GROUPS` 分组渲染（目前「背景」一组，以后加「文字」组），
  再按 `type` 渲染 `segmented` / `slider` / `color` 控件，加设置项**不用改组件**。
  **内置 / 外部界限**：`themeSupportsOverlaySetting(theme, capability)` —— 内置主题默认支持；
  外部主题必须在 `theme.json` 写 `"overlaySettings": ["background", "textColor"]`，否则不出现对应控件、
  `OverlayView` 也不应用该设置（ThemeHost 在 `ready` 事件里带回 theme.json 的声明）。
  面板按 `OVERLAY_SETTING_GROUPS` 分为「背景 / 文字」两组，作用于「选中主题占用的槽位」，
  主题没启用 / 外部主题未声明时只给提示。
  ⚠️ 新设置项若按槽位存，记得同步 `OverlayView.vue` 读值 + 透传给 `ThemeHost` / `acc:ui`。
- **预览框固定尺寸**：`.tpk-stage` 高度写死 `15.3846rem`，删掉 `.has-preview` 的高度切换与
  `transition:height` —— 点不点预览都不跳变（用户明确要求）。打开弹窗会自动选中并预览
  当前分类第一个主题（有已启用的优先已启用）。
- **文案收敛 + 整个弹窗尺寸写死（2026-11-10 第四次微调）**：
  `.dlg.dlg-theme.tpk-split` 固定 `width:min(74rem,100%)` + `height:min(80vh,44rem)`，
  `.tpk-main{overflow:hidden}`、`.tpk-pane-bar` 固定高度、`.tpk-stage-head` 不换行 ——
  选中主题后右侧设置面板冒出内容时，弹窗仍是恒定尺寸（实测各状态都是 1117×642 @1440×900），
  多出来的内容只在 `.dlg-body` / 侧栏内部滚动，底部「完成」永远可见。
  同时按用户要求去掉：`toggleUse()` 的启用 / 停用 notice、预览标题栏与卡片里的 `300×190`
  设计尺寸、卡片里的「· 悬浮窗 N」（恒显示「内置 / 外部」，启用状态看色条 / 「停用」按钮 /
  「已启用」chip）、右侧 chip 和设置面板的槽位编号、`useTitle` tooltip 的「（槽位 N）」、
  设置页两行的当前主题名与主面板弹窗的「当前使用 X」。**只删展示，不动底层槽位逻辑**
  （`slotOfTheme()` / `activeSlot` / 按槽位存的主题与背景键都还在）。
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
  `acc:init` 多带 `fit`/`box`/`theme`/`backgroundColor`/`backgroundOpacity`/`background`，切黑白或改背景补发 `acc:ui`；
  `ready` 事件还会带回 theme.json 的 `overlaySettings` 供 OverlayView 判断外观能力）。主题 4s 没 ready → emit `fail` → 回退内置面板。
- **悬浮窗背景（2026-10-28；2026-10-29 修正色值；2026-11-10 收进右侧设置面板并改为连续可调）**：
  悬浮窗主题弹窗右侧「悬浮窗设置」分「背景 / 文字」两组：背景不透明度滑杆（0~100%）、
  背景颜色（黑 / 白预设 + 调色盘）、背景模糊（关 / 开启）、文字颜色
  （`#7db6c7` / `#f7c964` / `#e53325` / 黑 / 白 + 调色盘）。
  设置页不再单独占一行；多选下作用于选中主题占用的槽位。
  存储改为 localStorage `acc.overlayAppearance.<slot> = { color, opacity, blur, textColor }`（旧
  `acc.overlayBackground.<slot>` 的 transparent/opaque 自动迁移），设置项注册表见 `overlay-settings.js`。
  窗口本身一直是 `transparent(true)`，`OverlayView` 用 `overlayBackdropStyle()` 给根节点铺
  `rgba(color, opacity)` 内联底色（只影响背景，不淡化主题内容），不再有 `.ov-root.opaque` 类。
  主题通过 `acc:init` / `acc:ui` 的 `backgroundColor` / `backgroundOpacity` 得知精确值；
  `background: transparent | opaque` 保留为兼容字段（只有 opacity ≥ 99% 才给 opaque，中间档保持
  transparent，避免老主题的 `html[data-bg='opaque']` 把自带底板画死）。
  「模糊」真机走 **custom SWCA Acrylic**：`OverlayView` 调 Rust 命令
  `set_overlay_backdrop(enabled, color, opacity)`，Rust 直接 `GetProcAddress("SetWindowCompositionAttribute")`
  并设置 `ACCENT_ENABLE_ACRYLICBLURBEHIND`（state 4，`main.rs::window_accent`）。
  整窗效果；系统 Acrylic 的模糊半径固定，只有开 / 关；tint 的 alpha 固定 1（几乎透明），
  背景颜色 / 不透明度仍由前端 `.ov-root` 叠。开启时同时调
  `DwmSetWindowAttribute(DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)` 让窗口圆角，
  试图把矩形 blur 层裁掉直角（`SetWindowRgn` 对 SWCA blur 无效）。
  **踩过的坑：**
  - `apply_acrylic`（window-vibrancy）在 Win11 22621+ 走 DWM `DWMSBT_TRANSIENTWINDOW`，窗口失焦时
    系统会把 Acrylic 退化成实色 —— 悬浮窗平时穿透 / 不聚焦，表现为「点别处就不透明」。
  - `apply_blur`（`ACCENT_ENABLE_BLURBEHIND`，state 3）在 Win11 上会把窗口渲染成纯黑，实测不可用。
  - 纯 CSS `backdrop-filter` 在透明 WebView2 窗口里采样不到窗口外的游戏画面。
  设置页预览没有 OS 窗口，仍用 16px CSS 模糊近似。权限只授给 `overlay-*` 窗口
  （`allow-set-overlay-backdrop`）。
  文字颜色通过 `acc:init` / `acc:ui` 的 `textColor` 传给主题，内置主题写入 `--text-color` 并让
  主数字 / 标签的 `color` 引用它（`color-mix` 处理压暗档）。
  外部主题必须在 `theme.json` 的 `overlaySettings` 声明 `"background"` / `"textColor"` 才可调 / 生效，
  内置主题默认支持。

  ⚠️ **主题卡片别拿 72% 近黑当"半透明"**（2026-10-29 踩过）。`rgba(11,14,19,.72)` 压在游戏画面上
  就是个实心黑块，用户选「透明」会以为设置没生效。真要能看见画面，卡片背景就得是 `transparent`
  （`speed-gear` 现在就是零底板，靠字号 + 字重保证可读，不加文字阴影），别靠压暗背景。
  用户拉高不透明度时宿主会铺所选颜色；主题自带底板时用 `var(--host-bg)` 同步（模板里已改），不要叠得比底板更黑。
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

## 13. 赛道会话边界：暂停 vs 回主菜单（2026-11-09）

**症状**：跑了一会儿退出赛道回主菜单，历史页那条记录仍显示「记录中」，点删除提示无法删除。

**根因**：`sessions` 表没有 live / 结束字段，`live` 是纯内存计算值
（`HistoryService.list_sessions`：`streaming and item.id == Recorder._session_id`）。
让一条记录变非 live 的**唯一途径**是 `Recorder.stop()` 把 `_recording=False`、`_session_id=None`。
而 `collect_loop` 遇到非直播帧只往 `bus.event_queue` 丢 `{"type":"stopped"}` 给前端清实时帧，
**完全不碰 Recorder**；`run_worker` 原有的自动结束条件只有 `not provider.available()`（段消失），
ACC 进程还在主菜单时段仍在，于是 session 永远 live。

**能不能区分暂停和主菜单**：ACC 的 `graphics.status` 官方枚举本身是能区分的
（`pyACCSharedMemory/acc_enum.py::ACCStatus`：0=OFF / 1=REPLAY / 2=LIVE / 3=PAUSE），
暂停报 3、主菜单/无赛事报 0。**但底层 `MMapControl` 是 copy 模式**（`access_mode=0`），
只有 `packetId` 变化才把 mmap 拷进 `self.data`；ACC 离开赛道后若不再刷 graphics 页，
`read()` 里的 status 会冻结在最后一帧（2 或 3），游戏写下的 0 根本读不到。
所以加 `AccSharedMemoryProvider.peek_realtime_status()` 直读 `_realtime`
（`from_buffer(_mmap_buffer)`，不走 copy 门槛），再用 `packetId` 新鲜度兜底。

**判定规则**（`backend/collector/reader.py::TrackPresence`，常量在文件头）：

| 观测 | 处理 |
| --- | --- |
| LIVE(2) 且 packetId 在动 | 在赛道 |
| **PAUSE(3)** | **永不结束**，用户随时会继续，这一场要保留 |
| OFF(0) / REPLAY(1) 持续 ≥ `LEAVE_TRACK_GRACE_S`(2s) | 回主菜单 → 结束 |
| packetId 冻结 ≥ `PACKETS_FROZEN_GRACE_S`(8s) 且非 PAUSE | 数据源已死 → 兜底结束 |
| 共享内存段消失 | 立即结束 |

- **必须边沿触发**：从「在赛道」变「离开」只请求一次，恢复 LIVE 才重新武装。
  否则前端 `tickSupervisor` 每 3s 重发 `start`，会被同一个请求反复 stop。
- 信号经 `bus.runtime["session_end_requested"]` 传给 `Recorder.run_worker` 的
  0.5s timeout 分支，消费后调 `stop()`；请求无论当时是否 recording 都要先清掉。
- `stop()` 除了 `stopped` 还必须补一条 `{"type":"history","reason":"session-end"}`：
  前端 `stopped` 分支只清实时帧、**不动 `historyRevision`**，历史页不重拉列表，
  那一行仍带 `live=true`、删除按钮一直禁用。听 `history` 才会刷新。
- 前端 `ui.liveSessionId` 由 `MonitorView` 的 watch 在 `status !== 'streaming'` 时置 null，
  配合列表刷新，`deleteIsLive` 才会变 false。
- `stop()` 后前端会自动 `start()` 待命（新 session 只在内存，没有 LIVE 帧就不落库），
  旧记录因 id 不匹配而可删；再次进赛道会自动开一条新记录，正好「一次进图一条」。

**真机验证**：后端日志会打 `ACC graphics.status → N` 和 `判定已离开赛道（<reason>）`。
进赛道=2、按 Esc 暂停=3、退回主菜单=0。若某版本 ACC 暂停也报 0，
就无法从数据源区分，只能调大 `LEAVE_TRACK_GRACE_S` 或用「暂停后手动停止监测」兜底。

> 改了 Python 后端 → 按第 2 节重打包 sidecar（三处 exe 同步），否则跑的还是旧 exe。
