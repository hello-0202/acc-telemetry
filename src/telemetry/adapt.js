// 把后端契约数据转换为 draw.js / 视图需要的数组形状。
// 所有历史对比都按进度 p 对齐，固定重采样到同一 N。

import { clamp } from './model.js';

export const DETAIL_POINTS = 1200;

export function resampleClosed(spline, n = DETAIL_POINTS) {
  if (!Array.isArray(spline) || spline.length < 2 || n < 2) return [];
  const pts = spline.map((p) => ({ x: Number(p[0]), y: Number(p[1]) }));
  const cum = [0];
  let length = 0;
  for (let i = 0; i < pts.length; i += 1) {
    const a = pts[i];
    const b = pts[(i + 1) % pts.length];
    length += Math.hypot(b.x - a.x, b.y - a.y);
    cum.push(length);
  }
  if (length === 0) return [];
  const out = [];
  let j = 0;
  for (let i = 0; i < n; i += 1) {
    const target = (length * i) / n;
    while (j < cum.length - 2 && cum[j + 1] < target) j += 1;
    const span = cum[j + 1] - cum[j] || 1;
    const t = (target - cum[j]) / span;
    const a = pts[j % pts.length];
    const b = pts[(j + 1) % pts.length];
    out.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
  }
  return out;
}

export function modelFromSpline(spline, n = DETAIL_POINTS, lenM = null) {
  const pts = resampleClosed(spline, n);
  const path = pts.length
    ? `M${pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join('L')}Z`
    : '';
  return {
    pts,
    n: pts.length,
    d: path,
    ghost: pts.map((p) => ({ ...p })),
    lenM,
  };
}

function lerpField(a, b, key, t) {
  const av = a[key];
  const bv = b[key];
  if (av == null && bv == null) return null;
  if (av == null) return bv;
  if (bv == null) return av;
  return av + (bv - av) * t;
}

export function lapFromDetail(detail, n = DETAIL_POINTS) {
  const samples = Array.isArray(detail.samples) ? detail.samples : [];
  const ms = Number(detail.ms) || 0;
  const count = Math.max(2, n);
  const v = new Array(count).fill(0);
  const th = new Array(count).fill(0);
  const br = new Array(count).fill(0);
  const g = new Array(count).fill(0);
  const T = new Array(count).fill(0);

  if (samples.length) {
    let cursor = 0;
    for (let i = 0; i < count; i += 1) {
      const p = (i + 0.5) / count;
      while (cursor + 1 < samples.length && samples[cursor + 1].p < p) cursor += 1;
      const a = samples[cursor];
      const b = samples[Math.min(cursor + 1, samples.length - 1)];
      const span = b.p - a.p || 1;
      const t = clamp((p - a.p) / span, 0, 1);

      v[i] = (lerpField(a, b, 'speed', t) ?? 0) / 3.6;
      th[i] = lerpField(a, b, 'throttle', t) ?? 0;
      br[i] = lerpField(a, b, 'brake', t) ?? 0;
      const tMs = lerpField(a, b, 'tMs', t);
      T[i] = (tMs != null && tMs > 0 ? tMs : p * ms) / 1000;
      const gLong = lerpField(a, b, 'gLong', t);
      g[i] = gLong == null ? 0 : gLong;
    }
  } else {
    for (let i = 0; i < count; i += 1) {
      T[i] = (ms / 1000) * ((i + 0.5) / count);
    }
  }

  // T 必须单调；最后一个点对齐到整圈时间
  for (let i = 1; i < count; i += 1) {
    if (T[i] < T[i - 1]) T[i] = T[i - 1];
  }
  if (ms > 0) T[count - 1] = ms / 1000;

  // 缺少纵向 G 时用速度差分估算（仅用于游标读数）
  for (let i = 0; i < count; i += 1) {
    if (g[i] !== 0) continue;
    if (i === 0) {
      g[i] = 0;
    } else {
      const dt = T[i] - T[i - 1];
      g[i] = dt > 0 ? clamp((v[i] - v[i - 1]) / dt / 9.81, -2.75, 1.3) : 0;
    }
  }

  return {
    no: detail.no,
    label: `L${detail.no}`,
    ms,
    kind: detail.kind,
    valid: detail.valid,
    s1: detail.s1,
    s2: detail.s2,
    s3: detail.s3,
    T,
    v,
    P: { th, br, g },
    samples,
  };
}
