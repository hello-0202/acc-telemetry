<script setup>
// 世界坐标 Canvas 赛道地图：
// - 真实宽度的左右边界多边形
// - 玩家位置用一个小圆点表示
// - 滚轮缩放 / 拖动平移 / 双击复位
import { onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { buildTrackGeometry, clamp, cssVar, uiScale } from '../telemetry/model.js';

const props = defineProps({
  points: { type: Array, default: () => [] },
  car: { type: Object, default: null },
  themeVersion: { type: Number, default: 0 },
});

const wrapRef = ref(null);
const canvasRef = ref(null);

let ctx = null;
let resizeObserver = null;
let cssW = 0;
let cssH = 0;
let dpr = 1;
let bounds = null;
let geom = null; // 平滑后的世界坐标：左右边界 / 中心线
let paths = null; // 世界坐标 Path2D：平移 / 缩放时不再重建曲线
let fitScale = 1;
let drag = null;

const view = reactive({ scale: 1, ox: 0, oy: 0 });

/* ------------------------------------------------------------- 几何 */
// 路面几何在 model.js 里统一生成（中心线平滑 + 闭合环法线 + 真实左右边距），
// 屏幕坐标是线性变换，世界坐标算一次就能复用。
function buildGeometry() {
  geom = buildTrackGeometry(props.points);
  bounds = geom ? geom.bounds : null;
  paths = null;
  if (!geom) return;

  // 路面：左边界 → 右边界反向；两段在起跑线处用直线连接
  const road = new Path2D();
  traceClosed(geom.left, true, road);
  traceClosed(geom.right.slice().reverse(), false, road);
  road.closePath();

  // 底图只保留路面（不再构建中心线 Path2D：实时面板按需求去掉底图里的线）

  paths = {
    road,
    start: geom.start
      ? { a: geom.start.a, b: geom.start.b }
      : null,
  };
}

function fitView() {
  if (!bounds || !cssW || !cssH) return;
  const pad = 16;
  const scale = Math.max(
    1e-6,
    Math.min((cssW - 2 * pad) / bounds.spanX, (cssH - 2 * pad) / bounds.spanZ)
  );
  view.scale = scale;
  view.ox = (cssW - bounds.spanX * scale) / 2;
  view.oy = (cssH - bounds.spanZ * scale) / 2;
  fitScale = scale;
}

// 世界坐标 → 设备坐标。世界 Z 向上而 Canvas Y 向下，所以 d 取负。
function applyWorldTransform() {
  const s = view.scale * dpr;
  ctx.setTransform(
    s, 0, 0, -s,
    dpr * (view.ox - bounds.minX * view.scale),
    dpr * (view.oy + bounds.maxZ * view.scale)
  );
}

function clampView() {
  if (!bounds) return;
  const contentW = bounds.spanX * view.scale;
  const contentH = bounds.spanZ * view.scale;
  view.ox = clamp(view.ox, cssW * 0.25 - contentW, cssW * 0.75);
  view.oy = clamp(view.oy, cssH * 0.25 - contentH, cssH * 0.75);
}

/* ------------------------------------------------------------- 绘制 */
function draw() {
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const u = uiScale();
  const bg = cssVar('--map-bg') || '#f5f7f4';
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, cssW, cssH);

  if (!bounds || !geom || !paths || !props.points.length) {
    ctx.fillStyle = cssVar('--text-faint') || '#5b6572';
    ctx.font = (12 * u).toFixed(1) + 'px "Cascadia Code", Consolas, monospace';
    ctx.textAlign = 'center';
    ctx.fillText('赛道数据未加载', cssW / 2, cssH / 2);
    ctx.textAlign = 'start';
    return;
  }

  applyWorldTransform();
  // 线宽 / 虚线都在世界坐标里定义，除以缩放让它们在屏幕上保持像素尺寸
  const px = (v) => v / view.scale;

  const roadFill = cssVar('--map-road-fill') || 'rgba(255, 255, 255, 0.92)';
  const roadLine = cssVar('--map-road-line') || 'rgba(255, 255, 255, 0.30)';

  // 路面（左边界 → 右边界反向闭合）
  ctx.fillStyle = roadFill;
  ctx.fill(paths.road);
  ctx.lineJoin = 'round';
  ctx.lineCap = 'butt';
  // 两层描边，与圈速概要小地图（.map-road-halo + .map-road）同款：
  // 外层半透明柔光（车面色，宽 1.8）+ 内层清晰细轮廓（--map-road-line，.6）
  ctx.strokeStyle = roadFill;
  ctx.lineWidth = px(1.8 * u);
  ctx.stroke(paths.road);
  ctx.strokeStyle = roadLine;
  ctx.lineWidth = px(0.6 * u);
  ctx.stroke(paths.road);

  // 底图里不再画中心线（按需求去掉），只保留路面 + 起跑线 + 车辆

  // 起跑线：横跨整条赛道宽度，端点就是左右边界的第 0 个点。
  // 样式与圈速概要（.map-sf line）一致——主题墨色（黑）单线、2.2px、平头帽
  if (paths.start) {
    ctx.beginPath();
    ctx.moveTo(paths.start.a.x, paths.start.a.z);
    ctx.lineTo(paths.start.b.x, paths.start.b.z);
    ctx.strokeStyle = cssVar('--map-ink') || '#10141a';
    ctx.lineWidth = px(2.2 * u);
    ctx.stroke();
  }

  drawCar(px);

  // 回到 CSS 像素坐标，避免后续绘制受世界缩放影响
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

// 闭合环的平滑走线：把 Catmull-Rom 样条转成三次贝塞尔。
// 与"以点为控制点、经过中点"的二次贝塞尔不同，这种走线严格穿过每个采样点，
// 弯角不会被切掉，放大后边距仍然和 fastlane.ai 里的一致。
// target 可以是 ctx，也可以是 Path2D。
function traceClosed(list, move, target = ctx) {
  const n = list.length;
  if (!n) return;
  if (n < 3) {
    if (move) target.moveTo(list[0].x, list[0].z);
    else target.lineTo(list[0].x, list[0].z);
    for (let i = 1; i < n; i += 1) target.lineTo(list[i].x, list[i].z);
    return;
  }
  if (move) target.moveTo(list[0].x, list[0].z);
  else target.lineTo(list[0].x, list[0].z);
  for (let i = 0; i < n; i += 1) {
    const p0 = list[(i - 1 + n) % n];
    const p1 = list[i];
    const p2 = list[(i + 1) % n];
    const p3 = list[(i + 2) % n];
    // 均匀 Catmull-Rom 的控制点（1/6 系数）
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1z = p1.z + (p2.z - p0.z) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2z = p2.z - (p3.z - p1.z) / 6;
    target.bezierCurveTo(c1x, c1z, c2x, c2z, p2.x, p2.z);
  }
}

function drawCar(px) {
  const car = props.car;
  if (!car || !Number.isFinite(car.x) || !Number.isFinite(car.z)) return;

  const color = cssVar('--signal-text') || '#c8f53e';
  const u = uiScale();

  // 外圈柔光 + 实心小圆点（世界坐标绘制，半径按屏幕像素换算）
  ctx.beginPath();
  ctx.arc(car.x, car.z, px(7 * u), 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.globalAlpha = 0.22;
  ctx.fill();
  ctx.globalAlpha = 1;

  ctx.beginPath();
  ctx.arc(car.x, car.z, px(3.6 * u), 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.strokeStyle = cssVar('--bg') || '#0b0d10';
  ctx.lineWidth = px(1.2 * u);
  ctx.stroke();
}

/* ------------------------------------------------------------- 交互 */
function resize() {
  const wrap = wrapRef.value;
  const canvas = canvasRef.value;
  if (!wrap || !canvas) return;
  cssW = wrap.clientWidth;
  cssH = wrap.clientHeight;
  if (!cssW || !cssH) return;
  dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  canvas.style.width = '100%';
  canvas.style.height = '100%';
  buildGeometry();
  fitView();
  draw();
}

function onWheel(event) {
  if (!bounds) return;
  event.preventDefault();
  const rect = canvasRef.value.getBoundingClientRect();
  const mx = event.clientX - rect.left;
  const my = event.clientY - rect.top;
  const factor = event.deltaY > 0 ? 0.88 : 1.14;
  const nextScale = clamp(view.scale * factor, fitScale * 0.4, fitScale * 40);
  if (nextScale === view.scale) return;

  // 保持鼠标下的世界坐标不动
  const wx = (mx - view.ox) / view.scale + bounds.minX;
  const wz = bounds.maxZ - (my - view.oy) / view.scale;
  view.scale = nextScale;
  view.ox = mx - (wx - bounds.minX) * nextScale;
  view.oy = my - (bounds.maxZ - wz) * nextScale;
  clampView();
  draw();
}

function onPointerDown(event) {
  if (event.button !== 0) return;
  drag = { x: event.clientX, y: event.clientY, ox: view.ox, oy: view.oy };
  canvasRef.value.setPointerCapture?.(event.pointerId);
  canvasRef.value.style.cursor = 'grabbing';
}

function onPointerMove(event) {
  if (!drag) return;
  view.ox = drag.ox + (event.clientX - drag.x);
  view.oy = drag.oy + (event.clientY - drag.y);
  clampView();
  draw();
}

function onPointerUp(event) {
  if (!drag) return;
  drag = null;
  canvasRef.value.releasePointerCapture?.(event.pointerId);
  canvasRef.value.style.cursor = 'grab';
}

function resetView() {
  fitView();
  draw();
}

onMounted(() => {
  ctx = canvasRef.value.getContext('2d');
  resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(wrapRef.value);
  resize();
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  resizeObserver = null;
});

watch(
  () => props.points,
  () => {
    buildGeometry();
    fitView();
    draw();
  }
);

watch(() => props.car, draw);
watch(() => props.themeVersion, draw);
</script>

<template>
  <div class="track-canvas-wrap" ref="wrapRef">
    <canvas
      ref="canvasRef"
      @wheel="onWheel"
      @dblclick="resetView"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
    ></canvas>
    <div class="track-canvas-hint">滚轮缩放 · 拖动平移 · 双击复位</div>
  </div>
</template>

<style scoped>
.track-canvas-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}
.track-canvas-wrap canvas {
  display: block;
  width: 100%;
  height: 100%;
  cursor: grab;
  touch-action: none;
}
.track-canvas-hint {
  position: absolute;
  right: 0.615rem;
  bottom: 0.462rem;
  pointer-events: none;
  font-family: var(--font-mono);
  font-size: 0.654rem;
  color: var(--text-faint);
}
</style>
