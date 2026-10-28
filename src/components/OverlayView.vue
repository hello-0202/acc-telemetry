<script setup>
// 悬浮窗：主题宿主 + 窗口自身表现。
//   - 主题不连 WebSocket，只收宿主灌进去的 state（后端已算好的快照）
//   - 数据用被动模式接（passive）：只收不发，绝不影响主窗口的记录状态
//   - 默认鼠标穿透（锁定）；Ctrl+Shift+L 切编辑模式，可拖动 / 拉边缩放
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { getCurrentWindow } from '@tauri-apps/api/window';

import ThemeHost from './ThemeHost.vue';
import { OVERLAY_SLOT_MAX, readThemeChoice } from '../telemetry/theme-choice.js';
import { readOverlayBackground } from '../telemetry/overlay-background.js';
import { disconnect, start, telemetry } from '../telemetry/useTelemetry.js';

const isTauri = typeof window !== 'undefined' && !!window.__TAURI_INTERNALS__;
const appWindow = isTauri ? getCurrentWindow() : null;

// 窗口 label 就是槽位：overlay-1 / overlay-2 / overlay-3（浏览器预览时当 1 号）
function slotFromLabel(label) {
  const m = /^overlay-(\d+)$/.exec(label || '');
  const n = m ? Number(m[1]) : 1;
  return Math.min(OVERLAY_SLOT_MAX, Math.max(1, n || 1));
}
const slot = slotFromLabel(appWindow?.label);

// 当前悬浮窗主题：按槽位存在 localStorage（与主窗口同源共享），设置页切换后会推一条事件过来
const choice = ref(readThemeChoice('overlay', slot));

// 悬浮窗背景：透明（默认）/ 不透明。设置页切换后同样会推事件过来。
const background = ref(readOverlayBackground(slot));

function applyTheme() {
  choice.value = readThemeChoice('overlay', slot);
}

// true = 穿透（默认）。浏览器里预览时先当作未锁定，方便看编辑态的样式。
const locked = ref(true);
// 窗口逻辑尺寸 / 内容区尺寸（内容区 = 扣掉 6px 内边距与 1px 边框，正是主题 design 的基准）
const size = ref('');
const design = ref('');

let unlistenLock = null;
let unlistenResize = null;
let unlistenFocus = null;
let unlistenVisible = null;
let unlistenTheme = null;
let unlistenBackground = null;
let blurTimer = null;
// 拖动 / 缩放期间焦点会短暂离开 webview，此时不能当成「用户去玩游戏了」而回锁
let interacting = false;
let interactTimer = null;
// 位置 / 尺寸持久化：拖动 / 缩放结束后把几何写回磁盘
let unlistenMove = null;
let saveTimer = null;

// 主题决定窗口尺寸：设计尺寸 + 悬浮窗内边距（6px×2）与 1px 边框×2，
// 这样主题内容刚好按 design 的 1:1 渲染（k≈1），不会被无谓缩小。
const OVERLAY_CHROME = 14;
// 编辑态工具条高度：解锁时 Rust 会把窗口长高这么多（主题区尺寸不变），算 design 时要扣掉
const EDIT_BAR = 22;

async function syncSize() {
  if (!appWindow) return;
  try {
    const s = await appWindow.innerSize();
    // innerSize 是物理像素：必须除以缩放系数，否则 125% / 150% 缩放下
    // 报出来的「尺寸」直接放大一倍多，拿它定主题 design 就会错。
    let dpr = 1;
    try { dpr = (await appWindow.scaleFactor()) || 1; } catch { dpr = 1; }
    const w = Math.round(s.width / dpr);
    const h = Math.round(s.height / dpr);
    size.value = `${w}×${h}`;
    // 编辑态的窗口多了工具条那一行，design 读数要扣掉，才是主题真正的基准尺寸
    const chrome = OVERLAY_CHROME + (locked.value ? 0 : EDIT_BAR);
    design.value = `${Math.max(0, w - OVERLAY_CHROME)}×${Math.max(0, h - chrome)}`;
  } catch { /* 权限缺失时忽略 */ }
}

// 拖动 / 缩放结束后（停 400ms）把当前几何写回磁盘，供下次启动恢复。
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(doSave, 400);
}

async function doSave() {
  if (!appWindow) return;
  // 编辑态的窗口多了一行工具条（高度 +22、位置上移），不能当成正式几何存下来；
  // 锁定 / 隐藏时 Rust 会复原窗口，那一次 resize/move 事件再落盘才是对的。
  if (!locked.value) return;
  try {
    const pos = await appWindow.outerPosition();
    const size = await appWindow.outerSize();
    await invoke('save_overlay_rect', {
      x: pos.x,
      y: pos.y,
      w: size.width,
      h: size.height,
    });
  } catch { /* 权限缺失时忽略 */ }
}

async function lock() {
  if (!isTauri) return;
  interacting = false;
  try {
    await invoke('set_overlay_locked', { locked: true });
  } catch { /* 忽略：下一次 Ctrl+Shift+L 仍可切回 */ }
}

// 显式拖动：不依赖 data-tauri-drag-region（它只认事件 target 自身，
// 点在子元素上就失效），这里直接调窗口 API，整块区域都能拖。
async function startDrag(e) {
  if (!isTauri || e.button !== 0) return;
  e.preventDefault();
  markInteracting();
  try { await appWindow.startDragging(); } catch { /* 权限缺失时忽略 */ }
}

const EDGES = [
  ['n', 'North'],
  ['s', 'South'],
  ['e', 'East'],
  ['w', 'West'],
  ['ne', 'NorthEast'],
  ['nw', 'NorthWest'],
  ['se', 'SouthEast'],
  ['sw', 'SouthWest'],
];

// 无边框窗口在 Windows 上没有原生可拉的边框（没有 non-client area），
// 必须自己做热区 + startResizeDragging，否则 resizable:true 等于没开。
async function startResize(e, dir) {
  if (!isTauri || e.button !== 0) return;
  e.preventDefault();
  e.stopPropagation();
  markInteracting();
  try { await appWindow.startResizeDragging(dir); } catch { /* 权限缺失时忽略 */ }
}

function markInteracting() {
  interacting = true;
  clearTimeout(interactTimer);
  interactTimer = setTimeout(() => { interacting = false; }, 1500);
}

// 只在窗口可见时连后端：隐藏的悬浮窗不该占着数据流
const active = ref(false);

async function fitToTheme(d) {
  if (!isTauri || !d?.w || !d?.h) return;
  // 编辑态不要按主题 design 重置窗口：用户正在手动定尺寸
  if (!locked.value) return;
  try {
    await invoke('set_overlay_size', {
      width: d.w + OVERLAY_CHROME,
      height: d.h + OVERLAY_CHROME,
    });
    syncSize();
  } catch { /* 失败就保持现有尺寸，主题会按 contain 缩放 */ }
}

watch(active, (on) => {
  if (!isTauri) return;
  if (on) start({ passive: true });
  else disconnect();
});

onMounted(async () => {
  applyTheme();
  if (!isTauri) {
    locked.value = false;
    design.value = '366×136';
    size.value = '380×150';
    return;
  }
  // 配置在 localStorage 里（同源共享），不用等 sidecar 起来
  applyTheme();
  // 事件带槽位：改的是别的悬浮窗的主题，这边不用动
  listen('overlay-theme-changed', (e) => {
    const changed = Number(e.payload?.slot);
    if (!changed || changed === slot) applyTheme();
  })
    .then((fn) => { unlistenTheme = fn; })
    .catch(() => { /* 权限缺失时忽略 */ });
  listen('overlay-background-changed', (e) => {
    const changed = Number(e.payload?.slot);
    if (!changed || changed === slot) background.value = readOverlayBackground(slot);
  })
    .then((fn) => { unlistenBackground = fn; })
    .catch(() => { /* 权限缺失时忽略 */ });
  listen('overlay-lock', (e) => {
    locked.value = !!e.payload;
    clearTimeout(blurTimer);
    // 窗口尺寸 / 位置随锁定态变（工具条那一行），读数要跟着刷新
    syncSize();
  })
    .then((fn) => { unlistenLock = fn; })
    .catch(() => { /* 权限缺失时忽略 */ });
  appWindow.onResized(() => { syncSize(); scheduleSave(); })
    .then((fn) => { unlistenResize = fn; })
    .catch(() => {});
  // 拖动（startDragging）结束时也会掉这个事件：用它记住位置
  appWindow.onMoved(() => { scheduleSave(); })
    .then((fn) => { unlistenMove = fn; })
    .catch(() => {});
  // 解锁后一旦把焦点交还给游戏，重新穿透，避免忘记锁定后点不动游戏。
  // 拖动 / 缩放结束的那一下也会掉焦点，所以延迟复核，别刚解锁就自己锁回去。
  appWindow.onFocusChanged(({ payload: focused }) => {
    if (focused) {
      clearTimeout(blurTimer);
      return;
    }
    clearTimeout(blurTimer);
    blurTimer = setTimeout(async () => {
      if (locked.value || interacting || document.hasFocus()) return;
      // 多开时焦点常常只是挪到另一个悬浮窗去了：那种情况下不能回锁，
      // 否则点一下第二个窗口、第一个窗口的失焦计时器会把所有窗口一起锁回去。
      let otherFocused = false;
      try { otherFocused = await invoke('overlay_any_focused'); } catch { otherFocused = false; }
      if (otherFocused) return;
      if (!document.hasFocus()) lock();
    }, 800);
  })
    .then((fn) => { unlistenFocus = fn; })
    .catch(() => {});
  // 悬浮窗显隐由 Rust 广播，隐藏时就断开数据流
  listen('overlay-visibility', (e) => { active.value = !!e.payload; })
    .then((fn) => { unlistenVisible = fn; })
    .catch(() => {});

  syncSize();
  try {
    active.value = await appWindow.isVisible();
  } catch {
    active.value = true;
  }
});

onBeforeUnmount(() => {
  clearTimeout(blurTimer);
  clearTimeout(interactTimer);
  clearTimeout(saveTimer);
  if (unlistenLock) unlistenLock();
  if (unlistenResize) unlistenResize();
  if (unlistenMove) unlistenMove();
  if (unlistenFocus) unlistenFocus();
  if (unlistenVisible) unlistenVisible();
  if (unlistenTheme) unlistenTheme();
  if (unlistenBackground) unlistenBackground();
});
</script>

<template>
  <div class="ov-root" :class="{ editing: !locked, opaque: background === 'opaque' }">
    <!-- 编辑态工具条：单独一行、固定在主题正上方。
         解锁时 Rust 会把窗口向上长高 22px（主题位置和尺寸都不动），
         所以工具条不会压住主题，也不会把 contain 的缩放比压小。 -->
    <div v-if="!locked" class="ov-bar" @pointerdown="startDrag">
      <span class="ov-drag">拖动移动 · 拉边缩放</span>
      <span class="ov-size" :title="'窗口 ' + size">
        design <b>{{ design }}</b>
      </span>
      <button class="ov-lock" type="button" @pointerdown.stop @click="lock">锁定</button>
    </div>

    <div class="ov-card">
      <div id="theme-slot" class="ov-slot">
        <ThemeHost
          :theme="choice.id"
          :source="choice.source"
          mode="overlay"
          :background="background"
          :state="telemetry.state"
          @ready="fitToTheme"
        />
      </div>

      <!-- 编辑态拖动层：整块主题都能拖着走 -->
      <div v-if="!locked" class="ov-grab" @pointerdown="startDrag" />
    </div>

    <!-- 八向缩放热区：贴窗口边缘（无边框窗口没有原生边框可拉） -->
    <template v-if="!locked">
      <div
        v-for="[cls, dir] in EDGES"
        :key="dir"
        class="ov-edge"
        :class="cls"
        @pointerdown="startResize($event, dir)"
      />
    </template>
  </div>
</template>

<style scoped>
.ov-root {
  position: relative;
  width: 100vw;
  height: 100vh;
  box-sizing: border-box;
  padding: 6px;
  /* 纵向排：编辑态工具条一行 + 主题卡片。窗口解锁时会自动长高 22px（Rust 侧），
     所以卡片高度始终等于主题 design，不会被工具条挤压。 */
  display: flex;
  flex-direction: column;
}

/* 不透明模式：整窗（含 6px 内边距）铺实心底板，主题的透明留白也不会透出游戏画面。
   窗口本身是透明的，所以圆角外的四个角仍然透 —— 和主题卡片的圆角视觉一致。 */
.ov-root.opaque {
  background: #0b0e13;
  border-radius: 12px;
}

.ov-card {
  position: relative;
  width: 100%;
  flex: 1 1 auto;
  min-height: 0;
  box-sizing: border-box;
  display: flex;
  border-radius: 12px;
  /* 边框宽度固定 1px（锁定态透明）：改宽度会挤到主题区，编辑态用 box-shadow 加粗 */
  border: 1px solid transparent;
  overflow: hidden;
}

/* 锁定态：主题自己带圆角和背景，不要再套一层可见边框 */
.ov-root:not(.editing) .ov-card {
  border-color: transparent;
  box-shadow: none;
}

/* 编辑态：实线高亮框把悬浮窗（主题区）整个框出来，一眼能看清边界 */
.ov-root.editing .ov-card {
  border-color: rgba(120, 190, 255, 0.95);
  box-shadow:
    0 0 0 2px rgba(120, 190, 255, 0.28),
    0 0 14px rgba(120, 190, 255, 0.22);
}

.ov-slot {
  width: 100%;
  height: 100%;
  box-sizing: border-box;
}

/* 拖动层铺在主题上：编辑态就是调布局，不需要主题里的交互 */
.ov-grab {
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  bottom: 0;
  z-index: 2;
  cursor: move;
}

/* 工具条：占满一行、固定在主题正上方（不压住主题，也不改主题缩放） */
.ov-bar {
  flex: 0 0 22px;
  height: 22px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 6px;
  border-radius: 6px;
  background: rgba(10, 12, 16, 0.78);
  box-shadow: inset 0 0 0 1px rgba(120, 190, 255, 0.35);
  font: 11px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  color: rgba(255, 255, 255, 0.6);
  cursor: move;
  user-select: none;
}

.ov-drag {
  pointer-events: none;
}

.ov-size {
  margin-left: auto;
  letter-spacing: 0.04em;
  color: rgba(160, 205, 255, 0.85);
  pointer-events: none;
}

.ov-size b {
  font-weight: 600;
  color: #cfe6ff;
}

.ov-lock {
  height: 16px;
  padding: 0 8px;
  border: 1px solid rgba(120, 190, 255, 0.5);
  border-radius: 4px;
  background: transparent;
  color: rgba(160, 205, 255, 0.9);
  font: 10px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  cursor: pointer;
}

.ov-lock:hover {
  background: rgba(120, 190, 255, 0.16);
  color: #fff;
}

/* 缩放热区：边缘 5px、四角 12px，压在拖动层之上 */
.ov-edge {
  position: absolute;
  z-index: 4;
  touch-action: none;
}

.ov-edge.n,
.ov-edge.s {
  left: 12px;
  right: 12px;
  height: 5px;
  cursor: ns-resize;
}

.ov-edge.n { top: 0; }
.ov-edge.s { bottom: 0; }

.ov-edge.e,
.ov-edge.w {
  top: 12px;
  bottom: 12px;
  width: 5px;
  cursor: ew-resize;
}

.ov-edge.e { right: 0; }
.ov-edge.w { left: 0; }

.ov-edge.ne,
.ov-edge.nw,
.ov-edge.se,
.ov-edge.sw {
  width: 12px;
  height: 12px;
}

.ov-edge.nw { top: 0; left: 0; cursor: nwse-resize; }
.ov-edge.se { bottom: 0; right: 0; cursor: nwse-resize; }
.ov-edge.ne { top: 0; right: 0; cursor: nesw-resize; }
.ov-edge.sw { bottom: 0; left: 0; cursor: nesw-resize; }
</style>
