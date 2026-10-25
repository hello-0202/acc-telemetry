import { reactive } from 'vue';

// Shared UI state that crosses component boundaries.
export const ui = reactive({
  statLeft: '未连接 · ws://127.0.0.1:8008',
  monOn: false,
  liveTrackId: null,
  liveSessionId: null,
  // 全局连接 / 游戏状态（App.vue 轮询 /health 写入）
  backendOnline: null, // null=检测中
  gameRunning: null,
});
