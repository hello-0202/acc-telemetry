<script setup>
// 主题选择器（设置页一行入口 + 弹窗）。两种形态共用一个组件、一块实时预览大框：
//
//   mode="overlay"（游戏悬浮窗）：左侧是**双列二级侧栏** —— 第一列分类，第二列该分类下的
//     主题条目（每项「预览 / 使用·停用 / 删除」）；右侧只有「固定大尺寸预览框 + 按选中主题
//     的设置面板」。可同时启用最多 5 个悬浮窗：每个「使用」占一个槽位，Rust 按槽位建窗口。
//   mode="monitor"（主面板数据区）：仍是「预览大框 + 不分组平铺网格」，单选，保持原样。
//
// 设置页两个入口都只留「标题 + 更改」，**都不显示当前选中的主题名**（悬浮窗是多选，
// 没有「当前这一个」；主面板按用户要求也不展示），弹窗里也不写「当前使用 X」。
// 界面刻意不暴露槽位编号 / 主题设计尺寸：卡片恒显示「内置 / 外部」，启用状态靠色条、
// 「停用」按钮与「已启用」标签表达，不出现「悬浮窗 1/2/3」；启用 / 停用都不弹提示。
// 校验在 themes.js，主题选择存 theme-choice.js，槽位存 overlay-slots.js，设置项存
// overlay-settings.js（注册表驱动，以后加颜色等设置不用改这个组件）。
//
// ⚠️ 预览大框的 iframe 跑在主窗口里（用户明确要求「全部内嵌」）：它会继承主窗口的
// Tauri 命令权限，sandbox 挡不住 IPC 注入。2026-11-10 起连「独立窗口打开 / 关闭预览」按钮
// 也按用户要求去掉了 —— 现在预览恒定内嵌且不可关闭，只能预览可信主题。
// Rust 的 open_theme_preview / theme-preview 隔离窗口仍保留（做主题市场前必须把预览挪回去），
// 只是设置页不再提供入口。详见 ENGINEERING-NOTES 第 10 节。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { invoke } from '@tauri-apps/api/core';
import { emit as emitEvent } from '@tauri-apps/api/event';
// 注意别名：本文件里 `open` 是弹窗开关的 ref
import { open as openFileDialog, save as saveFileDialog } from '@tauri-apps/plugin-dialog';

import {
  BUILTIN_PANEL,
  DEFAULT_MONITOR_THEME,
  DEFAULT_THEME,
  listThemes,
  themesForMode,
} from '../telemetry/themes.js';
import {
  OVERLAY_SLOT_MAX,
  saveThemeChoice,
  themeChoice,
  themeChoiceKey,
} from '../telemetry/theme-choice.js';
import { onThemesChanged } from '../telemetry/theme-watch.js';
import { THEME_CATEGORIES, UNCATEGORIZED, categoryLabel } from '../telemetry/theme-categories.js';
import { overlaySlots, setOverlaySlotEnabled } from '../telemetry/overlay-slots.js';
import { settingGroupsForTheme, settingsForTheme, writeOverlaySetting } from '../telemetry/overlay-settings.js';
import {
  overlayThemeSettingsVersion,
  readThemeSettings,
  themeSettingsKey,
  writeThemeSetting,
} from '../telemetry/overlay-theme-settings.js';
import { resolveThemeSettings } from '../telemetry/theme-settings.js';
import ThemePreview from './ThemePreview.vue';

const props = defineProps({
  /** overlay | monitor */
  mode: { type: String, required: true },
  /** 悬浮窗默认槽位（多选下只用作 monitor 之外的兜底，不再代表「唯一窗口」） */
  slot: { type: Number, default: 1 },
  /** 行标题（默认按模式生成） */
  title: { type: String, default: '' },
  /** 行内短标题（缩进到某个槽位下面时用；弹窗标题仍用完整 label） */
  rowLabel: { type: String, default: '' },
  /** 设置页当前是否可见（切走时把弹窗收掉，避免残留在别的页面上） */
  active: { type: Boolean, default: true },
});

const themes = ref([]);
const busy = ref(false);
const importing = ref(false);
const exporting = ref(false);
const notice = ref('');
const error = ref('');
const pendingDelete = ref('');
const deletingKey = ref('');
const open = ref(false);
const dialogEl = ref(null);

const current = computed(() => themeChoice[themeChoiceKey(props.mode, props.slot)]);
const currentKey = computed(() => current.value.source + ':' + current.value.id);
const label = computed(() => props.title || (props.mode === 'monitor' ? '主面板主题' : '悬浮窗主题'));
const rowText = computed(() => props.rowLabel || label.value);
// 设置页两行都不显示「当前选中的主题名」；currentKey 仍用于主面板的单选高亮 / pick()

// 主面板多一个「内置面板」选项：它不是主题（没有 iframe），但功能最全（含实时赛道图）
function cardList() {
  const list = themesForMode(themes.value, props.mode);
  if (props.mode !== 'monitor') return list;
  return [
    {
      key: `${BUILTIN_PANEL.source}:${BUILTIN_PANEL.id}`,
      id: BUILTIN_PANEL.id,
      source: BUILTIN_PANEL.source,
      name: '内置面板',
      desc: '程序自带的车辆面板，含实时赛道图与弯心提示。主题拿不到地图几何，所以它是功能最全的一个。',
      design: null,
      error: null,
      isPanel: true,
    },
    ...list,
  ];
}
const cards = computed(cardList);
const flat = computed(() => props.mode === 'monitor');
const sidebar = computed(() => props.mode === 'overlay');

const EXTERNAL_ID = '__external';
const BROKEN_ID = '__broken';

/* ------------------------------ 分组 ------------------------------ */
// 主面板：单一无标题分组，直接一张网格列完（内置面板置顶、坏主题沉底）。
// 悬浮窗：每个主题只出现一次 —— **外部主题（source=user）统一归「外部主题」，不再按 category
// 散到各个业务分类**；坏主题归「异常主题」；其余按主分类，顺序固定 THEME_CATEGORIES。
const groups = computed(() => {
  if (flat.value) return [{ id: '__all', label: '', items: cards.value }];
  const byCat = new Map();
  for (const t of cards.value) {
    let key;
    if (t.isPanel) key = '__panel';
    else if (t.error) key = BROKEN_ID;
    else if (t.source === 'user') key = EXTERNAL_ID;
    else key = THEME_CATEGORIES.some((c) => c.id === t.category) ? t.category : UNCATEGORIZED;
    if (!byCat.has(key)) byCat.set(key, []);
    byCat.get(key).push(t);
  }
  const out = [];
  if (byCat.has('__panel')) out.push({ id: '__panel', label: '内置面板', items: byCat.get('__panel') });
  for (const c of THEME_CATEGORIES) {
    const items = byCat.get(c.id);
    if (items?.length) out.push({ id: c.id, label: categoryLabel(c.id), items });
  }
  // 就算一个外部主题都没有，也要留这个分类：导入 / 导出 / 打开文件夹 / 刷新工具都放在它里面
  out.push({
    id: EXTERNAL_ID,
    label: '外部主题',
    items: byCat.get(EXTERNAL_ID) || [],
  });
  if (byCat.has(BROKEN_ID)) {
    out.push({ id: BROKEN_ID, label: '异常主题', items: byCat.get(BROKEN_ID) });
  }
  return out;
});

/* ------------------------------ 第一列：分类导航 ------------------------------ */
// 固定顺序：6 类 + 未分类 → 外部主题 → 内置面板 / 异常主题（后两者只在真有条目时出现）。
// 空分类禁用不产生死点击；**外部主题永远可点**（否则没有外部主题时连导入按钮都找不到）。
const navItems = computed(() => {
  if (!sidebar.value) return [];
  const present = new Map(groups.value.map((g) => [g.id, g]));
  // 「已启用几个」按分类算：isEnabled() 读的是响应式的槽位列表 + 各槽位主题，
  // 启用 / 停用后这里会立刻跟着变，不需要手动刷新。
  const usedOf = (g) => (g?.items || []).filter((t) => isEnabled(t)).length;
  const items = THEME_CATEGORIES.map((c) => {
    const g = present.get(c.id);
    return {
      id: c.id,
      label: c.label,
      count: g?.items.length || 0,
      used: usedOf(g),
      alwaysAvailable: false,
    };
  });
  const ext = present.get(EXTERNAL_ID);
  items.push({
    id: EXTERNAL_ID,
    label: ext?.label || '外部主题',
    count: ext?.items.length || 0,
    used: usedOf(ext),
    alwaysAvailable: true,
  });
  for (const id of ['__panel', BROKEN_ID]) {
    const g = present.get(id);
    if (g) items.push({
      id, label: g.label,
      count: g.items.length, used: usedOf(g), alwaysAvailable: false,
    });
  }
  return items;
});

// 「外部主题」是特殊分类（导入 / 导出工具都在里面），单独钉在侧栏底部；
// 其余业务分类作为一组垂直居中，避免它跟着分类一起悬在中间。
const navMainItems = computed(() => navItems.value.filter((n) => n.id !== EXTERNAL_ID));
const navFootItems = computed(() => navItems.value.filter((n) => n.id === EXTERNAL_ID));

// 用户点过的分类；失效时（主题被删空、导入后重排）自动回落到「第一个已启用主题所在分类」，
// 再没有就落到第一个非空分类，保证第二列 / 右侧永远不会莫名空白。
const selectedGroup = ref('');
const activeGroupId = computed(() => {
  if (!sidebar.value || !navItems.value.length) return '';
  const chosen = navItems.value.find((n) => n.id === selectedGroup.value && (n.count > 0 || n.alwaysAvailable));
  if (chosen) return chosen.id;
  const firstEnabled = cards.value.find((t) => slotOfTheme(t) > 0);
  const curGroup = firstEnabled && groups.value.find((g) => g.items.some((t) => t.key === firstEnabled.key));
  if (curGroup) return curGroup.id;
  return (navItems.value.find((n) => n.count > 0) || navItems.value[0]).id;
});
const activeNav = computed(() => navItems.value.find((n) => n.id === activeGroupId.value) || null);
const activeGroupItems = computed(() => groups.value.find((g) => g.id === activeGroupId.value)?.items || []);
const visibleGroups = computed(() => (sidebar.value ? [] : groups.value));
const isExternalGroup = computed(() => activeGroupId.value === EXTERNAL_ID);
// 工具只在「外部主题」分类里出现；主面板没有分类侧栏，照常全显示
const showTools = computed(() => !sidebar.value || isExternalGroup.value);

function selectGroup(id) {
  selectedGroup.value = id;
  // 右侧跟着分类走：自动选中这一分类里第一个能预览的主题
  const items = groups.value.find((g) => g.id === id)?.items || [];
  selectTheme(items.find((t) => canPreview(t)) || items[0] || null);
}

/* ------------------------------ 多悬浮窗：槽位分配 ------------------------------ */
// 一个主题 = 一个开关：启用时占一个空闲槽位，停用时把该槽位还给池子。
// 上限 = Rust OVERLAY_SLOTS=5 / OVERLAY_SLOT_MAX=5（两边必须一致，改上限要重编译 Tauri）。
// 槽位上限不再写在侧栏里：分类项自己带「共几个 / 已用几个」，见 navItems。

function slotOfTheme(t) {
  if (!t || t.isPanel) return 0;
  const src = t.source || 'builtin';
  for (const slot of overlaySlots.list) {
    const c = themeChoice[themeChoiceKey('overlay', slot)];
    if (c && c.id === t.id && (c.source || 'builtin') === src) return slot;
  }
  return 0;
}

function isEnabled(t) {
  return slotOfTheme(t) > 0;
}

function freeSlot() {
  for (let s = 1; s <= OVERLAY_SLOT_MAX; s += 1) {
    if (!overlaySlots.list.includes(s)) return s;
  }
  return 0;
}

function canUse(t) {
  if (!t || t.error || t.isPanel) return false;
  return isEnabled(t) || !!freeSlot();
}

function useTitle(t) {
  if (t.error) return '主题有问题，无法启用';
  if (isEnabled(t)) return '停用这个悬浮窗';
  if (!freeSlot()) return `最多同时启用 ${OVERLAY_SLOT_MAX} 个悬浮窗，先停用其中一个`;
  return '启用为一个新悬浮窗';
}

async function toggleUse(t) {
  if (!canUse(t)) return;
  error.value = '';
  notice.value = '';
  const slot = slotOfTheme(t);
  if (slot) {
    await setOverlaySlotEnabled(slot, false);
    return;
  }
  const free = freeSlot();
  // 先把主题写进这个槽位再启用窗口，否则新窗口会读到上一个主题 / 默认值
  saveThemeChoice({ id: t.id, source: t.source }, 'overlay', free);
  try {
    await emitEvent('overlay-theme-changed', { slot: free, id: t.id, source: t.source });
  } catch { /* 浏览器预览里没有 Tauri 事件 */ }
  await setOverlaySlotEnabled(free, true);
  // 不弹「已启用 / 已停用」提示：按钮本身变成「停用」、条目出现色条就是反馈，
  // 也避免界面上冒出「悬浮窗 1/2/3」这类槽位编号（用户明确要求不展示）。
}

/* ------------------------------ 选中主题 → 预览 + 设置 ------------------------------ */
// 选中的就是预览的：点左侧条目或「预览」按钮都走这里，右侧预览框与设置面板一起联动。
const selectedKey = ref('');
const previewSeq = ref(0);
const selectedTheme = computed(() => cards.value.find((t) => t.key === selectedKey.value) || null);
const previewTarget = computed(() => {
  const t = selectedTheme.value;
  if (!t || !canPreview(t)) return null;
  return {
    key: t.key,
    id: t.id,
    source: t.source,
    category: THEME_CATEGORIES.some((c) => c.id === t.category) ? t.category : UNCATEGORIZED,
    design: t.design || null,
  };
});
const previewMeta = computed(() => {
  const t = previewTarget.value;
  if (!t) return '';
  // 悬浮窗预览栏右侧不再显示分类名 / 设计尺寸，只留「预览 · 主题名」。
  // 主面板仍是平铺网格，保留原有的尺寸提示 + 说明这是等比缩小的预览。
  if (!flat.value) return '';
  return t.design?.w && t.design?.h ? `${t.design.w}×${t.design.h} · 等比预览` : '自适应';
});

/* ------------------------------ 预览缩放（主面板） ------------------------------ */
// 主面板主题是 fit: fluid 的大面板（内置 gt-cockpit 为 1280×640）。直接把 iframe 铺进
// 几百像素高的预览框，主题会按这个「小视口」重新排版：字号有 9px 下限、间距是固定值，
// 于是数值挤叠、模块重叠 —— 看起来就是「预览压缩了面板、显示异常」。
// 预览里强制 contain：iframe 始终拿到设计尺寸的视口，排版与真机一致，再整体等比缩小居中。
// 运行时（MonitorView / OverlayView）仍走 theme.json 的 fit，这里只影响预览。
const previewFit = computed(() => (flat.value && previewTarget.value?.design?.w ? 'contain' : null));

// 预览框自身也按 design 的宽高比撑开、限高并居中：比例对了不会有上下大片黑边，
// 高度被钳住也不会把下面的主题卡片列表挤成一条。想看细节可以点「放大预览」。
const zoomed = ref(false);
const stageStyle = computed(() => {
  if (!flat.value) return null;
  const d = previewTarget.value?.design;
  const w = d?.w > 0 ? d.w : 1280;
  const h = d?.h > 0 ? d.h : 640;
  const maxH = zoomed.value ? 'min(46vh, 22rem)' : 'min(30vh, 15rem)';
  return {
    aspectRatio: `${w} / ${h}`,
    maxWidth: `calc(${maxH} * ${Number((w / h).toFixed(4))})`,
  };
});

// 右侧设置面板：按 overlay-settings.js 的注册表渲染，作用于「选中主题占用的槽位」。
// 主题还没启用时没有槽位可写，只给一句提示；以后加设置项不用动这里。
const activeSlot = computed(() => slotOfTheme(selectedTheme.value));
const activeSettings = computed(() => settingsForTheme(selectedTheme.value, activeSlot.value));
// 设置面板按「背景 / 以后加的文字」分组渲染；空分组自动隐藏。
const activeSettingGroups = computed(() => settingGroupsForTheme(selectedTheme.value, activeSlot.value));
// 内置主题自己的配置项：schema 来自 theme.json（themes.js 已归一，外部主题恒为空）。
// 值按「槽位 + 主题」存，切主题 / 切槽位互不串；写完后 version 变化让这里立即重算。
const activeThemeSettings = computed(() => {
  const t = selectedTheme.value;
  if (!t || !activeSlot.value || !(t.settings || []).length) return {};
  overlayThemeSettingsVersion.value; // 依赖：同窗口写入后重算
  return resolveThemeSettings(t.settings, readThemeSettings(activeSlot.value, themeSettingsKey(t)));
});
const activeThemeSettingGroups = computed(() => {
  const schema = selectedTheme.value?.settings || [];
  if (!schema.length) return [];
  const values = activeThemeSettings.value;
  const byGroup = new Map();
  for (const def of schema) {
    const label = def.group || '主题';
    if (!byGroup.has(label)) byGroup.set(label, []);
    byGroup.get(label).push({ def, value: values[def.id] });
  }
  return [...byGroup].map(([label, items]) => ({ id: `theme:${label}`, label, items }));
});
// 外部主题没有在 theme.json 里声明 overlaySettings 时，注册表返回空数组：
// 不显示任何控件，只提示「该主题不支持这些外观设置」—— 这就是内置 / 外部的界限。
const settingsUnsupported = computed(() => (
  sidebar.value && !!selectedTheme.value && !!activeSlot.value && activeSettings.value.length === 0
));
function changeSetting(def, value) {
  writeOverlaySetting(def, activeSlot.value, value);
}

// 内置主题自己的配置项：写入「槽位 + 主题」存储，广播后悬浮窗立刻生效。
function changeThemeSetting(def, value) {
  const t = selectedTheme.value;
  if (!t || !activeSlot.value) return;
  writeThemeSetting(activeSlot.value, themeSettingsKey(t), def.id, value);
}

function settingValue(id) {
  return activeSettings.value.find((s) => s.def.id === id)?.value;
}

function sameColor(a, b) {
  return String(a || '').toLowerCase() === String(b || '').toLowerCase();
}

// 预览同步设置项：背景颜色 / 不透明度立刻反映在预览里（值来自响应式的 activeSettings，
// 拖滑杆 / 点色块会更新，不用手动刷新）。主面板不传（留空）。
const previewBackgroundColor = computed(() => (
  sidebar.value && activeSlot.value ? settingValue('backgroundColor') || '' : ''
));
const previewBackgroundOpacity = computed(() => {
  if (!sidebar.value || !activeSlot.value) return 0;
  const v = settingValue('backgroundOpacity');
  return typeof v === 'number' ? v / 100 : 0;
});
// 真机用 Windows Acrylic（强度由系统决定）；预览页没有 OS 窗口，用 16px CSS 模糊近似。
const previewBackgroundBlur = computed(() => {
  if (!sidebar.value || !activeSlot.value) return 0;
  return Number(settingValue('backgroundBlur')) > 0 ? 16 : 0;
});
const previewTextColor = computed(() => (
  sidebar.value && activeSlot.value ? settingValue('textColor') || '#ffffff' : '#ffffff'
));
// 预览同步主题配置：选中主题即可预览，不必先启用；未启用时按默认值渲染。
const previewThemeSettings = computed(() => {
  const t = selectedTheme.value;
  if (!sidebar.value || !t || !(t.settings || []).length) return null;
  overlayThemeSettingsVersion.value; // 依赖：改完配置预览立即跟着变
  const raw = activeSlot.value ? readThemeSettings(activeSlot.value, themeSettingsKey(t)) : {};
  return resolveThemeSettings(t.settings, raw);
});

function canPreview(t) {
  return !!t && !t.error && !t.isPanel;
}

function previewTitle(t) {
  if (t.error) return '主题有问题，无法预览';
  if (t.isPanel) return '内置面板无需预览';
  return previewTarget.value?.key === t.key ? '重新加载预览' : '在右侧预览框里实时查看';
}

function selectTheme(t) {
  if (!t) {
    selectedKey.value = '';
    return;
  }
  selectedKey.value = t.key;
  previewSeq.value += 1; // 再点一次同一个主题也重建 iframe
}

function clearPreview() {
  selectedKey.value = '';
  zoomed.value = false;
}

/** 选中项失效（主题被删 / 改坏 / 切分类）时回落到当前分类第一个主题，右侧不会莫名空白。 */
function ensureSelection() {
  if (!sidebar.value) return;
  if (cards.value.some((t) => t.key === selectedKey.value)) return;
  const items = activeGroupItems.value;
  const inGroup = items.find((t) => canPreview(t)) || items[0];
  if (inGroup) {
    selectTheme(inGroup);
    return;
  }
  selectTheme(cards.value.find((t) => canPreview(t)) || null);
}

async function reload() {
  busy.value = true;
  try {
    themes.value = await listThemes();
    error.value = '';
    ensureSelection();
  } catch (e) {
    error.value = '读取主题失败：' + e.message;
  } finally {
    busy.value = false;
  }
}

/** 主面板仍是单选：换主题只写 monitor 那一个键。悬浮窗多选走 toggleUse()。 */
function pick(t) {
  if (t.error || t.key === currentKey.value) return;
  saveThemeChoice({ id: t.id, source: t.source }, props.mode, props.slot);
}

/* ------------------------------ 选择弹窗 ------------------------------ */
function openDialog() {
  if (open.value) return;
  open.value = true;
  notice.value = '';
  clearPreview();
  reload();
  // 焦点收进弹窗，Esc / 回车不会漏到全局快捷键（1/2/3 切页）
  nextTick(() => dialogEl.value?.focus());
}

function closeDialog() {
  if (deletingKey.value) return;
  open.value = false;
  clearPreview();
}

function onDialogKey(e) {
  e.stopPropagation();
  if (e.key === 'Escape') {
    e.preventDefault();
    closeDialog();
  }
}

/* ------------------------------ 导入主题包（.zip） ------------------------------ */
// 选一个 .zip → Rust 解压进 %LOCALAPPDATA%\acc-telemetry\themes\<id>\。
// 校验（theme.json / index.html / 路径穿越 / 重名）都在 Rust 那边做，失败不会留下半成品。
async function importThemePack() {
  if (importing.value) return;
  notice.value = '';
  error.value = '';
  try {
    const path = await openFileDialog({
      title: '选择主题包',
      multiple: false,
      directory: false,
      filters: [{ name: '主题包', extensions: ['zip'] }],
    });
    if (!path || Array.isArray(path)) return; // 用户取消
    importing.value = true;
    // 带上当前选择器的模式：Rust 会拒绝 modes 不匹配的包（悬浮窗主题导入到主面板等）
    const id = await invoke('import_theme_zip', { path, mode: props.mode });
    await reload();
    selectedGroup.value = EXTERNAL_ID;
    notice.value = `已导入主题「${id}」，在左侧「外部主题」里点「使用」即可启用`;
  } catch (e) {
    error.value = '导入失败：' + (typeof e === 'string' ? e : (e && e.message) || String(e));
  } finally {
    importing.value = false;
  }
}

/* ------------------------------ 导出主题模板 ------------------------------ */
// 把对应槽位的模板安装包另存出来（Rust 里 include_bytes! 内置的两个 zip）。
// 用户拿到的是一个能直接「导入主题包」的 .zip：照着改 theme.json / index.html 就能做新主题。
async function exportTemplate() {
  if (exporting.value) return;
  notice.value = '';
  error.value = '';
  const monitor = props.mode === 'monitor';
  try {
    const path = await saveFileDialog({
      title: '导出主题模板',
      defaultPath: monitor ? 'theme-template-monitor.zip' : 'theme-template.zip',
      filters: [{ name: '主题模板包', extensions: ['zip'] }],
    });
    if (!path) return; // 用户取消
    exporting.value = true;
    await invoke('export_theme_template', { kind: props.mode, path });
    notice.value = `模板已导出：${path}`;
  } catch (e) {
    error.value = '导出失败：' + (typeof e === 'string' ? e : (e && e.message) || String(e));
  } finally {
    exporting.value = false;
  }
}

/* ------------------------------ 目录 / 删除 ------------------------------ */
async function openThemesDir() {
  try {
    await invoke('open_themes_dir');
  } catch (e) {
    error.value = '打开主题文件夹失败：' + e.message;
  }
}

function askDelete(t) {
  if (deletingKey.value) return;
  pendingDelete.value = t.key;
}

function cancelDelete() {
  if (deletingKey.value) return;
  pendingDelete.value = '';
}

async function confirmDelete(t) {
  if (deletingKey.value) return;
  deletingKey.value = t.key;
  try {
    // 只移到回收站，误删还能捞回来
    await invoke('delete_user_theme', { id: t.id });
    pendingDelete.value = '';
    // 删掉的正是某个已启用的悬浮窗 → 那个槽位回退到默认主题，别让窗口一直报错
    if (sidebar.value) {
      const slot = slotOfTheme(t);
      if (slot) {
        saveThemeChoice(DEFAULT_THEME, 'overlay', slot);
        try {
          await emitEvent('overlay-theme-changed', {
            slot, id: DEFAULT_THEME.id, source: DEFAULT_THEME.source,
          });
        } catch { /* 浏览器预览里没有 Tauri 事件 */ }
      }
    } else if (currentKey.value === t.key) {
      pick(DEFAULT_MONITOR_THEME);
    }
    await reload();
  } catch (e) {
    // Tauri 命令返回 Err(String) 时，这里 reject 的是字符串而不是 Error，
    // 直接取 e.message 会变成 "删除失败：undefined"，把真正的原因（比如回收站失败）吞掉。
    error.value = '删除失败：' + (typeof e === 'string' ? e : (e && e.message) || String(e));
  } finally {
    deletingKey.value = '';
  }
}

let offWatch = null;
onMounted(() => {
  reload();
  // 用户往主题文件夹里拷东西时自动刷新，不必手动点「刷新」
  offWatch = onThemesChanged(() => reload());
});
onBeforeUnmount(() => {
  if (offWatch) offWatch();
});
watch(() => props.active, (on) => {
  if (on) {
    // 回到设置页时把弹窗里的列表刷新一遍（中间可能拷了新主题进去）
    if (open.value) reload();
  } else if (open.value) {
    // 弹窗 Teleport 到 body，不会随页面一起隐藏，切页时得自己收掉
    open.value = false;
    clearPreview();
  }
});
</script>

<template>
  <!-- 设置页里只占一行：标题 +「更改」。两种模式都不显示当前选中的主题名
       （悬浮窗是多选没有「当前这一个」，主面板按用户要求也一并隐藏） -->
  <div class="set-row">
    <span>{{ rowText }}</span>
    <div class="tpk-ctl">
      <button class="btn btn-ghost" @click="openDialog">更改</button>
    </div>
  </div>

  <Teleport to="body">
    <div v-if="open" class="dlg-mask" @click.self="closeDialog">
      <div
        ref="dialogEl"
        class="dlg dlg-wide dlg-theme"
        :class="{ 'tpk-split': sidebar, 'tpk-flat': flat }"
        role="dialog"
        aria-modal="true"
        :aria-label="label"
        tabindex="-1"
        @keydown="onDialogKey"
      >
        <button class="dlg-x" type="button" aria-label="关闭" @click="closeDialog">×</button>
        <div class="dlg-head">
          <span class="dlg-ico" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="3.2" y="4.2" width="17.6" height="15.6" rx="1.6" />
              <path d="M3.2 9.2h17.6" />
              <path d="M9.6 9.2v10.6" />
            </svg>
          </span>
          <div class="dlg-ttl">
            <h3>{{ label }}</h3>
          </div>
        </div>

        <div class="tpk-content">
          <!-- 第一列：分类导航（空分类禁用；外部主题永远可点，工具都在它里面） -->
          <nav v-if="sidebar" class="tpk-side" aria-label="主题分类">
            <!-- 业务分类：整组在剩余空间里垂直居中 -->
            <div class="tpk-side-body">
              <div class="tpk-side-head">主题分类</div>
              <button
                v-for="n in navMainItems"
                :key="n.id"
                type="button"
                class="tpk-side-item"
                :class="{ active: n.id === activeGroupId }"
                :disabled="!n.count && !n.alwaysAvailable"
                :title="n.used ? `共 ${n.count} 个主题 · 已启用 ${n.used} 个` : `共 ${n.count} 个主题`"
                @click="selectGroup(n.id)"
              >
                <span class="tpk-side-label">{{ n.label }}</span>
                <!-- 两个数字并排：高亮胶囊 = 这个分类里正在用的个数，淡色数字 = 共几个主题 -->
                <span v-if="n.used" class="tpk-side-used">
                  <i class="tpk-side-dot" aria-hidden="true"></i>{{ n.used }}
                </span>
                <span class="tpk-side-count">{{ n.count }}</span>
              </button>
            </div>

            <!-- 外部主题固定钉在底部，不和业务分类一起居中 -->
            <div v-if="navFootItems.length" class="tpk-side-foot">
              <button
                v-for="n in navFootItems"
                :key="n.id"
                type="button"
                class="tpk-side-item"
                :class="{ active: n.id === activeGroupId }"
                :disabled="!n.count && !n.alwaysAvailable"
                :title="n.used ? `共 ${n.count} 个主题 · 已启用 ${n.used} 个` : `共 ${n.count} 个主题`"
                @click="selectGroup(n.id)"
              >
                <span class="tpk-side-label">{{ n.label }}</span>
                <span v-if="n.used" class="tpk-side-used">
                  <i class="tpk-side-dot" aria-hidden="true"></i>{{ n.used }}
                </span>
                <span class="tpk-side-count">{{ n.count }}</span>
              </button>
            </div>
          </nav>

          <!-- 第二列：该分类下的主题条目，每项自带 预览 / 使用·停用 / 删除 -->
          <div v-if="sidebar" class="tpk-list" aria-label="主题列表">
            <div class="tpk-list-head">
              <span>{{ activeNav?.label }}</span>
              <span class="tpk-list-count">{{ activeGroupItems.length }}</span>
            </div>
            <div
              v-for="t in activeGroupItems"
              :key="t.key"
              class="tpk-item"
              :class="{ active: selectedKey === t.key, enabled: isEnabled(t), bad: !!t.error }"
            >
              <div class="tpk-item-body">
                <button
                  type="button"
                  class="tpk-item-main"
                  :title="t.error || t.desc || ''"
                  @click="selectTheme(t)"
                >
                  <span class="tpk-item-name">{{ t.isPanel ? '内置面板' : t.id }}</span>
                  <!-- 恒显示来源，不显示「300×190」设计尺寸，启用后也不冒出「悬浮窗 1/2/3」。
                       启用状态由 .tpk-item.enabled 的左侧色条 + 「停用」按钮表达。 -->
                  <span class="tpk-item-meta">{{ t.source === 'user' ? '外部' : '内置' }}</span>
                </button>
                <div v-if="t.error" class="tc-err tpk-item-err">{{ t.error }}</div>
              </div>

              <!-- 按钮固定在卡片右侧；删除确认占满整行，避免挤在窄列里 -->
              <div v-if="pendingDelete === t.key" class="tpk-item-actions tpk-item-confirm">
                <span class="tc-ask">移入回收站？</span>
                <button class="btn btn-danger btn-mini" :disabled="deletingKey === t.key" @click="confirmDelete(t)">
                  {{ deletingKey === t.key ? '删除中…' : '删除' }}
                </button>
                <button class="btn btn-ghost btn-mini" :disabled="deletingKey === t.key" @click="cancelDelete">取消</button>
              </div>
              <div v-else class="tpk-item-actions">
                <button
                  class="btn btn-ghost btn-mini"
                  :disabled="!canPreview(t)"
                  :title="previewTitle(t)"
                  @click="selectTheme(t)"
                >{{ previewTarget?.key === t.key ? '预览中' : '预览' }}</button>
                <button
                  class="btn btn-mini"
                  :class="isEnabled(t) ? 'btn-ghost' : 'btn-primary'"
                  :disabled="!canUse(t)"
                  :title="useTitle(t)"
                  @click="toggleUse(t)"
                >{{ isEnabled(t) ? '停用' : '使用' }}</button>
                <button
                  v-if="t.source === 'user' && !t.isPanel"
                  class="btn btn-ghost btn-mini"
                  @click="askDelete(t)"
                >删除</button>
              </div>
            </div>
            <div v-if="!activeGroupItems.length" class="tpk-empty">
              {{ isExternalGroup ? '还没有外部主题，用右侧的「导入主题包」添加' : '该分类下暂时没有主题' }}
            </div>
          </div>

          <!-- 右侧：标题栏 + 固定大尺寸预览框 + 按注册表的设置面板 / 工具 -->
          <div class="tpk-main">
            <div v-if="sidebar" class="tpk-pane-bar">
              <span class="tpk-pane-title">{{ selectedTheme ? (selectedTheme.isPanel ? '内置面板' : selectedTheme.id) : '悬浮窗主题' }}</span>
              <span
                v-if="selectedTheme"
                class="tpk-slot-chip"
                :class="{ on: !!activeSlot }"
              >{{ activeSlot ? '已启用' : '未启用' }}</span>
            </div>

            <!-- 固定尺寸：不随有没有预览而变大变小；点左侧条目 / 预览按钮就在这里实时渲染 -->
            <div class="tpk-preview-pane">
              <div class="tpk-stage-head">
                <span class="tpk-stage-title">{{ previewTarget ? '预览 · ' + previewTarget.id : '主题预览' }}</span>
                <span v-if="previewMeta" class="tpk-stage-meta">{{ previewMeta }}</span>
                <button
                  v-if="previewTarget && flat"
                  class="btn btn-ghost btn-mini"
                  :title="zoomed ? '恢复成小尺寸等比预览' : '按主题设计比例把预览放大，卡片列表会自动让出空间'"
                  @click="zoomed = !zoomed"
                >{{ zoomed ? '缩小预览' : '放大预览' }}</button>
              </div>
              <div class="tpk-stage" :class="{ zoomed }" :style="stageStyle">
                <ThemePreview
                  v-if="previewTarget"
                  :key="previewSeq"
                  :theme="previewTarget.id"
                  :source="previewTarget.source"
                  :mode="mode"
                  :design="previewTarget.design"
                  :fit="previewFit"
                  :background-color="previewBackgroundColor"
                  :background-opacity="previewBackgroundOpacity"
                  :background-blur="previewBackgroundBlur"
                  :text-color="previewTextColor"
                  :settings="previewThemeSettings"
                  :badge="false"
                  live
                />
                <div v-else class="tpk-stage-empty">
                  在左侧选一个主题，这里会用演示数据实时渲染它
                </div>
              </div>
            </div>

            <div class="dlg-body">
              <!-- 不显示「当前使用 X」：设置页两行入口都不展示选中的主题信息，弹窗里也保持一致 -->
              <div v-if="error" class="tc-err tpk-err">{{ error }}</div>
              <div v-if="notice" class="tc-ok tpk-err">{{ notice }}</div>

              <!-- 主面板：保持原来的不分组平铺网格 -->
              <template v-if="!sidebar">
                <div v-for="g in visibleGroups" :key="g.id" class="theme-group">
                  <h4 v-if="g.label" class="tc-group-title">{{ g.label }}</h4>
                  <div class="theme-grid">
                    <div
                      v-for="t in g.items"
                      :key="t.key"
                      class="theme-card"
                      :class="{
                        active: t.key === currentKey,
                        bad: !!t.error,
                        previewing: previewTarget?.key === t.key,
                      }"
                    >
                      <div class="tc-head">
                        <span class="tc-name">{{ t.isPanel ? '内置面板' : t.id }}</span>
                        <span class="tc-badge">{{ t.isPanel ? '内置' : (t.source === 'user' ? '外部' : '内置') }}</span>
                      </div>
                      <div v-if="t.error" class="tc-err">{{ t.error }}</div>
                      <div v-else class="tc-meta">{{ t.design ? t.design.w + '×' + t.design.h : '自适应' }}</div>

                      <div v-if="pendingDelete === t.key" class="tc-actions">
                        <span class="tc-ask">移入回收站？</span>
                        <button class="btn btn-danger" :disabled="deletingKey === t.key" @click="confirmDelete(t)">
                          {{ deletingKey === t.key ? '删除中…' : '删除' }}
                        </button>
                        <button class="btn btn-ghost" :disabled="deletingKey === t.key" @click="cancelDelete">取消</button>
                      </div>
                      <div v-else class="tc-actions">
                        <button
                          class="btn btn-ghost"
                          :disabled="!!t.error || t.key === currentKey"
                          @click="pick(t)"
                        >{{ t.key === currentKey ? '已启用' : '使用' }}</button>
                        <button
                          class="btn btn-ghost"
                          :disabled="!canPreview(t)"
                          :title="previewTitle(t)"
                          @click="selectTheme(t)"
                        >{{ previewTarget?.key === t.key ? '预览中' : '预览' }}</button>
                        <button v-if="t.source === 'user' && !t.isPanel" class="btn btn-ghost" @click="askDelete(t)">删除</button>
                      </div>
                    </div>
                  </div>
                </div>
              </template>

              <!-- 悬浮窗：按 overlay-settings.js 注册表的可扩展设置面板 -->
              <div v-else class="tpk-settings">
                <div class="tpk-settings-head">
                  <span>悬浮窗设置</span>
                </div>
                <div v-if="!selectedTheme" class="tpk-settings-empty">在左侧选一个主题，这里会显示它的预览与设置。</div>
                <div v-else-if="!activeSlot" class="tpk-settings-empty">
                  先点「使用」把它启用成一个悬浮窗，就能在这里设置它。
                </div>
                <!-- 外部主题未声明 overlaySettings：只显示说明，不暴露控件（内置 / 外部的界限） -->
                <div v-else-if="settingsUnsupported" class="tpk-settings-empty">
                  这个外部主题没有在 theme.json 里声明支持悬浮窗外观设置
                  （<code>overlaySettings: ["background", "textColor"]</code>），所以只能沿用主题自带样式，
                  不能调整背景或文字颜色。
                </div>
                <template v-else>
                  <section v-for="g in activeSettingGroups" :key="g.id" class="tpk-setting-group">
                    <div class="tpk-setting-group-head">{{ g.label }}</div>
                    <div v-for="s in g.items" :key="s.def.id" class="tpk-setting">
                    <div class="tpk-setting-label">{{ s.def.label }}</div>

                    <!-- 滑杆：透明度 0~100% -->
                    <template v-if="s.def.type === 'slider'">
                      <input
                        class="tpk-setting-range"
                        type="range"
                        :min="s.def.min"
                        :max="s.def.max"
                        :step="s.def.step || 1"
                        :value="s.value"
                        :aria-label="s.def.label"
                        @input="changeSetting(s.def, Number($event.target.value))"
                      />
                      <span class="tpk-setting-value">{{ s.value }}{{ s.def.unit || '' }}</span>
                      <div v-if="s.def.presets?.length" class="tpk-setting-presets">
                        <button
                          v-for="p in s.def.presets"
                          :key="p.value"
                          type="button"
                          class="btn btn-ghost btn-mini tpk-setting-preset"
                          :class="{ active: Number(s.value) === Number(p.value) }"
                          @click="changeSetting(s.def, p.value)"
                        >{{ p.label }}</button>
                      </div>
                    </template>

                    <!-- 取色器：预设色板 + 系统调色盘 -->
                    <template v-else-if="s.def.type === 'color'">
                      <div v-if="s.def.presets?.length" class="tpk-setting-presets" role="group" :aria-label="s.def.label + '预设'">
                        <button
                          v-for="c in s.def.presets"
                          :key="c"
                          type="button"
                          class="tpk-swatch"
                          :class="{ active: sameColor(s.value, c) }"
                          :style="{ backgroundColor: c }"
                          :title="c"
                          :aria-label="'使用颜色 ' + c"
                          @click="changeSetting(s.def, c)"
                        ></button>
                      </div>
                      <input
                        class="tpk-setting-color"
                        type="color"
                        :value="s.value"
                        :aria-label="s.def.label + '（自定义）'"
                        title="自定义颜色"
                        @input="changeSetting(s.def, $event.target.value)"
                      />
                    </template>

                    <div v-else-if="s.def.type === 'segmented'" class="seg tpk-setting-seg" role="group" :aria-label="s.def.label">
                      <button
                        v-for="o in s.def.options"
                        :key="o.value"
                        :class="{ active: s.value === o.value }"
                        @click="changeSetting(s.def, o.value)"
                      >{{ o.label }}</button>
                    </div>

                    <div v-if="s.def.hint" class="tpk-setting-hint">{{ s.def.hint }}</div>
                    </div>
                  </section>

                  <!-- 内置主题自己的配置项（theme.json 的 settings）。
                       外部主题 schema 恒为空，所以这里不会出现 —— 外部仍只有背景 / 文字色。 -->
                  <section
                    v-for="g in activeThemeSettingGroups"
                    :key="g.id"
                    class="tpk-setting-group tpk-setting-group-theme"
                  >
                    <div class="tpk-setting-group-head">{{ g.label }}</div>
                    <div v-for="s in g.items" :key="s.def.id" class="tpk-setting">
                      <div class="tpk-setting-label">{{ s.def.label }}</div>
                      <div class="seg tpk-setting-seg" role="group" :aria-label="s.def.label">
                        <button
                          v-for="o in s.def.options"
                          :key="o.value"
                          :class="{ active: s.value === o.value }"
                          @click="changeThemeSetting(s.def, o.value)"
                        >{{ o.label }}</button>
                      </div>
                      <div v-if="s.def.hint" class="tpk-setting-hint">{{ s.def.hint }}</div>
                    </div>
                  </section>
                </template>
              </div>

              <!-- 导入 / 导出 / 打开目录 / 刷新：悬浮窗只在「外部主题」分类里出现 -->
              <div v-if="showTools" class="theme-tools">
                <button class="btn btn-ghost" :disabled="importing" @click="importThemePack">
                  {{ importing ? '导入中…' : '导入主题包' }}
                </button>
                <button class="btn btn-ghost" :disabled="exporting" @click="exportTemplate">
                  {{ exporting ? '导出中…' : '导出模板' }}
                </button>
                <button class="btn btn-ghost" @click="openThemesDir">打开主题文件夹</button>
                <button class="btn btn-ghost" :disabled="busy" @click="reload">
                  {{ busy ? '刷新中…' : '刷新' }}
                </button>
                <span class="tc-hint">
                  把主题目录拷进该文件夹就会自动出现在这里（{{ mode === 'monitor' ? 'modes 含 monitor 的主题才能当主面板'
                    : 'modes 含 overlay 的主题才能当悬浮窗' }}）
                </span>
              </div>
            </div>
          </div>
        </div>

        <div class="dlg-foot">
          <button class="btn btn-primary" @click="closeDialog">完成</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
