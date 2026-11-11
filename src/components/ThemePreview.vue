<script setup>
// 主题缩略预览：套一层「模拟桌面」底色，再把 ThemeHost 铺上去。
//
// 主题是透明 HUD，直接放在面板底色上会看不清（深色主题的白字贴在白色设置页上尤其糟），
// 所以这里铺一块「游戏画面」当底：优先用 public/overlay-preview-bg.png（真实游戏截图，
// 由悬浮窗预览背景取景工具裁剪），没上传时回落到深灰渐变底。透明区域一眼能看出来。
// 底色不再带斜纹（用户要求去掉「斜杠杠」）。
//
// ⚠️ 预览场景一律可以传 fit="contain"：fit: fluid 的主面板主题（如 1280×640 的
// gt-cockpit）如果直接按预览框的几百像素视口 fluid 排版，字号下限 / 固定间距会把
// 数值挤叠变形。contain 让 iframe 始终看到设计尺寸的视口，再整体等比缩小到框里，
// 排版和真机一致。真正在运行时（主面板 / 悬浮窗）仍按 theme.json 的 fit，不受影响。
//
// 悬浮窗外观设置（背景颜色 / 不透明度）会通过 :background-color / :background-opacity
// 透传进来，预览和真机一致：
//   - opacity > 0：悬浮窗本体（.tp-window）按「颜色 + 透明度」铺一层 rgba 底板，
//     和 OverlayView 根节点的内联底色一致；游戏画面（.tp-stage）始终保留，
//     所以预览里能看到「窗有底色、但游戏还在后面」。
//   - opacity = 0（默认）/ 空：窗保持完全透明，游戏画面直接透出来。
// 底色只作用在窗这一层，绝不再涂黑整个预览框（用户明确要求）。
// 以后加别的「影响预览外观」的设置项，主题作者只要把值透传到这里即可，无需改 .tp-stage。
//
// 数据和交互：
//   - 数据用契约 example 造的演示帧（mock-state.js），不连后端也能预览；
//   - live 时才跑定时器，缩略图用静态帧，几个主题同时开着也不费 CPU；
//   - 整块 pointer-events:none —— 预览不该抢点击，点击交给外层卡片按钮。
import { computed, onBeforeUnmount, ref } from 'vue';

import ThemeHost from './ThemeHost.vue';
import { currentMockState, subscribeMockState } from '../telemetry/mock-state.js';
import { overlayColorWithAlpha } from '../telemetry/overlay-background.js';

const props = defineProps({
  theme: { type: String, required: true },
  /** builtin | user */
  source: { type: String, default: 'builtin' },
  /** 设计尺寸：角标 + 传给 ThemeHost 当 contain 缩放基准（不传则 ThemeHost 自己读 theme.json） */
  design: { type: Object, default: null },
  /** 强制缩放方式：'contain' = 按 design 等比缩小；null = 沿用 theme.json 的 fit */
  fit: { type: String, default: null },
  /** 预览哪种窗口的排版：overlay / monitor / board（透传给主题的 acc:init） */
  mode: { type: String, default: 'overlay' },
  /** true = 每 500ms 灌一帧新数据；false = 一张静态图 */
  live: { type: Boolean, default: false },
  /** 是否显示尺寸角标 */
  badge: { type: Boolean, default: true },
  /** 悬浮窗背景色（#rrggbb）；仅 overlay 预览且 opacity > 0 时可见。 */
  backgroundColor: { type: String, default: '' },
  /** 悬浮窗背景不透明度 0..1；0 = 完全透明（默认，直接看见模拟游戏画面）。 */
  backgroundOpacity: { type: Number, default: 0 },
  /** 悬浮窗背景模糊（px）：预览页背后是同一页面的游戏截图，backdrop-filter 效果最明显。 */
  backgroundBlur: { type: Number, default: 0 },
  /** 悬浮窗文字颜色（#rrggbb）；主题支持时预览里的字也跟着变。 */
  textColor: { type: String, default: '#ffffff' },
  /** 内置主题自己的配置值（曲线布局 / 显示模块……），预览和真机保持一致。 */
  settings: { type: Object, default: null },
});

// 只有悬浮窗（overlay）预览才把 HUD 画成「浮在游戏上的窗」（四周留游戏画面）。
// 主面板（monitor）仍铺满舞台，维持原样。
const isOverlay = computed(() => props.mode === 'overlay');

// 预览里「窗」的比例要严格跟真机一致：真机加载主题时 fitToTheme 会把窗口改成
// 主题 design 尺寸（+6px 内边距，比例影响可忽略），所以这里用 design 的宽高比。
// 没拿到 design 时退回悬浮窗默认 380×150。主面板预览不需要（铺满）。
const windowStyle = computed(() => {
  if (!isOverlay.value) return null;
  const w = props.design?.w > 0 ? props.design.w : 380;
  const h = props.design?.h > 0 ? props.design.h : 150;
  const style = { aspectRatio: `${w} / ${h}` };
  // 背景颜色 / 不透明度只作用在窗这一层；和真机 OverlayView 的 rgba 底板完全同一套算法。
  if (props.backgroundOpacity > 0) {
    style.backgroundColor = overlayColorWithAlpha(
      props.backgroundColor || '#0b0f14',
      props.backgroundOpacity,
    );
  }
  if (props.backgroundBlur > 0) {
    style.backdropFilter = `blur(${props.backgroundBlur}px)`;
    style.WebkitBackdropFilter = `blur(${props.backgroundBlur}px)`;
  }
  return style;
});

const state = ref(currentMockState());
let unsubscribe = null;

if (props.live) {
  unsubscribe = subscribeMockState((s) => {
    state.value = s;
  });
}

onBeforeUnmount(() => {
  if (unsubscribe) unsubscribe();
});
</script>

<template>
  <div class="tp-root">
    <div class="tp-stage" aria-hidden="true"></div>
    <div class="tp-window" :class="{ card: isOverlay }" :style="windowStyle">
      <ThemeHost
        :theme="theme"
        :source="source"
        :mode="mode"
        :state="state"
        :design="design"
        :fit="fit"
        :background-color="backgroundColor || '#0b0f14'"
        :background-opacity="backgroundOpacity"
        :text-color="textColor || '#ffffff'"
        :settings="settings"
      />
    </div>
    <span v-if="badge && design" class="tp-badge">{{ design.w }}×{{ design.h }}</span>
  </div>
</template>

<style scoped>
.tp-root {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  /* 预览只负责看，点击交给外层按钮 */
  pointer-events: none;
}

/* 模拟游戏画面：默认铺一张真实游戏截图（悬浮窗预览背景取景工具产出，
 * 存到 public/overlay-preview-bg.png）。照片用 cover 填充，比例不一时裁掉溢出；
 * 没上传截图时第一层加载失败，自动回落到下面的深底+顶部柔光。无斜纹。
 * 这一层永远是「游戏画面」，绝不被不透明设置涂黑。 */
.tp-stage {
  position: absolute;
  inset: 0;
  background-color: #0d1116;
  background-image:
    url('/overlay-preview-bg.png'),
    radial-gradient(75% 130% at 50% 0%, rgba(126, 146, 168, 0.2), transparent 62%);
  background-size: cover, cover;
  background-position: center, center;
  background-repeat: no-repeat, no-repeat;
}

/* 悬浮窗本体（HUD 容器）。默认铺满舞台（主面板预览保持原样）。
   游戏画面（.tp-stage）永远在它下面，不会被涂黑。 */
.tp-window {
  position: absolute;
  inset: 0;
}

/* 悬浮窗模式：把 HUD 画成「浮在游戏上的窗」——按真机 design 比例（aspect-ratio 由
   inline style 注入），居中、明显比游戏画面小，四周露出赛道截图。不透明只作用在窗上。
   圆角 + 裁切让 HUD 不会溢出窗体。 */
.tp-window.card {
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 54%;
  max-height: 78%;
  border-radius: 12px;
  overflow: hidden;
}

.tp-badge {
  position: absolute;
  right: 0.3077rem;
  bottom: 0.2308rem;
  padding: 0.0769rem 0.3077rem;
  border-radius: 0.1538rem;
  background: rgba(6, 9, 12, 0.62);
  color: rgba(233, 238, 244, 0.72);
  font-family: var(--font-mono);
  font-size: 0.6154rem;
  line-height: 1.4;
}
</style>
