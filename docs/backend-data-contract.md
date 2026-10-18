# ACC 遥测工具 · 前后端数据契约（Data Contract）

> 适用范围：Vue 前端（MonitorView / HistoryView / SettingsView） ↔ 后端（python-server：WebSocket 实时 + REST/SQLite 历史）
> 约定：时间单位全部使用 **毫秒（ms）**；比例/归一化字段范围见各字段说明。

---

## 一、总览：两条数据通道

| 通道 | 协议 | 用途 | 触发 |
|------|------|------|------|
| 监测（实时） | WebSocket `ws://127.0.0.1:8008` | 实时帧流、圈完成通知 | 前端发 `{"cmd":"start"}` |
| 历史（查询） | HTTP REST（同服务或独立端口） + SQLite | 赛道列表、圈速、单圈采样、删除 | 前端按需 GET / DELETE |

后端 `python-server/server.py` 同时承担两个角色：
1. 读 ACC 共享内存 → 转发为 WebSocket 帧；
2. 暴露 `/api/*` 读写本地 SQLite 库（落库每圈采样）。

---

## 二、监测（实时）— WebSocket

### 2.1 连接与握手
- 前端连接后发送：`{"cmd":"start"}`
- 后端先回一条 `session`（检测到的赛道/车辆/几何），随后高频推 `frame`，每圈结束推 `lap`。

### 2.2 `session` 消息（每监测会话一次）
```json
{
  "type": "session",
  "track": {
    "id": "spa",
    "name": "Spa-Francorchamps",
    "lengthM": 7004,
    "spline": [[x, y], ...],        // 120~200 个点，用于迷你地图画赛道线
    "sectors": [0.33, 0.66],        // S1/S2 分界（按进度 0..1）
    "corners": [{"pos": 0.07, "label": "1"}, ...]  // 可选弯号标记
  },
  "car": { "id": "porsche_991ii_gt3_r", "name": "Porsche 991 II GT3 R" },
  "carSpec": { "maxRpm": 9000, "gearCount": 6, "maxFuel": 100 },
  "pbMs": 137579                    // 可选：当前赛道 PB，用于实时对 PB Delta
}
```
> ⚠️ ACC 共享内存**不提供**赛道样条，后端需维护一份「trackName → spline」的赛道地图库（初始放几条标定赛道，逐步扩充）。

### 2.3 `frame` 消息（实时帧，建议 30–60Hz 推送；ACC 物理 100Hz）
```json
{
  "type": "frame",
  "t": 1718000000123,               // 时间戳 ms
  "lap": 3,                         // 当前圈号
  "lapTime": 42150,                 // 当前圈已用时间 ms
  "valid": true,                    // 当前圈是否有效
  "speed": 248.3,                   // km/h → 速度 HUD
  "rpm": 8420,                      // → 转速灯 / 红线
  "gear": 5,                        // → 档位
  "throttle": 0.82,                 // 0..1 → 油门踏板
  "brake": 0.10,                    // 0..1 → 刹车踏板
  "steer": -0.23,                   // -1..1 → 方向盘
  "progress": 0.421,                // 0..1 圈进度 → 迷你地图游标点
  "wheels": [
    {"corner":"FL", "pressure":27.8, "temp":91.2, "brakeTemp":412},
    {"corner":"FR", "pressure":27.5, "temp":89.0, "brakeTemp":398},
    {"corner":"RL", "pressure":26.9, "temp":96.5, "brakeTemp":530},
    {"corner":"RR", "pressure":27.1, "temp":94.8, "brakeTemp":521}
  ]
}
```

**MonitorView 字段消费映射**
| 组件 | 消费字段 |
|------|----------|
| 速度 HUD | `speed` / `rpm` / `gear` |
| 踏板条 | `throttle` / `brake` / `steer` |
| 工程矩阵 2×2 | `wheels[4]` 的 `pressure` / `temp` / `brakeTemp` |
| 迷你地图 | `progress` + session 的 `spline` |

### 2.4 `lap` 消息（每圈完成）
```json
{
  "type": "lap",
  "no": 3, "ms": 137579, "kind": "ok", "valid": true,
  "s1": 32290, "s2": 51013, "s3": 57055,
  "samples": [ /* 可选：本圈完整采样，用于落库 */ ]
}
```
- `kind` 取值：`ok` / `invalid` / `out`（出场圈）
- 用途：刷新左栏「数据集 N 圈」、更新 PB、写入 SQLite。

---

## 三、历史（REST + SQLite）

### 3.1 SQLite 表结构
```sql
tracks(
  id TEXT PRIMARY KEY,
  name TEXT,
  length_m REAL,
  spline TEXT,        -- JSON: [[x,y],...]
  sectors TEXT,       -- JSON: [0.33,0.66]
  date TEXT
);

sessions(
  id TEXT PRIMARY KEY,
  track_id TEXT,
  car_name TEXT,
  date TEXT
);

laps(
  id TEXT PRIMARY KEY,
  session_id TEXT,
  no INTEGER,
  ms INTEGER,
  kind TEXT,          -- ok / invalid / out
  valid INTEGER,
  s1 INTEGER, s2 INTEGER, s3 INTEGER
);

samples(
  lap_id TEXT,
  idx INTEGER,
  p REAL,             -- 0..1 进度（均匀分布，建议 150~300 点/圈）
  speed REAL, rpm INTEGER, gear INTEGER,
  throttle REAL, brake REAL, steer REAL,
  fl_p REAL, fl_t REAL, fl_bt REAL,
  fr_p REAL, fr_t REAL, fr_bt REAL,
  rl_p REAL, rl_t REAL, rl_bt REAL,
  rr_p REAL, rr_t REAL, rr_bt REAL
);
```

### 3.2 接口清单

#### `GET /api/tracks`
返回左栏赛道列表。
```json
[
  {"id":"spa","name":"Spa-Francorchamps","laps":24,"pbMs":137579,
   "date":"2026-10-17","live":false}
]
```

#### `GET /api/tracks/:id/laps`
点赛道展开圈速列表。
```json
[
  {"no":1,"ms":140210,"kind":"out","valid":false,"deltaPb":2631},
  {"no":7,"ms":137579,"kind":"ok","valid":true,"pb":true,"deltaPb":0}
]
```

#### `GET /api/tracks/:id/laps/:no`
右栏分析的完整数据源。
```json
{
  "no":7, "ms":137579, "kind":"ok", "valid":true,
  "s1":32290, "s2":51013, "s3":57055,
  "track": {"id":"spa", "spline":[[x,y],...], "sectors":[0.33,0.66]},
  "samples": [
    {"p":0.0,"speed":241.2,"rpm":8400,"gear":6,"throttle":0.9,"brake":0.0,"steer":0.0,
     "flP":27.8,"flT":91,"flBt":412, "frP":27.5,"frT":89,"frBt":398,
     "rlP":26.9,"rlT":96,"rlBt":530, "rrP":27.1,"rrT":94,"rrBt":521},
    ...
  ]
}
```

#### `DELETE /api/tracks/:id`
对应左栏 ⋮「删除此赛道」，删除该赛道及关联圈速/采样（参数化查询，防注入）。

### 3.3 HistoryView 字段消费映射
| 右栏区块 | 消费字段 |
|----------|----------|
| 圈速概要 | `ms` / `deltaPb` / `pbMs` |
| 弯对比 | `s1`/`s2`/`s3` + 选弯后按 `p` 取 `samples` 相邻点算 Δ |
| 时序曲线 | `samples[].p` 为 X，`speed/rpm/throttle/brake/steer` 为 Y；弯道带用 `sectors`+`spline` |
| 热力图 | `samples[].{flT,frT,rlT,rrT,...}` 沿 `p` 着色 |
| 差距图 | 当前圈 vs PB 圈按 `p` 对齐逐点 Δ |
| 迷你地图 | `track.spline` 画线，游标由图表 cursor 的 `p` 决定 |

---

## 四、后端对接要点（硬约束）

1. **spline 来源**：ACC 不给赛道几何，后端必须维护赛道地图库（id/name → spline）。监测握手与圈速详情都返回它，前端才画得出地图。
2. **采样按进度对齐**：所有历史对比（弯/Δ/热力）都按 `p`（0..1 进度）对齐，而非时间戳。所以 `samples` 必须带 `p` 且均匀分布，前端插值才准。
3. **PB 透传**：监测帧若显示「对 PB」，可在 `session` 带 `pbMs`，或前端进监测页前先 `GET /api/tracks` 取一次。
4. **SQL 安全**：所有删除/查询使用参数化语句，禁止字符串拼接。
5. **采样分辨率**：每圈 `samples` 默认下采样到 **720 点**（`acc_telemetry/config.py` 的 `SAMPLES_PER_LAP`，可调）。点越多时序曲线越细，库体积也越大；后端以 100Hz 采样，720 点约等于每 0.1–0.2 秒一个点。

---

## 五、前端对接模块（建议）
- `useTelemetry()`：封装 WebSocket 连接、session 解析、frame 写入响应式状态、start/stop。
- `useHistory()`：封装 `GET /api/tracks`、`GET laps`、`GET lap detail`、`DELETE`，以及按 `p` 对齐计算 Δ 的工具函数。

---

## 六、实现扩展（v0.2，向后兼容）

以下为实现时新增的字段/接口，均不改变上述必选字段的语义。

### 6.1 数据库位置与表
- 路径：`%LOCALAPPDATA%\acc-telemetry\telemetry.db`（环境变量 `ACC_TELEMETRY_DB` 可覆盖，仅用于测试）。
- 新增 `config(key TEXT PRIMARY KEY, value TEXT)` 表保存前端配置。
- `samples` 新增可空列：`t_ms`（本圈已用时间 ms）、`g_long`/`g_lat`（车体坐标 G 值）。

### 6.2 REST 扩展
- `GET /api/tracks` 每项额外返回 `lenM`、`car`。
- `GET /api/tracks/{id}/laps`：
  - 默认返回**最新 session** 的圈；可用 `?sessionId=` 指定。
  - 每项额外返回 `pbMs`、`pbNo`（全赛道 PB 的圈号）、`sessionId`。
  - `deltaPb` 为 `null` 时表示该圈不是有效圈或暂无 PB。
- `GET /api/tracks/{id}/laps/{no}` 额外返回 `pbMs`、`sessionId`；samples 额外带 `tMs/gLong/gLat`。
- `DELETE /api/tracks`：清空全部历史（Settings 使用）；监测中返回 409。
- `GET/POST /api/config`：`{pushHz, autoStart, dbPath, version}`；`pushHz` 取值 5–60。
- 数据库不可用时 `/api/*` 返回 503；实时推流不受影响。

### 6.3 WebSocket 扩展
- 路径保持不变：`ws://127.0.0.1:8008/ws/telemetry`。
- `session` 额外带 `sessionId`、`dbOk`、`pbRef`（PB 圈按 p 对齐的 `{p:[], tMs:[]}`，约 240 点，用于实时 delta）。
- `frame` 的 `gear` 保持 ACC 原始值（0=R, 1=N, 2+=1 档），额外提供 `gearText` 供 UI 直接显示；另带 `currentMaxRpm`。
- `lap` 额外带 `pb`、`pbMs`、`sampleCount`、`persisted`；**不携带 samples**（已落库，前端通过 REST 刷新）。
- 非契约消息：`{"type":"error","code","message","error"}`（保留旧版 `error` 键）、`{"type":"warning","message"}`、`{"type":"stopped"}`。
- 同一时刻只允许一个采集会话；重复 `start` 返回 `ALREADY_STREAMING`。
- 暂停/回放（`graphics.status != 2`）时不采样、不推送。

### 6.4 赛道几何
- ACC 共享内存不提供赛道样条。后端内置 spa / monza / zandvoort / silverstone / barcelona 五条近似形状（480×290 坐标系、160 点），未知赛道回退为确定性占位环线并置 `geometryKnown=false`、`lengthM=null`。
- 这些形状只用于迷你地图展示，不是真实测绘数据；替换 `python-server/acc_telemetry/trackmaps/catalog.py` 与 `shapes.py` 即可接入真实几何。

### 6.5 历史记录按 session 分组（v0.3）
每次进入赛道都会新建一条 `sessions` 行；历史页把**每条 session 显示为一条独立记录**（同一赛道多次进图 = 多条记录，而不是合并成一条）。

- `GET /api/sessions`
  ```json
  [{"id":"<session id>","trackId":"monza","name":"Monza","lenM":5793,
    "car":"McLaren 720S GT3 EVO","date":"2026-10-17",
    "laps":8,"pbMs":111980,"live":false}]
  ```
- `GET /api/sessions/{session_id}/laps`
- `GET /api/sessions/{session_id}/laps/{no}`
- `DELETE /api/sessions/{session_id}`（删除一条记录）
- `DELETE /api/sessions`（清空全部记录）

`PB`、`deltaPb`、`pbNo` 均按单条 session 统计；监测页「数据集」显示当前 session 已完成的圈数。原 `/api/tracks/*` 接口已由上述接口替代。
- `date` 字段为 `YYYY-MM-DDTHH:MM:SS`（旧记录可能只有 `YYYY-MM-DD`）；前端头部显示到分钟，列表中使用 `MM-DD HH:mm` 紧凑格式。

### 6.6 圈边界与出场圈
- 圈边界优先使用 `completedLaps`，同时用「进度回绕（记录本轮最大进度）」和「圈计时器大幅归零」兜底，避免出场圈/飞驰圈被合并。
- `out` 只表示从维修区出发（起圈在 pit lane 或多数采样点在 pit lane）；监测中途开始导致的采样不完整不再标记为出场圈。
- 采样点少于 10 个的误判边界会被丢弃。
- **出场圈不落库**。每个 session 只保存有效/无效的飞驰圈，圈号 `no` 从 **1** 开始按实际保存顺序连续编号，与 ACC 的 `completedLaps` 无关；因此出场圈之后的第一个飞驰圈显示为 `L1`。数据库迁移 v2 会删除已有出场圈并重新编号。

### 6.8 端口自动选择
- sidecar 优先监听 `127.0.0.1:8008`；被占用时自动尝试 **8009–8019**。
- 前端启动时依次探测 `8008–8019` 的 `GET /health`，用返回 `ok:true` 的端口作为 REST/WS 基地址。
- 后端重启或换端口后，前端在下一次请求/重连时重新探测；设置页会显示实际使用的地址。

### 6.7 赛道地图自动生成（实验）
- 采集时读取 `graphics.carCoordinates` 中玩家车（按 `playerCarID` 匹配 `carIDs`）的世界坐标，随采样点保存在内存，不落库。
- 每完成第一个有效圈（`kind='ok'`），后端按 `p` 排序、平滑、归一化到 480×290 视口，等弧长重采样为 160 点，写入 `tracks.spline`，并标记 `map_source='recorded'`。
- 之后同一赛道的所有 session 在 `session.track.spline` 中优先使用 recorded 地图；内置近似形状只在没有实跑地图时使用。
- 生成成功后额外发送：
  ```json
  {"type":"trackmap","trackId":"spa","spline":[[x,y],...],"geometryKnown":true}
  ```
  前端收到后只替换地图，不重置当前圈状态。
- `recorded` 地图不会被后续 session 启动时写入的 generated 形状覆盖；删除历史记录不会删除赛道地图（`tracks` 行保留）。
