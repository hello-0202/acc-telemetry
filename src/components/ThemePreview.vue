<script setup>
// 主题缩略预览：套一层「模拟桌面」底色，再把 ThemeHost 铺上去。
//
// 主题是透明 HUD，直接放在面板底色上会看不清（深色主题的白字贴在白色设置页上尤其糟），
// 所以这里固定用一块深灰渐变 + 斜纹底，代表「游戏画面」，透明区域一眼能看出来。
//
// 数据和交互：
//   - 数据用契约 example 造的演示帧（mock-state.js），不连后端也能预览；
//   - live 时才跑定时器，缩略图用静态帧，几个主题同时开着也不费 CPU；
//   - 整块 pointer-events:none —— 预览不该抢点击，点击交给外层卡片按钮。
import { onBeforeUnmount, ref } from 'vue';

import ThemeHost from './ThemeHost.vue';
import { currentMockState, subscribeMockState } from '../telemetry/mock-state.js';

const props = defineProps({
  theme: { type: String, required: true },
  /** builtin | user */
  source: { type: String, default: 'builtin' },
  /** 设计尺寸，仅用于右下角角标（真实缩放由 ThemeHost 自己按 theme.json 算） */
  design: { type: Object, default: null },
  /** 预览哪种窗口的排版：overlay / monitor / board（透传给主题的 acc:init） */
  mode: { type: String, default: 'overlay' },
  /** true = 每 500ms 灌一帧新数据；false = 一张静态图 */
  live: { type: Boolean, default: false },
  /** 是否显示尺寸角标 */
  badge: { type: Boolean, default: true },
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
    <ThemeHost :theme="theme" :source="source" :mode="mode" :state="state" />
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

/* 模拟游戏画面：深底 + 两道斜纹，透明区域能看出来 */
.tp-stage {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(75% 130% at 50% 0%, rgba(126, 146, 168, 0.2), transparent 62%),
    repeating-linear-gradient(45deg, rgba(150, 162, 177, 0.06) 0 5px, transparent 5px 11px),
    #0d1116;
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
