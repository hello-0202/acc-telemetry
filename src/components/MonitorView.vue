<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { fmtDelta, fmtLap, gripText, rainText, reduceMotion, tireClass } from '../telemetry/model.js';
import { themeVersion } from '../telemetry/theme.js';
import { ui } from '../telemetry/store.js';
import { start as startTelemetry, stop as stopTelemetry, telemetry, gearText } from '../telemetry/useTelemetry.js';
import { loadPrefs, prefs } from '../telemetry/prefs.js';
import { modelFromSpline } from '../telemetry/adapt.js';
import { getBaseUrl, getTrackGeometry } from '../telemetry/api.js';
import TrackCanvasMap from './TrackCanvasMap.vue';

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

// 服务连接指示灯（绿=已连接 / 红=断开）
// 启动宽限期内（ui.booting）后端可能还没起来，任何「断开」都先按「连接中」显示
const dotClass = computed(() => {
  if (telemetry.status === 'streaming' && frame.value) return 'good';
  if (telemetry.status === 'error' && !ui.booting) return 'hot';
  if (telemetry.status === 'connecting' || telemetry.status === 'waiting' || ui.booting || (userStarted.value && !frame.value)) return 'warn';
  return 'off';
});
const connText = computed(() => {
  if (telemetry.status === 'streaming' && frame.value) return '已连接';
  if (telemetry.status === 'error' && !ui.booting) return '连接断开';
  if (telemetry.status === 'connecting' || ui.booting) return '连接中…';
  if (telemetry.status === 'waiting') return '等待游戏';
  if (userStarted.value) return '等待数据';
  return '未连接';
});
const connTitle = computed(() => {
  // 启动中不暴露「连接被拒」之类的瞬时错误，容易误导
  if (ui.booting && telemetry.status !== 'streaming') return '正在连接本地后端 · ' + wsLabel();
  if (telemetry.status === 'error') return telemetry.error || '后端连接已断开';
  if (telemetry.status === 'streaming') return '本地后端已连接 · ' + wsLabel();
  if (telemetry.status === 'connecting') return '正在连接 ' + wsLabel();
  if (telemetry.status === 'waiting') return '等待进入赛道 · 进入后自动开始记录 · ' + wsLabel();
  return '未连接 · ' + wsLabel();
});
const trackText = computed(() => session.value?.track?.name || '—');
const carText = computed(() => session.value?.car?.name || '—');

const datasetText = computed(() => {
  if (!userStarted.value) return '—';
  if (!session.value) return '默认数据';
  return telemetry.lapsThisSession + ' 圈 · ' + telemetry.fps + ' Hz';
});

// 场景信息随帧下发；没有实时帧时保持占位符，避免误报 0 度
const airTempText = computed(() => {
  const v = Number(frame.value?.airTemp);
  return frame.value && Number.isFinite(v) ? v.toFixed(1) + ' °C' : '—';
});
const roadTempText = computed(() => {
  const v = Number(frame.value?.roadTemp);
  return frame.value && Number.isFinite(v) ? v.toFixed(1) + ' °C' : '—';
});
const weatherText = computed(() =>
  frame.value ? rainText(frame.value.rainIntensity) : '—'
);
const gripTextValue = computed(() =>
  frame.value ? gripText(frame.value.trackGrip) : '—'
);

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
  // 底图只画路面粗线（.map-base），不再叠加中线虚线（按需求去掉底图里的线）
  let html = '<path class="map-base" d="' + M.d + '"/>';
  const step = Math.max(1, Math.floor(M.n / 10));
  for (let k = 0; k < M.n; k += step) {
    const p = M.pts[k];
    html += '<circle class="map-corner" cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="2"/>';
  }
  // 起跑线：垂直于赛道方向的横线；样式与圈速概要（.map-sf line）一致——墨色（黑）单线
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

/* ----------------------------- actions / ui ----------------------------- */
// 监督循环间隔：游戏/后端稍后才就绪时自动接入，不必用户再点一次
const SUPERVISE_MS = 3000;
let retryTimer = null;

function stopRetry() {
  if (retryTimer !== null) {
    clearInterval(retryTimer);
    retryTimer = null;
  }
}

/**
 * 只要还处于「监测」状态（手动点开 或 启动时自动开始），就在条件满足时自动接入记录。
 * 用于覆盖：应用启动时游戏还没开、退出赛道回菜单、游戏重启后重新进赛道。
 * 游戏没开 / 后端离线时不发 start——否则每 3 秒刷一次 GAME_NOT_FOUND。
 */
function tickSupervisor() {
  if (!userStarted.value) return;
  if (telemetry.status === 'streaming' || telemetry.status === 'connecting') return;
  // 后端还没确认在线（启动中 / 未知）时先别发：连接必然失败，
  // 还会在界面上留下一条「无法连接本地后端」。等 /health 探到在线再接。
  if (ui.backendOnline !== true) return;
  if (ui.gameRunning === false) return;
  startTelemetry();
}

function startRetry() {
  stopRetry();
  retryTimer = setInterval(tickSupervisor, SUPERVISE_MS);
}

function setMon(on, { auto = false } = {}) {
  userStarted.value = on;
  // 只有「用户亲手点开」才需要在游戏没开时弹提示；自动开始属于后台待命
  ui.manualStart = on && !auto;
  if (on) {
    // 无真实数据时展示 0 值默认面板，先清掉上一次会话的残值
    tires.forEach((t) => { t.t = 0; t.p = 0; t.b = 0; });
    mapModel.value = null;
    // 自动开始时后端多半还没起来（sidecar 冷启动 6 秒左右）：
    // 交给监督循环在探测到在线后再接入，避免开局就留下连接失败的错误状态。
    // 手动点击则立即尝试，给用户即时反馈。
    if (!auto || ui.backendOnline === true) startTelemetry();
    startRetry();
  } else {
    stopTelemetry();
    stopRetry();
  }
}

onMounted(async () => {
  // 配置读回后再决定是否自动开始（读回前 prefs 是默认值，不可信）
  await loadPrefs();
  if (!prefs.autoStart || userStarted.value) return;
  // 自动开始：即使游戏还没进赛道也先置位，由监督循环在就绪后自动接入
  setMon(true, { auto: true });
});

onBeforeUnmount(stopRetry);

// 后端刚探到在线的一瞬间就接入，不必等监督循环下一个 3 秒节拍
watch(() => ui.backendOnline, (online) => {
  if (online === true) tickSupervisor();
});

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
    } else if (ui.booting) {
      // 启动宽限期：后端可能还没起来，别把瞬时失败当故障写进状态栏
      ui.statLeft = '连接中 · ' + wsLabel() + ' · 默认数据';
      ui.liveTrackId = null;
      ui.liveSessionId = null;
    } else if (telemetry.status === 'error') {
      ui.statLeft = '未连接 · ' + (telemetry.error || '后端错误') + ' · 默认数据';
      ui.liveTrackId = null;
      ui.liveSessionId = null;
    } else if (telemetry.status === 'waiting') {
      ui.statLeft = '等待进入赛道 · ' + wsLabel() + ' · 默认数据';
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
        <div class="mon-col">
          <div class="mon-line">
            <span class="sdot" :class="dotClass" :title="connTitle"></span>
            <span class="mon-title">{{ connText }}</span>
          </div>
          <div class="mon-line"><span class="k">赛道</span><span class="v" :title="trackText">{{ trackText }}</span></div>
          <div class="mon-line"><span class="k">车辆</span><span class="v" :title="carText">{{ carText }}</span></div>
          <div class="mon-line"><span class="k">数据集</span><span class="v">{{ datasetText }}</span></div>
        </div>
        <div class="mon-col">
          <div class="mon-line"><span class="k">环境温度</span><span class="v">{{ airTempText }}</span></div>
          <div class="mon-line"><span class="k">路面温度</span><span class="v">{{ roadTempText }}</span></div>
          <div class="mon-line"><span class="k">天气</span><span class="v">{{ weatherText }}</span></div>
          <div class="mon-line"><span class="k">路面</span><span class="v">{{ gripTextValue }}</span></div>
        </div>
      </div>
      <div class="mon-action">
        <button class="start-btn" :class="{ on: startOn }" @click="setMon(!monOn)" aria-label="开始监测">
          <svg v-if="iconPlay" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13l11-6.5L8 5.5z" /></svg>
          <svg v-else viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="1" /></svg>
          <span class="sb-col"><span class="sb-t">{{ startLabel }}</span></span>
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
    </div>
    <div class="live-off" v-show="!liveShown">
      {{ ui.booting && telemetry.status !== 'streaming' ? '正在连接本地后端…'
        : telemetry.status === 'error' ? telemetry.error
        : telemetry.status === 'waiting' ? '已开启自动监测 · 进入赛道后自动开始记录'
        : '未监测 · 点击右侧「开始监测」后显示实时数据' }}
    </div>
  </section>
</template>
