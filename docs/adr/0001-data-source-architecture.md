# ADR-0001：数据源架构 —— 共享内存 / 广播双通道的边界

- 状态：已接受（2026-11-10）
- 相关任务书：[`../HANDOFF-broadcast-foundation.md`](../HANDOFF-broadcast-foundation.md)
- 相关文档：[`../ENGINEERING-NOTES.md`](../ENGINEERING-NOTES.md)、[`../theme-state-contract.md`](../theme-state-contract.md)

## 1. 背景

工具现在只有一条数据源：ACC **共享内存**。它能给玩家自己的几乎一切数据
（速度 / 圈速 / 踏板 / 胎温 / 官方名次 / 参赛车总数），也能给全场车的**世界坐标**
（`graphics.carCoordinates[60]` + `carIDs[60]` + `playerCarID`）。

它给不了的：**其他车的身份与官方名次**（车号 / 车手名 / 组别 / position / 圈速 / 赛道进度）。
所以「完整排行榜」「与对手的赛道差距」这类需求在共享内存上无解。

ACC 还有第二条官方通道：**Broadcasting Network Protocol（UDP）**。它给 EntryList
（车号 / 姓名 / 车队 / 组别）和每辆车的 `position` / `trackPosition` / `splinePosition` /
圈速 / 世界坐标。仓库里已有完整解析实现（vendored 的 `pyACCSharedMemory/acc_udp.py`），
但**从未接入运行中的后端**。

本 ADR 只解决一件事：**这条新数据源应该以什么形状进入系统**，不讨论功能。

## 2. 决策

### 2.1 分层与依赖方向

```
        ┌──────────────────────────────────────────────┐
  契约层 │ contracts/telemetry-state.json · state.py     │  只认领域模型，不认 ACC
        └───────────────▲──────────────────────────────┘
                        │
        ┌───────────────┴──────────────────────────────┐
  读模型 │ 合并 + 新鲜度 + 生命周期（下一轮实现）        │  唯一允许「跨源 join」的层
        │  bus.latest_standings（类型明确的槽位）        │
        └───────────────▲──────────────────────────────┘
                        │
        ┌───────────────┴──────────────────────────────┐
  适配层 │ providers/acc_provider.py  → Snapshot          │  唯一允许接触 ACC 结构体的层
        │ sources/broadcast.py       → StandingsSnapshot │  （ctypes / UDP / 字段名都关在这里）
        └───────────────▲──────────────────────────────┘
                        │
        ┌───────────────┴──────────────────────────────┐
  领域层 │ acc_telemetry/domain/  （纯 dataclass）        │  叶子节点，什么都不依赖
        └──────────────────────────────────────────────┘
```

硬规则：

1. **`domain/` 是叶子**：不 import `ctypes`、`pyACCSharedMemory`、`backend`、`fastapi`，
   也不 import `acc_telemetry` 的任何其他子包。只允许标准库 `dataclasses` / `typing`。
2. **依赖单向**：`domain` ← `sources` / `providers` ← 读模型 ← `state` / 契约。
   反向 import 一律视为设计事故。
3. **ACC 词汇止步于适配器**：`carCoordinates`、`worldPosX`、`carLocation`、`splinePosition`、
   `sessionPhase` 这些名字只允许出现在 `acc_provider.py` / `broadcast.py` 里。
   领域模型用语义名（`world_x`、`car_location`、`session_phase`）。
4. **整数枚举语义化在适配器完成**：领域层只声明「合法字符串词表」
   （`domain/model.py` 的 `SESSION_TYPES` / `SESSION_PHASES` / `CAR_LOCATIONS`），
   ACC 的 `0..8`、`0..4` → 字符串的映射表放在适配器里。
   这样换协议版本只改适配器；领域模型的取值集合不会因为上游加枚举而漂移。

### 2.2 广播是增强，不是依赖

这一条比分层更硬，因为它决定故障半径：

- 广播**任何**异常（连不上、超时、解析失败、会话切换、游戏退出）都不得：
  设置 `state_error`、停止录制、影响圈次识别、拖慢 100Hz 采集、卡住 WebSocket 广播。
- 广播断开时，上层读到的就是「没有 `latest_standings`」，UI 退化成现在的单机视图。
- 反过来不成立：共享内存不需要广播的任何数据。
- 频率天生不同：共享内存 ~100Hz、广播 4Hz（`realtimeUpdateInterval` 可调）、主题 state 30Hz。
  **广播绝不驱动高频 HUD 帧**，它只喂读模型，读模型再按自己的节奏（≤4Hz 变化）更新。

推论（下一轮才实现，但方向现在就定死）：

- 广播客户端是 `server.py` lifespan 里的**独立 asyncio Task**，与 recorder / broadcast_worker / collect_loop 平级；
- 结果进 `bus.latest_standings: StandingsSnapshot | None` 这个**类型明确的槽位**，
  **不许塞进 `bus.runtime`**（那个字典已经是杂项抽屉，再塞就无法推断谁在写）；
- 生命周期跟 `TrackPresence` 的边沿走（进赛道才连、离开赛道就断），断线指数退避，默认开关可控；
- 榜单**不落 SQLite**：模型还在动，落库等于把不确定的东西固化。

### 2.3 跨源 join 的唯一主键是 `carId`

- 共享内存 `graphics.carIDs[i]` 的**值** = 广播的 `carId` = EntryList 的 `entryId`。
  适配器把 Broadcast 的 `carId` 直接写成领域模型的 `car_id`，读模型用它 join。
- **禁止**用 `position` / 车号（`raceNumber`）/ 车手名 / `splinePosition` 做 join：
  它们都会在比赛中变化或重复，且两边语义不完全一致。
- **禁止**用广播的 `focusedCarId` 判定「玩家自己」。玩家身份的唯一真源是共享内存的
  `graphics.playerCarID`；`focusedCarId` 是摄像机的焦点，切镜头就会变。
  （探针会验证两者在默认视角下的关系，但结论只作为参考，不作为判定依据。）

### 2.4 新鲜度是多维的，不是单一时间戳

广播的三个来源刷新率完全不同，读模型必须分开记时间：

| 数据 | 刷新时机 | 过期语义 |
|---|---|---|
| EntryList（身份：姓名/车号/组别） | 进服一次 + 重同步 | 几乎不过期，只在**换会话**时清空 |
| TrackData（赛道长度） | 请求一次 | 换赛道才变 |
| RealtimeCarUpdate（位置/圈速/名次） | 每车 ~4Hz | **每车独立新鲜度**，某车卡住不代表全场卡住 |

所以 `StandingsSnapshot.received_at` 只代表「这份快照被组装出来的时刻」，
不代表每一行都新鲜。每行的时效靠适配器内的 `carId → 最后更新时间` 判定：
过期行仍保留（保留名次和历史），但把「位置类」字段（`spline_position` / `world_x/y`）
置空，宁可缺也不给假值——这与 `spline_position` 未知用 `None`（0 是合法起跑线位置）同一条原则。

### 2.5 领域模型与 ACC 结构体的对应

| 领域模型字段 | 来源 | 注意 |
|---|---|---|
| `car_id` | `UDPCarInfo.carId`（实时）/ `entryId`（名册） | 两者应相等；不等说明名册过期，触发重同步 |
| `driver_name` | `currentDriverInfo.first/lastName` → `shortName` → `""` | 字节串解码见 2.6 |
| `position` / `cup_position` / `track_position` | 同名字段 | `0` = 未知，不做 1 基换算 |
| `spline_position` | `splinePosition` | **未收到实时更新时是 `None`，不是 0** |
| `*_lap_ms` | `bestSessionLap` / `lastLap` / `currentLap.laptimeMS` | 哨兵值过滤，见 2.7 |
| `delta_best_ms` | `deltaBest` | 未收到实时更新 / 哨兵 → `None` |
| `car_location` | `carLocation` + `inGarage` | 见 2.8 |
| `world_x/world_y` | `worldPosX/worldPosY` | `(0,0)` 视为无坐标 → `None` |
| `session_type` / `session_phase` | `sessionInfo.sessionType` / `sessionPhase` | 语义化字符串。**广播的 `RaceSessionType` 与共享内存 `graphics.session` 不是一套枚举**（Qualifying=4、Race=10），适配器单独映射，禁止复用 `session_type_name()` |

### 2.6 字节串解码

EntryList 里的姓名是**裸字节**，编码由游戏客户端决定（多数 UTF-8，混排环境可能是 Latin-1）。
适配器统一：先按 UTF-8 解，失败退 Latin-1（`replace`），再退空串；并且**永不抛异常**——
一个车手名解不出来不能导致整个榜单消失。

### 2.7 圈速哨兵

`99999999` / `2147483647` / `<= 0` 视为「无成绩」→ `0`（沿用
`acc_provider._lap_ms` 的口径）。

`deltaBest`：`>= 2_000_000_000`、`99999999` 和 **`0`** 都视为无效 → `None`。
社区实测里 `0` 出现在出场圈 / 尚无有效圈 / 换会话后，并不代表「正好等于本场最佳」；
真机探针会拿玩家广播 `deltaBest` 与共享内存 `iDeltaLapTime` 对照，若证明 0 合法再放开。

### 2.8 车库判定

vendored 解析器用「进站 + 位移 > 20m 视为瞬移」来推断 `inGarage`。这个启发式在一辆车的
**第一帧**会误判（上一位置默认 `(0,0)`，车在远离原点的维修区就会「瞬移」）。
所以适配器以 `carLocation` 为**唯一主依据**：

- `1→track`、`2→pitlane`、`3→pit_entry`、`4→pit_exit`；
- 只有 `carLocation == 0`（未知）**且** `inGarage` 才判 `garage`；
- 其余 `unknown`。

### 2.9 `trackMeters` 的线格式

**真机报文确认（capture-01，Monza）**：`TrackData.trackMeters` 在 UDP 线上是
**int32**，字节 `a1 16 00 00` = 5793，与仓库 `trackmaps/catalog.py` 的 Monza 长度一致。
Kunos C# SDK 把它暴露成 `float`，但传输层是整数；vendored 解析器按 int32 读是**正确**的。

适配器仍从原始报文独立按 `<i` 解码，并做 `500–50000m` 范围校验：
这样既不依赖 `c_float` 容器，也能在将来协议变化时单独调整。
`raw.trackData.trackMeters` 仅供诊断，业务代码用 `adapter.track_meters`。

## 3. 本轮落地的形状（已实现）

```
python-server/
├─ acc_telemetry/domain/
│  ├─ __init__.py            # 只 re-export
│  └─ model.py               # StandingRow / StandingsSnapshot + 合法词表（纯 stdlib）
├─ acc_telemetry/sources/
│  ├─ __init__.py
│  ├─ broadcast.py           # 唯一接触 UDPCarInfo / UDPLapInfo 的地方
│  ├─ fixture.py             # 夹具格式（写/读），纯 stdlib
│  └─ replay.py              # 夹具 → 适配器 → StandingsSnapshot（离线、确定性）
├─ tools/broadcast_probe.py  # 独立探针：注册 / 请求 / 累积 / 落盘 / 交叉验证 / 报告
└─ tests/
   ├─ broadcast_fixtures.py     # 合成 UDP 报文（离线构造，无真实姓名）
   ├─ test_broadcast_adapter.py
   └─ test_broadcast_replay.py
```

**明确不在本轮**：接 lifespan、写 `bus`、改 `state.py`、改契约、加 UI、落库。

## 4. 后果

正面：

- 广播数据的可信度问题被关在**可离线复现的夹具**里，调参不用开游戏；
- 上层只会看到语义化字符串和 `None`，不会被 `carCoordinates` / `splinePosition=0`
  这类陷阱二次污染；
- 下一轮接 pipeline 是纯增量：新增一个 Task + 一个 `bus` 槽位 + 读模型，不改任何现有链路；
- 广播彻底挂掉，工具行为与本轮之前完全一致（100Hz 主链路零改动）。

代价 / 风险：

- 领域模型是对 ACC 的一层**手写映射**，协议升级（`BROADCASTING_PROTOCOL_VERSION` 变了）要改适配器；
- `UDPEntryList.entryListCars` 是 vendored 代码里的**类级共享 `defaultdict`**，
  同进程内所有 `UDPBroadcastOutput` 共用一个字典。所以：适配器要显式 `reset()`，
  测试之间必须清理，**同进程只允许一个适配器实例**（本轮以文档 + 断言约束，不改 vendored 代码）；
- 广播数据是 4Hz，任何需要「毫秒级」的差距计算都要在 4Hz 与 100Hz 之间插值/对齐，
  这属于读模型的职责，不能交给主题。

## 5. 后续（下一轮 ADR 或本节增补）

1. `StandingsService`：广播 Task 的生命周期 / 退避 / 边沿触发，新鲜度合并写入 `bus.latest_standings`；
2. `state.py` 组装 `standings`（契约只增不删），主题消费；
3. 「相对距离」：`splinePosition × trackMeters` 与共享内存 `distanceTraveled` 的交叉校验
   （探针第 7 问负责先证明 `trackMeters` 可信）；
4. 主题市场之前必须先解决的隔离问题与本 ADR 无关，见 `ENGINEERING-NOTES.md`。

## 6. 待真机验证（探针负责，结果回填本节）

见任务书第 5 节的 8 个问题。探针 `python-server/tools/broadcast_probe.py` 会把能自动判定
的部分直接写进 `report.md`（注册结果、名册完整性、carId 对照、坐标轴假设检验、
覆盖时间、名次对照、**玩家 delta 对照**），需要人眼确认的部分在报告末尾留空待填。

### 6.1 已经确认的两件事（2026-11-10，本机排查探针预检时发现）

1. **`broadcasting.json` 是 UTF-16LE 且无 BOM**（不是 UTF-8）。
   实测字节：`b'{\x00\n\x00 \x00...'`。直接用 `read_text(encoding="utf-8")` 读不会报错
   （NUL 是合法 UTF-8 字节），但 `json.loads` 一定失败 —— 报错信息是
   `Expecting property name enclosed in double quotes: line 1 column 2`，
   很容易误判成「文件损坏」。读它的代码必须先看字节形态选编码（探针里是
   `detect_encoding()`）。
2. **ACC 生成的默认配置里 `updListenerPort = 0`**，等于广播关闭、游戏不监听任何端口。
   必须手动改成 9000（或任意空闲端口）并**重启 ACC**，否则注册一定超时。
   探针的预检会直接把这条写出来，而不是让你对着「注册超时」猜。

> 这两条都是「不知道就会浪费一晚上」级别的坑，所以先记在 ADR 里，不用等真机报告。

### 6.2 真机验证结果（2026-11-10，Monza，capture-01）

环境：单机 ACC，`updListenerPort=9000`，探针 60s，2412 个包。
原始报文：`python-server/tests/fixtures/broadcast/capture-01/raw.bin`
（已在 `.gitignore`，含真实车手名，**不入库**）。

| 问题 | 结果 |
|---|---|
| 注册 | 成功，`connectionId=1`；单机模式可用 |
| EntryList | 10/10；中文/多字节姓名解码正常；前 9 辆在 140ms 内到齐，第 10 辆是 40.5s 的迟到加入者 |
| carId | 玩家 `playerCarID=1016` 与广播 `carId` 完全对应；`focusedCarId` 当时也等于玩家，但按 §2.3 仍不可依赖 |
| 坐标 | `bcX=+sm.x, bcY=+sm.z`，平均残差约 `5.3e-5 m` → 同源同单位，可直接混用 |
| 名次 | 玩家 57/57 个样本一致 |
| 赛道 | `trackMeters=5793`（Monza），与 `trackmaps/catalog.py` 一致；线上是 int32，见 §2.9 |
| 覆盖 | 9 辆活跃车各 232 次更新，平均 0.259s（≈4Hz）；迟到车辆在本窗口内没有实时更新 |
| 会话 | `race / session`（sessionIndex=1），广播枚举映射正确 |
| delta | **未验证**：采集时玩家在维修区，共享内存 delta 为 0，没有形成对照。需要一次在赛道行驶的采集 |

结论：除 delta 语义外，广播数据已满足「完整排名 + 相对距离」所需。
下一轮可以直接接 pipeline；delta 继续按 `None` 保守处理，待行驶采集后再决定是否放开 0。

### 6.3 接入结果（Phase 2，2026-11-10）

`docs/HANDOFF-broadcast-integration.md` 已全部落地，本文的三条边界在实际代码里的落点：

| 边界 | 落点 |
|---|---|
| 适配层（只认识 ACC 结构体） | `acc_telemetry/sources/broadcast.py`（唯一 import `UDPCarInfo` / `UDPLapInfo` 的文件） |
| 读模型（跨源 join，纯函数） | `acc_telemetry/services/standings_model.py`（不 import backend、不读总线） |
| 契约层（转成前置字段名） | `backend/api/state.py::_standings()` → `state.standings` |

依赖方向仍然单向：`domain ← sources ← services ← backend`，`services` 不反向 import `backend`。
`pyACCSharedMemory.acc_udp` 只出现在 `sources/broadcast.py`（结构体解析）和
`backend/collector/broadcast_reader.py`（只 import `set_register_message` 构注册报文）；
其余 backend 模块不直接接触 vendored 结构体。

新增两条来自实现的约束：

1. **收报不能用 `asyncio.Queue`**：4Hz 的等待节奏遇上 4Hz 的包流，超时取消会直接吞掉已出队的
   报文，实测榜单全是 `stale`。现在 `_DatagramFeed` 用「deque + Event」，Event 只叫醒、数据等 drain。
2. **`reset()` 之后 `session_index` 回到 -1**：直接比较会判成「又换会话了」→ 再 reset → 死循环。
   `_session_key()` 对负值返回 `None`（表示「还不知道，别判」），赛道名在调用侧粘住。
