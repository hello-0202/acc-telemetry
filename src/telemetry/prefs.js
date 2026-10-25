// 采集 / 性能配置的前端镜像。
// 真实值由后端 /api/config 持久化，这里只用于跨组件共享（例如 App.vue 的关闭行为）。
import { reactive } from 'vue';

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
