# ACC 遥测主题制作指南

> 这份文件随主题包一起下发，**但不会被装进主题目录**（导入时只解压运行时资源，
> `.md` 不在白名单里）。它留在这里，给拿到包的玩家或 AI 当说明书用。主题本身照常导入。

---

## 0. 一个主题是什么

就是一个**文件夹**，里面至少要有：

| 文件 | 作用 | 必须 |
|---|---|---|
| `theme.json` | 清单：id、设计尺寸、fit、支持哪些窗口 | ✅ |
| `index.html` | 主题的本体结构 | ✅ |
| `theme.css` | 样式（可以内联进 html，也可以拆出来） | 可选 |
| `theme.js` | 收数据 + 渲染（可以没有，纯 CSS 主题也行） | 可选 |

**没有构建步骤、没有打包**。写（或让 AI 写）完这几个文件，丢进主题目录就能用。

---

## 1. 怎么装到软件里

两种方式，效果一样：

1. **手动**：把整个 `<主题id>` 文件夹复制到
   `%LOCALAPPDATA%\acc-telemetry\themes\<主题id>\`
   （设置页有「打开主题目录」按钮一键打开）。粘贴进去几秒内，设置页就会出现这张卡片
   （热重载，不用重启）。
2. **压缩包导入**：把文件夹打成 `.zip`（根目录放 `theme.json`+`index.html`，或套一层文件夹都行），
   在软件里点导入即可。注意从哪个选择器导入会校验 `modes`：悬浮窗主题不能从主面板导入，
   主面板主题也不能从悬浮窗导入（否则装上了也不会出现在当前列表里）。

注意：

- `theme.json` 里的 `id` **必须等于文件夹名**，否则导入/加载会报错。
- 已存在同名 `id` 会被拒（导入失败），要么先在设置页删掉，要么改 `id`。
- 这个包里如果有多份 `theme.json`，导入会报错 —— **一个 zip 只能装一个主题**。

---

## 2. `theme.json` 字段

```json
{
  "id": "my-hud",
  "name": "my-hud",
  "version": "1.0.0",
  "author": "你或 AI 的名字",
  "desc": "一句话说明这个主题长啥样",
  "design": { "w": 380, "h": 150 },
  "fit": "contain",
  "modes": ["overlay"]
}
```

| 字段 | 说明 |
|---|---|
| `id` | 字母 / 数字 / `-` / `_`，且必须等于文件夹名。**卡片和设置页显示的就是这个「安装包名」** |
| `name` | **请写成和 `id` 完全一样**（两个名字统一），否则同一款主题会出现两种叫法 |
| `version` / `author` / `desc` | 辅助信息，可随便填（卡片上不再展示描述） |
| `design` | 设计基准尺寸（像素） |
| `fit` | `contain`（默认）或 `fluid`，见第 5 节 |
| `modes` | 这个主题给哪种窗口用：`overlay`（悬浮窗）和/或 `monitor`（主面板） |

> **`id` 和 `name` 必须是同一个名字。** 卡片/设置页显示的是安装包名（`id`，也就是文件夹名）；
> `name` 只是辅助字段，两者不一致会让同一款主题出现两种叫法，导入后容易认错包。

一个主题**只能有一种 `fit`**，所以：

- 想当**悬浮窗** HUD → `fit:"contain"`、`modes:["overlay"]`
- 想当**主面板**大屏 → `fit:"fluid"`、`modes:["monitor"]`
- 要两种窗口都用 → 做**两个主题**（各一个 zip），分别声明不同 `modes`。
  不要在一个主题里既写 `contain` 又写 `fluid`。

---

## 3. 数据从哪来（postMessage 协议）

主题运行在一个 `<iframe>` 里，宿主（软件）通过 `postMessage` 把数据推给主题。
主题**不连 WebSocket、不直接读共享内存**，只关心「收到一个对象、画出来」。

**宿主 → 主题** 的消息：

| 消息 | 含义 |
|---|---|
| `{ type: 'acc:init', v, mode, design, fit, scale, box, theme, background }` | 握手：告诉主题当前窗口模式、设计尺寸、缩放方式、黑/白主题、悬浮窗背景 |
| `{ type: 'acc:ui', theme, background }` | 用户切黑白主题 / 悬浮窗透明-不透明时补发，不用重新握手 |
| `{ type: 'acc:state', payload }` | **真正的数据**，录制中 30Hz、待机 2Hz |

**主题 → 宿主** 只需回一条：

| 消息 | 含义 |
|---|---|
| `{ type: 'acc:ready' }` | 主题脚本就绪。**每次收到 `acc:init` 都要回一次**（幂等） |

最小骨架（`theme.js`）：

```js
window.addEventListener('message', function (e) {
  var msg = e.data;
  if (!msg || typeof msg !== 'object') return;
  if (msg.type === 'acc:init') {
    applyUi(msg);
    window.parent.postMessage({ type: 'acc:ready' }, '*');   // 一定回
    return;
  }
  if (msg.type === 'acc:ui') {
    applyUi(msg);                                            // { theme?, background? }
    return;
  }
  if (msg.type === 'acc:state' && msg.payload) {
    try { render(msg.payload); } catch (err) { console.warn('render failed', err); }
  }
});

// 把宿主给的界面状态写到 <html> 的 data-*，CSS 里按需切换
function applyUi(msg) {
  if (msg.theme) document.documentElement.dataset.theme = msg.theme;
  if (msg.background) document.documentElement.dataset.bg = msg.background;
}
```

两个坑（宿主和主题都做了兜底，但你要知道）：

- 主题脚本可能比宿主的监听先跑完 → 首条 `acc:init` 会丢。宿主会每 400ms 重发 `init`
  直到收到 `ready`；所以**对每个 `init` 都回 `ready`** 就行。
- 主题 4 秒没回 `ready` → 宿主显示「主题加载失败 + 重试」，不会白屏。

---

## 4. 你能拿到哪些数据（state 字段全表）

`acc:state` 的 `payload` 就是一个扁平对象，按命名空间分组。**后端只增不删**，
遇到不认识的字段主题必须忽略；`status` 不是 `streaming` 时实时字段保留最后一个值。

### 约定（写渲染前先看）

- **时间一律毫秒**（整数），圈速类无值为 `0`，增量类无值为 `null`。
- **比例一律 0..1**，不给百分比，取整由主题决定。
- 单位：速度 `km/h`、温度 `°C`、胎压 `psi`、油量 `L`、距离/坐标 `m`、角度 `rad`（显示成度自己乘系数）。
- **真机数据里字段可能是 `null`**（ACC 没读到时后端就留 `null`）。详见第 6 节的 `num()` 坑。

### 顶层

| 字段 | 类型 | 说明 |
|---|---|---|
| `v` | int | 契约版本 |
| `seq` | int | 递增序号（丢帧判断） |
| `t` | int(ms) | 采样时刻 |
| `status` | str | `idle` / `waiting` / `streaming` / `error` |

### `session`（场次）

| 字段 | 说明 |
|---|---|
| `session.type` | 练习/排位/正赛…，菜单里可能 `unknown` |
| `session.timeLeftMs` | 计时赛剩余（毫秒） |
| `session.lapsTotal` | 总圈数，0 = 不限 |
| `session.index` | 会话序号 |

### `track`（赛道）

| 字段 | 说明 |
|---|---|
| `track.name` | ACC 内部名（如 `monza`），做键用 |
| `track.title` | 显示名（如 `Monza`） |
| `track.configuration` | 布局，多数空 |
| `track.lengthM` | 赛道长度（米） |
| `track.sectorCount` | 计区段数，通常 3 |

### `car`（车辆）

| 字段 | 单位 | 说明 |
|---|---|---|
| `car.name` | | 车款名 |
| `car.maxRpm` | rpm | 引擎红线 |
| `car.maxFuel` | L | 油箱容量 |
| `car.maxTorqueNm` / `car.maxPowerKw` | | 引擎参数 |
| `car.speed` | km/h | 速度 |
| `car.rpm` | rpm | 当前转速 |
| `car.maxRpmCurrent` | rpm | 画红线用（0 时退回 `maxRpm`） |
| `car.gear` | | **已语义化**：-1=R、0=N、1..n |
| `car.gearText` | | `R`/`N`/`数字` |
| `car.throttle` / `car.brake` | 0..1 | 油门/刹车（不給百分比） |
| `car.steer` | -1..1 | 方向盘，显示时 ×450 得度 |
| `car.clutch` | 0..1 | 0 = 踩到底 |
| `car.fuel` | L | 剩余油量 |
| `car.fuelPerLap` | L/圈 | 游戏估算油耗 |
| `car.fuelLapsLeft` | 圈 | 游戏估算可跑圈数 |
| `car.fuelUsed` | L | 本场累计用油 |
| `car.brakeBias` | 0..1 | 前轴刹车占比 |
| `car.tcLevel` / `car.absLevel` | | 牵引力/ABS 档位，0 = 关 |
| `car.tcActive` / `car.absActive` | 0..1 | 是否正在介入 |
| `car.engineMap` | | 引擎映射 |
| `car.pitLimiter` | bool | 维修区限速 |
| `car.drs` | bool | GT3 恒 false |
| `car.autoShifter` | bool | 自动换挡 |
| `car.aiControlled` | bool | 回放/接管时 true |
| `car.tyreCompound` | str | 轮胎配方（游戏原样字符串） |
| `car.rainTyres` | bool | 是否雨胎 |
| `car.gLong` / `car.gLat` | g | 纵向/横向加速度（已除 9.81） |

### `lap`（圈速）

| 字段 | 单位 | 说明 |
|---|---|---|
| `lap.num` | | 本次监测以来的第几圈（从 1） |
| `lap.completed` | | 已完成圈数 |
| `lap.timeMs` | ms | 本圈已用 |
| `lap.lastMs` | ms | 上一圈，0 = 暂无 |
| `lap.bestMs` | ms | 游戏内本场最佳 |
| `lap.pbMs` | ms | **本工具历史 PB**，0/null = 无参考 |
| `lap.deltaMs` | ms | 对 PB 实时差，**负 = 更快**，无参考为 `null` |
| `lap.estimatedMs` | ms | 游戏估算本圈 |
| `lap.progress` | 0..1 | 圈进度 |
| `lap.sector` | | 当前计区段（1 起） |
| `lap.sectorLastMs` | ms | 上一段用时 |
| `lap.splitMs` | ms | 分段 |
| `lap.valid` | bool | 本圈是否有效 |
| `lap.inPit` / `lap.inPitLane` | bool | 是否在维修区 |
| `lap.position` | | 名次，0 = 无效 |
| `lap.penaltyMs` | ms | 待执行罚时 |

### `position`（位置，做地图用）

| 字段 | 单位 | 说明 |
|---|---|---|
| `position.x` / `position.z` | m | 平面坐标（ACC 的 y 是高度，平面用 x-z） |
| `position.heading` | rad | 车头朝向 |
| `position.distanceM` | m | 本场累计里程 |

### `wheels[]`（四轮，固定 4 项，顺序 FL/FR/RL/RR）

| 字段 | 单位 | 说明 |
|---|---|---|
| `wheels[].corner` | | `FL`/`FR`/`RL`/`RR` |
| `wheels[].pressure` | psi | 胎压 |
| `wheels[].temp` | °C | 胎芯温度 |
| `wheels[].brakeTemp` | °C | 刹车盘温度 |
| `wheels[].slip` | 0..1 | 滑移 |
| `wheels[].slipRatio` | | 纵向滑移率 |
| `wheels[].slipAngle` | rad | 横向滑移角 |
| `wheels[].suspensionTravel` | m | 悬挂行程 |
| `wheels[].wear` | 0..1 | 磨损 |

### `env`（环境）

| 字段 | 单位 | 说明 |
|---|---|---|
| `env.airTemp` / `env.roadTemp` | °C | 气温/路温 |
| `env.airDensity` | kg/m³ | 空气密度 |
| `env.rainIntensity` | 0..5 | 0 无雨 … 5 暴雨 |
| `env.rainIn10min` / `env.rainIn30min` | | 降雨预报 |
| `env.grip` | 0..5 | 赛道抓地（0 green … 5 wet） |
| `env.surfaceGrip` | 0..1 | 路面抓地 |
| `env.windSpeed` | m/s | 风速 |
| `env.windDirection` | rad | 风向 |
| `env.timeOfDay` | s | 一天中的秒数（0 = 午夜） |

### `flags`（旗号）

| 字段 | 说明 |
|---|---|
| `flags.yellow` / `white` / `green` / `chequered` / `red` | 0/1 |
| `flags.trackStatus` | 原文，如 `GREEN` |
| `flags.mandatoryPitMissing` | 还差几次强制进站 |

> **v1 暂不暴露**（以后可能加）：对手车坐标/圈速、分区黄旗、车辆损伤、每段独立成绩、FFB 信号。

---

## 5. overlay 与 monitor 的写法差异

### `fit: "contain"`（悬浮窗，固定设计尺寸）

宿主按 `design` 把 iframe 渲染成固定像素，再用 `transform: scale(k)` 等比缩放并居中。
**主题里所有尺寸都按 `design.w × design.h` 写死，不要用 `vw/vh`，也不要做响应式。**
留白是透明的（悬浮窗没有黑边）。例：design 380×150 就当画布是 380×150 来写。

### `fit: "fluid"`（主面板，铺满数据区）

iframe 直接 `width:100%; height:100%`，宿主**不做整体缩放**，主题的视口就是容器尺寸。
最省事的自适应手法是「根字号跟随视口」：

```css
html { font-size: clamp(10px, calc(1.5vh + 0.4vw), 20px); }
```

然后其余尺寸**全部写 `rem`**，用 `%` / `flex` / `grid` 排版，像写普通网页一样。
不要假设固定宽度，也不要用 `height:100%` 叠在没定高的父级上 —— 主面板会被拉到任意比例。

### 字体：抄软件同一套栈

主题是独立 iframe，拿不到宿主的 CSS 变量，所以字体要自己声明一份 —— 但**请照抄软件外壳用的这两套栈**，
否则主题会像「贴上去的」，和软件本身不搭：

```css
:root {
  /* 标题 / 标签 / 单位：Windows 上通常落到 Bahnschrift（DIN 风格） */
  --font-display: 'Chakra Petch', 'Bahnschrift', 'DIN Alternate', 'Microsoft YaHei', sans-serif;
  /* 数字：等宽 + 表格数字，防止数字变化时宽度抖动 */
  --font-mono: 'JetBrains Mono', 'Cascadia Code', Consolas, 'Microsoft YaHei', monospace;
}
```

分工很明确：

- **数字一律用 `--font-mono`，并加 `font-variant-numeric: tabular-nums`**。
- **标签用 `--font-display`**（`BRAKE` / `THROTTLE` / `LAP` 这类大写短词），配 `letter-spacing: .14em ~ .2em`。
- 这和软件主面板的 `.big-num` / `.unit` / `.gear-num` 是同一套观感（见 `src/style.css`）。

字体栈里的中文项别删：`--font-mono` 里的 `'Microsoft YaHei'` 是给「L/圈」这类混排兜底的。

---

## 6. 渲染的几个坑（照抄能少踩 90% 的雷）

**① `isFinite(null) === true` 陷阱。** 真机字段可能是 `null`，但 `Number(null) === 0`、
`isFinite(null)` 为真，紧接着 `x.toFixed()` 会抛 `TypeError` 让整帧渲染中断
（表现：速度一直是 0、别的却正常）。统一用这个护盾：

```js
function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }
// 用法：var spd = num(car.speed); el.textContent = spd === null ? '–' : Math.round(spd);
```

**② 单帧脏数据别让整个面板停更。** 用 `try/catch` 包住 `render()`，出错只记一条日志，下一帧继续。

**③ `render` 外再包一层 try。** 主题 iframe 里任何未捕获异常都只影响这一帧。

**④ 黑/白主题靠 `acc:init` / `acc:ui` 里的 `theme` 字段切换：**

```js
if (msg.theme) document.documentElement.dataset.theme = msg.theme;
```

在 CSS 里用 `html[data-theme="light"] { ... }` 覆盖变量即可。

**⑤ 悬浮窗背景可能被用户切成「不透明」：**

设置里每个悬浮窗都有「背景：透明 / 不透明」。切成不透明时，宿主会在 iframe 后面
铺一层实心底板，并把 `background: "opaque"` 通过 `acc:init` / `acc:ui` 告诉主题。
主题默认保持透明即可；如果自带半透明卡片，加一条规则让它在不透明模式下变实心，
免得两层叠出色差：

```css
html[data-bg="opaque"] .card { background: #0b0e13; }
```

值只有 `transparent` / `opaque` 两种；`data-bg` 由 `applyUi(msg)` 写入。

**⑥ 非 `streaming` 时要显示占位，不要渲染旧值：**

后端在待机 / 退出游戏后仍会按 2Hz 推 `acc:state` 心跳，并**保留上一场最后一次有效值**，
只把 `status` 改成 `idle` / `waiting` / `error`。照单渲染的话，没开游戏也会看到速度、
圈速、踏板停在上一场的位置，像还在采集。统一这样处理：

```js
var live = st.status === 'streaming';
var car = live ? (st.car || {}) : null;
$('speed').textContent = car ? Math.round(car.speed || 0) : '--';
```

---

## 7. 不用开游戏就能调试

- **双击 `index.html`**（或 `file://` 打开）：没有宿主时 2 秒后自动跑演示数据，改 CSS 立刻见效。
- **主题调试页 `dev.html`**（开发者 `npm run dev` 后开 `http://localhost:1420/dev.html`）：
  滑杆手动控数据、场景预设、看 state JSON，不用连游戏。
- **热重载**：改主题目录里的任意文件（css/json/html/js），悬浮窗和设置页预览一秒内重建。
- **设置页预览**也是演示数据，不需要后端。

---

## 8. 安全提示（重要）

主题 iframe 用了 `sandbox="allow-scripts"`，**但没有 `allow-same-origin`**，
所以主题拿不到宿主的 DOM / localStorage / cookie。

**但 Tauri 的 IPC 注入挡不住**：WebView2 会把 `window.__TAURI_INTERNALS__` 注入到所有 frame，
包括主题 iframe。也就是说「把不可信主题放进 sandbox」并不能构成隔离 —— 真正的防线是
**按窗口收紧的 ACL**（命令权限）。

所以：**只预览 / 安装你信任的主题**。软件设置页的预览窗口现在已经是独立的 WebView
（只给 `listen` 权限、不给任何应用命令），比早期安全；但从网上下载的陌生主题，
仍建议先在隔离环境看一眼代码再导入。

---

## 9. 想照着改？从这里起步

- 本包（悬浮窗模板）：改 `theme.css` 调样式、`theme.js` 里 `render()` 决定显示哪些字段。
- 主面板模板（`theme-template-monitor`）：`fit:"fluid"`，学它的根字号 + `rem` 自适应套路。
- 字段不够用？看第 4 节，把想要的字段加进 `render()` 即可；后端只增不删，新字段会自动出现。
