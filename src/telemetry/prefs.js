// 采集 / 性能配置的前端镜像。
// 真实值由后端 /api/config 持久化，这里只用于跨组件共享（例如 App.vue 的关闭行为）。
import { reactive } from 'vue';

import { getConfig } from './api.js';

export const prefs = reactive({
  sampleHz: 100,
  pushHz: 50,
  autoStart: true,
  backgroundResident: false,
});

export function applyPrefs(values) {
  if (!values) return;
  if (values.sampleHz != null) prefs.sampleHz = Number(values.sampleHz);
  if (values.pushHz != null) prefs.pushHz = Number(values.pushHz);
  if (values.autoStart != null) prefs.autoStart = !!values.autoStart;
  if (values.backgroundResident != null) {
    prefs.backgroundResident = !!values.backgroundResident;
  }
}

let loading = null;

/**
 * 读一次后端配置写入 prefs（多处调用共用同一个 Promise）。
 * 后端未启动时失败也无所谓：保留默认值。
 */
export function loadPrefs() {
  if (!loading) {
    loading = getConfig()
      .then((values) => {
        applyPrefs(values);
        return values;
      })
      .catch(() => {
        // 后端（sidecar）冷启动期间读不到配置。失败不能缓存，
        // 否则后续调用会一直拿到 null，前端就按默认值跑了。
        loading = null;
        return null;
      });
  }
  return loading;
}

/**
 * 强制重读后端配置（loadPrefs 成功后会一直复用同一个 Promise）。
 * 悬浮窗收到「主题已切换」事件时用它，否则读到的还是启动时那份。
 */
export function reloadPrefs() {
  loading = null;
  return loadPrefs();
}
