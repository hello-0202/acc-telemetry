<script setup>
// 主题宿主：把用户/预设主题的 HTML 放进 iframe，按「设计基准 + contain 缩放」渲染，
// 并把后端组装好的 state 快照用 postMessage 灌进去。
//
// 三条硬规则（见 docs/theme-state-contract.md）：
//   1. iframe 沙箱隔离：sandbox 只给 allow-scripts，不给 allow-same-origin
//   2. 主题不碰 WebSocket，也不发 start/stop —— 它只收 state
//   3. 主题报错 / 加载超时必须能回退提示，绝不留白屏
import { computed, onBeforeUnmount, onMounted, ref, toRaw, watch } from 'vue';
import { themeBaseUrl } from '../telemetry/theme-url.js';
import { onThemesChanged } from '../telemetry/theme-watch.js';
import { themeName } from '../telemetry/theme.js';

const props = defineProps({
  /** 主题目录名（内置 public/themes/<id>/，用户 %LOCALAPPDATA%\acc-telemetry\themes\<id>\） */
  theme: { type: String, required: true },
  /** builtin = 打包进二进制；user = 用户目录，走 theme:// 自定义协议 */
  source: { type: String, default: 'builtin' },
  /** overlay | monitor | board —— 主题可据此切布局 */
  mode: { type: String, default: 'overlay' },
  /** 后端 state 快照（telemetry.state） */
  state: { type: Object, default: null },
  /** 覆盖 theme.json 的设计尺寸（调试用） */
  design: { type: Object, default: null },
  /**
   * 悬浮窗背景：transparent（默认，能看见游戏画面）/ opaque（宿主铺实心底板）。
   * 主题可以据此把自己的底板也画成实心（CSS 里查 html[data-bg]）。
   */
  background: { type: String, default: 'transparent' },
  /**
   * 缩放方式，默认取 theme.json 的 fit：
   *   contain —— 按设计尺寸等比缩放塞进容器（悬浮窗：主题 1:1 大小恒定）
   *   fluid   —— iframe 铺满容器、不缩放，主题自己用 %/flex 排版（主面板/看板）
   */
  fit: { type: String, default: null },
});

// 主题就绪时把设计尺寸抛给页面：悬浮窗据此把窗口调成「主题设计尺寸 + 内边距」
const emit = defineEmits(['ready', 'fail']);

const READY_TIMEOUT_MS = 4000;

const box = ref(null);
const frame = ref(null);
const design = ref({ w: 380, h: 150 });
const fitMode = ref('contain');
const boxSize = ref({ w: 0, h: 0 });
const scale = ref(1);
const ready = ref(false);
const failed = ref(false);
const reloadKey = ref(0);

let ro = null;
let readyTimer = null;
let handshakeTimer = null;
let lastSeq = -1;

const baseUrl = computed(() => themeBaseUrl(props.theme, props.source));
const src = computed(() => `${baseUrl.value}index.html?r=${reloadKey.value}`);

// 整块按设计尺寸渲染，再用 transform 等比缩放到容器内（contain）；
// fluid 则直接铺满容器交给主题自己响应式。
const frameStyle = computed(() => {
  if (fitMode.value === 'fluid') {
    return { width: '100%', height: '100%', transform: 'none', left: '0px', top: '0px' };
  }
  const dw = design.value.w;
  const dh = design.value.h;
  const k = scale.value;
  const left = Math.max(0, (boxSize.value.w - dw * k) / 2);
  const top = Math.max(0, (boxSize.value.h - dh * k) / 2);
  return {
    width: `${dw}px`,
    height: `${dh}px`,
    transform: `scale(${k})`,
    left: `${left}px`,
    top: `${top}px`,
  };
});

function measure() {
  const el = box.value;
  if (!el) return;
  const rect = el.getBoundingClientRect();
  boxSize.value = { w: rect.width, h: rect.height };
  if (fitMode.value === 'fluid') {
    scale.value = 1;
    return;
  }
  const k = Math.min(rect.width / design.value.w, rect.height / design.value.h);
  scale.value = Number.isFinite(k) && k > 0 ? k : 1;
}

async function loadMeta() {
  try {
    const res = await fetch(`${baseUrl.value}theme.json`, { cache: 'no-cache' });
    if (res.ok) {
      const meta = await res.json();
      if (meta?.design?.w > 0 && meta?.design?.h > 0) {
        design.value = { w: meta.design.w, h: meta.design.h };
      }
      if (meta?.fit === 'fluid' || meta?.fit === 'contain') fitMode.value = meta.fit;
    }
  } catch {
    /* 没有 theme.json 就用默认尺寸 */
  }
  if (props.fit === 'fluid' || props.fit === 'contain') fitMode.value = props.fit;
  if (props.design?.w > 0 && props.design?.h > 0) design.value = { ...design.value, ...props.design };
  measure();
}

function post(message) {
  frame.value?.contentWindow?.postMessage(message, '*');
}

// postMessage 走结构化克隆，而 telemetry.state 存在 Vue 的 reactive() 里，
// 取出来是 Proxy —— Proxy 不可克隆，直接 post 会抛 DataCloneError。
// 更糟的是这个异常发生在组件更新（watch → pushState）过程中：Vue 的 patch 被打断，
// 之后整个悬浮窗的渲染就废了（编辑态工具条 / 八向缩放热区永远不出现，
// 看起来就是「调整悬浮窗大小不管用」）。所以出口一律转成纯数据。
function toCloneable(v) {
  const raw = toRaw(v);
  try {
    return structuredClone(raw);
  } catch {
    return JSON.parse(JSON.stringify(raw));
  }
}

function pushState(state) {
  if (!ready.value || !state) return;
  if (typeof state.seq === 'number') {
    if (state.seq === lastSeq) return;
    lastSeq = state.seq;
  }
  post({ type: 'acc:state', payload: toCloneable(state) });
}

function sendInit() {
  post({
    type: 'acc:init',
    v: 1,
    mode: props.mode,
    design: { ...design.value },
    fit: fitMode.value,
    scale: scale.value,
    // fluid 模式下主题的视口就是容器本身，顺手告诉它尺寸，省得靠 window.innerWidth 猜
    box: { ...boxSize.value },
    // 宿主当前是黑底还是白底：主题自己决定要不要跟着换配色（CSS 里查 html[data-theme]）
    theme: themeName.value,
    // 悬浮窗背景：transparent | opaque，主题据此决定底板是否画实心
    background: props.background,
  });
}

function onFrameLoad() {
  ready.value = false;
  lastSeq = -1;
  failed.value = false;
  clearTimeout(readyTimer);
  clearInterval(handshakeTimer);

  // 主题脚本可能比宿主的 message 监听先跑完（ready 会丢），
  // 所以 load 之后主动握手，直到收到 ready。主题侧对 init 的回复是幂等的。
  sendInit();
  handshakeTimer = setInterval(() => {
    if (ready.value) {
      clearInterval(handshakeTimer);
      handshakeTimer = null;
      return;
    }
    sendInit();
  }, 400);

  // 主题没响应就说明脚本挂了，超时兜底提示（同时通知外层回退到内置面板）
  readyTimer = setTimeout(() => {
    if (!ready.value) {
      failed.value = true;
      emit('fail');
    }
  }, READY_TIMEOUT_MS);
}

function onMessage(event) {
  if (event.source !== frame.value?.contentWindow) return;
  if (event.data?.type === 'acc:ready') {
    if (ready.value) return; // 幂等：重复的 ready 不能让握手来回弹
    ready.value = true;
    failed.value = false;
    clearTimeout(readyTimer);
    clearInterval(handshakeTimer);
    handshakeTimer = null;
    pushState(props.state);
    emit('ready', { ...design.value });
  }
}

function retry() {
  failed.value = false;
  reloadKey.value += 1;
}

// 热重载：用户主题目录里的文件变了就重建 iframe（内置主题编译进二进制，改不了也不用听）。
// theme.json 也常被一起改（design 变了要连锁调整悬浮窗大小），所以重建前先重读一次元数据。
let offWatch = null;
let lastReloadAt = 0;

async function hotReload() {
  const now = Date.now();
  if (now - lastReloadAt < 300) return; // Rust 侧已合并 500ms，这里只兜零散的连环事件
  lastReloadAt = now;
  await loadMeta();
  reloadKey.value += 1;
}

watch(() => props.state, (s) => pushState(s));

// 宿主切黑白主题 / 悬浮窗背景：不发 init（那会触发一次 ready，主题可能重绘），
// 只补一条轻量通知
watch(themeName, (t) => {
  if (ready.value) post({ type: 'acc:ui', theme: t });
});
watch(() => props.background, (b) => {
  if (ready.value) post({ type: 'acc:ui', background: b });
});

// 换主题（或换来源）时 design 可能完全不同：重读 theme.json 再重新量一次容器，
// 否则新主题会被按旧主题的尺寸缩放。iframe 的 src 是 computed，会自己重新加载。
watch(
  () => [props.theme, props.source],
  async () => {
    failed.value = false;
    lastSeq = -1;
    design.value = { w: 380, h: 150 };
    fitMode.value = 'contain';
    await loadMeta();
    measure();
  },
);

// 监听必须在 setup 阶段就挂上（早于 iframe 创建）：主题脚本可能在宿主挂载前
// 就把 ready 发出来，挂晚了这条消息就永久丢了。
window.addEventListener('message', onMessage);

onMounted(async () => {
  ro = new ResizeObserver(() => measure());
  if (box.value) ro.observe(box.value);
  await loadMeta();
  measure();

  if (props.source === 'user') {
    offWatch = onThemesChanged((ids) => {
      if (ids.includes(props.theme)) hotReload();
    });
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('message', onMessage);
  if (offWatch) offWatch();
  if (ro) ro.disconnect();
  clearTimeout(readyTimer);
  clearInterval(handshakeTimer);
});
</script>

<template>
  <div ref="box" class="th-root">
    <iframe
      ref="frame"
      class="th-frame"
      :src="src"
      :style="frameStyle"
      sandbox="allow-scripts"
      referrerpolicy="no-referrer"
      tabindex="-1"
      @load="onFrameLoad"
    ></iframe>

    <div v-if="failed" class="th-fail">
      <span>主题加载失败</span>
      <button type="button" @click="retry">重试</button>
    </div>
  </div>
</template>

<style scoped>
.th-root {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.th-frame {
  position: absolute;
  border: 0;
  transform-origin: top left;
  background: transparent;
  color-scheme: normal;
}

.th-fail {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border: 1px dashed rgba(255, 120, 120, 0.5);
  border-radius: 8px;
  color: #d98b8b;
  font: 12px/1.4 ui-monospace, monospace;
}

.th-fail button {
  height: 22px;
  padding: 0 10px;
  border: 1px solid rgba(255, 120, 120, 0.5);
  border-radius: 4px;
  background: transparent;
  color: #e0a0a0;
  font: 11px/1 ui-monospace, monospace;
  cursor: pointer;
}

.th-fail button:hover {
  background: rgba(255, 120, 120, 0.15);
  color: #fff;
}
</style>
