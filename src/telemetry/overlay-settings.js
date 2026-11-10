// 悬浮窗「设置项」注册表 —— 右侧设置面板的单一真源。
//
// 2026-11-10 的需求：左侧选中一个已启用的悬浮窗后，右侧显示它的相关设置。
// 目前有两项外观设置（背景颜色 / 背景不透明度），底层要能继续扩展：
// 以后给内置悬浮窗加文字颜色、字号之类的设置，只需要往下面这个数组里追加一项，
// **不用改 ThemePicker.vue**。新增设置项若影响预览，再在 ThemePicker 里透传给预览即可
// （参照 backgroundColor / backgroundOpacity）。
//
// 每一项（def）的契约：
//   id        唯一标识，也用于 v-for 的 key
//   group     所属分组（对应 OVERLAY_SETTING_GROUPS）：'background'，以后可加 'text'
//   label     设置项名称（分组内短名，如「不透明度」「颜色」）
//   type      控件类型：'segmented'（分段按钮）/ 'slider'（滑杆）/ 'color'（取色器 + 预设色板）
//   options   segmented 专用：[{ value, label }]
//   min/max/step/unit  slider 专用
//   presets   slider 专用快捷档位 [{ value, label }]；color 专用预设色（#rrggbb 字符串数组）
//   hint      一句话说明，显示在控件下面
//   appliesTo(theme) -> bool  可选钩子，用来限定「哪些主题才有这项设置」
//   read(slot) -> value       **必须读响应式状态**（reactive / ref），右侧面板才能在别的窗口改完后跟着更新
//   write(slot, value)        按槽位写 localStorage + 广播事件，让对应 OverlayView 立刻生效
//
// 扩展步骤（以后加「文字颜色」为例）：
//   1. 在 overlay-background.js（或新建 overlay-text.js）里加按槽位存值 + emit 事件；
//   2. 在 OVERLAY_SETTING_GROUPS 追加 { id:'text', label:'文字' }，
//      再在下面追加一项 { id:'textColor', group:'text', label:'颜色', type:'color', presets:[...], appliesTo, read, write }；
//   3. 外部主题要支持就在 theme.json 的 overlaySettings 里加 'textColor'；
//      OverlayView.vue 读到值后透传给 ThemeHost / `acc:ui`，主题自己决定怎么用。
// 右侧面板、选中联动、按槽位隔离都由 ThemePicker 统一处理，不需要再改 UI。
import {
  overlayAppearanceFor,
  setOverlayAppearance,
} from './overlay-background.js';

/**
 * 主题能支持哪些外观设置：约定写在 theme.json 的 `overlaySettings` 数组里。
 *   "overlaySettings": ["background", "textColor"]
 *
 * 内置主题随应用一起发布，默认视为支持全部设置（改了内置主题也来得及一起发版）；
 * 外部（用户）主题只有显式声明过的能力才会出现对应设置项 —— 这是内置 / 外部的界限。
 * 老外部主题不带这个字段时，右侧只显示一句说明，不会出现控件。
 */
export function themeSupportsOverlaySetting(theme, capability) {
  if (!theme) return false;
  if ((theme.source || 'builtin') !== 'user') return true;
  const list = theme.overlaySettings;
  return Array.isArray(list) && list.includes(capability);
}

// 一组外观设置共用同一个能力名：以后要独立控制，再拆成 'backgroundOpacity' 等即可。
const CAP_BACKGROUND = 'background';
const CAP_TEXT = 'textColor';

/**
 * 设置面板的分组（显示顺序）。每项 def 的 `group` 对应这里的 id。
 * 以后加「文字颜色」时：在下面追加 { id:'text', label:'文字' }，
 * 并把新 def 的 group 写成 'text' 即可，ThemePicker 会自动多出一栏。
 */
export const OVERLAY_SETTING_GROUPS = [
  { id: 'background', label: '背景' },
  { id: 'text', label: '文字' },
];

export const OVERLAY_SETTING_DEFS = [
  {
    id: 'backgroundOpacity',
    group: 'background',
    // 分组标题已经是「背景」，控件标签不再重复前缀
    label: '不透明度',
    type: 'slider',
    min: 0,
    max: 100,
    step: 1,
    unit: '%',
    // 快捷档位：保留旧版「透明 / 不透明」的肌肉记忆，中间再加一档半透明
    presets: [
      { value: 0, label: '透明' },
      { value: 50, label: '半透明' },
      { value: 100, label: '不透明' },
    ],
    appliesTo: (theme) => themeSupportsOverlaySetting(theme, CAP_BACKGROUND),
    read: (slot) => Math.round(overlayAppearanceFor(slot).opacity * 100),
    write: (slot, value) => setOverlayAppearance(slot, { opacity: Number(value) / 100 }),
  },
  {
    id: 'backgroundColor',
    group: 'background',
    label: '颜色',
    type: 'color',
    // 预设只保留黑 / 白；其它颜色点右侧色盘自选
    presets: ['#000000', '#ffffff'],
    appliesTo: (theme) => themeSupportsOverlaySetting(theme, CAP_BACKGROUND),
    read: (slot) => overlayAppearanceFor(slot).color,
    write: (slot, value) => setOverlayAppearance(slot, { color: value }),
  },
  {
    id: 'backgroundBlur',
    group: 'background',
    label: '模糊',
    type: 'segmented',
    options: [
      { value: 0, label: '关闭' },
      { value: 1, label: '开启' },
    ],
    appliesTo: (theme) => themeSupportsOverlaySetting(theme, CAP_BACKGROUND),
    read: (slot) => (overlayAppearanceFor(slot).blur > 0 ? 1 : 0),
    write: (slot, value) => setOverlayAppearance(slot, { blur: Number(value) > 0 ? 1 : 0 }),
  },
  {
    id: 'textColor',
    group: 'text',
    label: '颜色',
    type: 'color',
    // 预设：浅蓝 / 琥珀 / 红 + 黑 / 白；其它颜色点右侧色盘自选
    presets: ['#7db6c7', '#f7c964', '#e53325', '#000000', '#ffffff'],
    appliesTo: (theme) => themeSupportsOverlaySetting(theme, CAP_TEXT),
    read: (slot) => overlayAppearanceFor(slot).textColor,
    write: (slot, value) => setOverlayAppearance(slot, { textColor: value }),
  },
];

/** 某个主题在某个槽位上可显示的设置项（带当前值）。theme 为 null 或槽位为 0 时返回空数组。 */
export function settingsForTheme(theme, slot) {
  if (!theme || !slot) return [];
  return OVERLAY_SETTING_DEFS
    .filter((def) => !def.appliesTo || def.appliesTo(theme))
    .map((def) => ({ def, value: def.read(slot) }));
}

/**
 * 按分组返回设置项：ThemePicker 渲染「背景」这类小节时用。
 * 空分组会被丢掉；没有任何可用设置时返回空数组（外部主题未声明能力的情况）。
 * @returns {Array<{ id, label, items: Array<{ def, value }> }>}
 */
export function settingGroupsForTheme(theme, slot) {
  const items = settingsForTheme(theme, slot);
  if (!items.length) return [];
  return OVERLAY_SETTING_GROUPS
    .map((group) => ({
      ...group,
      items: items.filter((s) => s.def.group === group.id),
    }))
    .filter((group) => group.items.length > 0);
}

/** 按设置项定义写值；ThemePicker 不关心各项内部怎么存 / 怎么广播。 */
export function writeOverlaySetting(def, slot, value) {
  if (!slot) return;
  def.write(slot, value);
}

/**
 * 读某个设置项的当前值（预览用）：ThemePicker 把它透传给预览组件，设置面板改完预览立刻跟着变。
 * 注意：只在该设置项 appliesTo 这个主题时才有意义；不支持的主题请检查 settingsForTheme 的结果。
 * @returns {*} 该设置项在当前槽位的值，找不到设置项 / 无效参数时返回 undefined。
 */
export function readSettingValue(theme, slot, id) {
  if (!theme || !slot || !id) return undefined;
  const def = OVERLAY_SETTING_DEFS.find((d) => d.id === id);
  return def ? def.read(slot) : undefined;
}
