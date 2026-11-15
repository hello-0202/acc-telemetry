# 社区客户端 · 成绩同步与排行榜可行性评估

> 状态：评估稿（2026-11-11）
> 评估对象：本仓库 v1.4.0 的采集与存储实现
> 结论口径：只讨论「现有代码事实上能不能支撑」，不以 README/设想为依据

---

## 0. 结论摘要

1. **圈速排行榜的数据骨架已经够用**：单圈毫秒、三段、有效性、车型、赛道、1200 点等进度采样（速度/转速/档位/油门/刹车/转向/四轮胎压胎温刹温/G 值/世界坐标）都在本地 SQLite 里，且都是结构化字段，不需要解析日志。
2. **但「可信排行榜」还差三块，且都不是加密能补的**：
   - **身份**：现在没有账号、没有设备标识，唯一「身份」是游戏里的 `car_model` 字符串，任何人可以在本地改库后冒充任何成绩；
   - **比赛条件**：胎温/油量/天气/TC/ABS/胎种等**采集到了却没落库**，排行榜无法分组，公平性无从谈起；
   - **可复算的原始证据**：落库的是按进度分箱降采样后的 1200 点，服务端无法据此无损复算，只能做一致性校验。
3. **加密能做的是「信道 + 身份 + 完整性」，做不了「防作弊」**：TLS 1.3、OIDC 登录、Ed25519 设备签名、OS 凭据库（DPAPI/凭据管理器）都是成熟件，工程量不大；但客户端既持有私钥也持有明文数据库，**任何纯客户端加密方案都可以被本机绕过**。真正的护城河是服务端复算 + 统计校验 + （如果有）社区 ACC 服务器侧的权威成绩。
4. **建议路线**：先补「元数据落库 + 服务端权威字典 + 全局 UUID」，再做「社区账号 OIDC + 增量上传 + 服务端校验」，最后接「服务器侧比赛结果」。不要一上来做客户端加密/反作弊，投入产出比最差。

---

## 1. 现状盘点（代码事实）

### 1.1 已落库：够画榜的部分

`python-server/acc_telemetry/db.py` 的表结构：

| 表 | 关键字段 | 对排行榜的意义 |
|---|---|---|
| `tracks` | `id`、`name`、`length_m`、`spline`、`sectors`、`geometry`、`map_source` | 赛道维度。但 `id` 是**本地 slug 推导**（见 §1.4） |
| `sessions` | `id`(uuid)、`track_id`、`car_name`、`date`、`session_type` | 一次进图一条；`date` 是**本地时钟字符串**，可伪造 |
| `laps` | `id`(uuid4 hex)、`session_id`、`no`、`ms`、`kind`、`valid`、`s1/s2/s3`、`session_type`、`created_at` | 成绩主体。`valid` 直接采信游戏标志 |
| `samples` | `p`、`speed`、`rpm`、`gear`、`throttle`、`brake`、`steer`、四轮 `p/t/bt`、`t_ms`、`g_long/g_lat`、`wx/wy` | 复算与曲线对比的原料 |

采样密度：`config.py` 的 `SAMPLES_PER_LAP = 1200`（注意 `docs/backend-data-contract.md` 第 5 节仍写 720，**文档已过期**，对外契约前必须先统一）。

体量估算（按 26 列、多为 float 计）：SQLite 约 **200 B/点 → 约 240 KB/圈**；30 圈的 session 约 7 MB。全量上传不现实，需分层（见 §6）。

### 1.2 采集到但**没有**落库：排行榜真正缺的部分

`providers/base.py::Snapshot` 与 `acc_provider.py` 已经读出了下列字段，但当前只进实时 `frame`/`state`，**没有任何一张表保存**：

| 类别 | 字段 | 为什么排行榜需要 |
|---|---|---|
| 环境 | `air_temp`、`road_temp`、`rain_intensity`、`rain_in_10min/30min`、`wind_speed/direction`、`track_grip`、`surface_grip`、`time_of_day` | 低温/低油/干湿决定圈速，必须分组或标注 |
| 轮胎 | `tyre_compound`、`rain_tyres`、`current_tyre_set`、`tyre_wear[4]`、`pad_life`、`disc_life` | 软胎排位圈与正赛圈不能同榜 |
| 电子辅助/调校 | `tc_level`、`tc_cut_level`、`abs_level`、`engine_map`、`brake_bias`、`auto_shifter_on` | 辅助等级是分榜维度 |
| 油量 | `fuel`、`fuel_per_lap`、`fuel_used` | 轻油刷圈与满油比赛不可比 |
| 判罚/状态 | `penalty_ms`、`flag_*`、`track_status`、`ai_controlled`、`in_pit*` | 无效圈、AI 代跑、罚时 |
| 名次 | `position`、`drivers_total`、`laps_total`、`session_time_left_ms` | 比赛榜上下文 |

其中 `ai_controlled` 尤其关键：不落库就无法事后剔除 AI 代跑圈。

### 1.3 对手数据（UDP 广播）

`acc_telemetry/domain/model.py` 的 `StandingsSnapshot` 每车含 `driver_name`、`team_name`、`race_number`、`position`、`last/best/current lap`、`spline_position`、世界坐标，约 4Hz。

但按 ADR-0001 §2.2 **明确不落库、不进 `frame`**。这条数据对「同一场服务器内的官方名次校验」很有价值，同时**车手姓名属于个人信息**，上传他人名册有合规问题（见 §8）。

### 1.4 标识符规范化程度：不足

- **赛道**：`trackmaps/resolver.py` 用 `slugify(trackName + configuration)` + 5 条内置目录 + difflib 模糊匹配，未命中就**用哈希兜底**生成 `unknown_xxxxxxxx`。同一赛道在不同客户端、不同 DLC 命名下可能得到不同 `id`（例如 `monza` 与 `monza_2019` 走不同分支），**不能直接作为跨用户主键**。已知赛道只有 5 条（spa/monza/zandvoort/silverstone/barcelona）。
- **车辆**：`trackmaps/cars.py` 只有 23 条 `carModel → 名称` 映射，未命中直接把原始字符串当 id；没有组别（GT3/GT4/GT2/TCX）、没有 BoP/版本维度。
- **圈**：`laps.id = uuid4().hex` 是全局唯一的，**可以直接当上传幂等键**，这一点是现成的优势。
- **时间**：`sessions.date` 来自本机时钟，`created_at` 用 `strftime('now','now')`（UTC）。客户端时间不可信，服务端必须自己盖 `received_at`。

### 1.5 网络与身份现状

- 唯一出网路径是 Tauri updater（`src-tauri/tauri.conf.json` 的两个 endpoint），**没有任何业务上传代码**。
- 没有账号体系、没有 token 存储、没有设备指纹；`config.json` 是明文 JSON。
- `tauri.conf.json` 的 `security.csp = null`，`capabilities/default.json` 没有 `http` 插件权限；`python-server/requirements.txt` 只有 `fastapi`、`uvicorn`，**没有 HTTP 客户端**（同步实现需新增依赖）。
- README 明确承诺「never uploaded to any server」。**做同步就是在改产品承诺**，需要显式开关、默认关闭、隐私说明同步更新。

---

## 2. 排行榜需要的六个硬缺口

按「不做就做不成」排序：

1. **身份与归属（P0）**
   社区账号 ↔ 游戏内车手 ↔ 设备。没有它，榜上的名字没有意义，也没法处理「我成绩被冒用」。
2. **条件元数据落库（P0）**
   §1.2 那张表。最小集：`tyre_compound`、`fuel`（起圈油量）、`air_temp`、`road_temp`、`rain_intensity`、`track_grip`、`tc_level`、`abs_level`、`ai_controlled`、`penalty_ms`。没有这些，榜只能做成「全条件混排」，必然被刷。
3. **赛道/车辆权威字典（P0）**
   服务端维护 `ACC trackName+configuration → canonical id`、`carModel → car id + class`。客户端**只上传原始 ACC 名**，由服务端归一化；客户端现有 slug 规则只用于本地展示，不能外溢成跨用户契约。
4. **成绩定义与分榜维度（P1）**
   榜的 key 必须是 `(track, layout, car_class, car_id 或全车型, session_type, 条件桶, 辅助桶)`。否则「排位 + 软胎 + 10L 油 + 18℃」会永久占据总榜。
5. **证据与校验（P1）**
   摘要 + 可校验样本 + payload hash + schema/client 版本；服务端保存原始包并可重算（§5）。
6. **合规与开关（P1）**
   默认关闭、逐项勾选上传内容、可撤回删除、隐私政策；车手名等 PII 不外传（§8）。

---

## 3. 加密能做什么、不能做什么

### 3.1 能做（成熟、低成本、建议全做）

| 目标 | 方案 | 落点 |
|---|---|---|
| 传输机密性/完整性 | **TLS 1.3** + 严格证书校验；社区域名固定可加 **certificate pinning**（Python `httpx` 自定义 SSLContext 或 Rust `reqwest` 的 `CertificatePinning`） | 客户端同步模块 |
| 账号认证 | **OIDC / OAuth 2.1 + PKCE**，桌面端用**系统浏览器 + 回环回调**（RFC 8252），**不要自建密码登录**，社区网站已有账号就直接复用 | 打开 `https://community.example/oauth/authorize?...` |
| 令牌保管 | 刷新令牌进 **Windows 凭据管理器（DPAPI）/ macOS Keychain / libsecret**，绝不写 `config.json`；短寿命 access token + 轮换 | Rust 侧 `keyring` 或 Python `keyring` |
| 设备身份与防冒用 | 每设备生成 **Ed25519** 密钥对，私钥存 OS 凭据库；注册时上传公钥；每次请求对 `canonical_json(body) + timestamp + nonce` 签名 | 客户端签名，服务端验签 |
| 防重放/防重复入库 | 请求头 `X-Timestamp`（±5 min）、`X-Nonce`（服务端短期缓存）、`Idempotency-Key = laps.id` | 服务端中间件 |
| 本地数据静态保护 | 可选：SQLite 字段级加密或整库 SQLCipher（DPAPI 派生密钥），保护的是「本机其他用户/木马」，**不是防本人改库** | 可选 |
| 发行完整性 | 已有 Tauri updater 的 minisign 公钥（`createUpdaterArtifacts: true`）；建议再加 **Windows Authenticode 代码签名**，降低 SmartScreen 拦截 | CI |

### 3.2 不能做（必须说清楚，避免走弯路）

| 常见想法 | 为什么不行 |
|---|---|
| 「上传前 AES 加密，服务端解密，别人就改不了」 | 密钥必须在客户端，攻击者与本机同一权限，直接改内存/改库/改明文再加密即可。加密只提升逆向成本，不提供真实性 |
| 「客户端签名 = 防作弊」 | 签名只证明「这条数据出自某个已注册设备」，不证明「这条数据是真的」。作弊者用真设备跑假数据，签名照样有效 |
| 「代码混淆/反调试/完整性自检」 | 桌面 Windows 上可被轻易绕过；维护成本高，误报会伤害正常玩家；对社区规模不划算 |
| 「TPM 远程证明」 | 生态碎片化、CI/驱动/虚拟机兼容差，玩家机器上失败率高；除非做高奖金赛事，否则不建议 |
| 「客户端算好 hash 存本地，改了就发现」 | 攻击者可以连 hash 一起改，或直接调用你的上传函数传任意值 |

**一句话**：加密解决「谁在传、传的过程有没有被改」，不解决「传的内容是不是真的」。后者只能靠服务端复算 + 统计异常 + 权威数据源。

### 3.3 最强的「防作弊」其实是数据源选择

如果社区自己有 ACC 服务器（dedicated server），**比赛/排位的成绩应优先取服务器侧结果**（服务器掌控 session、圈速、判罚，客户端无法伪造）。客户端上传只用于：

- 单人 practice / hotlap 榜（社区最常见的需求）；
- 服务器结果的补充（遥测曲线、轮胎数据）；
- 离线成绩（标注 `unverified`，单独一张榜或不进主榜）。

这比在客户端堆任何加密都有效，而且省掉大量对抗成本。

---

## 4. 威胁模型（谁在攻击、挡不挡得住）

| 攻击 | 现有数据能否发现 | 建议对策 | 残余风险 |
|---|---|---|---|
| 直接改 SQLite 里的 `ms` | 能（分段和/速度积分/采样对不上） | 服务端复算 + 样本一致性 | 中：精细伪造可绕过，需统计与人工 |
| 改 `valid`/`kind` 为有效 | 部分（切弯轨迹、分段边界） | 忽略客户端 `valid`，服务端按轨迹+进度自判 | 中 |
| 重放他人的上传包 | 能 | `laps.id` 全局唯一 + 设备签名 + nonce | 低 |
| 冒用他人账号 | 能（设备公钥不匹配） | OIDC + 设备绑定 + 新设备二次确认 | 低 |
| 中间人窃听/篡改 | 能 | TLS + pinning | 低 |
| 内存修改器/物理外挂（抓地力、马力） | 难（需物理一致性） | 纵向/横向 G 与速度、曲率、档位传动比交叉校验 | 高：客户端方案的上限 |
| 抄近道 / 切弯 | 能 | `wx/wy` 轨迹到官方中心线距离阈值 + 进度单调 | 低 |
| AI 代跑 | 能（若落库 `ai_controlled`） | 上传时带标志，服务端丢弃 | 低 |
| 伪造客户端版本 | 部分 | 版本白名单 + 代码签名 | 中 |

结论：**能做到「低成本作弊基本绝迹、高成本作弊可被发现并申诉」**，做不到「绝对可信」。这对社区排行榜是行业常态，可接受。

---

## 5. 服务端可复算的一致性校验（用现有字段就能做）

按性价比排序，全部可在服务端用上传样本完成，无需客户端配合：

1. **分段和**：`|s1+s2+s3 - ms| ≤ 50 ms`（ACC 分段取整误差）。
2. **时间轴自洽**：`t_ms` 单调递增、末值 ≈ `ms`、`p` 单调覆盖 0→1、无跳变 > 2%。
3. **速度积分 vs 圈长**：`Σ v_i·Δt_i`（v 由 km/h ÷ 3.6，Δt 由 `t_ms` 差分）与赛道长度误差 ≤ 2%；未知赛道用 `geometry.lengthM` 或众包中位数。
4. **纵向加速度**：`g_long ≈ Δv/Δt / 9.81`，逐点残差中位数 ≤ 0.15 g；刹车段与 `brake` 正相关、加速段与 `throttle` 正相关。
5. **横向加速度**：由 `wx/wy` 算曲率 κ，`g_lat ≈ v²κ/9.81`；同时校验轨迹到官方中心线距离 ≤ 半赛道宽 + 2 m。
6. **传动比一致性**：`rpm / v` 在同一档位下应近似常数（±3%），能抓「瞬移/改马力」。
7. **踏板物理**：`throttle` 与 `brake` 同时 > 0.9 的样本占比阈值；`steer` 与曲率符号一致率。
8. **温度范围**：胎温 40–130℃、胎压 20–32 psi、刹温 100–1000℃；越界即拒。
9. **圈间一致性**：同 session 内圈速分布、与全场广播 `best_lap_ms` 对照（若同一服务器）。
10. **统计离群**：相对赛道历史分布的 Z-score；分段理论下限（用社区大数据拟合）；新账号首圈进榜人工复核。

只要上传**每圈 60–120 点抽样 + 摘要**，上述 1–9 大部分可算；追求更强校验再要求全量 1200 点（压缩后约 30–50 KB/圈）。

---

## 6. 建议架构（保持 local-first，增量接入）

```
┌──────────── 客户端（现有进程不变） ────────────┐
│ 采集 → recorder → SQLite ──► sync_outbox ──► 同步 Task │
│                                   │                     │
│ 账号/OIDC ◄── 系统浏览器          │ 签名 + gzip + TLS   │
└───────────────────────────────────┼─────────────────────┘
                                    ▼
                     ┌──────── 社区服务端 ────────┐
                     │ 验签 → 校验 → 存档 → 物化榜 │
                     └─────────────────────────────┘
```

设计要点：

- **不改采集链路**：新增 `backend/sync/worker.py` 作为与 recorder/broadcast 平级的 asyncio Task（照抄 ADR-0001 §2.2 的隔离原则：同步挂掉不得影响采集与 HUD）。
- **outbox 模式**：`sync_outbox(lap_id PK, payload BLOB, state, attempts, next_retry_at)`，断网/关机可续传；`lap_id` 即幂等键。
- **默认关闭 + 精细开关**：只传成绩摘要 / 带样本 / 全量；可按赛道、按 session 类型关闭。
- **服务端权威字典**：客户端上报 `acc_track_name`、`acc_track_configuration`、`car_model` 原文，服务端映射；未映射的进待审队列，不直接进榜。
- **服务端保留原始包**：校验规则可迭代、可申诉重算；榜是物化视图，不是唯一真相。
- **版本化**：payload `schema_version` + `client_version` + `samples_per_lap`；拒绝未知版本但落盘留证。

### 6.1 上传载荷草案

```json
{
  "schema_version": 1,
  "client": {"name": "acc-telemetry", "version": "1.5.0", "platform": "windows"},
  "device_id": "ed25519:<pubkey-fingerprint>",
  "lap": {
    "id": "b1c0…（laps.id，幂等键）",
    "track": {"acc_name": "monza", "acc_configuration": "", "canonical_id": null},
    "car": {"acc_model": "ferrari_296_gt3", "class": null},
    "session_type": "practice",
    "ms": 111980, "s1": 37210, "s2": 40120, "s3": 34650,
    "valid": true, "kind": "ok", "penalty_ms": 0,
    "conditions": {
      "air_temp_c": 21.0, "road_temp_c": 28.5, "rain": 0, "grip": 1,
      "tyre_compound": "dry_compound", "fuel_start_l": 30.0,
      "tc_level": 3, "abs_level": 2, "ai_controlled": false,
      "recorded_at_local": "2026-11-11T21:40:12+08:00"
    },
    "samples": {
      "encoding": "columnar",
      "p": [0.0, 0.00083, "…1200 点"],
      "speed": [], "rpm": [], "gear": [], "throttle": [], "brake": [], "steer": [],
      "tyre_temp": [[], [], [], []], "g_long": [], "g_lat": [],
      "wx": [], "wy": []
    }
  },
  "digest": {"algo": "sha256", "value": "…"}
}
```

列式数组比逐点对象小 2–3 倍；gzip 后 1200 点约 30–50 KB。

### 6.2 接口草案

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/v1/devices` | 注册设备公钥，绑定账号 |
| `POST` | `/v1/laps` | 批量上传（≤20 圈/次），`202` 接收，异步校验 |
| `GET` | `/v1/laps/{id}` | 查询校验状态：`pending / verified / rejected(reason)` |
| `GET` | `/v1/leaderboards` | `?track=&layout=&car_class=&session_type=&conditions=` |
| `GET` | `/v1/leaderboards/{id}/around-me` | 个人排名与前后名次 |
| `DELETE` | `/v1/me/laps/{id}` | 撤回（合规要求） |
| `GET` | `/v1/catalog` | 赛道/车辆权威字典（带 ETag，客户端缓存） |

---

## 7. 分阶段路线与工作量

以 1 名熟悉本仓库的开发者计（含测试，不含服务端运维）：

| 阶段 | 内容 | 规模 | 产出 |
|---|---|---|---|
| **P0 打地基** | 元数据落库（迁移 v3）、`laps` 增加 `uuid`/`payload_hash`/`upload_state`、赛道/车辆权威字典、统一采样数（修 720/1200 文档矛盾） | 3–5 人日 | 本地数据具备「可上传性」 |
| **P1 能上榜** | OIDC 登录 + 令牌保管、outbox + 同步 Task、上传/校验/榜单 API、基础榜（单圈、按车型分组）、默认关闭开关 | 客户端 5–8 人日 / 服务端 8–12 人日 | 可用的社区榜 |
| **P2 防作弊** | 服务端 10 条校验、离群检测、申诉与人工复核、`verified` 分级、客户端 Ed25519 签名 | 8–12 人日 | 榜可信度达标 |
| **P3 权威化** | 接入社区 ACC 服务器结果（比赛榜以服务器为准）、赛季/分组/赛道纪录、榜单缓存与 CDN | 视服务器栈 10+ 人日 | 比赛级榜单 |

**建议不要在 P0/P1 之前碰**：客户端加密、代码混淆、TPM、反调试——它们既不解决信任，又会拖慢上线。

---

## 8. 合规与运营风险（容易被忽略）

1. **改产品承诺**：README「never uploaded」必须改，并在首次开启同步时明确告知上传内容；默认关闭。
2. **个人信息**：广播里的 `driver_name` / `team_name` 是他人个人信息，**未经同意不要上传**。只上传玩家自己的数据；同场名次校验可由服务器侧完成。
3. **中国《个人信息保护法》/GDPR**：告知同意、最小必要、可删除、跨境传输（服务器在境外时尤其注意）。
4. **ACC/Kunos**：读共享内存与 UDP 广播是社区通行做法，但**不要使用官方 logo/名称暗示官方认证**；商业化和数据再分发前先看 EULA。
5. **榜单治理**：擦边圈（切弯、出界、维修区捷径）、Bug 圈（游戏版本差异）、DLC 赛道缺失，都需要明确规则与公示，否则社区会先吵起来。
6. **运维成本**：反作弊是长期对抗，不是一次性功能；要预留人工复核与申诉通道。

---

## 9. 最终判断

- **技术上完全可行**，且现有代码的分层（provider → recorder → SQLite；ADR-0001 的隔离原则）很适合增量加一个同步 Task，不需要重构。
- **数据侧的前置条件**是：条件元数据落库 + 服务端权威字典 + 全局幂等键（`laps.id` 已有）。
- **加密侧**：TLS + OIDC + Ed25519 设备签名 + OS 凭据库是标准组合，工程量约 3–5 人日；它保证「信道安全、身份可信、数据未被中途篡改」。
- **但要清醒**：加密不提供「成绩真实性」。排行榜的可信度来自**服务端复算 + 统计校验 + 服务器侧权威结果 + 申诉机制**，客户端加密只是配角。
- **最划算的顺序**：先做「只传摘要、服务端复算、默认关闭」的 MVP，用真实社区数据校准校验阈值，再决定是否要求全量样本、是否上设备签名。

---

## 附录 A：本文引用的代码位置

- 表结构：`python-server/acc_telemetry/db.py`
- 采样密度与合法圈速：`python-server/acc_telemetry/config.py`
- 圈次结果结构：`python-server/acc_telemetry/services/lap_processor.py`
- 可采集字段全集：`python-server/acc_telemetry/providers/base.py`（`Snapshot`）
- 赛道归一化：`python-server/acc_telemetry/trackmaps/resolver.py`、`catalog.py`
- 车辆映射：`python-server/acc_telemetry/trackmaps/cars.py`
- 广播领域模型：`python-server/acc_telemetry/domain/model.py`
- 数据源边界：`docs/adr/0001-data-source-architecture.md`
- 现有契约：`docs/backend-data-contract.md`
- 网络与权限：`src-tauri/tauri.conf.json`、`src-tauri/capabilities/*.json`、`python-server/requirements.txt`

## 附录 B：需要在动工前先定的事

1. 排行榜覆盖哪些 session 类型（只 hotlap/practice，还是含 race）？
2. 是否按车型分榜，还是全车型混排 + 车型筛选？
3. 是否要求上传遥测样本（影响带宽、存储、隐私与校验强度）？
4. 社区服务器是否可提供比赛结果导出（决定比赛榜的权威源）？
5. 是否接受「客户端数据默认不可信、以服务端校验为准」的产品叙事？
