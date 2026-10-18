<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { cssVar, fmtDelta, fmtLap, reduceMotion, tireClass } from '../telemetry/model.js';
import { themeVersion } from '../telemetry/theme.js';
import { ui } from '../telemetry/store.js';
import { start as startTelemetry, stop as stopTelemetry, telemetry, gearText } from '../telemetry/useTelemetry.js';
import { modelFromSpline } from '../telemetry/adapt.js';
import { getBaseUrl } from '../telemetry/api.js';

function wsLabel() {
  return getBaseUrl().replace(/^http/, 'ws');
}

/* ----------------------------- config ----------------------------- */
const RPM_SEGS = 20;
const zones = Array.from({ length: RPM_SEGS }, (_, i) => i < 12 ? 's-good' : (i < 16 ? 's-warn' : 's-hot'));

/* ----------------------------- session state ----------------------------- */
const userStarted = ref(false);
const liveShown = computed(() => userStarted.value);
// 没有真实数据时由各 computed 的 0 值兜底（速度 0、转速 0、踏板 0 等）
const frame = computed(() => telemetry.frame);
const session = computed(() => telemetry.session);

const monOn = computed(() => userStarted.value);
const startOn = userStarted;
const iconPlay = computed(() => !userStarted.value);
const startLabel = computed(() => userStarted.value ? '停止监测' : '开始监测');
const startSub = computed(() => {
  if (telemetry.status === 'streaming') return telemetry.fps + ' Hz';
  if (telemetry.status === 'connecting') return '连接中…';
  if (userStarted.value) return '默认数据';
  return '';
});

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
const rpmText = computed(() =>
  String(rpm.value).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' rpm'
);
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

const monDotGood = computed(() => telemetry.status === 'streaming' && !!frame.value);
const monTitle = computed(() => {
  if (telemetry.status === 'streaming' && session.value) return '监测中 · ' + session.value.track.name;
  if (telemetry.status === 'error') return telemetry.error || '连接出错 · 显示默认数据';
  if (telemetry.status === 'connecting') return '正在连接… · 显示默认数据';
  if (userStarted.value) return '等待数据 · 显示默认数据';
  return '未开始监测';
});
const carText = computed(() => session.value?.car?.name || '—');

const datasetText = computed(() => {
  if (!userStarted.value) return '—';
  if (!session.value) return '默认数据 · 0 Hz';
  return telemetry.lapsThisSession + ' 圈 · ' + telemetry.fps + ' Hz';
});

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

function buildMapSvg(M) {
  if (!M || !M.n) return '';
  let html = '<path class="map-base" d="' + M.d + '"/><path class="map-inline" d="' + M.d + '"/>';
  const step = Math.max(1, Math.floor(M.n / 10));
  for (let k = 0; k < M.n; k += step) {
    const p = M.pts[k];
    html += '<circle class="map-corner" cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="2"/>';
  }
  const start = M.pts[0];
  html += '<line x1="' + (start.x - 8) + '" y1="' + (start.y - 8) + '" x2="' + (start.x + 8) + '" y2="' + (start.y + 8) + '" stroke="' + cssVar('--signal-text') + '" stroke-width="2.5"/>';
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

watch(progress, (p) => updateMapDot(p));

watch(session, (s) => {
  if (s?.track?.spline?.length) {
    mapModel.value = modelFromSpline(s.track.spline, 160, s.track.lengthM);
    nextTick(() => updateMapDot(progress.value));
  } else {
    mapModel.value = null;
  }
}, { immediate: true });

/* ----------------------------- actions / ui ----------------------------- */
let retryTimer = null;

function stopRetry() {
  if (retryTimer !== null) {
    clearInterval(retryTimer);
    retryTimer = null;
  }
}

function startRetry() {
  stopRetry();
  // 游戏/后端稍后才就绪时自动重试，真实数据一到就替换默认值
  retryTimer = setInterval(() => {
    if (!userStarted.value) return;
    if (telemetry.session || telemetry.status === 'streaming' || telemetry.status === 'connecting') return;
    startTelemetry();
  }, 3000);
}

function setMon(on) {
  userStarted.value = on;
  if (on) {
    // 无真实数据时展示 0 值默认面板，先清掉上一次会话的残值
    tires.forEach((t) => { t.t = 0; t.p = 0; t.b = 0; });
    mapModel.value = null;
    startTelemetry();
    startRetry();
  } else {
    stopTelemetry();
    stopRetry();
  }
}

onBeforeUnmount(stopRetry);

watch(
  [() => telemetry.status, () => telemetry.fps, session, userStarted],
  () => {
    ui.monOn = userStarted.value;
    if (telemetry.status === 'streaming' && session.value) {
      ui.statLeft = '已连接 · ' + telemetry.fps + ' Hz · ' + session.value.track.name;
      ui.liveTrackId = session.value.track.id;
      ui.liveSessionId = session.value.sessionId ?? null;
    } else if (!userStarted.value) {
      ui.statLeft = '未连接 · ' + wsLabel();
      ui.liveTrackId = null;
      ui.liveSessionId = null;
    } else if (telemetry.status === 'error') {
      ui.statLeft = '未连接 · ' + (telemetry.error || '后端错误') + ' · 默认数据';
      ui.liveTrackId = null;
      ui.liveSessionId = null;
    } else if (telemetry.status === 'connecting') {
      ui.statLeft = '连接中 · ' + wsLabel() + ' · 默认数据';
      ui.liveTrackId = null;
      ui.liveSessionId = null;
    } else {
      ui.statLeft = '默认数据 · 等待游戏数据';
      ui.liveTrackId = null;
      ui.liveSessionId = null;
    }
  },
  { immediate: true }
);
</script>

<template>
  <section class="view" id="view-monitor">
    <div class="mon-top">
      <div class="panel mon-status">
        <div class="mon-line"><span class="sdot" :class="monDotGood ? 'good' : (userStarted ? 'warn' : 'off')"></span><span class="mon-title">{{ monTitle }}</span></div>
        <div class="mon-line"><span style="color:var(--text-faint)">车辆</span><span>{{ carText }}</span></div>
        <div class="mon-line"><span style="color:var(--text-faint)">数据集</span><span>{{ datasetText }}</span></div>
      </div>
      <div class="mon-action">
        <button class="start-btn" :class="{ on: startOn }" @click="setMon(!monOn)" aria-label="开始监测">
          <svg v-if="iconPlay" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13l11-6.5L8 5.5z" /></svg>
          <svg v-else viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="1" /></svg>
          <span class="sb-col"><span class="sb-t">{{ startLabel }}</span><span class="sb-s mono" v-show="startSub">{{ startSub }}</span></span>
        </button>
      </div>
    </div>

    <div class="live-wrap" v-show="liveShown">
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
            <div class="panel">
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
              <svg class="map-svg" viewBox="0 0 480 290" ref="liveMapSvg" v-html="liveMapStatic"></svg>
            </div>
            <div class="map-progress"><i :style="{ width: mapProg + '%' }"></i></div>
            <div class="map-meta"><span>{{ mapNext }}</span><span>{{ mapPct }}</span></div>
          </div>
        </div>
      </div>
    </div>
    <div class="live-off" v-show="!liveShown">
      {{ telemetry.status === 'error' ? telemetry.error : '未监测 · 点击右侧「开始监测」后显示实时数据' }}
    </div>
  </section>
</template>
