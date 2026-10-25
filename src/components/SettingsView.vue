<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ACCENTS, accentName, setAccent, setTheme, themeName } from '../telemetry/theme.js';
import { clearHistory, getBaseUrl, getConfig, getHealth, listSessions, saveConfig } from '../telemetry/api.js';
import { telemetry } from '../telemetry/useTelemetry.js';
import { applyPrefs, prefs } from '../telemetry/prefs.js';
import HelpTip from './HelpTip.vue';

const props = defineProps({ visible: { type: Boolean, default: true } });

const SW = [100, 50, 20];
const PUSH = [60, 50, 30];
const SAMPLE_LABEL = { 100: '100 Hz 高精', 50: '50 Hz 标准', 20: '20 Hz 省电' };

const swAuto = ref(true);
const clearText = ref('清空历史数据');
const health = ref(null);
const config = ref(null);
const sessionCount = ref(0);
const lapCount = ref(0);
const errorText = ref('');
const serverText = ref('127.0.0.1:8008');
const saving = ref(false);
let healthTimer = null;

const totalLapsText = computed(() => sessionCount.value + ' 组记录 · ' + lapCount.value + ' 圈');
const dbPath = computed(() => health.value?.dbPath || config.value?.dbPath || '—');
const configPath = computed(() => config.value?.configPath || '—');
const versionText = computed(() => (health.value?.version || config.value?.version || '—') + ' · 本地后端');
const statusText = computed(() => {
  if (!health.value) return '未连接';
  if (!health.value.dbOk) return '数据库异常';
  return health.value.streaming ? '监测中' : '在线';
});
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

async function clearData() {
  if (!window.confirm('确定清空所有赛道历史数据？此操作不可恢复。')) return;
  try {
    await clearHistory();
    clearText.value = '已清空';
    await refresh();
  } catch (e) {
    clearText.value = e.status === 409 ? '监测中无法清空' : '清空失败';
    errorText.value = e.status === 409 ? '请先停止监测' : ('清空失败：' + e.message);
  } finally {
    setTimeout(() => { clearText.value = '清空历史数据'; }, 1600);
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
});

onBeforeUnmount(stopPolling);

watch(() => props.visible, (visible) => {
  if (visible) {
    refresh();
    startPolling();
  } else {
    stopPolling();
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
      <div class="set-row"><span>启动后自动开始监测</span>
        <button class="switch" :class="{ on: swAuto }" @click="toggleAuto" aria-label="自动监测"></button>
      </div>
    </div>

    <div class="set-group">
      <span class="glbl">数据</span>
      <div class="set-row"><span>数据目录</span><span class="v" :title="dbPath">{{ dbPath }}</span></div>
      <div class="set-row"><span>配置文件</span><span class="v" :title="configPath">{{ configPath }}</span></div>
      <div class="set-row"><span>已记录</span><span class="v">{{ totalLapsText }}</span></div>
      <div v-if="errorText" class="set-row"><span></span><span class="v" style="color:var(--hot)">{{ errorText }}</span></div>
      <div style="margin-top:0.77rem"><button class="btn btn-danger" @click="clearData">{{ clearText }}</button></div>
    </div>

    <div class="set-group">
      <span class="glbl">关于</span>
      <div class="set-row"><span>版本</span><span class="v">{{ versionText }}</span></div>
    </div>
  </section>
</template>
