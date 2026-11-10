# 任务书：ACC 遥测 — 广播数据源基础打通

> 状态：**已实现（2026-11-10）** —— 架构结论见 [`adr/0001-data-source-architecture.md`](./adr/0001-data-source-architecture.md)，
> 交付物与实际落地形状见该 ADR 第 3 节；第 5 节的 8 个问题仍需真机跑探针回填。
> 起草：2026-11-10
> 仓库：`D:\Dev\development\my-code\acc-telemetry-tool\acc-telemetry`
> 平台：Windows（ACC 共享内存和广播协议都只在 Windows 上可用）
> 本轮目标：把 ACC Broadcasting（UDP）数据变成**可信、可离线回放、可被上层消费**的内部数据，
> 不接 UI、不改主题契约。为后续「完整排名 / 相对距离」功能铺路。

---

## 0. 背景（必须理解，避免走错方向）

当前工具只用了 ACC 的**共享内存**。它能提供：自己的速度/圈速/踏板/胎温、自己的官方名次
（`graphics.position`）、参赛车总数（`graphics.activeCars`）、以及全场车的**世界坐标**
（`graphics.carCoordinates[60]` + `carIDs[60]` + `playerCarID`）。

但它**不提供**：其他车的车号、车手名、官方名次、圈速、赛道进度。所以现在做不出完整排行榜，
也算不出与别人的赛道差距。

ACC 还有第二条官方通道：**Broadcasting Network Protocol（UDP）**。它能提供 EntryList
（车号/车手名/车队/组别）和每辆车的 `position` / `trackPosition` / `splinePosition` / 圈速 /
世界坐标。仓库里已经有一份完整的协议解析实现（vendored），但**从未接入运行中的后端**。

**本轮不追求功能可看见，追求底层数据可信。**

---

## 1. 已确认的现状（不要重复调研）

| 事实 | 位置 |
|---|---|
| 自身遥测源接口 `TelemetryProvider`（`available/open/read/read_static/close`），`Snapshot` 是拉取式、同步、约 100Hz | `python-server/acc_telemetry/providers/base.py` |
| 共享内存适配器 | `python-server/acc_telemetry/providers/acc_provider.py` |
| 采集循环：读 provider → `bus.latest_snapshot` + 帧队列 | `python-server/backend/collector/reader.py` |
| 总线：`latest_snapshot` / `latest_static` / `runtime` / `frame_queue` / `event_queue` | `python-server/backend/bus.py` |
| 主题状态组装：`build_state()`，`STATE_VERSION = 1`，只读 `bus.latest_snapshot` + `bus.runtime["state_meta"]` | `python-server/backend/api/state.py` |
| 应用装配：lifespan 里 create_task(recorder / broadcast_worker / collect_loop) | `python-server/backend/api/server.py` |
| 主题契约可读版 / 机器可读单一真源 | `docs/theme-state-contract.md` / `python-server/contracts/telemetry-state.json` |
| 广播协议解析已实现（`set_register_message`、`acc_udp_connect`、`parse_udp_stream`、`UDPCarInfo`、`UDPLapInfo` 等） | `python-server/pyACCSharedMemory/acc_udp.py` |
| 可运行的广播示例（打印名次/车手名/车型/车号） | `python-server/pyACCSharedMemory/tests/read_acc_udp.py` |
| 广播代码当前只被示例引用，**后端零接入** | grep 已确认 |
| 测试是普通 pytest 函数，文件内自行 `sys.path.insert` | `python-server/tests/` |

**已实测的坑**：`UDPEntryList.entryListCars` 是**类级共享的 `defaultdict`**，所有
`UDPBroadcastOutput` 实例共用一个字典（已用 Python 验证）。这意味着：

- 换会话 / 重连必须显式 `entryListCars.clear()`；
- 单元测试之间必须清理，否则串数据；
- 不要假设「新建一个 dataset 就是干净的」。

---

## 2. 本轮范围

### 必做

**A. 架构决策文档（ADR）**

- 新建 `docs/adr/0001-data-source-architecture.md`
- 写清三条边界：数据源适配层 / 读模型层（合并 + 新鲜度）/ 对外契约层；以及「广播是增强，不是依赖」
- 写清领域模型与 ACC 结构体的依赖方向
- 这是后续所有实现的依据，先写再写代码

**B. 广播探针脚本（最高优先级，独立运行）**

- 新建 `python-server/tools/broadcast_probe.py`
- 只依赖标准库 + 仓库内 `pyACCSharedMemory`，**不修改主工程任何文件**
- 注册、请求 EntryList / TrackData、持续接收并累积 `RealtimeCarUpdate`
- 同时打开共享内存读自身数据，用于交叉验证
- 把原始 UDP 报文落盘，供后续回放
- 运行方式（写进 `--help`）：
  ```bash
  cd python-server
  python tools/broadcast_probe.py --host 127.0.0.1 --port 9000 --interval 250 --duration 60 --out tests/fixtures/broadcast/capture-01
  ```

**C. 领域模型 + 广播适配器（纯离线）**

- 新建 `python-server/acc_telemetry/domain/`（纯数据，不 import ctypes / vendor / backend）
- 新建广播适配器（建议 `python-server/acc_telemetry/sources/broadcast.py`）：把 vendor 结构体转成领域模型
- 适配器是**唯一**允许接触 `UDPCarInfo` / `UDPLapInfo` 的地方
- 不接采集循环，不写 bus

**D. record / replay 测试底座**

- 固定一种夹具格式（建议：`[u32 len][u32 t_ms][payload]` 小端重复），落盘原始报文
- 写回放函数：从夹具喂 `parse_udp_stream` → 适配器 → 断言 `StandingsSnapshot`
- 共享内存侧同样支持把三块结构体原始字节落盘并回放（可选，先做广播）
- **若夹具要提交进 git，必须先清洗真实车手姓名**；否则把夹具目录加入 `.gitignore`，只提交生成脚本

**E. 单元测试**

- 新建 `python-server/tests/test_broadcast_adapter.py`、`test_broadcast_replay.py`
- 不依赖游戏、不依赖网络，用合成/夹具数据
- 覆盖：EntryList 累积、换会话清空、缺失字段兜底、名字解码、圈速哨兵值、坐标可为空

### 下一轮（本轮先不做，但 ADR 要预留）

- 把广播客户端作为独立 asyncio Task 接进 `server.py` lifespan
- 新增**类型明确**的槽位（例如 `bus.latest_standings: StandingsSnapshot | None`），
  **不要塞进 `bus.runtime` 这个杂项字典**
- 生命周期跟 `TrackPresence` 的边沿走；断线指数退避；默认关闭/可配置开关
- 然后再谈 `state.py` 组装、契约新增 `standings`、主题消费
- 榜单**不要**落 SQLite，等模型稳定

---

## 3. 硬性架构约束

1. **依赖方向单向**：`domain` ← `sources/adapters` ← `pipeline` ← `state/contract`。
   领域模型不得 import 任何 ACC/后端模块。
2. **ACC 字段名不得泄漏到领域模型以上**：`carCoordinates`、`worldPosX`、`carLocation` 等
   只能出现在适配器内部；领域模型用语义名。
3. **join 主键只用 `carId`**。不要用 `position`、车号、姓名做 join（都不可靠）。
4. **不要用广播的 `focusedCarId` 判断「玩家自己」**，用共享内存的 `playerCarID`；探针要验证两者关系。
5. **频率解耦**：共享内存约 100Hz、广播约 4Hz、主题 state 30Hz。广播绝不驱动高频 HUD。
6. **失败隔离**：广播任何异常都不得设置 `state_error`、不得停止记录、不得影响圈次识别。
   广播只是增强。
7. **契约只增不删**；本轮不修改 `contracts/telemetry-state.json`，不升 `STATE_VERSION`。
8. **不要改 vendored `pyACCSharedMemory/`**。需要补充逻辑就包一层；确有必要时单独提交并写明原因
   （它是上游拷贝，后续可能整体更新）。
9. **现有 100Hz 主链路行为不变**。所有新代码默认不参与运行。

---

## 4. 领域模型建议（第一版，可精简）

```python
# acc_telemetry/domain/model.py
@dataclass(frozen=True, slots=True)
class StandingRow:
    car_id: int
    race_number: int | None
    driver_name: str          # 语义化：优先 "First Last"，其次 shortName，最后 ""
    team_name: str | None
    position: int             # 官方 P/Q/R，0 = 未知
    cup_position: int         # 0 = 未知
    track_position: int       # 0 = 未知
    spline_position: float | None  # 0..1，未知用 None（0 是合法起跑线位置）
    completed_laps: int
    last_lap_ms: int          # 0 = 无
    best_lap_ms: int
    current_lap_ms: int
    delta_best_ms: int | None
    car_location: str         # track / pitlane / pit_entry / pit_exit / garage / unknown
    world_x: float | None
    world_y: float | None
    speed_kmh: int
    in_pit: bool


@dataclass(frozen=True, slots=True)
class StandingsSnapshot:
    received_at: float        # time.monotonic()
    source: str               # "broadcasting"
    track_meters: float | None
    session_type: str         # 复用 session_type_name() 的语义
    session_phase: str        # 语义化，不要暴露原始整数
    rows: tuple[StandingRow, ...]
```

注意：

- `spline_position` 未知时用 `None`，不要用 0。
- 圈速沿用现有的哨兵过滤思路（`99999999` / `2147483647` 视为无成绩）。
- `car_location`、`session_phase` 在适配器里映射成稳定字符串，参照现有
  `session_type_name()` 的做法。

---

## 5. 探针必须回答的问题（把答案回填到 ADR / 交接报告）

1. `broadcasting.json` 到底要放哪、哪些字段、是否需要特殊启动参数（单机是否可用）？
2. 注册能否成功？`connectionId` / `isReadOnly` / `errorMessage` 各是什么？
3. EntryList 是否包含**全部**车辆的车号、姓名、组别？能否稳定重同步？
4. 广播的 `carId` 是否与共享内存 `graphics.carIDs[]` **一一对应**？玩家车 `playerCarID` 能否匹配上？
5. 广播的 `worldPosX/worldPosY` 与共享内存 `carCoordinates.x/z` 的**轴对应、符号、单位、精度**是什么？
   （用玩家车交叉验证）
6. `RealtimeCarUpdate` 的覆盖范围：多久能收齐全场？焦点车之外的车更新频率如何？
7. `trackData.trackMeters` 是否可靠？能否用于 `splinePosition × 长度` 换算米？
8. 排位/正赛里 `position` 是否就是游戏内显示的名次？与共享内存 `graphics.position`（玩家）是否一致？

探针输出至少包含：注册结果、消息类型计数、赛道信息、EntryList 全车列表、每车最新实时行、
与共享内存玩家数据的对照。

---

## 6. 验收标准

- [ ] `cd python-server && python -m pytest tests -q` 全绿（含新增测试）
- [ ] 探针可在无游戏时给出明确中文错误；有游戏时成功产出报告与原始报文
- [ ] 回放夹具 → `StandingsSnapshot` 的结果**确定性可重复**
- [ ] 现有 `frame / session / lap` 消息、`build_state()` 输出、主题契约 JSON **零改动**
- [ ] 新增领域层不 import `ctypes` / `pyACCSharedMemory` / `backend`
- [ ] 没有修改 `pyACCSharedMemory/`（如修改必须单独说明）
- [ ] 未提交任何含真实车手姓名的夹具，或已清洗

---

## 7. 明确禁止

- 不要为了「先用起来」把广播数据直接塞进 `bus.runtime` 或 `state.py`
- 不要让主题参与 join、坐标换算、时间差计算
- 不要用广播数据驱动高频帧
- 不要在本轮加 UI、加设置项、改主题契约、写数据库
- 不要重写现有 `TelemetryProvider` / 采集循环来「顺便统一」
- 不要用 `position` / 姓名 / 车号做跨源 join

---

## 8. 交付物

1. `docs/adr/0001-data-source-architecture.md`
2. `python-server/tools/broadcast_probe.py`（含 `--help` 与运行说明）
3. `python-server/acc_telemetry/domain/`（`model.py`、`__init__.py`）
4. `python-server/acc_telemetry/sources/broadcast.py`
5. `python-server/tests/test_broadcast_adapter.py`、`test_broadcast_replay.py`
6. 夹具格式说明 + 生成脚本（或清洗后的夹具）
7. 一份简短的探针结果报告（第 5 节 8 个问题的答案），由用户在真机上运行后回填

**完成标准不是「能显示排名」，而是「广播数据已经被证明可信、可回放、边界清晰，
下一步接 pipeline 是纯增量」。**
