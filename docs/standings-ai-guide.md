# standings 使用指南（给 AI / 主题作者）

> **这份文档的用途**：让一个没看过本仓库代码的 AI，读完就能正确消费 `state.standings`，
> 不踩坑、不瞎猜、不编字段。全文只需要 `acc:state` 消息里的 `payload.standings` 这一个对象。
>
> 数据真源是 `python-server/contracts/telemetry-state.json`（含 `enums` 和完整示例），
> 可读版口径在 [`theme-state-contract.md`](./theme-state-contract.md) 的 `standings` 一节。
> 本文与那两份冲突时，以 JSON 为准。

---

## 0. 三十秒速览

`standings` 是**全场每辆车的只读榜单**，来自 ACC 的 UDP 广播（后端另开一条只读连接），
和共享内存那条链路**完全独立**。它提供：**身份 + 名次 + 赛道进度 + 圈速 + 坐标 + 相对你本人的距离**。

```js
const st = payload.standings;

if (!st.available) {
  showEmpty(st.reason);        // 7 种原因，见 §3.4
} else {
  for (const row of st.rows) { // 已按官方名次排好序
    // row.position 0 = 未知（不是第一名！）
    // row.gapToSelfM 正 = 在你前方，负 = 在你后方，单位「米」，不是秒
  }
}
```

**三条铁律**（违反必错）：

1. `gapToSelfM` 是**距离（米）**，v1 没有时间差。**永远不要**除以速度换算成秒。
2. `position === 0` 表示「还不知道」，不是第一名也不是最后一名。
3. `rows` 里**包含你自己**，而 `standings.self` 是同一辆车的另一份拷贝。别重复渲染两次。

---

## 1. 数据怎么到达

宿主通过 `postMessage` 把整份 state 推给主题（不是 WebSocket，主题也不要自己连）：

```js
window.addEventListener('message', (e) => {
  const msg = e.data;
  if (!msg || typeof msg !== 'object') return;
  if (msg.type === 'acc:state') {
    try { render(msg.payload); } catch (err) { console.error(err); }  // 一帧脏数据不能停更
  }
});
```

| 事实 | 值 |
|---|---|
| 推送频率 | 录制中 **30Hz**，待机 **2Hz** |
| 榜单内容实际刷新率 | **≈4Hz**（广播侧节奏，外面套着 30Hz 的信封） |
| 顶层 `payload.status` | `idle` / `waiting` / `streaming` / `error` |
| 有没有 `standings` 这个键 | **永远有**，键不会缺，别写 `if (payload.standings)` 兜底 |

因为内容只有 4Hz，**别拿它做逐帧平滑动画**——位置会一跳一跳。要做平滑，就在 `spline` 上自己插值。

---

## 2. 什么时候有数据（生命周期）

```
status !== 'streaming'  →  standings.available = false，rows = []，reason 说明原因
status === 'streaming'  →  才可能有实时榜单
```

- **非直播态（暂停 / 菜单 / 回放 / 退出）一律给空结构**，不会保留上一场的旧榜单。
  所以「显示占位」是对的，「继续显示冻结的榜单」是 bug。空结构里 `rows` 是 `[]`，不是 `null`。
- 广播服务自己会退避重连（1/2/5/10s），`reason` 会如实变化。**用户什么都不用做**，
  除了 `disabled` / `port-not-configured` 这两种需要改配置。
- 广播挂了**不影响任何其它字段**（`car` / `lap` / `wheels` 照常）。所以榜单 UI 不能是主题的骨架，
  只能是可缺的一块。

---

## 3. 字段全集

### 3.1 顶层（固定 8 个键）

| 字段 | 类型 | 说明 |
|---|---|---|
| `available` | bool | 总开关。`false` 时 `rows` 为空，看 `reason` |
| `reason` | str \| null | 不可用原因枚举，`available=true` 时为 `null`。见 §3.4 |
| `stale` | bool | 整份榜单不可信：**所有车**都超过 5s 没实时更新，或快照本身超过 3s。此时 `rows` **仍然保留** |
| `complete` | bool | 名册（EntryList）是否收齐。`false` = `rows` 可能缺车（有人迟到加入） |
| `source` | str | 固定 `"broadcasting"`，多数据源预留。**不要拿来分支判断** |
| `trackMeters` | number \| null | 赛道长度（米，1 位小数）。为 `null` 时所有人的 `gapToSelfM` 都是 `null` |
| `self` | object \| null | 你自己那一行（按 `carId` 匹配）。见 §3.2 |
| `rows` | array | 全场车辆，**已排序**（见 §3.5） |

### 3.2 `standings.self`

`self` = §3.3 的 20 个字段（完全同构）**再加 2 个**：

| 额外字段 | 类型 | 说明 |
|---|---|---|
| `gapAheadM` | number \| null | 紧贴你**前方**那辆车的距离，**正数**。没有前车 / 算不出为 `null` |
| `gapBehindM` | number \| null | 紧贴你**后方**那辆车的距离，**负数**。没有后车 / 算不出为 `null` |

- `self` 可能是 `null`（刚进赛道、名册还没回来）。此时 `rows` 照出，别人的 `gapToSelfM` **仍然算得出来**。
- 需要「我」的实时信息时，优先用 `payload.car` / `payload.lap` / `payload.position`——
  它们来自共享内存，100Hz、**始终存在**。`self` 只用来补「我在榜单里的姓名 / 车号 / 名次」。
- `self.gapToSelfM` 恒为 `0.0`。

### 3.3 `standings.rows[]`（每车 20 个字段）

| 字段 | 类型 | 单位 / 取值 | 说明 |
|---|---|---|---|
| `carId` | int | | **唯一主键**。跨源 join、DOM key 都用它 |
| `raceNumber` | int | | 车号。**0 = 未知** |
| `name` | str | | 显示名：`FirstName LastName`；缺失时退 shortName；最后可能空串 |
| `team` | str \| null | | 车队名，未填为 `null` |
| `position` | int | 名次 | 官方 P/Q/R 名次。**0 = 未知** |
| `cupPosition` | int | 名次 | 组别名次。0 = 未知 / 无分组 |
| `trackPosition` | int | 名次 | 赛道实际位置。`position` 未知时后端用它排序 |
| `spline` | number \| null | `0..1` | 赛道归一化进度（5 位小数）。**`0` 是起跑线，是合法值**；`null` 才是未知 |
| `laps` | int | 圈 | 已完成圈数 |
| `lastLapMs` | int | ms | 上一圈。**0 = 还没跑完一圈** |
| `bestLapMs` | int | ms | 本场最佳。**0 = 还没有** |
| `currentLapMs` | int | ms | 当前圈已用时 |
| `deltaBestMs` | int \| null | ms | 广播原样给的 delta（ACC 口径：**负 = 更快**）。后端已把「无参考圈」的 `0` 和哨兵值归一成 `null` |
| `location` | str | 枚举 | `track` / `pitlane` / `pit_entry` / `pit_exit` / `garage` / `unknown` |
| `inPit` | bool | | 严格等于 `location === 'pitlane'`；**进出站口和车库不算** |
| `x` | number \| null | m | 世界坐标 x，与 `payload.position.x` **同一坐标系**（1 位小数） |
| `z` | number \| null | m | 世界坐标 z，与 `payload.position.z` **同一坐标系**（1 位小数） |
| `speedKmh` | int \| null | km/h | 车速。**注意：超时后它不会变成 `null`**，仍是最后一次读到的值；判断新鲜度只看 `stale` |
| `gapToSelfM` | number \| null | m | 相对你的距离：**正 = 前方，负 = 后方**。自己那行恒 `0.0`；算不出 `null` |
| `stale` | bool | | 这一辆车超过 5s 没实时更新 |

### 3.4 `reason` 枚举（7 种，文案直接可用）

| 值 | 含义 | 给用户看的提示 |
|---|---|---|
| `disabled` | 配置里关掉了广播 | 「广播榜单未启用」 |
| `port-not-configured` | `broadcasting.json` 的 `updListenerPort` 为 0（**ACC 默认就是 0**） | 「请在 ACC 里开启 UDP 广播端口」 |
| `connecting` | 正在注册 / 等第一个报文 | 「正在连接…」 |
| `timeout` | 注册超时，或 3s 没新报文（正在退避重连） | 「榜单连接中断，重试中」 |
| `no-session` | 游戏没运行 / 不在赛道上（菜单、回放） | 「等待进入赛道」 |
| `no-data` | 还没收到过任何广播数据 | 「暂无对手数据」 |
| `error` | 注册被拒 / 协议异常 | 「榜单数据异常」 |

**未知字符串当作 `no-data` 处理**（后端也会归一，但契约允许将来加值）。

### 3.5 排序规则（不要自己重排名次榜）

后端已按这个顺序排好：

1. `position > 0` 的，按 `position` 升序；
2. `position === 0` 但 `trackPosition > 0` 的，按 `trackPosition` 升序；
3. 都未知的，按 `carId` 升序。

**唯一该重排的场合**：做「相对距离条 / 前后车列表」时，按 `gapToSelfM` 排（同 ACC 的 relative 视图）。

---

## 4. 语义陷阱（每条都踩过）

1. **`gapToSelfM` 不是秒**。v1 明确不提供「+1.2s」。别 `gap / speed` 自己估——误差大到会误导人。
   要显示就写 `12.3m`。
2. **`position === 0` 不是最后一名**。它表示「还不知道」（排位赛刚开始、刚进赛道）。
   直接 `sort((a,b) => a.position - b.position)` 会把 0 排到最前，看起来像「未知车手夺冠」。
3. **`spline === 0` 不是未知**。是起跑线。只有 `null` 才是未知。同理 `x`/`z` 的 `0` 是合法坐标。
4. **`rows` 含自己**。你要么 `rows.filter(r => r.carId !== self.carId)` 再渲染「对手」，
   要么在榜单里给自己那行加高亮。**别两份都画**。
5. **`self` 可能为 `null`**，而 `rows` 不为空。所有读 `self.xxx` 的地方都要兜住。
6. **`self` 里的 `gapAheadM` / `gapBehindM` 只在 `self` 存在时才有这两个键**。
   `rows[]` 里**没有**它们——别去 `row.gapAheadM` 取。
7. **`deltaBestMs === 0` 不存在**。后端把 0 归一成 `null` 了（0 在 ACC 里表示「还没有有效参考圈」）。
   所以「delta 正好是 0」永远显示不出来，这是已知取舍。
8. **单行 `stale` 与整体 `stale` 是两回事**。行级 `stale=true` 表示这辆车超时：
   它的 `spline` / `x` / `z` / `deltaBestMs` 已被置 `null`，
   但 `position` / `laps` / `lastLapMs` / `bestLapMs` / `speedKmh` **仍然保留**（历史结论不该随刷新率消失）。
   UI 上应该给它置灰，而不是删掉这行。
   ⚠️ **`speedKmh` 是唯一不随 `stale` 置空的实时量**——所以不要用 `speedKmh !== null` 判断车是否在动，
   一律看 `stale`。
9. **`inPit` 不覆盖进出站口**。`location` 是 `pit_entry` / `pit_exit` / `garage` 时 `inPit` 是 `false`。
   要「是否在维修区相关状态」就自己判断 `location.startsWith('pit') || location === 'garage'`。
10. **`laps` 是「已完成圈数」**，和 `payload.lap.completed` 同源但属于不同车。
    「正在跑第几圈」= `laps + 1`。
11. **`complete === false` 不代表数据坏了**。只代表名册还没收齐，`rows` 可能少几辆车。
    这时显示「18 辆车」是诚实的，显示「全场 18 辆」是撒谎。
12. **字段可能是 `null`，别用 `isFinite()` 判空**。`Number(null) === 0`，`isFinite(null)` 返回 **true**，
    紧接着 `null.toFixed()` 抛 `TypeError`，整帧渲染中断。用 §6 的 `num()`。
13. **`raceNumber === 0` 是未知**，别显示成「0 号车」。
14. **别把 `carId` 当车号**。`carId` 是内部 ID（可能上千），车号是 `raceNumber`。两者没有关系。

---

## 5. 现成配方

### 5.1 完整的最小渲染器（可直接抄）

```js
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

const fmtLap = (ms) => {
  const v = num(ms);
  if (v === null || v <= 0) return '–';           // 0 = 还没有
  const m = Math.floor(v / 60000);
  const s = ((v % 60000) / 1000).toFixed(3).padStart(6, '0');
  return `${m}:${s}`;                             // 1:23.456
};

const fmtGap = (m) => {
  const v = num(m);
  if (v === null) return '–';
  if (v === 0) return '0.0m';
  return `${v > 0 ? '+' : '−'}${Math.abs(v).toFixed(1)}m`;   // +12.3m / −4.5m
};

function render(payload) {
  const st = payload.standings;
  const board = document.getElementById('board');

  // 1) 不可用：显示占位，并清空旧行（绝不能留着上一场的榜单）
  if (!st || !st.available) {
    board.replaceChildren();
    showReason(st ? st.reason : 'no-data');
    return;
  }
  clearReason();

  // 2) 自己那行单独拎出来，rows 里过滤掉，避免重复
  const meId = st.self ? st.self.carId : null;
  const others = st.rows.filter((r) => r.carId !== meId);

  // 3) 榜单整体 stale → 整块置灰（但数据照显示）
  board.classList.toggle('is-stale', st.stale);
  if (!st.complete) markPartial();      // 「部分车辆」

  renderMine(st.self);
  renderRows(others);
  renderRelative(st);                  // 见 5.2
}
```

**性能**：30Hz × 20~40 行 = 每秒上千次 DOM 写。**按 `carId` 复用节点**，只改 `textContent`
和 `style`，不要 `innerHTML = ''` 重建：

```js
const nodes = new Map();                // carId -> tr

function renderRows(rows) {
  for (const row of rows) {
    let tr = nodes.get(row.carId);
    if (!tr) { tr = createRow(row.carId); nodes.set(row.carId, tr); board.append(tr); }
    updateRow(tr, row);
  }
  // 车走了（退赛 / 名册变化）要把节点删掉，否则会留下幽灵行
  for (const [id, tr] of nodes) {
    if (!rows.some((r) => r.carId === id)) { tr.remove(); nodes.delete(id); }
  }
}
```

### 5.2 「相对距离条」（ACC 的 relative 视图）

```js
function renderRelative(st) {
  const me = st.self;
  if (!me) { hideRelative(); return; }        // 广播里还没自己 → 这一块画不了

  const near = st.rows
    .filter((r) => r.carId !== me.carId && num(r.gapToSelfM) !== null)
    .filter((r) => Math.abs(r.gapToSelfM) <= 300)      // 只看前后 300m
    .sort((a, b) => b.gapToSelfM - a.gapToSelfM)       // 从最前（大正数）到最后（大负数）

  // 前后车提示
  aheadEl.textContent  = me.gapAheadM  === null ? '—' : `${me.gapAheadM.toFixed(1)}m`;
  behindEl.textContent = me.gapBehindM === null ? '—' : `${Math.abs(me.gapBehindM).toFixed(1)}m`;
}
```

### 5.3 一维赛道条 / 小地图

```js
// 一维：直接用 spline（0..1），把每辆车画在一条横线上
const left = (row) => (num(row.spline) === null ? null : row.spline * 100) + '%';

// 二维：x/z 与 payload.position.x/z 同一坐标系，可以和自车点画在同一张图上
if (num(row.x) !== null && num(row.z) !== null) plot(row.x, row.z);
```

⚠️ **`state` 里没有赛道几何**（没有弯道轮廓、没有中心线数据）。所以：
- 能画「散点云」和「相对自车的位置」；
- 想画**带赛道的**小地图，必须主题自己带一张底图，然后做坐标仿射变换——
  而 `x`/`z` 的缩放/旋转参数**不在 state 里**，得写死或自己拟合。**别硬做**，先做散点版。

### 5.4 进站 / 状态列表

```js
const isPitRelated = (loc) => loc === 'garage' || loc.startsWith('pit');   // pitlane / pit_entry / pit_exit
const inPitNow = (row) => row.inPit;                                       // 严格 pitlane
```

### 5.5 空状态（按 `reason` 给不同文案，别只说「无数据」）

```js
const REASON_TEXT = {
  disabled: '广播榜单未启用',
  'port-not-configured': '请在 ACC 中开启 UDP 广播端口',
  connecting: '正在连接…',
  timeout: '榜单连接中断，重试中',
  'no-session': '等待进入赛道',
  'no-data': '暂无对手数据',
  error: '榜单数据异常',
};
const text = REASON_TEXT[st.reason] ?? REASON_TEXT['no-data'];
```

---

## 6. 工具函数

```js
// null 安全取数：null / undefined / NaN / 非 number 一律 null
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

// 毫秒 → 1:23.456；0 / null → '–'
const fmtLap = (ms) => {
  const v = num(ms);
  if (v === null || v <= 0) return '–';
  return `${Math.floor(v / 60000)}:${((v % 60000) / 1000).toFixed(3).padStart(6, '0')}`;
};

// 距离（米）：正 = 前方，负 = 后方
const fmtGap = (m) => {
  const v = num(m);
  if (v === null) return '–';
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(1)}m`;
};

// 圈速差（自己算的，不是官方 gap）：负 = 更快
const fmtDelta = (ms) => {
  const v = num(ms);
  if (v === null) return '–';
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${(Math.abs(v) / 1000).toFixed(3)}`;
};

// 名次：0 不是第一名
const fmtPos = (p) => (num(p) && p > 0 ? String(p) : '–');

// 车号：0 是未知
const fmtNum = (n) => (num(n) && n > 0 ? String(n) : '–');
```

---

## 7. 「用户要 X」→ 用哪些字段

| 用户想要 | 用这些字段 | 备注 |
|---|---|---|
| 名次榜 / 排行榜 | `rows[]` 原顺序 + `position` + `name` + `raceNumber` | 别重排 |
| 前后车距离提示 | `self.gapAheadM` / `self.gapBehindM` | 最省事，直接有 |
| 相对距离条 / 无线电式排序 | `rows[].gapToSelfM` 排序 | 只看 ±300m 更有用 |
| 组别榜 | `cupPosition` | 没有组别**名称**，只有数字（见 §8） |
| 谁的圈速最快 | `rows[].bestLapMs` 排序，`> 0` 才是有效成绩 | 无效圈混在里面，见 §8 |
| 进站状态 | `inPit` + `location` | `inPit` 只管 pitlane |
| 小地图散点 | `x` / `z` | 没有赛道底图 |
| 一维赛道条 | `spline` + `trackMeters` | `spline` 可能是 `null` |
| 套圈判断 | `laps` 差 | `gapToSelfM` 已含跨圈修正 |
| 我第几名 / 总车数 | `self.position` + `rows.length` | 总车数用 `rows.length`，**不要**用 `session.driversTotal`（那是共享内存口径，可能对不上） |
| 单圈时间差（非官方） | `bestLapMs` 两车相减 | 只能算「圈速差」，不是赛道上的实时 gap |

---

## 8. 这个数据源做不到的事（别硬编）

| 做不到 | 原因 | 如果你被要求做 |
|---|---|---|
| **时间差 `+1.2s`** | 广播不提供 gap 时间，v1 契约明确不给 | 显示米；或明确告诉用户「v1 只支持距离」 |
| **对手的油量 / 胎温 / 胎压 / 磨损 / ABS / TC** | ACC 广播协议里根本没有这些字段，只有自车共享内存有 | 直接说做不到，别用自车数据冒充 |
| **对手的扇区分段时间** | 协议字段未映射，填充不稳定 | 用 `lastLapMs` / `bestLapMs` 替代 |
| **对手的圈是否有效 / 是否出场圈** | v1 未暴露（数据源已有，属可扩展项） | 只展示时间，别下「有效」结论 |
| **组别名称（Pro / ProAm / Am…）** | v1 只有 `cupPosition` 数字 | 显示「组内 P2」，别猜组名 |
| **任意两车的距离** | 只有「相对自己」 | 用 `spline` + `trackMeters` 自己算「进度差 × 赛道长度」 |
| **带赛道轮廓的小地图** | `state` 不含赛道几何 | 做散点版，或让主题自带底图并固定变换参数 |
| **对手的逐圈历史** | 需要后端缓存 + 落库，属 `deferred` | 不做 |

如果「任意两车距离」非要算：

```js
// 只对 spline 都已知的车有效；跨起跑线要修正，否则会算出「差一整圈」
function gapBetween(a, b, trackMeters) {
  const t = num(trackMeters);
  if (t === null || num(a.spline) === null || num(b.spline) === null) return null;
  let d = a.spline - b.spline;
  if (d > 0.5) d -= 1;
  else if (d < -0.5) d += 1;
  return (a.laps - b.laps + d) * t;     // 正 = a 在 b 前方
}
```

---

## 9. 自查清单（提交前过一遍）

- [ ] `available === false` 时清空了旧行，并显示了 `reason` 对应的文案
- [ ] `position === 0` 显示成 `–`，没排到第一名
- [ ] `spline === 0` 没被当成未知
- [ ] `rows` 里自己那行没和 `self` 重复渲染
- [ ] `self === null` 时整块不崩（前后车区域隐藏或显示 `–`）
- [ ] `gapToSelfM` 显示单位是「m」，没有除以速度变秒
- [ ] 所有数字都过了 `num()`，没有 `isFinite(null)` 之后 `.toFixed()`
- [ ] `stale` 整块置灰 / 行级 `stale` 置灰，但**没有删行**
- [ ] `complete === false` 时没有说「全场 N 辆」
- [ ] 按 `carId` 复用 DOM 节点，没每帧 `innerHTML = ''`
- [ ] `render()` 包了 `try/catch`
- [ ] 非 `streaming` 时没保留上一场的榜单

---

## 10. 相关文件

| 文件 | 用途 |
|---|---|
| [`theme-state-contract.md`](./theme-state-contract.md) | 完整 state 契约（不止 standings），含稳定性规则 |
| [`theme-authoring.md`](./theme-authoring.md) | 主题怎么写：`theme.json`、`design`、`acc:ready` 握手、调试三档 |
| `python-server/contracts/telemetry-state.json` | **单一真源**：字段表 + `enums` + 完整 `example` |
| `python-server/acc_telemetry/services/standings_model.py` | 读模型（`gapToSelfM` / `gapAheadM` 就是这里算的） |
| `python-server/backend/api/state.py` | `build_state()` 组装入口 |

**调试数据**：`src/telemetry/mock-state.js` 直接取契约的 `example` 当预览数据，
所以 `dev.html`、设置页预览里 `standings` 已经带 2 台车的真样例，**不用开游戏**就能调 UI。
想看真实数据用 `GET /api/state`（和 WebSocket 推的是同一个对象）。
