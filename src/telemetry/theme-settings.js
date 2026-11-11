// 内置主题自己的配置项（theme.json 的 `settings`）—— 纯数据，不碰 Vue / Tauri。
//
// 和 `overlaySettings` 的区别：
//   overlaySettings  宿主外观能力（背景 / 文字色），内置与外部主题都能声明；
//   settings         主题自己的业务开关（曲线布局、显示哪些模块……），**只对内置主题开放**。
// 外部主题必须保持「导入 → 预览 / 使用 → 只调背景和文字色」的稳定边界，
// 所以 themes.js 在生成清单时会把用户主题的 settings 直接丢掉，避免外部包偷偷改宿主 UI。
//
// 定义格式（写进 theme.json）：
//   "settings": [
//     { "id": "traceLayout", "group": "曲线", "label": "布局", "type": "segmented",
//       "default": "split",
//       "options": [{ "value": "split", "label": "分开" }, { "value": "merged", "label": "合并" }],
//       "hint": "一句话说明" }
//   ]
// 宿主只负责渲染控件、按槽位存值，并把解析后的对象通过 acc:init / acc:ui 发给主题；
// 主题收到后自己决定怎么画。新增配置项 = 改 theme.json + 主题脚本，不用改宿主代码。

/** 一个主题最多认这么多配置项，防止脏清单把设置面板撑爆。 */
const MAX_SETTINGS = 24;
const MAX_OPTIONS = 8;
const ID_RE = /^[A-Za-z0-9_-]{1,48}$/;

/**
 * 校验 / 归一 theme.json 的 `settings`。
 * 非法项直接丢掉（不判整个主题坏），保证老主题、坏清单都能正常加载。
 * @returns {Array<{id, group, label, type, options: Array<{value, label}>, default, hint}>}
 */
export function normalizeThemeSettingsSchema(raw) {
  if (!Array.isArray(raw)) return [];
  const seen = new Set();
  const out = [];

  for (const item of raw.slice(0, MAX_SETTINGS)) {
    if (!item || typeof item !== 'object') continue;
    const id = typeof item.id === 'string' ? item.id.trim() : '';
    if (!ID_RE.test(id) || seen.has(id)) continue;

    // 目前只有分段按钮一种控件；以后加 slider / switch 在这里放开类型即可。
    if (item.type !== 'segmented') continue;

    const options = Array.isArray(item.options)
      ? item.options
        .filter((o) => o && o.value != null && typeof o.label === 'string' && o.label)
        .slice(0, MAX_OPTIONS)
        .map((o) => ({ value: String(o.value), label: o.label }))
      : [];
    if (options.length < 2) continue;

    const allowed = new Set(options.map((o) => o.value));
    let def = item.default != null ? String(item.default) : options[0].value;
    if (!allowed.has(def)) def = options[0].value;

    seen.add(id);
    out.push({
      id,
      group: typeof item.group === 'string' && item.group ? item.group : '主题',
      label: typeof item.label === 'string' && item.label ? item.label : id,
      type: 'segmented',
      options,
      default: def,
      hint: typeof item.hint === 'string' ? item.hint : '',
    });
  }

  return out;
}

/**
 * 把用户存的值和清单默认值合并成主题真正能用的对象。
 * 不在 options 里的值（旧版本遗留 / 手改 localStorage）一律回默认。
 */
export function resolveThemeSettings(schema, values) {
  const src = values && typeof values === 'object' ? values : {};
  const out = {};
  for (const def of Array.isArray(schema) ? schema : []) {
    const v = src[def.id] != null ? String(src[def.id]) : '';
    out[def.id] = def.options.some((o) => o.value === v) ? v : def.default;
  }
  return out;
}
