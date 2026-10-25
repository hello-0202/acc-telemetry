<script setup>
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue';
import { getCurrentWindow } from '@tauri-apps/api/window';
import MonitorView from './components/MonitorView.vue';
import HistoryView from './components/HistoryView.vue';
import SettingsView from './components/SettingsView.vue';
import { themeName, toggleTheme } from './telemetry/theme.js';
import { ui } from './telemetry/store.js';
import { getConfig, getHealth } from './telemetry/api.js';
import { applyPrefs, prefs } from './telemetry/prefs.js';
import { telemetry } from './telemetry/useTelemetry.js';

const VIEWS = ['monitor', 'history', 'settings'];
const view = ref('monitor');
let unlistenClose = null;

/* ------------------- 全局连接状态 ------------------- */
// 周期探测后端 /health，驱动全局置灰与提示；监控页自身仍用 WS 状态。
const HEALTH_INTERVAL_MS = 3000;
let healthTimer = null;
let healthBusy = false;
const noticeDismissed = ref(false);

async function pollHealth() {
  if (healthBusy) return;
  healthBusy = true;
  try {
    const health = await getHealth();
    ui.backendOnline = true;
    ui.gameRunning = !!health.gameRunning;
  } catch (e) {
    ui.backendOnline = false;
    ui.gameRunning = false;
  } finally {
    healthBusy = false;
  }
}

const offline = computed(() =>
  ui.backendOnline === false ||
  ui.gameRunning === false ||
  telemetry.status === 'error'
);

const noticeText = computed(() => {
  if (ui.backendOnline === false) return '本地服务未连接，请确认后端服务已启动（端口 8008）';
  if (telemetry.status === 'error' && telemetry.error) return telemetry.error;
  if (ui.gameRunning === false) return '未检测到 ACC 游戏，请先启动游戏并进入比赛';
  return '';
});

const showNotice = computed(
  () => offline.value && !noticeDismissed.value && !!noticeText.value
);

// 状态恢复后重置「已关闭」，下次断开时重新提醒
watch(offline, (value) => {
  if (!value) noticeDismissed.value = false;
});

// 浏览器里直接预览时没有 Tauri 运行时，窗口操作直接忽略
const appWindow = (typeof window !== 'undefined' && window.__TAURI_INTERNALS__)
  ? getCurrentWindow()
  : null;

async function windowAction(action) {
  if (!appWindow) return;
  try {
    await appWindow[action]();
  } catch (e) {
    console.error('window action failed:', action, e);
  }
}

function switchView(name) {
  if (VIEWS.indexOf(name) < 0) name = 'monitor';
  view.value = name;
  try { history.replaceState(null, '', '#' + name); } catch (e) { /* ignore */ }
}

function onKey(e) {
  const t = e.target;
  if (t && /INPUT|TEXTAREA/.test(t.tagName)) return;
  const k = e.key.toLowerCase();
  if (k >= '1' && k <= '3') switchView(VIEWS[+k - 1]);
  else if (k === 't') toggleTheme();
}

onMounted(() => {
  const h = (location.hash || '').replace('#', '');
  if (VIEWS.indexOf(h) >= 0) view.value = h;
  window.addEventListener('keydown', onKey);

  // 读取后端配置，决定关闭窗口时是否隐藏到托盘
  getConfig().then(applyPrefs).catch(() => { /* 后端未启动时保持默认值 */ });

  // 常驻探测服务与游戏状态
  pollHealth();
  healthTimer = setInterval(pollHealth, HEALTH_INTERVAL_MS);
  if (appWindow) {
    appWindow
      .onCloseRequested(async (event) => {
        if (!prefs.backgroundResident) return;
        event.preventDefault();
        try {
          await appWindow.hide();
        } catch (e) {
          console.error('hide window failed:', e);
        }
      })
      .then((fn) => { unlistenClose = fn; })
      .catch(() => { /* 旧版 Tauri 或权限缺失时忽略 */ });
  }
});
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey);
  if (unlistenClose) unlistenClose();
  if (healthTimer !== null) {
    clearInterval(healthTimer);
    healthTimer = null;
  }
});
</script>

<template>
  <div class="desktop">
    <div class="win" id="win">
      <!-- 自定义标题栏：data-tauri-drag-region 让它可以拖动窗口 -->
      <div class="win-bar" data-tauri-drag-region>
        <div class="win-id" data-tauri-drag-region>
          <img class="brand-logo" src="/logo.png" alt="" data-tauri-drag-region /><span class="name" data-tauri-drag-region>ACC · Telemetry</span>
        </div>
        <div class="wbtns">
          <span class="wb" role="button" title="最小化" @click="windowAction('minimize')"><svg viewBox="0 0 10 10"><rect x="0" y="4.4" width="10" height="1.2" fill="currentColor" /></svg></span>
          <span class="wb" role="button" title="最大化 / 还原" @click="windowAction('toggleMaximize')"><svg viewBox="0 0 10 10"><rect x="0.6" y="0.6" width="8.8" height="8.8" fill="none" stroke="currentColor" stroke-width="1.2" /></svg></span>
          <span class="wb close" role="button" title="关闭" @click="windowAction('close')"><svg viewBox="0 0 10 10"><path d="M0.6 0.6 9.4 9.4M9.4 0.6 0.6 9.4" stroke="currentColor" stroke-width="1.3" /></svg></span>
        </div>
      </div>

      <div class="app">
        <!-- topbar -->
        <header class="topbar">
          <nav class="tabs" aria-label="页面切换">
            <button class="tab" :class="{ active: view === 'monitor' }" @click="switchView('monitor')">
              <span class="tab-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 4.5 13.5H11L9.5 22 19 10h-6.5L13 2z" /></svg></span>
              <span class="tab-label">监测</span>
            </button>
            <button class="tab" :class="{ active: view === 'history' }" @click="switchView('history')">
              <span class="tab-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M5 21V4" /><path d="M5 5h14l-2.5 4L19 13H5" /></svg></span>
              <span class="tab-label">数据</span>
            </button>
            <button class="tab" :class="{ active: view === 'settings' }" @click="switchView('settings')">
              <span class="tab-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.11-1.56 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.65 8.9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h.08A1.7 1.7 0 0 0 10.12 3V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.08a1.7 1.7 0 0 0 1.56 1.03H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1.03z" /></svg></span>
              <span class="tab-label">设置</span>
            </button>
          </nav>
          <div class="top-right">
            <button class="sq-btn" :class="{ on: themeName === 'light' }" @click="toggleTheme()" aria-label="切换黑白主题" title="切换主题 (T)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z" /></svg>
            </button>
          </div>
        </header>

        <!-- stage -->
        <main class="stage">
          <MonitorView :class="{ active: view === 'monitor' }" />
          <HistoryView :class="{ active: view === 'history' }" :visible="view === 'history'" />
          <SettingsView :class="{ active: view === 'settings' }" :visible="view === 'settings'" />
        </main>

        <!-- statusbar -->
        <footer class="statusbar">
          <span class="r">
            <span><kbd>1</kbd><kbd>2</kbd><kbd>3</kbd> 切换页面</span>
            <span><kbd>T</kbd> 主题</span>
          </span>
        </footer>
      </div>

      <!-- 断开 / 游戏未启动提示 -->
      <div class="offline-toast" v-if="showNotice" role="alert">
        <span class="od"></span>
        <span class="ot">{{ noticeText }}</span>
        <button class="ox" type="button" aria-label="关闭提示" @click="noticeDismissed = true">×</button>
      </div>
    </div>
  </div>
</template>
