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
      .catch(() => null);
  }
  return loading;
}
