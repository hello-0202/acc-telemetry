// 预览用演示数据。
//
// 直接取契约里的 `example`（python-server/contracts/telemetry-state.json），
// 这样主题作者在设置页预览时看到的字段，和真机上的 state 完全一致 ——
// 契约一改，预览数据跟着改，不会再出现「预览能跑、真机没数据」。
//
// 数值会按时间做几组正弦波动（速度 / 转速 / 本圈时间 / delta），
// 让放大预览看起来是活的；缩略图用同一个对象的静态快照，不额外跑定时器。
import contract from '../../python-server/contracts/telemetry-state.json';

const BASE = contract?.example && typeof contract.example === 'object'
  ? contract.example
  : { v: 1, status: 'idle', car: {}, lap: {}, track: {}, wheels: [] };

const clone = (v) => JSON.parse(JSON.stringify(v));

let seq = Number(BASE.seq) || 0;
let clockMs = 0;
let latest = null;
let timer = null;
const listeners = new Set();

const TICK_MS = 500;

function wave(periodMs, phase = 0) {
  return Math.sin((clockMs / periodMs) * Math.PI * 2 + phase);
}

function gearFor(speed) {
  if (speed < 10) return 1;
  if (speed < 95) return 2;
  if (speed < 145) return 3;
  if (speed < 198) return 4;
  if (speed < 245) return 5;
  return 6;
}

/** 造一帧演示状态（深拷贝，改坏了也不影响 BASE）。 */
export function nextMockState() {
  seq += 1;
  clockMs += TICK_MS;

  const s = clone(BASE);
  s.seq = seq;
  s.t = Date.now();
  s.status = 'streaming';

  const speed = 208 + wave(11000) * 44 + wave(2300) * 9;
  const rpm = 4300 + wave(2300, 0.9) * 2100 + wave(11000) * 700;
  const gear = gearFor(speed);

  s.car = {
    ...s.car,
    speed: Math.round(speed * 10) / 10,
    rpm: Math.round(rpm),
    gear,
    gearText: String(gear),
    throttle: Math.max(0, Math.min(1, 0.55 + wave(11000) * 0.6)),
    brake: Math.max(0, Math.min(1, -wave(11000) * 0.45)),
    steer: wave(7000, 1.2) * 0.22,
    gLat: -wave(7000, 1.2) * 1.6,
    gLong: wave(11000) * 0.5,
    fuel: Math.max(0, (s.car?.fuel ?? 60) - clockMs / 900000),
  };

  // 本圈时间 41s → 87s 循环，delta 正负交替，进度跟着走
  const lapMs = 41230 + (clockMs % 46000);
  const lapTotal = Number(s.lap?.bestMs) || 92310;
  s.lap = {
    ...s.lap,
    timeMs: lapMs,
    progress: Math.min(0.999, lapMs / lapTotal),
    deltaMs: Math.round(wave(9000) * 780),
    sector: Math.min(3, 1 + Math.floor((lapMs / lapTotal) * 3)),
    inPit: false,
    inPitLane: false,
  };

  if (s.position) {
    s.position = { ...s.position, distanceM: 14233 + (lapMs / lapTotal) * 5793 };
  }

  // 胎温 / 胎压轻微抖动，让轮胎类主题看起来不是一张死图
  if (Array.isArray(s.wheels)) {
    s.wheels = s.wheels.map((w, i) => ({
      ...w,
      temp: Math.round(((w.temp ?? 80) + wave(15000, i * 1.6) * 3.2) * 10) / 10,
      brakeTemp: Math.round((w.brakeTemp ?? 400) + wave(9000, i) * 60),
      wear: Math.min(1, (w.wear ?? 0.1) + clockMs / 36000000),
    }));
  }

  return s;
}

/** 共享的当前演示帧（第一次调用时才生成，之后跟随定时器更新）。 */
export function currentMockState() {
  if (!latest) latest = nextMockState();
  return latest;
}

/**
 * 契约 `example` 的深拷贝，不带任何波形。
 * 给 `ThemeDevView`（主题调试页）当「手动可控」的底座用 —— 那边要的是
 * 定格在某一个数值上看排版，而不是跟着正弦波晃。
 */
export function baseMockState() {
  return clone(BASE);
}

/**
 * 订阅演示数据：每 500ms 推一帧。多个预览共用同一个定时器，退订到 0 才停。
 * @param {(state: object) => void} cb
 * @returns {() => void} 退订
 */
export function subscribeMockState(cb, intervalMs = TICK_MS) {
  listeners.add(cb);
  cb(currentMockState());
  if (timer === null) timer = setInterval(() => {
    latest = nextMockState();
    for (const fn of listeners) fn(latest);
  }, intervalMs);
  return () => {
    listeners.delete(cb);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}
