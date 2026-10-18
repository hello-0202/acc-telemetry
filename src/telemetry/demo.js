// 默认/演示数据：当已点击「开始监测」但后端未连接、或游戏未启动/暂无真实数据时，
// 在一条内置程序化赛道上生成平滑的演示帧，避免监测页出现空/零值。
// 真实数据一旦到达，MonitorView 会自动切回真实帧。

import { ref, onUnmounted } from 'vue';
import { modelFromSpline } from './adapt.js';
import { clamp, vnoise } from './model.js';

function genControlPoints() {
  // 一条带 3 个明显弯 + 1 段长直的闭合样条（无需外部地图库）
  const cps = [];
  const R = 200;
  for (let k = 0; k < 48; k++) {
    const a = (k / 48) * Math.PI * 2;
    const r = 1 + 0.34 * Math.sin(3 * a + 0.6) + 0.12 * Math.cos(2 * a);
    cps.push([Math.cos(a) * R * r * 1.4, Math.sin(a) * R * r]);
  }
  return cps;
}

function build() {
  const M = modelFromSpline(genControlPoints(), 180, 4200);
  const n = M.n, pts = M.pts;
  if (!n) return null;

  const dx = new Array(n), dy = new Array(n), kap = new Array(n);
  for (let i = 0; i < n; i++) {
    const a = pts[(i - 1 + n) % n], b = pts[i], c = pts[(i + 1) % n];
    dx[i] = c.x - a.x; dy[i] = c.y - a.y;
    const la = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    const lb = Math.hypot(c.x - b.x, c.y - b.y) || 1;
    const cross = (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x);
    kap[i] = cross / (la * lb + 1e-6); // signed curvature
  }
  const absK = kap.map(Math.abs);
  const KAP = Math.max(...absK, 1e-6);
  const VMAX = 268, CORNER = 78;
  const spd = new Array(n);
  for (let i = 0; i < n; i++) spd[i] = CORNER + (VMAX - CORNER) * (1 - clamp(absK[i] / KAP, 0, 1) ** 0.8);

  // 运动学约束：刹车减速 + 牵引加速
  const A = 11, B = 28, ds = 4200 / n;
  for (let i = 1; i < n; i++) {
    const v = spd[i - 1] / 3.6;
    spd[i] = Math.min(spd[i], Math.sqrt(Math.max(v * v + 2 * A * ds, 0)) * 3.6);
  }
  for (let i = n - 2; i >= 0; i--) {
    const v = spd[i + 1] / 3.6;
    spd[i] = Math.min(spd[i], Math.sqrt(Math.max(v * v + 2 * B * ds, 0)) * 3.6);
  }

  // 圈速与逐点时间参考
  let lapS = 0;
  const T = new Array(n);
  for (let i = 0; i < n; i++) {
    lapS += ds / Math.max(spd[i] / 3.6, 5);
    T[i] = lapS;
  }
  const lapMs = lapS * 1000;

  // 转向（航向差）
  const steer = new Array(n);
  for (let i = 0; i < n; i++) {
    const h0 = Math.atan2(dy[(i - 2 + n) % n], dx[(i - 2 + n) % n]);
    const h1 = Math.atan2(dy[i], dx[i]);
    let d = h1 - h0;
    while (d > Math.PI) d -= 2 * Math.PI;
    while (d < -Math.PI) d += 2 * Math.PI;
    steer[i] = clamp(d / 0.6, -1, 1);
  }

  const session = {
    type: 'session',
    track: {
      id: 'demo', name: '示例赛道 · Demo', lengthM: 4200,
      spline: pts.map((p) => [+p.x.toFixed(1), +p.y.toFixed(1)]),
      sectors: [1 / 3, 2 / 3],
      corners: Array.from({ length: 10 }, (_, k) => ({ pos: k / 10, label: String(k + 1) })),
    },
    car: { id: 'demo_gt3', name: 'GT3 示例车' },
    carSpec: { maxRpm: 9000, gearCount: 6, maxFuel: 100 },
    pbMs: Math.round(lapMs * 0.985),
    pbRef: { p: Array.from({ length: n }, (_, i) => i / n), tMs: T.map((t) => t * 1000) },
  };
  return { M, n, spd, steer, T, lapMs, session };
}

export function useMonitorDemo() {
  const active = ref(false);
  const frame = ref(null);
  const session = ref(null);
  let model = null, raf = 0, last = 0, p = 0;

  function synth(p) {
    const m = model, n = m.n;
    const i = Math.min(n - 1, Math.floor(p * n));
    const iPrev = (i - 1 + n) % n;
    const speed = Math.max(0, m.spd[i] + vnoise(p * 17.3) * 1.2);

    // 档位 / 转速
    const bands = [0, 60, 110, 160, 210, 260, 300];
    let g = 1;
    for (let k = 1; k < bands.length - 1; k++) if (speed >= bands[k]) g = k;
    const lo = bands[g], hi = bands[g + 1];
    const rpm = Math.round(3200 + ((speed - lo) / Math.max(1, hi - lo)) * 5600);
    const gear = g + 1; // 1-based raw → gearText 显示 1..6

    // 踏板（纵向加速度派生）
    const dv = (m.spd[i] - m.spd[iPrev]) / 3.6;
    const dt = (m.T[i] - m.T[iPrev]) || 0.05;
    const a = dv / dt;
    let throttle = 0, brake = 0;
    if (a > 0.3) throttle = clamp(a / 5, 0, 1);
    else if (a < -0.5) brake = clamp(-a / 9, 0, 1);

    const st = m.steer[i];
    const load = speed / 268;
    const base = 88 + load * 10 + brake * 14;
    const corners = ['FL', 'FR', 'RL', 'RR'];
    const offs = [2, -1, 4, 1];
    const wheels = corners.map((c, k) => {
      const temp = base + offs[k] + vnoise(p * 9 + k * 2.1) * 1.5 + (k >= 2 ? 4 : 0);
      const bt = 340 + brake * 230 + (k >= 2 ? 40 : 0) + vnoise(p * 11 + k) * 8;
      const pr = 27.4 + (k % 2 ? -0.2 : 0.2) + vnoise(p * 13 + k) * 0.15;
      return {
        corner: c,
        pressure: +pr.toFixed(1),
        temp: +temp.toFixed(1),
        brakeTemp: +bt.toFixed(0),
      };
    });

    return {
      type: 'frame', t: Date.now(), lap: 4, lapTime: Math.round(p * m.lapMs), valid: true,
      speed: +speed.toFixed(1), rpm, gear,
      throttle: +throttle.toFixed(2), brake: +brake.toFixed(2),
      steer: +st.toFixed(2), progress: p, wheels,
    };
  }

  function loop(ts) {
    if (!last) last = ts;
    const dt = Math.min(0.05, (ts - last) / 1000);
    last = ts;
    if (model) {
      p = (p + dt / (model.lapMs / 1000)) % 1;
      frame.value = synth(p);
    }
    raf = requestAnimationFrame(loop);
  }

  function start() {
    if (active.value) return;
    if (!model) model = build();
    if (!model) return;
    active.value = true;
    session.value = model.session;
    last = 0;
    raf = requestAnimationFrame(loop);
  }
  function stop() {
    active.value = false;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    frame.value = null;
  }

  onUnmounted(stop);
  return { active, frame, session, start, stop };
}
