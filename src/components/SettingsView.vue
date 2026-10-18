<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import { ACCENTS, accentName, setAccent, setTheme, themeName } from '../telemetry/theme.js';
import { clearHistory, getBaseUrl, getConfig, getHealth, listSessions, saveConfig } from '../telemetry/api.js';
import { telemetry } from '../telemetry/useTelemetry.js';

const swAuto = ref(true);
const clearText = ref('清空历史数据');
const health = ref(null);
const config = ref(null);
const sessionCount = ref(0);
const lapCount = ref(0);
const errorText = ref('');
const serverText = ref('127.0.0.1:8008');

const totalLapsText = computed(() => sessionCount.value + ' 组记录 · ' + lapCount.value + ' 圈');
const dbPath = computed(() => health.value?.dbPath || config.value?.dbPath || '—');
const pushText = computed(() => (config.value?.pushHz ?? 50) + ' Hz');
const versionText = computed(() => (health.value?.version || config.value?.version || '—') + ' · 本地后端');
const statusText = computed(() => {
  if (!health.value) return '未连接';
  if (!health.value.dbOk) return '数据库异常';
  return health.value.streaming ? '监测中' : '在线';
});

async function refresh() {
  try {
    const [h, c, sessions] = await Promise.all([getHealth(), getConfig(), listSessions()]);
    health.value = h;
    config.value = c;
    sessionCount.value = sessions.length;
    lapCount.value = sessions.reduce((sum, s) => sum + (s.laps || 0), 0);
    swAuto.value = !!c.autoStart;
    serverText.value = getBaseUrl().replace(/^https?:\/\//, '');
    errorText.value = '';
  } catch (e) {
    health.value = null;
    errorText.value = '无法连接后端：' + e.message;
  }
}

async function toggleAuto() {
  swAuto.value = !swAuto.value;
  try {
    config.value = await saveConfig({ autoStart: swAuto.value });
  } catch (e) {
    swAuto.value = !swAuto.value;
    errorText.value = '保存失败：' + e.message;
  }
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

onMounted(refresh);
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
      <span class="glbl">监测</span>
      <div class="set-row"><span>后端状态</span><span class="v">{{ statusText }}</span></div>
      <div class="set-row"><span>推送频率</span><span class="v">{{ pushText }}</span></div>
      <div class="set-row"><span>服务地址</span><span class="v">{{ serverText }}</span></div>
      <div class="set-row"><span>启动后自动开始监测</span>
        <button class="switch" :class="{ on: swAuto }" @click="toggleAuto" aria-label="自动监测"></button>
      </div>
    </div>

    <div class="set-group">
      <span class="glbl">数据</span>
      <div class="set-row"><span>数据目录</span><span class="v" :title="dbPath">{{ dbPath }}</span></div>
      <div class="set-row"><span>已记录</span><span class="v">{{ totalLapsText }}</span></div>
      <div v-if="errorText" class="set-row"><span></span><span class="v" style="color:var(--hot)">{{ errorText }}</span></div>
      <div style="margin-top:10px"><button class="btn btn-danger" @click="clearData">{{ clearText }}</button></div>
    </div>

    <div class="set-group">
      <span class="glbl">关于</span>
      <div class="set-row"><span>版本</span><span class="v">{{ versionText }}</span></div>
    </div>
  </section>
</template>
