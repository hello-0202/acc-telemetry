// HTTP 客户端：低频业务与历史查询（REST）。
// WebSocket 实时通道见 useTelemetry.js。
//
// 后端优先监听 8008；被占用时会自动尝试 8009–8019。
// 这里通过 /health 探测实际端口，避免端口冲突导致连不上。

const PREFERRED_PORT = 8008;
const PORT_COUNT = 12;
const PROBE_TIMEOUT_MS = 350;
const TIMEOUT_MS = 4000;

let baseUrl = null;
let resolving = null;

async function probe(port) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/health`, {
      signal: controller.signal,
    });
    if (!response.ok) return null;
    const data = await response.json();
    if (data && data.ok === true && (data.dbPath || data.version)) {
      return `http://127.0.0.1:${port}`;
    }
  } catch (e) {
    /* 端口未监听或不是我们的后端 */
  } finally {
    clearTimeout(timer);
  }
  return null;
}

export function getBaseUrl() {
  return baseUrl || `http://127.0.0.1:${PREFERRED_PORT}`;
}

export function resetBaseUrl() {
  baseUrl = null;
  resolving = null;
}

export function resolveBaseUrl() {
  if (baseUrl) return Promise.resolve(baseUrl);
  if (resolving) return resolving;

  resolving = (async () => {
    const ports = [
      PREFERRED_PORT,
      ...Array.from({ length: PORT_COUNT - 1 }, (_, i) => PREFERRED_PORT + 1 + i),
    ];
    // 首次启动时 sidecar 可能还在解压，稍等后重试一轮
    for (let attempt = 0; attempt < 2; attempt += 1) {
      for (const port of ports) {
        const found = await probe(port);
        if (found) {
          baseUrl = found;
          resolving = null;
          return found;
        }
      }
      if (attempt === 0) {
        await new Promise((resolve) => setTimeout(resolve, 400));
      }
    }
    resolving = null;
    const last = PREFERRED_PORT + PORT_COUNT - 1;
    throw new Error(`无法连接本地后端（端口 ${PREFERRED_PORT}–${last}）`);
  })();

  return resolving;
}

async function request(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const base = await resolveBaseUrl();
    const headers = { ...(options.headers || {}) };
    if (options.body != null) headers['Content-Type'] = 'application/json';
    const response = await fetch(base + path, {
      ...options,
      headers,
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = response.statusText || `HTTP ${response.status}`;
      try {
        const body = await response.json();
        detail = body.detail || body.message || detail;
      } catch (e) { /* ignore */ }
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    if (response.status === 204) return null;
    return await response.json();
  } catch (err) {
    // 网络类错误：下次调用重新探测端口
    if (err && (err.name === 'TypeError' || err.name === 'AbortError')) {
      resetBaseUrl();
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export function getHealth() {
  return request('/health');
}

// 每条 session（每次进图）是一条独立历史记录
export function listSessions() {
  return request('/api/sessions');
}

export function listLaps(sessionId) {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/laps`);
}

const lapCache = new Map();

export async function getLap(sessionId, no, { fresh = false } = {}) {
  const key = `${sessionId}:${no}`;
  if (!fresh && lapCache.has(key)) return lapCache.get(key);
  const data = await request(
    `/api/sessions/${encodeURIComponent(sessionId)}/laps/${encodeURIComponent(no)}`
  );
  lapCache.set(key, data);
  return data;
}

export function invalidateLapCache() {
  lapCache.clear();
}

export function deleteSession(sessionId) {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
}

export function clearHistory() {
  return request('/api/sessions', { method: 'DELETE' });
}

export function getConfig() {
  return request('/api/config');
}

export function saveConfig(values) {
  return request('/api/config', {
    method: 'POST',
    body: JSON.stringify(values),
  });
}
