<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ACCENTS, accentName, setAccent, setTheme, themeName } from '../telemetry/theme.js';
import { openUrl } from '@tauri-apps/plugin-opener';
import { getVersion } from '@tauri-apps/api/app';
import { clearHistory, getBaseUrl, getConfig, getHealth, listSessions, saveConfig } from '../telemetry/api.js';
import { telemetry } from '../telemetry/useTelemetry.js';
import { applyPrefs, prefs } from '../telemetry/prefs.js';
import HelpTip from './HelpTip.vue';

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

const swAuto = ref(true);
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

async function refresh() {
  try {
    const [h, c, sessions] = await Promise.all([getHealth(), getConfig(), listSessions()]);
    health.value = h;
    config.value = c;
    applyPrefs(c);
    swAuto.value = !!c.autoStart;
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
    swAuto.value = !!c.autoStart;
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

function toggleAuto() {
  patch({ autoStart: !swAuto.value });
}

/* 自动更新走弹窗里的下载/安装流程，这里只保留一个手动下载的兜底入口 */
async function openReleases() {
  try {
    await openUrl(releasesUrl.value);
  } catch (e) {
    console.error('open releases page failed:', e);
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
  startPolling();
  // 浏览器预览里没有 Tauri 运行时，取不到就用占位符
  getVersion().then((v) => { appVersion.value = v; }).catch(() => { /* ignore */ });
});

onBeforeUnmount(stopPolling);

watch(() => props.visible, (visible) => {
  if (visible) {
    refresh();
    startPolling();
  } else {
    stopPolling();
    // 切走页面时把弹窗收掉（Teleport 到 body，不会随页面一起隐藏）
    if (!clearing.value) confirmOpen.value = false;
  }
});
watch(() => telemetry.status, refresh);
watch(() => telemetry.historyRevision, refresh);
</script>

<template>
  <section class="view" id="view-settings">
    <div class="set-group">
      <span class="glbl">外观</span>
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
      <div class="set-row"><span title="打开应用后自动开始监测；监测中重新进入赛道会自动接入记录">打开应用后自动开始监测</span>
        <button class="switch" :class="{ on: swAuto }" @click="toggleAuto" aria-label="打开应用后自动开始监测"></button>
      </div>
    </div>

    <div class="set-group">
      <span class="glbl">数据</span>
      <div class="set-row"><span>数据目录</span><span class="v" :title="dbPath">{{ dbPath }}</span></div>
      <div class="set-row"><span>配置文件</span><span class="v" :title="configPath">{{ configPath }}</span></div>
      <div class="set-row"><span>已记录</span><span class="v">{{ totalLapsText }}</span></div>
      <div v-if="errorText" class="set-row"><span></span><span class="v" style="color:var(--hot)">{{ errorText }}</span></div>
      <div style="margin-top:0.77rem"><button class="btn btn-danger" @click="openConfirm">{{ clearText }}</button></div>
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
  </section>
</template>
