// ============================================================================
// Drawing helpers — build SVG/Canvas for the history analysis panel.
// All colors are resolved via cssVar() so they render correctly inside SVG
// attributes (presentation attributes don't support var()).
// ============================================================================
import { cssVar, clamp, lerp, mixHex, fmtLap, fmtDelta, smoothLoop, uiScale } from './model.js';
import { mapInk } from './apex.js';

const c = (n) => cssVar(n);

export function panelSVG(cfg, W, H) {
  // 图表内部的边距 / 字号跟随根字号缩放，窗口放大后曲线不会缩在角落
  const u = uiScale();
  // 顶部留出标题行：标题在绘图区上方，和右侧的刻度文字彻底分开
  const m = { l: 38 * u, r: 10 * u, t: 18 * u, b: 2 * u }, iw = W - m.l - m.r;
  const BAND = 13 * u, yBot = H - BAND - u, ih = yBot - m.t;
  const x0 = clamp(cfg.x0 ?? 0, 0, 1), x1 = clamp(cfg.x1 ?? 1, 0, 1);
  const span = Math.max(1e-6, x1 - x0);
  const px = p => m.l + ((p - x0) / span) * iw;
  const y = v => m.t + (1 - (v - cfg.min) / (cfg.max - cfg.min)) * ih;
  const clipId = cfg.clipId || ('clip-' + Math.random().toString(36).slice(2));
  let s = '<svg class="chart-svg" viewBox="0 0 ' + W + ' ' + H + '" height="' + H + '">';
  s += '<defs><clipPath id="' + clipId + '"><rect x="' + m.l + '" y="' + m.t + '" width="' + iw + '" height="' + ih + '"/></clipPath></defs>';
  // 底部刻度：显示当前可视范围的圈进度百分比
  const NC = 10;
  for (let cc = 0; cc < NC; cc++) {
    const pa = x0 + span * cc / NC, pb = x0 + span * (cc + 1) / NC;
    const cx0 = px(pa), cx1 = px(pb);
    s += '<rect x="' + cx0.toFixed(1) + '" y="' + (yBot + u).toFixed(1) + '" width="' + (cx1 - cx0).toFixed(1) + '" height="' + BAND + '" fill="' + (cc % 2 ? c('--chart-band-b') : c('--chart-band-a')) + '"/>';
    s += '<text x="' + ((cx0 + cx1) / 2).toFixed(1) + '" y="' + (H - 3 * u).toFixed(1) + '" text-anchor="middle" font-family="' + c('--font-mono') + '" font-size="' + (7.5 * u).toFixed(2) + '" fill="' + c('--text-faint') + '">' + Math.round(((pa + pb) / 2) * 100) + '%</text>';
  }
  // 横向网格
  for (let k = 0; k <= 3; k++) {
    const v = cfg.min + (cfg.max - cfg.min) * k / 3, yy = y(v).toFixed(1);
    s += '<line x1="' + m.l + '" y1="' + yy + '" x2="' + (W - m.r) + '" y2="' + yy + '" stroke="' + c('--grid-line') + '"/>';
    s += '<text x="' + (m.l - 6 * u) + '" y="' + (Number(yy) + 3.5 * u).toFixed(1) + '" text-anchor="end" font-family="' + c('--font-mono') + '" font-size="' + (8.5 * u).toFixed(2) + '" fill="' + c('--text-faint') + '">' + v.toFixed(0) + '</text>';
  }
  // x轴
  s += '<line x1="' + m.l + '" y1="' + yBot + '" x2="' + (W - m.r) + '" y2="' + yBot + '" stroke="' + c('--line-2') + '"/>';
  // 分段标签（不再画分段虚线，只保留弯心参考线）
  [[1 / 6, 'S1'], [1 / 2, 'S2'], [5 / 6, 'S3']].forEach(([p, label]) => {
    if (p >= x0 - 0.02 && p <= x1 + 0.02) {
      const lx = clamp(px(p), m.l + 10 * u, W - m.r - 10 * u);
      s += '<text x="' + lx.toFixed(1) + '" y="' + (yBot - 3 * u).toFixed(1) + '" text-anchor="middle" font-family="' + c('--font-mono') + '" font-size="' + (8 * u).toFixed(2) + '" fill="' + c('--text-dim') + '">' + label + '</text>';
    }
  });
  // 弯心参考线：按钮开关；distance 按圈长归一化到进度
  if (cfg.apexOn && cfg.lenM && cfg.apexes?.length) {
    const apex = c('--apex');
    cfg.apexes.forEach(a => {
      const p = clamp(a.distance / cfg.lenM, 0, 1);
      if (p < x0 || p > x1) return;
      const sx = px(p);
      const sel = cfg.selApex === a.name;
      s += '<line x1="' + sx.toFixed(1) + '" y1="' + m.t + '" x2="' + sx.toFixed(1) + '" y2="' + yBot +
        '" stroke="' + (sel ? apex : c('--text-faint')) +
        '" stroke-width="' + ((sel ? 1.4 : 1) * u).toFixed(2) +
        '" stroke-dasharray="' + (3 * u).toFixed(2) + ' ' + (4 * u).toFixed(2) + '" opacity="' + (sel ? '.9' : '.45') + '"/>';
      s += '<text x="' + (sx + 4 * u).toFixed(1) + '" y="' + (m.t + 10 * u) +
        '" font-family="' + c('--font-mono') + '" font-weight="700" font-size="' + (9.5 * u).toFixed(2) + '" fill="' + apex + '">' +
        a.name + '</text>';
    });
  }
  s += '<g clip-path="url(#' + clipId + ')">';
  cfg.series.forEach(se => {
    let d = '';
    for (let i = 0; i < cfg.n; i += 1) {
      d += (i === 0 ? 'M' : 'L') + px(i / cfg.n).toFixed(1) + ',' + y(se.fn(i)).toFixed(1) + ' ';
    }
    s += '<path d="' + d + '" fill="none" stroke="' + se.color + '" stroke-width="' + ((se.dash ? 1.4 : 1.8) * u).toFixed(2) + '" ' + (se.dash ? 'stroke-dasharray="' + (4 * u).toFixed(2) + ' ' + (3 * u).toFixed(2) + '" opacity=".9"' : '') + ' stroke-linejoin="round"/>';
  });
  s += '</g>';
  s += '<text x="' + m.l + '" y="' + (m.t - 8 * u) + '" text-anchor="start" font-family="' + c('--font-display') + '" font-weight="600" font-size="' + (9 * u).toFixed(2) + '" fill="' + c('--text-dim') + '">' + cfg.title + '</text>';
  s += '</svg>';
  return s;
}

export function renderCharts(wrap, lp, pb, M, showPb = true, view = null) {
  if (!wrap) return;
  Array.prototype.slice.call(wrap.querySelectorAll('.chart-panel')).forEach(e => e.remove());
  const u = uiScale();
  const W = Math.max(240 * u, wrap.clientWidth || 420 * u), n = M.n;
  const x0 = view?.x0 ?? 0, x1 = view?.x1 ?? 1;
  const apexCfg = {
    apexes: view?.apexes || [],
    apexOn: Boolean(view?.apexOn),
    selApex: view?.selApex || null,
    lenM: view?.lenM || M.lenM || null,
  };

  const speedSeries = [];
  if (showPb) speedSeries.push({ fn: i => pb.v[i] * 3.6, color: c('--text-dim'), dash: true });
  speedSeries.push({ fn: i => lp.v[i] * 3.6, color: c('--chart-line') });

  const pedalSeries = [];
  if (showPb) {
    pedalSeries.push({ fn: i => pb.P.th[i] * 100, color: c('--good'), dash: true });
    pedalSeries.push({ fn: i => pb.P.br[i] * 100, color: c('--hot'), dash: true });
  }
  pedalSeries.push({ fn: i => lp.P.th[i] * 100, color: c('--good') });
  pedalSeries.push({ fn: i => lp.P.br[i] * 100, color: c('--hot') });

  const spd = document.createElement('div'); spd.className = 'chart-panel';
  spd.innerHTML = panelSVG({ title: '速度', min: 0, max: 340, n: n, series: speedSeries, x0, x1, clipId: 'chart-clip-speed', ...apexCfg }, W, 118 * u);
  const ped = document.createElement('div'); ped.className = 'chart-panel';
  ped.innerHTML = panelSVG({ title: '踏板', min: 0, max: 105, n: n, series: pedalSeries, x0, x1, clipId: 'chart-clip-ped', ...apexCfg }, W, 104 * u);
  wrap.appendChild(spd); wrap.appendChild(ped);
}

export function drawMiniMap(svg, M, opts = {}) {
  if (!svg) return;
  // 路面：真实左右边距围成的多边形；没有几何数据的老记录退回中心线。
  // 两层描边与实时面板（TrackCanvasMap canvas）同款：外层半透明柔光（车面色）+
  // 内层清晰细轮廓（--map-road-line）。
  // 必须加 vector-effect="non-scaling-stroke"：小地图 viewBox 会被容器放大
  // （内容框 ~630 宽铺到 ~1000px），不加的话线宽跟着放大，比 canvas 粗一倍。
  const road = M.edgeD
    ? '<path class="map-road-halo" vector-effect="non-scaling-stroke" d="' + M.edgeD + '"/>' +
      '<path class="map-road" vector-effect="non-scaling-stroke" d="' + M.edgeD + '"/>'
    : '<path class="map-base" d="' + M.d + '"/>';
  // 踏板热力画在路面、本圈实线之上：只画本圈实际走线（轨迹），不铺满路面，
  // 颜色盖住路线，突出踏板分布。不做路面裁剪，出场段也画（见下）。
  const heatRaw = opts.heat ? heatOverlayMarkup(opts.heat, M) : '';
  // 不再把热力裁剪到路面多边形（mapRoadClip 已移除）：走线点是全局坐标，
  // 赛车驶出赛道 / 压路肩那段也必须画出来，否则看着像热力断了一截。
  const heat = heatRaw;
  // 本圈实线走线（常驻）+ PB 圈（半透明参考线，叠在热力之上做对比）
  const lapLine = lapLineMarkup(opts.lapLine, 'map-lap-cur');
  const pbLine = lapLineMarkup(opts.pbLine, 'map-lap-pb');
  // AI 中心线只在「本圈确实没有走线数据」时兜底交代行车线走向，否则两条线会互相压着。
  // 用户主动隐藏本圈路线时不要画（opts.centerLine=false）：按钮写着隐藏，又冒出另一条线会很违和。
  const wantCenter = opts.centerLine !== undefined ? opts.centerLine : !opts.lapLine;
  const center = M.edgeD && M.centerD && wantCenter && !heat
    ? '<path class="map-ai-line" d="' + M.centerD + '"/>'
    : '';
  const startFinish = M.startLine ? startFinishMarkup(M.startLine) : '';
  // 方向箭头按需求不画了（directionArrowMarkup 保留着，需要时在这里加回来）
  const arrow = '';

  // 卡片窄的时候只放弯号（T1–T2），宽的时候才带上真实弯名，避免小地图被文字塞满
  const mapOpts = { ...opts, mapWidth: svg.clientWidth || 480 };
  // 先按赛道本体算取景框：弯心标签摆放要贴着这个框，不能再反过来把框撑大
  const vb = contentViewBox(M, mapOpts);

  // 预留出左上角比例尺的区域（约 104×26 元素像素，含余量），换算回 viewBox 坐标，
  // 让弯名标签避开这里——页面放大后赛道顶角顶到左上角时，比例尺才会和标签打架。
  let scaleExclude = null;
  const rect0 = svg.getBoundingClientRect ? svg.getBoundingClientRect() : null;
  if (vb && rect0 && rect0.width && rect0.height) {
    const s0 = Math.min(rect0.width / vb.width, rect0.height / vb.height);
    if (s0 > 0) {
      const ox0 = (rect0.width - vb.width * s0) / 2;
      const oy0 = (rect0.height - vb.height * s0) / 2;
      const RE_W = 104, RE_H = 26;
      scaleExclude = {
        x0: vb.x + (0 - ox0) / s0,
        y0: vb.y + (0 - oy0) / s0,
        x1: vb.x + (RE_W - ox0) / s0,
        y1: vb.y + (RE_H - oy0) / s0,
      };
    }
  }

  svg.innerHTML =
    '<g id="mapZoom">' + road + center + lapLine + heat + pbLine +
      startFinish + arrow + '</g>' +
    apexOverlayMarkup(M, { ...mapOpts, box: vb, exclude: scaleExclude }) +
    cursorOverlayMarkup() +
    scaleBarMarkup(M);

  if (vb) {
    // 收紧 viewBox，让赛道铺满地图区域，而不是在 480x290 里居中留白
    svg.setAttribute('viewBox', vb.x.toFixed(2) + ' ' + vb.y.toFixed(2) + ' ' + vb.w.toFixed(2) + ' ' + vb.h.toFixed(2));
  }
  layoutApexOverlay(svg, opts.view || { scale: 1, tx: 0, ty: 0 });
  layoutScaleBar(svg, opts.view || { scale: 1, tx: 0, ty: 0 });
  return vb;
}

/* --------------------------- 地图装饰 --------------------------- */

// 起终点线：从左边界的第 0 个点连到右边界的第 0 个点，天然垂直于赛道方向。
// 两端各向外延伸一点（真实赛道的线会画到路肩外），整圈视图里才看得出这是起跑线。
function startFinishMarkup(line) {
  const { a, b } = line;
  const dx = b.x - a.x, dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const ext = Math.min(6, Math.max(2.4, len * 0.35));
  return '<g class="map-sf">' +
    '<line x1="' + (a.x - ux * ext).toFixed(2) + '" y1="' + (a.y - uy * ext).toFixed(2) +
      '" x2="' + (b.x + ux * ext).toFixed(2) + '" y2="' + (b.y + uy * ext).toFixed(2) + '"/>' +
    '</g>';
}

// 方向箭头：找一段最直的直道，沿切线画一个小箭头，提示 lap 方向。
function directionArrowMarkup(M, opts = {}) {
  if (opts.arrow === false) return '';
  const left = M.left, right = M.right;
  if (!Array.isArray(left) || !Array.isArray(right) || left.length < 20) return '';
  const n = Math.min(left.length, right.length);
  const c = (i) => ({
    x: (left[(i + n) % n].x + right[(i + n) % n].x) / 2,
    y: (left[(i + n) % n].y + right[(i + n) % n].y) / 2,
  });

  // 直道判定：前后各 6 个采样点的方向夹角接近 0
  const span = 6;
  let best = -1, bestScore = -Infinity;
  for (let i = 0; i < n; i += 4) {
    // 起终点附近不放箭头，避免和起跑线挤在一起
    if (Math.min(i, n - i) < n * 0.06) continue;
    const a = c(i - span), b = c(i), d = c(i + span);
    const ax = b.x - a.x, ay = b.y - a.y;
    const bx = d.x - b.x, by = d.y - b.y;
    const la = Math.hypot(ax, ay) || 1, lb = Math.hypot(bx, by) || 1;
    const cos = (ax * bx + ay * by) / (la * lb);
    if (cos > bestScore) { bestScore = cos; best = i; }
  }
  if (best < 0) return '';

  const prev = c(best - 3), cur = c(best), next = c(best + 3);
  const tx = next.x - prev.x, ty = next.y - prev.y;
  const tl = Math.hypot(tx, ty) || 1;
  const ux = tx / tl, uy = ty / tl;
  const L = 13, W = 4.6;
  const tip = { x: cur.x + ux * L, y: cur.y + uy * L };
  const tail = { x: cur.x - ux * L, y: cur.y - uy * L };
  const wing = { x: tip.x - ux * W * 1.9, y: tip.y - uy * W * 1.9 };
  const p1 = { x: wing.x - uy * W, y: wing.y + ux * W };
  const p2 = { x: wing.x + uy * W, y: wing.y - ux * W };
  const f = (p) => p.x.toFixed(2) + ' ' + p.y.toFixed(2);
  return '<g class="map-dir" pointer-events="none">' +
    '<line x1="' + tail.x.toFixed(2) + '" y1="' + tail.y.toFixed(2) +
      '" x2="' + (cur.x + ux * L * 0.82).toFixed(2) + '" y2="' + (cur.y + uy * L * 0.82).toFixed(2) + '"/>' +
    '<path d="M' + f(tip) + ' L' + f(p1) + ' L' + f(p2) + ' Z"/>' +
    '</g>';
}

// 比例尺：只有拿到真实几何（米）时才画。长度在视图里固定，缩放时重新算。
const SCALE_STEPS = [5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000];

function scaleBarMarkup(M) {
  if (!M.mPerUnit || !Number.isFinite(M.mPerUnit)) return '';
  // 底板：比例尺画在地图内容之上（#mapScale 是最后一个子元素），
  // 但页面放大后赛道本体顶角会顶到左上角，淡灰标尺压在白路上几乎看不见。
  // 加一块与卡片背景同色、带细边框的底板，无论底下是白路还是弯名标签都清晰可读。
  return '<g id="mapScale" class="map-scale" pointer-events="none" data-mperunit="' + M.mPerUnit + '">' +
    '<rect class="map-scale-bg" rx="2.5"/>' +
    '<line class="map-scale-bar"/>' +
    '<line class="map-scale-tick" x1="0" y1="-3" x2="0" y2="3"/>' +
    '<line class="map-scale-tick" y1="-3" y2="3"/>' +
    '<text class="map-scale-text" y="-6" text-anchor="middle"></text>' +
    '</g>';
}

/** 地图平移 / 缩放后重算比例尺：始终选一个“整数米”且长度合适的刻度。 */
export function layoutScaleBar(svg, view) {
  const g = svg?.querySelector('#mapScale');
  if (!g) return;
  const mPerUnit = Number(g.dataset.mperunit) || 0;
  if (!mPerUnit) return;
  const zoom = view?.scale || 1;
  // 目标屏幕长度 ≈ 46~96 视图单位（对齐地图卡片宽度）
  const unitPerM = 1 / mPerUnit;
  let meters = SCALE_STEPS[SCALE_STEPS.length - 1];
  for (const step of SCALE_STEPS) {
    const px = step * unitPerM * zoom;
    if (px >= 42 && px <= 96) { meters = step; break; }
    if (px > 96) break;
  }
  const width = meters * unitPerM * zoom;
  const bar = g.querySelector('.map-scale-bar');
  const ticks = g.querySelectorAll('.map-scale-tick');
  const text = g.querySelector('.map-scale-text');
  if (bar) { bar.setAttribute('x1', '0'); bar.setAttribute('y1', '0'); bar.setAttribute('x2', width.toFixed(2)); bar.setAttribute('y2', '0'); }
  if (ticks[0]) ticks[0].setAttribute('x2', '0');
  if (ticks[1]) { ticks[1].setAttribute('x1', width.toFixed(2)); ticks[1].setAttribute('x2', width.toFixed(2)); }
  if (text) {
    text.setAttribute('x', (width / 2).toFixed(2));
    text.textContent = meters >= 1000 ? (meters / 1000) + ' km' : meters + ' m';
  }
  // 底板盖住刻度线 + 上方文字 + 端点，留一点余量；随比例尺一起缩放
  const bg = g.querySelector('.map-scale-bg');
  if (bg) {
    bg.setAttribute('x', (-4).toFixed(2));
    bg.setAttribute('y', (-14).toFixed(2));
    bg.setAttribute('width', (width + 8).toFixed(2));
    bg.setAttribute('height', '20');
  }
  // 固定在 SVG 元素可视区的左上角（而不是 viewBox 内容包围盒的左上角）。
  // viewBox 是紧贴赛道的包围盒，不同赛道长宽比不同 → preserveAspectRatio
  // （默认 xMidYMid meet）的居中留白也不同；若直接按 viewBox 原点摆，
  // 比例尺在每张图上会飘到不同位置。这里换算 letterbox 偏移，
  // 把比例尺钉到元素左上角（固定约 10px / 14px 边距），每张图位置一致。
  const vb = svg.viewBox?.baseVal;
  const rect = svg.getBoundingClientRect ? svg.getBoundingClientRect() : null;
  if (vb && vb.width) {
    let x = vb.x + 10, y = vb.y + 14;
    if (rect && rect.width && rect.height) {
      const s = Math.min(rect.width / vb.width, rect.height / vb.height);
      if (s > 0) {
        const ox = (rect.width - vb.width * s) / 2;
        const oy = (rect.height - vb.height * s) / 2;
        x = vb.x + (10 - ox) / s;
        y = vb.y + (14 - oy) / s;
      }
    }
    g.setAttribute('transform', 'translate(' + x.toFixed(2) + ' ' + y.toFixed(2) + ')');
  }
}

/* --------------------------- 圈线对比 --------------------------- */

function polylinePath(points) {
  if (!Array.isArray(points) || points.length < 3) return '';
  let d = 'M' + Number(points[0].x).toFixed(1) + ',' + Number(points[0].y).toFixed(1);
  // 走线首尾本来就是相邻点，不能补 Z：闭合会在起终点处画出一条横切赛道的直线
  for (let i = 1; i < points.length; i += 1) {
    d += 'L' + Number(points[i].x).toFixed(1) + ',' + Number(points[i].y).toFixed(1);
  }
  return d;
}

// 单条走线：本圈实线 / PB 圈参考线，样式由 class 决定。
// non-scaling-stroke：线宽写成 CSS 像素，不随 viewBox 放大，
// 这样才能和热力轨迹（thinHeatMarkup 里也是非缩放像素）真正一样宽。
function lapLineMarkup(points, cls) {
  const d = polylinePath(points);
  return d ? '<path class="' + cls + '" vector-effect="non-scaling-stroke" d="' + d + '"/>' : '';
}

/* --------------------------- 海拔剖面 --------------------------- */

/**
 * 地图卡片底部的海拔剖面：横轴是整圈里程，纵轴是海拔。
 * 数据来自 fastlane 的 y（等距重采样后每个 spacing 米一个点）。
 * 画法参考 资料/MAP_RENDERING_NOTES.md 第 9 节：单色细线 + 极浅填充，
 * 起终点画一条竖线，不抢地图的视觉重心。
 */
export function drawElevation(svg, M, opts = {}) {
  if (!svg) return false;
  const alt = M?.alt;
  if (!Array.isArray(alt) || alt.length < 8) {
    svg.innerHTML = '';
    svg.__elev = null;
    return false;
  }

  const u = uiScale();
  const W = Math.max(180, Math.round(svg.clientWidth || svg.parentElement?.clientWidth || 420));
  const H = Math.max(32, Math.round(svg.clientHeight || 46));
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);

  // 左侧留出海拔刻度，底部留一点空隙
  const x0 = 30 * u, x1 = W - 8 * u;
  const top = 5 * u, bottom = H - 6 * u;
  const iw = Math.max(10, x1 - x0);
  const ih = Math.max(8, bottom - top);

  const lo = Number.isFinite(M.altMin) ? M.altMin : Math.min(...alt);
  const hi = Number.isFinite(M.altMax) ? M.altMax : Math.max(...alt);
  const pad = Math.max(0.8, (hi - lo) * 0.18);
  const yLo = lo - pad, yHi = hi + pad;

  const n = alt.length;
  const px = (i) => x0 + (i / (n - 1)) * iw;
  const py = (v) => top + (1 - (v - yLo) / (yHi - yLo)) * ih;

  // 卡片只有几十像素高，降采样到 ~520 个点就够（再密看不出来）
  const stride = Math.max(1, Math.ceil(n / 520));
  let line = '';
  let first = true;
  for (let i = 0; i < n; i += stride) {
    line += (first ? 'M' : 'L') + px(i).toFixed(1) + ',' + py(alt[i]).toFixed(1);
    first = false;
  }
  line += 'L' + px(n - 1).toFixed(1) + ',' + py(alt[n - 1]).toFixed(1);

  const base = py(yLo) + 2;
  const area = line + 'L' + px(n - 1).toFixed(1) + ',' + base.toFixed(1) +
    'L' + px(0).toFixed(1) + ',' + base.toFixed(1) + 'Z';

  // 每 1 km 一条浅刻度，帮助判断上坡落在哪一段
  let ticks = '';
  const stepM = 1000;
  const lapM = Number(M.totalLen) || (M.spacing ? M.spacing * (n - 1) : 0);
  if (lapM > 0) {
    for (let d = stepM; d < lapM; d += stepM) {
      const tx = x0 + (d / lapM) * iw;
      ticks += '<line class="elev-tick" x1="' + tx.toFixed(1) + '" y1="' + top.toFixed(1) +
        '" x2="' + tx.toFixed(1) + '" y2="' + bottom.toFixed(1) + '"/>';
    }
  }

  svg.innerHTML =
    '<path class="elev-area" d="' + area + '"/>' +
    ticks +
    '<path class="elev-line" d="' + line + '"/>' +
    '<line class="elev-sf" x1="' + x0.toFixed(1) + '" y1="' + top.toFixed(1) +
      '" x2="' + x0.toFixed(1) + '" y2="' + bottom.toFixed(1) + '"/>' +
    '<text class="elev-label" x="' + (x0 - 4).toFixed(1) + '" y="' + (top + 7).toFixed(1) +
      '" text-anchor="end">' + Math.round(hi) + '</text>' +
    '<text class="elev-label" x="' + (x0 - 4).toFixed(1) + '" y="' + bottom.toFixed(1) +
      '" text-anchor="end">' + Math.round(lo) + '</text>' +
    '<g id="elevCursor" opacity="0">' +
      '<line class="elev-cursor-line" x1="0" y1="' + top.toFixed(1) + '" x2="0" y2="' + bottom.toFixed(1) + '"/>' +
      '<circle class="elev-cursor-dot" r="2.4" cx="0" cy="' + py(alt[0]).toFixed(1) + '"/>' +
    '</g>';

  svg.__elev = { x0, iw, top, bottom, yLo, yHi, n };
  if (opts.progress != null) updateElevationCursor(svg, M, opts.progress);
  return true;
}

/** 游标同步：竖线 + 小圆点跟随当前进度，读数仍然由地图上的 readout 负责。 */
export function updateElevationCursor(svg, M, p) {
  const L = svg?.__elev;
  const g = svg?.querySelector('#elevCursor');
  if (!L || !g) return;
  const ratio = clamp(Number(p) || 0, 0, 0.999999);
  const x = L.x0 + ratio * L.iw;
  const alt = M?.alt;
  let y = L.top;
  if (Array.isArray(alt) && alt.length) {
    const i = clamp(Math.round(ratio * (alt.length - 1)), 0, alt.length - 1);
    y = L.top + (1 - (alt[i] - L.yLo) / (L.yHi - L.yLo)) * (L.bottom - L.top);
  }
  g.setAttribute('opacity', '1');
  const line = g.querySelector('.elev-cursor-line');
  const dot = g.querySelector('.elev-cursor-dot');
  if (line) { line.setAttribute('x1', x.toFixed(1)); line.setAttribute('x2', x.toFixed(1)); }
  if (dot) { dot.setAttribute('cx', x.toFixed(1)); dot.setAttribute('cy', y.toFixed(1)); }
}

function contentViewBox(M, opts = {}) {
  const groups = [];
  if (Array.isArray(M?.left) && M.left.length) groups.push(M.left);
  if (Array.isArray(M?.right) && M.right.length) groups.push(M.right);
  if (!groups.length && Array.isArray(M?.pts) && M.pts.length) groups.push(M.pts);
  if (Array.isArray(opts.lapLine) && opts.lapLine.length) groups.push(opts.lapLine);
  // 热力轨迹用的是同一条本圈走线（全局坐标，含出场段）；只开热力、关了本圈实线时，
  // 取景框也要把这条路算进去，否则驶出赛道那段会被 viewBox 切掉。
  if (Array.isArray(opts.heat?.line) && opts.heat.line.length) groups.push(opts.heat.line);
  if (Array.isArray(opts.pbLine) && opts.pbLine.length) groups.push(opts.pbLine);
  if (!groups.length) return null;

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const group of groups) {
    for (const p of group) {
      if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) continue;
      if (p.x < minX) minX = p.x;
      if (p.x > maxX) maxX = p.x;
      if (p.y < minY) minY = p.y;
      if (p.y > maxY) maxY = p.y;
    }
  }
  if (!Number.isFinite(minX)) return null;

  // 取景只看赛道本体（路面 + 本圈走线）。以前把每个弯心标签的整段文字宽度
  // 左右各算一遍，Monza 这种长弯名的赛道会凭空多出 150 多个单位的宽度：
  // 赛道被压小 25% 还被推到一侧，看起来就像「路画错了」。
  // 标签本身改由 placeApexLabels 约束在框内摆放。
  const padX = 8;
  const padTop = 10;
  // 底部多留一点：比例尺固定在 viewBox 左下角，不压在赛道上
  const padBottom = M?.mPerUnit ? 18 : 8;
  return {
    x: minX - padX,
    y: minY - padTop,
    w: Math.max(1, maxX - minX + padX * 2),
    h: Math.max(1, maxY - minY + padTop + padBottom),
  };
}

// 弯心标签放在 mapZoom 之外：缩放地图时只更新位置，字号和圆点保持恒定。
// 标签位置按“就近 + 互不重叠”挑一个候选方向，密集弯（Lesmo / Ascari）也不会叠字。
const APEX_LABEL_FONT = 14;
const APEX_LABEL_H = 11;
const APEX_CANDIDATES = [
  { dx: 9, dy: -5, anchor: 'start' },
  { dx: 9, dy: 11, anchor: 'start' },
  { dx: -9, dy: -5, anchor: 'end' },
  { dx: -9, dy: 11, anchor: 'end' },
  { dx: 0, dy: -8, anchor: 'middle' },
  { dx: 0, dy: 15, anchor: 'middle' },
];

function labelBox(it, cnd, w) {
  const x0 = cnd.anchor === 'start'
    ? it.x + cnd.dx
    : (cnd.anchor === 'end' ? it.x + cnd.dx - w : it.x + cnd.dx - w / 2);
  const y0 = it.y + cnd.dy - APEX_LABEL_H + 1;
  return { x0, y0, x1: x0 + w, y1: y0 + APEX_LABEL_H };
}

// 标签宽度估算：弯号用大字号，弯名小一号跟在后面
function apexLabelWidth(it) {
  return String(it.name || '').length * APEX_LABEL_FONT * 0.6 + 6;
}

function boxesOverlap(a, b, gap = 2) {
  return !(a.x1 + gap < b.x0 || b.x1 + gap < a.x0 || a.y1 + gap < b.y0 || b.y1 + gap < a.y0);
}

function placeApexLabels(items, frame = null, exclude = null) {
  const placed = new Map();
  const boxes = [];
  // 比例尺底板占用的左上角区域：弯名标签不要压在它上面（页面放大后才会出现冲突）
  if (exclude) boxes.push({ x0: exclude.x0, y0: exclude.y0, x1: exclude.x1, y1: exclude.y1 });
  // 标签要留在取景框里：框现在只按赛道本体收紧，贴边的弯心必须往内侧写，
  // 否则弯名会被裁掉一半。
  const inside = (b) => !frame
    || (b.x0 >= frame.x + 2 && b.x1 <= frame.x + frame.w - 2
      && b.y0 >= frame.y + 2 && b.y1 <= frame.y + frame.h - 2);
  // 选中的弯优先占位，其次是靠上的（读图习惯从上往下）
  const order = items.slice().sort((a, b) =>
    (b.selected ? 1 : 0) - (a.selected ? 1 : 0) || a.y - b.y || a.x - b.x
  );
  for (const it of order) {
    const w = apexLabelWidth(it);
    let best = null, bestScore = -Infinity;
    for (const cnd of APEX_CANDIDATES) {
      const box = labelBox(it, cnd, w);
      const overlap = boxes.reduce((n, b) => n + (boxesOverlap(b, box) ? 1 : 0), 0);
      // 优先「完整落在框内」，其次「不和已放的标签打架」
      const score = (inside(box) ? 4 : 0) - overlap * 3;
      if (score > bestScore) { bestScore = score; best = { cnd, box }; }
    }
    boxes.push(best.box);
    placed.set(it, best);
  }
  return placed;
}

function apexOverlayMarkup(M, opts = {}) {
  const { apexes = [], selected = null, show = true } = opts;
  if (!show || !apexes.length) return '';
  const toView = typeof M?.toScreen === 'function'
    ? M.toScreen
    : (x, z) => ({ x, y: z });
  const ink = mapInk();
  // 弯心标注文字与选中高亮统一为深绿 #3F7D0F，深浅主题一致
  // （原本深色主题用亮柠 --apex、浅色主题用深绿 --apex，两主题颜色不同）。
  const APEX_FILL = '#3F7D0F';
  // 弯心标记的描边光晕与圆点底色固定为白色，不再随主题取 --panel：
  // 深浅主题的路面都是白色，统一成白色描边后两种主题下都清晰且一致
  // （原本深色主题用深色 --panel 当描边，和浅色主题的白色描边不一致）。
  const bg = '#FFFFFF';

  const items = [];
  for (const a of apexes) {
    const p = toView(a.x, a.z);
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) continue;
    items.push({
      name: String(a.name || ''),
      x: p.x,
      y: p.y,
      selected: selected === a.name,
    });
  }
  if (!items.length) return '';

  const placed = placeApexLabels(items, opts.box || null, opts.exclude || null);
  let h = '';
  for (const it of items) {
    const { cnd } = placed.get(it);
    const sel = it.selected;
    const fill = APEX_FILL;
    const label = it.name;
    // 文字用白色描边（paint-order=stroke），压在路面 / 热力上都看得清，深浅主题一致
    h += '<g class="apex-tag" data-x="' + it.x.toFixed(2) + '" data-y="' + it.y.toFixed(2) + '">' +
      (sel ? '<circle r="9" fill="' + APEX_FILL + '" opacity=".18"/>' : '') +
      '<circle r="3.4" fill="' + bg + '" fill-opacity=".6" stroke="' + ink + '" stroke-width="' + (sel ? 1.9 : 1.3) + '"/>' +
      '<text x="' + cnd.dx + '" y="' + cnd.dy + '" text-anchor="' + cnd.anchor + '"' +
      ' paint-order="stroke" stroke="' + bg + '" stroke-width="3" stroke-linejoin="round"' +
      ' font-family="' + c('--font-display') + '" font-weight="700" font-size="' + APEX_LABEL_FONT + '"' +
      ' fill="' + fill + '">' + label + '</text>' +
      '</g>';
  }
  return '<g id="apexOverlay">' + h + '</g>';
}

/** 地图平移 / 缩放后重新摆放弯心标签（标签本身不缩放）。 */
export function layoutApexOverlay(svg, view) {
  const g = svg?.querySelector('#apexOverlay');
  if (!g) return;
  const s = view?.scale ?? 1, tx = view?.tx ?? 0, ty = view?.ty ?? 0;
  for (const node of g.children) {
    const x = Number(node.dataset.x) || 0, y = Number(node.dataset.y) || 0;
    node.setAttribute('transform', 'translate(' + (tx + x * s).toFixed(2) + ' ' + (ty + y * s).toFixed(2) + ')');
  }
}

// 踏板热力叠加：沿本圈「实际跑过的走线」着色（一支带踏板颜色的轨迹），
// 不铺满整条路面 —— 路面 / 起跑线等地图底图样式保持独立、不被热力覆盖。
// 输出放在 mapZoom 里，跟随地图一起缩放和平移（与 canvas drawHeatmap 同一套轨迹逻辑）。
export function heatOverlayMarkup(lp, M) {
  const thRaw = lp?.P?.th;
  const brRaw = lp?.P?.br;
  if (!Array.isArray(thRaw) || !Array.isArray(brRaw) || thRaw.length < 3) return '';
  const pal = heatPalette();
  // 无论有没有真实路面几何，热力都只画本圈真实走线（轨迹）；
  // 老记录没有几何时 lapLinePoints 也会回退到可用的中心线。
  return thinHeatMarkup(lp, M, pal);
}

// 圆角笔帽在急弯外侧会鼓出路面，调用方用 clipPath 裁到路面多边形里；
// 这里只负责把颜色铺满，不做裁剪判断。
function smoothColorRing(list, passes) {
  let src = list;
  for (let p = 0; p < passes; p += 1) {
    const n = src.length;
    src = src.map((_, i) => {
      const a = src[(i - 1 + n) % n], b = src[i], d = src[(i + 1) % n];
      return [(a[0] + b[0] * 2 + d[0]) / 4, (a[1] + b[1] * 2 + d[1]) / 4, (a[2] + b[2] * 2 + d[2]) / 4];
    });
  }
  return src;
}

// 老记录没有真实路面几何：维持原来的「沿走线的细带」画法
function thinHeatMarkup(lp, M, pal) {
  const raw = lapLinePoints(lp, M);
  if (!raw || raw.length < 3 || !lp?.P?.th || !lp?.P?.br) return '';
  // 走线点自带该处的踏板值（时间序、含出赛道段）→ 与坐标严格逐点对齐；
  // 老记录没有这两个字段，回退到「按进度等分的 P.th / P.br」。
  const own = raw[0]?.th != null && raw[0]?.br != null;
  const n = own
    ? raw.length
    : Math.min(raw.length, lp.P.th.length, lp.P.br.length);
  if (n < 3) return '';

  // 几何只压掉单点 GPS 抖动；踏板值同样只做 ±1 点平滑，
  // 刹车点 / 开油点的位置和幅度都保持真实，不做时间上的提前量。
  const line = smoothClosedPoints(raw.slice(0, n), 1);
  const th = smoothClosedArray(
    own ? raw.slice(0, n).map((it) => it.th) : lp.P.th.slice(0, n),
    1
  );
  const br = smoothClosedArray(
    own ? raw.slice(0, n).map((it) => it.br) : lp.P.br.slice(0, n),
    1
  );

  const { pts, values } = subdivideClosedLoop(line, [th, br], 2);
  const thv = values[0], brv = values[1];
  // 轨迹线宽：比本圈实线 / PB 参考线（各 1 CSS 像素，见 style.css）略宽一档到 1.4，
  // 热力是彩色踏板信息、太细会看不清，但也不能粗到盖住路线对比。
  // 必须是字面像素（不能乘 uiScale），否则和 CSS 里的宽度对不齐。
  const width = (1.4).toFixed(2);

  const cols = smoothColorRing(pts.map((_, i) => heatColorRgb(brv[i], thv[i], pal)), 2);

  let out = '<g id="heatOverlay" opacity=".95" stroke-linecap="round">';
  for (let i = 0; i < pts.length; i += 1) {
    const j = (i + 1) % pts.length;
    const a = pts[i], b = pts[j];
    out += '<line x1="' + a.x.toFixed(2) + '" y1="' + a.y.toFixed(2) +
      '" x2="' + b.x.toFixed(2) + '" y2="' + b.y.toFixed(2) +
      '" stroke="' + rgbCss(cols[i]) + '" stroke-width="' + width +
      '" vector-effect="non-scaling-stroke"/>';
  }
  return out + '</g>';
}

// 闭合环的向心 Catmull-Rom 细分（Barry–Goldman 形式）。
// 曲线严格经过每个原始点；values 用同一参数做线性插值，保持逐点对齐。
function subdivideClosedLoop(points, values, subdiv) {
  const n = points.length;
  const pts = [];
  const vals = values.map(() => []);
  const dist = (a, b) => Math.max(1e-6, Math.hypot(b.x - a.x, b.y - a.y)) ** 0.5;

  for (let i = 0; i < n; i += 1) {
    const p0 = points[(i - 1 + n) % n];
    const p1 = points[i];
    const p2 = points[(i + 1) % n];
    const p3 = points[(i + 2) % n];
    const t0 = 0;
    const t1 = dist(p0, p1);
    const t2 = t1 + dist(p1, p2);
    const t3 = t2 + dist(p2, p3);

    for (let s = 0; s < subdiv; s += 1) {
      const u = s / subdiv;
      const t = t1 + (t2 - t1) * u;
      pts.push(catmullPoint(p0, p1, p2, p3, t0, t1, t2, t3, t));
      for (let k = 0; k < values.length; k += 1) {
        const v = values[k];
        vals[k].push(v[i] + (v[(i + 1) % n] - v[i]) * u);
      }
    }
  }
  return { pts, values: vals };
}

function catmullPoint(p0, p1, p2, p3, t0, t1, t2, t3, t) {
  const mix = (ta, tb, a, b) => {
    const span = tb - ta;
    if (Math.abs(span) < 1e-9) return { x: a.x, y: a.y };
    const wa = (tb - t) / span, wb = (t - ta) / span;
    return { x: wa * a.x + wb * b.x, y: wa * a.y + wb * b.y };
  };
  const a1 = mix(t0, t1, p0, p1);
  const a2 = mix(t1, t2, p1, p2);
  const a3 = mix(t2, t3, p2, p3);
  const b1 = mix(t0, t2, a1, a2);
  const b2 = mix(t1, t3, a2, a3);
  return mix(t1, t2, b1, b2);
}

// 圈速概要里的游标：放在 mapZoom 之外，缩放时只更新位置，圆点大小保持不变
function cursorOverlayMarkup() {
  const col = c('--chart-line');
  const ink = mapInk();
  return '<g id="cursorOverlay" pointer-events="none">' +
    '<g id="mapCursor" opacity="0">' +
      '<circle r="9" fill="none" stroke="' + col + '" stroke-width="1.2" opacity=".5"/>' +
      '<circle r="4.2" fill="' + col + '" stroke="' + ink + '" stroke-width="1.4"/>' +
    '</g></g>';
}

// 历史圈的实际走线（视图坐标）；旧数据没有坐标时退回赛道中心线
export function lapLinePoints(lp, M) {
  if (lp && Array.isArray(lp.line) && lp.line.length) return lp.line;
  if (lp && Array.isArray(lp.X) && Array.isArray(lp.Y) && lp.X.length) {
    return lp.X.map((x, i) => ({ x, y: lp.Y[i] }));
  }
  return M?.pts || [];
}

export const smoothClosedPoints = smoothLoop;

function smoothClosedArray(values, window) {
  const n = values.length;
  if (!n || window <= 0) return values;
  return values.map((_, i) => {
    let sum = 0, count = 0;
    for (let k = -window; k <= window; k += 1) {
      sum += values[(i + k + n) % n]; count += 1;
    }
    return sum / count;
  });
}

export function drawHeatmap(cv, lp, M, view = null) {
  if (!cv) return;
  const ctx = cv.getContext('2d');
  const w = cv.clientWidth || 420, h = Math.round(w * 0.62), dpr = window.devicePixelRatio || 1;
  cv.width = w * dpr; cv.height = h * dpr; cv.style.height = h + 'px';
  const zoom = view?.scale ?? 1, tx = view?.tx ?? 0, ty = view?.ty ?? 0;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
  const s = Math.min((w - 24) / 480, (h - 20) / 290), ox = (w - 480 * s) / 2, oy = (h - 290 * s) / 2;
  ctx.translate(ox, oy);
  ctx.scale(s, s);
  ctx.translate(tx, ty);
  ctx.scale(zoom, zoom);
  const lw = px => px / (s * zoom); // 保持屏幕像素宽度
  ctx.lineCap = 'round'; ctx.lineJoin = 'round';

  // 折线本来就是按进度重采样的离散点，直接画会在弯里显得很尖；
  // 这里对几何和踏板值都做轻度平滑，只影响热力图显示。
  const line = lapLinePoints(lp, M);
  const n = line.length;
  const pts = smoothClosedPoints(line, 1);
  // 走线点自带该处的踏板值（时间序、含出赛道段）时逐点对齐；
  // 老记录没有这两个字段，回退到「按进度等分的 P.th / P.br」。
  const own = line[0]?.th != null && line[0]?.br != null;
  const th = smoothClosedArray(own ? line.map((it) => it.th) : lp.P.th, 2);
  const br = smoothClosedArray(own ? line.map((it) => it.br) : lp.P.br, 2);

  // 背景：真实赛道路面（左右边界），旧数据回退到中心线
  if (M.edgeD) {
    const road = new Path2D(M.edgeD);
    ctx.fillStyle = c('--map-road-fill') || 'rgba(150, 162, 177, 0.13)';
    ctx.fill(road);
    ctx.globalAlpha = 0.55;
    ctx.strokeStyle = c('--map-road-line') || c('--line-2'); ctx.lineWidth = lw(1);
    ctx.stroke(road);
    ctx.globalAlpha = 1;
  } else {
    ctx.strokeStyle = c('--line'); ctx.lineWidth = lw(8);
    ctx.beginPath();
    pts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
    ctx.closePath(); ctx.stroke();
  }

  const pal = heatPalette();
  const step = 2;
  for (let i = 0; i < n; i += step) {
    const j = (i + step) % n;
    const a = pts[i], b = pts[j];
    // 每段用两端颜色做线性渐变，避免色块突变
    const grad = ctx.createLinearGradient(a.x, a.y, b.x, b.y);
    grad.addColorStop(0, heatColor(i, { th, br }, pal));
    grad.addColorStop(1, heatColor(j, { th, br }, pal));
    ctx.strokeStyle = grad;
    ctx.lineWidth = lw(4.5);
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  }

  // 赛道中心线参考
  ctx.strokeStyle = c('--text-dim'); ctx.globalAlpha = .4; ctx.lineWidth = lw(1.4); ctx.setLineDash([lw(4), lw(4)]);
  ctx.beginPath(); M.ghost.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)); ctx.closePath(); ctx.stroke();
  ctx.setLineDash([]); ctx.globalAlpha = 1;
}

function heatColor(i, P, pal) {
  return heatColorAt(P.br[i], P.th[i], pal);
}

// 热力配色随主题切换：深色主题用亮黄 / 荧光绿，浅色主题自动换成深一档，
// 否则黄色 / 黄绿色在白底上几乎看不见。
const HEAT_FALLBACK = {
  coast: [250, 204, 21], // #FACC15 滑行
  brkLo: [245, 158, 11], // #F59E0B 轻刹
  brkHi: [225, 29, 72],  // #E11D48 重刹
  thrLo: [163, 230, 53], // #A3E635 轻油
  thrHi: [21, 128, 61],  // #15803D 全油
};

// 每次绘制解析一次即可：数千段热力共用同一份调色板，避免反复读取样式。
function heatPalette() {
  return {
    coast: cssRgb('--heat-coast', HEAT_FALLBACK.coast),
    brkLo: cssRgb('--heat-brake-lo', HEAT_FALLBACK.brkLo),
    brkHi: cssRgb('--heat-brake-hi', HEAT_FALLBACK.brkHi),
    thrLo: cssRgb('--heat-thr-lo', HEAT_FALLBACK.thrLo),
    thrHi: cssRgb('--heat-thr-hi', HEAT_FALLBACK.thrHi),
  };
}

// 解析 CSS 变量里的 #rrggbb / rgb(a) 颜色为 [r,g,b]
function cssRgb(name, fallback) {
  const raw = c(name);
  let m = /^#([0-9a-f]{6})$/i.exec(raw);
  if (m) {
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  m = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/i.exec(raw);
  if (m) return [+m[1], +m[2], +m[3]];
  return fallback;
}

const mixRgb = (a, b, t) => [
  Math.round(lerp(a[0], b[0], t)),
  Math.round(lerp(a[1], b[1], t)),
  Math.round(lerp(a[2], b[2], t)),
];
const rgbCss = (c) => 'rgb(' + Math.round(c[0]) + ',' + Math.round(c[1]) + ',' + Math.round(c[2]) + ')';
const smoothstep = (edge0, edge1, x) => {
  const t = clamp((x - edge0) / (edge1 - edge0), 0, 1);
  return t * t * (3 - 2 * t);
};

// 踏板值 → RGB。数值本身不做任何夸张，只在临界点附近用权重过渡，
// 避免刹车 / 油门 / 滑行三种状态切换时出现硬色块。
function heatColorRgb(brake, throttle, pal = heatPalette()) {
  const wb = smoothstep(0.02, 0.12, brake);
  const wt = smoothstep(0.02, 0.12, throttle) * (1 - wb);
  const brakeCol = mixRgb(pal.brkLo, pal.brkHi, clamp(brake, 0, 1));
  const thrCol = mixRgb(pal.thrLo, pal.thrHi, clamp(throttle, 0, 1));
  let col = mixRgb(pal.coast, brakeCol, wb);
  col = mixRgb(col, thrCol, wt);
  return col;
}

function heatColorAt(brake, throttle, pal) {
  return rgbCss(heatColorRgb(brake, throttle, pal));
}

