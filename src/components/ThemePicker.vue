<script setup>
// 主题选择器（设置页用）：设置页里只留一行「当前主题名 + 更改」，
// 点开后在弹窗里给完整卡片网格（缩略预览 / 放大预览 / 启用 / 删除）。
//
// 按「槽位」复用：mode="overlay" 是游戏悬浮窗，mode="monitor" 是主面板数据区。
// 同一个组件两处调用，规则只写一份 —— 校验在 themes.js，选择存 theme-choice.js。
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
import { saveThemeChoice, themeChoice, themeChoiceKey } from '../telemetry/theme-choice.js';
import { onThemesChanged } from '../telemetry/theme-watch.js';

const props = defineProps({
  /** overlay | monitor */
  mode: { type: String, required: true },
  /** 悬浮窗槽位（1~3）：每个悬浮窗各自记一个主题 */
  slot: { type: Number, default: 1 },
  /** 行标题（默认按模式 / 槽位生成） */
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
const label = computed(() =>
  props.title || (props.mode === 'monitor' ? '主面板主题' : `悬浮窗 ${props.slot} 主题`)
);
// 缩进在槽位下面时只显示「主题」，弹窗标题仍带「悬浮窗 N」上下文
const rowText = computed(() => props.rowLabel || label.value);
// 统一显示「安装包名」= theme.json 的 id（等于文件夹名）；内置面板不是主题、没有包名，特判成中文
const currentName = computed(() => {
  const hit = cards.value.find((t) => t.key === currentKey.value);
  if (hit) return hit.isPanel ? '内置面板' : hit.id;
  return current.value.id === BUILTIN_PANEL.id ? '内置面板' : current.value.id;
});

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

async function reload() {
  busy.value = true;
  try {
    themes.value = await listThemes();
    error.value = '';
  } catch (e) {
    error.value = '读取主题失败：' + e.message;
  } finally {
    busy.value = false;
  }
}

async function pick(t) {
  if (t.error || t.key === currentKey.value) return;
  saveThemeChoice({ id: t.id, source: t.source }, props.mode, props.slot);
  // 悬浮窗是独立 webview，读完 localStorage 也不会自己知道，必须显式推一条事件过去；
  // 事件带槽位，各悬浮窗只认自己那条
  const eventName = props.mode === 'overlay' ? 'overlay-theme-changed' : null;
  if (eventName) {
    try {
      await emitEvent(eventName, { slot: props.slot, id: t.id, source: t.source });
    } catch { /* 浏览器预览里没有 Tauri 事件 */ }
  }
}

/* ------------------------------ 选择弹窗 ------------------------------ */
function openDialog() {
  if (open.value) return;
  open.value = true;
  notice.value = '';
  reload();
  // 焦点收进弹窗，Esc / 回车不会漏到全局快捷键（1/2/3 切页、T 换黑白）
  nextTick(() => dialogEl.value?.focus());
}

function closeDialog() {
  if (deletingKey.value) return;
  open.value = false;
}

function onDialogKey(e) {
  e.stopPropagation();
  if (e.key === 'Escape') {
    e.preventDefault();
    closeDialog();
  }
}

/* ------------------------------ 放大预览（独立窗口） ------------------------------ */
// 预览不再嵌在主窗口内（那样第三方主题会继承主窗口 ACL，能调 stop_sidecar 等），
// 改成打开一个独立、权限受限的 WebviewWindow（capabilities/preview.json 不含任何 app 命令）。
// 预览按钮的悬停说明：当前正在用的主题、内置面板、以及有问题的主题都不给开预览
function previewTitle(t) {
  if (t.error) return '主题有问题，无法预览';
  if (t.isPanel) return '内置面板无需预览';
  if (t.key === currentKey.value) return '当前正在使用，无需预览';
  return '点击打开预览窗口';
}

async function openThemePreview(t) {
  if (t.error || t.isPanel || t.key === currentKey.value) return;
  try {
    await invoke('open_theme_preview', { id: t.id, source: t.source, mode: props.mode });
  } catch (e) {
    error.value = '打开预览失败：' + e.message;
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
    notice.value = `已导入主题「${id}」，点它卡片上的「使用」即可启用`;
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
    // 删掉的正是正在用的主题 → 回退到默认，否则那一侧会一直空着
    if (currentKey.value === t.key) {
      await pick(props.mode === 'monitor' ? DEFAULT_MONITOR_THEME : DEFAULT_THEME);
    }
    await reload();
  } catch (e) {
    error.value = '删除失败：' + e.message;
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
  }
});
</script>

<template>
  <!-- 设置页里只占一行：当前用的是哪个 + 一个「更改」按钮，点开才弹选择框 -->
  <div class="set-row">
    <span>{{ rowText }}</span>
    <div class="tpk-ctl">
      <span class="v tpk-now" :title="currentName">{{ currentName }}</span>
      <button class="btn btn-ghost" @click="openDialog">更改</button>
    </div>
  </div>

  <Teleport to="body">
    <div v-if="open" class="dlg-mask" @click.self="closeDialog">
      <div
        ref="dialogEl"
        class="dlg dlg-wide dlg-theme"
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

        <div class="dlg-body">
          <p class="tpk-lead">
            当前使用 <strong>{{ currentName }}</strong>。
          </p>
          <div v-if="error" class="tc-err tpk-err">{{ error }}</div>
          <div v-if="notice" class="tc-ok tpk-err">{{ notice }}</div>

          <div class="theme-grid">
            <div
              v-for="t in cards"
              :key="t.key"
              class="theme-card"
              :class="{ active: t.key === currentKey, bad: !!t.error }"
            >
              <button
                type="button"
                class="tc-preview"
                :disabled="!!t.error || t.isPanel || t.key === currentKey"
                :title="previewTitle(t)"
                @click="openThemePreview(t)"
              >
                <span v-if="t.error" class="tc-preview-empty">无法预览</span>
                <span v-else-if="t.isPanel" class="tc-panel-thumb">内置面板</span>
                <span v-else class="tc-thumb">
                  <span class="tc-thumb-dim">{{ t.design ? t.design.w + '×' + t.design.h : '自适应' }}</span>
                  <span class="tc-thumb-hint">{{ t.key === currentKey ? '使用中' : '点击预览' }}</span>
                </span>
              </button>

              <div class="tc-head">
                <span class="tc-name">{{ t.isPanel ? '内置面板' : t.id }}</span>
                <span class="tc-badge">{{ t.isPanel ? '内置' : (t.source === 'user' ? '外部' : '内置') }}</span>
              </div>
              <div v-if="t.error" class="tc-err">{{ t.error }}</div>

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
                <button v-if="t.source === 'user' && !t.isPanel" class="btn btn-ghost" @click="askDelete(t)">删除</button>
              </div>
            </div>
          </div>

          <div class="theme-tools">
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

        <div class="dlg-foot">
          <button class="btn btn-primary" @click="closeDialog">完成</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
