// 实时遥测单例：封装 WebSocket 生命周期与消息解析。
// 视图始终挂载，所以连接状态放在模块级，避免切换页面时断流。

import { reactive } from 'vue';

import { resetBaseUrl, resolveBaseUrl } from './api.js';

export const telemetry = reactive({
  status: 'idle', // idle | connecting | streaming | error | stopped
  session: null,
  frame: null,
  lastLap: null,
  deltaMs: null,
  error: null,
  warning: null,
  fps: 0,
  lapsThisSession: 0,
  historyRevision: 0,
});

let socket = null;
let fpsTimer = null;
let frameCount = 0;

function startFpsCounter() {
  stopFpsCounter();
  frameCount = 0;
  fpsTimer = setInterval(() => {
    telemetry.fps = frameCount;
    frameCount = 0;
  }, 1000);
}

function stopFpsCounter() {
  if (fpsTimer !== null) {
    clearInterval(fpsTimer);
    fpsTimer = null;
  }
  telemetry.fps = 0;
}

function interpReference(ps, ts, p) {
  if (!ps.length) return null;
  if (p <= ps[0]) return ts[0];
  if (p >= ps[ps.length - 1]) return ts[ts.length - 1];
  let i = 0;
  while (i + 1 < ps.length && ps[i + 1] < p) i++;
  const span = ps[i + 1] - ps[i] || 1;
  const t = (p - ps[i]) / span;
  return ts[i] + (ts[i + 1] - ts[i]) * t;
}

function updateDelta() {
  const frame = telemetry.frame;
  const session = telemetry.session;
  if (!frame) {
    telemetry.deltaMs = null;
    return;
  }
  if (session?.pbRef?.p?.length) {
    const reference = interpReference(session.pbRef.p, session.pbRef.tMs, frame.progress);
    telemetry.deltaMs = reference == null ? null : frame.lapTime - reference;
  } else if (session?.pbMs) {
    // 没有逐点参考时退化为按进度线性估算
    telemetry.deltaMs = frame.lapTime - session.pbMs * frame.progress;
  } else {
    telemetry.deltaMs = null;
  }
}

function handleMessage(message) {
  switch (message.type) {
    case 'session':
      telemetry.session = message;
      telemetry.frame = null;
      telemetry.lastLap = null;
      telemetry.deltaMs = null;
      telemetry.error = null;
      telemetry.lapsThisSession = 0;
      telemetry.status = 'streaming';
      break;
    case 'frame':
      telemetry.frame = message;
      telemetry.status = 'streaming';
      frameCount += 1;
      updateDelta();
      break;
    case 'lap':
      telemetry.lastLap = message;
      telemetry.lapsThisSession += 1;
      telemetry.historyRevision += 1;
      break;
    case 'error':
      telemetry.error = message.message || message.error || '后端返回错误';
      telemetry.status = 'error';
      break;
    case 'warning':
      telemetry.warning = message.message || null;
      break;
    case 'trackmap':
      // 后端用实跑数据生成了真实赛道形状：只替换地图，不重置当前圈状态
      if (telemetry.session && telemetry.session.track?.id === message.trackId) {
        telemetry.session = {
          ...telemetry.session,
          track: {
            ...telemetry.session.track,
            spline: message.spline,
            geometryKnown: message.geometryKnown !== false,
          },
        };
      }
      break;
    case 'stopped':
      telemetry.status = 'stopped';
      break;
    default:
      break;
  }
}

function closeSocket() {
  if (!socket) return;
  socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
  try {
    socket.close();
  } catch (e) { /* ignore */ }
  socket = null;
}

export async function start() {
  telemetry.error = null;
  telemetry.warning = null;

  // 已有连接时直接复用（例如后端因游戏退出返回 error，但连接仍在）
  if (socket && socket.readyState === WebSocket.OPEN) {
    telemetry.status = 'connecting';
    socket.send(JSON.stringify({ cmd: 'start' }));
    return;
  }
  if (socket && socket.readyState === WebSocket.CONNECTING) return;

  closeSocket();
  telemetry.status = 'connecting';

  let base;
  try {
    base = await resolveBaseUrl();
  } catch (e) {
    telemetry.status = 'error';
    telemetry.error = e.message || '无法连接本地后端';
    return;
  }

  const wsUrl = base.replace(/^http/, 'ws') + '/ws/telemetry';
  try {
    socket = new WebSocket(wsUrl);
  } catch (e) {
    telemetry.status = 'error';
    telemetry.error = `无效的 WebSocket 地址：${e.message}`;
    return;
  }

  socket.onopen = () => {
    if (!socket) return;
    socket.send(JSON.stringify({ cmd: 'start' }));
  };

  socket.onmessage = (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch (e) {
      return;
    }
    handleMessage(message);
  };

  socket.onerror = () => {
    if (telemetry.status !== 'streaming') {
      telemetry.status = 'error';
      telemetry.error = '无法连接 Python 后端，请确认 8008 端口服务已启动';
    }
  };

  socket.onclose = () => {
    stopFpsCounter();
    socket = null;
    // 后端可能换了端口，下次 start 时重新探测
    resetBaseUrl();
    if (telemetry.status === 'connecting' || telemetry.status === 'streaming') {
      telemetry.status = telemetry.error ? 'error' : 'stopped';
      if (!telemetry.error) telemetry.error = '连接已断开';
    }
  };

  startFpsCounter();
}

export function stop() {
  if (socket && socket.readyState === WebSocket.OPEN) {
    try {
      socket.send(JSON.stringify({ cmd: 'stop' }));
    } catch (e) { /* ignore */ }
  }
  telemetry.status = 'stopped';
  telemetry.session = null;
  telemetry.frame = null;
  telemetry.deltaMs = null;
  stopFpsCounter();
  // 给服务端一点时间发送 stopped 并收尾，然后断开
  setTimeout(() => {
    closeSocket();
    telemetry.status = 'idle';
  }, 200);
}

export function disconnect() {
  stopFpsCounter();
  closeSocket();
  telemetry.status = 'idle';
  telemetry.session = null;
  telemetry.frame = null;
  telemetry.lastLap = null;
  telemetry.deltaMs = null;
  telemetry.error = null;
}

export function steerDegrees(steer) {
  return (steer ?? 0) * 450;
}

export function gearText(rawGear) {
  if (rawGear === 0) return 'R';
  if (rawGear === 1) return 'N';
  return String(rawGear - 1);
}

export function useTelemetry() {
  return { telemetry, start, stop, disconnect, steerDegrees, gearText };
}
