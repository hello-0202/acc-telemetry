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
  maxRadius: 100, // 半径小于该值才可能是弯心 (m)
  clusterM: 50, // 距离差小于该值的候选合并为同一个弯 (m)
};

/**
 * 局部极小值 → 阈值 → 聚类去重 → 编号。
 * trackData 视为闭合圈，所以首尾相邻；返回按 distance 升序的 T1、T2…
 */
export function detectApexes(trackData, options = {}) {
  const { maxRadius, clusterM } = { ...APEX_DEFAULTS, ...options };
  const pts = Array.isArray(trackData) ? trackData : [];
  const n = pts.length;
  if (n < 3) return [];

  const distAt = (i) => Number(pts[i].distance) || 0;
  // 非正数（缺失 / 直道）按无穷大处理，避免被误判成弯心
  const radiusAt = (i) => {
    const p = pts[(i + n) % n];
    const r = Number(p.radius_m ?? p.radius);
    return Number.isFinite(r) && r > 0 ? r : Infinity;
  };

  // 圈长：首尾归一化后，补一个采样间隔作为回到起点的缺口
  const base = distAt(0);
  const rel = (d) => d - base;
  const total = rel(distAt(n - 1));
  const step = total / Math.max(1, n - 1);
  const lapLen = options.lapLen ?? total + step;

  // 1) 局部极小值 + 阈值。平台取第一个点，所以右侧用 <=
  const candidates = [];
  for (let i = 0; i < n; i += 1) {
    const r = radiusAt(i);
    if (r >= maxRadius) continue;
    if (r < radiusAt(i - 1) && r <= radiusAt(i + 1)) {
      candidates.push({ index: i, distance: distAt(i), radius: r });
    }
  }
  if (!candidates.length) return [];

  // 2) 聚类去重：相邻候选距离差 < clusterM 视为同一个弯，保留半径更小的
  candidates.sort((a, b) => a.distance - b.distance);
  const groups = [];
  for (const c of candidates) {
    const g = groups[groups.length - 1];
    if (g && c.distance - g.lastD < clusterM) {
      g.lastD = c.distance;
      if (c.radius < g.best.radius) g.best = c;
    } else {
      groups.push({ firstD: c.distance, lastD: c.distance, best: c });
    }
  }
  // 闭环：起跑线两侧的候选可能属于同一个弯
  if (groups.length > 1) {
    const first = groups[0];
    const last = groups[groups.length - 1];
    const gapAcrossLine = lapLen - rel(last.lastD) + rel(first.firstD);
    if (gapAcrossLine < clusterM) {
      if (last.best.radius < first.best.radius) first.best = last.best;
      groups.pop();
    }
  }

  // 3) 排序 + 编号
  return groups
    .map(({ best }) => {
      const p = pts[best.index];
      return {
        name: '',
        x: Number(p.x),
        z: Number(p.z),
        distance: best.distance,
        radius: best.radius,
        index: best.index,
      };
    })
    .sort((a, b) => a.distance - b.distance)
    .map((a, i) => ({ ...a, name: 'T' + (i + 1) }));
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
