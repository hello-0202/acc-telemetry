# 写一个悬浮窗主题

这份文档面向「想让悬浮窗长成我想要的样子」的人。不需要改应用代码 —— 主题是一个独立目录，
放进用户主题文件夹，在设置页点「使用」即可。

想先看效果？把 [`docs/theme-template/`](./theme-template/) 整个目录拷到
用户主题目录的 `my-hud\` 下，改 `theme.json` 的 `id`，然后在设置页刷新。

> 用户主题目录默认是 `<安装目录>\data\themes\`（装到只读目录或老版本升级时可能仍是
> `%LOCALAPPDATA%\acc-telemetry\themes\`）。**别去猜路径**：设置页「数据与存储 → 主题目录」
> 会显示当前实际位置，点「打开」直接跳过去；后面文档里的 `%LOCALAPPDATA%\acc-telemetry\themes`
> 一律理解为「设置页显示的那个目录」。
目录里的四个文件就是最小可运行主题。

也可以把主题目录打包成 **.zip**（theme.json / index.html 放在压缩包根目录，或套一层文件夹都行），
在设置页「更改」→「导入主题包」里选它，应用会解压到 `themes/<theme.json 里的 id>/`。
重名（目录已存在）会拒绝导入，先删掉旧主题再导即可。
导入会按当前选择器校验 `modes`：悬浮窗主题不能从主面板选择器导入，反之亦然，避免装上了却不出现在列表里。

想从模板起手？设置页「更改」→「导出模板」会把对应类型的模板包（悬浮窗 / 主面板各一个）另存出来，
改完再「导入主题包」导回去即可。

---

## 一、目录结构

```
<主题目录>\            ← 默认是 <安装目录>\data\themes，以设置页显示为准
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
  "name": "my-hud",
  "version": "0.1.0",
  "author": "你",
  "desc": "一句话说明",
  "category": "core",
  "tags": ["speed", "gear"],
  "design": { "w": 420, "h": 120 },
  "fit": "contain",
  "modes": ["overlay"],
  "overlaySettings": ["background", "textColor"]
}
```

| 字段 | 必需 | 说明 |
|---|---|---|
| `id` | ✅ | 必须与目录名一致，`[A-Za-z0-9_-]` |
| `design` | ✅ | **设计基准尺寸**（整数 px，80~8192）。见第三节 |
| `modes` | ✅ | 这个主题给哪些窗口用：`overlay`（悬浮窗）/ `monitor`（主面板） |
| `name` | | **请与 `id` 保持一致**（两个名字统一）；卡片/设置页显示的是 `id`（安装包名） |
| `version` / `author` / `desc` | | 只用于展示 |
| `fit` | | `contain`（默认，等比缩放）或 `fluid`（流式铺满，主面板用）。见第三节末尾 |
| `category` | | **可选**，单选分类 id（见下表）。不填、写错、写成非字符串都归「未分类」，**不会让主题失效** |
| `tags` | | **可选**，字符串数组；留给搜索 / 以后做场景预设，本期设置页不展示 |
| `overlaySettings` | | **可选**，字符串数组。声明主题支持哪些宿主外观设置：`"background"`（背景颜色 / 不透明度）、`"textColor"`（文字颜色）。外部主题只有声明过的能力才会出现对应控件；不声明则只能使用主题自带样式（内置主题默认支持） |

### 悬浮窗外观能力（`overlaySettings`）

宿主可以在悬浮窗里替你铺背景底板、换文字颜色。主题要不要支持由你在 `theme.json` 里声明：

```json
{ "overlaySettings": ["background", "textColor"] }
```

- **`background`**：用户设置背景颜色 / 不透明度时，宿主会把 rgba 底板铺在 iframe 后面，并通过
  `acc:init` / `acc:ui` 告诉你 `backgroundColor`（`#rrggbb`）和 `backgroundOpacity`（0..1）。
  主题保持自己的透明留白，或按这两个值同步自带底板即可。该能力还包含宿主的「模糊」
  开关（Acrylic，整窗效果，只有开 / 关），由宿主在窗口层处理，主题无需适配。
- **`textColor`**：用户选文字颜色时，宿主把 `textColor`（`#rrggbb`）发给你。
  主题把它写进 CSS 变量（例如 `--text-color`），让主数字 / 标签跟着换色即可。
- **不声明**：设置页不会出现对应控件，宿主也不会应用该设置（避免外部主题被上一个主题
  留下的设置影响）。老主题不带这个字段完全不受影响。
- 内置主题随应用发布，默认视为支持全部能力；外部主题必须显式声明 —— 这就是「内置 / 外部」的界限。

`category` 的固定取值（`src/telemetry/theme-categories.js` 是单一真源，只有 6 个业务分类 + 未分类）：

| id | 分类 | 适合的主题 |
|---|---|---|
| `core` | 核心驾驶 | 速度 / 挡位 / 转速，以及和圈速差值组合的 HUD |
| `timing` | 圈速与差值 | 本圈 / 最佳 / PB / delta / 扇区 |
| `inputs` | 操控输入 | 油门 / 刹车 / 转向 / TC / ABS |
| `tyres` | 轮胎与制动 | 胎温 / 胎压 / 磨损 / 刹车盘温度 |
| `fuel` | 油量与策略 | 剩余油量 / 每圈油耗 / 可跑圈数 |
| `overview` | 综合 / 全览 | 名次 / 圈数 / 旗语 / 天气 / 剩余时间 |
| `uncategorized` | 未分类 | 不填 category 或写了未知值时自动归入 |

`category` / `tags` **不参与校验**：清单校验（`themes.js::checkMeta()`）只看 `id` / `design` / `fit` / `modes` /
`index.html`。老主题不带这两个字段完全没问题，照常导入、照常启用，内置主题会排到「未分类」组。
**外部（用户）主题不看 `category`**，在悬浮窗选择器里统一归到「外部主题」分类，导入导出工具也在那里。
主面板主题（`modes:["monitor"]`）的 `category` 可以照填，但设置页的主面板选择器**不分组**，卡片直接一张网格列完。

必需字段写错了不会崩：设置页里会标红并写明原因，主题可见但不可启用（悬浮窗选择器里另归到「异常主题」组）。

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
`design` 这时只作为 `acc:init.design` 的默认值传给主题，设置页顶部的预览框会铺满，不参与缩放。

最省事的写法是让根字号跟随视口，其余全用 `rem`：

```css
html { font-size: clamp(10px, calc(1.5vh + 0.4vw), 20px); }  /* 534px 高 ≈ 13px，1080p 顶到 20px */
.card { padding: 0.8rem; }
.big  { font-size: 3.4rem; }
```

布局用 `flex` / `grid` + `%`，不要假设固定宽度；也不要让 `height:100%` 叠在没有确定高度的父级上。
可直接参考 `public/themes/gt-cockpit/`（1280×640 的仪表盘，`modes:["monitor"]`、`fit:"fluid"`）。

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
    // { v, mode, design: {w,h}, scale, fit, box, theme, background,
    //   backgroundColor, backgroundOpacity, textColor }
    applyUi(msg);
    window.parent.postMessage({ type: 'acc:ready' }, '*');
    return;
  }

  // 切黑白主题 / 改悬浮窗背景或文字颜色时补发，不用重新握手
  if (msg.type === 'acc:ui') {
    applyUi(msg);                 // { theme?, background?, backgroundColor?, backgroundOpacity?, textColor? }
    return;
  }

  if (msg.type === 'acc:state') {
    render(msg.payload);          // 数据帧
  }
});

// 约定：把宿主给的界面状态写到 <html> 的 data-* / CSS 变量，CSS 里按需切换
function applyUi(msg) {
  if (msg.theme) document.documentElement.dataset.theme = msg.theme;
  if (msg.background) document.documentElement.dataset.bg = msg.background;
  if (msg.backgroundColor) {
    document.documentElement.style.setProperty('--host-bg', msg.backgroundColor);
  }
  if (typeof msg.backgroundOpacity === 'number') {
    document.documentElement.style.setProperty('--host-bg-opacity', String(msg.backgroundOpacity));
  }
  if (msg.textColor) {
    document.documentElement.style.setProperty('--text-color', msg.textColor);
  }
}
```

- `acc:init` 会**重复到达**（宿主在 iframe 加载后每 400ms 重发一次，直到收到 ready）。这是故意设计的：
  主题脚本常常比宿主的监听先跑完，第一条 ready 会丢。所以**别用「只发一次」的开关**。
- 悬浮窗背景现在有两层信息：
  - `backgroundColor`（`#rrggbb`）+ `backgroundOpacity`（0..1，**精确值**）—— 新主题用这两个；
    宿主已经按 rgba 在 iframe 后面铺好底板，主题只需把自己的底板同步成同色 / 保持透明。
  - `background`（`transparent` | `opaque`）—— 兼容字段。只有 `opacity` 接近 100% 才给 `opaque`
    （中间档保持 `transparent`，防止老主题把自带底板画死），老主题照旧写
    `html[data-bg='opaque']` 即可。
  外部主题必须声明 `"overlaySettings": ["background"]`，背景设置才会生效 / 可调。
- 文字颜色用 `textColor`（`#rrggbb`）传进来。宿主不会替主题改字色（字在你的 HTML 里），
  主题把它写进 CSS 变量并让文字颜色引用该变量即可；建议主数字 / 标签跟随，状态色（红黄绿）保持语义。
  外部主题必须声明 `"overlaySettings": ["textColor"]` 才会出现「文字 → 颜色」设置。
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
| `session.timeLeftMs` / `session.lapsTotal` | ms / 圈 | 当前会话剩余（计时赛）或总圈数 |
| `session.driversTotal` | 辆 | 参赛车总数（含 AI），和 `lap.position` 配对显示「当前/总数」 |
| `session.raceDurationMs` | ms | 本周末正赛总时长；排位阶段做正赛油量规划用，联机/未知为 `null` |

字段缺失时显示 `—` 或 `0` 都可以，但**不要假设它一定存在**（不同车/赛道拿得到的字段不同）。

## 五、调试（三档，从快到慢）

**① 双击 `index.html`** —— 不用开任何东西。主题自己检测到没有宿主，会跑内置演示数据。
改样式 → 刷新浏览器，最快的一档。

**② 主题调试页** —— `npm run dev` 后开 `http://localhost:1420/dev.html`。
把主题放进 iframe，用滑杆定格任意数值（0 速、6 档、delta ±3s、进站…），
可切 1:1 / 适应、三种底色（游戏画面 / 纯黑 / 透明格），还能展开看完整 state JSON。
**这一档最有用**：能确认极端值下排版不炸。

**③ 设置页预览 / 真悬浮窗** —— 主题放进用户目录后，在悬浮窗主题选择器左侧第一列点「外部主题」，
第二列就能看到它，点条目的「预览」在右侧固定大预览框里实时渲染（预览会同步你设的背景等设置）。
点「使用」会把它启用成一个新的悬浮窗（最多同时 5 个），
再点变「停用」就关掉那个窗口；选中已启用的主题后，右侧「悬浮窗设置」里可以拖「背景不透明度」
滑杆、点预设色块或用调色盘改「背景颜色」，并在「文字」分组里改文字颜色（外部主题需在
`theme.json` 的 `overlaySettings` 里声明 `"background"` / `"textColor"`）。
此时可以边改文件边看效果：

> 改了用户主题目录里的文件（css / html / js / theme.json），**悬浮窗和预览会在一秒内自动重建**，
> 不用重启、不用点重试。改了 `design` 也会连带把窗口尺寸调过去。

内置主题（`public/themes/`）没有热重载 —— 它们编译进了二进制，改完要重新 `npm run build`。
另外「把文件放进 `public/themes/<id>/`」只是第一步：目录里的文件不会被 import 分析，
`public/` 也没法枚举，所以**必须再把这个 id 加进 `src/telemetry/themes.js` 的 `BUILTIN_THEME_IDS`**，
否则列表里根本不会出现它。

## 六、必须知道的几条

1. **`contain` 模式按设计像素排版**。不写媒体查询，不写 `vw/vh`，宿主负责缩放。
   `fluid` 模式反过来：必须自己响应式（见第三节）。
2. **`html, body { height: 100% }` 一定要写**。漏了的话 `.card{height:100%}` 只拿到内容高度，
   卡片会缩在顶部，看起来像「垂直居中坏了」。
3. **背景默认保持透明**（`background: transparent`）。悬浮窗是透明窗口，画不透明底色会挡住游戏。
   用户在设置里可以拖「背景不透明度」并选「背景颜色」——宿主会按
   `rgba(backgroundColor, backgroundOpacity)` 自己在 iframe 后面铺底板，并通过
   `acc:init` / `acc:ui` 把 `backgroundColor` / `backgroundOpacity`（以及旧字段
   `background: "transparent" | "opaque"`）告诉主题。主题想让自带卡片也跟随，
   就加一条 `html[data-bg="opaque"] .card { background: var(--host-bg, #0b0f14); }`
   （模板里已写好）。外部主题必须在 `theme.json` 声明 `"overlaySettings": ["background"]`
   才会出现这些设置并应用底板。
   文字颜色同理：声明 `"textColor"` 后，把 `acc:init` / `acc:ui` 的 `textColor` 写进
   `--text-color`，并让主数字 / 标签的 `color` 引用它（内置主题都是这么做的）。
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

9. **非 `streaming` 时要显示占位，不要渲染旧值**。后端在待机 / 退出游戏后仍会按 2Hz 推
   `acc:state` 心跳，并**保留上一场最后一次有效值**（契约里的 stability 约定），
   只是把 `status` 改成 `idle` / `waiting` / `error`。如果照单渲染，没开游戏也会看到速度、
   圈速、踏板停在上一场的位置，看起来像还在采集。判断 `st.status === 'streaming'`，
   否则把实时字段当空值显示 `--` / `–`（内置主题都是这么做的）。

   另外把 `render()` 包进 `try/catch`，一帧脏数据不该让面板停更。

10. **主面板主题要做深/浅两套**（悬浮窗无所谓，它盖在游戏画面上）。
   宿主把当前界面是黑还是白，放在 `acc:init.theme` / `acc:ui.theme` 里（取值 `dark` / `light`），
   用户随时可能在设置里切，切完会补发一条 `acc:ui`，**不会重新握手**。
   只做深色的话，浅色界面的用户会看到一块黑面板钉在白窗口里 —— 内置的 `gt-cockpit` 做了两套，可以直接抄结构：

   ```css
   :root { --page: #000; --ink: #fff; }          /* 深色值 */
   html[data-theme='light'] { --page: #f2f4f7; }  /* 只覆盖变量 */
   html[data-theme='light'] body { --ink: #10151b; }
   ```

   两条经验：**颜色必须全部走变量**（写死一个 `#fff`，白底主题里就有一块会消失）；
   **只换颜色、不动布局**（字号、行高、间距两套必须完全一致，否则切换时整块面板会跳一下）。
   状态色在浅底上通常要压深一档：满饱和的 `#00ff6a` / `#ffe600` 放在白底上会糊成荧光噪点，
   而给黑底做的 `box-shadow` 辉光在浅底上会变成一圈脏晕，记得在浅色分支里去掉。

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
- **设置页预览**的 iframe 跑在主窗口里，继承主窗口的全部命令权限（`sandbox` 挡不住 Tauri 注入的 IPC，
  这是 2026-11-10 按需求改回内嵌预览、并去掉隔离窗口入口的已知取舍）。所以只预览你自己写的主题；
  外部主题放进主题文件夹用悬浮窗跑最安全——那个窗口只被授予 `set_overlay_size` / `set_overlay_locked`，
  权限最小；Rust 的 `theme-preview` 隔离窗口仍在，但设置页不再提供入口，做主题市场前要把预览挪回去。

主题读不到你的历史数据：iframe 没有 `allow-same-origin`，是 opaque origin，
碰不到 localStorage、数据库、宿主 DOM。
