// ============================================================================
// Drawing helpers — build SVG/Canvas for the history analysis panel.
// All colors are resolved via cssVar() so they render correctly inside SVG
// attributes (presentation attributes don't support var()).
// ============================================================================
import { cssVar, clamp, lerp, mixHex, fmtLap, fmtDelta } from './model.js';

const c = (n) => cssVar(n);

export function panelSVG(cfg, W, H) {
  const m = { l: 38, r: 10, t: 8, b: 2 }, iw = W - m.l - m.r;
  const BAND = 13, yBot = H - BAND - 1, ih = yBot - m.t;
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
    s += '<rect x="' + cx0.toFixed(1) + '" y="' + (yBot + 1).toFixed(1) + '" width="' + (cx1 - cx0).toFixed(1) + '" height="' + BAND + '" fill="' + (cc % 2 ? c('--chart-band-b') : c('--chart-band-a')) + '"/>';
    s += '<text x="' + ((cx0 + cx1) / 2).toFixed(1) + '" y="' + (H - 3).toFixed(1) + '" text-anchor="middle" font-family="' + c('--font-mono') + '" font-size="7.5" fill="' + c('--text-faint') + '">' + Math.round(((pa + pb) / 2) * 100) + '%</text>';
  }
  // 横向网格
  for (let k = 0; k <= 3; k++) {
    const v = cfg.min + (cfg.max - cfg.min) * k / 3, yy = y(v).toFixed(1);
    s += '<line x1="' + m.l + '" y1="' + yy + '" x2="' + (W - m.r) + '" y2="' + yy + '" stroke="' + c('--grid-line') + '"/>';
    s += '<text x="' + (m.l - 6) + '" y="' + (yy + 3.5) + '" text-anchor="end" font-family="' + c('--font-mono') + '" font-size="8.5" fill="' + c('--text-faint') + '">' + v.toFixed(0) + '</text>';
  }
  // x轴
  s += '<line x1="' + m.l + '" y1="' + yBot + '" x2="' + (W - m.r) + '" y2="' + yBot + '" stroke="' + c('--line-2') + '"/>';
  // 分段（S1/S2/S3）虚线
  [1 / 3, 2 / 3].forEach(p => {
    if (p >= x0 && p <= x1) {
      s += '<line x1="' + px(p).toFixed(1) + '" y1="' + m.t + '" x2="' + px(p).toFixed(1) + '" y2="' + yBot + '" stroke="' + c('--line-2') + '" stroke-dasharray="3 3"/>';
    }
  });
  // 分段标签
  [[1 / 6, 'S1'], [1 / 2, 'S2'], [5 / 6, 'S3']].forEach(([p, label]) => {
    if (p >= x0 - 0.02 && p <= x1 + 0.02) {
      const lx = clamp(px(p), m.l + 10, W - m.r - 10);
      s += '<text x="' + lx.toFixed(1) + '" y="' + (yBot - 3).toFixed(1) + '" text-anchor="middle" font-family="' + c('--font-mono') + '" font-size="8" fill="' + c('--text-dim') + '">' + label + '</text>';
    }
  });
  s += '<g clip-path="url(#' + clipId + ')">';
  cfg.series.forEach(se => {
    let d = '';
    for (let i = 0; i < cfg.n; i += 1) {
      d += (i === 0 ? 'M' : 'L') + px(i / cfg.n).toFixed(1) + ',' + y(se.fn(i)).toFixed(1) + ' ';
    }
    s += '<path d="' + d + '" fill="none" stroke="' + se.color + '" stroke-width="' + (se.dash ? 1.4 : 1.8) + '" ' + (se.dash ? 'stroke-dasharray="4 3" opacity=".9"' : '') + ' stroke-linejoin="round"/>';
  });
  s += '</g>';
  s += '<text x="' + (m.l - 6) + '" y="' + (m.t + 8) + '" text-anchor="end" font-family="' + c('--font-display') + '" font-weight="600" font-size="9" fill="' + c('--text-dim') + '">' + cfg.title + '</text>';
  s += '</svg>';
  return s;
}

export function renderCharts(wrap, lp, pb, M, showPb = true, view = null) {
  if (!wrap) return;
  Array.prototype.slice.call(wrap.querySelectorAll('.chart-panel')).forEach(e => e.remove());
  const W = Math.max(240, wrap.clientWidth || 420), n = M.n;
  const x0 = view?.x0 ?? 0, x1 = view?.x1 ?? 1;

  const speedSeries = [];
  if (showPb) speedSeries.push({ fn: i => pb.v[i] * 3.6, color: c('--text-dim'), dash: true });
  speedSeries.push({ fn: i => lp.v[i] * 3.6, color: c('--signal-text') });

  const pedalSeries = [];
  if (showPb) {
    pedalSeries.push({ fn: i => pb.P.th[i] * 100, color: c('--good'), dash: true });
    pedalSeries.push({ fn: i => pb.P.br[i] * 100, color: c('--hot'), dash: true });
  }
  pedalSeries.push({ fn: i => lp.P.th[i] * 100, color: c('--good') });
  pedalSeries.push({ fn: i => lp.P.br[i] * 100, color: c('--hot') });

  const spd = document.createElement('div'); spd.className = 'chart-panel';
  spd.innerHTML = panelSVG({ title: '速度', min: 0, max: 340, n: n, series: speedSeries, x0, x1, clipId: 'chart-clip-speed' }, W, 110);
  const ped = document.createElement('div'); ped.className = 'chart-panel';
  ped.innerHTML = panelSVG({ title: '踏板', min: 0, max: 105, n: n, series: pedalSeries, x0, x1, clipId: 'chart-clip-ped' }, W, 96);
  wrap.appendChild(spd); wrap.appendChild(ped);
}

export function drawMiniMap(svg, M) {
  if (!svg) return;
  let h = '<path class="map-base" d="' + M.d + '"/>';
  h += '<path d="' + M.d + '" fill="none" stroke="' + c('--line-2') + '" stroke-width="1.4" stroke-dasharray="2 4"/>';
  const sp = M.pts[0];
  h += '<line x1="' + (sp.x - 10) + '" y1="' + (sp.y - 10) + '" x2="' + (sp.x + 10) + '" y2="' + (sp.y + 10) + '" stroke="' + c('--signal-text') + '" stroke-width="3"/>';
  for (let k = 0; k < M.n; k += Math.floor(M.n / 10)) { const p = M.pts[k]; h += '<circle class="map-corner" cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="3.4"/>'; }
  h += '<circle id="miniDot" cx="0" cy="0" r="9" fill="none" stroke="' + c('--signal-text') + '" stroke-width="2"/>';
  svg.innerHTML = '<g id="mapZoom">' + h + '</g>';
}

export function smoothClosedPoints(points, window) {
  const n = points.length;
  if (!n || window <= 0) return points;
  return points.map((_, i) => {
    let sx = 0, sy = 0, count = 0;
    for (let k = -window; k <= window; k += 1) {
      const p = points[(i + k + n) % n];
      sx += p.x; sy += p.y; count += 1;
    }
    return { x: sx / count, y: sy / count };
  });
}

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
  const pts = smoothClosedPoints(M.pts, 1);
  const th = smoothClosedArray(lp.P.th, 2);
  const br = smoothClosedArray(lp.P.br, 2);

  ctx.strokeStyle = c('--line'); ctx.lineWidth = lw(8);
  ctx.beginPath();
  pts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
  ctx.closePath(); ctx.stroke();

  const step = 2;
  for (let i = 0; i < M.n; i += step) {
    const j = (i + step) % M.n;
    const a = pts[i], b = pts[j];
    // 每段用两端颜色做线性渐变，避免色块突变
    const grad = ctx.createLinearGradient(a.x, a.y, b.x, b.y);
    grad.addColorStop(0, heatColor(i, { th, br }));
    grad.addColorStop(1, heatColor(j, { th, br }));
    ctx.strokeStyle = grad;
    ctx.lineWidth = lw(7);
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  }

  ctx.strokeStyle = c('--text-dim'); ctx.globalAlpha = .5; ctx.lineWidth = lw(1.6); ctx.setLineDash([lw(4), lw(4)]);
  ctx.beginPath(); M.ghost.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)); ctx.closePath(); ctx.stroke();
  ctx.setLineDash([]); ctx.globalAlpha = 1;
  const st = pts[0];
  ctx.strokeStyle = c('--signal-text'); ctx.lineWidth = lw(2.4);
  ctx.beginPath(); ctx.moveTo(st.x - 9, st.y - 9); ctx.lineTo(st.x + 9, st.y + 9); ctx.stroke();
}

function heatColor(i, P) {
  const b = P.br[i], t = P.th[i];
  // 刹车：琥珀 → 红；油门：青柠 → 深绿；滑行：亮黄
  if (b > 0.03) return mixHex('#F59E0B', '#E11D48', clamp(b, 0, 1));
  if (t > 0.03) return mixHex('#A3E635', '#15803D', clamp(t, 0, 1));
  return '#FACC15';
}

