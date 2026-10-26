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
  // 启动宽限期内为 true：sidecar 还没起来，各种「断开」都不该当成故障显示
  booting: true,
  // 用户是否「手动」点过开始监测（自动开始为 false）。
  // 自动监测时游戏没开属于预期，静默等待即可；手动点开关才需要明确提示。
  manualStart: false,
});
