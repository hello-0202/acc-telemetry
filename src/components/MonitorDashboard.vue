<script setup>
// 内置主面板的「数据展示区」：速度/档位/转速/圈速/踏板/轮胎 + 实时赛道图。
//
// 这里是从 MonitorView 抽出来的纯展示层（P4 壳化）：只读 telemetry，不发命令、不管开始/停止，
// 也不碰状态栏。外壳（状态条、开始按钮、immersive 兜底提示）留在 MonitorView。
// 数据全部直接取 `telemetry.frame` / `telemetry.session` 这两个模块级单例，省掉 props 传递，
// 也避免「DOM 更新了但数据没跟上」这类跨层时序问题。
import { computed, nextTick, reactive, ref, watch } from 'vue';

import { fmtDelta, fmtLap, reduceMotion, tireClass } from '../telemetry/model.js';
import { themeVersion } from '../telemetry/theme.js';
import { gearText, telemetry } from '../telemetry/useTelemetry.js';
import { modelFromSpline } from '../telemetry/adapt.js';
import { getTrackGeometry } from '../telemetry/api.js';
import TrackCanvasMap from './TrackCanvasMap.vue';

const RPM_SEGS = 20;
const zones = Array.from({ length: RPM_SEGS }, (_, i) => (i < 12 ? 's-good' : i < 16 ? 's-warn' : 's-hot'));

const frame = computed(() => telemetry.frame);
const session = computed(() => telemetry.session);

const spd = computed(() => (frame.value ? Math.round(frame.value.speed) : 0));
const gear = computed(() => {
  const g = frame.value?.gear;
  return g == null ? '-' : gearText(g);
});

const maxRpm = computed(() => {
  const live = frame.value?.currentMaxRpm;
  const spec = session.value?.carSpec?.maxRpm;
  return live || spec || 7600;
});
const rpm = computed(() => frame.value?.rpm ?? 0);
const rpmText = computed(() => String(rpm.value).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' rpm');
const rpmLit = computed(() => Math.round((rpm.value / Math.max(1, maxRpm.value)) * RPM_SEGS));
const rpmFlash = computed(() => !reduceMotion && !!frame.value && rpm.value > maxRpm.value * 0.96);

const thr = computed(() => Math.round((frame.value?.throttle ?? 0) * 100));
const brk = computed(() => Math.round((frame.value?.brake ?? 0) * 100));

// 方向盘：ACC steerAngle 为 -1..1，显示为 ±450°（满舵）
const steerAngle = computed(() => (frame.value?.steer ?? 0) * 450);
const steerVal = computed(() => Math.round(steerAngle.value));
const steerFillLeft = computed(() => {
  const sh = (steerAngle.value / 450) * 50;
  return 50 + Math.min(0, sh);
});
const steerFillWidth = computed(() => Math.abs((steerAngle.value / 450) * 50));

const dlta = computed(() => {
  if (!frame.value) return '+0.000';
  return telemetry.deltaMs == null ? '—' : fmtDelta(telemetry.deltaMs);
});
const dltaHot = computed(() => telemetry.deltaMs != null && telemetry.deltaMs >= 0);
const curLap = computed(() => fmtLap(frame.value?.lapTime ?? 0));
const lastLap = computed(() => (telemetry.lastLap ? fmtLap(telemetry.lastLap.ms) : '--:--.---'));
const pbLap = computed(() => fmtLap(session.value?.pbMs ?? null));

/* ----------------------------- tires ----------------------------- */
const tires = reactive([
  { c: 'FL', t: 0, p: 0, b: 0 },
  { c: 'FR', t: 0, p: 0, b: 0 },
  { c: 'RL', t: 0, p: 0, b: 0 },
  { c: 'RR', t: 0, p: 0, b: 0 },
]);
function bw(value) {
  return Math.max(3, Math.min(100, (value / 800) * 100)).toFixed(0) + '%';
}
watch(frame, (f) => {
  if (!f || !Array.isArray(f.wheels)) return;
  f.wheels.forEach((wheel, i) => {
    const tire = tires[i];
    if (!tire) return;
    tire.c = wheel.corner || tire.c;
    tire.t = wheel.temp ?? tire.t;
    tire.p = wheel.pressure ?? tire.p;
    tire.b = wheel.brakeTemp ?? tire.b;
  });
});

/* ----------------------------- mini map ----------------------------- */
const liveMapSvg = ref(null);
const mapModel = ref(null);
const trackGeometry = ref(null);

const canvasCar = computed(() => {
  const f = frame.value;
  if (!f || f.x == null || f.z == null) return null;
  return { x: f.x, z: f.z };
});

async function loadTrackGeometry(s) {
  trackGeometry.value = null;
  const trackId = s?.track?.id;
  if (!trackId || s?.track?.geometryAvailable === false) return;
  try {
    const geometry = await getTrackGeometry(trackId);
    if (session.value?.track?.id === trackId) {
      trackGeometry.value = geometry;
    }
  } catch (e) {
    // 没有 fastlane.ai 或接口不可用 → 回退到内置/录制 SVG 地图
  }
}

function buildMapSvg(M) {
  if (!M || !M.n) return '';
  // 底图只画路面粗线（.map-base），不再叠加中线虚线
  let html = '<path class="map-base" d="' + M.d + '"/>';
  const step = Math.max(1, Math.floor(M.n / 10));
  for (let k = 0; k < M.n; k += step) {
    const p = M.pts[k];
    html += '<circle class="map-corner" cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="2"/>';
  }
  // 起跑线：垂直于赛道方向的横线，样式与圈速概要一致
  const start = M.pts[0];
  const next = M.pts[1] || start;
  const dx = next.x - start.x, dy = next.y - start.y;
  const len = Math.hypot(dx, dy) || 1;
  const half = 2.75;
  const nx = (-dy / len) * half, ny = (dx / len) * half;
  html += '<line class="map-start" x1="' + (start.x - nx) + '" y1="' + (start.y - ny) + '" x2="' + (start.x + nx) + '" y2="' + (start.y + ny) + '"/>';
  html += '<circle class="map-dot-glow" id="mapGlow" cx="' + start.x.toFixed(1) + '" cy="' + start.y.toFixed(1) + '" r="9"/>';
  html += '<circle class="map-dot" id="mapDot" cx="' + start.x.toFixed(1) + '" cy="' + start.y.toFixed(1) + '" r="3.8"/>';
  return html;
}

const liveMapStatic = computed(() => {
  themeVersion.value;
  return buildMapSvg(mapModel.value);
});

function updateMapDot(p) {
  const M = mapModel.value;
  const svg = liveMapSvg.value;
  if (!M || !M.n || !svg) return;
  const i = Math.min(M.n - 1, Math.max(0, Math.floor(p * M.n)));
  const pt = M.pts[i];
  const dot = svg.querySelector('#mapDot');
  const glow = svg.querySelector('#mapGlow');
  if (dot) { dot.setAttribute('cx', pt.x.toFixed(1)); dot.setAttribute('cy', pt.y.toFixed(1)); }
  if (glow) { glow.setAttribute('cx', pt.x.toFixed(1)); glow.setAttribute('cy', pt.y.toFixed(1)); }
}

const progress = computed(() => frame.value?.progress ?? 0);
const mapProg = computed(() => progress.value * 100);
const mapPct = computed(() => Math.round(progress.value * 100) + '%');
const mapNext = computed(() => {
  const p = progress.value;
  const sector = p < 1 / 3 ? 'S1' : (p < 2 / 3 ? 'S2' : 'S3');
  const len = session.value?.track?.lengthM;
  if (!len) return sector + ' · 进度 ' + (p * 100).toFixed(0) + '%';
  return sector + ' · 已完成 ' + ((p * len) / 1000).toFixed(2) + ' / ' + (len / 1000).toFixed(1) + ' km';
});

watch(progress, (p) => updateMapDot(p));

watch(session, (s) => {
  if (s?.track?.spline?.length) {
    mapModel.value = modelFromSpline(s.track.spline, 160, s.track.lengthM);
    nextTick(() => updateMapDot(progress.value));
  } else {
    mapModel.value = null;
  }
  loadTrackGeometry(s);
}, { immediate: true });

// 外壳在开始监测时会清掉上一场的残值；这里跟着清，避免「先显示上一场的胎温」
function reset() {
  tires.forEach((t) => { t.t = 0; t.p = 0; t.b = 0; });
  mapModel.value = null;
}

defineExpose({ reset });
</script>

<template>
  <div class="live">
    <div class="live-main">
      <div class="panel live-hero">
        <div class="spd"><span class="big-num">{{ spd }}</span><span class="unit">KM/H</span></div>
        <div class="gear"><div class="gear-num">{{ gear }}</div><div class="gear-lbl">档位</div></div>
        <div class="dlta"><div class="dlta-lbl">DELTA · 对 PB</div><div class="dlta-val mono" :class="telemetry.deltaMs == null ? '' : (dltaHot ? 'hot' : 'good')">{{ dlta }}</div></div>
        <div class="rpm" style="grid-area:rpm">
          <div class="rpm-bar" :class="{ flash: rpmFlash }">
            <div v-for="i in RPM_SEGS" :key="i" class="rpm-seg" :class="[(i - 1) < rpmLit ? 'on' : '', zones[i - 1]]"></div>
          </div>
          <div class="rpm-meta"><span>{{ rpmText }}</span><span>红线 {{ maxRpm }}</span></div>
        </div>
        <div class="lap-row" style="grid-area:lap">
          <div><span class="lbl">本圈</span><span class="val mono">{{ curLap }}</span></div>
          <div><span class="lbl">上一圈</span><span class="val mono">{{ lastLap }}</span></div>
          <div><span class="lbl">本赛道 PB</span><span class="val mono signal-text">{{ pbLap }}</span></div>
        </div>
      </div>
      <div class="live-lower">
        <div class="panel">
          <div class="pedal-row"><span class="pedal-name">油门</span><div class="pedal-track"><div class="pedal-fill throttle" :style="{ width: thr + '%' }"></div></div><span class="pedal-val">{{ thr }}%</span></div>
          <div class="pedal-row"><span class="pedal-name">刹车</span><div class="pedal-track"><div class="pedal-fill brake" :style="{ width: brk + '%' }"></div></div><span class="pedal-val">{{ brk }}%</span></div>
          <div class="pedal-row"><span class="pedal-name">方向盘</span><div class="steer-track"><span class="steer-center"></span><div class="steer-fill" :style="{ left: steerFillLeft + '%', width: steerFillWidth + '%' }"></div></div><span class="pedal-val">{{ steerVal }}°</span></div>
        </div>
        <div class="panel tire-panel">
          <div class="tyre-layout">
            <div class="tire-grid">
              <div class="tire-card" v-for="t in tires" :key="t.c">
                <div class="tire-head"><span class="tire-corner">{{ t.c }}</span><span class="tire-pres">{{ t.p.toFixed(1) }}</span></div>
                <div class="tire-temp"><span class="t-block" :class="tireClass(t.t)"></span><span class="t-val">{{ t.t.toFixed(0) }}°</span></div>
                <div class="tire-brake"><span>{{ t.b.toFixed(0) }}°</span><div class="b-bar"><i :class="t.b > 640 ? 'hot' : (t.b < 380 ? 'cold' : '')" :style="{ width: bw(t.b) }"></i></div></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="live-side">
      <div class="panel map-panel">
        <div class="map-fill">
          <TrackCanvasMap
            v-if="trackGeometry?.points?.length"
            :points="trackGeometry.points"
            :car="canvasCar"
            :theme-version="themeVersion"
          />
          <svg v-else class="map-svg" viewBox="0 0 480 290" ref="liveMapSvg" v-html="liveMapStatic"></svg>
        </div>
        <div class="map-progress"><i :style="{ width: mapProg + '%' }"></i></div>
        <div class="map-meta"><span>{{ mapNext }}</span><span>{{ mapPct }}</span></div>
      </div>
    </div>
  </div>
</template>
