<script setup>
// 小问号 + 悬停气泡。全应用共用一个实现（设置页 / 悬浮窗设置面板）。
//
// 为什么气泡是 position:fixed 并由这里算坐标：调用方基本都在滚动容器里
// （#view-settings、.tpk-main 都是 overflow-y:auto）。绝对定位的气泡会被
// overflow 裁掉 —— 顶部那几行设置刚好是最需要说明的，一裁就等于没提示。
// fixed 不受滚动容器裁剪，坐标按问号的实际矩形现算，靠近屏幕边缘自动夹住，
// 上方放不下就翻到下方（placement='bottom' 则反之）。
import { onBeforeUnmount, ref } from 'vue';

const props = defineProps({
  text: { type: String, required: true },
  label: { type: String, default: '' },
  // 'top'（默认）优先显示在问号上方，放不下自动翻下方；'bottom' 反之
  placement: { type: String, default: 'top' },
});

const btnEl = ref(null);
const tipEl = ref(null);
const on = ref(false);
const coords = ref({ left: 0, top: 0 });

const GAP = 8; // 气泡与问号 / 屏幕边缘的最小间距

function measure() {
  const b = btnEl.value && btnEl.value.getBoundingClientRect();
  const t = tipEl.value;
  if (!b || !t) return;
  const w = t.offsetWidth;
  const h = t.offsetHeight;
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  let left = b.left + b.width / 2 - w / 2;
  left = Math.max(GAP, Math.min(left, vw - w - GAP));

  let top = props.placement === 'bottom' ? b.bottom + GAP : b.top - h - GAP;
  if (top < GAP) top = b.bottom + GAP; // 上方放不下 → 翻到下方
  if (top + h > vh - GAP) top = Math.max(GAP, b.top - h - GAP); // 下方也放不下 → 贴上方

  coords.value = { left, top };
}

function show() {
  // 气泡一直在 DOM 里（只是 visibility:hidden），尺寸可直接量，量完再显示，
  // 不会在 0,0 闪一帧
  measure();
  on.value = true;
  // 容器滚动 / 窗口缩放时跟着走，否则气泡会留在原地
  window.addEventListener('scroll', measure, true);
  window.addEventListener('resize', measure);
}

function hide() {
  on.value = false;
  window.removeEventListener('scroll', measure, true);
  window.removeEventListener('resize', measure);
}

onBeforeUnmount(hide);
</script>

<template>
  <button
    ref="btnEl"
    type="button"
    class="help"
    :aria-label="label || text"
    @mouseenter="show"
    @mouseleave="hide"
    @focus="show"
    @blur="hide"
  >
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="6.67" />
      <path d="M6.06 6a2 2 0 0 1 3.89.67c0 1.33-2 2-2 2" />
      <circle cx="8" cy="11.4" r=".85" class="dot" />
    </svg>
    <span
      ref="tipEl"
      class="tip"
      :class="{ on }"
      role="tooltip"
      :style="{ left: coords.left + 'px', top: coords.top + 'px' }"
    >{{ text }}</span>
  </button>
</template>

<style scoped>
.help {
  position: relative;
  flex: none;
  width: 1.077rem;
  height: 1.077rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  background: none;
  color: var(--text-faint);
  cursor: help;
  transition: color .12s;
}
.help svg {
  width: 1rem;
  height: 1rem;
  display: block;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.2;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.help svg .dot {
  fill: currentColor;
  stroke: none;
}
.help:hover,
.help:focus-visible {
  color: var(--text-dim);
  outline: none;
}
.help:focus-visible {
  color: var(--text);
  box-shadow: 0 0 0 0.154rem var(--signal-dim);
  border-radius: 50%;
}
/* fixed：不被 #view-settings / .tpk-main 这类滚动容器裁掉；坐标由脚本算 */
.tip {
  position: fixed;
  left: 0;
  top: 0;
  /* 要盖住弹窗（.dlg-mask 是 200） */
  z-index: 300;
  width: max-content;
  max-width: min(19.23rem, calc(100vw - 1rem));
  padding: 0.538rem 0.692rem;
  border: 1px solid var(--line-2);
  border-radius: 0.308rem;
  background: var(--panel-2);
  color: var(--text-dim);
  font-family: var(--font-mono);
  font-size: 0.769rem;
  line-height: 1.6;
  text-align: left;
  white-space: normal;
  box-shadow: 0 0.462rem 1.385rem rgba(0, 0, 0, .35);
  opacity: 0;
  visibility: hidden;
  transition: opacity .12s, visibility .12s;
  pointer-events: none;
}
.tip.on {
  opacity: 1;
  visibility: visible;
}
</style>
