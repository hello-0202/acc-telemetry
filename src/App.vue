<script setup>
import { computed, ref, shallowRef, watch, onMounted, onBeforeUnmount } from 'vue';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { listen } from '@tauri-apps/api/event';
import { check } from '@tauri-apps/plugin-updater';
import { exit } from '@tauri-apps/plugin-process';
import MonitorView from './components/MonitorView.vue';
import HistoryView from './components/HistoryView.vue';
import SettingsView from './components/SettingsView.vue';
import UpdateDialog from './components/UpdateDialog.vue';
import { themeName, toggleTheme } from './telemetry/theme.js';
import { ui } from './telemetry/store.js';
import { immersive, exitImmersive, toggleImmersive } from './telemetry/immersive.js';
import { initOverlayWatcher, overlayVisible, toggleOverlay, setOverlayLocked } from './telemetry/overlay-window.js';
import { pushOverlaySlots } from './telemetry/overlay-slots.js';
import { getHealth } from './telemetry/api.js';
import { loadPrefs, prefs } from './telemetry/prefs.js';
import { telemetry, clearConnectError } from './telemetry/useTelemetry.js';

const VIEWS = ['monitor', 'history', 'settings'];
const view = ref('monitor');
let unlistenClose = null;

/* ------------------- 全局连接状态 ------------------- */
// 周期探测后端 /health，驱动全局置灰与提示；监控页自身仍用 WS 状态。
const HEALTH_INTERVAL_MS = 3000;
// 启动宽限期：sidecar 冷启动实测要 5.8~6.2 秒（PyInstaller 解压 + 服务起来），
// 而前端一挂载就探测，第一次必然失败。这段时间内的「离线」不算数，
// 否则每次打开软件都会先弹一条「本地服务未连接」的假警告。
const BOOT_GRACE_MS = 10000;
const bootGrace = ref(true);
let bootTimer = null;
let healthTimer = null;
let healthBusy = false;
let healthPending = false;
const noticeDismissed = ref(false);

async function pollHealth() {
  if (healthBusy) {
    // 正在探测时记一笔，等本轮结束后立刻补一次，避免宽限结束时拿不到新结果
    healthPending = true;
    return;
  }
  healthBusy = true;
  try {
    const health = await getHealth();
    ui.backendOnline = true;
    ui.gameRunning = !!health.gameRunning;
    // 后端已可达：之前因为「启动时序竞争」留下的连接错误立刻作废，
    // 否则界面会一直挂着「无法连接本地后端」，直到用户手动点一次开始。
    clearConnectError();
    // 不必死等满宽限期：探到后端就能正常判定了
    if (bootGrace.value) endBootGrace();
  } catch (e) {
    ui.backendOnline = false;
    ui.gameRunning = false;
  } finally {
    healthBusy = false;
    if (healthPending) {
      healthPending = false;
      pollHealth();
    }
  }
}

function endBootGrace(reprobe = false) {
  if (!bootGrace.value) return;
  bootGrace.value = false;
  ui.booting = false;
  if (bootTimer !== null) {
    clearTimeout(bootTimer);
    bootTimer = null;
  }
  if (reprobe) {
    // 宽限期到点仍离线：把宽限期内那次「离线」结果作废重探一次，
    // 让弹窗判定基于启动完成之后的真实状态；探测中则等本轮结束自动补一次。
    ui.backendOnline = null;
    pollHealth();
  }
}

const offline = computed(() =>
  ui.backendOnline === false ||
  ui.gameRunning === false ||
  telemetry.status === 'error' ||
  // 自动监测已开启但还没进赛道：也提示一下，否则用户会以为没在记录
  telemetry.status === 'waiting'
);

const noticeText = computed(() => {
  if (ui.backendOnline === false) return '本地服务未连接，请确认后端服务已启动（端口 8008）';
  if (telemetry.status === 'error' && telemetry.error) return telemetry.error;
  // 自动监测在后台待命时，游戏没开是预期状态（指示灯已显示「等待游戏」），
  // 不必每次打开软件都弹一条；只有用户手动点了开始才明确提示。
  if (ui.gameRunning === false && ui.manualStart) {
    return '未检测到 ACC 游戏，请先启动游戏并进入比赛';
  }
  // 已开自动监测、游戏也在跑，但还没进赛道（拿不到赛道/车辆信息）：静默等待即可
  if (telemetry.status === 'waiting') return '等待进入赛道，进入后会自动开始记录';
  return '';
});

const showNotice = computed(
  () => !bootGrace.value && offline.value && !noticeDismissed.value && !!noticeText.value
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

/* ------------------- 软件更新检查 ------------------- */
// 目前只检查新版本，不接入下载/安装（downloadAndInstall）。
// updateInfo 存的是 plugin-updater 返回的 Update 对象，有值即代表有新版本。
// 用 shallowRef：Update 内部用私有字段存 Rust 资源句柄，被 Vue 深度代理后方法会失效。
const updateInfo = shallowRef(null);
const isCheckingUpdate = ref(false);
const updateErrorMsg = ref('');
const updateChecked = ref(false);

// 「启动时自动检查」偏好只影响前端行为，存 localStorage 即可，不必走后端配置。
const AUTO_CHECK_KEY = 'acc-auto-check-update';
const autoCheckUpdate = ref(true);
try {
  autoCheckUpdate.value = localStorage.getItem(AUTO_CHECK_KEY) !== '0';
} catch (e) { /* ignore */ }

// 手动检查时打开居中结果弹窗；启动时的静默检查不弹
const updateDialogOpen = ref(false);

// 不安装时，旧的 Update 句柄要 close 掉，否则 Rust 侧资源一直挂着
function closeUpdate() {
  const old = updateInfo.value;
  updateInfo.value = null;
  if (old && typeof old.close === 'function') {
    old.close().catch(() => { /* 已经关闭或权限缺失，忽略 */ });
  }
}

async function handleCheckUpdate(manual = false) {
  if (isCheckingUpdate.value) return;
  // 浏览器（vite dev）里没有 Tauri 运行时，插件调用必然失败，直接拦掉
  if (!appWindow) {
    if (manual) {
      updateErrorMsg.value = '当前在浏览器环境，无法检查更新。';
      updateDialogOpen.value = true;
    }
    return;
  }

  if (manual) {
    updateErrorMsg.value = '';
    updateDialogOpen.value = true;
  }
  isCheckingUpdate.value = true;
  updateErrorMsg.value = '';
  try {
    const update = await check({ timeout: 5000 });
    updateChecked.value = true;
    closeUpdate();
    if (update) {
      updateInfo.value = update;
      // 启动时的静默检查也走右下角弹窗，取代原来状态栏里的一行提示
      updateDialogOpen.value = true;
    }
  } catch (e) {
    // GitHub 在国内访问不稳，静默检查失败只记日志，不打扰用户
    console.error('update check failed:', e);
    updateErrorMsg.value = '无法连接更新服务器，请检查网络后重试。';
  } finally {
    isCheckingUpdate.value = false;
  }
}

function toggleAutoCheckUpdate() {
  autoCheckUpdate.value = !autoCheckUpdate.value;
  try {
    localStorage.setItem(AUTO_CHECK_KEY, autoCheckUpdate.value ? '1' : '0');
  } catch (e) { /* ignore */ }
}

function switchView(name) {
  if (VIEWS.indexOf(name) < 0) name = 'monitor';
  view.value = name;
  try { history.replaceState(null, '', '#' + name); } catch (e) { /* ignore */ }
}

function onKey(e) {
  const t = e.target;
  if (t && /INPUT|TEXTAREA/.test(t.tagName)) return;
  // 沉浸模式里只留 Esc 一个出口，避免误触切走页面后连导航都看不见
  if (immersive.value) {
    if (e.key === 'Escape') exitImmersive();
    return;
  }
  const k = e.key.toLowerCase();
  if (k >= '1' && k <= '3') switchView(VIEWS[+k - 1]);
  else if (k === 't') toggleTheme();
}

// 离开监测页就退出沉浸，否则外壳（含导航）被藏起来了没法切回去
watch(view, (v) => {
  if (v !== 'monitor' && immersive.value) exitImmersive();
});

onMounted(() => {
  const h = (location.hash || '').replace('#', '');
  if (VIEWS.indexOf(h) >= 0) view.value = h;
  window.addEventListener('keydown', onKey);

  // 悬浮窗按钮的状态跟着 Rust 侧的显隐走（快捷键也能同步）
  initOverlayWatcher();
  // 把「启用了哪几个悬浮窗」推给 Rust（它据此建窗口、响应快捷键）；浏览器里会静默失败
  pushOverlaySlots();

  // 全局快捷键被别的软件占用时 Rust 只能写 stderr，这里再留一条带上下文的记录：
  // 按 Ctrl+Shift+O / L 没反应时，先看这条（也可以点顶栏按钮兜底）。
  listen('overlay-shortcut-error', (e) => {
    console.warn('[overlay] 全局快捷键不可用，请改用面板按钮：', e.payload);
  }).catch(() => {});

  // 读取后端配置（自动开始 / 后台常驻等），MonitorView 复用同一个 Promise 做自动开始
  loadPrefs();

  // 启动时静默检查一次更新（可在设置里关掉）；失败不打扰用户
  if (autoCheckUpdate.value) handleCheckUpdate(false);

  // 启动宽限期：这段时间内的离线状态不弹提示（sidecar 还没起来）。
  // 探到后端就绪会提前结束；到点仍离线才作废旧结果重探一次。
  ui.booting = true;
  bootTimer = setTimeout(() => endBootGrace(true), BOOT_GRACE_MS);

  // 常驻探测服务与游戏状态
  pollHealth();
  healthTimer = setInterval(pollHealth, HEALTH_INTERVAL_MS);
  if (appWindow) {
    appWindow
      .onCloseRequested(async (event) => {
        if (prefs.backgroundResident) {
          event.preventDefault();
          try {
            await appWindow.hide();
          } catch (e) {
            console.error('hide window failed:', e);
          }
          return;
        }
        // 不常驻：关主窗口 = 退出整个应用。
        // 不能只让主窗口自己销毁 —— 悬浮窗是独立窗口，它还开着的话进程就留在后台，
        // 托盘图标跟着残留，而「显示主窗口」再也找不回已销毁的 main，点了等于没反应。
        event.preventDefault();
        try {
          await exit(0);
        } catch (e) {
          console.error('quit app failed:', e);
          // 兜底：至少把窗口关掉，别让用户卡在「点了没反应」
          appWindow.destroy().catch(() => {});
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
  if (bootTimer !== null) {
    clearTimeout(bootTimer);
    bootTimer = null;
  }
  closeUpdate();
});
</script>

<template>
  <div class="desktop">
    <div class="win" id="win" :class="{ immersive }">
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
            <!-- 全屏 / 悬浮窗只对监测页有意义，其它页面不显示 -->
            <button
              v-if="view === 'monitor'"
              class="sq-btn"
              :class="{ on: immersive }"
              @click="toggleImmersive"
              aria-label="全屏沉浸"
              title="全屏沉浸（Esc 退出）"
            >
              <svg v-if="!immersive" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4H4v5" /><path d="M15 4h5v5" /><path d="M15 20h5v-5" /><path d="M9 20H4v-5" /></svg>
              <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9h5V4" /><path d="M20 9h-5V4" /><path d="M20 15h-5v5" /><path d="M4 15h5v5" /></svg>
            </button>
            <button
              v-if="view === 'monitor'"
              class="sq-btn"
              :class="{ on: overlayVisible }"
              @click="toggleOverlay"
              aria-label="悬浮窗"
              title="悬浮窗（Ctrl+Shift+O）"
            >
              <!-- 悬浮窗图标：屏幕外框 + 右下角实心浮窗（右下角略压出边框，表达「浮在上面」） -->
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="3" y="3.8" width="16.6" height="15.4" rx="2" /><rect x="14" y="13.4" width="7.2" height="7.2" rx="1.6" fill="currentColor" stroke="none" /></svg>
            </button>
            <button
              v-if="view === 'monitor' && overlayVisible"
              class="sq-btn"
              @click="setOverlayLocked(false)"
              aria-label="调整悬浮窗"
              title="调整悬浮窗位置与大小（Ctrl+Shift+L）"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6" /><path d="M9 21H3v-6" /><path d="M21 3l-7 7" /><path d="M3 21l7-7" /></svg>
            </button>
            <button class="sq-btn" :class="{ on: themeName === 'light' }" @click="toggleTheme()" aria-label="切换黑白主题" title="切换主题 (T)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z" /></svg>
            </button>
          </div>
        </header>

        <!-- stage -->
        <main class="stage">
          <MonitorView :class="{ active: view === 'monitor' }" />
          <HistoryView :class="{ active: view === 'history' }" :visible="view === 'history'" />
          <SettingsView
            :class="{ active: view === 'settings' }"
            :visible="view === 'settings'"
            :update-info="updateInfo"
            :update-checking="isCheckingUpdate"
            :update-checked="updateChecked"
            :update-error="updateErrorMsg"
            :auto-check="autoCheckUpdate"
            @check-update="handleCheckUpdate(true)"
            @toggle-auto-check="toggleAutoCheckUpdate"
            @start-update="updateDialogOpen = true"
          />
        </main>

        <!-- statusbar -->
        <footer class="statusbar">
          <span class="r">
            <span><kbd>1</kbd><kbd>2</kbd><kbd>3</kbd> 切换页面</span>
            <span><kbd>T</kbd> 主题</span>
          </span>
        </footer>
      </div>

      <!-- 全屏沉浸时外壳被收起：右上角留一组浮动控件（悬浮窗 / 退出，Esc 同效） -->
      <div v-if="immersive" class="imm-tools">
        <button
          class="imm-tool"
          :class="{ on: overlayVisible }"
          type="button"
          title="悬浮窗（Ctrl+Shift+O）"
          @click="toggleOverlay"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="3" y="3.8" width="16.6" height="15.4" rx="2" /><rect x="14" y="13.4" width="7.2" height="7.2" rx="1.6" fill="currentColor" stroke="none" /></svg>
        </button>
        <button class="imm-tool" type="button" title="退出全屏（Esc）" @click="exitImmersive">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9h5V4" /><path d="M20 9h-5V4" /><path d="M20 15h-5v5" /><path d="M4 15h5v5" /></svg>
          <span>ESC</span>
        </button>
      </div>

      <!-- 断开 / 游戏未启动提示 -->
      <div class="offline-toast" v-if="showNotice" role="alert">
        <span class="od"></span>
        <span class="ot">{{ noticeText }}</span>
        <button class="ox" type="button" aria-label="关闭提示" @click="noticeDismissed = true">×</button>
      </div>

      <!-- 手动检查更新的结果弹窗 -->
      <UpdateDialog
        v-if="updateDialogOpen"
        :checking="isCheckingUpdate"
        :update="updateInfo"
        :error="updateErrorMsg"
        @close="updateDialogOpen = false"
        @retry="handleCheckUpdate(true)"
      />
    </div>
  </div>
</template>
