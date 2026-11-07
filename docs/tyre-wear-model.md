# 轮胎磨损估算模型说明

> 面向：车辆动力学 / 轮胎模型方向的评审者，以及后续调整参数的维护者。
> 代码：[`python-server/acc_telemetry/services/tyre_wear.py`](../python-server/acc_telemetry/services/tyre_wear.py)
> 单测：[`python-server/tests/test_tyre_wear.py`](../python-server/tests/test_tyre_wear.py)
> 状态：**估算模型**，不是游戏内部真值。界面上标「估算」。
> 最近修改：2026-11-07（修正标定量纲，加入单圈自校准）

---

## 1. 为什么需要估算

ACC 共享内存 v1.8.12 把 `physics.tyreWear` 标红为 **「Field is not used by ACC」**，
实车/实测里通常恒为 0。因此拿不到逐轮剩余胎面深度，只能从**可用字段**反推：

- `physics.wheelSlip`：综合滑移（量纲不透明，见 §10）
- `physics.slipRatio`：纵向滑移率
- `physics.slipAngle`：横向滑移角（文档标注 rad）
- `physics.tyreCoreTemp`：胎芯温度 (°C)
- `physics.wheelPressure`：胎压 (psi)
- `physics.speedKmh`：车速 (km/h)
- `graphics.rainTyres`：是否雨胎

模型的目标不是复刻 ACC 内部磨损曲线，而是给出**寿命量级正确、前后/左右有差异、
每圈损耗可读**的估算值。

---

## 2. 代码位置与数据流

```
ACC 共享内存
  └─ acc_provider.read()                 # 每帧组 Snapshot（providers/base.py）
       └─ collector/reader.py            # 100 / 50 / 20 Hz 可调；只喂直播帧
            ├─ estimator.update(raw)     # 本模型：逐帧积分
            └─ bus.runtime["tyre_wear"] = estimator.snapshot()
                 └─ state.py::_tyre()    # 合并进主题状态 wheels[]
                      └─ 主题 iframe      # public/themes/tyre-wear/theme.js
```

关键点：

- 只有 `status == 2`（直播）帧参与积分；暂停 / 回放 / 菜单不推进磨损。
- 估算器是纯内存对象，不读写数据库；进程重启后从头累计。
- 状态通过 WebSocket 以 30Hz 推给主题，但磨损积分按采集频率（默认 100Hz）进行。

---

## 3. 逐帧积分模型

### 3.1 采样门控

对相邻两帧：

```
dt = t_mono(n) - t_mono(n-1)
若 0 < dt <= MAX_DT (0.5 s) 且 speed >= MIN_SPEED_KMH (3 km/h) 才积分
```

`dt > 0.5 s` 视为暂停 / 卡顿 / 回放，只更新基线、不累加，避免一帧补上大段能量。

### 3.2 垂直载荷（纵向转移）

用**速度差分**求纵向加速度，而不是 `accG`/`heading`（后者的符号约定在历史实现中
不可靠，见 `ENGINEERING-NOTES.md` 第 4 节）：

```
a_x      = (v - v_prev) / dt                    [m/s²]
transfer = clamp(0.18 * a_x / 9.81, -0.35, 0.35)
front    = 0.5 * (1 - transfer)
rear     = 0.5 * (1 + transfer)
load_FL = load_FR = front / 2
load_RL = load_RR = rear / 2
```

- `0.18 ≈ h_cg / wheelbase`，GT3 量级；`±0.35` 防止刹车/起步瞬间的差分尖峰。
- 只有**纵向**转移；横向载荷转移没有单独建模，左右差异交给各轮自身的滑移量。

### 3.3 综合滑移

```
derived = hypot(slipRatio, tan(clamp(slipAngle, -1.2, 1.2)))
slip    = max( clamp(|wheelSlip|, 0, 1.5), clamp(derived, 0, 1.5) )
```

- 两个来源取大：`wheelSlip` 是综合量，`slipRatio/slipAngle` 是分量推导。
- `slipAngle` 按 **rad** 处理；`tan` 前夹到 ±1.2 rad（≈69°），防止异常值爆炸。
- 结果夹到 `[0, 1.5]`，抑制单帧锁死/打转的尖峰。

### 3.4 滚动基线

纯直道也有缓慢磨损，加一个很小的速度相关项：

```
slide = slip + 0.005 * min(1, v / 50)        # v: m/s
```

车速 50 m/s 以上时基线恒为 0.005，约为典型滑移量的几十分之一。

### 3.5 温度 / 胎压 / 雨胎修正

胎芯温度 `T` (°C)：

| 区间 | 系数 |
|---|---|
| `T ≤ 0`（无数据） | 1.00 |
| `T < 50` | 0.80 |
| `50 ≤ T < 80` | `0.80 + 0.20·(T-50)/30` |
| `80 ≤ T ≤ 100` | 1.00 |
| `100 < T ≤ 115` | `1.00 + 0.02·(T-100)` |
| `T > 115` | `1.30 + 0.02·min(T-115, 25)` |

胎压 `P` (psi)：

```
f_pressure = 1 + min(|P - 27.5|, 8) * 0.01
```

雨胎：`f_compound = 0.90`（干地条件下雨胎磨得更快，这里做保守折减；仅按标记判断，
未区分干/湿路面）。

### 3.6 单轮摩擦功率与能量

```
power_i = load_i * v * slide_i * f_temp(T_i) * f_pressure(P_i) * f_compound
E_i    += power_i * dt
```

`E_i` 是累计「滑移耗散能量」，单位由 `slide` 的量纲决定（无物理单位，故称归一化）。
**注意**：这里没有乘轮上法向力（N），`load` 是归一化份额（四轮之和 = 1），
所以 `power` 不是瓦特；它只是**相对能量**，绝对值由 §4 的标定吸收。

---

## 4. 磨损换算与单圈自校准

### 4.1 主公式

```
wear_i = min(0.999, E_i / FULL_ENERGY)
```

`FULL_ENERGY` 不是物理常数，而是**标定尺度**：`E = FULL_ENERGY` 时视为磨完。

### 4.2 为什么必须自校准

旧实现把 `FULL_ENERGY` 写死为 `4000`，隐含假设 `wheelSlip ≈ 0.08` 量级。
实车上该字段量级大得多，结果正常驾驶每圈累积约 20%，4~5 圈就显示 100%。

由于 `wheelSlip` 的绝对量纲无法从官方文档确认（官方只说 "Tyre slip"），
模型改为**每完成一圈，用该圈实测能量反推 `FULL_ENERGY`**：

```
deltas[i]      = (E_i - E_lap_start_i) / lap_count
observed       = median(deltas)                     # 四轮中位数，抗单轮打滑
lap_energy_ref = observed                           # 首个有效圈
               = lap_energy_ref + 0.25 * (observed - lap_energy_ref)   # 之后 EMA
FULL_ENERGY    = lap_energy_ref / TARGET_WEAR_PER_LAP   # 0.025
```

含义：**正常驾驶的一整圈 ≈ 2.5% 磨损**（约 40 圈用完一套胎）。之后跑得越狠，
本圈 `deltas` 相对参考值越大，磨损与每圈速率就越高；反之更低。

- `CALIBRATION_GAIN = 0.25`：既能跟上赛道/驾驶风格变化，又不会被单圈失误带偏。
- 四轮取中位数而不是均值，避免某个轮子锁死/打滑主导标定。
- `observed < MIN_LAP_ENERGY (1.0)` 时跳过，防止停停走走的圈污染标定。

### 4.3 半圈保护

应用可能在中途启动，或刚换胎：

- 首帧 `normalizedCarPosition > 0.10` → 认为从半圈接入；
- 换胎 / 圈数回退 / session 切换时同样标记为「半圈」；
- 这样的第一圈**只更新 `lap_energy` 基线，不参与校准，也不产生 `ratePerLap`**，
  等下一整圈再开始校准。

另外，若 `completedLaps` 一次前进多圈，单圈能量会除以 `lap_count`。

### 4.4 每圈速率与剩余圈数

```
ratePerLap = min(MAX_WEAR_PER_LAP, delta / FULL_ENERGY)     # 上限 12%/圈
lapsLeft   = (1 - wear) / ratePerLap                        # 仅 rate > 1e-6 时
```

`MAX_WEAR_PER_LAP = 0.12` 防止一次冲出赛道 / 长时间打滑把「剩余圈数」瞬间打到 0。
主题侧的「剩余 N 圈」取四轮中**最小**的 `lapsLeft`。

### 4.5 复位条件

以下任一发生即清零全部积分、速率和标定参考：

- `completedLaps` 回退（重开比赛）；
- `currentTyreSet` 变化且新旧都非 0（换胎）；
- `sessionIndex` 变化（练习 → 排位 → 正赛）；
- 游戏退出 / 共享内存关闭（reader 调用 `reset()`）。

### 4.6 原始 `tyreWear` 的采用规则

虽然官方标 unused，但偶发情况下该字段可能真的在变化。只有同时满足：

1. 本 stint 内 `raw_last - raw_first ≥ 0.01`；
2. `raw_last` 不低于本 stint 最大值 0.002（单调、非重置）；
3. `0 ≤ raw_last ≤ 1`

才用原始值覆盖估算值，输出 `source = "shared"`；否则一律 `estimated`。

---

## 5. 输出契约

`state.wheels[]` 中与本模型相关的字段（完整定义见 `theme-state-contract.md`）：

| 字段 | 单位 | 说明 |
|---|---|---|
| `wear` | 0..1 | 原始 `physics.tyreWear`，通常恒 0，仅调试 |
| `wearEst` | 0..1 | 本模型估算磨损，0 = 新胎 |
| `wearRatePerLap` | 0..1/圈 | 上一圈磨损增量；首圈 / 半圈为 `null` |
| `wearLapsLeft` | 圈 | `(1 - wearEst) / wearRatePerLap`，无速率为 `null` |
| `wearSource` | enum | `estimated` / `shared` / `none` |

`TyreWearEstimator.snapshot()` 还会返回整体 `source` 和四轮中最小的 `lapsLeft`，
由 `state.py` 合并。

---

## 6. 参数速查

| 常量 | 当前值 | 作用 | 调整影响 |
|---|---|---|---|
| `FULL_ENERGY` | `40000` | 首圈校准前的兜底尺度 | 只影响第一圈显示 |
| `TARGET_WEAR_PER_LAP` | `0.025` | 自校准目标：正常一圈的磨损 | **最主要的寿命旋钮**；0.02→约 50 圈，0.04→约 25 圈 |
| `CALIBRATION_GAIN` | `0.25` | 单圈能量 EMA 系数 | 越大越跟手，越小越抗异常圈 |
| `MIN_LAP_ENERGY` | `1.0` | 参与校准的最小单圈能量 | 过滤停停走走 |
| `MAX_WEAR_PER_LAP` | `0.12` | 单圈速率上限 | 防止剩余圈数瞬间归零 |
| `PARTIAL_LAP_PROGRESS` | `0.10` | 半圈接入判定阈值 | 影响首圈是否校准 |
| `MAX_DT` | `0.5 s` | 积分最大帧间隔 | 抗暂停/卡顿 |
| `MIN_SPEED_KMH` | `3.0` | 最低积分车速 | 抗停稳时的噪声 |
| `SHARED_MIN_DELTA` | `0.01` | 采用原始值的最小变化 | 抗 `tyreWear` 噪声 |
| `SHARED_MAX_BACKOFF` | `0.002` | 原始值允许的回差 | 判断单调性 |

载荷转移系数 `0.18`、滚阻项 `0.005`、温度/胎压系数都是**硬编码在函数里**的，
调整位置见 `_integrate`、`_temp_factor`、`_pressure_factor`。

---

## 7. 验证现状

单测（`python-server/tests/test_tyre_wear.py`，7 条）：

- 磨损随帧累积；
- 换胎后清零；
- 首次完整圈后 `ratePerLap` 落在 1.5%~4%（≈目标 2.5%）；
- 半圈接入时首圈不校准、下一整圈才校准；
- 原始 `tyreWear` 单调增长时采用，恒定 0 时忽略；
- 无帧时输出 `none`。

一个模拟（110 s/圈、`slip=0.92`，对应当前实车能量量级）的结果：

| 圈 | 磨损 | 每圈速率 | 剩余 |
|---|---|---|---|
| 1 | 2.5% | 2.50% | 39 圈 |
| 3 | 7.5% | 2.50% | 37 圈 |
| 5 | 12.5% | 2.50% | 35 圈 |

### 现场数据缺口

本地历史库 `samples` 表**没有保存 `wheelSlip / slipRatio / slipAngle`**，
所以无法用已录圈次离线验证滑移量级与磨损曲线。若要做正经标定，建议：

1. 在 `samples` 增加四轮滑移字段（或单独的 `tyre_samples` 表）；
2. 同时记录 `tyreWear`（若某版本可用）、胎温、胎压；
3. 用同一套胎的多圈数据回归 `TARGET_WEAR_PER_LAP` 与载荷/滑移指数。

---

## 8. 已知局限（请评审者重点看）

1. **量纲不透明**：`wheelSlip` 的绝对量纲未确认；模型靠自校准吸收量级差异，
   因此「每圈 2.5%」是**约定的目标值**，不是从物理第一性原理推出的真值。
2. **自校准会归一化驾驶风格**：参考值取当前驾驶的单圈能量，长期平均的每圈磨损
   会被拉回目标附近；风格差异主要体现在相对参考的短期波动，而不是绝对寿命。
3. **能量模型是简化的**：`load` 为归一化份额，未乘法向力；无空气下压力、无横向载荷转移、
   无轮速/滑移率耦合、无胎面接地压力分布；温度只影响系数，不建模热循环/热降解。
4. **雨胎判断只看标记**：未区分干/湿路面，雨地实际磨损会明显不同。
5. **中途接入**：估算从收到第一帧开始，不包含之前的磨损；半圈保护只能避免标定被污染，
   无法还原真实起始胎面深度。
6. **单圈平均的局限**：`ratePerLap` 是一圈平均，不能反映圈内某段（如长弯）的瞬时磨损。
7. **切弯/冲线/进站**：`completedLaps` 的边界错位、进站停车、出站圈都会影响单圈能量口径，
   目前的半圈保护只覆盖「接入/换胎」，不覆盖出站圈。
8. **`slipAngle` 单位**：按官方文档的 rad 处理；若 ACC 实际返回度，`tan` 分支需要重标。

---

## 9. 建议评审的问题

1. 是否有 ACC 社区/官方资料能确认 `wheelSlip` 的归一化方式与典型量级？
2. GT3 干胎在 Monza / Spa 这类赛道，合理的「每圈磨损百分比」与寿命目标是多少？
   2.5%/圈（≈40 圈）是否偏保守/偏乐观？
3. 磨损更接近 ∝ 滑移**能量**（本模型），还是 ∝ 滑移**功** / 温升 / 接地压力积分？
   是否该换成 `load(N) × slip × v` 的物理量？
4. 是否需要把横向载荷转移、下压力、路面温度纳入模型？投入产出比如何？
5. 雨胎 / 干胎、不同配方是否应各有一套目标磨损率？
6. 自校准目标应该固定，还是按赛道长度/弯数归一（如「每公里磨损」）？

---

## 10. 变更记录

- **2026-11-07**：修正标定量纲（旧版 `FULL_ENERGY=4000` 导致 4~5 圈磨光），
  新增单圈能量自校准与半圈保护，单圈速率上限 12%。见 `CHANGELOG.md`。
