<script setup>
// 主面板的「壳」：决定数据展示区用哪一个。
//
//   内置面板（BUILTIN_PANEL）—— MonitorDashboard.vue，功能最全（含实时赛道图）
//   主题（mode = monitor）    —— ThemeHost 挂 iframe，主题自己排版
//
// 为什么只壳化展示区：选圈、缩放、页签切换、开始/停止都在外层 Vue 里，
// 把整块做成 iframe 会让这些交互全部失效。主题只负责「怎么把数据画出来」。
//
// 主题加载失败（4s 没 ready）会自动回退到内置面板并提示，绝不留空白。
import { computed, ref, watch } from 'vue';

import MonitorDashboard from './MonitorDashboard.vue';
import ThemeHost from './ThemeHost.vue';
import { telemetry } from '../telemetry/useTelemetry.js';
import { BUILTIN_PANEL, DEFAULT_MONITOR_THEME } from '../telemetry/themes.js';
import { themeChoice } from '../telemetry/theme-choice.js';

const dash = ref(null);
const failed = ref(false);

const choice = computed(() => themeChoice.monitor || DEFAULT_MONITOR_THEME);
// '_panel' 是内置面板的占位 id（不是主题），见 themes.js::BUILTIN_PANEL。
// 主题加载失败时也回退到内置面板 —— 主面板不能留空白。
const isPanel = computed(() => choice.value.id === BUILTIN_PANEL.id || failed.value);

// 换主题 / 重新启用时把失败标记清掉，否则回退状态会一直粘着
watch(() => `${choice.value.source}:${choice.value.id}`, () => {
  failed.value = false;
});

function onThemeFail() {
  failed.value = true;
}

/** 开始监测时清掉上一场的残值（胎温、地图走线）——壳负责把调用转给内置面板。 */
function reset() {
  dash.value?.reset?.();
}

defineExpose({ reset });
</script>

<template>
  <div class="mon-shell">
    <MonitorDashboard v-if="isPanel" ref="dash" />
    <div v-else class="mon-theme">
      <ThemeHost
        :theme="choice.id"
        :source="choice.source"
        mode="monitor"
        :state="telemetry.state"
        @fail="onThemeFail"
      />
    </div>

    <div v-if="failed" class="mon-theme-fallback">
      <span>主题「{{ choice.id }}」没能加载（脚本没发 acc:ready），已临时切回内置面板。</span>
      <span class="dim">去「设置 → 主面板主题」换成别的，或修好主题后重试。</span>
    </div>
  </div>
</template>

<style scoped>
.mon-shell {
  display: flex;
  flex-direction: column;
  /* 父级 .live-wrap 是 display:block（高度由 flex:1 撑开），所以这里必须 height:100%
     而不是 flex:1 —— 否则壳的高度塌成内容高度，主题 iframe 拿不到空间。 */
  height: 100%;
  min-height: 0;
  gap: 0.4615rem;
}

/* 主题拿到整个数据区的空间；外壳的圆角/间距由 .live-wrap 统一给 */
.mon-theme {
  flex: 1;
  min-height: 0;
  border-radius: 0.4615rem;
  overflow: hidden;
}

.mon-theme-fallback {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4615rem;
  padding: 0.3846rem 0.6154rem;
  border-radius: 0.3077rem;
  background: rgba(220, 120, 60, 0.1);
  box-shadow: inset 0 0 0 0.0769rem rgba(220, 120, 60, 0.32);
  color: var(--text);
  font-size: 0.8462rem;
}

.mon-theme-fallback .dim {
  color: var(--text-dim);
}
</style>
