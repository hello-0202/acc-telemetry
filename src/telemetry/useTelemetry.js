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
// 前端是否「期望正在监测」（用户点了开始 / 启动时自动开始）。
// 停止之后后端可能还会补发一条 GAME_NOT_FOUND，不能据此把状态改成等待。
let wanted = false;
// 当前 error 是否只是「连不上后端」（端口探测 / WS 建立失败）。
// 这类错误在应用刚启动、sidecar 还没解压完时必然会出现一次，
// 后端一旦可达就该自动作废，而不是把界面永久钉在「连接断开」。
let connectFailed = false;

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
      // 进入新记录（换赛道 / 重开 session / 首次监测）：让历史页立刻重拉列表。
      // 此时后端可能还没把 session 写进库，真正的通知由后面的 history 事件补上。
      telemetry.historyRevision += 1;
      break;
    case 'history':
      // 后端已把新 session 写入数据库（或补写了会话类型）。
      // 只有收到这条才保证拉列表时那一行确实存在，历史页据此实时刷新。
      telemetry.historyRevision += 1;
      break;
    case 'pbref':
      // 后端按当前会话类型推送 PB 参考圈（排位只对比排位、正赛只对比正赛），
      // 仅更新 session 的 pbRef/pbMs，不重置实时帧与 delta
      if (telemetry.session) {
        telemetry.session = {
          ...telemetry.session,
          pbRef: message.pbRef ?? telemetry.session.pbRef,
          pbMs: message.pbMs ?? telemetry.session.pbMs,
        };
      }
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
      // 「游戏还没进赛道」不是故障：置为等待态，由监视页的监督循环在游戏就绪后重试，
      // 否则每次应用启动、或退出赛道回菜单都会弹一条红色错误。
      if (message.code === 'GAME_NOT_FOUND') {
        if (wanted) {
          telemetry.status = 'waiting';
          telemetry.error = null;
        }
        break;
      }
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
      // 直播结束（游戏退出 / 退到主菜单 / 暂停 / 切赛道）：清掉实时帧，
      // 不再显示冻结的旧数据；session 保留，恢复直播后由新帧重新填充。
      telemetry.frame = null;
      telemetry.deltaMs = null;
      telemetry.lastLap = null;
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
  wanted = true;
  telemetry.error = null;
  telemetry.warning = null;

  // 已有连接时直接复用（例如后端因游戏退出返回 error，但连接仍在）
  if (socket && socket.readyState === WebSocket.OPEN) {
    // 已经有会话说明后端还在记录（暂停时重新 start 是空操作）：
    // 不要把状态退化成「连接中…」，否则会一直挂在那里等不存在的响应
    if (!telemetry.session) telemetry.status = 'connecting';
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
    connectFailed = true;
    telemetry.status = 'error';
    telemetry.error = e.message || '无法连接本地后端';
    return;
  }

  const wsUrl = base.replace(/^http/, 'ws') + '/ws/telemetry';
  try {
    socket = new WebSocket(wsUrl);
  } catch (e) {
    connectFailed = true;
    telemetry.status = 'error';
    telemetry.error = `无效的 WebSocket 地址：${e.message}`;
    return;
  }

  socket.onopen = () => {
    if (!socket) return;
    connectFailed = false;
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
    connectFailed = true;
    if (telemetry.status !== 'streaming') {
      telemetry.status = 'error';
      telemetry.error = '无法连接 Python 后端，请确认 8008 端口服务已启动';
    }
  };

  socket.onclose = () => {
    stopFpsCounter();
    socket = null;
    connectFailed = true;
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
  wanted = false;
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

/**
 * 后端已经可达（/health 探测成功）时，作废「连不上后端」这类残留错误。
 *
 * 场景：应用刚启动，sidecar 还在解压，前端先尝试连接必然失败；如果不清理，
 * 界面会一直停在「连接断开 / 无法连接本地后端」，而实际后端早就好了。
 * 只清理连接类错误——后端主动返回的业务错误（如 GAME_EXITED）保持原样。
 */
export function clearConnectError() {
  if (!connectFailed) return;
  if (telemetry.status !== 'error') {
    connectFailed = false;
    return;
  }
  connectFailed = false;
  telemetry.error = null;
  // 期望监测但还没接上：回到等待态，由监督循环重新接入
  telemetry.status = wanted ? 'waiting' : 'idle';
}

export function disconnect() {
  wanted = false;
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
