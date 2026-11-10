// 软件更新线路：auto = GitHub → Gitee 自动兜底；github / gitee = 强制单线路。
//
// 存 localStorage（本机界面偏好，不进 config.json）：
//   acc-update-route = 'auto' | 'github' | 'gitee'
//
// auto 走 tauri-plugin-updater 配置里的双 endpoint；强制线路走 Rust 命令
// check_update_route（它按指定 endpoint 重新建 Updater，返回同样的 metadata）。
import { ref } from 'vue';

const KEY = 'acc-update-route';

export const UPDATE_ROUTES = [
  { id: 'auto', label: '自动' },
  { id: 'github', label: 'GitHub' },
  { id: 'gitee', label: 'Gitee' },
];

function normalize(value) {
  return UPDATE_ROUTES.some((r) => r.id === value) ? value : 'auto';
}

function read() {
  try {
    return normalize(localStorage.getItem(KEY));
  } catch {
    return 'auto';
  }
}

export const updateRoute = ref(read());

export function setUpdateRoute(value) {
  const next = normalize(value);
  updateRoute.value = next;
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* 隐私模式 / 存储被禁用：本次会话仍生效 */
  }
  return next;
}
