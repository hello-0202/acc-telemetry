<script setup>
// 主题选择器（设置页用）：卡片网格 + 缩略预览 + 放大预览 + 启用/删除。
//
// 按「槽位」复用：mode="overlay" 是游戏悬浮窗，mode="monitor" 是主面板数据区。
// 同一个组件两处调用，规则只写一份 —— 校验在 themes.js，选择存 theme-choice.js。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { invoke } from '@tauri-apps/api/core';
import { emit as emitEvent } from '@tauri-apps/api/event';

import LazyMount from './LazyMount.vue';
import ThemePreview from './ThemePreview.vue';
import {
  BUILTIN_PANEL,
  DEFAULT_MONITOR_THEME,
  DEFAULT_THEME,
  listThemes,
  themesForMode,
} from '../telemetry/themes.js';
import { saveThemeChoice, themeChoice } from '../telemetry/theme-choice.js';
import { onThemesChanged } from '../telemetry/theme-watch.js';

const props = defineProps({
  /** overlay | monitor */
  mode: { type: String, required: true },
  /** 设置页当前是否可见（不可见时不挂 iframe，省资源） */
  active: { type: Boolean, default: true },
});

const themes = ref([]);
const busy = ref(false);
const error = ref('');
const pendingDelete = ref('');
const deletingKey = ref('');

const current = computed(() => themeChoice[props.mode]);
const currentKey = computed(() => current.value.source + ':' + current.value.id);

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
    // 弹窗拿的是列表项的旧引用，列表重建后同步一份，否则「已启用」状态滞后
    if (previewOf.value) {
      const key = previewOf.value.key;
      previewOf.value = cards.value.find((t) => t.key === key) || null;
    }
  } catch (e) {
    error.value = '读取主题失败：' + e.message;
  } finally {
    busy.value = false;
  }
}

async function pick(t) {
  if (t.error || t.key === currentKey.value) return;
  saveThemeChoice({ id: t.id, source: t.source }, props.mode);
  // 悬浮窗是独立 webview，读完 localStorage 也不会自己知道，必须显式推一条事件过去
  const eventName = props.mode === 'overlay' ? 'overlay-theme-changed' : null;
  if (eventName) {
    try {
      await emitEvent(eventName, { id: t.id, source: t.source });
    } catch { /* 浏览器预览里没有 Tauri 事件 */ }
  }
}

/* ------------------------------ 放大预览 ------------------------------ */
const previewOf = ref(null);
const previewCloseBtn = ref(null);

async function openPreview(t) {
  if (t.error || t.isPanel) return;
  previewOf.value = t;
  await nextTick();
  previewCloseBtn.value?.focus();
}

function closePreview() {
  previewOf.value = null;
}

const previewStageStyle = computed(() => {
  const d = previewOf.value?.design;
  return d && d.w > 0 && d.h > 0 ? { aspectRatio: `${d.w} / ${d.h}` } : {};
});

async function usePreviewed() {
  const t = previewOf.value;
  if (!t) return;
  await pick(t);
  closePreview();
}

// 阻止冒泡：否则 1/2/3 会切页、T 会切黑白主题
function onPreviewKey(e) {
  e.stopPropagation();
  if (e.key === 'Escape') {
    e.preventDefault();
    closePreview();
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
  if (on) reload();
  else if (!deletingKey.value) confirmCloseIfAny();
});

// 切走设置页时把弹窗收掉（Teleport 到 body，不会随页面一起隐藏）
function confirmCloseIfAny() {
  previewOf.value = null;
}
</script>

<template>
  <div class="set-group">
    <span class="glbl">{{ mode === 'monitor' ? '主面板主题' : '悬浮窗主题' }}</span>
    <div v-if="error" class="set-row"><span></span><span class="v" style="color:var(--hot)">{{ error }}</span></div>

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
          :class="{ static: t.isPanel }"
          :disabled="!!t.error || t.isPanel"
          :title="t.error ? '主题有问题，无法预览' : (t.isPanel ? '内置面板无需预览' : '点击放大预览')"
          @click="openPreview(t)"
        >
          <LazyMount v-if="!t.error && !t.isPanel && active">
            <ThemePreview :theme="t.id" :source="t.source" :mode="mode" :design="t.design" />
          </LazyMount>
          <span v-else-if="t.error" class="tc-preview-empty">无法预览</span>
          <span v-else class="tc-panel-thumb">内置面板</span>
          <span v-if="!t.error && !t.isPanel" class="tc-zoom" aria-hidden="true">放大</span>
        </button>

        <div class="tc-head">
          <span class="tc-name">{{ t.name }}</span>
          <span class="tc-badge">{{ t.isPanel ? '内置' : (t.source === 'user' ? '用户' : '内置') }}</span>
        </div>
        <div class="tc-meta">
          {{ t.design ? t.design.w + '×' + t.design.h : (t.isPanel ? '自适应铺满' : '尺寸未知') }}<template v-if="t.author"> · {{ t.author }}</template>
        </div>
        <div v-if="t.desc" class="tc-desc">{{ t.desc }}</div>
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
      <button class="btn btn-ghost" @click="openThemesDir">打开主题文件夹</button>
      <button class="btn btn-ghost" :disabled="busy" @click="reload">
        {{ busy ? '刷新中…' : '刷新' }}
      </button>
      <span class="tc-hint">
        把主题目录拷进该文件夹就会自动出现在这里（{{ mode === 'monitor' ? 'modes 含 monitor 的主题才能当主面板'
          : 'modes 含 overlay 的主题才能当悬浮窗' }}）
      </span>
    </div>

    <!-- 放大预览：看完可以直接启用 -->
    <Teleport to="body">
      <div v-if="previewOf" class="dlg-mask" @click.self="closePreview">
        <div
          class="dlg dlg-wide"
          role="dialog"
          aria-modal="true"
          aria-labelledby="pv-title"
          @keydown="onPreviewKey"
        >
          <div class="dlg-head">
            <span class="dlg-ico" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="2.6" y="4" width="18.8" height="12.8" rx="1.6" />
                <path d="M9 20.4h6M12 16.8v3.6" />
              </svg>
            </span>
            <div class="dlg-ttl">
              <h3 id="pv-title">{{ previewOf.name }}</h3>
            </div>
          </div>

          <div class="dlg-body">
            <div class="pv-stage" :style="previewStageStyle">
              <ThemePreview
                :theme="previewOf.id"
                :source="previewOf.source"
                :mode="mode"
                :design="previewOf.design"
                live
                :badge="false"
              />
            </div>

            <div class="pv-meta">
              <span class="pv-chip">{{ previewOf.source === 'user' ? '用户主题' : '内置主题' }}</span>
              <span v-if="previewOf.design" class="pv-chip">{{ previewOf.design.w }}×{{ previewOf.design.h }}</span>
              <span v-if="previewOf.version" class="pv-chip">v{{ previewOf.version }}</span>
              <span v-if="previewOf.author" class="pv-dim">{{ previewOf.author }}</span>
            </div>

            <p v-if="previewOf.desc" class="pv-desc">{{ previewOf.desc }}</p>
            <p class="pv-hint">演示数据渲染，画面与真机数据同名字段一一对应。透明区域在这里显示为斜纹底。</p>
          </div>

          <div class="dlg-foot">
            <button ref="previewCloseBtn" type="button" class="btn btn-ghost" @click="closePreview">关闭</button>
            <button
              type="button"
              class="btn btn-primary"
              :disabled="previewOf.key === currentKey"
              @click="usePreviewed"
            >{{ previewOf.key === currentKey ? '已启用' : '使用此主题' }}</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
