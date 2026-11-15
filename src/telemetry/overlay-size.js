// 用户手动拉边改过的悬浮窗尺寸：按「槽位 + 主题」存 localStorage。
//
//      acc.overlayUserSize.<slot> = {
//        "builtin:speed-delta": { w: 640, h: 300, designW: 598, designH: 306 }
//      }
//
// 为什么需要它：Rust 的 window-state.json 已经存了物理宽高，但主题 ready 时
// `fitToTheme()` 会按 theme.json 的 design 重新设尺寸；如果每次都 fit，
// 用户手动拉出来的大小下次启动就会被重置。这里把「用户尺寸」和「当时的 design」
// 一起记下来，宿主据此决定是沿用用户尺寸还是按新 design 适配。
//
// 同时记 design 的原因：主题升级可能改设计尺寸（比如 520×264 → 600×300）。
// 那时旧覆盖不再适用，应当自动失效，否则新主题会被塞进旧尺寸里。
//
// 尺寸一律用逻辑像素（与 theme.json 的 design 同一坐标系），且只记主题区本身
// （不含悬浮窗的 6px 内边距与 1px 边框），这样以后改内边距也不会让旧尺寸失效。

import { OVERLAY_SLOT_MAX } from './theme-choice.js';

function clampSlot(slot) {
  const n = Number(slot);
  return Number.isFinite(n) ? Math.min(OVERLAY_SLOT_MAX, Math.max(1, Math.trunc(n))) : 1;
}

function storageKey(slot) {
  return `acc.overlayUserSize.${clampSlot(slot)}`;
}

function readAll(slot) {
  try {
    const raw = JSON.parse(localStorage.getItem(storageKey(slot)) || 'null');
    return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  } catch {
    return {};
  }
}

function validEntry(entry, design) {
  return !!entry
    && Number.isFinite(entry.w)
    && Number.isFinite(entry.h)
    && entry.w > 0
    && entry.h > 0
    && entry.designW === design.w
    && entry.designH === design.h;
}

/**
 * 读某个槽位 + 主题的用户尺寸。
 * 没有记录、数据被写坏、或主题 design 已经变了（升级换了设计尺寸）都返回 null，
 * 调用方回退到按 design 适配。
 *
 * @param {number} slot 1~5
 * @param {string} themeKey `themeSettingsKey()` 的结果
 * @param {{w:number,h:number}} design 当前主题的设计尺寸
 * @returns {{w:number,h:number}|null} 主题区尺寸（逻辑像素，不含内边距与边框）
 */
export function readOverlayUserSize(slot, themeKey, design) {
  if (!themeKey || !design?.w || !design?.h) return null;
  const entry = readAll(slot)[themeKey];
  if (!validEntry(entry, design)) return null;
  return { w: entry.w, h: entry.h };
}

/**
 * 记下用户手动缩放后的窗口尺寸。
 * @param {number} slot 1~5
 * @param {string} themeKey `themeSettingsKey()` 的结果
 * @param {{w:number,h:number}} design 当前主题的设计尺寸
 * @param {number} w 主题区逻辑宽
 * @param {number} h 主题区逻辑高
 */
export function writeOverlayUserSize(slot, themeKey, design, w, h) {
  if (!themeKey || !design?.w || !design?.h) return;
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return;
  const all = readAll(slot);
  all[themeKey] = {
    w: Math.round(w),
    h: Math.round(h),
    designW: design.w,
    designH: design.h,
  };
  try {
    localStorage.setItem(storageKey(slot), JSON.stringify(all));
  } catch {
    /* 隐私模式 / 配额满：本次会话仍按窗口实际尺寸走 */
  }
}

/**
 * 清掉某个主题的用户尺寸（用户把尺寸调回 design 时，或点了「默认尺寸」）。
 * 只影响当前槽位 + 当前主题，其它主题的记录保留。
 */
export function clearOverlayUserSize(slot, themeKey) {
  if (!themeKey) return;
  const all = readAll(slot);
  if (!(themeKey in all)) return;
  delete all[themeKey];
  try {
    localStorage.setItem(storageKey(slot), JSON.stringify(all));
  } catch {
    /* 同上 */
  }
}
