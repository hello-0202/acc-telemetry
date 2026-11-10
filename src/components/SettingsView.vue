<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ACCENTS, accentName, setAccent, setTheme, themeName } from '../telemetry/theme.js';
import { openUrl } from '@tauri-apps/plugin-opener';
import { getVersion } from '@tauri-apps/api/app';
import { invoke } from '@tauri-apps/api/core';
import { open as openDirDialog } from '@tauri-apps/plugin-dialog';
import { clearHistory, getBaseUrl, getConfig, getHealth, listSessions, saveConfig } from '../telemetry/api.js';
import { telemetry } from '../telemetry/useTelemetry.js';
import { applyPrefs, prefs } from '../telemetry/prefs.js';
import { UPDATE_ROUTES, setUpdateRoute, updateRoute } from '../telemetry/update-route.js';
import HelpTip from './HelpTip.vue';
import ThemePicker from './ThemePicker.vue';

const props = defineProps({
  visible: { type: Boolean, default: true },
  updateInfo: { type: Object, default: null },
  updateChecking: { type: Boolean, default: false },
  updateChecked: { type: Boolean, default: false },
  updateError: { type: String, default: '' },
  autoCheck: { type: Boolean, default: true },
});

const emit = defineEmits(['check-update', 'toggle-auto-check', 'start-update']);

// 发版版本号每次都变，下载页不能用写死的 latest，按检测到的版本拼对应 tag
const REPO_URL = 'https://github.com/hello-0202/acc-telemetry';
const releasesUrl = computed(() => {
  const v = String(props.updateInfo?.version || '').replace(/^v/i, '');
  return v ? `${REPO_URL}/releases/tag/v${v}` : `${REPO_URL}/releases/latest`;
});

const SW = [100, 50, 20];
const PUSH = [60, 50, 30];
const SAMPLE_LABEL = { 100: '100 Hz 高精', 50: '50 Hz 标准', 20: '20 Hz 省电' };

const clearText = ref('清空历史数据');
const confirmOpen = ref(false);
const clearing = ref(false);
const cancelBtn = ref(null);
const health = ref(null);
const config = ref(null);
const sessionCount = ref(0);
const lapCount = ref(0);
const errorText = ref('');
const serverText = ref('127.0.0.1:8008');
const saving = ref(false);
const appVersion = ref('');
let healthTimer = null;

const totalLapsText = computed(() => sessionCount.value + ' 组记录 · ' + lapCount.value + ' 圈');
const dbPath = computed(() => health.value?.dbPath || config.value?.dbPath || '—');
const configPath = computed(() => config.value?.configPath || '—');
// 版本号取 Tauri 打包时写入的桌面应用版本（随发版自动变化）；
// sidecar 的版本要重新打包才会更新，只放在悬停提示里作为排查信息。
const versionText = computed(() => (appVersion.value ? 'v' + appVersion.value : '—'));
const backendVersion = computed(() => health.value?.version || config.value?.version || '');
const statusText = computed(() => {
  if (!health.value) return '未连接';
  if (!health.value.dbOk) return '数据库异常';
  return health.value.streaming ? '监测中' : '在线';
});
// release.yml 里 releaseBody 固定写成这句占位文案，没有实际信息，不展示
const updateNotes = computed(() => {
  const body = (props.updateInfo?.body || '').trim();
  return /^\s*see the changelog\.md for details\.?\s*$/i.test(body) ? '' : body;
});
const updateStateText = computed(() => {
  if (props.updateChecking) return '检查中…';
  if (props.updateInfo) return '发现新版本 v' + props.updateInfo.version;
  if (props.updateError) return '检查失败';
  if (props.updateChecked) return '已是最新版本';
  return '尚未检查';
});
// 后端清空接口在监测中会返回 409，弹窗里提前把按钮禁掉，不用等失败
const streamingNow = computed(() => !!health.value?.streaming);
const sampleText = computed(() => SAMPLE_LABEL[prefs.sampleHz] || prefs.sampleHz + ' Hz');
const sampleHint = computed(() => '精读档位：' + sampleText.value + ' · 调低采样率可降低游戏掉帧风险。');
const pushText = computed(() => prefs.pushHz + ' Hz');
const queueMax = computed(() => health.value?.queueMax ?? 2000);
const queueSize = computed(() => health.value?.queueSize ?? 0);
const queuePct = computed(() => Math.min(100, Math.round((queueSize.value / Math.max(1, queueMax.value)) * 100)));
const cpuText = computed(() => (health.value?.cpuPercent == null ? '—' : health.value.cpuPercent.toFixed(1) + ' %'));
const droppedText = computed(() => String(health.value?.droppedFrames ?? 0));

/* ── 存储位置：对外只有一个入口，内部仍是三个各自独立的目录 ──────────────────
   选一个根目录，按安装布局派生：<根> / <根>\data / <根>\data\themes。
   三行只读展示各自路径 + 一行中文说明里面放什么，只有一处「更改」，
   点一次把三者一起迁移。整块收进「高级」，默认折叠，避免误触破坏性操作。 */
const PATH_KEYS = ['configDir', 'dataDir', 'themesDir'];
const STORAGE_ROWS = [
  { key: 'configDir', label: '配置文件', hint: '界面设置与窗口位置' },
  { key: 'dataDir', label: '数据库', hint: '圈速历史记录' },
  { key: 'themesDir', label: '主题', hint: '导入或手工放进去的主题包' },
];

const pathsInfo = ref(null);
const pathBusy = ref(false);
const pathError = ref('');
const pathChoice = ref(null);
const pathChoiceBusy = ref(false);
const pathCancelBtn = ref(null);
const restartBusy = ref(false);
const advOpen = ref(false); // 「高级」折叠区，默认收起

const activeLayout = computed(() => pathsInfo.value?.effective || null);
const pendingLayout = computed(() => pathsInfo.value?.pending || null);
const activeRoot = computed(() => activeLayout.value?.configDir || '');
const pendingRoot = computed(() => pendingLayout.value?.configDir || '');
function sameLayout(a, b) {
  return !!a && !!b && PATH_KEYS.every((k) => a[k] === b[k]);
}
const hasPendingChange = computed(() => !!pendingLayout.value && !sameLayout(activeLayout.value, pendingLayout.value));
const isDefaultLayout = computed(() => sameLayout(activeLayout.value, pathsInfo.value?.defaultLayout));
const choiceChanges = computed(() => STORAGE_ROWS.filter(
  (i) => !!activeLayout.value && !!pathChoice.value && pathChoice.value.layout[i.key] !== activeLayout.value[i.key],
));
const applyText = computed(() => (pathChoice.value?.isReset ? '恢复默认并重启' : '移动数据并重启'));

/** 根目录 → 三个子目录。和 Rust `install_layout()` 保持同一种形状，别各写一套。 */
function joinPath(base, ...parts) {
  const root = String(base || '').replace(/[\\/]+$/, '');
  return [root, ...parts].join('\\');
}
function layoutFromRoot(root) {
  const base = String(root).replace(/[\\/]+$/, '');
  return {
    configDir: base,
    dataDir: joinPath(base, 'data'),
    themesDir: joinPath(base, 'data', 'themes'),
  };
}

async function loadPaths() {
  try {
    pathsInfo.value = await invoke('get_data_paths');
  } catch (e) {
    // 浏览器预览里没有 Tauri IPC：保留 null，退回后端返回的只读路径展示
    pathsInfo.value = null;
  }
}

async function openPath(kind) {
  try {
    await invoke('open_data_path', { kind });
    pathError.value = '';
  } catch (e) {
    pathError.value = '打开目录失败：' + (e?.message || e);
  }
}

async function changePath() {
  if (pathBusy.value) return;
  const base = pendingLayout.value || activeLayout.value;
  if (!base) return;
  const current = base.configDir;
  pathBusy.value = true;
  try {
    const picked = await openDirDialog({
      title: '选择存储位置',
      directory: true,
      multiple: false,
      defaultPath: current,
    });
    if (!picked || Array.isArray(picked)) return;
    const chosen = String(picked).replace(/[\\/]+$/, '');
    if (chosen === String(current).replace(/[\\/]+$/, '')) return;
    pathError.value = '';
    pathChoice.value = {
      title: '更改存储位置',
      layout: layoutFromRoot(chosen),
      isReset: false,
    };
    nextTick(() => pathCancelBtn.value?.focus());
  } catch (e) {
    pathError.value = '选择目录失败：' + (e?.message || e);
  } finally {
    pathBusy.value = false;
  }
}

function resetPaths() {
  if (pathBusy.value || !pathsInfo.value?.defaultLayout) return;
  pathError.value = '';
  pathChoice.value = {
    title: '恢复默认存储位置',
    layout: { ...pathsInfo.value.defaultLayout },
    isReset: true,
  };
  nextTick(() => pathCancelBtn.value?.focus());
}

function closePathChoice() {
  if (pathChoiceBusy.value) return;
  pathChoice.value = null;
}

function onPathDialogKey(e) {
  e.stopPropagation();
  if (e.key === 'Escape') {
    e.preventDefault();
    closePathChoice();
  }
}

async function applyPathChoice() {
  if (!pathChoice.value || pathChoiceBusy.value) return;
  pathChoiceBusy.value = true;
  try {
    const next = pathChoice.value.isReset
      ? await invoke('reset_data_paths')
      : await invoke('set_data_paths', { layout: pathChoice.value.layout });
    pathsInfo.value = next;
    pathError.value = '';
    pathChoice.value = null;
    // 文案就是「移动数据并重启」：保存完立刻重启，迁移在重启时、sidecar 起来之前完成。
    // 重启失败不会丢东西 —— pending 已落盘，界面上会留「立即重启」按钮兜底。
    await restartNow();
  } catch (e) {
    pathError.value = '保存失败：' + (e?.message || e);
    pathChoice.value = null;
  } finally {
    pathChoiceBusy.value = false;
  }
}

async function restartNow() {
  if (restartBusy.value) return;
  restartBusy.value = true;
  pathError.value = '';
  try {
    await invoke('restart_app');
    // restart_app 会立刻结束进程并拉起新实例，这里一般收不到后续响应
  } catch (e) {
    pathError.value = '重启失败，请手动关闭并重新打开软件：' + (e?.message || e);
    restartBusy.value = false;
  }
}

async function cancelPending() {
  if (!activeLayout.value || pathBusy.value) return;
  pathBusy.value = true;
  try {
    pathsInfo.value = await invoke('set_data_paths', {
      layout: { ...activeLayout.value },
    });
    pathError.value = '';
  } catch (e) {
    pathError.value = '撤销失败：' + (e?.message || e);
  } finally {
    pathBusy.value = false;
  }
}

async function refresh() {
  try {
    const [h, c, sessions] = await Promise.all([getHealth(), getConfig(), listSessions()]);
    health.value = h;
    config.value = c;
    applyPrefs(c);
    sessionCount.value = sessions.length;
    lapCount.value = sessions.reduce((sum, s) => sum + (s.laps || 0), 0);
    serverText.value = getBaseUrl().replace(/^https?:\/\//, '');
    errorText.value = '';
  } catch (e) {
    health.value = null;
    errorText.value = '无法连接后端：' + e.message;
  }
}

async function patch(values) {
  if (saving.value) return;
  saving.value = true;
  try {
    const c = await saveConfig(values);
    config.value = c;
    applyPrefs(c);
    errorText.value = '';
  } catch (e) {
    errorText.value = '保存失败：' + e.message;
    // 保存失败时从后端重新拉取，避免 UI 和实际参数不一致
    refresh();
  } finally {
    saving.value = false;
  }
}

function setSampleHz(hz) {
  if (prefs.sampleHz !== hz) patch({ sampleHz: hz });
}

function setPushHz(hz) {
  if (prefs.pushHz !== hz) patch({ pushHz: hz });
}

function toggleResident() {
  patch({ backgroundResident: !prefs.backgroundResident });
}

/* 自动更新走弹窗里的下载/安装流程，这里只保留一个手动下载的兜底入口 */
async function openReleases() {
  try {
    await openUrl(releasesUrl.value);
  } catch (e) {
    console.error('open releases page failed:', e);
  }
}

async function openProjectSite() {
  try {
    await openUrl(REPO_URL);
  } catch (e) {
    console.error('open project site failed:', e);
  }
}

/* 清空历史：先开自绘确认弹窗，确认后才真正请求删除 */
function openConfirm() {
  if (clearing.value) return;
  confirmOpen.value = true;
  // 破坏性操作默认聚焦「取消」，回车不会误删
  nextTick(() => cancelBtn.value?.focus());
}

function closeConfirm() {
  if (clearing.value) return;
  confirmOpen.value = false;
}

async function confirmClear() {
  if (clearing.value) return;
  clearing.value = true;
  try {
    await clearHistory();
    confirmOpen.value = false;
    clearText.value = '已清空';
    await refresh();
  } catch (e) {
    confirmOpen.value = false;
    clearText.value = e.status === 409 ? '监测中无法清空' : '清空失败';
    errorText.value = e.status === 409 ? '请先停止监测' : ('清空失败：' + e.message);
  } finally {
    clearing.value = false;
    setTimeout(() => { clearText.value = '清空历史数据'; }, 1600);
  }
}

/* 弹窗内的键盘操作；阻止冒泡，避免 1/2/3、T 在弹窗打开时切页换主题 */
function onDialogKey(e) {
  e.stopPropagation();
  if (e.key === 'Escape') {
    e.preventDefault();
    closeConfirm();
    return;
  }
  if (e.key === 'Enter') {
    // 焦点在按钮上时交给按钮原生行为，否则「取消」按钮会被回车变成确认
    if (e.target && e.target.closest && e.target.closest('button')) return;
    e.preventDefault();
    confirmClear();
  }
}

function stopPolling() {
  if (healthTimer !== null) {
    clearInterval(healthTimer);
    healthTimer = null;
  }
}

function startPolling() {
  stopPolling();
  if (!props.visible) return;
  healthTimer = setInterval(() => {
    getHealth()
      .then((h) => { health.value = h; })
      .catch(() => { /* 保持上一次读数，等待下次轮询 */ });
  }, 1000);
}

onMounted(() => {
  refresh();
  loadPaths();
  startPolling();
  // 浏览器预览里没有 Tauri 运行时，取不到就用占位符
  getVersion().then((v) => { appVersion.value = v; }).catch(() => { /* ignore */ });
});

onBeforeUnmount(() => {
  stopPolling();
});

watch(() => props.visible, (visible) => {
  if (visible) {
    refresh();
    loadPaths();
    startPolling();
  } else {
    stopPolling();
    // 切走页面时把两个自绘弹窗收掉（Teleport 到 body，不会随页面一起隐藏）
    if (!clearing.value) confirmOpen.value = false;
    if (!pathChoiceBusy.value) pathChoice.value = null;
  }
});
watch(() => telemetry.status, refresh);
watch(() => telemetry.historyRevision, refresh);
</script>

<template>
  <section class="view" id="view-settings">
    <div class="set-group">
      <span class="glbl">外观与主题</span>
      <div class="set-row"><span>主题</span>
        <div class="seg">
          <button :class="{ active: themeName === 'dark' }" @click="setTheme('dark')">黑</button>
          <button :class="{ active: themeName === 'light' }" @click="setTheme('light')">白</button>
        </div>
      </div>
      <div class="set-row"><span>主题色</span>
        <div class="accent-row">
          <button
            v-for="a in ACCENTS"
            :key="a.id"
            class="accent-dot"
            :class="{ active: accentName === a.id }"
            :style="{ background: a.color }"
            :title="a.name"
            :aria-label="a.name"
            @click="setAccent(a.id)"
          ></button>
        </div>
      </div>
      <!-- 两个主题入口都只留「标题 + 更改」，都不显示当前选中的主题名；点开弹窗再选 -->
      <ThemePicker mode="monitor" :active="visible" />
      <!-- 悬浮窗：一个入口。弹窗是「双列二级侧栏（分类 / 主题）+ 右侧固定预览与设置」，
           固定宽高、内容在内部滚动，选中主题后弹窗尺寸不变；每个主题都能「使用 / 停用」，
           可同时开多个悬浮窗（槽位上限 3），但界面不写「悬浮窗 1/2/3」这类编号。
           底层仍按槽位存，Rust 不用改。 -->
      <ThemePicker mode="overlay" :slot="1" :active="visible" title="悬浮窗主题" />
      <div class="set-row"><span>界面语言</span><span class="v">简体中文</span></div>
    </div>

    <div class="set-group">
      <span class="glbl">采集与性能</span>
      <div class="set-row"><span class="set-label">采样率
          <HelpTip label="采样率说明" :text="sampleHint" />
        </span>
        <div class="seg seg-hz">
          <button
            v-for="hz in SW"
            :key="hz"
            :class="{ active: prefs.sampleHz === hz }"
            :disabled="saving"
            @click="setSampleHz(hz)"
          >{{ hz }} Hz</button>
        </div>
      </div>

      <div class="set-row"><span class="set-label">推流频率
          <HelpTip label="推流频率说明" text="只影响主面板刷新快慢，不改变落库的数据精度。" />
        </span>
        <div class="seg seg-hz">
          <button
            v-for="hz in PUSH"
            :key="hz"
            :class="{ active: prefs.pushHz === hz }"
            :disabled="saving"
            @click="setPushHz(hz)"
          >{{ hz }} Hz</button>
        </div>
      </div>

      <div class="set-row"><span class="set-label">后台常驻
          <HelpTip label="后台常驻说明" text="开启后关闭主窗口会隐藏到系统托盘，采集与记录继续运行。" />
        </span>
        <button class="switch" :class="{ on: prefs.backgroundResident }" :disabled="saving" @click="toggleResident" aria-label="后台常驻"></button>
      </div>

      <div class="set-row"><span>采集队列压力</span>
        <span class="v metric">
          <span class="metric-track"><i :style="{ width: queuePct + '%' }" :class="{ hot: queuePct > 70 }"></i></span>
          {{ queueSize }} / {{ queueMax }}
        </span>
      </div>
      <div class="set-row"><span>CPU 占用估算</span><span class="v">{{ cpuText }}</span></div>
      <div class="set-row"><span>累计丢帧</span><span class="v">{{ droppedText }}</span></div>
    </div>

    <div class="set-group">
      <span class="glbl">监测</span>
      <div class="set-row"><span>后端状态</span><span class="v">{{ statusText }}</span></div>
      <div class="set-row"><span>当前推流</span><span class="v">{{ pushText }}</span></div>
      <div class="set-row"><span>服务地址</span><span class="v">{{ serverText }}</span></div>
    </div>

    <div class="set-group">
      <span class="glbl">数据与存储</span>
      <div class="set-row"><span>已记录</span><span class="v">{{ totalLapsText }}</span></div>
      <div v-if="errorText" class="set-row"><span></span><span class="v" style="color:var(--hot)">{{ errorText }}</span></div>
      <div style="margin-top:0.77rem"><button class="btn btn-danger" @click="openConfirm">{{ clearText }}</button></div>
    </div>

    <!-- 高级：存储位置整块默认收起，普通用户不会误触「移动数据」 -->
    <div class="set-group">
      <button
        type="button"
        class="adv-head"
        :aria-expanded="advOpen"
        aria-controls="adv-storage"
        @click="advOpen = !advOpen"
      >
        <span class="glbl adv-glbl">高级</span>
        <svg class="adv-chev" :class="{ open: advOpen }" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M4.5 6.5 8 10l3.5-3.5" />
        </svg>
      </button>

      <div v-show="advOpen" id="adv-storage">
        <div class="set-row">
          <span class="set-label">存储位置
            <HelpTip
              label="存储位置说明"
              text="配置文件、数据库、主题各放一个独立目录，但只有这一处入口：改一个根目录，三个目录一起迁移。改完立即重启，重启时先把数据搬到新位置再启动后端，旧文件随之删除；中途失败会整体回滚。安装目录不可写时会自动回退到用户数据目录。"
            />
          </span>
          <div class="path-actions">
            <button class="btn btn-ghost btn-mini" :disabled="pathBusy || !pathsInfo" @click="openPath('root')">打开</button>
            <button class="btn btn-ghost btn-mini" :disabled="pathBusy || !pathsInfo" @click="changePath">更改</button>
          </div>
        </div>

        <!-- 浏览器预览（没有 Tauri IPC）时退回只读展示 -->
        <template v-if="!pathsInfo">
          <div class="set-row"><span>数据目录</span><span class="v" :title="dbPath">{{ dbPath }}</span></div>
          <div class="set-row"><span>配置文件</span><span class="v" :title="configPath">{{ configPath }}</span></div>
        </template>

        <template v-else>
          <div class="path-value" :title="activeRoot">{{ activeRoot }}</div>
          <div v-for="row in STORAGE_ROWS" :key="row.key" class="path-sub">
            <span class="path-sub-label">{{ row.label }}</span>
            <span class="path-sub-value" :title="activeLayout[row.key]">{{ activeLayout[row.key] }}</span>
            <span class="path-sub-hint">{{ row.hint }}</span>
          </div>

          <div v-if="pathsInfo.note" class="path-note">{{ pathsInfo.note }}</div>
          <div v-if="hasPendingChange" class="path-note warn">
            <b>已保存新位置，重启软件后生效</b>（当前运行仍使用上面的目录）。
            <div class="path-pending">{{ pendingRoot }}</div>
            <div class="path-note-actions">
              <button class="btn btn-primary btn-mini" :disabled="restartBusy" @click="restartNow">
                {{ restartBusy ? '正在重启…' : '立即重启' }}
              </button>
              <button class="btn btn-ghost btn-mini" :disabled="pathBusy || restartBusy" @click="cancelPending">撤销待生效的更改</button>
            </div>
          </div>
          <div v-if="pathError" class="path-note bad">{{ pathError }}</div>
          <div class="set-row">
            <span>默认位置</span>
            <button class="btn btn-ghost btn-mini" :disabled="isDefaultLayout || pathBusy" @click="resetPaths">恢复默认</button>
          </div>
        </template>
      </div>
    </div>

    <div class="set-group">
      <span class="glbl">更新</span>
      <div class="set-row"><span class="set-label">软件更新
          <HelpTip label="更新说明" text="查找到新版本后可一键下载并安装；自动更新失败时可改用手动下载。" />
        </span>
        <div class="upd-actions">
          <span class="v upd-state" :class="{ good: !!updateInfo, bad: !updateInfo && !!updateError }" :title="updateError || ''">{{ updateStateText }}</span>
          <button class="btn btn-ghost" :disabled="updateChecking" @click="emit('check-update')">{{ updateChecking ? '检查中…' : '检查更新' }}</button>
        </div>
      </div>
      <div class="set-row"><span class="set-label">启动时自动检查
          <HelpTip label="自动检查说明" text="每次打开软件时静默检查一次；发现新版本会在右下角弹窗提示。" />
        </span>
        <button class="switch" :class="{ on: autoCheck }" @click="emit('toggle-auto-check')" aria-label="启动时自动检查更新"></button>
      </div>
      <div class="set-row"><span class="set-label">更新线路
          <HelpTip label="更新线路说明" text="自动：先走 GitHub，失败后再尝试 Gitee；也可以固定只走其中一个。" />
        </span>
        <div class="seg">
          <button
            v-for="r in UPDATE_ROUTES"
            :key="r.id"
            :class="{ active: updateRoute === r.id }"
            @click="setUpdateRoute(r.id)"
          >{{ r.label }}</button>
        </div>
      </div>
      <div v-if="updateInfo" class="set-row upd-note">
        <div class="upd-note-main">
          <div class="upd-ver">v{{ updateInfo.currentVersion }} → v{{ updateInfo.version }}</div>
          <div v-if="updateNotes" class="upd-body">{{ updateNotes }}</div>
        </div>
        <div class="upd-actions">
          <button class="btn btn-ghost" @click="openReleases">手动下载</button>
          <button class="btn btn-primary" @click="emit('start-update')">立即更新</button>
        </div>
      </div>
    </div>

    <div class="set-group">
      <span class="glbl">关于</span>
      <div class="set-row"><span>版本</span><span class="v" :title="backendVersion ? '本地后端 v' + backendVersion : ''">{{ versionText }}</span></div>
      <div class="set-row"><span>项目主页</span>
        <button class="btn btn-ghost" @click="openProjectSite">GitHub</button>
      </div>
    </div>

    <!-- 清空历史数据的确认弹窗：Teleport 到 body，避免被 .view 的 transform 影响定位 -->
    <Teleport to="body">
      <div v-if="confirmOpen" class="dlg-mask" @click.self="closeConfirm">
        <div
          class="dlg"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="dlg-title"
          aria-describedby="dlg-desc"
          @keydown="onDialogKey"
        >
          <div class="dlg-head">
            <span class="dlg-ico danger" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 4.2 3.2 19.8h17.6L12 4.2z" />
                <path d="M12 10.2v3.9" />
                <circle cx="12" cy="17.2" r="1.05" fill="currentColor" stroke="none" />
              </svg>
            </span>
            <div class="dlg-ttl">
              <h3 id="dlg-title">清空历史数据</h3>
            </div>
          </div>

          <div class="dlg-body" id="dlg-desc">
            <p>将从本机数据库中永久删除 <strong>{{ totalLapsText }}</strong>的全部数据。</p>

            <ul class="dlg-list">
              <li>所有圈速、分段计时与完成圈记录</li>
              <li>逐帧遥测采样（速度 / 踏板 / 走线）</li>
              <li>PB 记录与历史圈对比基准</li>
            </ul>

            <div class="dlg-keep"><b>保留</b>赛道地图缓存、设置项与界面偏好不受影响。</div>

            <div v-if="streamingNow" class="dlg-warn">
              <b>正在监测中</b>请先在「监测」页停止，否则无法清空。
            </div>
          </div>

          <div class="dlg-foot">
            <button ref="cancelBtn" type="button" class="btn btn-ghost" :disabled="clearing" @click="closeConfirm">取消</button>
            <button
              type="button"
              class="btn btn-danger"
              :disabled="clearing || streamingNow"
              @click="confirmClear"
            >{{ clearing ? '清空中…' : '确认清空' }}</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 改存储位置：先确认要不要把旧数据复制过去，再写 pending（重启生效） -->
    <Teleport to="body">
      <div v-if="pathChoice" class="dlg-mask" @click.self="closePathChoice">
        <div
          class="dlg"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="path-dlg-title"
          @keydown="onPathDialogKey"
        >
          <div class="dlg-head">
            <div class="dlg-ttl"><h3 id="path-dlg-title">{{ pathChoice.title }}</h3></div>
          </div>

          <div class="dlg-body">
            <p>点下面的按钮后<strong>立刻重启</strong>：重启过程中把三个目录的数据搬到新位置，旧位置的文件随即删除。</p>
            <ul class="dlg-list">
              <li v-for="item in choiceChanges" :key="item.key">
                {{ item.label }}
                <div class="path-change">{{ activeLayout[item.key] }} → {{ pathChoice.layout[item.key] }}</div>
              </li>
            </ul>
            <div class="dlg-warn">
              <b>会搬走</b>圈速数据库（连同 WAL / SHM）、配置文件与主题目录。
              新位置已有同名文件时保留新位置的版本，对应的旧文件不删。
            </div>
            <div class="dlg-keep">
              <b>失败自动回滚</b>迁移中途出错（磁盘满、权限不足、文件被占用）会整体撤回，旧位置数据保持原样，不会留下半截数据。
            </div>
          </div>

          <div class="dlg-foot">
            <button ref="pathCancelBtn" type="button" class="btn btn-ghost" :disabled="pathChoiceBusy" @click="closePathChoice">取消</button>
            <button type="button" class="btn btn-primary" :disabled="pathChoiceBusy" @click="applyPathChoice">
              {{ pathChoiceBusy ? '正在保存…' : applyText }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
/* 「高级」折叠标题：和 .set-group > .glbl 同一套观感，但整行可点 */
.adv-head {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  width: 100%;
  padding: 0;
  margin: 0 0 0.77rem;
  background: none;
  border: 0;
  font: inherit;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.adv-head:hover .adv-glbl { color: var(--text); }
.adv-glbl {
  display: flex;
  align-items: center;
  gap: 0.4615rem;
  font-family: var(--font-display);
  font-size: 1.0769rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--text-dim);
  transition: color 0.15s;
}
.adv-glbl::before {
  content: "";
  flex: none;
  width: 0.1538rem;
  height: 1.1538rem;
  border-radius: 0.0769rem;
  background: var(--line);
}
.adv-chev { width: 0.85rem; height: 0.85rem; color: var(--text-dim); transition: transform 0.18s; }
.adv-chev.open { transform: rotate(180deg); }

.path-actions { display: flex; align-items: center; gap: 0.35rem; flex: none; }
.btn-mini { padding: 0.2rem 0.55rem; font-size: 0.78rem; font-weight: 600; line-height: 1.4; }
/* 三个派生目录：只读展示，各占一行 + 一行小字说明里面放什么 */
.path-sub {
  display: grid;
  grid-template-columns: 3.7rem 1fr;
  gap: 0.1rem 0.5rem;
  padding: 0.3rem 0.1rem;
  border-bottom: 0.0769rem dashed var(--line);
}
.path-sub:last-of-type { border-bottom: none; }
.path-sub-label { font-size: 0.8rem; color: var(--text-dim); }
.path-sub-value { font-family: var(--font-mono); font-size: 0.76rem; color: var(--text-dim); word-break: break-all; }
.path-sub-hint { grid-column: 2; font-size: 0.7rem; color: var(--text-faint); }
.path-value {
  font-family: var(--font-mono);
  font-size: 0.78rem;
  color: var(--text-dim);
  line-height: 1.45;
  word-break: break-all;
  padding: 0 0.1rem;
}
.path-note {
  margin: 0.45rem 0;
  padding: 0.5rem 0.65rem;
  border-radius: 0.35rem;
  background: var(--panel-2);
  font-size: 0.8rem;
  line-height: 1.55;
  color: var(--text-dim);
  word-break: break-all;
}
.path-note.warn { background: var(--warn-dim); color: var(--warn); }
.path-note.bad { background: var(--hot-dim); color: var(--hot); }
.path-note b { font-family: var(--font-display); font-weight: 600; }
.path-note-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 0.35rem; margin-top: 0.45rem; }
.path-pending { font-family: var(--font-mono); font-size: 0.74rem; margin: 0.3rem 0; }
.path-note .btn-mini { margin-top: 0.35rem; }
.path-note-actions .btn-mini { margin-top: 0; }
.path-change {
  font-family: var(--font-mono);
  font-size: 0.76rem;
  line-height: 1.45;
  color: var(--text-dim);
  word-break: break-all;
  margin-top: 0.2rem;
}
.dlg-foot { flex-wrap: wrap; }
</style>
