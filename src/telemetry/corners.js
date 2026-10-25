// ============================================================================
// 弯角名称表。
//
// 曲率能自动找出「哪里是弯」，但找不出「这个弯叫什么」。真实弯名（Rettifilo /
// Lesmo / Ascari…）需要人工对照，而且 F1 习惯把连续弯合成一组（T1–T2、
// T8–T10），所以这里按赛道维护一张表，和曲率识别配合使用：
//
//   · 表里有的赛道 → 直接用表的点位与命名（比纯曲率识别更准，编号也和真实弯号一致）
//   · 表里没有的赛道 → 退回 apex.js 的曲率识别，命名 T1、T2…
//
// 位置用「占圈长比例」而不是绝对米数：换采样精度、或不同来源的圈长统计口径
// （fastlane 的 distance 字段 vs 逐段累加）都不会错位。
// ============================================================================

import { detectApexes } from './apex.js';

export const CORNER_CATALOG = {
  monza: [
    { p: 0.1677, code: 'T1–T2', name: 'Rettifilo' },
    { p: 0.2363, code: 'T3', name: 'Curva Grande' },
    { p: 0.3701, code: 'T4–T5', name: 'Roggia' },
    { p: 0.4396, code: 'T6', name: 'Lesmo 1' },
    { p: 0.4969, code: 'T7', name: 'Lesmo 2' },
    { p: 0.6985, code: 'T8–T10', name: 'Ascari' },
    { p: 0.9035, code: 'T11', name: 'Parabolica' },
  ],
};

function lerp(a, b, t) {
  return a + (b - a) * t;
}

/** 在 geometry.points（按 distance 递增的闭合圈）里按比例插值出一个点位。 */
function sampleAt(points, ratio) {
  const n = points.length;
  const first = Number(points[0].distance) || 0;
  const last = Number(points[n - 1].distance) || 0;
  // 闭合圈：最后一段要从末点走回起点
  const total = last - first + Math.hypot(
    Number(points[0].x) - Number(points[n - 1].x),
    Number(points[0].z) - Number(points[n - 1].z)
  );
  const target = first + total * Math.min(0.999999, Math.max(0, ratio));

  let i = 0;
  while (i + 1 < n && Number(points[i + 1].distance) <= target) i += 1;
  const a = points[i];
  const b = points[(i + 1) % n];
  const da = Number(a.distance) || 0;
  const db = Number(b.distance) || da;
  const span = db - da || 1;
  const t = Math.min(1, Math.max(0, (target - da) / span));

  return {
    index: i,
    distance: target,
    x: lerp(Number(a.x), Number(b.x), t),
    z: lerp(Number(a.z), Number(b.z), t),
    radius: Number(a.radius) || 0,
  };
}

/**
 * 生成某条赛道的弯心列表。
 * 返回 null 表示这张表里没有该赛道（调用方自行回退）。
 */
export function catalogApexes(trackId, geometry) {
  const table = CORNER_CATALOG[String(trackId || '').toLowerCase()];
  if (!table || !geometry?.points?.length) return null;
  return table.map((c) => {
    const at = sampleAt(geometry.points, c.p);
    return {
      name: c.code,
      title: c.name,
      x: at.x,
      z: at.z,
      distance: at.distance,
      radius: at.radius,
      index: at.index,
    };
  });
}

/** 有表用表，没有表用曲率识别——两个视图都走这里，保证弯号一致。 */
export function apexesForTrack(trackId, geometry) {
  const fromCatalog = catalogApexes(trackId, geometry);
  if (fromCatalog) return fromCatalog;
  if (!geometry?.points?.length) return [];
  return detectApexes(geometry.points, { lapLen: geometry.lengthM });
}
