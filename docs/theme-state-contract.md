# 主题数据契约（Theme State Contract v1）

> 单一真源：`python-server/contracts/telemetry-state.json`（机器可读，含每条字段的来源与换算）
> 本文是它的可读版本，给主题作者和后续实现用。改字段先改 JSON，再同步这里。

## 一、这份契约是干什么的

主题（悬浮窗 / 监测面板）不直接读共享内存，也不自己连 WebSocket 处理事件。宿主（`ThemeHost`）把数据合成一份**扁平状态**，按 **30Hz** 注入主题 iframe：

```
ACC 共享内存 ──► collector ──► StateAssembler ──► {"type":"state","data":{...}} ──► ThemeHost ──► iframe
                      ▲
                 SQLite（PB 参考）
```

主题只关心两件事：**收到一个对象**、**把它画出来**。

## 二、跟现有 WS 消息的关系

| 消息 | 状态 | 用途 |
|---|---|---|
| `frame` / `session` / `lap` / `pbref` / `trackmap` / `stopped` | **保持不变** | 现有主窗口 `useTelemetry` + `MonitorView` 继续用 |
| `{"type":"state"}` | **已实现（P1）** | 只给主题宿主：录制中 30Hz，待机 2Hz 心跳 |

两者并行推送，互不影响。等主面板完全主题化（P4）之后，旧消息才会退出历史舞台。

调试入口（不用连 WS 就能看到后端此刻的数据）：

- `GET /api/state` —— 与 WS 里 `{"type":"state"}` **完全同一份**对象
- `GET /api/contract` —— 直接返回 `contracts/telemetry-state.json`（前端不必再打包副本）

## 三、字段命名约定

- **时间**：一律毫秒（`ms`），整数。没有值用 `0`（圈速类）或 `null`（增量类）。
- **比例**：一律 `0..1`，不给百分比，取整由主题决定。
- **速度** `km/h`、**温度** `°C`、**胎压** `psi`、**油量** `L`、**距离/坐标** `m`、**角度** `rad`（显示成度由主题换算）。
- **命名空间**：`session` / `track` / `car` / `lap` / `position` / `wheels[]` / `env` / `flags`。
- 未知字段主题必须忽略；后端只增不删。

## 四、完整样例

```json
{
  "v": 1,
  "seq": 84213,
  "t": 1793030400123,
  "status": "streaming",
  "session": { "type": "practice", "timeLeftMs": 0, "lapsTotal": 0, "index": 0 },
  "track": { "name": "monza", "title": "Monza", "configuration": "", "lengthM": 5793.0, "sectorCount": 3 },
  "car": {
    "name": "Ferrari 488 GT3 Evo",
    "maxRpm": 7600, "maxFuel": 120.0, "maxTorqueNm": 700.0, "maxPowerKw": 441.0,
    "speed": 213.4, "rpm": 6820, "maxRpmCurrent": 7600,
    "gear": 4, "gearText": "4",
    "throttle": 0.98, "brake": 0.0, "steer": -0.04, "clutch": 0.0,
    "fuel": 62.5, "fuelPerLap": 2.85, "fuelLapsLeft": 21.9, "fuelUsed": 8.4,
    "brakeBias": 0.56,
    "tcLevel": 3, "tcActive": 0.0, "absLevel": 2, "absActive": 0.0, "engineMap": 1,
    "pitLimiter": false, "drs": false, "autoShifter": false, "aiControlled": false,
    "tyreCompound": "dry_compound", "rainTyres": false,
    "gLong": 0.18, "gLat": -1.24
  },
  "lap": {
    "num": 4, "completed": 3,
    "timeMs": 41230, "lastMs": 92310, "bestMs": 91980, "pbMs": 91820,
    "deltaMs": -310, "estimatedMs": 92100,
    "progress": 0.62, "sector": 2, "sectorLastMs": 31450, "splitMs": 31200,
    "valid": true, "inPit": false, "inPitLane": false,
    "position": 7, "penaltyMs": 0
  },
  "position": { "x": 812.4, "z": -1533.2, "heading": 1.5708, "distanceM": 14233.7 },
  "wheels": [
    { "corner": "FL", "pressure": 27.4, "temp": 78.2, "brakeTemp": 412.0, "slip": 0.02, "slipRatio": 0.01, "slipAngle": 0.03, "suspensionTravel": 0.041, "wear": 0.12 }
  ],
  "env": { "airTemp": 26.0, "roadTemp": 31.0, "airDensity": 1.18, "rainIntensity": 0, "rainIn10min": 0, "rainIn30min": 0, "grip": 2, "surfaceGrip": 1.0, "windSpeed": 1.6, "windDirection": 2.1, "timeOfDay": 52200.0 },
  "flags": { "yellow": 0, "white": 0, "green": 1, "chequered": 0, "red": 0, "trackStatus": "GREEN", "mandatoryPitMissing": 0 }
}
```

`wheels` 实际固定 4 项，顺序 **FL / FR / RL / RR**。

## 五、字段表

### 顶层

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `v` | int | meta | 契约版本，破坏性变更才 +1 |
| `seq` | int | meta | 递增序号，可用于丢帧判断 |
| `t` | int(ms) | meta | 采样时刻（Unix ms） |
| `status` | enum | meta | `idle` / `waiting` / `streaming` / `error` |

### session

| 字段 | 类型 | 共享内存来源 | 说明 |
|---|---|---|---|
| `session.type` | enum | `graphics.session` | 练习/排位/正赛…，菜单里可能 `unknown` |
| `session.timeLeftMs` | int(ms) | `graphics.sessionTimeLeft` | 计时赛剩余，圈数赛为 0 |
| `session.lapsTotal` | int | `graphics.numberOfLaps` | 0 = 不限 |
| `session.index` | int | `graphics.sessionIndex` | 会话序号 |

### track

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `track.name` | str | `static.trackName` | ACC 内部名，如 `monza`；适合做键，不适合直接显示 |
| `track.title` | str | 本地赛库（计算） | 显示名，如 `Monza` |
| `track.configuration` | str | `static.trackConfiguration` | 布局，多数为空 |
| `track.lengthM` | float(m) | 本地赛库（计算） | 赛道长度；ACC 的 `trackSplineLength` 官方标注未使用，恒 0 |
| `track.sectorCount` | int | `static.sectorCount` | 通常 3 |

### car

| 字段 | 类型 | 单位 | 来源 | 说明 |
|---|---|---|---|---|
| `car.name` | str | | `static.carModel` | 车款名 |
| `car.maxRpm` | int | rpm | `static.maxRpm` | 引擎最大转速 |
| `car.maxFuel` | float | L | `static.maxFuel` | 油箱容量 |
| `car.maxTorqueNm` / `car.maxPowerKw` | float | Nm / kW | `static.maxTorque` / `maxPower` | 引擎参数 |
| `car.speed` | float | km/h | `physics.speedKmh` | 保留 1 位小数 |
| `car.rpm` | int | rpm | `physics.rpm` | |
| `car.maxRpmCurrent` | int | rpm | `physics.currentMaxRPM` | 画红线用，0 时退回 `car.maxRpm` |
| `car.gear` | int | | `physics.gear` | **已语义化**：-1=R、0=N、1..n |
| `car.gearText` | str | | 计算 | `R` / `N` / `1..n` |
| `car.throttle` / `car.brake` | float | 0..1 | `physics.throttle` / `brake` | 不给百分比 |
| `car.steer` | float | -1..1 | `physics.steerAngle` | 显示时 ×450 得度数 |
| `car.clutch` | float | 0..1 | `physics.clutch` | 0 = 踩到底 |
| `car.fuel` | float | L | `physics.fuel` | 剩余油量 |
| `car.fuelPerLap` | float | L/圈 | `graphics.fuelXLap` | 游戏估算 |
| `car.fuelLapsLeft` | float | 圈 | `graphics.fuelEstimatedLaps` | 游戏估算 |
| `car.fuelUsed` | float | L | `graphics.usedFuel` | 本场累计 |
| `car.brakeBias` | float | 0..1 | `physics.brakeBias` | 前轴占比 |
| `car.tcLevel` / `car.absLevel` | int | | `graphics.tcLevel` / `absLevel` | 0 = 关 |
| `car.tcActive` / `car.absActive` | float | 0..1 | `physics.tcActive` / `absActive` | 是否正在介入 |
| `car.engineMap` | int | | `graphics.engineMap` | |
| `car.pitLimiter` | bool | | `physics.pitLimiterOn` | |
| `car.drs` | bool | | `physics.drsActive` | GT3 恒 false |
| `car.autoShifter` | bool | | `physics.autoShifterOn` | |
| `car.aiControlled` | bool | | `physics.isAIControlled` | 回放/接管时 true |
| `car.tyreCompound` | str | | `graphics.tyreCompound` | 游戏原样字符串 |
| `car.rainTyres` | bool | | `graphics.rainTyres` | |
| `car.gLong` / `car.gLat` | float | g | 计算 | 车体坐标，已除 9.81 |

### lap

| 字段 | 类型 | 单位 | 来源 | 说明 |
|---|---|---|---|---|
| `lap.num` | int | | 计算 | 本次「开始监测」以来的第几圈（从 1），与主面板一致；比赛自身圈数用 `lap.completed + 1` |
| `lap.completed` | int | | `graphics.completedLaps` | |
| `lap.timeMs` | int | ms | `graphics.iCurrentTime` | 本圈已用 |
| `lap.lastMs` | int | ms | `graphics.iLastTime` | 0 = 暂无 |
| `lap.bestMs` | int | ms | `graphics.iBestTime` | 游戏内本场最佳 |
| `lap.pbMs` | int | ms | SQLite | 本工具历史最快，0/null = 无参考 |
| `lap.deltaMs` | int | ms | 计算 | 对 PB 实时差，**负 = 更快**，无参考为 null |
| `lap.estimatedMs` | int | ms | `graphics.iEstimatedLapTime` | 游戏估算 |
| `lap.progress` | float | 0..1 | `graphics.normalizedCarPosition` | |
| `lap.sector` | int | | `graphics.currentSectorIndex` | 1 起 |
| `lap.sectorLastMs` | int | ms | `graphics.lastSectorTime` | |
| `lap.splitMs` | int | ms | `graphics.iSplit` | |
| `lap.valid` | bool | | `graphics.isValidLap` | |
| `lap.inPit` / `lap.inPitLane` | bool | | `graphics.isInPit` / `isInPitLane` | |
| `lap.position` | int | 名次 | `graphics.position` | 0 = 无效 |
| `lap.penaltyMs` | int | ms | `graphics.penaltyTime` | 待执行罚时 |

### position

| 字段 | 类型 | 单位 | 来源 | 说明 |
|---|---|---|---|---|
| `position.x` | float | m | `graphics.carCoordinates[playerCarID]` | 无坐标时 null |
| `position.z` | float | m | 同上 | ACC 的 y 是高度，平面用 x-z |
| `position.heading` | float | rad | `physics.heading` | |
| `position.distanceM` | float | m | `graphics.distanceTraveled` | 本场累计里程 |

### wheels[]（固定 4 项，FL/FR/RL/RR）

| 字段 | 类型 | 单位 | 来源 | 说明 |
|---|---|---|---|---|
| `wheels[].corner` | str | | 计算 | `FL` / `FR` / `RL` / `RR` |
| `wheels[].pressure` | float | psi | `physics.wheelPressure` | |
| `wheels[].temp` | float | °C | `physics.tyreCoreTemp` | 胎芯温度 |
| `wheels[].brakeTemp` | float | °C | `physics.brakeTemp` | |
| `wheels[].slip` | float | 0..1 | `physics.wheelSlip` | |
| `wheels[].slipRatio` | float | | `physics.slipRatio` | 纵向滑移率 |
| `wheels[].slipAngle` | float | rad | `physics.slipAngle` | 横向滑移角 |
| `wheels[].suspensionTravel` | float | m | `physics.suspensionTravel` | |
| `wheels[].wear` | float | 0..1 | `physics.tyreWear` | 文档标 unused，实测有值 |

### env

| 字段 | 类型 | 单位 | 来源 | 说明 |
|---|---|---|---|---|
| `env.airTemp` / `env.roadTemp` | float | °C | `physics.airTemp` / `roadTemp` | |
| `env.airDensity` | float | kg/m³ | `physics.airDensity` | |
| `env.rainIntensity` | enum | | `graphics.rainIntensity` | 0 无雨 … 5 暴雨 |
| `env.rainIn10min` / `env.rainIn30min` | int | | `graphics.rainIntensityIn10min` / `30min` | 预报 |
| `env.grip` | enum | | `graphics.trackGripStatus` | 0 green … 5 wet |
| `env.surfaceGrip` | float | 0..1 | `graphics.surfaceGrip` | |
| `env.windSpeed` | float | m/s | `graphics.windSpeed` | |
| `env.windDirection` | float | rad | `graphics.windDirection` | ACC 原值是角度，后端转弧度 |
| `env.timeOfDay` | float | s | `graphics.timeOfDay` | 一天中的秒数，0 = 午夜 |

### flags

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `flags.yellow` / `white` / `green` / `chequered` / `red` | int | `graphics.global*` | 0/1 |
| `flags.trackStatus` | str | `graphics.trackStatus` | 原文，如 `GREEN` |
| `flags.mandatoryPitMissing` | int | `graphics.missingMandatoryPits` | 还差几次强制进站 |

## 六、需要后端计算的字段

这些共享内存里没有，是契约的重点：

| 字段 | 算法 |
|---|---|
| `car.gear` / `car.gearText` | ACC 原始 `0=R,1=N,2+=1挡` → 减 1；文本 `R`/`N`/数字 |
| `car.gLong` / `car.gLat` | `acceleration`（世界坐标）按 `heading` 旋转到车体坐标，除 9.81 |
| `lap.num` | 取自 `bus.runtime["lap"]`（记录层圈序号），与主面板显示的圈号一致。**过线瞬间 ACC 先加 `completedLaps`、下一帧才把 `progress` 绕回 0**，短暂错位属正常 |
| `lap.pbMs` | 查 SQLite：同赛道 + 同车的最快有效圈 |
| `lap.deltaMs` | 用当前 `progress` 在 PB 参考圈上插值取时间，减去当前 `lap.timeMs`；无参考返回 `null` |
| `status` | `recording=false` → `idle`（有未清除的错误则 `error`）；recording 中但超过 1.5s 没有新帧 → `waiting`；否则 `streaming`。`GAME_NOT_FOUND`（还没进赛道）不算错误 |

## 七、稳定性规则

1. **只增不删**。字段废弃时保留键、值置 `null`，并在本文标注 `deprecated`。
2. **只有破坏性变更才升 `v`**（改单位、改语义、改嵌套层级）。加字段不升版。
3. 主题必须**忽略不认识的字段**，不能读不到就崩。
4. **只有 `status == streaming` 时才给出实时值**；`idle`（没在监测 / 游戏没启动 / 已停止）与 `waiting`
   （暂停 / 回放 / 退到菜单 / 游戏进程在但不在赛道）时实时字段一律置空，由主题显示占位或灰掉——
   不能把上一场冻结的速度、圈速、胎温当成当前值继续展示。短暂掉帧（< 1.5s）仍算 `streaming`，不会闪零。
   非实时字段（赛道名、车名、`lap.pbMs` 等会话元信息）在会话有效期内保留。

## 八、暂不暴露（v1.1 候选）

| 项 | 原因 |
|---|---|
| `opponents[]`（对手车坐标/圈速） | `carCoordinates` + `carIDs` 有坐标，但缺「车 ID → 车手名/圈速」完整映射，要解析额外 session 命令 |
| 分区黄旗 `globalYellow1/2/3` | v1 只给全场黄旗 |
| `carDamage`（5 个部位） | 当前 UI 用不到 |
| 每段独立成绩 `sectorTimes[]` | 等历史库结构定型 |
| FFB 振动信号 | 只在动感平台场景有用 |

## 九、实现状态

- ✅ **P0（本文 + JSON）**：字段冻结。
- ✅ **P1**：`backend/api/state.py` 组装 + `{"type":"state"}`（录制 30Hz / 待机 2Hz）+ `GET /api/contract` + `GET /api/state`。
  `contracts/telemetry-state.json` 已随 PyInstaller 打进包里（`_MEIPASS/contracts/`）。
- ✅ **P2**：`ThemeHost`（iframe 沙箱 + contain 缩放 + postMessage）+ 第一个预设主题 `overlay-min`（悬浮窗）。
### 实现细节（给后续接手的人）

| 位置 | 职责 |
|---|---|
| `acc_telemetry/providers/acc_provider.py::read()` | 把契约要的共享内存字段全部搬进 `Snapshot`（只搬不换算） |
| `backend/collector/reader.py` | 每帧额外挂一份 `bus.latest_snapshot`（`frame` 只是子集，不够组装 state） |
| `backend/storage/recorder.py::_publish_meta()` | 把赛道/车辆/PB 挂到 `bus.runtime["state_meta"]`，state 组装不去查库 |
| `backend/api/state.py::build_state()` | 组装 + delta 插值 + status 判定，纯函数式读取总线 |
| `backend/api/broadcaster.py` | 与旧帧并行推送，`state_hz` / `state_idle_hz` 可在 `bus.runtime` 调 |

**PB 参考圈不进 state**：`pbRef` 有上千点，只留在内存算 `lap.deltaMs`；逐点曲线仍走原来的 `pbref` 事件。

## 十、主题包格式与宿主协议（P2）

### 目录结构

```
public/themes/overlay-min/
  theme.json     # 元信息 + 设计基准，宿主据此决定窗口尺寸与缩放
  index.html     # 结构
  theme.css      # 样式
  theme.js       # 收数据并渲染（可以没有）
```

`theme.json`：

```json
{
  "id": "overlay-min",
  "name": "极简 HUD",
  "version": "1.0.0",
  "author": "acc-telemetry",
  "desc": "速度 / 档位 / 本圈 / DELTA，适合小尺寸悬浮窗",
  "design": { "w": 380, "h": 150 },
  "fit": "contain",
  "modes": ["overlay"]
}
```

- `design`：设计基准。`contain` 模式下主题内所有尺寸按这个像素写死，不要用 `vw/vh`，也不要做响应式。
- `fit`：`contain`（默认）或 `fluid`。见下面「缩放规则」。
- `modes`：`overlay` / `monitor`，宿主按用途筛主题。一个主题可以同时声明多个。

### 缩放规则

**`fit: "contain"`（默认，悬浮窗用）**

宿主测出容器尺寸后算 `k = min(容器宽 / design.w, 容器高 / design.h)`，
把 iframe 按 `design` 尺寸渲染，再用 `transform: scale(k)` 等比缩放并居中。
比例不一致时四周留空，**留空是透明的**（悬浮窗没有可见黑边）。

**`fit: "fluid"`（主面板用）**

iframe 直接 `width:100%; height:100%`、不做 transform，**主题的视口就是容器尺寸**。
主题自己用 `%` / `flex` / `clamp()` 排版，像写普通网页一样。约定：

- `design` 这时候只用作**预览时的宽高比参考**（设置页缩略图按它撑开），不参与缩放；
- 根字号是最省事的自适应手法：`html{font-size:clamp(10px, calc(1.5vh + 0.4vw), 20px)}`，其余尺寸全写 `rem`；
- 主题被拉伸到任意比例都要能看：不要假设固定宽度，也不要用 `height:100%` 叠在没定高的父级上。

内置示例：`public/themes/monitor-grid/`（1200×640 的 2 列仪表盘，`modes:["monitor"]`、`fit:"fluid"`）。

### postMessage 协议

| 方向 | 消息 | 说明 |
|---|---|---|
| 主题 → 宿主 | `{ type: 'acc:ready' }` | 主题脚本就绪，可以喂数据。**每次收到 `acc:init` 都要回一次**（幂等，宿主会忽略重复） |
| 宿主 → 主题 | `{ type: 'acc:init', v, mode, design, fit, scale, box, theme, background }` | 握手 + 告知当前 mode / 设计尺寸 / 缩放方式 / 容器初始尺寸（`box`）/ 宿主黑白色（`theme`）/ 悬浮窗背景（`background`：`transparent` \| `opaque`） |
| 宿主 → 主题 | `{ type: 'acc:ui', theme, background }` | 用户切黑白主题 / 悬浮窗透明-不透明时补发，不用重新 init（重新 init 会再触发一次 ready） |
| 宿主 → 主题 | `{ type: 'acc:state', payload }` | 就是本文第三节的 state，录制中 30Hz、待机 2Hz |

握手顺序上有两个坑，两边都做了兜底：

1. 主题脚本可能比宿主的 `message` 监听先跑完 → 首条 `acc:ready` 会丢。
   宿主在 `load` 后每 400ms 重发 `acc:init`，直到收到 `ready`；主题对每个 `init` 都回 `ready`。
2. 主题没响应（脚本报错 / 路径写错）→ 4 秒后显示「主题加载失败 + 重试」，不留白屏。
   主面板还会**自动回退到内置面板**并提示（主面板不能空着）。

### 独立预览

双击 `index.html`（`file://`）或直接开 `http://localhost:4176/themes/overlay-min/index.html`：
没有宿主时 2 秒后自动跑演示数据，改 CSS 不用开游戏、不用起后端。

### 主题放在哪 / 怎么被加载

| 来源 | 磁盘位置 | 加载地址 | 说明 |
|---|---|---|---|
| 内置 `builtin` | `public/themes/<id>/`（打包后编译进二进制） | `/themes/<id>/index.html` | 走 WebView 自己的资源协议，dev 与打包后同一路径 |
| 用户 `user` | `%LOCALAPPDATA%\acc-telemetry\themes\<id>\` | `http://theme.localhost/<id>/index.html`（Windows）<br>`theme://localhost/<id>/index.html`（macOS/Linux） | Rust 注册的 `theme` 自定义协议，启动时自动创建该目录 |

前端用 `src/telemetry/theme-url.js::themeBaseUrl(id, source)` 拼地址，**不要在业务代码里手拼前缀**：
WebView2 只认 http(s)，Windows 上写 `theme://...` 必定加载失败。

`theme` 协议的访问规则（`src-tauri/src/main.rs::serve_user_theme`）：

- 路径第一段是主题 id，只允许 `[A-Za-z0-9_-]`；文件名做 URL 解码后拒绝 `..`、`.`、反斜杠、NUL。
- `canonicalize()` 后再校验仍在 `<用户主题根>/<id>/` 内（防符号链接跳出去）。
- 扩展名白名单：`html css js mjs json svg png jpg jpeg webp gif woff2 woff ttf`。
- 越权与不存在一律返回 `404`，不回显任何绝对路径。
- 响应带 `Cache-Control: no-store`（改完 CSS 刷新生效）与 `Access-Control-Allow-Origin: *`
  （宿主页要 `fetch theme.json` 读 design，跨源必须放行）。

### 安全边界

- iframe 用 `sandbox="allow-scripts"`，**不给 `allow-same-origin`**：主题是 opaque origin，
  拿不到宿主 DOM / localStorage / cookie。
- **但 Tauri 的 IPC 注入挡不住**：`window.__TAURI_INTERNALS__` 由 WebView2 的
  `AddScriptToExecuteOnDocumentCreated` 注入，对所有 frame 生效。所以"把不可信主题放进 sandbox iframe"本身
  并不构成隔离 —— 命令权限必须靠 ACL 按窗口收紧（见下）。
- 应用自定义命令已过 ACL（`src-tauri/permissions/app.toml`），授权按窗口最小化：

  | 命令 | main 窗口 | overlay 窗口 |
  |---|---|---|
  | `stop_sidecar` | ✅ | ❌ |
  | `toggle_overlay` / `show_overlay` / `hide_overlay` | ✅ | ❌ |
  | `set_overlay_locked` | ✅ | ✅ |
  | `set_overlay_size` | ❌ | ✅ |

  也就是说主题若真拿到 IPC，最多只能改悬浮窗自己的尺寸 / 锁定态，**杀不掉 sidecar、也动不了主窗口**。
  `stop_sidecar` 另有一道运行时校验（只接受来自 `main` 窗口的调用）。
- ⚠️ **设置页预览是例外**：预览 iframe 跑在主窗口里（`ThemePreview`），ACL 是按窗口授权的，
  所以主窗口里的主题脚本能拿到 main 窗口的命令（`stop_sidecar` / `delete_user_theme` 等）。
  **只预览你信任的主题**。要做「主题市场 / 从网络下载主题」，必须先改成独立
  `WebviewWindow`（label 前缀 `preview-`，**capability 里只有 `core:event:allow-listen`，
  不给任何 app 命令** —— 不是"权限列表为空"，空了连 state 都收不到；数据靠 `emit` 推过去）——
  这条是 P6 之后的前置条件，别在 iframe 里预览陌生主题。
- 回归信号：`public/themes/overlay-min/theme.js` 里 `console.log('[theme] __TAURI_INTERNALS__ =', typeof ...)`，
  值为 `object` 说明注入确实进了子 frame（ACL 是唯一防线），改成 `undefined` 也不能撤掉 ACL。

### 主题清单与选择（设置页）

- 清单来源：内置主题在 `src/telemetry/themes.js::BUILTIN_THEME_IDS` 里显式登记（`public/` 下的文件没法自动枚举）；
  用户主题由 Rust 命令 `list_user_themes` 扫 `%LOCALAPPDATA%\acc-telemetry\themes\` 得到。
- 校验规则**只有一份**：`themes.js::checkMeta()`（`theme.json` 可解析、`id` 与目录名一致、`design` 为 80~8192 的整数、
  `fit ∈ {contain, fluid}`、`modes ⊆ {overlay, monitor}` 且非空、目录里必须有 `index.html`）。
  坏主题只在列表里标红，不影响应用启动，也不会 500。
- 列表排序：用户主题在前 → 内置 → 坏主题沉底，各自按名称排。
- 选择结果存 **localStorage 的 `acc.overlayTheme`**（`{id, source}`），不走 `config.json`：
  `POST /api/config` 是按白名单收键的，加键要改 Python 并重打包 sidecar，而主题选择只是本机界面偏好。
  主窗口与悬浮窗同源，localStorage 天然共享；切换后主窗口 `emit('overlay-theme-changed')` 通知悬浮窗重建 iframe。
- 添加：设置页「打开主题文件夹」+「刷新」（把主题目录拷进去即可，不需要安装步骤）。
- 删除：只对用户主题开放，走系统回收站（可恢复），确认按钮在卡片内二次确认；删的是当前主题则自动回退到内置默认。

### 设置页预览

选主题前先看效果 —— 卡片顶部是缩略图，点开是 1:1 放大预览，看完可直接启用。

| 组件 | 职责 |
|---|---|
| `LazyMount.vue` | 卡片进入视口（提前 160px）才挂 iframe；设置页切走时销毁（`visible` 为假） |
| `ThemePreview.vue` | 一层「模拟游戏画面」底色 + `ThemeHost`；整块 `pointer-events:none`，点击交给外层按钮 |
| `mock-state.js` | 演示数据，直接取契约 `example`，500ms 一帧让数值会动 |

要点：

- **演示数据来自契约的 `example`**（`import contract from '../../python-server/contracts/telemetry-state.json'`），
  不另写一份 mock。契约改了预览跟着改，不会出现「预览能跑、真机字段名不对」。没有后端也能预览。
- 缩略图用**静态帧**（`live: false`），只有放大预览跑 500ms 定时器；多个缩略图共用同一个 state 对象，
  不给每个主题各起一个定时器。定时器是模块级单例，订阅数归零自动停。
- 缩放不用自己算：`ThemeHost` 已经按 `theme.json` 的 `design` 做 contain，缩略图小、弹出大，只换容器尺寸。
- 弹窗舞台高度按主题的 `design` 比例（`aspect-ratio`）撑开，主题正好满宽；上下限 `6.5rem ~ min(46vh, 26rem)` 防极端比例。
- 坏主题（`error` 非空）不渲染 iframe，显示「无法预览」。
- 弹窗的 `keydown` 要 `stopPropagation`，否则 `1/2/3` 会切页、`T` 会切黑白主题；`Esc` 关闭。

### 热重载

改用户主题目录里的任何文件（`theme.css` / `theme.json` / `html` / `js`），悬浮窗与设置页预览会在一秒内重建 iframe。

- Rust 侧 `notify`（`recommended_watcher`，Windows 上是 `ReadDirectoryChangesW`）递归监听
  `%LOCALAPPDATA%\acc-telemetry\themes\`，**攒到静默 500ms 再抛一次** —— 编辑器保存常连发
  临时文件 / 改名 / 属性变化多条事件，不合并会让主题重建三四轮。
- 事件载荷是受影响的**主题 id 数组**（`emit('themes-changed', ["my-hud"])`），不是原始路径：
  前端不知道也不该知道磁盘布局。根目录里的零散文件（不属于任何主题）不触发。
- 前端 `theme-watch.js` 做模块级单例订阅（多组件共用一个 `listen`），`ThemeHost` 只在
  `source === "user"` 且 id 命中时重建；重建前先重读 `theme.json` —— 改 `design` 会连带把窗口尺寸调过去。
- 设置页收到同一事件只做 `reloadThemes()`（列表自动跟上，拷进目录即出现）。
- **内置主题不监听**：它们编译进二进制，磁盘上没有可读目录，改完要重新 `npm run build`。

### 主题调试页（`dev.html`）

给主题作者用的独立页面（Vite 第三入口），`npm run dev` 后开 `http://localhost:1420/dev.html`。
不开游戏、不开悬浮窗就能把主题放到可控数据下看排版。

| 区块 | 内容 |
|---|---|
| 顶栏 | 主题下拉（内置 + 用户）、自动波形开关、1:1 / 适应、底色（游戏画面 / 纯黑 / 透明格）、设计尺寸与缩放系数 |
| 左栏 | 场景预设（怠速 / 直道 / 重刹 / 弯中 / 进站）+ 滑杆（速度、转速、档位、油门、刹车、方向、delta、本圈、油量、胎温）+ 进站/有效圈开关 |
| 主区 | `ThemeHost` 预览；1:1 模式把画布钉成 `design` 尺寸（超容器可滚动），适应模式铺满 |
| 底部 | 可折叠的 state JSON + 复制按钮 |

数据底座是 `mock-state.js::baseMockState()`（契约 `example` 的深拷贝，不带波形），
所以字段名永远和真机一致；打开「自动波形」时才交给共享定时器驱动。

### 主面板壳化（P4）

主面板的**数据展示区**可以换成主题，外壳（状态条、开始/停止、页签、沉浸态）仍留在 Vue 里。

```
MonitorView.vue          外壳：状态条 + 开始监测 + 加载失败/未监测兜底
  └─ MonitorShell.vue    壳：二选一
       ├─ MonitorDashboard.vue   内置面板（default）—— 含实时赛道图
       └─ ThemeHost mode="monitor"  主题（fit 由 theme.json 决定，内置示例用 fluid）
```

- 选择存在 **localStorage 的 `acc.monitorTheme`**（与悬浮窗的 `acc.overlayTheme` 分开）。
  同窗口内组件直接读 `theme-choice.js` 的响应式 `themeChoice.monitor` → 设置页一改，主面板当场换，不用事件。
- **「内置面板」不是主题**：`BUILTIN_PANEL = { id: '_panel' }` 是占位 id，选中它时 `MonitorShell`
  直接渲染 `MonitorDashboard`，不挂 iframe。它是**功能最全**的那个 —— 实时赛道图/弯心来自
  `getTrackGeometry()` 的几何数据，目前不在 state 里，主题拿不到（P5 再考虑单独推 `acc:map`）。
- 主题加载失败（4 秒没 `acc:ready`）→ 自动回退内置面板 + 顶部提示条，主面板不会留空白。
- 两个槽位共用 `ThemePicker.vue`（卡片网格 + 缩略预览 + 放大预览 + 启用/删除），只是传入的 `mode` 不同；
  列表按 `theme.json` 的 `modes` 过滤，用户不会看到「只能当悬浮窗的主题」出现在主面板里。

### 当前限制（后续做）

- 主题拿不到赛道几何，所以主题化的主面板没有实时赛道图（内置面板仍有）。
- 主题选择存在 localStorage，没有进 `config.json`（跨设备/手改配置暂时不生效）。
- 热重载只看文件改动，**不改主题的 capability**：主题里想用新的宿主命令，仍需改 Rust 并重启。
- `fit: "fluid"` 已在主面板用起来。
