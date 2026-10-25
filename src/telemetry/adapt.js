// 把后端契约数据转换为 draw.js / 视图需要的数组形状。
// 所有历史对比都按进度 p 对齐，固定重采样到同一 N。

import { buildTrackGeometry, clamp } from './model.js';

export const DETAIL_POINTS = 1200;

// 历史分析统一使用 480x290 视图坐标，与实时地图的 viewBox 保持一致
const VIEW_W = 480;
const VIEW_H = 290;
const VIEW_PAD = 8;

function pathFrom(points, closed = true) {
  if (!points || !points.length) return '';
  return (
    'M' + points.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join('L') + (closed ? 'Z' : '')
  );
}

const fmt = (p) => p.x.toFixed(1) + ',' + p.y.toFixed(1);
const fmtCtrl = (p) => p.x.toFixed(2) + ',' + p.y.toFixed(2);

// 闭合环的平滑走线：Catmull-Rom 样条转三次贝塞尔，曲线严格穿过每个采样点。
// 旧版"经过中点的二次贝塞尔"会把弯角内侧切掉，这里换掉以保留真实边距。
function loopCommands(points) {
  const n = points.length;
  let d = '';
  for (let i = 0; i < n; i += 1) {
    const p0 = points[(i - 1 + n) % n];
    const p1 = points[i];
    const p2 = points[(i + 1) % n];
    const p3 = points[(i + 2) % n];
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    d += 'C' + fmtCtrl({ x: c1x, y: c1y }) + ' ' + fmtCtrl({ x: c2x, y: c2y }) + ' ' + fmt(p2);
  }
  return d;
}

/** 左右边界闭合成一圈路面：两段曲线在起跑线处用直线连接。 */
function ringPath(left, right) {
  if (left.length < 3 || right.length < 3) {
    return pathFrom(left.concat(right.slice().reverse()));
  }
  const rev = right.slice().reverse();
  return 'M' + fmt(left[0]) + loopCommands(left) + 'L' + fmt(rev[0]) + loopCommands(rev) + 'Z';
}

export function resampleClosedPoints(points, n = DETAIL_POINTS) {
  if (!Array.isArray(points) || points.length < 2 || n < 2) return [];
  const cum = [0];
  let length = 0;
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
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
    const a = points[j % points.length];
    const b = points[(j + 1) % points.length];
    out.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
  }
  return out;
}

export function resampleClosed(spline, n = DETAIL_POINTS) {
  if (!Array.isArray(spline) || spline.length < 2 || n < 2) return [];
  return resampleClosedPoints(
    spline.map((p) => ({ x: Number(p[0]), y: Number(p[1]) })),
    n
  );
}

export function modelFromSpline(spline, n = DETAIL_POINTS, lenM = null) {
  const pts = resampleClosed(spline, n);
  return {
    pts,
    n: pts.length,
    d: pathFrom(pts),
    centerD: '',
    ghost: pts.map((p) => ({ ...p })),
    edgeD: '',
    mPerUnit: null,
    unitPerM: null,
    alt: null,
    spacing: null,
    totalLen: lenM,
    startLine: null,
    toScreen: null,
    world: false,
    lenM,
  };
}

/**
 * 用 fastlane.ai 的真实世界坐标构建历史地图模型。
 * 返回 480x290 视图坐标下的中心线、路面左右边界，以及世界坐标 → 视图坐标的变换，
 * 这样历史圈采样点的 (x, z) 就能和实时地图使用同一套坐标。
 */
export function modelFromGeometry(geometry, n = DETAIL_POINTS, lenM = null) {
  const src = Array.isArray(geometry?.points) ? geometry.points : [];
  if (src.length < 2) return null;

  // 与实时地图共用同一套几何算法：真实左右边距 + 闭合环法线 + 轻度平滑
  const g = buildTrackGeometry(src);
  if (!g) return null;

  const { minX, maxX, minZ, maxZ, spanX, spanZ } = g.bounds;
  // 竖向赛道（如 Monza）在横向卡片里只占中间一条，旋转 90° 后能铺满
  const rotate = spanZ > spanX;
  const fitW = rotate ? spanZ : spanX;
  const fitH = rotate ? spanX : spanZ;
  const scale = Math.min(
    (VIEW_W - 2 * VIEW_PAD) / fitW,
    (VIEW_H - 2 * VIEW_PAD) / fitH
  );
  const offX = (VIEW_W - fitW * scale) / 2;
  const offY = (VIEW_H - fitH * scale) / 2;

  // 世界 Z 向上，视图 Y 向下 → 翻转；旋转时交换 X/Z 并保持左右方向不镜像
  const toScreen = rotate
    ? (x, z) => ({ x: offX + (maxZ - z) * scale, y: offY + (x - minX) * scale })
    : (x, z) => ({ x: offX + (x - minX) * scale, y: offY + (maxZ - z) * scale });

  const left = g.left.map((p) => toScreen(p.x, p.z));
  const right = g.right.map((p) => toScreen(p.x, p.z));
  const centers = g.centers.map((p) => toScreen(p.x, p.z));
  const pts = resampleClosedPoints(centers, n);

  return {
    pts,
    n: pts.length,
    d: pathFrom(pts),
    // 中心线用重采样后的高密度点（不是 1200 点的曲线点）：AI 线要和路面边界
    // 用同一套采样，放大后不会出现两条线互相对不齐
    centerD: pathFrom(centers),
    ghost: pts.map((p) => ({ ...p })),
    edgeD: ringPath(left, right),
    left,
    right,
    // 视图单位 / 米：比例尺、方向箭头等按真实尺寸绘制时要用
    unitPerM: scale,
    mPerUnit: 1 / scale,
    // 海拔剖面：alt 沿圈等距（spacing 米一个点），可直接当曲线数据用
    alt: g.alt,
    altMin: g.altMin,
    altMax: g.altMax,
    spacing: g.spacing,
    totalLen: g.totalLen || lenM,
    startLine: g.start
      ? { a: toScreen(g.start.a.x, g.start.a.z), b: toScreen(g.start.b.x, g.start.b.z) }
      : null,
    toScreen,
    world: true,
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

export function lapFromDetail(detail, n = DETAIL_POINTS, model = null) {
  const samples = Array.isArray(detail.samples) ? detail.samples : [];
  const ms = Number(detail.ms) || 0;
  const count = Math.max(2, n);
  const v = new Array(count).fill(0);
  const th = new Array(count).fill(0);
  const br = new Array(count).fill(0);
  const g = new Array(count).fill(0);
  const T = new Array(count).fill(0);
  // 视图坐标下的实际走线：优先用每圈采样点，缺失时退回赛道中心线
  const X = new Array(count).fill(0);
  const Y = new Array(count).fill(0);
  let gpsPoints = 0;

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

      const wx = lerpField(a, b, 'x', t);
      const wz = lerpField(a, b, 'z', t);
      if (wx != null && wz != null && model?.toScreen) {
        const screen = model.toScreen(wx, wz);
        X[i] = screen.x;
        Y[i] = screen.y;
        gpsPoints += 1;
      } else if (model?.pts?.[i]) {
        X[i] = model.pts[i].x;
        Y[i] = model.pts[i].y;
      }
    }
  } else {
    for (let i = 0; i < count; i += 1) {
      T[i] = (ms / 1000) * ((i + 0.5) / count);
      if (model?.pts?.[i]) {
        X[i] = model.pts[i].x;
        Y[i] = model.pts[i].y;
      }
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
    X,
    Y,
    line: X.map((x, i) => ({ x, y: Y[i] })),
    hasGps: gpsPoints > count * 0.8,
    samples,
  };
}
