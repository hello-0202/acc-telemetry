<script setup>
// 主题预览窗口的内容：把选中的主题铺满整窗渲染（演示数据）。
//
// 主题 id / 来源 / 模式从 URL 查询参数取（Rust 的 open_theme_preview 拼好传进来）。
// 主窗口复用已开的预览窗口换主题时，会推一条 preview-load 事件，这里据此重新加载。
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { listen } from '@tauri-apps/api/event';

import ThemePreview from './ThemePreview.vue';

const params = new URLSearchParams(location.search);
const theme = ref(params.get('id') || '');
const source = ref(params.get('source') || 'builtin');
const mode = ref(params.get('mode') || 'overlay');
const reloadKey = ref(0);
const error = ref('');

let unlisten = null;

onMounted(() => {
  if (!theme.value) {
    error.value = '缺少主题 id';
    return;
  }
  // 主窗口复用已开的预览窗口、换成另一个主题时，Rust 推这条事件
  listen('preview-load', (e) => {
    const p = e.payload || {};
    if (p.id) {
      theme.value = p.id;
      source.value = p.source || 'builtin';
      mode.value = p.mode || 'overlay';
      reloadKey.value += 1; // 换个 key 强制 ThemePreview 重新加载 iframe
    }
  })
    .then((fn) => { unlisten = fn; })
    .catch(() => { /* 权限缺失时忽略 */ });
});

onBeforeUnmount(() => {
  if (unlisten) unlisten();
});
</script>

<template>
  <div class="pv-win">
    <div v-if="error" class="pv-err">{{ error }}</div>
    <ThemePreview
      v-else
      :key="reloadKey"
      :theme="theme"
      :source="source"
      :mode="mode"
      live
      :badge="true"
    />
  </div>
</template>

<style scoped>
.pv-win {
  position: fixed;
  inset: 0;
  background: #0d1116;
}

.pv-err {
  color: #d98b8b;
  font: 12px/1.4 ui-monospace, monospace;
  padding: 12px;
}
</style>
