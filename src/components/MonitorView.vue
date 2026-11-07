<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ui } from '../telemetry/store.js';
import { start as startTelemetry, telemetry } from '../telemetry/useTelemetry.js';
import MonitorShell from './MonitorShell.vue';

// 打开应用即进入监测：不再有「开始 / 停止监测」按钮和顶部的状态数据条。
// 下面的监督循环会在后端 / 游戏就绪后自动接入记录；连接状态由左下角状态栏的小圆点表示。
const monitoring = ref(false);

const session = computed(() => telemetry.session);
const shell = ref(null);

// 监督循环间隔：游戏/后端稍后才就绪时自动接入，不必用户再操作
const SUPERVISE_MS = 3000;
let retryTimer = null;

function stopRetry() {
  if (retryTimer !== null) {
    clearInterval(retryTimer);
    retryTimer = null;
  }
}

/**
 * 只要处于监测状态，就在条件满足时自动接入记录。
 * 覆盖：启动时游戏还没开、退出赛道回菜单、游戏重启后重新进赛道。
 * 游戏没开 / 后端离线时不发 start——否则每 3 秒刷一次 GAME_NOT_FOUND。
 */
function tickSupervisor() {
  if (!monitoring.value) return;
  if (telemetry.status === 'streaming' || telemetry.status === 'connecting') return;
  // 后端还没确认在线（启动中 / 未知）时先别发：连接必然失败，
  // 还会在界面上留下一条「无法连接本地后端」。等 /health 探到在线再接。
  if (ui.backendOnline !== true) return;
  if (ui.gameRunning === false) return;
  startTelemetry();
}

function startRetry() {
  stopRetry();
  retryTimer = setInterval(tickSupervisor, SUPERVISE_MS);
}

function beginMonitoring() {
  if (monitoring.value) return;
  monitoring.value = true;
  ui.manualStart = false;
  // 先清掉上一次会话的残值（胎温、地图走线等）
  shell.value?.reset?.();
  // 自动开始通常早于 sidecar 冷启动完成：交给监督循环在探测到在线后再接入，
  // 避免开局就留下连接失败的错误状态；游戏没开时也不发 start。
  if (ui.backendOnline === true && ui.gameRunning !== false) startTelemetry();
  startRetry();
}

onMounted(beginMonitoring);
onBeforeUnmount(stopRetry);

// 后端刚探到在线的一瞬间就接入，不必等监督循环下一个 3 秒节拍
watch(() => ui.backendOnline, (online) => {
  if (online === true) tickSupervisor();
});

// HistoryView 的主面板依赖这几个总线字段（当前是否监测 / 正在记录哪一场）
watch(
  [() => telemetry.status, session, monitoring],
  () => {
    ui.monOn = monitoring.value;
    if (telemetry.status === 'streaming' && session.value) {
      ui.liveTrackId = session.value.track.id;
      ui.liveSessionId = session.value.sessionId ?? null;
    } else {
      ui.liveTrackId = null;
      ui.liveSessionId = null;
    }
  },
  { immediate: true }
);
</script>

<template>
  <section class="view" id="view-monitor">
    <div class="live-wrap">
      <MonitorShell ref="shell" />
    </div>
  </section>
</template>
