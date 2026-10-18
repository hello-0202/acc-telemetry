<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

const DEFAULT_WS_URL = "ws://127.0.0.1:8008/ws/telemetry";

const wsUrl = ref(DEFAULT_WS_URL);
const status = ref("disconnected"); // connecting | connected | error | disconnected
const message = ref("");
const data = ref(null);
const rate = ref(0);

let socket = null;
let rateTimer = null;
let received = 0;

const statusText = computed(
  () =>
    ({
      connecting: "连接中…",
      connected: "已连接",
      error: "出错",
      disconnected: "未连接",
    })[status.value],
);

const gearText = computed(() => {
  const gear = data.value?.gear;
  if (gear === undefined || gear === null) return "–";
  if (gear === 0) return "R";
  if (gear === 1) return "N";
  return String(gear - 1);
});

const rpmRatio = computed(() => {
  const d = data.value;
  if (!d || !d.maxRpm) return 0;
  return Math.min(1, d.rpm / d.maxRpm);
});

function pct(value) {
  if (value === undefined || value === null) return "--";
  return `${Math.round(value * 100)}%`;
}

function formatLapTime(ms) {
  if (ms === undefined || ms === null || ms <= 0) return "0:00.000";
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.floor((ms % 60000) / 1000);
  const millis = ms % 1000;
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

function stopRateTimer() {
  if (rateTimer !== null) {
    clearInterval(rateTimer);
    rateTimer = null;
  }
}

function connect() {
  disconnect();

  message.value = "";
  data.value = null;
  rate.value = 0;
  received = 0;
  status.value = "connecting";

  try {
    socket = new WebSocket(wsUrl.value);
  } catch (err) {
    status.value = "error";
    message.value = `无效的地址：${err.message}`;
    return;
  }

  socket.onopen = () => {
    if (!socket) return;
    status.value = "connected";
    rateTimer = setInterval(() => {
      rate.value = received;
      received = 0;
    }, 1000);
  };

  socket.onmessage = (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (payload.error) {
      message.value = payload.error;
      status.value = "error";
      return;
    }
    data.value = payload;
    received += 1;
  };

  socket.onerror = () => {
    if (status.value !== "connected") {
      status.value = "error";
      message.value = "无法连接 Python 服务，请确认 server.py 已在 8008 端口运行";
    }
  };

  socket.onclose = () => {
    stopRateTimer();
    socket = null;
    if (status.value === "connected" || status.value === "connecting") {
      status.value = message.value ? "error" : "disconnected";
      if (!message.value) message.value = "连接已断开";
    }
  };
}

function disconnect() {
  stopRateTimer();
  if (socket) {
    socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
    socket.close();
    socket = null;
  }
  received = 0;
  rate.value = 0;
  status.value = "disconnected";
}

onMounted(connect);
onBeforeUnmount(disconnect);
</script>

<template>
  <main class="dashboard">
    <header class="bar">
      <div class="brand">
        <span class="dot" :class="status"></span>
        <h1>ACC 遥测测试</h1>
        <span class="status">{{ statusText }}</span>
      </div>
      <div class="controls">
        <input
          v-model="wsUrl"
          spellcheck="false"
          :disabled="status === 'connected' || status === 'connecting'"
        />
        <button
          v-if="status === 'connected' || status === 'connecting'"
          @click="disconnect"
        >
          断开
        </button>
        <button v-else @click="connect">连接</button>
      </div>
    </header>

    <p v-if="message" class="alert" :class="{ error: status === 'error' }">
      {{ message }}
    </p>

    <section class="grid">
      <div class="card">
        <span class="label">速度</span>
        <div class="speed">
          {{ data ? data.speed.toFixed(1) : "--" }}<small>km/h</small>
        </div>
      </div>

      <div class="card">
        <span class="label">档位</span>
        <div class="gear">{{ gearText }}</div>
      </div>

      <div class="card wide">
        <div class="row">
          <span class="label">转速</span>
          <span class="value">
            {{ data ? data.rpm : "--" }} / {{ data?.maxRpm || "--" }} rpm
          </span>
        </div>
        <div class="track">
          <div
            class="fill rpm"
            :class="{ redline: rpmRatio > 0.9 }"
            :style="{ width: `${rpmRatio * 100}%` }"
          ></div>
        </div>
      </div>

      <div class="card wide">
        <div class="row">
          <span class="label">油门</span>
          <span class="value">{{ pct(data?.throttle) }}</span>
        </div>
        <div class="track">
          <div
            class="fill throttle"
            :style="{ width: `${(data?.throttle ?? 0) * 100}%` }"
          ></div>
        </div>

        <div class="row">
          <span class="label">刹车</span>
          <span class="value">{{ pct(data?.brake) }}</span>
        </div>
        <div class="track">
          <div
            class="fill brake"
            :style="{ width: `${(data?.brake ?? 0) * 100}%` }"
          ></div>
        </div>
      </div>

      <div class="card wide">
        <span class="label">当前圈速</span>
        <div class="lap">{{ formatLapTime(data?.lapTime) }}</div>
      </div>
    </section>

    <footer class="foot">
      <span>推送频率 {{ rate }} Hz</span>
      <span>packetId {{ data?.packetId ?? "--" }}</span>
    </footer>
  </main>
</template>

<style scoped>
.dashboard {
  min-height: 100vh;
  box-sizing: border-box;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  background: radial-gradient(circle at top, #151b26 0%, #0b0e14 60%);
  color: #e8edf5;
  font-family: "Segoe UI", system-ui, sans-serif;
}

.bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.brand h1 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #5b6474;
}

.dot.connecting {
  background: #e7b23c;
  animation: pulse 1s infinite;
}

.dot.connected {
  background: #3ddc84;
}

.dot.error {
  background: #ff5a5f;
}

@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}

.status {
  font-size: 12px;
  color: #8b94a5;
}

.controls {
  display: flex;
  gap: 8px;
}

.controls input {
  width: 320px;
  padding: 7px 10px;
  border: 1px solid #2a3342;
  border-radius: 8px;
  background: #121722;
  color: #cdd6e4;
  font-family: Consolas, monospace;
  font-size: 12px;
  outline: none;
}

.controls input:focus {
  border-color: #3d7eff;
}

.controls input:disabled {
  opacity: 0.6;
}

.controls button {
  padding: 7px 16px;
  border: none;
  border-radius: 8px;
  background: #3d7eff;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}

.controls button:hover {
  background: #2f6ae0;
}

.alert {
  margin: 0;
  padding: 9px 12px;
  border-radius: 8px;
  background: #1b2434;
  color: #a9b4c6;
  font-size: 13px;
}

.alert.error {
  background: #2a1618;
  color: #ff9a9d;
}

.grid {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 12px;
}

.card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 16px;
  border: 1px solid #1e2632;
  border-radius: 12px;
  background: #11161f;
}

.card.wide {
  grid-column: 1 / -1;
}

.label {
  font-size: 12px;
  color: #7c8697;
  letter-spacing: 0.08em;
}

.speed {
  font-family: Consolas, "SF Mono", monospace;
  font-size: 52px;
  font-weight: 700;
  line-height: 1;
  color: #fff;
}

.speed small {
  margin-left: 6px;
  font-size: 14px;
  font-weight: 400;
  color: #7c8697;
}

.gear {
  font-family: Consolas, "SF Mono", monospace;
  font-size: 64px;
  font-weight: 700;
  line-height: 1;
  text-align: center;
  color: #ffd166;
}

.row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.value {
  font-family: Consolas, monospace;
  font-size: 13px;
  color: #cdd6e4;
}

.track {
  height: 8px;
  border-radius: 4px;
  background: #1d2430;
  overflow: hidden;
}

.fill {
  height: 100%;
  border-radius: 4px;
  transition: width 80ms linear;
}

.fill.rpm {
  background: linear-gradient(90deg, #3ddc84 0%, #ffd166 70%, #ff5a5f 100%);
  background-size: 200% 100%;
}

.fill.rpm.redline {
  animation: flash 0.25s infinite alternate;
}

.fill.throttle {
  background: #3ddc84;
}

.fill.brake {
  background: #ff5a5f;
}

@keyframes flash {
  to {
    opacity: 0.5;
  }
}

.lap {
  font-family: Consolas, "SF Mono", monospace;
  font-size: 30px;
  color: #fff;
}

.foot {
  display: flex;
  justify-content: space-between;
  font-family: Consolas, monospace;
  font-size: 11px;
  color: #5f6979;
}

@media (max-width: 640px) {
  .controls input {
    width: 100%;
  }

  .grid {
    grid-template-columns: 1fr;
  }

  .card.wide {
    grid-column: auto;
  }
}
</style>
