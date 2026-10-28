// 主题清单：内置主题（编译进二进制）+ 用户主题（%LOCALAPPDATA%\acc-telemetry\themes\<id>）。
//
// 职责划分：Rust 只负责「把用户目录里的文件找出来」（list_user_themes），
// **校验规则全部在这里**，只写一份，避免前后端两套规则漂移。
import { invoke } from '@tauri-apps/api/core';

import { isTauriRuntime, themeBaseUrl } from './theme-url.js';

// 内置主题登记表：新增一个内置主题 = 放进 public/themes/<id>/ + 这里加一行。
// （public/ 下的文件不会被 import 分析，没法自动枚举，所以只能显式列。）
export const BUILTIN_THEME_IDS = ['overlay-min', 'monitor-grid'];

export const DEFAULT_THEME = { id: 'overlay-min', source: 'builtin' };

// 主面板的「零号主题」：不是主题，而是编译进主程序的 Vue 面板（含实时赛道图，
// 主题拿不到地图几何，所以内置面板仍然是功能最全的那个）。MonitorShell 见到这个 id
// 就直接渲染 MonitorDashboard，不挂 iframe。
export const BUILTIN_PANEL = { id: '_panel', source: 'builtin' };

export const DEFAULT_MONITOR_THEME = BUILTIN_PANEL;

/** 某个窗口槽位（overlay / monitor）能用的主题：按 theme.json 的 modes 过滤。 */
export function themesForMode(list, mode) {
  return list.filter((t) => t.error || (t.modes || []).includes(mode));
}

const MODES = ['overlay', 'monitor'];
const FITS = ['contain', 'fluid'];

/**
 * 校验 theme.json。
 * @returns {string|null} 错误原因；合法返回 null
 */
function checkMeta(id, meta, hasIndex) {
  if (meta === undefined) return 'theme.json 不是合法 JSON';
  if (!meta || typeof meta !== 'object') return '缺少 theme.json';
  if (meta.id !== id) return `theme.json 的 id（${meta.id ?? '空'}）与目录名不一致`;
  const d = meta.design;
  if (!d || !Number.isInteger(d.w) || !Number.isInteger(d.h)) return 'design.w / design.h 必须是整数';
  if (d.w < 80 || d.w > 8192 || d.h < 80 || d.h > 8192) return 'design 尺寸超出 80~8192';
  if (meta.fit != null && !FITS.includes(meta.fit)) return `fit 只支持 ${FITS.join(' / ')}`;
  if (!Array.isArray(meta.modes) || meta.modes.length === 0) return 'modes 不能为空';
  if (meta.modes.some((m) => !MODES.includes(m))) return 'modes 里有未知的窗口类型';
  if (!hasIndex) return '缺少 index.html';
  return null;
}

function makeEntry(id, source, meta, hasIndex) {
  const m = meta && typeof meta === 'object' ? meta : null;
  return {
    key: `${source}:${id}`,
    id,
    source,
    error: checkMeta(id, meta, hasIndex),
    name: m?.name || id,
    version: m?.version || '',
    author: m?.author || '',
    desc: m?.desc || '',
    design: m?.design || null,
    fit: m?.fit || 'contain',
    modes: m?.modes || [],
  };
}

/**
 * 扫一遍所有主题。坏主题不抛异常，只在 `error` 里写原因（应用不能因为一个坏主题挂掉）。
 * @returns {Promise<Array>} 用户主题在前、坏主题沉底
 */
export async function listThemes() {
  const out = [];

  for (const id of BUILTIN_THEME_IDS) {
    let meta = null;
    try {
      const res = await fetch(`${themeBaseUrl(id, 'builtin')}theme.json`, { cache: 'no-cache' });
      if (res.ok) meta = await res.json();
      else if (res.status !== 404) meta = undefined;
    } catch {
      meta = null;
    }
    out.push(makeEntry(id, 'builtin', meta, true));
  }

  if (isTauriRuntime()) {
    let raw = [];
    try {
      raw = (await invoke('list_user_themes')) || [];
    } catch (e) {
      console.warn('读取用户主题失败:', e);
    }
    for (const t of raw) {
      let meta = null;
      if (t.meta != null) {
        try {
          meta = JSON.parse(t.meta);
        } catch {
          meta = undefined; // 解析失败：checkMeta 会报「不是合法 JSON」
        }
      }
      out.push(makeEntry(t.id, 'user', meta, !!t.hasIndex));
    }
  }

  const rank = (t) => (t.error ? 2 : 0) + (t.source === 'user' ? 0 : 1);
  return out.sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name, 'zh-Hans-CN'));
}
