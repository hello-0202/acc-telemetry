// ============================================================================
// Telemetry utilities — pure formatting / math helpers.
// 生产数据来自后端（WS 实时 + REST 历史），这里不再生成模拟数据。
// 绘图辅助在 draw.js。
// ============================================================================

/* ----------------------------- utils ----------------------------- */
export const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
export const lerp = (a, b, t) => a + (b - a) * t;

export function fmtLap(ms) {
  if (ms == null || !isFinite(ms)) return '--:--.---';
  const m = Math.floor(ms / 60000), s = Math.floor(ms % 60000 / 1000), t = Math.floor(ms % 1000);
  return m + ':' + String(s).padStart(2, '0') + '.' + String(t).padStart(3, '0');
}
export function fmtShort(ms) {
  const s = Math.floor(ms / 1000);
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
}
export function fmtDelta(ms) {
  return (ms >= 0 ? '+' : '−') + (Math.abs(ms) / 1000).toFixed(3);
}
export function hash1(n) {
  const s = Math.sin(n * 127.1 + 0.7) * 43758.5453;
  return s - Math.floor(s);
}
export function vnoise(x) {
  const i = Math.floor(x), f = x - i, a = hash1(i), b = hash1(i + 1), u = f * f * (3 - 2 * f);
  return lerp(a, b, u) * 2 - 1;
}
export function cssVar(n) {
  return getComputedStyle(document.documentElement).getPropertyValue(n).trim();
}

// UI 等比缩放系数：以 style.css 里 13px 的基准根字号为准。
// 画布 / SVG 里写死的像素尺寸用它换算，才能和 CSS 的 rem 缩放保持一致。
export function uiScale() {
  if (typeof window === 'undefined') return 1;
  const fs = parseFloat(getComputedStyle(document.documentElement).fontSize) || 13;
  return fs / 13;
}
export function mixHex(h1, h2, t) {
  const p = h => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  const a = p(h1), b = p(h2);
  return 'rgb(' + a.map((v, i) => Math.round(lerp(v, b[i], t))).join(',') + ')';
}
export const reduceMotion = typeof window !== 'undefined' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// 闭合折线的循环滑动平均：压掉采样噪声，让赛道中心线/左右边界更柔和。
// yKey 允许世界坐标点使用 z 作为第二个分量。
export function smoothLoop(points, window = 2, yKey = 'y') {
  const n = points.length;
  if (!n || window <= 0) return points;
  const w = Math.min(window, Math.floor((n - 1) / 2));
  if (w <= 0) return points;
  return points.map((_, i) => {
    let sx = 0, sy = 0, count = 0;
    for (let k = -w; k <= w; k += 1) {
      const p = points[(i + k + n) % n];
      sx += p.x; sy += p[yKey]; count += 1;
    }
    const out = { x: sx / count };
    out[yKey] = sy / count;
    return out;
  });
}

// 闭合标量序列的循环滑动平均（边距 / 宽度这类单值字段用）。
function smoothLoopValues(values, window) {
  const n = values.length;
  const w = Math.min(Math.max(0, window | 0), Math.floor((n - 1) / 2));
  if (w <= 0) return values.slice();
  const span = 2 * w + 1;
  return values.map((_, i) => {
    let sum = 0;
    for (let k = -w; k <= w; k += 1) sum += values[(i + k + n) % n];
    return sum / span;
  });
}

// 闭合标量序列的中值滤波：fastlane 的边距偶尔出现单点跳变（例如 25 m），
// 均值滤波会把这个跳变摊到相邻点上、让路面鼓包；中值先把孤立异常点剔掉。
function medianLoopValues(values, radius) {
  const n = values.length;
  const r = Math.min(Math.max(0, radius | 0), Math.floor((n - 1) / 2));
  if (r <= 0) return values.slice();
  return values.map((_, i) => {
    const win = [];
    for (let k = -r; k <= r; k += 1) win.push(values[(i + k + n) % n]);
    win.sort((a, b) => a - b);
    return win[win.length >> 1];
  });
}

/**
 * 用 fastlane.ai 的采样点还原真实赛道路面（世界坐标，单位米）。
 *
 * 输入点：{ x, z, l, r }，l / r 是 AI 线到左 / 右边界的距离。
 *
 * 做法（详见 资料/MAP_RENDERING_NOTES.md）：
 *   1. 清洗 + 去重（首尾重复点会让切线退化成 0），然后**按弧长等距重采样**。
 *      fastlane 的原始点直道稀、弯里密，间距不均；不重采样的话同样的角度
 *      在直道和弯里对应的“米”不一样，路宽和法线都会失真。
 *   2. 左右边距先做中值滤波去掉单点跳变（例如 25 m 的异常值），再做均值平滑；
 *      窗口按采样间距折算，改动采样精度不会改变平滑强度。
 *   3. 切线用闭合环上的中心差分求，法线 = 切线逆时针旋转 90°；
 *      窗口同样按米折算，急弯处法线不会逐点抖动。
 *   4. 左边界 = 中心线 + 法线 × 左边距，右边界 = 中心线 − 法线 × 右边距。
 *      左右边距不相等，所以路面是非对称的真实形状。
 *   5. 用离散曲率算出局部转弯半径，把边距压到 0.9×半径以内：
 *      急弯处两侧边界不会互相穿过（多边形自交会渲染出破洞）。
 *
 * 返回 { centers, left, right, tangents, normals, start, bounds, count, spacing, totalLen }；
 * 点数不足时返回 null。
 */
export function buildTrackGeometry(points, opts = {}) {
  const src = Array.isArray(points) ? points : [];

  // ── 1) 清洗 + 去重 ────────────────────────────────────────────────
  const raw = [];
  for (const p of src) {
    const x = Number(p.x);
    const z = Number(p.z);
    if (!Number.isFinite(x) || !Number.isFinite(z)) continue;
    const last = raw[raw.length - 1];
    if (last && Math.hypot(last.x - x, last.z - z) < 1e-3) continue;
    const l = Number(p.l);
    const r = Number(p.r);
    const y = Number(p.y);
    raw.push({
      x,
      z,
      y: Number.isFinite(y) ? y : 0,
      l: Number.isFinite(l) && l > 0 ? l : 0,
      r: Number.isFinite(r) && r > 0 ? r : 0,
    });
  }
  if (raw.length > 2) {
    const a = raw[0], b = raw[raw.length - 1];
    if (Math.hypot(a.x - b.x, a.z - b.z) < 1e-3) raw.pop();
  }
  const n0 = raw.length;
  if (n0 < 3) return null;

  // ── 2) 按弧长等距重采样 ──────────────────────────────────────────
  const cum = new Array(n0 + 1);
  cum[0] = 0;
  for (let i = 0; i < n0; i += 1) {
    const a = raw[i], b = raw[(i + 1) % n0];
    cum[i + 1] = cum[i] + Math.hypot(b.x - a.x, b.z - a.z);
  }
  const totalLen = cum[n0];
  if (!(totalLen > 1)) return null;

  // 采样间距默认 3 m：480px 的地图卡片里一段不到 1 像素，
  // 比原始点密一倍多，弯里的形状还原得更准。
  const spacing = clamp(Number(opts.spacing) || 3, 0.5, 50);
  const n = Math.round(clamp(totalLen / spacing, 64, opts.maxPoints || 4000));
  const step = totalLen / n;
  const samples = new Array(n);
  let cursor = 0;
  for (let i = 0; i < n; i += 1) {
    const target = i * step;
    while (cursor + 1 < n0 && cum[cursor + 1] < target) cursor += 1;
    const span = cum[cursor + 1] - cum[cursor] || 1;
    const t = clamp((target - cum[cursor]) / span, 0, 1);
    const a = raw[cursor], b = raw[(cursor + 1) % n0];
    samples[i] = {
      x: a.x + (b.x - a.x) * t,
      z: a.z + (b.z - a.z) * t,
      y: a.y + (b.y - a.y) * t,
      l: a.l + (b.l - a.l) * t,
      r: a.r + (b.r - a.r) * t,
    };
  }

  // ── 3) 边距：默认只做 3 点中值（去掉孤立跳变），不做均值平滑 ─────
  //    fastlane 的边距本身就是干净的，多一层均值会把弯里的宽度变化抹平
  const medRadius = Math.max(0, Math.round((opts.widthMedM ?? 3) / step));
  const meanRadius = Math.max(0, Math.round((opts.widthMeanM ?? 0) / step));
  const widthL = smoothLoopValues(medianLoopValues(samples.map((s) => s.l), medRadius), meanRadius);
  const widthR = smoothLoopValues(medianLoopValues(samples.map((s) => s.r), medRadius), meanRadius);

  // ── 4) 中心线：默认不平滑 ────────────────────────────────────────
  //    等距重采样是沿原始折线插值，点本来就落在线上；再平滑就会切弯心，
  //    所以这里默认 0，需要压噪声时再用 posSmoothM（米）显式打开。
  const posRadius = Math.max(0, Math.round((opts.posSmoothM ?? 0) / step));
  const centers = posRadius > 0
    ? smoothLoop(samples, posRadius, 'z')
    : samples.map((s) => ({ x: s.x, z: s.z }));

  // 海拔剖面：只做 ±12 m 的均值平滑，上下坡的起伏保留下来
  const altRadius = Math.max(1, Math.round((opts.altSmoothM ?? 12) / step));
  const alt = smoothLoopValues(samples.map((s) => s.y), altRadius);
  let altMin = Infinity, altMax = -Infinity;
  for (const v of alt) {
    if (v < altMin) altMin = v;
    if (v > altMax) altMax = v;
  }

  // ── 5) 闭合环中心差分求切线 / 法线 ──────────────────────────────
  const tanRadius = Math.max(1, Math.round((opts.tangentM ?? 4) / step));
  const tangents = new Array(n);
  const normals = new Array(n);
  for (let i = 0; i < n; i += 1) {
    const a = centers[(i - tanRadius + n) % n];
    const b = centers[(i + tanRadius) % n];
    let tx = b.x - a.x;
    let tz = b.z - a.z;
    let len = Math.hypot(tx, tz);
    if (len < 1e-9) {
      // 采样点重合时退回相邻点差分
      const p = centers[(i - 1 + n) % n];
      const q = centers[(i + 1) % n];
      tx = q.x - p.x;
      tz = q.z - p.z;
      len = Math.hypot(tx, tz);
    }
    if (len < 1e-9) { tx = 1; tz = 0; len = 1; }
    tx /= len; tz /= len;
    tangents[i] = { x: tx, z: tz };
    // 左法线（世界 x-z 平面逆时针 90°），与旧版 dir_x / dir_z 的约定一致
    normals[i] = { x: -tz, z: tx };
  }

  // ── 6) 局部转弯半径 → 限制偏移，避免急弯处边界自交 ──────────────
  const radius = new Array(n);
  const kSpan = Math.max(1, Math.round((opts.curvM ?? 8) / step));
  for (let i = 0; i < n; i += 1) {
    const ta = tangents[(i - kSpan + n) % n];
    const tb = tangents[(i + kSpan) % n];
    const cross = ta.x * tb.z - ta.z * tb.x;
    const dot = clamp(ta.x * tb.x + ta.z * tb.z, -1, 1);
    const angle = Math.abs(Math.atan2(cross, dot));
    const arc = 2 * kSpan * step;
    radius[i] = angle > 1e-6 ? arc / angle : Infinity;
  }

  // ── 7) 边界点 ────────────────────────────────────────────────────
  const left = new Array(n);
  const right = new Array(n);
  let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
  for (let i = 0; i < n; i += 1) {
    const c = centers[i];
    const nrm = normals[i];
    // 半径未知（直道）时不限制；急弯里最多压到 0.9 倍半径
    const cap = Number.isFinite(radius[i]) ? radius[i] * 0.9 : Infinity;
    const lw = Math.min(widthL[i], cap);
    const rw = Math.min(widthR[i], cap);
    // fastlane 的 left/right 是「沿行进方向的左右」。在世界 x-z 平面上，
    // -n（即法线取负）才是行进方向的左手边，所以 left 用 −n、right 用 +n；
    // 装反的话整条路面会整体偏到 AI 线的另一侧，弯心贴内侧的关系就全错了。
    left[i] = { x: c.x - nrm.x * lw, z: c.z - nrm.z * lw };
    right[i] = { x: c.x + nrm.x * rw, z: c.z + nrm.z * rw };
    for (const p of [left[i], right[i], c]) {
      if (p.x < minX) minX = p.x;
      if (p.x > maxX) maxX = p.x;
      if (p.z < minZ) minZ = p.z;
      if (p.z > maxZ) maxZ = p.z;
    }
  }

  return {
    centers,
    left,
    right,
    tangents,
    normals,
    radius,
    alt,
    altMin,
    altMax,
    count: n,
    spacing: step,
    totalLen,
    start: { a: left[0], b: right[0] },
    bounds: {
      minX,
      maxX,
      minZ,
      maxZ,
      spanX: Math.max(1e-6, maxX - minX),
      spanZ: Math.max(1e-6, maxZ - minZ),
    },
  };
}

/* ----------------------------- helpers ----------------------------- */
export function tireClass(t) { return t < 75 ? 'cold' : (t > 105 ? 'hot' : 'good'); }

// ACC 天气 / 路面枚举 → 中文展示
const RAIN_TEXT = ['干地', '毛毛雨', '小雨', '中雨', '大雨', '雷暴'];
const GRIP_TEXT = ['全新', '快速', '最佳', '油滑', '潮湿', '湿滑', '积水'];
export function rainText(v) {
  return RAIN_TEXT[v] ?? '—';
}
export function gripText(v) {
  return GRIP_TEXT[v] ?? '—';
}
export function sectorsOf(T, n) {
  const i1 = Math.floor(n / 3), i2 = Math.floor(2 * n / 3);
  return [T[i1], T[i2] - T[i1], T[n - 1] - T[i2]];
}
