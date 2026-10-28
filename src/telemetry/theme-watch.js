// 用户主题目录的变更广播（Rust `notify` → emit `themes-changed`）。
//
// 为什么要个中间层：设置页和悬浮窗 / 预览缩略图都要听同一个事件，
// 各自 listen 会注册多个回调（而且浮窗和主窗口是两个 webview，本来就各听一份）。
// 这里做模块级单例：第一次有人订阅时才 listen，订阅者全退订就保持空跑，
// 避免「页面切走 → 回来」把监听叠加成 N 份。
//
// payload 是受影响的主题 id 数组（`["my-hud"]`），可能一次多个（批量粘贴目录）。
import { listen } from '@tauri-apps/api/event';

const isTauri = typeof window !== 'undefined' && !!window.__TAURI_INTERNALS__;

const subs = new Set();
let listening = false;

async function ensureListening() {
  if (listening || !isTauri) return;
  listening = true;
  try {
    await listen('themes-changed', (event) => {
      const ids = Array.isArray(event.payload) ? event.payload : [];
      for (const fn of subs) {
        // 单个订阅者出错不能连累其他订阅者（例如设置页列表重建失败）
        try {
          fn(ids);
        } catch (err) {
          console.warn('[theme] 变更回调异常:', err);
        }
      }
    });
  } catch (err) {
    // 权限缺失 / 非 Tauri 环境下静默降级：热重载没有，但功能不受影响
    console.warn('[theme] 主题变更监听不可用（热重载关闭）:', err);
  }
}

/**
 * 订阅用户主题目录变更。
 * @param {(ids: string[]) => void} fn
 * @returns {() => void} 取消订阅
 */
export function onThemesChanged(fn) {
  subs.add(fn);
  ensureListening();
  return () => subs.delete(fn);
}
