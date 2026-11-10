# 任务书：广播数据接入后端状态（Phase 2）

> 状态：**已实现（2026-11-10）** —— 服务 / 读模型 / `state.standings` / 契约 / 文档 / 测试全部落地，
> `python -m pytest tests -q` → 159 passed（原有 86 项无回归）。真机验收（§9 后半）仍需在 ACC 赛道上跑一遍。
> 字段最终口径以 `docs/theme-state-contract.md` 的 `standings` 一节和
> `contracts/telemetry-state.json` 为准，本文的「建议形状」仅供追溯。
> 前置：`docs/adr/0001-data-source-architecture.md`、`docs/HANDOFF-broadcast-foundation.md`
> 仓库：`D:\Dev\development\my-code\acc-telemetry-tool\acc-telemetry`
> 平台：Windows

---

## 0. 一句话目标

把已经验证过的广播数据（车号 / 车手名 / 官方名次 / 圈速 / 赛道进度 / 世界坐标）
接进后端，合并成一份 **`state.standings`**，通过现有的主题 state 通道推给前端/主题，
让使用者可以在「完整排名 + 相对距离」上做 UI。

**本轮只做到数据可用，不做 UI。**

---

## 1. 已完成、不要重做

数据源本身已经实现并通过真机验证：

| 文件 | 职责 |
|---|---|
| `python-server/acc_telemetry/domain/model.py` | `StandingRow` / `StandingsSnapshot`（纯数据） |
| `python-server/acc_telemetry/sources/broadcast.py` | `UDPCarInfo` → 领域模型；唯一接触 ACC UDP 结构体的地方 |
| `python-server/acc_telemetry/sources/fixture.py` | 原始报文夹具格式 |
| `python-server/acc_telemetry/sources/replay.py` | 夹具 → 确定性 `StandingsSnapshot` |
| `python-server/tools/broadcast_probe.py` | 真机探针（只读注册、抓包、报告） |
| `python-server/tests/test_broadcast_*.py` | 86 项测试全绿 |

**真机已验证（Monza，2026-11-10，capture-01）**：

- 注册成功，单机可用；`connectionId=1`
- EntryList 10/10，中文/多字节姓名解码正常
- 玩家 `playerCarID=1016` 与广播 `carId` 完全对应
- 坐标映射 `bcX=+sm.x, bcY=+sm.z`，平均残差约 `5.3e-5 m` → 同源同单位
- 玩家名次 57/57 与共享内存一致
- `trackMeters` 线上是 **int32**（Monza = 5793），适配器已正确解码
- 9 辆活跃车各约 4Hz（平均 0.259s）；迟到车辆不会立刻有 `RealtimeCarUpdate`
- 会话类型映射正确（`race / session`）

**唯一未验证项**：`deltaBest == 0` 的真实语义。当前适配器按「无有效参考圈」→ `None` 处理，
本轮不要改；等一次玩家在赛道上行驶的采集再定。

**不要修改** `python-server/pyACCSharedMemory/`（vendored）。
**不要提交** `python-server/tests/fixtures/broadcast/` 下含真实车手名的夹具（已 gitignore）。

---

## 2. 本轮范围

### 必做

1. `Snapshot` 增加 `player_car_id`
2. 广播服务（独立 asyncio Task）：配置发现、只读注册、生命周期、重连
3. `bus.latest_standings` 类型明确槽位
4. 读模型：跨源 join、新鲜度、相对距离
5. `build_state()` 输出 `standings`（契约只增不删）
6. 契约 JSON / 主题契约文档同步
7. 测试

### 不在本轮

- 不做任何主题 / Vue 组件 / UI
- 不把榜单写入 SQLite
- 不修改高频 `frame` / `session` / `lap` 消息
- 不做「时间差（秒）」推算（只做距离，见 §7）
- 不做逐段成绩（splits）
- 不加设置页 UI（配置项先用 `config.json` 默认值）

---

## 3. 硬性约束（来自 ADR-0001）

1. **依赖单向**：`domain` ← `sources` ← 读模型 ← `state` / 契约。
   领域层不得 import `ctypes` / `pyACCSharedMemory` / `backend` / `fastapi`。
2. **ACC 字段名止步于适配器**：上层用 `world_x` / `car_location` / `session_phase` 这类语义名。
3. **join 主键只用 `carId`**；不能用 `position` / 车号 / 姓名。
4. **玩家身份只认共享内存 `graphics.playerCarID`**；不要用广播 `focusedCarId`。
5. **广播是增强不是依赖**：任何异常都不得设置 `state_error`、不得停止录制、
   不得影响圈次识别、不得阻塞 100Hz 采集。
6. **频率解耦**：共享内存 ~100Hz、广播 ~4Hz、state 30Hz。广播绝不驱动高频 HUD。
7. **契约只增不删**；不升 `STATE_VERSION`（当前为 1）。
8. **广播数据不许塞进 `bus.runtime`**（那个字典已经是杂项抽屉）。
9. **同进程只允许一个 `BroadcastAdapter` 实例**（`UDPEntryList.entryListCars` 是类级共享字典）。
10. **不要改 vendored `pyACCSharedMemory/`**。

---

## 4. 实施步骤

### 4.1 `Snapshot` 增加 `player_car_id`

文件：`python-server/acc_telemetry/providers/base.py`、`.../providers/acc_provider.py`

- `Snapshot` 增加 `player_car_id: int = 0`（带默认值，避免破坏现有构造调用）。
- `AccSharedMemoryProvider.read()` 里填 `int(getattr(g, "playerCarID", 0))`。
  当前 `_player_world_xy()` 已经读了 `playerCarID`，可顺手复用 / 提出。
- 这是跨源 join 的唯一自我标识；不要再从广播侧猜。

### 4.2 抽出 `broadcasting.json` 发现逻辑

现在 `tools/broadcast_probe.py` 里有这些纯函数：路径候选、`detect_encoding`、
`read_text_any_encoding`、解析 `updListenerPort`。**不要把 tools 当库 import。**

建议新建：`python-server/acc_telemetry/sources/broadcasting_config.py`

- 提供 `find_broadcasting_json() -> Path | None`
- `load_broadcasting_config(path=None) -> dict`（含 `path` / `encoding` / `port` / 密码 / 原始 keys）
- 处理 UTF-16LE 无 BOM（已实测）
- 探针改为 import 这些函数；`test_broadcast_probe_helpers.py` 里原有的导入路径
  可以在探针里保留 re-export，避免不必要地改测试。

规则：`updListenerPort <= 0` → 广播关闭，服务保持 idle 并在 state 里给出 `reason`。

### 4.3 广播服务（独立 asyncio Task）

建议文件：`python-server/backend/collector/broadcast_reader.py`

职责，且只做这些：

- 用**非阻塞 socket**（`sock.setblocking(False)` + `loop.sock_recv` / `loop.sock_sendto`）
  完成注册和数据接收。不要用阻塞 `recv`，不要开线程。
- 注册时发**只读** `command_password`（见探针里的 `READ_ONLY_COMMAND_PASSWORD`），
  探针已验证这样能正常收数据。
- 注册成功后请求 `REQUEST_ENTRY_LIST` / `REQUEST_TRACK_DATA`。
- 把每个 UDP 包喂给 `BroadcastAdapter.feed(payload)`。
- 以 **≤4Hz** 的节奏调用 `adapter.snapshot()`，写入 `bus.latest_standings`。
  不要每个包都组装。
- 生命周期：
  - 游戏在跑且处于赛道会话（可参考 `bus.runtime["game_running"]`、
    `bus.runtime["acc_status"]`、`bus.latest_static`）时才连接；
  - 离开赛道 / 游戏退出 → 关闭 socket，`bus.latest_standings = None`；
  - 会话或赛道变化（`sessionIndex` / `sessionType` / `trackName` 变）→
    `adapter.reset()`，重新请求名册与赛道数据；
  - 无包超过约 3s → 视为断线，关闭后按 1s / 2s / 5s / 10s 退避重连。
- 名册补拉：
  - 启动时请求一次；
  - `adapter.needs_entry_list_sync` 为真时请求；
  - `not adapter.entry_list_complete` 时每 5s 请求；
  - 其余情况每 30s 请求一次（覆盖迟到加入者）。
- TrackData：每次进入赛道/换赛道请求一次；不要每帧请求。
- 所有异常必须捕获并记日志，任务本身永不退出（除非 `stop_event`）。

在 `python-server/backend/api/server.py` 的 lifespan 里：
- `asyncio.create_task(broadcast_standings_loop(stop_event, ...), name="standings")`
- 与 recorder / broadcast_worker / collect_loop 平级；
- 停止时先 `stop_event.set()`，再 `await` 它退出；
- **不得**让它阻塞其他 Task 的启动/退出。

配置项（`python-server/acc_telemetry/repositories/config_repo.py::DEFAULTS`）：

```python
"broadcastEnabled": True,
"broadcastPort": 0,          # 0 = 自动读 broadcasting.json，读不到则回退 9000
"broadcastIntervalMs": 250,  # realtimeUpdateInterval
```

- `broadcastEnabled=False` → 服务不连接，state 里 `standings.available=false`, `reason="disabled"`
- `broadcastPort=0` 且文件里端口为 0 → `reason="port-not-configured"`
- 本轮不加设置页控件；`config.json` 手改即可。

### 4.4 总线槽位

文件：`python-server/backend/bus.py`

```python
latest_standings: StandingsSnapshot | None = None
```

- 在 `reset()` 里置 `None`。
- **不要**放进 `runtime`。
- 类型标注用 `Any` 或 `TYPE_CHECKING` 导入，避免循环依赖。

### 4.5 读模型（纯函数）

建议文件：`python-server/acc_telemetry/services/standings_model.py`

输入：
- `snapshot: StandingsSnapshot | None`
- 自身参照：`car_id` / `position` / `spline` / `completed_laps`
  （从 `Snapshot.player_car_id` / `.position` / `.progress` / `.completed_laps` 取）
- `now: float`（`time.monotonic()`）
- `stale_after_s`（建议 3.0）

输出：给契约用的普通 dict（或 frozen dataclass，再在 state.py 转 dict）。

规则：

1. `snapshot is None` → `{available: False, stale: False, reason: "no-data", rows: []}`
2. `now - snapshot.received_at > stale_after_s` → `stale: True`，但**保留** rows
   （名次/姓名是历史结论，位置类字段已由适配器置空）。
3. 按 `car_id` 找 `self`；找不到 → `self: None`，rows 仍然输出。
4. `track_meters` 为 `None` → 所有 `gapToSelfM` 为 `None`。
5. 排序沿用适配器：官方 `position` 优先，其次 `track_position`，最后 `car_id`。
6. 所有浮点四舍五入到合理精度（坐标/距离 0.1m，spline 保留 5 位）。

### 4.6 `state.py` 集成

文件：`python-server/backend/api/state.py`

- `_build_live()` 里调用读模型，把结果放到 `state["standings"]`。
- `_blank_state()` 里也要有完整的 `standings` 空结构（键不缺失）：
  ```python
  "standings": {
      "available": False, "stale": False, "complete": False,
      "source": "broadcasting", "reason": "no-data",
      "trackMeters": None, "self": None, "rows": [],
  }
  ```
- **只有 `status == "streaming"` 时才输出实时值**（沿用现有稳定性规则）。
- 现有字段一个都不动；`STATE_VERSION` 保持 1。
- 不要把 `StandingsSnapshot` 或 `StandingRow` 直接塞进 JSON（要转成契约字段名）。

### 4.7 契约与文档

1. `python-server/contracts/telemetry-state.json`
   - `example` 增加 `standings`（`mock-state.js` 会自动拿它当预览数据，见 §4.8）
   - `fields` 列表增加 `standings.*` 条目（type / unit / source / desc）
   - `deferred` 里的 `opponents` 改为已实现或删除
2. `docs/theme-state-contract.md`
   - 新增 `standings` 小节：字段表、单位、符号约定、`available/stale/complete` 语义、
     null 的含义
   - 更新「暂不暴露」表
3. `docs/backend-data-contract.md`
   - 注明：`standings` 只走 **state 通道**，不进高频 `frame`
4. `docs/README.md` 给本任务书加索引（📋 任务书）

### 4.8 前端预览数据（可选但推荐）

`src/telemetry/mock-state.js` 直接深拷贝 `contract.example`。
只要 §4.7 把 `standings` 写进 `example`，设置页预览和 `dev.html` 就会自动带数据，
主题作者不用开游戏就能排版。**不要另写一套 mock 结构。**

---

## 5. 契约形状（建议）

```json
{
  "standings": {
    "available": true,
    "stale": false,
    "complete": true,
    "source": "broadcasting",
    "reason": null,
    "trackMeters": 5793.0,
    "self": {
      "carId": 1016,
      "raceNumber": 811,
      "name": "BAOBAO DUDU",
      "team": null,
      "position": 9,
      "cupPosition": 0,
      "trackPosition": 9,
      "spline": 0.03,
      "laps": 0,
      "lastLapMs": 0,
      "bestLapMs": 0,
      "deltaBestMs": null,
      "location": "pitlane",
      "inPit": true,
      "x": 123.4,
      "z": -567.8,
      "gapToSelfM": 0.0,
      "gapAheadM": 12.4,
      "gapBehindM": -8.7,
      "stale": false
    },
    "rows": [
      {
        "carId": 1004,
        "raceNumber": 802,
        "name": "Selim Ganiev",
        "team": null,
        "position": 1,
        "cupPosition": 0,
        "trackPosition": 1,
        "spline": 0.233,
        "laps": 3,
        "lastLapMs": 115085,
        "bestLapMs": 114900,
        "currentLapMs": 42000,
        "deltaBestMs": null,
        "location": "track",
        "inPit": false,
        "x": 10.0,
        "z": 20.0,
        "gapToSelfM": 1250.6,
        "stale": false
      }
    ]
  }
}
```

字段约定：

| 字段 | 说明 |
|---|---|
| `available` | 是否有广播数据可用；false 时 rows 为空或为空数组 |
| `stale` | 整份快照超过 TTL；rows 仍保留但位置类字段可能为 null |
| `complete` | EntryList 是否收齐（`entry_list_complete`） |
| `source` | 固定 `"broadcasting"` |
| `reason` | 不可用原因：`disabled` / `port-not-configured` / `connecting` / `timeout` / `no-session` / `no-data` |
| `trackMeters` | 赛道长度，来自适配器；未知为 null |
| `gapToSelfM` | **正 = 在自车前方，负 = 在自车后方**，单位米 |
| `x` / `z` | 与 `state.position.x/z` 同一坐标系（已验证同源同单位） |
| `deltaBestMs` | 未知为 null（0 目前按未知处理） |
| `position` | 官方 P/Q/R，0 表示未知 |

---

## 6. 生命周期状态机（建议）

```
IDLE（游戏未运行 / 广播关闭 / 端口为 0）
  │ 游戏进入赛道 && 配置可用
  ▼
CONNECTING（发送只读注册，等待 REGISTRATION_RESULT）
  │ 成功                         │ 超时 / 失败
  ▼                              ▼
CONNECTED ── 每 250ms 组装 ──→ bus.latest_standings     BACKOFF（1/2/5/10s）
  │ 无包 >3s / 离开赛道 / 换会话        ▲
  ▼                                   │
RESET + 重新请求名册/赛道 ─────────────┘
```

- 任何阶段异常都不能冒泡到 lifespan。
- 离开赛道时 `bus.latest_standings = None`，旧榜单不能继续显示。
- 会话/赛道变化必须 `adapter.reset()`（类级共享字典的坑）。

---

## 7. 相对距离算法

对每行：

```python
if row.spline_position is None or self.spline is None or track_meters is None:
    gap = None
else:
    d_spline = row.spline_position - self.spline
    if d_spline > 0.5:
        d_spline -= 1.0
    elif d_spline < -0.5:
        d_spline += 1.0
    d_laps = row.completed_laps - self.completed_laps
    gap = (d_laps + d_spline) * track_meters
```

- 正数 = 前方，负数 = 后方。
- `self` 行的 gap 固定 `0.0`。
- `self.gapAheadM`：所有正 gap 中最小的；`self.gapBehindM`：所有负 gap 中最大的（最接近 0）。
- 没有有效值时给 `null`，不要给 0。
- **本轮不做时间差（秒）**；那需要插值/圈速模型，等距离稳定后再单独立项。

---

## 8. 测试要求

新增（不依赖真机、不依赖 gitignored 夹具）：

1. `python-server/tests/test_standings_model.py`
   - 无广播数据 → `available=False`
   - 超过 TTL → `stale=True`，rows 保留
   - self 匹配 / self 缺失
   - gap 符号、跨起跑线 wrap（±0.5 修正）、差圈
   - `track_meters=None` → gap 全 null
   - 排序：有 position / 只有 track_position / 都没有
2. `python-server/tests/test_state_standings.py`
   - 用 `SimpleNamespace` 伪造 `bus.latest_snapshot` / `bus.latest_standings` / `runtime`
     （参考现有 `test_state_delta_best.py` 的写法）
   - streaming 时包含 standings；idle/waiting 时为空结构
   - 现有字段与 `STATE_VERSION` 不变
3. 服务层（可选，至少覆盖纯逻辑）
   - 配置发现：UTF-16LE、端口 0、路径缺失
   - 退避序列
   - 不要为了测试引入真实 socket

验收命令：

```bash
cd python-server
python -m pytest tests -q
```

现有 86 项必须继续全绿。

---

## 9. 验收标准

- [ ] `cd python-server && python -m pytest tests -q` 全绿
- [ ] 不开 ACC 启动应用：无异常，`GET /api/state` 的 `standings.available=false`，
      `reason` 合理，现有字段不变
- [ ] ACC 在赛道 + `updListenerPort=9000`：
      - `GET /api/state` 出现 `standings.available=true`
      - `rows` 含车号/姓名/名次/圈速/进度/坐标
      - `self` 正确指向 `playerCarID`
      - `gapToSelfM` 符号正确，起跑线附近不出现 ±一整圈的错误
- [ ] `/ws/telemetry` 的 state 消息包含 `standings`
- [ ] 断开广播 / 关闭广播端口：录制、圈速、主面板、悬浮窗完全不受影响
- [ ] 换会话 / 换赛道后不残留上一场车辆
- [ ] `STATE_VERSION` 仍为 1；`frame/session/lap` 形状未变
- [ ] 未修改 `pyACCSharedMemory/`
- [ ] 未提交真实车手姓名夹具

---

## 10. 禁止事项

- 不要把 `standings` 塞进 `bus.runtime`、也不要把 `StandingRow` 原样丢给前端
- 不要用 `focusedCarId` 判断玩家
- 不要用 `position` / 姓名 / 车号做 join
- 不要把广播数据放进高频 `frame`
- 不要每帧都请求 EntryList / TrackData
- 不要在事件循环里做阻塞 `recv` / `sleep`
- 不要让广播异常进入 `state_error`
- 不要为了「先显示」而绕过 TTL / `available` / `stale`
- 不要改 `deltaBest == 0` 的现有处理（等行驶采集）
- 不要本轮顺手做 UI 或设置页

---

## 11. 交付物

1. `Snapshot.player_car_id` + provider 填充
2. `acc_telemetry/sources/broadcasting_config.py`（探针复用）
3. `backend/collector/broadcast_reader.py`
4. `bus.latest_standings`
5. `acc_telemetry/services/standings_model.py`
6. `backend/api/state.py` 的 `standings`
7. `contracts/telemetry-state.json` + `docs/theme-state-contract.md` + `docs/backend-data-contract.md`
8. 新增测试与全绿结果
9. 一份简短的实现说明（实际文件路径、配置项、如何验证）

---

## 12. 已定决策 / 待确认

已定：

- 只读注册；错误 `commandPassword` → 只读连接（探针已验证）
- `gapToSelfM` 正 = 前方，负 = 后方
- 本轮只做距离，不做时间差
- `deltaBest == 0` 继续按未知（`null`）
- 广播不进高频 frame，只进 state

待确认（实现时可留 TODO）：

- ACC 允许多少个广播客户端；多客户端时 `maxConnections` 的行为
- 会话切换瞬间 `sessionIndex` 的抖动（实现时以「变化即 reset」为保守策略）
- 迟到加入者的名册补拉频率是否 5s 足够
