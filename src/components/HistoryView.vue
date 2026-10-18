<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
import { fmtLap, fmtDelta, sectorsOf, cssVar, clamp } from '../telemetry/model.js';
import { themeVersion } from '../telemetry/theme.js';
import { ui } from '../telemetry/store.js';
import { telemetry } from '../telemetry/useTelemetry.js';
import { drawMiniMap, renderCharts, drawHeatmap, smoothClosedPoints } from '../telemetry/draw.js';
import { listSessions, listLaps, getLap, deleteSession, invalidateLapCache } from '../telemetry/api.js';
import { DETAIL_POINTS, lapFromDetail, modelFromSpline } from '../telemetry/adapt.js';

const props = defineProps({ visible: { type: Boolean, default: true } });

const tracks = ref([]);
const selTrack = ref(null);
const selLap = ref(null);
const selCorner = ref(null);
const cursorP = ref(0.42);
const menuOpenId = ref(null);
const scrollEl = ref(null);
const loading = ref(false);
const errorText = ref('');
// 普通变量（非 ref）：只影响图表绘制，切换时不必重建整个分析区
let showPb = true;
// 三张图的缩放/平移状态：时序曲线（进度范围）、迷你地图、热力图
const chartView = { x0: 0, x1: 1 };
const mapView = { scale: 1, tx: 0, ty: 0 };
const heatView = { scale: 1, tx: 0, ty: 0 };

const N = DETAIL_POINTS;

const headTitle = computed(() => (selTrack.value && selLap.value) ? (selTrack.value.name + ' · ' + selLap.value.label) : '');
const headSub = computed(() => {
  const tk = selTrack.value;
  if (!tk) return '';
  const len = tk.lenM ? (tk.lenM / 1000).toFixed(1) + ' km' : '';
  return [tk.car, fmtSessionDate(tk.date), len].filter(Boolean).join(' · ');
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
  const tk = selTrack.value, lp = selLap.value, pb = tk.pb, M = tk.model;
  if (!pb || !M || !M.n) return '<div class="empty">数据不完整：缺少 PB 圈或赛道几何</div>';
  const d = lp.ms - pb.ms;
  const isPb = lp.no === pb.no;
  return '' +
    '<div class="ana-card"><h4>圈速概要</h4>' +
      '<div class="ana-split">' +
        '<div class="mini-map" id="miniMapWrap"><svg viewBox="0 0 480 290" id="miniMap"></svg>' +
          '<div class="map-hint" id="mapHint">点击选弯 · 滚轮缩放 · 双击重置</div></div>' +
        '<div class="sum-right">' +
          '<div class="kv-grid sum-main">' +
            '<div class="kv lap"><span class="k">圈速</span><span class="v sig">' + fmtLap(lp.ms) + '</span></div>' +
            '<div class="kv"><span class="k">对 PB</span><span class="v ' + (isPb ? '' : 'hot') + '">' + (isPb ? '—' : fmtDelta(d)) + '</span></div>' +
            '<div class="kv"><span class="k">PB</span><span class="v sig">' + fmtLap(pb.ms) + '</span></div>' +
          '</div>' +
          '<div class="sector-box" id="sectorBox">' +
            '<div class="sb-head"><span id="sbTitle">弯对比 · 整圈</span></div>' +
            '<div class="kv-grid sector-grid" id="sbGrid"></div>' +
          '</div>' +
        '</div>' +
      '</div>' +
    '</div>' +
    '<div class="ana-card"><h4>时序曲线</h4>' +
      '<div class="legend"><span><i style="background:' + cssVar('--signal-text') + '"></i>本圈</span>' +
        '<span class="pb-legend' + (showPb ? '' : ' off') + '"><i style="background:repeating-linear-gradient(90deg,' + cssVar('--text-dim') + ' 0 4px,transparent 4px 7px)"></i>PB 圈</span>' +
        '<span class="zoom-hint">滚轮缩放 · 拖动平移 · 双击重置</span>' +
        '<button class="pb-toggle' + (showPb ? ' on' : '') + '" id="pbToggle" type="button">' + (showPb ? '隐藏 PB' : '显示 PB') + '</button>' +
      '</div>' +
      '<div class="chart-wrap" id="chartWrap"><div class="cursor-line" id="cursorLine"></div></div>' +
      '<div class="cursor-readout" id="readout" style="display:flex;gap:14px;flex-wrap:wrap;font-family:' + cssVar('--font-mono') + ';font-size:10px;color:var(--text-dim);margin-top:7px"></div></div>' +
    '<div class="ana-card"><h4>踏板热力图</h4><canvas id="heatCanvas"></canvas>' +
      '<div class="heat-tip" id="heatTip"></div>' +
      '<div class="heat-legend"><span>全油门</span><span class="bar"></span><span>滑行</span><span class="bar"></span><span>重刹</span><span class="zoom-hint">滚轮缩放 · 拖动平移 · 双击重置</span></div></div>';
});

/* --------------------------- data loading --------------------------- */
function normalizeTrack(item) {
  // id 是 session id：同一赛道每次进图 = 一条独立记录
  return {
    id: item.id,
    trackId: item.trackId,
    name: item.name,
    lenM: item.lenM,
    car: item.car,
    date: item.date,
    lapCount: item.laps,
    pbMs: item.pbMs,
    laps: [],
    pb: null,
    model: null,
  };
}

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

async function loadLapsFor(tk) {
  loading.value = true;
  try {
    const list = await listLaps(tk.id);
    tk.laps = list.map((l) => ({ ...l, label: `L${l.no}` }));
    tk.lapCount = list.length;
    if (list.length) tk.pbMs = list[0].pbMs ?? tk.pbMs;
    tk.pb = null;
    tk.model = null;
    errorText.value = '';
  } catch (e) {
    errorText.value = '加载圈速失败：' + e.message;
    tk.laps = [];
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
  selCorner.value = null;
  cursorP.value = 0.42;
  await loadLapsFor(tk);
}

async function selectLap(lap) {
  const tk = selTrack.value;
  if (!tk) return;
  loading.value = true;
  errorText.value = '';
  try {
    const detail = await getLap(tk.id, lap.no);
    tk.model = modelFromSpline(detail.track.spline, N, tk.lenM);
    const adapted = lapFromDetail(detail, N);
    Object.assign(lap, adapted);
    selLap.value = lap;

    let pbLap = lap;
    if (lap.pbNo != null && lap.pbNo !== lap.no) {
      const pbDetail = await getLap(tk.id, lap.pbNo);
      const pbAdapted = lapFromDetail(pbDetail, N);
      const inList = tk.laps.find((x) => x.no === lap.pbNo);
      if (inList) {
        Object.assign(inList, pbAdapted);
        pbLap = inList;
      } else {
        pbLap = pbAdapted;
      }
    }
    tk.pb = pbLap;
    selCorner.value = null;
    cursorP.value = 0.42;
    chartView.x0 = 0; chartView.x1 = 1;
    mapView.scale = 1; mapView.tx = 0; mapView.ty = 0;
    heatView.scale = 1; heatView.tx = 0; heatView.ty = 0;
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

function lapDeltaText(lap, tk) {
  if (lap.kind !== 'ok') return '';
  // PB 圈本身不显示差距秒数
  if (lap.pb || (tk.pb && tk.pb.no === lap.no)) return '';
  if (lap.deltaPb != null) {
    const abs = Math.abs(lap.deltaPb) / 1000;
    return (lap.deltaPb > 0 ? '+' : '−') + abs.toFixed(3);
  }
  if (tk.pb && tk.pb.no !== lap.no) {
    const diff = lap.ms - tk.pb.ms;
    return (diff > 0 ? '+' : '−') + (Math.abs(diff) / 1000).toFixed(3);
  }
  return '';
}

/* --------------------------- interactions --------------------------- */
function toggleMenu(id) { menuOpenId.value = menuOpenId.value === id ? null : id; }

function renderSectorBox(lp, pb, M, cor) {
  const sc = scrollEl.value; if (!sc) return;
  const sL = sectorsOf(lp.T, M.n), sP = sectorsOf(pb.T, M.n);
  const rows = [['S1', sL[0], sL[0] - sP[0]], ['S2', sL[1], sL[1] - sP[1]], ['S3', sL[2], sL[2] - sP[2]]];
  let title, ptDelta, ptLabel;
  if (cor == null) { title = '弯对比 · 整圈'; ptDelta = lp.ms - pb.ms; ptLabel = '整圈 Δ'; }
  else {
    const sec = cor < 1 / 3 ? 'S1' : (cor < 2 / 3 ? 'S2' : 'S3');
    title = '弯对比 · ' + sec + ' · 进度 ' + (cor * 100).toFixed(0) + '%';
    const i = Math.floor(cor * M.n) % M.n; ptDelta = (lp.T[i] - pb.T[i]) * 1000; ptLabel = '游标点 Δ';
  }
  const sbTitle = sc.querySelector('#sbTitle'), sbGrid = sc.querySelector('#sbGrid');
  if (sbTitle) sbTitle.textContent = title;
  if (sbGrid) {
    let html = '';
    rows.forEach(r => {
      html += '<div class="kv"><span class="k">' + r[0] + '</span><span class="v">' + r[1].toFixed(3) + '</span></div>' +
        '<div class="kv"><span class="k">' + r[0] + ' Δ</span><span class="v hot">' + fmtDelta(r[2] * 1000) + '</span></div>';
    });
    html += '<div class="kv"><span class="k">' + ptLabel + '</span><span class="v hot">' + fmtDelta(ptDelta) + '</span></div>';
    sbGrid.innerHTML = html;
  }
}

function updateCursor(p, lp, pb, M) {
  const sc = scrollEl.value; if (!sc) return;
  // 在相邻采样点之间插值，鼠标每移动一点读数都会连续变化
  const f = clamp(p, 0, 0.999999) * M.n;
  const i0 = Math.floor(f) % M.n;
  const i1 = (i0 + 1) % M.n;
  const t = f - Math.floor(f);
  const L = (arr) => arr[i0] + (arr[i1] - arr[i0]) * t;
  const i = i0;
  const rd = sc.querySelector('#readout');
  if (rd) rd.innerHTML =
    '<span>进度 <b style="color:var(--text)">' + (p * 100).toFixed(1) + '%</b></span>' +
    '<span>速度 <b style="color:var(--text)">' + (L(lp.v) * 3.6).toFixed(1) + ' km/h</b></span>' +
    '<span>油门 <b style="color:var(--good)">' + (L(lp.P.th) * 100).toFixed(0) + '%</b></span>' +
    '<span>刹车 <b style="color:var(--hot)">' + (L(lp.P.br) * 100).toFixed(0) + '%</b></span>' +
    '<span>G <b style="color:var(--text)">' + L(lp.P.g).toFixed(2) + '</b></span>' +
    '<span>计时 <b style="color:var(--text)">' + (fmtLap(L(lp.T) * 1000)) + '</b></span>';
  const line = sc.querySelector('#cursorLine'), wrap = sc.querySelector('#chartWrap');
  if (line && wrap) {
    const W = wrap.clientWidth || 420, m = { l: 38, r: 10 }, iw = W - m.l - m.r;
    const span = Math.max(1e-6, chartView.x1 - chartView.x0);
    const ratio = clamp((p - chartView.x0) / span, 0, 1);
    line.style.display = 'block';
    line.style.left = (m.l + ratio * iw) + 'px';
  }
  const dot = sc.querySelector('#miniDot');
  if (dot) { const pt = M.pts[i]; dot.setAttribute('cx', pt.x.toFixed(1)); dot.setAttribute('cy', pt.y.toFixed(1)); }
}

function bindHeatmap(cv, lp, pb, M) {
  const sc = scrollEl.value;
  if (!sc) return;
  const tip = sc.querySelector('#heatTip');
  // 与 drawHeatmap 使用同一套平滑后的几何，保证命中位置和画面一致
  const pts = smoothClosedPoints(M.pts, 1);

  const geom = () => {
    const rect = cv.getBoundingClientRect();
    const w = rect.width || cv.clientWidth || 420;
    const h = rect.height || cv.clientHeight || Math.round(w * 0.62);
    const s = Math.min((w - 24) / 480, (h - 20) / 290);
    return { rect, w, h, s, ox: (w - 480 * s) / 2, oy: (h - 290 * s) / 2 };
  };
  const localPoint = (e) => {
    const g = geom();
    return {
      ...g,
      mx: ((e.clientX - g.rect.left - g.ox) / g.s - heatView.tx) / heatView.scale,
      my: ((e.clientY - g.rect.top - g.oy) / g.s - heatView.ty) / heatView.scale,
    };
  };

  let drag = null;
  cv.onpointerdown = (e) => {
    if (e.button !== 0) return;
    drag = { x: e.clientX, y: e.clientY, tx: heatView.tx, ty: heatView.ty };
    try { cv.setPointerCapture(e.pointerId); } catch (e2) { /* ignore */ }
  };
  cv.onpointermove = (e) => {
    if (drag) {
      const { s } = geom();
      const k = s * heatView.scale;
      heatView.tx = drag.tx + (e.clientX - drag.x) / k;
      heatView.ty = drag.ty + (e.clientY - drag.y) / k;
      clampMapView(heatView);
      drawHeatmap(cv, lp, M, heatView);
      if (tip) tip.style.display = 'none';
      return;
    }

    const g = localPoint(e);
    let best = 0, bestD2 = Infinity;
    for (let i = 0; i < M.n; i += 1) {
      const dx = pts[i].x - g.mx, dy = pts[i].y - g.my;
      const d2 = dx * dx + dy * dy;
      if (d2 < bestD2) { bestD2 = d2; best = i; }
    }
    // 离赛道太远时不显示，避免出现与画面无关的数据
    if (Math.sqrt(bestD2) * g.s * heatView.scale > 24) {
      if (tip) tip.style.display = 'none';
      return;
    }

    const p = best / M.n;
    const i = best % M.n;
    const thr = Math.round(lp.P.th[i] * 100);
    const brk = Math.round(lp.P.br[i] * 100);
    if (tip) {
      tip.innerHTML =
        '进度 ' + (p * 100).toFixed(1) + '%<br>' +
        '油门 ' + thr + '% · 刹车 ' + brk + '%<br>' +
        '速度 ' + (lp.v[i] * 3.6).toFixed(0) + ' km/h';
      tip.style.display = 'block';
      const x = Math.min(window.innerWidth - 140, e.clientX + 14);
      const y = Math.min(window.innerHeight - 70, e.clientY + 14);
      tip.style.left = x + 'px';
      tip.style.top = y + 'px';
    }

    // 同步时序曲线的游标与读数
    cursorP.value = p;
    updateCursor(p, lp, pb, M);
  };
  cv.onpointerup = (e) => { drag = null; try { cv.releasePointerCapture(e.pointerId); } catch (e2) { /* ignore */ } };
  cv.onwheel = (e) => {
    e.preventDefault();
    const g = geom();
    const localX = (e.clientX - g.rect.left - g.ox) / g.s;
    const localY = (e.clientY - g.rect.top - g.oy) / g.s;
    const old = heatView.scale;
    const z = clamp(old * (e.deltaY > 0 ? 0.83 : 1.2), 1, 8);
    heatView.tx = localX - (localX - heatView.tx) * (z / old);
    heatView.ty = localY - (localY - heatView.ty) * (z / old);
    heatView.scale = z;
    clampMapView(heatView);
    drawHeatmap(cv, lp, M, heatView);
    if (tip) tip.style.display = 'none';
  };
  cv.ondblclick = () => {
    heatView.scale = 1; heatView.tx = 0; heatView.ty = 0;
    drawHeatmap(cv, lp, M, heatView);
  };
  cv.onpointerleave = () => {
    if (tip) tip.style.display = 'none';
    const line = sc.querySelector('#cursorLine');
    if (line) line.style.display = 'none';
  };
  cv.style.cursor = 'crosshair';
}

function applyMapTransform(svg) {
  const g = svg && svg.querySelector('#mapZoom');
  if (g) g.setAttribute('transform', `translate(${mapView.tx} ${mapView.ty}) scale(${mapView.scale})`);
}

// 限制平移，避免把图拖出可视区域
function clampPanAxis(value, zoom, size) {
  const content = size * zoom;
  if (content <= size) return (size - content) / 2;
  return Math.min(0, Math.max(size - content, value));
}

function clampMapView(view) {
  view.tx = clampPanAxis(view.tx, view.scale, 480);
  view.ty = clampPanAxis(view.ty, view.scale, 290);
}

function bindMiniMap(svg, lp, pb, M) {
  let drag = null;
  const group = () => svg.querySelector('#mapZoom') || svg;

  svg.onclick = function (e) {
    if (svg.__dragged) { svg.__dragged = false; return; }
    const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
    const loc = pt.matrixTransform(group().getScreenCTM().inverse());
    let best = 0, bd = 1e9;
    for (let i = 0; i < M.n; i++) { const dx = M.pts[i].x - loc.x, dy = M.pts[i].y - loc.y, dd = dx * dx + dy * dy; if (dd < bd) { bd = dd; best = i; } }
    selCorner.value = best / M.n;
    const dot = svg.querySelector('#miniDot');
    if (dot) { dot.setAttribute('cx', M.pts[best].x.toFixed(1)); dot.setAttribute('cy', M.pts[best].y.toFixed(1)); }
    renderSectorBox(lp, pb, M, selCorner.value);
    cursorP.value = selCorner.value; updateCursor(cursorP.value, lp, pb, M);
  };

  svg.onpointerdown = (e) => {
    if (e.button !== 0) return;
    drag = { x: e.clientX, y: e.clientY, tx: mapView.tx, ty: mapView.ty };
    svg.__dragged = false;
    try { svg.setPointerCapture(e.pointerId); } catch (e2) { /* ignore */ }
  };
  svg.onpointermove = (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) svg.__dragged = true;
    if (!svg.__dragged) return;
    const ctm = svg.getScreenCTM();
    mapView.tx = drag.tx + dx / (ctm.a || 1);
    mapView.ty = drag.ty + dy / (ctm.d || 1);
    clampMapView(mapView);
    applyMapTransform(svg);
  };
  svg.onpointerup = (e) => { drag = null; try { svg.releasePointerCapture(e.pointerId); } catch (e2) { /* ignore */ } };
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
  svg.style.cursor = 'grab';
}

function bindChartZoom(wrap, lp, pb, M) {
  const m = { l: 38, r: 10 };
  let drag = null;
  const pAt = (e) => {
    const r = wrap.getBoundingClientRect();
    const iw = r.width - m.l - m.r;
    const ratio = clamp((e.clientX - r.left - m.l) / iw, 0, 1);
    return chartView.x0 + ratio * (chartView.x1 - chartView.x0);
  };

  wrap.onwheel = (e) => {
    e.preventDefault();
    const r = wrap.getBoundingClientRect();
    const iw = r.width - m.l - m.r;
    const ratio = clamp((e.clientX - r.left - m.l) / iw, 0, 1);
    const span = chartView.x1 - chartView.x0;
    const p = chartView.x0 + ratio * span;
    const factor = e.deltaY > 0 ? 1.25 : 0.8;
    const newSpan = clamp(span * factor, 0.02, 1);
    const nx0 = clamp(p - ratio * newSpan, 0, 1 - newSpan);
    chartView.x0 = nx0; chartView.x1 = nx0 + newSpan;
    renderCharts(wrap, lp, pb, M, showPb, chartView);
    updateCursor(clamp(p, chartView.x0, chartView.x1), lp, pb, M);
  };
  wrap.onpointerdown = (e) => {
    if (e.button !== 0) return;
    drag = { x: e.clientX, x0: chartView.x0, span: chartView.x1 - chartView.x0 };
    try { wrap.setPointerCapture(e.pointerId); } catch (e2) { /* ignore */ }
  };
  wrap.onpointermove = (e) => {
    if (drag) {
      const r = wrap.getBoundingClientRect();
      const iw = r.width - m.l - m.r;
      const shift = (e.clientX - drag.x) / iw * drag.span;
      const nx0 = clamp(drag.x0 - shift, 0, 1 - drag.span);
      chartView.x0 = nx0; chartView.x1 = nx0 + drag.span;
      renderCharts(wrap, lp, pb, M, showPb, chartView);
    } else {
      updateCursor(pAt(e), lp, pb, M);
    }
  };
  wrap.onpointerup = (e) => { drag = null; try { wrap.releasePointerCapture(e.pointerId); } catch (e2) { /* ignore */ } };
  wrap.ondblclick = () => {
    chartView.x0 = 0; chartView.x1 = 1;
    renderCharts(wrap, lp, pb, M, showPb, chartView);
    updateCursor(0.42, lp, pb, M);
  };
  wrap.style.cursor = 'crosshair';
}

function renderAnalysis() {
  const sc = scrollEl.value; if (!sc) return;
  const mini = sc.querySelector('#miniMap'), wrap = sc.querySelector('#chartWrap');
  const cv = sc.querySelector('#heatCanvas');
  if (!selTrack.value || !selLap.value || !selTrack.value.pb || !selTrack.value.model || !mini || !wrap || !cv) return;
  const lp = selLap.value, pb = selTrack.value.pb, M = selTrack.value.model;
  drawMiniMap(mini, M);
  applyMapTransform(mini);
  bindMiniMap(mini, lp, pb, M);
  renderSectorBox(lp, pb, M, selCorner.value);
  renderCharts(wrap, lp, pb, M, showPb, chartView);
  drawHeatmap(cv, lp, M, heatView);
  bindHeatmap(cv, lp, pb, M);
  bindChartZoom(wrap, lp, pb, M);
  updateCursor(cursorP.value, lp, pb, M);

  const pbToggle = sc.querySelector('#pbToggle');
  if (pbToggle) {
    pbToggle.onclick = () => {
      showPb = !showPb;
      pbToggle.classList.toggle('on', showPb);
      pbToggle.textContent = showPb ? '隐藏 PB' : '显示 PB';
      const legend = sc.querySelector('.pb-legend');
      if (legend) legend.classList.toggle('off', !showPb);
      renderCharts(wrap, lp, pb, M, showPb, chartView);
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

onMounted(() => {
  loadTracks();
  document.addEventListener('click', onDocClick);
});
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick);
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
                  <span v-if="tk.id === ui.liveSessionId && ui.monOn" class="live-dot"></span>{{ tk.name }}
                </div>
                <div class="track-meta">
                  <b>{{ tk.lapCount }} 圈</b><span>PB {{ tk.pb ? fmtLap(tk.pb.ms) : (tk.pbMs ? fmtLap(tk.pbMs) : '—') }}</span><span :title="fmtSessionDate(tk.date)">{{ fmtSessionDate(tk.date, true) }}</span>
                </div>
              </div>
            </button>
            <button class="track-kebab" :class="{ open: menuOpenId === tk.id }" @click.stop="toggleMenu(tk.id)" aria-label="更多操作" title="更多操作">⋮</button>
            <div class="track-menu" :class="{ show: menuOpenId === tk.id }">
              <button class="danger" @click="removeTrack(tk.id)">删除此记录</button>
            </div>
          </div>
          <div class="lap-list" v-show="selTrack === tk">
            <button class="lap-item" v-for="l in tk.laps" :key="l.no"
              :class="[l.kind !== 'ok' ? 'invalid' : '', (l.pb || (tk.pb && tk.pb.no === l.no)) ? 'best' : '', selLap === l ? 'active' : '']"
              @click="selectLap(l)">
              <span class="lap-no">L{{ l.no }}</span>
              <span class="lap-time">{{ fmtLap(l.ms) }}</span>
              <span class="mono" v-if="lapDeltaText(l, tk)" style="font-size:10px;color:var(--hot)">{{ lapDeltaText(l, tk) }}</span>
              <span class="tag" v-if="l.kind !== 'ok'" :class="l.kind === 'out' ? 'out' : 'bad'">{{ l.kind === 'out' ? '出场' : '无效' }}</span>
              <span class="tag pb" v-else-if="l.pb || (tk.pb && tk.pb.no === l.no)">PB</span>
            </button>
          </div>
        </div>
        <div v-if="!tracks.length && !errorText" class="list-empty">暂无历史数据 · 开始监测并完成一圈后自动入库</div>
        <div v-if="errorText" class="list-empty">{{ errorText }}</div>
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
