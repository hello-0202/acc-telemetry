// ============================================================================
// 弯心识别与标注。
// 输入为按累计距离排序的赛道点（fastlane 几何里曲率字段是 radius，
// 也兼容约定数据里的 radius_m）。历史分析页只做识别 + SVG 标注；
// Canvas 版本供实时地图使用。
// ============================================================================

import { cssVar } from './model.js';

// 主题相关颜色通过 CSS 变量解析，深 / 浅主题各自保证对比度。
export const APEX_COLOR = '#C8F53E';

export function apexColor() {
  return cssVar('--apex') || APEX_COLOR;
}

export function mapInk() {
  return cssVar('--map-ink') || '#FFFFFF';
}

export function apexGlow() {
  return cssVar('--apex-glow') || 'rgba(200, 245, 62, .20)';
}

const APEX_DEFAULTS = {
  minTurnDeg: 15, // 一段路累计转够 15° 才算一个弯（过滤微小抖动）
  floorDeg: 0.8,  // 单点转角低于此视为直道，用于切分弯段
  smooth: 0,      // 转角平滑窗口（采样点）；0=按采样间距自动取 ≈12m
  minGapM: 45,    // 两个弯心相距小于此值视为同一个弯，只留更紧的那个 (m)
};

/**
 * 按「累计转角」识别弯心，对所有赛道通用：不依赖曲率半径硬阈值，也不用手工名表。
 * 思路：沿中心线算每点转向角 → 平滑 → 把「符号一致且 |转角| 超门槛」的连续段切成一个弯，
 * 弯心取该段内半径最小的点。半径很大的快弯（长弧）因累计转角大而仍会被算作弯；
 * 半径小却只是微调的抖动因累计转角小而被滤掉。返回按 distance 升序的 T1、T2…
 */
export function detectApexes(trackData, options = {}) {
  const { minTurnDeg, floorDeg, smooth, minGapM } = { ...APEX_DEFAULTS, ...options };
  const pts = Array.isArray(trackData) ? trackData : [];
  const n = pts.length;
  if (n < 3) return [];

  const num = (v) => { const x = Number(v); return Number.isFinite(x) ? x : 0; };
  const xAt = (i) => num(pts[(i + n) % n].x);
  const zAt = (i) => num(pts[(i + n) % n].z);
  const distAt = (i) => num(pts[(i + n) % n].distance);
  const radiusAt = (i) => {
    const r = Number(pts[(i + n) % n].radius_m ?? pts[(i + n) % n].radius);
    return Number.isFinite(r) && r > 0 ? r : Infinity;
  };

  // 圈长（用于把起跑线旋转到最长直道中央）
  const total = distAt(n - 1) - distAt(0)
    + Math.hypot(xAt(0) - xAt(n - 1), zAt(0) - zAt(n - 1));

  // 每点朝向角（x-z 平面）与转向角 delta（弧度，已归一化到 [-π, π]）
  const heading = (i) => Math.atan2(xAt(i + 1) - xAt(i - 1), zAt(i + 1) - zAt(i - 1));
  const wrap = (d) => {
    while (d > Math.PI) d -= 2 * Math.PI;
    while (d < -Math.PI) d += 2 * Math.PI;
    return d;
  };
  const delta = Array.from({ length: n }, (_, i) => wrap(heading(i) - heading(i - 1)));

  // 平滑（移动平均）消除采样噪声；窗口默认按采样间距取 ≈12m
  const avgStep = total / Math.max(1, n);
  const W = smooth > 0 ? smooth : Math.max(1, Math.round(12 / Math.max(0.5, avgStep)));
  const sd = delta.map((_, i) => {
    let s = 0;
    for (let k = -W; k <= W; k += 1) s += delta[(i + k + n) % n];
    return s / (2 * W + 1);
  });

  const FLOOR = (floorDeg * Math.PI) / 180;
  const MIN_TURN = (minTurnDeg * Math.PI) / 180;

  // 把起始点旋转到「最长直道」中央，保证没有弯跨过首尾接缝
  let bestStart = 0, bestLen = 0, curStart = 0, curLen = 0;
  for (let i = 0; i <= n; i += 1) {
    if (i < n && Math.abs(sd[i]) < FLOOR) {
      curLen += 1;
    } else {
      if (curLen > bestLen) { bestLen = curLen; bestStart = curStart; }
      curLen = 0; curStart = i + 1;
    }
  }
  const rot = (bestStart + Math.floor(bestLen / 2)) % n;
  const idx = Array.from({ length: n }, (_, i) => (i + rot) % n);

  // 分段：符号一致且 |sd|≥FLOOR 的连续段 = 一个弯候选
  const corners = [];
  let i = 0;
  while (i < n) {
    if (Math.abs(sd[idx[i]]) < FLOOR) { i += 1; continue; }
    const sign = sd[idx[i]] > 0 ? 1 : -1;
    let acc = 0, bestR = Infinity, bestK = i;
    while (i < n && Math.abs(sd[idx[i]]) >= FLOOR && (sd[idx[i]] > 0 ? 1 : -1) === sign) {
      acc += sd[idx[i]];
      const r = radiusAt(idx[i]);
      if (r < bestR) { bestR = r; bestK = i; }
      i += 1;
    }
    if (Math.abs(acc) >= MIN_TURN) {
      const oi = idx[bestK];
      const p = pts[oi];
      corners.push({
        name: '',
        x: num(p.x),
        z: num(p.z),
        distance: distAt(oi),
        radius: Number.isFinite(bestR) ? bestR : 0,
        index: oi,
      });
    }
  }

  // 按里程排序后，把相距过近的相邻弯（< minGapM）合并，只留半径更小（更紧）的那个，
  // 避免一个连续弯群被拆成好几个只差几十米的 T 点。
  corners.sort((a, b) => a.distance - b.distance);
  const merged = [];
  for (const c of corners) {
    const last = merged[merged.length - 1];
    if (last && c.distance - last.distance < minGapM) {
      if (c.radius < last.radius) merged[merged.length - 1] = c;
    } else {
      merged.push(c);
    }
  }

  return merged.map((a, i) => ({ ...a, name: 'T' + (i + 1) }));
}

/**
 * Canvas 标注。世界坐标经过缩放绘制时，传入 mapScale 抵消缩放，
 * 让圆点和文字保持固定的屏幕大小。
 */
export function drawApexLabels(ctx, apexes, worldToCanvas, opts = {}) {
  if (!ctx || !apexes?.length || typeof worldToCanvas !== 'function') return;
  const { mapScale = 1, selected = null } = opts;
  const k = 1 / Math.max(1e-6, mapScale);
  const DOT_R = 4 * k;
  const SEL_R = 8 * k;
  const OFF_X = 8 * k;
  const OFF_Y = -8 * k;
  const color = apexColor();
  const ink = mapInk();

  ctx.save();
  ctx.setLineDash([]);
  ctx.lineJoin = 'round';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'alphabetic';

  for (const a of apexes) {
    const { x: cx, y: cy } = worldToCanvas(a.x, a.z);
    if (!Number.isFinite(cx) || !Number.isFinite(cy)) continue;
    const isSel = selected === a.name;

    if (isSel) {
      ctx.beginPath();
      ctx.arc(cx, cy, SEL_R, 0, Math.PI * 2);
      ctx.fillStyle = apexGlow();
      ctx.fill();
    }

    // 弯心圆点：主题描边、透明填充
    ctx.beginPath();
    ctx.arc(cx, cy, DOT_R, 0, Math.PI * 2);
    ctx.strokeStyle = ink;
    ctx.lineWidth = 1.5 * k;
    ctx.stroke();

    if (a.name) {
      ctx.font = `bold ${12 * k}px sans-serif`;
      ctx.fillStyle = isSel ? ink : color;
      ctx.fillText(a.name, cx + OFF_X, cy + OFF_Y);
    }
  }
  ctx.restore();
}

/** 地图点击吸附：返回命中半径内最近的弯心。 */
export function pickApex(apexes, worldToCanvas, cx, cy, hitRadius = 12) {
  let hit = null;
  let best = hitRadius * hitRadius;
  for (const a of apexes || []) {
    const p = worldToCanvas(a.x, a.z);
    const d = (p.x - cx) ** 2 + (p.y - cy) ** 2;
    if (d <= best) { best = d; hit = a; }
  }
  return hit;
}
