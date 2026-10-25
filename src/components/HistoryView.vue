<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
import { fmtLap, fmtDelta, cssVar, clamp, uiScale } from '../telemetry/model.js';
import { themeVersion } from '../telemetry/theme.js';
import { ui } from '../telemetry/store.js';
import { telemetry } from '../telemetry/useTelemetry.js';
import { drawMiniMap, renderCharts, smoothClosedPoints, lapLinePoints, layoutApexOverlay, layoutScaleBar } from '../telemetry/draw.js';
import { listSessions, listLaps, getLap, deleteSession, invalidateLapCache, getTrackGeometry, resetBaseUrl } from '../telemetry/api.js';
import { DETAIL_POINTS, lapFromDetail, modelFromGeometry, modelFromSpline } from '../telemetry/adapt.js';
import { pickApex } from '../telemetry/apex.js';
import { apexesForTrack } from '../telemetry/corners.js';

const props = defineProps({ visible: { type: Boolean, default: true } });

const tracks = ref([]);
const selTrack = ref(null);
const selLap = ref(null);
const selApex = ref(null);
const cursorP = ref(0.42);
const menuOpenId = ref(null);
const scrollEl = ref(null);
const loading = ref(false);
const errorText = ref('');
// 普通变量（非 ref）：只影响图表绘制，切换时不必重建整个分析区
let showPb = true;
let showApex = true;
let showHeat = false;
// 地图上叠加本圈 / PB 圈的走线做对比（默认关闭，保持地图干净）
let showPbLine = false;
// 本圈实线走线：默认显示（选中任意圈都画出本圈跑的路线），可单独关掉
let showLapLine = true;
// 两张图的缩放/平移状态：时序曲线（进度范围）、迷你地图
const chartView = { x0: 0, x1: 1 };
const mapView = { scale: 1, tx: 0, ty: 0, x: 0, y: 0, w: 480, h: 290 };
// 当前圈的走线缓存（平滑后），游标吸附和热力叠加共用
let lineCache = { lp: null, M: null, pts: null };

const N = DETAIL_POINTS;

// 重画图表会替换 #chartWrap 的整段内容，浏览器在替换的那一瞬间把面板的
// scrollHeight 当成 0，于是把滚动位置夹回顶部（点按钮后「跳回最上面」就是这个）。
// 所有重画都套这一层，画完把滚动位置还原。
function keepScroll(fn) {
  const sc = scrollEl.value;
  const top = sc ? sc.scrollTop : 0;
  fn();
  if (!sc) return;
  if (sc.scrollTop !== top) sc.scrollTop = top;
  requestAnimationFrame(() => {
    if (sc.scrollTop !== top) sc.scrollTop = top;
  });
}


// 弯对比面板「跟谁比」的标签：标题里常驻，选中弯心时也保留。
// 现在固定对比 PB 圈；后期支持导入他人数据文件做切换对比时，
// 只要把这里换成对应数据源的名字即可，标题结构不用再动。
const COMPARE_REF_LABEL = 'PB圈';

// 没有 PB 圈时的兜底参考：所有数值按 0 计算。
// 遥测面板照常展示，PB 圈速 / 各项差值都从 0 起算。
function zeroLapRef(n) {
  const zeros = () => new Array(n).fill(0);
  return { no: null, ms: 0, v: zeros(), T: zeros(), P: { th: zeros(), br: zeros(), g: zeros() } };
}

// ACC 会话类型：练习 / 排位赛 / 正赛…，正赛周末按类型拆成两个分组
const SESSION_TYPE_LABELS = {
  practice: '练习',
  qualify: '排位赛',
  race: '正赛',
  hotlap: '热圈',
  timeattack: '计时赛',
  drift: '漂移',
  drag: '直线加速',
  hotstint: '耐力冲刺',
  hotstintsuperpole: '超级杆位',
  unknown: '未标注',
};
const SESSION_TYPE_ORDER = [
  'practice', 'qualify', 'race', 'hotlap', 'timeattack',
  'hotstint', 'hotstintsuperpole', 'drift', 'drag', 'unknown',
];

function sessionTypeLabel(type) {
  return SESSION_TYPE_LABELS[type] || SESSION_TYPE_LABELS.unknown;
}

function sessionTypeRank(type) {
  const index = SESSION_TYPE_ORDER.indexOf(type);
  return index < 0 ? SESSION_TYPE_ORDER.length : index;
}

const headTitle = computed(() =>
  (selTrack.value && selLap.value)
    ? (selTrack.value.name + ' · ' + selLap.value.label)
    : ''
);
const headSub = computed(() => {
  const tk = selTrack.value;
  if (!tk) return '';
  return [tk.car, fmtSessionDate(tk.date)].filter(Boolean).join(' · ');
});

function fmtSessionDate(value, short = false) {
  if (!value) return '';
  const s = String(value).replace('T', ' ');
  if (short) {
    const match = s.match(/^\d{4}-(\d{2}-\d{2})(?: (\d{2}:\d{2}))?/);
    return match ? (match[2] ? match[1] + ' ' + match[2] : match[1]) : s;
  }
  return s.length >= 16 ? s.slice(0, 16) : s;
}
const chipText = computed(() => {
  const lp = selLap.value, tk = selTrack.value;
  if (!lp || !tk) return '';
  if (lp.kind === 'out') return '出场圈 · 不计统计';
  if (lp.kind === 'invalid') return '无效圈 · 四轮出界';
  return (tk.pb && lp.no === tk.pb.no) ? '本赛道最快圈' : '有效圈';
});

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const analysisHtml = computed(() => {
  themeVersion.value;
  if (loading.value) return '<div class="empty">加载中…</div>';
  if (errorText.value) return '<div class="empty">' + escapeHtml(errorText.value) + '</div>';
  if (!selTrack.value || !selLap.value) {
    return '<div class="empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 17l6-6 4 4 7-7"/></svg>左侧点击赛道展开，再点一圈查看分析</div>';
  }
  // 没有 PB 圈也照常展示本圈遥测；PB 相关数值按 0 计算
  const tk = selTrack.value, lp = selLap.value, M = tk.model;
  if (!M || !M.n) return '<div class="empty">数据不完整：缺少赛道几何</div>';
  const pb = tk.pb || zeroLapRef(M.n);
  const d = lp.ms - pb.ms;
  const isPb = !!tk.pb && lp.no === pb.no;
  return '' +
    '<div class="ana-card"><h4>圈速概要' +
        '<span class="toggle-group">' +
          '<button class="pb-toggle' + (showLapLine ? ' on' : '') + '" id="lapLineToggle" type="button">' + (showLapLine ? '隐藏本圈路线' : '显示本圈路线') + '</button>' +
          '<button class="pb-toggle' + (showHeat ? ' on' : '') + '" id="heatToggle" type="button">' + (showHeat ? '隐藏热力' : '显示热力') + '</button>' +
          '<button class="pb-toggle' + (showPbLine ? ' on' : '') + '" id="pbLineToggle" type="button">' + (showPbLine ? '隐藏 PB 路线' : '对比 PB 路线') + '</button>' +
          '<button class="pb-toggle' + (showApex ? ' on' : '') + '" id="apexToggle" type="button">' + (showApex ? '隐藏弯心' : '显示弯心') + '</button>' +
        '</span>' +
        '<span class="heat-legend map-heat-legend" id="heatLegend"' + (showHeat ? '' : ' style="display:none"') + '>' +
          '<span>全油门</span><span class="bar"></span><span>滑行</span><span class="bar"></span><span>重刹</span></span>' +
        '<span class="zoom-hint" title="拖动空白处平移地图">滚轮缩放 · 拖动游标 · 双击重置</span>' +
      '</h4>' +
      '<div class="ana-split">' +
        '<div class="mini-map" id="miniMapWrap"><svg viewBox="0 0 480 290" id="miniMap"></svg>' +
          '<div class="map-readout" id="mapReadout"></div></div>' +
        '<div class="sum-right">' +
          '<div class="kv-grid sum-main">' +
            '<div class="kv lap"><span class="k">圈速</span><span class="v sig">' + fmtLap(lp.ms) + '</span></div>' +
            '<div class="kv"><span class="k">PB</span><span class="v sig">' + fmtLap(pb.ms) + '</span></div>' +
            '<div class="kv"><span class="k">对 PB</span><span class="v ' + (isPb ? '' : 'hot') + '">' + (isPb ? '—' : fmtDelta(d)) + '</span></div>' +
          '</div>' +
          '<div class="sector-box" id="sectorBox">' +
            '<div class="sb-head"><span id="sbTitle">弯对比 · ' + COMPARE_REF_LABEL + '</span></div>' +
            '<div class="kv-grid sector-grid" id="sbGrid"></div>' +
          '</div>' +
        '</div>' +
      '</div>' +
    '</div>' +
    '<div class="ana-card"><h4>时序曲线' +
        '<span class="toggle-group">' +
          '<button class="pb-toggle' + (showPb ? ' on' : '') + '" id="pbToggle" type="button">' + (showPb ? '隐藏 PB' : '显示 PB') + '</button>' +
          '<button class="pb-toggle" id="chartZoomOut" type="button" title="缩小">−</button>' +
          '<button class="pb-toggle" id="chartZoomIn" type="button" title="放大">+</button>' +
          '<button class="pb-toggle" id="chartZoomReset" type="button" title="重置缩放">重置</button>' +
        '</span>' +
        '<span class="zoom-hint">滚轮缩放 · Shift+滚轮平移 · 双击重置</span>' +
        '<span class="zoom-level" id="zoomLevel">1×</span>' +
      '</h4>' +
      '<div class="legend"><span><i style="background:' + cssVar('--chart-line') + '"></i>本圈</span>' +
        '<span class="pb-legend' + (showPb ? '' : ' off') + '"><i style="background:repeating-linear-gradient(90deg,' + cssVar('--text-dim') + ' 0 4px,transparent 4px 7px)"></i>PB 圈</span>' +
      '</div>' +
      '<div class="chart-wrap" id="chartWrap"><div class="cursor-line" id="cursorLine"></div></div>' +
      '<div class="cursor-readout" id="readout"></div></div>';
});

/* --------------------------- data loading --------------------------- */
function normalizeTrack(item) {
  // id 是 session id：同一赛道每次进图 = 一条独立记录（和原来一样）
  const type = item.sessionType || 'unknown';
  return {
    id: item.id,
    trackId: item.trackId,
    name: item.name,
    lenM: item.lenM,
    car: item.car,
    date: item.date,
    lapCount: item.laps,
    pbMs: item.pbMs,
    sessionType: type,
    sessionTypes: (Array.isArray(item.sessionTypes) && item.sessionTypes.length
      ? item.sessionTypes
      : [type]).slice().sort((a, b) => sessionTypeRank(a) - sessionTypeRank(b)),
    laps: [],
    lapsLoaded: false,
    // 一条记录里同时有排位赛和正赛时才分组，否则保持原来的平铺圈速列表
    lapGroups: null,
    pb: null,
    model: null,
  };
}

function buildLapGroups(laps) {
  const types = [];
  for (const lap of laps) {
    const type = lap.sessionType || 'unknown';
    if (!types.includes(type)) types.push(type);
  }
  if (types.length <= 1) return null;

  return types
    .sort((a, b) => sessionTypeRank(a) - sessionTypeRank(b))
    .map((type) => {
      const groupLaps = laps.filter(
        (lap) => (lap.sessionType || 'unknown') === type
      );
      let pbMs = null;
      for (const lap of groupLaps) {
        if (lap.kind === 'ok' && (pbMs == null || lap.ms < pbMs)) pbMs = lap.ms;
      }
      return {
        type,
        label: sessionTypeLabel(type),
        open: true,
        pbMs,
        laps: groupLaps,
      };
    });
}

// 错误文案按第一个全角冒号拆成「标题 / 具体原因」：
// 左栏窄，长端口提示单独一行小字，不会把标题也撑成一大段红字
const errorTitle = computed(() => {
  const text = errorText.value;
  const i = text.indexOf('：');
  return i > 0 ? text.slice(0, i) : text;
});
const errorDetail = computed(() => {
  const text = errorText.value;
  const i = text.indexOf('：');
  return i > 0 ? text.slice(i + 1) : '';
});

async function loadTracks() {
  try {
    const list = await listSessions();
    const prevTrackId = selTrack.value?.id;
    const prevLapNo = selLap.value?.no;
    tracks.value = list.map(normalizeTrack);
    errorText.value = '';
    if (prevTrackId) {
      const tk = tracks.value.find((t) => t.id === prevTrackId);
      if (tk) {
        selTrack.value = tk;
        await loadLapsFor(tk);
        if (prevLapNo != null) {
          const lap = tk.laps.find((l) => l.no === prevLapNo);
          if (lap) await selectLap(lap);
        }
      } else {
        selTrack.value = null;
        selLap.value = null;
      }
    }
  } catch (e) {
    errorText.value = '无法连接后端：' + e.message;
    tracks.value = [];
  }
}

// 重试：窗口可能比 sidecar 先起来，先丢掉缓存的基地址让它重新探测 8008–8019
async function retryLoad() {
  resetBaseUrl();
  loading.value = true;
  await loadTracks();
  loading.value = false;
}

async function loadLapsFor(tk) {
  loading.value = true;
  try {
    const list = await listLaps(tk.id);
    tk.laps = list.map((l) => ({ ...l, label: `L${l.no}` }));
    tk.lapCount = list.length;
    if (list.length) tk.pbMs = list[0].pbMs ?? tk.pbMs;
    tk.lapGroups = buildLapGroups(tk.laps);
    tk.lapsLoaded = true;
    tk.pb = null;
    tk.model = null;
    tk.apexes = undefined;
    errorText.value = '';
  } catch (e) {
    errorText.value = '加载圈速失败：' + e.message;
    tk.laps = [];
    tk.lapGroups = null;
    tk.lapsLoaded = true;
  } finally {
    loading.value = false;
  }
}

async function selectTrack(tk) {
  if (selTrack.value === tk) {
    selTrack.value = null;
    selLap.value = null;
    errorText.value = '';
    return;
  }
  selTrack.value = tk;
  selLap.value = null;
  selApex.value = null;
  cursorP.value = 0.42;
  await loadLapsFor(tk);
}

async function loadGeometry(tk) {
  if (tk.geometry !== undefined) return tk.geometry;
  // 记录 id 是 session id，几何接口要的是 trackId
  if (!tk.trackId) {
    tk.geometry = null;
    return null;
  }
  try {
    tk.geometry = await getTrackGeometry(tk.trackId);
  } catch (e) {
    // 没有 fastlane.ai 或接口不可用 → 回退到实跑生成的折线
    tk.geometry = null;
  }
  return tk.geometry;
}

function buildModel(tk, detail, geometry) {
  // 没有记录圈长时用几何长度兜底，弯心进度换算依赖它
  const lenM = tk.lenM || geometry?.lengthM || null;
  return (
    (geometry ? modelFromGeometry(geometry, N, lenM) : null) ||
    modelFromSpline(detail.track.spline, N, lenM)
  );
}

// 弯心只跟赛道几何有关，每个赛道算一次；没有 fastlane 几何时为空
function ensureApexes(tk, geometry) {
  if (Array.isArray(tk.apexes)) return tk.apexes;
  tk.apexes = geometry?.points?.length
    ? apexesForTrack(tk.trackId || tk.id, geometry)
    : [];
  return tk.apexes;
}

async function selectLap(lap) {
  const tk = selTrack.value;
  if (!tk) return;
  loading.value = true;
  errorText.value = '';
  try {
    const detail = await getLap(tk.id, lap.no);
    const geometry = await loadGeometry(tk);
    ensureApexes(tk, geometry);
    tk.model = buildModel(tk, detail, geometry);
    const adapted = lapFromDetail(detail, N, tk.model);
    Object.assign(lap, adapted);
    selLap.value = lap;

    // pbNo 为空表示这条记录没有有效 PB 圈（例如全部无效/出场圈），
    // 此时不能把当前选中的圈当成 PB，否则赛道卡片会显示成它的圈速。
    let pbLap = null;
    if (lap.pbNo != null) {
      if (lap.pbNo === lap.no) {
        pbLap = lap;
      } else {
        const pbDetail = await getLap(tk.id, lap.pbNo);
        const pbAdapted = lapFromDetail(pbDetail, N, tk.model);
        const inList = tk.laps.find((x) => x.no === lap.pbNo);
        if (inList) {
          Object.assign(inList, pbAdapted);
          pbLap = inList;
        } else {
          pbLap = pbAdapted;
        }
      }
    }
    tk.pb = pbLap;
    selApex.value = null;
    cursorP.value = 0.42;
    chartView.x0 = 0; chartView.x1 = 1;
    mapView.scale = 1; mapView.tx = 0; mapView.ty = 0;
    lineCache = { lp: null, M: null, pts: null };
    nextTick(renderAnalysis);
  } catch (e) {
    errorText.value = '加载圈速数据失败：' + e.message;
    selLap.value = null;
  } finally {
    loading.value = false;
  }
}

async function removeTrack(id) {
  menuOpenId.value = null;
  try {
    await deleteSession(id);
    invalidateLapCache();
    if (selTrack.value?.id === id) {
      selTrack.value = null;
      selLap.value = null;
    }
    await loadTracks();
  } catch (e) {
    errorText.value = e.status === 409 ? '该记录正在监测中，请先停止监测' : ('删除失败：' + e.message);
  }
}

function lapDeltaText(lap) {
  // PB 圈本身不显示差距秒数
  if (lap.kind !== 'ok' || lap.pb || lap.deltaPb == null) return '';
  const abs = Math.abs(lap.deltaPb) / 1000;
  return (lap.deltaPb > 0 ? '+' : '−') + abs.toFixed(3);
}

/* --------------------------- interactions --------------------------- */
function toggleMenu(id) { menuOpenId.value = menuOpenId.value === id ? null : id; }

function trackLength(M) {
  return (M && M.lenM) || selTrack.value?.lenM || 0;
}

function renderSectorBox(lp, pb, M, apex = null) {
  const sc = scrollEl.value; if (!sc) return;
  const sbTitle = sc.querySelector('#sbTitle'), sbGrid = sc.querySelector('#sbGrid');
  const signed = (v, d = 1) => (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(d);
  const cell = (name, value, hot = false) =>
    '<div class="kv"><span class="k">' + name + '</span><span class="v' + (hot ? ' hot' : '') + '">' + value + '</span></div>';

  // 固定 4 格：未选中弯心时数值为 0，避免面板高度随选择跳变
  const len = trackLength(M);
  const p = apex && len ? clamp(apex.distance / len, 0, 0.999999) : null;
  const i = p == null ? -1 : Math.min(M.n - 1, Math.floor(p * M.n));
  const v = i >= 0 ? lp.v[i] * 3.6 : 0;
  const vp = i >= 0 ? pb.v[i] * 3.6 : 0;
  const t = i >= 0 ? lp.T[i] : 0;
  const tp = i >= 0 ? pb.T[i] : 0;

  let html = '';
  html += cell('弯心速度', v.toFixed(1));
  html += cell('弯心速度 DELTA', signed(v - vp), true);
  html += cell('弯心计时', t.toFixed(3));
  html += cell('弯心计时 DELTA', fmtDelta((t - tp) * 1000), true);

  if (sbTitle) {
    // 基准标签（PB圈）常驻，选中弯心时只在后面追加弯号与进度
    sbTitle.textContent = apex
      ? '弯对比 · ' + COMPARE_REF_LABEL + ' · ' + apex.name + ' · 进度 ' + (p * 100).toFixed(1) + '%'
      : '弯对比 · ' + COMPARE_REF_LABEL;
  }
  if (sbGrid) sbGrid.innerHTML = html;
}

// 当前圈的实际走线（已平滑）；同一圈内复用缓存，拖动游标时不再反复分配数组
function activeLine(lp, M) {
  if (lineCache.lp === lp && lineCache.M === M) return lineCache.pts;
  const pts = smoothClosedPoints(lapLinePoints(lp, M), 1);
  lineCache = { lp, M, pts };
  return pts;
}

// 视图坐标 → 赛道上最近的点
function nearestLineIndex(pts, x, y) {
  let best = 0, bestD2 = Infinity;
  for (let i = 0; i < pts.length; i += 1) {
    const dx = pts[i].x - x, dy = pts[i].y - y;
    const d2 = dx * dx + dy * dy;
    if (d2 < bestD2) { bestD2 = d2; best = i; }
  }
  return { index: best, dist: Math.sqrt(bestD2) };
}

// 圈速概要地图上的游标：与 mapZoom 共用同一套平移 / 缩放
function updateMapCursor(p, lp, M) {
  const svg = scrollEl.value?.querySelector('#miniMap');
  const cursor = svg?.querySelector('#mapCursor');
  if (!cursor) return;
  const s = mapView.scale || 1, tx = mapView.tx || 0, ty = mapView.ty || 0;
  const place = (x, y) => {
    cursor.setAttribute('transform', 'translate(' + (tx + x * s).toFixed(2) + ' ' + (ty + y * s).toFixed(2) + ')');
    cursor.setAttribute('opacity', '1');
  };

  // 选中弯心且游标正好停在弯心上时，对齐到弯心圆点的中心
  const apex = showApex ? selApex.value : null;
  if (apex?.name) {
    const len = trackLength(M);
    const apexP = len ? clamp(apex.distance / len, 0, 0.999999) : null;
    if (apexP != null && Math.abs(apexP - p) < 0.0015) {
      const v = M?.toScreen ? M.toScreen(apex.x, apex.z) : { x: apex.x, y: apex.z };
      place(v.x, v.y);
      return;
    }
  }

  const pts = activeLine(lp, M);
  const n = pts.length;
  if (!n) { cursor.setAttribute('opacity', '0'); return; }
  const f = clamp(p, 0, 0.999999) * n;
  const i0 = Math.floor(f) % n;
  const i1 = (i0 + 1) % n;
  const t = f - Math.floor(f);
  place(
    pts[i0].x + (pts[i1].x - pts[i0].x) * t,
    pts[i0].y + (pts[i1].y - pts[i0].y) * t
  );
}

function updateCursor(p, lp, pb, M) {
  const sc = scrollEl.value; if (!sc) return;
  // 在相邻采样点之间插值，鼠标每移动一点读数都会连续变化
  const f = clamp(p, 0, 0.999999) * M.n;
  const i0 = Math.floor(f) % M.n;
  const i1 = (i0 + 1) % M.n;
  const t = f - Math.floor(f);
  const L = (arr) => arr[i0] + (arr[i1] - arr[i0]) * t;
  const kmh = L(lp.v) * 3.6;
  const thr = L(lp.P.th) * 100;
  const brk = L(lp.P.br) * 100;
  const clock = L(lp.T) * 1000;
  const rd = sc.querySelector('#readout');
  if (rd) rd.innerHTML =
    '<span>进度 <b style="color:var(--text)">' + (p * 100).toFixed(1) + '%</b></span>' +
    (M.lenM ? '<span>距离 <b style="color:var(--text)">' + (p * M.lenM / 1000).toFixed(2) + ' km</b></span>' : '') +
    '<span>速度 <b style="color:var(--text)">' + kmh.toFixed(1) + ' km/h</b></span>' +
    '<span>油门 <b style="color:var(--good)">' + thr.toFixed(0) + '%</b></span>' +
    '<span>刹车 <b style="color:var(--hot)">' + brk.toFixed(0) + '%</b></span>' +
    '<span>G <b style="color:var(--text)">' + L(lp.P.g).toFixed(2) + '</b></span>' +
    '<span>计时 <b style="color:var(--text)">' + fmtLap(clock) + '</b></span>';

  // 地图上的游标读数：两行紧凑显示，不遮挡赛道主体
  const mr = sc.querySelector('#mapReadout');
  if (mr) {
    mr.innerHTML =
      '进度 ' + (p * 100).toFixed(1) + '% · ' + kmh.toFixed(0) + ' km/h' +
      (M.lenM ? ' · ' + (p * M.lenM / 1000).toFixed(2) + ' km' : '') +
      '<br>油门 ' + thr.toFixed(0) + '% · 刹车 ' + brk.toFixed(0) + '% · ' + fmtLap(clock);
  }

  const line = sc.querySelector('#cursorLine'), wrap = sc.querySelector('#chartWrap');
  if (line && wrap) {
    const u = uiScale();
    const W = wrap.clientWidth || 420, m = { l: 38 * u, r: 10 * u }, iw = W - m.l - m.r;
    const span = Math.max(1e-6, chartView.x1 - chartView.x0);
    const ratio = clamp((p - chartView.x0) / span, 0, 1);
    line.style.display = 'block';
    line.style.left = (m.l + ratio * iw) + 'px';
  }
  updateMapCursor(p, lp, M);
}

function applyMapTransform(svg) {
  const g = svg && svg.querySelector('#mapZoom');
  if (g) g.setAttribute('transform', `translate(${mapView.tx} ${mapView.ty}) scale(${mapView.scale})`);
  layoutApexOverlay(svg, mapView);
  // 比例尺按真实米数绘制，缩放后重新挑一个整数刻度
  layoutScaleBar(svg, mapView);
  // 游标和弯心标签一样在 mapZoom 外，缩放 / 平移后重新摆位
  if (selLap.value && selTrack.value?.model) {
    updateMapCursor(cursorP.value, selLap.value, selTrack.value.model);
  }
}

// 限制平移，避免把图拖出可视区域
function clampPanAxis(value, zoom, size, origin = 0) {
  if (zoom <= 1) return 0;
  return Math.min(origin * (1 - zoom), Math.max((origin + size) * (1 - zoom), value));
}

function clampMapView(view) {
  view.tx = clampPanAxis(view.tx, view.scale, mapView.w || 480, mapView.x || 0);
  view.ty = clampPanAxis(view.ty, view.scale, mapView.h || 290, mapView.y || 0);
}

function bindMiniMap(svg, lp, pb, M) {
  const pts = activeLine(lp, M);
  const pointCount = pts.length;
  // scrub = 拖动赛道上的游标；pan = 在空白处拖动地图
  let mode = null;
  let drag = null;

  const group = () => svg.querySelector('#mapZoom') || svg;
  const viewPoint = (e) => {
    const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
    return pt.matrixTransform(group().getScreenCTM().inverse());
  };
  const toView = (x, z) => (M.toScreen ? M.toScreen(x, z) : { x, y: z });
  // 命中半径按屏幕像素换算，缩放后仍然容易点中
  const apexAt = (loc, ctm) =>
    pickApex(selTrack.value?.apexes || [], toView, loc.x, loc.y, 20 / Math.max(1e-6, ctm.a || 1));
  const nearAt = (loc, ctm) => {
    const near = nearestLineIndex(pts, loc.x, loc.y);
    return { index: near.index, px: near.dist * (ctm.a || 1) };
  };
  const setCursor = (p) => {
    // 手动拖离弯心后取消弯心选中，弯对比面板回到 PB 圈
    if (selApex.value) { selApex.value = null; refreshApexViews(); }
    cursorP.value = p;
    updateCursor(p, lp, pb, M);
  };

  svg.onpointerdown = (e) => {
    if (e.button !== 0) return;
    mode = null;
    drag = null;
    const ctm = group().getScreenCTM();
    if (!ctm) return;
    const loc = viewPoint(e);
    const hit = apexAt(loc, ctm);
    if (hit) {
      // 点中弯心：选中并把游标移到弯心；时序图已放大时把它带到可视区中间
      const len = trackLength(M);
      const p = len ? clamp(hit.distance / len, 0, 0.999999) : 0;
      selApex.value = hit;
      cursorP.value = p;
      centerChartOn(p);
      refreshApexViews();
      return;
    }
    const near = nearAt(loc, ctm);
    if (pointCount && near.px <= 16) {
      mode = 'scrub';
      svg.__dragged = false;
      try { svg.setPointerCapture(e.pointerId); } catch (e2) { /* ignore */ }
      setCursor(near.index / pointCount);
      return;
    }
    mode = 'pan';
    drag = { x: e.clientX, y: e.clientY, tx: mapView.tx, ty: mapView.ty };
    svg.__dragged = false;
    try { svg.setPointerCapture(e.pointerId); } catch (e2) { /* ignore */ }
  };

  svg.onpointermove = (e) => {
    if (mode === 'scrub') {
      const ctm = group().getScreenCTM();
      if (!ctm || !pointCount) return;
      const near = nearAt(viewPoint(e), ctm);
      setCursor(near.index / pointCount);
      return;
    }
    if (mode === 'pan') {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) svg.__dragged = true;
      if (!svg.__dragged) return;
      const ctm = svg.getScreenCTM();
      mapView.tx = drag.tx + dx / (ctm.a || 1);
      mapView.ty = drag.ty + dy / (ctm.d || 1);
      clampMapView(mapView);
      applyMapTransform(svg);
      return;
    }
    // 悬停提示：弯心可点、赛道可拖游标、空白可平移
    const ctm = group().getScreenCTM();
    if (!ctm) return;
    const loc = viewPoint(e);
    if (apexAt(loc, ctm)) { svg.style.cursor = 'pointer'; return; }
    svg.style.cursor = (pointCount && nearAt(loc, ctm).px <= 16) ? 'crosshair' : 'grab';
  };

  svg.onpointerup = (e) => {
    // 空白处单击（没有拖动）：游标吸附到最近的赛道点
    if (mode === 'pan' && !svg.__dragged && pointCount) {
      const ctm = group().getScreenCTM();
      if (ctm) {
        const near = nearAt(viewPoint(e), ctm);
        if (near.px <= 120) setCursor(near.index / pointCount);
      }
    }
    mode = null;
    drag = null;
    try { svg.releasePointerCapture(e.pointerId); } catch (e2) { /* ignore */ }
  };

  svg.onpointercancel = () => {
    mode = null;
    drag = null;
  };

  svg.onpointerleave = () => {
    if (mode) return;
    svg.style.cursor = 'crosshair';
  };

  svg.onwheel = (e) => {
    e.preventDefault();
    const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
    const loc = pt.matrixTransform(svg.getScreenCTM().inverse());
    const old = mapView.scale;
    const z = clamp(old * (e.deltaY > 0 ? 0.83 : 1.2), 1, 8);
    mapView.tx = loc.x - (loc.x - mapView.tx) * (z / old);
    mapView.ty = loc.y - (loc.y - mapView.ty) * (z / old);
    mapView.scale = z;
    clampMapView(mapView);
    applyMapTransform(svg);
  };
  svg.ondblclick = () => {
    mapView.scale = 1; mapView.tx = 0; mapView.ty = 0;
    applyMapTransform(svg);
  };
  svg.style.cursor = 'crosshair';
}

// 选中弯心时，如果时序图处于放大状态且弯心在可视区外，把它移到中间
function centerChartOn(p) {
  const span = chartView.x1 - chartView.x0;
  if (span >= 0.999) return;
  if (p >= chartView.x0 && p <= chartView.x1) return;
  const nx0 = clamp(p - span / 2, 0, 1 - span);
  chartView.x0 = nx0;
  chartView.x1 = nx0 + span;
}

// 滚轮 / 按钮共用：保持 ratio 位置对应的进度不动，只改可视范围
function zoomCharts(factor, ratio = 0.5) {
  const sc = scrollEl.value;
  const tk = selTrack.value;
  const lp = selLap.value;
  const M = tk?.model;
  const wrap = sc?.querySelector('#chartWrap');
  if (!wrap || !lp || !M) return;
  const span = chartView.x1 - chartView.x0;
  const p = chartView.x0 + ratio * span;
  const newSpan = clamp(span * factor, 0.02, 1);
  const nx0 = clamp(p - ratio * newSpan, 0, 1 - newSpan);
  chartView.x0 = nx0;
  chartView.x1 = nx0 + newSpan;
  keepScroll(() => renderCharts(wrap, lp, tk.pb || zeroLapRef(M.n), M, showPb, chartView));
  cursorP.value = clamp(cursorP.value, chartView.x0, chartView.x1);
  updateCursor(cursorP.value, lp, tk.pb || zeroLapRef(M.n), M);
  syncZoomLabel();
}

// 水平平移：shift + 滚轮 / 触控板横向滑动都能用，不需要先取消缩放
function panCharts(fraction) {
  const sc = scrollEl.value;
  const tk = selTrack.value;
  const lp = selLap.value;
  const M = tk?.model;
  const wrap = sc?.querySelector('#chartWrap');
  if (!wrap || !lp || !M) return;
  const span = chartView.x1 - chartView.x0;
  if (span >= 0.999) return;
  const nx0 = clamp(chartView.x0 + fraction * span, 0, 1 - span);
  chartView.x0 = nx0;
  chartView.x1 = nx0 + span;
  keepScroll(() => renderCharts(wrap, lp, tk.pb || zeroLapRef(M.n), M, showPb, chartView));
  updateCursor(cursorP.value, lp, tk.pb || zeroLapRef(M.n), M);
  syncZoomLabel();
}

// 标题右边的倍率读数：滚轮到底是「缩放」还是「滚动」，一眼能看出来
function syncZoomLabel() {
  const el = scrollEl.value?.querySelector('#zoomLevel');
  if (!el) return;
  const span = chartView.x1 - chartView.x0;
  el.textContent = span >= 0.999 ? '1×' : (1 / span).toFixed(1) + '×';
}

function bindChartZoom(wrap, lp, pb, M) {
  // 边距跟随根字号；每次事件重新读取，窗口缩放后命中位置依然准确
  const margins = () => {
    const u = uiScale();
    return { l: 38 * u, r: 10 * u };
  };
  let drag = null;
  const pAt = (e) => {
    const r = wrap.getBoundingClientRect();
    const m = margins();
    const iw = r.width - m.l - m.r;
    const ratio = clamp((e.clientX - r.left - m.l) / iw, 0, 1);
    return chartView.x0 + ratio * (chartView.x1 - chartView.x0);
  };

  // 绑在整张「时序曲线」卡片上：图例、标题栏上滚动也能缩放，
  // 不需要精确落在绘图区里（卡片下沿常常贴在窗口边缘）
  const wheelTarget = wrap.closest('.ana-card') || wrap;
  const scroller = wrap.closest('.ana-scroll');
  // 只靠 preventDefault 不够：连发滚轮时 Chromium 可能已经在合成线程上把滚动做掉了，
  // 表现就是「一边缩放，整个面板一边往上跑」。这里把滚动位置钉回原处兜底。
  const holdScroll = (keep) => {
    if (!scroller || keep == null) return;
    if (scroller.scrollTop !== keep) scroller.scrollTop = keep;
    requestAnimationFrame(() => {
      if (scroller.scrollTop !== keep) scroller.scrollTop = keep;
    });
  };
  wheelTarget.onwheel = (e) => {
    // 一定要吃掉默认行为，否则面板会跟着一起滚（看起来就像「被识别成滚动」）
    e.preventDefault();
    e.stopPropagation();
    const keep = scroller ? scroller.scrollTop : null;
    // 单次滚动的步长放小一点：高频滚轮/触控板不会一下就冲到底
    const step = e.deltaMode === 1 ? 40 : 1; // 行模式换算成像素量级
    const mag = Math.abs(e.deltaY) / step;
    const factor = Math.min(1.6, Math.max(0.62, 1 + (e.deltaY > 0 ? 1 : -1) * Math.min(0.12, 0.045 * mag)));
    if (e.shiftKey) {
      // shift + 滚轮：左右平移（触控板横向滑动也是这个分支）
      panCharts((e.deltaY > 0 ? 1 : -1) * 0.12);
      holdScroll(keep);
      return;
    }
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
      panCharts((e.deltaX > 0 ? 1 : -1) * 0.12);
      holdScroll(keep);
      return;
    }
    const r = wrap.getBoundingClientRect();
    const m = margins();
    const iw = r.width - m.l - m.r;
    const ratio = clamp((e.clientX - r.left - m.l) / iw, 0, 1);
    zoomCharts(factor, ratio);
    holdScroll(keep);
  };
  wrap.onpointerdown = (e) => {
    if (e.button !== 0) return;
    drag = { x: e.clientX, x0: chartView.x0, span: chartView.x1 - chartView.x0 };
    try { wrap.setPointerCapture(e.pointerId); } catch (e2) { /* ignore */ }
  };
  wrap.onpointermove = (e) => {
    if (drag) {
      const r = wrap.getBoundingClientRect();
      const m = margins();
      const iw = r.width - m.l - m.r;
      const shift = (e.clientX - drag.x) / iw * drag.span;
      const nx0 = clamp(drag.x0 - shift, 0, 1 - drag.span);
      chartView.x0 = nx0; chartView.x1 = nx0 + drag.span;
      keepScroll(() => renderCharts(wrap, lp, pb, M, showPb, chartView));
      updateCursor(cursorP.value, lp, pb, M);
      syncZoomLabel();
    } else {
      cursorP.value = pAt(e);
      updateCursor(cursorP.value, lp, pb, M);
    }
  };
  wrap.onpointerup = (e) => { drag = null; try { wrap.releasePointerCapture(e.pointerId); } catch (e2) { /* ignore */ } };
  wrap.ondblclick = () => {
    chartView.x0 = 0; chartView.x1 = 1;
    keepScroll(() => renderCharts(wrap, lp, pb, M, showPb, chartView));
    cursorP.value = 0.42;
    updateCursor(cursorP.value, lp, pb, M);
    syncZoomLabel();
  };
  wrap.style.cursor = 'crosshair';
}

function apexMapOpts() {
  return {
    apexes: selTrack.value?.apexes || [],
    selected: selApex.value?.name || null,
    show: showApex,
  };
}

function miniMapOpts(lp) {
  const tk = selTrack.value;
  return {
    ...apexMapOpts(),
    heat: showHeat ? lp : null,
    // 本圈实线走线默认就画：选中任意一圈都同时显示赛道图 + 本圈跑的路线。
    // 「本圈路线」开关可单独关掉；关掉就是干净的赛道图，不再退画 AI 中心线。
    lapLine: showLapLine ? lp?.line : null,
    // AI 中心线只在「本圈没有走线数据可画」时兜底（老记录），
    // 用户主动隐藏本圈路线时不画，避免按钮写着隐藏却冒出另一条线。
    centerLine: showLapLine && !lp?.line,
    pbLine: showPbLine ? tk?.pb?.line : null,
  };
}

// 时序图的弯心状态挂在 chartView 上，缩放 / 平移重绘时不会丢
function syncApexView(M) {
  chartView.apexes = selTrack.value?.apexes || [];
  chartView.apexOn = showApex;
  chartView.selApex = selApex.value?.name || null;
  chartView.lenM = (M && M.lenM) || selTrack.value?.lenM || null;
}

function refreshApexViews() {
  const sc = scrollEl.value;
  const tk = selTrack.value;
  const lp = selLap.value;
  const M = tk?.model;
  if (!sc || !lp || !M) return;
  const pb = tk.pb || zeroLapRef(M.n);
  const mini = sc.querySelector('#miniMap'), wrap = sc.querySelector('#chartWrap');
  syncApexView(M);
  if (mini) {
    const vb = drawMiniMap(mini, M, miniMapOpts(lp));
    if (vb) { mapView.x = vb.x; mapView.y = vb.y; mapView.w = vb.w; mapView.h = vb.h; }
    applyMapTransform(mini);
  }
  if (wrap) keepScroll(() => renderCharts(wrap, lp, pb, M, showPb, chartView));
  renderSectorBox(lp, pb, M, selApex.value);
  updateCursor(cursorP.value, lp, pb, M);
}

function renderAnalysis() {
  const sc = scrollEl.value; if (!sc) return;
  const mini = sc.querySelector('#miniMap'), wrap = sc.querySelector('#chartWrap');
  if (!selTrack.value || !selLap.value || !selTrack.value.model || !mini || !wrap) return;
  const M = selTrack.value.model;
  // 没有 PB 圈时用 0 值参考兜底，面板照常渲染
  const lp = selLap.value, pb = selTrack.value.pb || zeroLapRef(M.n);
  syncApexView(M);
  const vb = drawMiniMap(mini, M, miniMapOpts(lp));
  if (vb) { mapView.x = vb.x; mapView.y = vb.y; mapView.w = vb.w; mapView.h = vb.h; }
  applyMapTransform(mini);
  bindMiniMap(mini, lp, pb, M);
  renderSectorBox(lp, pb, M, selApex.value);
  keepScroll(() => renderCharts(wrap, lp, pb, M, showPb, chartView));
  bindChartZoom(wrap, lp, pb, M);
  syncZoomLabel();
  updateCursor(cursorP.value, lp, pb, M);

  const pbToggle = sc.querySelector('#pbToggle');
  if (pbToggle) {
    pbToggle.onclick = () => {
      showPb = !showPb;
      pbToggle.classList.toggle('on', showPb);
      pbToggle.textContent = showPb ? '隐藏 PB' : '显示 PB';
      const legend = sc.querySelector('.pb-legend');
      if (legend) legend.classList.toggle('off', !showPb);
      keepScroll(() => renderCharts(wrap, lp, pb, M, showPb, chartView));
    };
  }

  const zoomIn = sc.querySelector('#chartZoomIn');
  if (zoomIn) zoomIn.onclick = () => zoomCharts(0.7, 0.5);
  const zoomOut = sc.querySelector('#chartZoomOut');
  if (zoomOut) zoomOut.onclick = () => zoomCharts(1.45, 0.5);
  const zoomReset = sc.querySelector('#chartZoomReset');
  if (zoomReset) zoomReset.onclick = () => {
    chartView.x0 = 0;
    chartView.x1 = 1;
    zoomCharts(1, 0.5);
  };

  const apexToggle = sc.querySelector('#apexToggle');
  if (apexToggle) {
    apexToggle.onclick = () => {
      showApex = !showApex;
      apexToggle.classList.toggle('on', showApex);
      apexToggle.textContent = showApex ? '隐藏弯心' : '显示弯心';
      refreshApexViews();
    };
  }

  // 踏板热力合并进圈速概要：按钮只在原地图上叠加/移除着色
  const lapLineToggle = sc.querySelector('#lapLineToggle');
  if (lapLineToggle) {
    lapLineToggle.onclick = () => {
      showLapLine = !showLapLine;
      lapLineToggle.classList.toggle('on', showLapLine);
      lapLineToggle.textContent = showLapLine ? '隐藏本圈路线' : '显示本圈路线';
      refreshApexViews();
    };
  }

  const pbLineToggle = sc.querySelector('#pbLineToggle');
  if (pbLineToggle) {
    pbLineToggle.onclick = () => {
      showPbLine = !showPbLine;
      pbLineToggle.classList.toggle('on', showPbLine);
      pbLineToggle.textContent = showPbLine ? '隐藏 PB 路线' : '对比 PB 路线';
      refreshApexViews();
    };
  }

  const heatToggle = sc.querySelector('#heatToggle');
  if (heatToggle) {
    heatToggle.onclick = () => {
      showHeat = !showHeat;
      heatToggle.classList.toggle('on', showHeat);
      heatToggle.textContent = showHeat ? '隐藏热力' : '显示热力';
      const legend = sc.querySelector('#heatLegend');
      if (legend) legend.style.display = showHeat ? '' : 'none';
      refreshApexViews();
    };
  }

  wrap.onmouseleave = function () { const cl = sc.querySelector('#cursorLine'); if (cl) cl.style.display = 'none'; };
}

watch(analysisHtml, () => nextTick(renderAnalysis));
watch(() => props.visible, (v) => {
  if (v) {
    loadTracks().then(() => nextTick(renderAnalysis));
  }
});
watch(() => telemetry.historyRevision, () => {
  if (props.visible) {
    invalidateLapCache();
    loadTracks();
  }
});

// 窗口尺寸变化后重绘时序曲线（SVG 固定 viewBox，不重绘会保持旧宽度）
let resizeTimer = null;
function onResize() {
  if (!props.visible || !selLap.value) return;
  if (resizeTimer !== null) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    resizeTimer = null;
    if (props.visible) refreshApexViews();
  }, 140);
}

onMounted(() => {
  loadTracks();
  document.addEventListener('click', onDocClick);
  window.addEventListener('resize', onResize);
});
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick);
  window.removeEventListener('resize', onResize);
  if (resizeTimer !== null) clearTimeout(resizeTimer);
});
function onDocClick(e) {
  if (!e.target.closest('.track-kebab') && !e.target.closest('.track-menu')) menuOpenId.value = null;
}
</script>

<template>
  <section class="view" id="view-history">
    <!-- left: track accordion -->
    <div class="col col-tracks">
      <div class="list-head"><h3>赛道</h3><span class="n">历史记录 {{ tracks.length }}</span></div>
      <div class="list-scroll">
        <div class="track-group" v-for="tk in tracks" :key="tk.id" :class="{ open: selTrack === tk }">
          <div class="track-row">
            <button class="track-item" :class="{ active: selTrack === tk }" @click="selectTrack(tk)">
              <span class="chev"></span>
              <div class="track-body">
                <div class="track-name">
                  <span v-if="tk.id === ui.liveSessionId && ui.monOn" class="live-dot"></span>
                  <span class="track-title">{{ tk.name }}</span>
                  <span class="track-types">
                    <span v-for="t in tk.sessionTypes" :key="t"
                      class="session-type" :class="'stype-' + t">{{ sessionTypeLabel(t) }}</span>
                  </span>
                </div>
                <div class="track-meta">
                  <b>{{ tk.lapCount }} 圈</b>
                  <span>PB {{ tk.pb ? fmtLap(tk.pb.ms) : (tk.pbMs ? fmtLap(tk.pbMs) : '—') }}</span>
                  <span :title="fmtSessionDate(tk.date)">{{ fmtSessionDate(tk.date, true) }}</span>
                </div>
              </div>
            </button>
            <button class="track-kebab" :class="{ open: menuOpenId === tk.id }" @click.stop="toggleMenu(tk.id)" aria-label="更多操作" title="更多操作">⋮</button>
            <div class="track-menu" :class="{ show: menuOpenId === tk.id }">
              <button class="danger" @click="removeTrack(tk.id)">删除此记录</button>
            </div>
          </div>

          <template v-if="selTrack === tk">
            <!-- 同一条记录里出现排位赛 + 正赛时，再按会话类型做一次下拉分类 -->
            <div v-if="tk.lapGroups" class="lap-groups">
              <div class="lap-group" v-for="g in tk.lapGroups" :key="g.type">
                <button class="lap-group-head" :class="{ open: g.open }" @click="g.open = !g.open">
                  <span class="sg-chev"></span>
                  <span class="sg-label" :class="'stype-' + g.type">{{ g.label }}</span>
                  <span class="sg-count">{{ g.laps.length }} 圈</span>
                  <span class="sg-pb" v-if="g.pbMs">PB {{ fmtLap(g.pbMs) }}</span>
                </button>
                <div class="lap-list" v-show="g.open">
                  <button class="lap-item" v-for="l in g.laps" :key="l.no"
                    :class="[l.kind !== 'ok' ? 'invalid' : '', l.pb ? 'best' : '', selLap === l ? 'active' : '']"
                    @click="selectLap(l)">
                    <span class="lap-no">L{{ l.no }}</span>
                    <span class="lap-time">{{ fmtLap(l.ms) }}</span>
                    <span class="mono lap-delta" v-if="lapDeltaText(l)">{{ lapDeltaText(l) }}</span>
                    <span class="tag" v-if="l.kind !== 'ok'" :class="l.kind === 'out' ? 'out' : 'bad'">{{ l.kind === 'out' ? '出场' : '无效' }}</span>
                    <span class="tag pb" v-else-if="l.pb">PB</span>
                  </button>
                </div>
              </div>
            </div>

            <!-- 单一会话类型：保持原来的平铺圈速列表 -->
            <div v-else class="lap-list">
              <div v-if="!tk.lapsLoaded" class="lap-loading">加载中…</div>
              <div v-else-if="!tk.laps.length" class="lap-loading">暂无有效圈</div>
              <button class="lap-item" v-for="l in tk.laps" :key="l.no"
                :class="[l.kind !== 'ok' ? 'invalid' : '', l.pb ? 'best' : '', selLap === l ? 'active' : '']"
                @click="selectLap(l)">
                <span class="lap-no">L{{ l.no }}</span>
                <span class="lap-time">{{ fmtLap(l.ms) }}</span>
                <span class="mono lap-delta" v-if="lapDeltaText(l)">{{ lapDeltaText(l) }}</span>
                <span class="tag" v-if="l.kind !== 'ok'" :class="l.kind === 'out' ? 'out' : 'bad'">{{ l.kind === 'out' ? '出场' : '无效' }}</span>
                <span class="tag pb" v-else-if="l.pb">PB</span>
              </button>
            </div>
          </template>
        </div>
        <div v-if="!tracks.length && !errorText" class="list-empty">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
            <path d="M4 5.5h16v13H4z"/>
            <path d="M4 9.5h16"/>
            <path d="M8 13.5h4"/>
          </svg>
          <div class="le-title">暂无历史数据</div>
          <div class="le-sub">开始监测并<wbr>完成一圈后自动入库</div>
        </div>
        <div v-if="errorText" class="list-empty is-error">
          <div class="le-head">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true">
              <path d="M12 9v4.6"/>
              <path d="M10.6 3.9 2.7 17.6a1.9 1.9 0 0 0 1.7 2.9h15.2a1.9 1.9 0 0 0 1.7-2.9L13.4 3.9a1.9 1.9 0 0 0-2.8 0Z"/>
              <circle cx="12" cy="16.9" r=".95" fill="currentColor" stroke="none"/>
            </svg>
            <span class="le-title">{{ errorTitle }}</span>
          </div>
          <div v-if="errorDetail" class="le-sub">{{ errorDetail }}</div>
          <button class="le-retry" type="button" @click="retryLoad">重试</button>
        </div>
      </div>
    </div>

    <!-- right: analysis -->
    <div class="col col-ana">
      <div class="ana-head" v-show="selTrack && selLap">
        <div>
          <div class="t">{{ headTitle }}</div>
          <div class="s">{{ headSub }}</div>
        </div>
        <span class="chip">{{ chipText }}</span>
      </div>
      <div class="ana-scroll" ref="scrollEl" v-html="analysisHtml"></div>
    </div>
  </section>
</template>
