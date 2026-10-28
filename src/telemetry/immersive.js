import { ref, watch } from 'vue';
import { getCurrentWindow } from '@tauri-apps/api/window';

// 全屏沉浸：把应用外壳（标题栏 / 顶部导航 / 状态栏）收起来，让当前面板铺满屏幕。
// 面板内容本身的排版规则不变，靠 html.immersive 的根字号等比放大（见 style.css）。
// 主窗口的「全屏」按钮与 Esc 共用这一份状态。

const isTauri = typeof window !== 'undefined' && !!window.__TAURI_INTERNALS__;
const appWindow = isTauri ? getCurrentWindow() : null;

export const immersive = ref(false);

// 挂到 <html> 上，供 CSS 改写根字号；同时也能让别的样式按沉浸态分支
watch(
  immersive,
  (on) => {
    const root = document.documentElement;
    root.classList.toggle('immersive', on);
  },
  { immediate: true }
);

// 进入沉浸前窗口是否已经是全屏（用户按过 F11 / 系统快捷键）：
// 退出沉浸时还原成进之前的样子，而不是一律取消全屏。
let restoreFullscreen = false;

async function setWindowFullscreen(on) {
  if (appWindow) {
    try {
      await appWindow.setFullscreen(on);
    } catch (e) {
      console.error('setFullscreen failed:', e);
    }
    return;
  }
  // 浏览器预览（vite dev / preview）没有 Tauri 窗口，退回 DOM 全屏 API
  try {
    if (on && !document.fullscreenElement) await document.documentElement.requestFullscreen();
    if (!on && document.fullscreenElement) await document.exitFullscreen();
  } catch (e) {
    /* 用户未授权或被浏览器拦截，忽略 */
  }
}

export async function enterImmersive() {
  if (immersive.value) return;
  restoreFullscreen = false;
  if (appWindow) {
    try {
      restoreFullscreen = await appWindow.isFullscreen();
    } catch (e) {
      /* 拿不到就当作否 */
    }
  }
  immersive.value = true;
  await setWindowFullscreen(true);
}

export async function exitImmersive() {
  if (!immersive.value) return;
  immersive.value = false;
  await setWindowFullscreen(appWindow ? restoreFullscreen : false);
}

export async function toggleImmersive() {
  if (immersive.value) await exitImmersive();
  else await enterImmersive();
}
