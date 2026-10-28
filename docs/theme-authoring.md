# 写一个悬浮窗主题

这份文档面向「想让悬浮窗长成我想要的样子」的人。不需要改应用代码 —— 主题是一个独立目录，
放进用户主题文件夹，在设置页点「使用」即可。

想先看效果？把 [`docs/theme-template/`](./theme-template/) 整个目录拷到
`%LOCALAPPDATA%\acc-telemetry\themes\my-hud\`，改 `theme.json` 的 `id`，然后在设置页刷新。
目录里的四个文件就是最小可运行主题。

---

## 一、目录结构

```
%LOCALAPPDATA%\acc-telemetry\themes\
└── my-hud\                 ← 目录名必须等于 theme.json 里的 id
    ├── theme.json          元数据（必需）
    ├── index.html          入口（必需）
    ├── theme.css           样式（可选，随便拆几个文件都行）
    └── theme.js            脚本（可选）
```

只允许 `[A-Za-z0-9_-]` 作目录名；中文目录名会被应用直接忽略（加载链接里带不了）。

## 二、`theme.json`

```json
{
  "id": "my-hud",
  "name": "我的 HUD",
  "version": "0.1.0",
  "author": "你",
  "desc": "一句话说明",
  "design": { "w": 420, "h": 120 },
  "fit": "contain",
  "modes": ["overlay"]
}
```

| 字段 | 必需 | 说明 |
|---|---|---|
| `id` | ✅ | 必须与目录名一致，`[A-Za-z0-9_-]` |
| `design` | ✅ | **设计基准尺寸**（整数 px，80~8192）。见第三节 |
| `modes` | ✅ | 这个主题给哪些窗口用：`overlay`（悬浮窗）/ `monitor`（主面板） |
| `name` | | 显示名，缺省用 id |
| `version` / `author` / `desc` | | 只用于展示 |
| `fit` | | `contain`（默认，等比缩放）或 `fluid`（流式铺满，主面板用）。见第三节末尾 |

写错了不会崩：设置页里会标红并写明原因，悬浮窗不会加载它。

## 三、`design` 怎么定

### `fit: "contain"`（悬浮窗）

主题内部**永远按设计像素写死数字**，宿主负责把整块缩放到实际窗口。所以：

1. 先在纸上/设计工具里定一个尺寸，比如「速度 + 档位 + delta 一行放下」→ 420×120。
2. 按这个尺寸写 CSS（`font-size: 52px` 就是 52px，别写 vw/rem/媒体查询）。
3. 悬浮窗会按 `contain` 缩放：窗口比例和 `design` 不一致时留白是**透明的**，看不出黑边。

反过来，你也可以先拉窗口再定 design：点悬浮窗的「调整悬浮窗」按钮（或 `Ctrl+Shift+L`）进编辑模式，
把窗口拉到满意的大小，工具条上会实时显示 `design = 366×136` —— **这个数直接抄进 `theme.json` 就行**
（它已经减掉了 14px 的内边距和边框）。

### `fit: "fluid"`（主面板）

`fluid` 不缩放整块：iframe 直接铺满数据区，**你的视口就是容器尺寸**，所以必须自己响应式。
`design` 这时只当「预览时的宽高比」用（设置页缩略图按它撑开），不参与缩放。

最省事的写法是让根字号跟随视口，其余全用 `rem`：

```css
html { font-size: clamp(10px, calc(1.5vh + 0.4vw), 20px); }  /* 534px 高 ≈ 13px，1080p 顶到 20px */
.card { padding: 0.8rem; }
.big  { font-size: 3.4rem; }
```

布局用 `flex` / `grid` + `%`，不要假设固定宽度；也不要让 `height:100%` 叠在没有确定高度的父级上。
可直接参考 `public/themes/monitor-grid/`（1200×640 的 2 列仪表盘，`modes:["monitor"]`、`fit:"fluid"`）。

## 四、收数据（三段消息）

宿主用 `postMessage` 和主题通信，没有别的接口：

```js
// ① 告诉宿主「我好了」。宿主收到后才开始推数据，否则首帧会丢。
window.parent.postMessage({ type: 'acc:ready' }, '*');

// ② 宿主回握手。注意：每次收到 init 都要再回一条 ready
window.addEventListener('message', (e) => {
  const msg = e.data;
  if (!msg || typeof msg !== 'object') return;

  if (msg.type === 'acc:init') {
    // { v, mode, design: {w,h}, scale }
    window.parent.postMessage({ type: 'acc:ready' }, '*');
    return;
  }

  if (msg.type === 'acc:state') {
    render(msg.payload);          // 数据帧
  }
});
```

- `acc:init` 会**重复到达**（宿主在 iframe 加载后每 400ms 重发一次，直到收到 ready）。这是故意设计的：
  主题脚本常常比宿主的监听先跑完，第一条 ready 会丢。所以**别用「只发一次」的开关**。
- `acc:state` 的频率：录制中 30Hz，待机 2Hz。
- 数据里带 `seq`，同值代表同一帧，可以凭它跳过重复渲染。

### 数据字段

完整字段表在 [`theme-state-contract.md`](./theme-state-contract.md)，也可以直接看
`python-server/contracts/telemetry-state.json`（单一真源，含 `enums` 和一份完整示例）。

最常用的：

| 路径 | 单位/取值 | 说明 |
|---|---|---|
| `status` | `idle`/`streaming`/`waiting`/`error` | 是否在收数据 |
| `car.speed` | km/h | |
| `car.rpm` / `car.maxRpm` | rpm | 换挡灯用后者做比例 |
| `car.gear` | -1=R, 0=N, 1..n | **语义化**，不要自己 −1 |
| `car.throttle` / `car.brake` | 0..1 | 不是 0..100 |
| `car.steer` | -1..1 | |
| `car.gLat` / `car.gLong` | g | |
| `car.fuel` / `car.maxFuel` | L | |
| `lap.timeMs` | ms | 本圈已跑时间 |
| `lap.lastMs` / `lap.bestMs` / `lap.pbMs` | ms | 上圈 / 本次最佳 / 历史最佳 |
| `lap.deltaMs` | ms | **负 = 领先**，后端算好，别自己减 |
| `lap.num` / `lap.completed` | | 圈序号 / 已完成 |
| `lap.inPit` / `lap.valid` | bool | |
| `track.title` / `track.lengthM` | | 赛道名 / 长度 |
| `wheels[]` | 4 个元素 | 胎温、胎压、磨损、刹车温度 |
| `session.type` | `unknown`/`practice`/`qualify`/`race` | |

字段缺失时显示 `—` 或 `0` 都可以，但**不要假设它一定存在**（不同车/赛道拿得到的字段不同）。

## 五、调试（三档，从快到慢）

**① 双击 `index.html`** —— 不用开任何东西。主题自己检测到没有宿主，会跑内置演示数据。
改样式 → 刷新浏览器，最快的一档。

**② 主题调试页** —— `npm run dev` 后开 `http://localhost:1420/dev.html`。
把主题放进 iframe，用滑杆定格任意数值（0 速、6 档、delta ±3s、进站…），
可切 1:1 / 适应、三种底色（游戏画面 / 纯黑 / 透明格），还能展开看完整 state JSON。
**这一档最有用**：能确认极端值下排版不炸。

**③ 设置页预览 / 真悬浮窗** —— 主题放进用户目录后，设置页有缩略图和放大预览；
点「使用」后悬浮窗就换成它。此时可以边改文件边看效果：

> 改了用户主题目录里的文件（css / html / js / theme.json），**悬浮窗和预览会在一秒内自动重建**，
> 不用重启、不用点重试。改了 `design` 也会连带把窗口尺寸调过去。

内置主题（`public/themes/`）没有热重载 —— 它们编译进了二进制，改完要重新 `npm run build`。

## 六、必须知道的几条

1. **`contain` 模式按设计像素排版**。不写媒体查询，不写 `vw/vh`，宿主负责缩放。
   `fluid` 模式反过来：必须自己响应式（见第三节）。
2. **`html, body { height: 100% }` 一定要写**。漏了的话 `.card{height:100%}` 只拿到内容高度，
   卡片会缩在顶部，看起来像「垂直居中坏了」。
3. **背景保持透明**（`background: transparent`）。悬浮窗是透明窗口，画不透明底色会挡住游戏。
   主面板主题可以画底色（它占满数据区），但更推荐面板色 + 透明，让黑白主题都好看。
4. **不要连 WebSocket，也不要自己算 delta**。数据只能从 `acc:state` 来，别的来源都会和主面板对不上。
5. **不要读本地文件 / 发网络请求**。主题跑在 sandbox iframe 里，读不到宿主的存储，
   也不应该有能力做别的事 —— 需要新数据请提需求加进契约。
6. **字体要自带或用系统字体**。外部 CDN 在离线时加载失败，整块会掉字体。
7. **别做逐帧动画**。30Hz 数据已经够密，再叠 CSS 动画会让 HUD 在游戏里更占 GPU。
8. **字段可能是 `null`，别用 `isFinite()` 判空**。ACC 没读到的量后端留 `null`：
   `Number(null) === 0`，所以 `isFinite(null)` 返回 **true**，紧接着 `null.toFixed()` 抛 `TypeError`，
   **整帧渲染中断**——表现是「速度一直是 0，胎温却正常」这种半截画面。写个 `num()`：

   ```js
   const num = (v) => (typeof v === 'number' && isFinite(v) ? v : null);
   el.textContent = num(w.pressure) === null ? '–' : num(w.pressure).toFixed(1);
   ```

   另外把 `render()` 包进 `try/catch`，一帧脏数据不该让面板停更。

## 七、常见症状对照

| 症状 | 原因 |
|---|---|
| 设置页里标红「theme.json 的 id 与目录名不一致」 | 改了目录名没改 `id`，或反过来 |
| 主题显示「加载失败」+ 重试按钮 | 4 秒内没回 `acc:ready`：`theme.js` 报错了，或路径写错（用相对路径 `./theme.css`） |
| 一片空白但没报错 | 忘了发 `acc:ready`，宿主以为主题没起来，一直没推数据 |
| 卡片缩在左上角/顶部 | 漏了 `html, body { height: 100% }` |
| 数字不刷新 | 只处理了 `acc:state`，但没在 `acc:init` 时回 `acc:ready`（也检查一下是不是被自己的一次性 flag 挡住） |
| 缩放后糊 | 主题按小尺寸设计、被放大很多倍。把 `design` 定得接近实际窗口大小 |
| 窗口里有黑边 | 那是留白处不透明。检查 `body`/`.card` 是否画了底色 |
| **只有一半字段在动，另一半永远是 0 / `--`** | 渲染函数中途抛异常了。最常见的是 `isFinite(null)` 之后调 `toFixed()`（见第六节第 8 条）；用 `try/catch` 把 `render` 包起来就能看到具体错误 |
| 主面板主题一加载就退回内置面板 | 4 秒内没 `acc:ready`，主题脚本报错。看控制台有没有 `[theme] render failed` |
| `fluid` 主题字体很小 / 空得厉害 | 没用根字号 / 没铺满。把 `html{font-size:clamp(...)}` 加上，其余尺寸写 `rem` |

## 八、安全边界

主题是**本地代码**，能做的事和任何本地脚本一样。应用里有两道限制，写主题的人应该知道：

- 悬浮窗这个窗口只被授予了 `set_overlay_size` / `set_overlay_locked` 两条命令，
  主题即使拿到 Tauri IPC 也动不了后端和主窗口。
- **设置页预览**的 iframe 跑在主窗口里，继承主窗口的全部命令权限。
  所以只预览你自己写的主题；别人发来的主题直接放进主题文件夹用悬浮窗跑更安全。

主题读不到你的历史数据：iframe 没有 `allow-same-origin`，是 opaque origin，
碰不到 localStorage、数据库、宿主 DOM。
