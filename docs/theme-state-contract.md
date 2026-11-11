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
- **命名空间**：`session` / `track` / `car` / `lap` / `position` / `wheels[]` / `env` / `flags` / `standings`。
- 未知字段主题必须忽略；后端只增不删。

## 四、完整样例

```json
{
  "v": 1,
  "seq": 84213,
  "t": 1793030400123,
  "status": "streaming",
  "session": { "type": "practice", "timeLeftMs": 0, "lapsTotal": 0, "driversTotal": 20, "index": 0, "raceDurationMs": 1200000 },
  "track": { "name": "monza", "title": "Monza", "configuration": "", "lengthM": 5793.0, "sectorCount": 3 },
  "car": {
    "name": "Ferrari 488 GT3 Evo",
    "maxRpm": 7600, "maxFuel": 120.0, "maxTorqueNm": 700.0, "maxPowerKw": 441.0,
    "speed": 213.4, "rpm": 6820, "maxRpmCurrent": 7600,
    "gear": 4, "gearText": "4",
    "throttle": 0.98, "brake": 0.0, "steer": -0.04, "clutch": 0.0,
    "fuel": 62.5, "fuelPerLap": 2.85, "fuelLapsLeft": 21.9, "fuelUsed": 8.4,
    "brakeBias": 0.56,
    "tcLevel": 3, "tcCutLevel": 2, "tcActive": 0.0, "absLevel": 2, "absActive": 0.0, "engineMap": 1,
    "pitLimiter": false, "drs": false, "autoShifter": false, "aiControlled": false,
    "tyreCompound": "dry_compound", "rainTyres": false,
    "indicatorLeft": false, "indicatorRight": false, "lightsStage": 1, "flashingLights": false, "wiperLevel": 0,
    "gLong": 0.18, "gLat": -1.24
  },
  "lap": {
    "num": 4, "completed": 3,
    "timeMs": 41230, "lastMs": 92310, "bestMs": 91980, "pbMs": 91820,
    "deltaMs": -310, "deltaBestMs": -280, "estimatedMs": 92100,
    "progress": 0.62, "sector": 2, "sectorLastMs": 31450, "splitMs": 31200,
    "valid": true, "inPit": false, "inPitLane": false,
    "position": 7, "penaltyMs": 0
  },
  "position": { "x": 812.4, "z": -1533.2, "heading": 1.5708, "distanceM": 14233.7 },
  "wheels": [
    { "corner": "FL", "pressure": 27.4, "temp": 78.2, "brakeTemp": 412.0, "slip": 0.02, "slipRatio": 0.01, "slipAngle": 0.03, "suspensionTravel": 0.041, "wear": 0.0, "wearEst": 0.12, "wearRatePerLap": 0.021, "wearLapsLeft": 41.9, "wearSource": "estimated" }
  ],
  "env": { "airTemp": 26.0, "roadTemp": 31.0, "airDensity": 1.18, "rainIntensity": 0, "rainIn10min": 0, "rainIn30min": 0, "grip": 2, "surfaceGrip": 1.0, "windSpeed": 1.6, "windDirection": 2.1, "timeOfDay": 52200.0 },
  "flags": { "yellow": 0, "white": 0, "green": 1, "chequered": 0, "red": 0, "trackStatus": "GREEN", "mandatoryPitMissing": 0 },
  "standings": { "available": true, "stale": false, "complete": true, "source": "broadcasting", "reason": null, "trackMeters": 5793.0,
    "self": { "carId": 1016, "raceNumber": 811, "name": "BAOBAO DUDU", "team": null, "position": 7, "cupPosition": 0, "trackPosition": 7, "spline": 0.62, "laps": 3, "lastLapMs": 92310, "bestLapMs": 91980, "currentLapMs": 41230, "deltaBestMs": null, "location": "track", "inPit": false, "x": 812.4, "z": -1533.2, "speedKmh": 213, "gapToSelfM": 0.0, "gapAheadM": 7125.4, "gapBehindM": null, "stale": false },
    "rows": [ /* 与 self 同结构，按官方名次排序；完整两条见 contracts/telemetry-state.json 的 example */ ] }
}
```

`wheels` 实际固定 4 项，顺序 **FL / FR / RL / RR**。`standings.rows[]` 的完整样例（含多车）
以 `contracts/telemetry-state.json` 的 `example.standings` 为准，本文只列字段含义。

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
| `session.driversTotal` | int | `graphics.activeCars` | 参赛车总数（含 AI），和 `lap.position` 配对显示「当前/总数」；0 = 未知 |
| `session.index` | int | `graphics.sessionIndex` | 会话序号 |
| `session.raceDurationMs` | int(ms) | ACC 本地 `menuSettings.json` 的 `CustomRace.raceLength` | 本周末正赛总时长，排位阶段预估正赛油量用；联机/读不到时为 `null` |

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
| `car.tcLevel` / `car.tcCutLevel` / `car.absLevel` | int | | `graphics.tcLevel` / `tcCutLevel` / `absLevel` | 0 = 关；tcCutLevel 是 TC 第二级（TC Cut） |
| `car.tcActive` / `car.absActive` | float | 0..1 | `physics.tcActive` / `absActive` | 是否正在介入 |
| `car.engineMap` | int | | `graphics.engineMap` | |
| `car.pitLimiter` | bool | | `physics.pitLimiterOn` | |
| `car.drs` | bool | | `physics.drsActive` | GT3 恒 false |
| `car.autoShifter` | bool | | `physics.autoShifterOn` | |
| `car.aiControlled` | bool | | `physics.isAIControlled` | 回放/接管时 true |
| `car.tyreCompound` | str | | `graphics.tyreCompound` | 游戏原样字符串 |
| `car.rainTyres` | bool | | `graphics.rainTyres` | |
| `car.indicatorLeft` / `car.indicatorRight` | bool | | `graphics.directionLightsLeft/Right` | 转向灯是否在闪 |
| `car.lightsStage` | int | | `graphics.lightsStage` | 大灯档位，0=关 |
| `car.flashingLights` | bool | | `graphics.flashingLights` | 正在闪远光；瞬时动作，档位不变，灯图标要 `lightsStage > 0 \|\| flashingLights` |
| `car.wiperLevel` | int | | `graphics.wiperLV` | 雨刷档位，0=关 |
| `car.gLong` / `car.gLat` | float | g | 计算 | 车体坐标，已除 9.81 |

### lap

| 字段 | 类型 | 单位 | 来源 | 说明 |
|---|---|---|---|---|
| `lap.num` | int | | 计算 | 本次「开始监测」以来的第几圈（从 1），与主面板一致；比赛自身圈数用 `lap.completed + 1` |
| `lap.completed` | int | | `graphics.completedLaps` | |
| `lap.timeMs` | int | ms | `graphics.iCurrentTime` | 本圈已用 |
| `lap.lastMs` | int | ms | `graphics.iLastTime` | 0 = 暂无 |
| `lap.bestMs` | int | ms | `graphics.iBestTime` | 游戏内本场最佳 |
| `lap.pbMs` | int | ms | SQLite | 同赛道 + 同车历史最快，0/null = 无参考 |
| `lap.deltaMs` | int | ms | 计算 | 对**历史 PB**（同赛道 + 同车 + 同会话类型）实时差，**负 = 更快**，无参考为 null |
| `lap.deltaBestMs` | int | ms | 计算 | 对**游戏内本场最佳**的实时差，与 ACC HUD 的 DELTA 同口径，**负 = 更快**；本场暂无最佳圈为 null |
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
| `wheels[].wear` | float | 0..1 | `physics.tyreWear` | 原始值。ACC 官方文档标红 unused，通常恒为 0，仅调试用 |
| `wheels[].wearEst` | float | 0..1 | 计算（`TyreWearEstimator`） | **主题推荐使用**：磨损估算，0 = 新胎 |
| `wheels[].wearRatePerLap` | float | 0..1/圈 | 计算 | 上一圈磨损增量；还没跑完一圈为 null |
| `wheels[].wearLapsLeft` | float | 圈 | 计算 | 按上一圈速率外推的剩余圈数；无速率为 null |
| `wheels[].wearSource` | enum | | 计算 | `shared` = 实测 / `estimated` = 估算 / `none` = 无数据 |

> **磨损是估算值。** ACC 共享内存的 `tyreWear` 被官方标记为「not used」，比赛里基本恒为 0；
> 后端用 `wheelSlip / slipRatio / slipAngle / 胎温 / 胎压 / 车速` 做滑移耗散能量积分，
> 并在每完成一圈后用该圈实测能量自校准（正常驾驶约 2.5%/圈，≈40 圈寿命），
> 换胎（`currentTyreSet` 变化）、圈数回退、session 切换时清零。
> 相对趋势（前后 / 左右差异、每圈损耗）比绝对百分比可信；绝对寿命只能逼近，
> 主题请按 `wearSource` 标注「实测 / 估算」，不要让用户误以为是与游戏 1:1 的真值。
> 估算从后端开始收到帧时起算，中途启动应用时不会包含之前的磨损。

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

### standings（对手榜单，来自广播）

数据源是 ACC 的 **UDP 广播协议**（后端开一条只读连接，每 250ms 组装一次），
和共享内存那条链路**完全独立**：广播没连上 / 挂了 / 用户根本没开端口，`standings`
只会变成 `available: false`，其它字段一个都不受影响。所以主题必须当作「可能一直不可用」来写。

| 字段 | 类型 | 说明 |
|---|---|---|
| `standings.available` | bool | 是否有数据。`false` 时 `rows` 为空，看 `reason` |
| `standings.stale` | bool | 所有车都超过 5s 没实时更新（或快照本身超过 3s）。`rows` 仍保留（名次/圈速是历史结论），位置类字段可能已是 `null` |
| `standings.complete` | bool | 名册（EntryList）是否收齐。`false` 表示 `rows` 可能缺车（有人迟到加入） |
| `standings.source` | str | 固定 `"broadcasting"`，为将来多数据源预留 |
| `standings.reason` | enum | 不可用原因，见下表。可用时为 `null` |
| `standings.trackMeters` | float | 赛道长度（米）。未知为 `null` —— 此时其它车的 `gapToSelfM` 也是 `null`（自己那行恒 `0.0`） |
| `standings.self` | object\|null | 自己这一行（按 `carId` 匹配）。广播里还没自己时为 `null`，`rows` 照出 |
| `standings.self.gapAheadM` | float\|null | 紧贴前方那辆车的距离（**正数**）。没有前车为 `null` |
| `standings.self.gapBehindM` | float\|null | 紧贴后方那辆车的距离（**负数**）。没有后车为 `null` |
| `standings.rows[]` | array | 按官方名次排序的行，字段见下 |

`standings.reason` 取值：

| 值 | 含义 | 用户能做什么 |
|---|---|---|
| `disabled` | 配置里关掉了广播 | 打开 `broadcastEnabled` |
| `port-not-configured` | `broadcasting.json` 的 `updListenerPort` 为 0（**默认就是 0**） | 改成 9000 并重启 ACC |
| `connecting` | 正在注册 / 等第一个报文 | 等 |
| `timeout` | 注册超时，或 3s 没新报文（正在退避重连） | 检查游戏是否在赛道上 |
| `no-session` | 游戏没运行 / 不在赛道上（菜单、回放） | 进场 |
| `no-data` | 还没收到过任何广播数据 | 等 |
| `error` | 注册被拒 / 协议异常 | 看后端日志 |

`rows[]` 每行：

| 字段 | 类型 | 说明 |
|---|---|---|
| `carId` | int | 车辆唯一 ID。**跨源 join 只用它**（姓名/车号/名次都可能重名或被改成 0） |
| `raceNumber` | int | 车号。未知为 0 |
| `name` | str | 车手显示名：`FirstName LastName`；缺失时退 `shortName`，最后空串 |
| `team` | str\|null | 车队名 |
| `position` | int | 官方名次（P/Q/R）。**0 = 未知**，不要当成最后一名 |
| `cupPosition` | int | 组别名次。0 = 未知 / 无分组 |
| `trackPosition` | int | 赛道实际位置，`position` 未知时后端用它排序 |
| `spline` | float\|null | 赛道归一化进度 `0..1`。超时后为 `null`（`0` 是起跑线，不能拿来当未知） |
| `laps` | int | 已完成圈数 |
| `lastLapMs` / `bestLapMs` / `currentLapMs` | int | 毫秒。0 = 还没有 |
| `deltaBestMs` | int\|null | 与最佳圈的差值，未知为 `null` |
| `location` | str | `track` / `pitlane` / `pit_entry` / `pit_exit` / `garage` / `unknown` |
| `inPit` | bool | 是否在维修区（`carLocation == pitlane`；pit_entry / pit_exit / garage 不算） |
| `x` / `z` | float\|null | 世界坐标，与 `position.x/z` **同一坐标系**，可直接和 `position` 一起画在同一张地图上；未知为 `null` |
| `speedKmh` | int\|null | 车速 |
| `gapToSelfM` | float\|null | **正 = 在自车前方，负 = 在自车后方**，单位米。自己那行恒为 `0.0`；算不出为 `null` |
| `stale` | bool | 这一辆车超过 5s 没实时更新：位置类字段已置空，名次/圈速仍保留 |

三个容易写错的地方：

1. **`gapToSelfM` 是距离不是时间差**。v1 不提供「+1.2s」这种秒差，别自己除以速度估算。
2. **`position: 0` 不是第一名也不是最后一名**，是「还不知道」。排序时才用 `trackPosition` 兜底。
3. **`self` 可能为 `null`**（刚进赛道、名册还没回来），此时 `rows` 里的 `gapToSelfM` 仍会算出来；
   需要「我」的信息时优先用 `state.car` / `state.position`，它们来自共享内存，始终有。

## 六、需要后端计算的字段

这些共享内存里没有，是契约的重点：

| 字段 | 算法 |
|---|---|
| `car.gear` / `car.gearText` | ACC 原始 `0=R,1=N,2+=1挡` → 减 1；文本 `R`/`N`/数字 |
| `car.gLong` / `car.gLat` | `acceleration`（世界坐标）按 `heading` 旋转到车体坐标，除 9.81 |
| `lap.num` | 取自 `bus.runtime["lap"]`（记录层圈序号），与主面板显示的圈号一致。**过线瞬间 ACC 先加 `completedLaps`、下一帧才把 `progress` 绕回 0**，短暂错位属正常 |
| `lap.pbMs` | 查 SQLite：同赛道 + 同车的最快有效圈 |
| `lap.deltaMs` | 用当前 `progress` 在 PB 参考圈上插值取时间，减去当前 `lap.timeMs`；无参考返回 `null` |
| `lap.deltaBestMs` | 游戏内 DELTA 的比较对象是**本场最佳圈**。优先用本场已记录、且圈速等于 `graphics.iBestTime` 的曲线（先找最快有效圈，再找最快任意圈——ACC 可能把无效圈算进最佳）；没有时退回 PB 曲线并按圈速比例缩放（起点 0、终点对齐 `iBestTime`），再退回 `pbMs × progress` 线性估算；本场无最佳圈返回 `null` |
| `session.raceDurationMs` | 共享内存不提供正赛长度；读 ACC 本地 `menuSettings.json` 里当前赛季 `CustomRace.raceLength`（秒）×1000。读不到 / 联机 → `null` |
| `status` | `recording=false` → `idle`（有未清除的错误则 `error`）；recording 中但超过 1.5s 没有新帧 → `waiting`；否则 `streaming`。`GAME_NOT_FOUND`（还没进赛道）不算错误 |
| `standings.rows[].gapToSelfM` | `(Δ圈数 + Δ进度) × 赛道长度`，Δ进度按 ±0.5 跨起跑线修正、Δ圈数单独计入（套圈的车约等于一整圈，不是几十米）。自己那行固定 `0.0` |
| `standings.self.gapAheadM` / `gapBehindM` | 所有正 gap 的最小值 / 所有负 gap 的最大值（最接近 0）。没有有效值为 `null` |

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
| 对手的逐圈历史 / 分段成绩 | 需要广播 + 本地库联合；v1 只给当前圈的 `lastLapMs` / `bestLapMs` |
| 分区黄旗 `globalYellow1/2/3` | v1 只给全场黄旗 |
| `carDamage`（5 个部位） | 当前 UI 用不到 |
| 每段独立成绩 `sectorTimes[]` | 等历史库结构定型 |
| FFB 振动信号 | 只在动感平台场景有用 |

## 九、实现状态

- ✅ **P0（本文 + JSON）**：字段冻结。
- ✅ **P1**：`backend/api/state.py` 组装 + `{"type":"state"}`（录制 30Hz / 待机 2Hz）+ `GET /api/contract` + `GET /api/state`。
  `contracts/telemetry-state.json` 已随 PyInstaller 打进包里（`_MEIPASS/contracts/`）。
- ✅ **P2**：`ThemeHost`（iframe 沙箱 + contain 缩放 + postMessage）+ 第一个预设主题 `speed-gear`（悬浮窗）。
- ✅ **广播榜单（`standings`）**：`backend/collector/broadcast_reader.py` 开一条**只读** UDP 连接
  （`backend/collector/broadcast_reader.py` 注册时故意给错的 `commandPassword`），≤4Hz 把
  `StandingsSnapshot` 写入 `bus.latest_standings`，`state.py` 用
  `acc_telemetry/services/standings_model.py` 合成 `standings`。字段口径见本文 §五。
  广播是**增强不是依赖**：任何异常都吞在广播 Task 里，不影响录制 / 圈次 / HUD。
### 实现细节（给后续接手的人）

| 位置 | 职责 |
|---|---|
| `acc_telemetry/providers/acc_provider.py::read()` | 把契约要的共享内存字段全部搬进 `Snapshot`（只搬不换算） |
| `backend/collector/reader.py` | 每帧额外挂一份 `bus.latest_snapshot`（`frame` 只是子集，不够组装 state） |
| `backend/storage/recorder.py::_publish_meta()` | 把赛道/车辆/PB 挂到 `bus.runtime["state_meta"]`，state 组装不去查库 |
| `backend/api/state.py::build_state()` | 组装 + delta 插值 + status 判定，纯函数式读取总线 |
| `backend/api/broadcaster.py` | 与旧帧并行推送，`state_hz` / `state_idle_hz` 可在 `bus.runtime` 调 |
| `backend/collector/broadcast_reader.py` | 广播 UDP 只读连接 + ≤4Hz 组装，写 `bus.latest_standings` / `bus.standings_status`；断线退避 1/2/5/10s，换会话/赛道 `adapter.reset()` |
| `acc_telemetry/services/standings_model.py` | 广播快照 + 共享内存里的自己 → `standings`（纯函数，可离线测） |
| `acc_telemetry/sources/broadcast.py` | 唯一接触 `UDPCarInfo` / `UDPLapInfo` 的地方；每车独立新鲜度（5s） |

**参考圈曲线不进 state**：`pbRef`（历史 PB）和 `sessionRef`（本场最佳）都有上千点，只留在内存算
`lap.deltaMs` / `lap.deltaBestMs`；逐点曲线仍走原来的 `pbref` 事件（那一路是历史 PB 口径）。

## 十、主题包格式与宿主协议（P2）

### 目录结构

```
public/themes/speed-gear/
  theme.json     # 元信息 + 设计基准，宿主据此决定窗口尺寸与缩放
  index.html     # 结构
  theme.css      # 样式
  theme.js       # 收数据并渲染（可以没有）
```

`theme.json`：

```json
{
  "id": "speed-gear",
  "name": "speed-gear",
  "version": "1.0.0",
  "author": "acc-telemetry",
  "desc": "速度 / 档位 / 本圈 / DELTA，适合小尺寸悬浮窗",
  "design": { "w": 380, "h": 150 },
  "fit": "contain",
  "modes": ["overlay"],
  "overlaySettings": ["background", "textColor"],
  "settings": [
    {
      "id": "traceLayout",
      "group": "曲线",
      "label": "布局",
      "type": "segmented",
      "default": "split",
      "options": [
        { "value": "split", "label": "分开" },
        { "value": "merged", "label": "合并" }
      ],
      "hint": "分开：刹车 / 油门各一条；合并：叠在同一张图里"
    }
  ]
}
```

- `design`：设计基准。`contain` 模式下主题内所有尺寸按这个像素写死，不要用 `vw/vh`，也不要做响应式。
- `fit`：`contain`（默认）或 `fluid`。见下面「缩放规则」。
- `modes`：`overlay` / `monitor`，宿主按用途筛主题。一个主题可以同时声明多个。
- `overlaySettings`：可选能力声明，如 `["background", "textColor"]`。外部主题只有声明后，
  设置页才会出现对应的「背景 / 文字」控件、宿主才会应用对应外观；内置主题默认支持。
  `background` 同时覆盖宿主级的「模糊」（Acrylic 整窗效果），主题无需处理。
  不参与 `checkMeta()` 校验，老主题不带此字段不受影响。
- `settings`：**仅内置主题**可用的业务配置项（如曲线布局、显示哪些模块）。
  宿主在「背景 / 文字」之后渲染这些控件，值按「悬浮窗槽位 + 主题」存 localStorage，
  并通过 `acc:init` / `acc:ui` 的 `settings` 对象发给主题；主题自己解释。
  外部主题即使写了也会在 `themes.js` 里被清空 —— 外部的边界始终只有背景和文字色。

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

内置示例：`public/themes/gt-cockpit/`（1280×640 的仪表盘，`modes:["monitor"]`、`fit:"fluid"`）。

### postMessage 协议

| 方向 | 消息 | 说明 |
|---|---|---|
| 主题 → 宿主 | `{ type: 'acc:ready' }` | 主题脚本就绪，可以喂数据。**每次收到 `acc:init` 都要回一次**（幂等，宿主会忽略重复） |
| 宿主 → 主题 | `{ type: 'acc:init', v, mode, design, fit, scale, box, theme, background, backgroundColor, backgroundOpacity, textColor, settings }` | 握手 + 告知当前 mode / 设计尺寸 / 缩放方式 / 容器初始尺寸（`box`）/ 宿主黑白色（`theme`）/ 悬浮窗外观（`backgroundColor`：`#rrggbb`，`backgroundOpacity`：0..1，`textColor`：`#rrggbb`；`background` 是兼容字段：`transparent` \| `opaque`）/ 内置主题配置（`settings`：已按 theme.json schema 补好默认值的对象，外部主题为空对象） |
| 宿主 → 主题 | `{ type: 'acc:ui', theme?, background?, backgroundColor?, backgroundOpacity?, textColor?, settings? }` | 用户切黑白主题 / 改悬浮窗背景、文字颜色或内置主题配置时补发，不用重新 init（重新 init 会再触发一次 ready） |
| 宿主 → 主题 | `{ type: 'acc:state', payload }` | 就是本文第三节的 state，录制中 30Hz、待机 2Hz |

握手顺序上有两个坑，两边都做了兜底：

1. 主题脚本可能比宿主的 `message` 监听先跑完 → 首条 `acc:ready` 会丢。
   宿主在 `load` 后每 400ms 重发 `acc:init`，直到收到 `ready`；主题对每个 `init` 都回 `ready`。
2. 主题没响应（脚本报错 / 路径写错）→ 4 秒后显示「主题加载失败 + 重试」，不留白屏。
   主面板还会**自动回退到内置面板**并提示（主面板不能空着）。

### 独立预览

双击 `index.html`（`file://`）或直接开 `http://localhost:4176/themes/speed-gear/index.html`：
没有宿主时 2 秒后自动跑演示数据，改 CSS 不用开游戏、不用起后端。

### 主题放在哪 / 怎么被加载

| 来源 | 磁盘位置 | 加载地址 | 说明 |
|---|---|---|---|
| 内置 `builtin` | `public/themes/<id>/`（打包后编译进二进制） | `/themes/<id>/index.html` | 走 WebView 自己的资源协议，dev 与打包后同一路径 |
| 用户 `user` | 默认 `<安装目录>\data\themes\<id>\`（可在设置里改主题目录） | `http://theme.localhost/<id>/index.html`（Windows）<br>`theme://localhost/<id>/index.html`（macOS/Linux） | Rust 注册的 `theme` 自定义协议，启动时自动创建该目录 |

> 主题根目录不再是固定的 `%LOCALAPPDATA%\acc-telemetry\themes`：三类存储位置都由桌面壳的
> `PathConfig` 统一决定，设置页「数据与存储」可分别修改，改完重启生效。
> 详见 `docs/ENGINEERING-NOTES.md` 第 12 节。

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
- 回归信号：`public/themes/speed-gear/theme.js` 里 `console.log('[theme] __TAURI_INTERNALS__ =', typeof ...)`，
  值为 `object` 说明注入确实进了子 frame（ACL 是唯一防线），改成 `undefined` 也不能撤掉 ACL。

### 主题清单与选择（设置页）

- 清单来源：内置主题在 `src/telemetry/themes.js::BUILTIN_THEME_IDS` 里显式登记（`public/` 下的文件没法自动枚举）；
  用户主题由 Rust 命令 `list_user_themes` 扫当前「主题目录」得到（默认 `<安装目录>\data\themes\`，见第 12 节说明）。
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
- 弹窗的 `keydown` 要 `stopPropagation`，否则 `1/2/3` 会切页；`Esc` 关闭。

### 热重载

改用户主题目录里的任何文件（`theme.css` / `theme.json` / `html` / `js`），悬浮窗与设置页预览会在一秒内重建 iframe。

- Rust 侧 `notify`（`recommended_watcher`，Windows 上是 `ReadDirectoryChangesW`）递归监听
  当前生效的用户主题目录（默认 `<安装目录>\data\themes\`，可在设置里改，老版为 `%LOCALAPPDATA%\acc-telemetry\themes\`），
  **攒到静默 500ms 再抛一次** —— 编辑器保存常连发
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
- 两个槽位共用 `ThemePicker.vue`（实时预览大框 + 启用/预览/删除 + 导入导出工具），只是 `mode` 不同：
  悬浮窗弹窗是「双列二级侧栏（分类 / 主题条目）+ 右侧固定大预览框 + 设置面板」，可同时启用最多 5 个
  悬浮窗（按槽位），右侧不列卡片网格；主面板弹窗是「预览大框 + 不分组平铺网格」，仍是单选。
  列表按 `theme.json` 的 `modes` 过滤，用户不会看到「只能当悬浮窗的主题」出现在主面板里。
  设置页两行入口都不显示当前主题名；悬浮窗 UI 不暴露槽位编号与设计尺寸，卡片恒显示来源
  （内置 / 外部），启用 / 停用不弹提示；弹窗固定宽高、内容在内部滚动（详见 `ENGINEERING-NOTES.md` 第 10 节）。

### 当前限制（后续做）

- 主题拿不到赛道几何，所以主题化的主面板没有实时赛道图（内置面板仍有）。
- 主题选择存在 localStorage，没有进 `config.json`（跨设备/手改配置暂时不生效）。
- 热重载只看文件改动，**不改主题的 capability**：主题里想用新的宿主命令，仍需改 Rust 并重启。
- `fit: "fluid"` 已在主面板用起来。
