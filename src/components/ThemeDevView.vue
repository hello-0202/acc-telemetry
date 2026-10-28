<script setup>
// 主题调试页：把主题放进 iframe，喂「手动可控」的 state，用来调排版 / 配色 / 数值格式。
//
// 和设置页预览的区别：
//   - 设置页预览是给「挑主题」用的，数据自动波动；
//   - 这里是给「写主题」用的，数值由滑杆定格，能看极端值（0 速、6 档、delta ±3s）。
//
// 数据底座直接取契约 `example`（baseMockState），字段名一定和真机一致。
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';

import ThemeHost from './ThemeHost.vue';
import { baseMockState, subscribeMockState } from '../telemetry/mock-state.js';
import { listThemes } from '../telemetry/themes.js';

const themes = ref([]);
const themeKey = ref('');
const auto = ref(false);
const stageBg = ref('game'); // game | black | checker
// 1:1 = 按 theme.json 的 design 原尺寸显示（和悬浮窗里一致，看字号/间距最准）
// fit  = 铺满容器（容器大时会放大，适合远看整体构图）
const zoom = ref('1x');
const showJson = ref(false);
const copyText = ref('复制 JSON');

const stage = ref(null);
const stageSize = ref({ w: 0, h: 0 });

const vals = reactive({
  speed: 208,
  rpm: 5200,
  gear: 4,
  throttle: 0.8,
  brake: 0,
  steer: -0.05,
  deltaMs: -120,
  lapTimeMs: 41230,
  fuel: 42,
  tyreTemp: 82,
  inPit: false,
  valid: true,
});

const state = ref(baseMockState());
let seq = Number(state.value.seq) || 0;
let offWave = null;
let ro = null;

const current = computed(() => themes.value.find((t) => t.key === themeKey.value) || null);
const design = computed(() => current.value?.design || { w: 380, h: 150 });

const scaleText = computed(() => {
  const d = design.value;
  if (zoom.value === '1x') return `${d.w}×${d.h} · 1:1`;
  const { w, h } = stageSize.value;
  if (!w || !h || !d?.w || !d?.h) return '—';
  const k = Math.min(w / d.w, h / d.h);
  return `${d.w}×${d.h} · k=${k.toFixed(2)}`;
});

// 1:1 时把画布钉死成设计尺寸（居中最优先，容器小了就滚动）
const canvasStyle = computed(() =>
  zoom.value === '1x' ? { width: `${design.value.w}px`, height: `${design.value.h}px` } : {},
);

function gearText(g) {
  if (g < 0) return 'R';
  if (g === 0) return 'N';
  return String(g);
}

function rebuild() {
  const s = baseMockState();
  seq += 1;
  s.seq = seq;
  s.t = Date.now();
  s.status = 'streaming';

  s.car = {
    ...s.car,
    speed: Math.round(vals.speed * 10) / 10,
    rpm: Math.round(vals.rpm),
    gear: vals.gear,
    gearText: gearText(vals.gear),
    throttle: vals.throttle,
    brake: vals.brake,
    steer: vals.steer,
    fuel: vals.fuel,
  };

  const best = Number(s.lap.bestMs) || 92000;
  s.lap = {
    ...s.lap,
    timeMs: Math.round(vals.lapTimeMs),
    deltaMs: Math.round(vals.deltaMs),
    progress: Math.min(0.999, vals.lapTimeMs / best),
    estimatedMs: Math.round(best + vals.deltaMs),
    inPit: vals.inPit,
    inPitLane: vals.inPit,
    valid: vals.valid,
  };

  if (Array.isArray(s.wheels)) {
    s.wheels = s.wheels.map((w) => ({ ...w, temp: vals.tyreTemp }));
  }

  state.value = s;
}

/* 场景预设：写主题时最常需要确认的几种极端状态 */
const SCENES = [
  {
    id: 'idle',
    name: '怠速',
    hint: '0 速 / 1 档 / 无油门',
    apply: { speed: 0, rpm: 1100, gear: 1, throttle: 0, brake: 0, steer: 0, deltaMs: 0, tyreTemp: 60, inPit: false },
  },
  {
    id: 'straight',
    name: '直道',
    hint: '全油 / 6 档 / delta 领先',
    apply: { speed: 271, rpm: 7480, gear: 6, throttle: 1, brake: 0, steer: 0.02, deltaMs: -480, tyreTemp: 92 },
  },
  {
    id: 'brake',
    name: '重刹',
    hint: '刹车踩满 / 2 档 / delta 落后',
    apply: { speed: 88, rpm: 5100, gear: 2, throttle: 0.05, brake: 0.96, steer: -0.35, deltaMs: 260, tyreTemp: 104 },
  },
  {
    id: 'mid',
    name: '弯中',
    hint: '半油 / 3 档 / 侧向 G',
    apply: { speed: 146, rpm: 6100, gear: 3, throttle: 0.42, brake: 0, steer: -0.28, deltaMs: 40, tyreTemp: 96 },
  },
  {
    id: 'pit',
    name: '进站',
    hint: '限速 / N 档 / 无限速圈',
    apply: { speed: 0, rpm: 900, gear: 0, throttle: 0, brake: 1, steer: 0, deltaMs: 0, fuel: 4, tyreTemp: 55, inPit: true, valid: false },
  },
];

function applyScene(scene) {
  auto.value = false;
  Object.assign(vals, scene.apply);
}

async function loadThemes() {
  try {
    themes.value = await listThemes();
  } catch (e) {
    console.warn('读取主题失败:', e);
    themes.value = [];
  }
  const first = themes.value.find((t) => !t.error) || themes.value[0];
  if (first) themeKey.value = first.key;
}

async function copyJson() {
  try {
    await navigator.clipboard.writeText(JSON.stringify(state.value, null, 2));
    copyText.value = '已复制';
  } catch {
    copyText.value = '复制失败';
  }
  setTimeout(() => { copyText.value = '复制 JSON'; }, 1400);
}

watch(vals, () => {
  if (!auto.value) rebuild();
}, { deep: true });

// 自动波形：切过去时由 mock-state 的共享定时器驱动，切回来立刻用滑杆值重建一帧
watch(auto, (on) => {
  if (on) {
    offWave = subscribeMockState((s) => { state.value = s; });
  } else {
    if (offWave) { offWave(); offWave = null; }
    rebuild();
  }
});

onMounted(async () => {
  await loadThemes();
  rebuild();
  if (stage.value) {
    ro = new ResizeObserver(() => {
      const r = stage.value.getBoundingClientRect();
      stageSize.value = { w: r.width, h: r.height };
    });
    ro.observe(stage.value);
  }
});

onBeforeUnmount(() => {
  if (offWave) offWave();
  if (ro) ro.disconnect();
});
</script>

<template>
  <div class="dv-root">
    <header class="dv-bar">
      <strong class="dv-title">主题调试</strong>
      <select v-model="themeKey" class="dv-select" aria-label="选择主题">
        <option v-for="t in themes" :key="t.key" :value="t.key" :disabled="!!t.error">
          {{ t.name }}{{ t.source === 'user' ? ' · 用户' : ' · 内置' }}{{ t.error ? ' ✗' : '' }}
        </option>
      </select>
      <span v-if="current?.error" class="dv-err">{{ current.error }}</span>

      <span class="dv-spacer"></span>

      <label class="dv-check"><input v-model="auto" type="checkbox" />自动波形</label>
      <div class="dv-seg" role="group" aria-label="显示比例">
        <button :class="{ on: zoom === '1x' }" title="按 theme.json 的 design 原尺寸显示，和悬浮窗里一致" @click="zoom = '1x'">1:1</button>
        <button :class="{ on: zoom === 'fit' }" title="铺满容器（会放大，看整体构图用）" @click="zoom = 'fit'">适应</button>
      </div>
      <div class="dv-seg" role="group" aria-label="预览底色">
        <button :class="{ on: stageBg === 'game' }" @click="stageBg = 'game'">游戏画面</button>
        <button :class="{ on: stageBg === 'black' }" @click="stageBg = 'black'">纯黑</button>
        <button :class="{ on: stageBg === 'checker' }" @click="stageBg = 'checker'">透明格</button>
      </div>
      <span class="dv-dim">{{ scaleText }}</span>
    </header>

    <div class="dv-body">
      <aside class="dv-panel">
        <div class="dv-group">
          <span class="dv-label">场景</span>
          <div class="dv-scenes">
            <button v-for="s in SCENES" :key="s.id" :title="s.hint" @click="applyScene(s)">{{ s.name }}</button>
          </div>
        </div>

        <div class="dv-group" :class="{ off: auto }">
          <label class="dv-row">
            <span>速度</span>
            <input v-model.number="vals.speed" type="range" min="0" max="330" step="1" :disabled="auto" />
            <b>{{ Math.round(vals.speed) }}</b>
          </label>
          <label class="dv-row">
            <span>转速</span>
            <input v-model.number="vals.rpm" type="range" min="0" max="8600" step="50" :disabled="auto" />
            <b>{{ vals.rpm }}</b>
          </label>
          <label class="dv-row">
            <span>档位</span>
            <input v-model.number="vals.gear" type="range" min="-1" max="6" step="1" :disabled="auto" />
            <b>{{ gearText(vals.gear) }}</b>
          </label>
          <label class="dv-row">
            <span>油门</span>
            <input v-model.number="vals.throttle" type="range" min="0" max="1" step="0.01" :disabled="auto" />
            <b>{{ Math.round(vals.throttle * 100) }}%</b>
          </label>
          <label class="dv-row">
            <span>刹车</span>
            <input v-model.number="vals.brake" type="range" min="0" max="1" step="0.01" :disabled="auto" />
            <b>{{ Math.round(vals.brake * 100) }}%</b>
          </label>
          <label class="dv-row">
            <span>方向</span>
            <input v-model.number="vals.steer" type="range" min="-1" max="1" step="0.01" :disabled="auto" />
            <b>{{ vals.steer.toFixed(2) }}</b>
          </label>
          <label class="dv-row">
            <span>delta</span>
            <input v-model.number="vals.deltaMs" type="range" min="-3000" max="3000" step="10" :disabled="auto" />
            <b>{{ (vals.deltaMs / 1000).toFixed(2) }}s</b>
          </label>
          <label class="dv-row">
            <span>本圈</span>
            <input v-model.number="vals.lapTimeMs" type="range" min="0" max="120000" step="100" :disabled="auto" />
            <b>{{ (vals.lapTimeMs / 1000).toFixed(1) }}s</b>
          </label>
          <label class="dv-row">
            <span>油量</span>
            <input v-model.number="vals.fuel" type="range" min="0" max="120" step="0.5" :disabled="auto" />
            <b>{{ vals.fuel.toFixed(1) }}</b>
          </label>
          <label class="dv-row">
            <span>胎温</span>
            <input v-model.number="vals.tyreTemp" type="range" min="20" max="140" step="1" :disabled="auto" />
            <b>{{ vals.tyreTemp }}°</b>
          </label>
          <div class="dv-flags">
            <label><input v-model="vals.inPit" type="checkbox" :disabled="auto" />进站</label>
            <label><input v-model="vals.valid" type="checkbox" :disabled="auto" />有效圈</label>
          </div>
        </div>
      </aside>

      <main class="dv-main">
        <div ref="stage" class="dv-stage" :class="`bg-${stageBg}`">
          <div class="dv-canvas" :class="{ exact: zoom === '1x' }" :style="canvasStyle">
            <ThemeHost
              v-if="current && !current.error"
              :theme="current.id"
              :source="current.source"
              mode="overlay"
              :state="state"
            />
            <div v-else class="dv-empty">选一个没报错的主题</div>
          </div>
        </div>

        <section class="dv-json">
          <button class="dv-json-toggle" @click="showJson = !showJson">
            {{ showJson ? '▾' : '▸' }} state JSON
          </button>
          <button class="dv-json-copy" @click="copyJson">{{ copyText }}</button>
          <pre v-if="showJson">{{ JSON.stringify(state, null, 2) }}</pre>
        </section>
      </main>
    </div>
  </div>
</template>

<style scoped>
.dv-root {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.dv-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid #1e252e;
  background: #0f141a;
  flex-wrap: wrap;
}

.dv-title {
  font-size: 13px;
  letter-spacing: 0.02em;
}

.dv-select {
  height: 26px;
  min-width: 180px;
  padding: 0 6px;
  border: 1px solid #28313c;
  border-radius: 4px;
  background: #141a21;
  color: #e3e8ee;
  font: inherit;
  font-size: 12px;
}

.dv-err {
  color: #e07a7a;
  font-size: 11px;
}

.dv-spacer {
  flex: 1;
}

.dv-check {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: #aab4c0;
  cursor: pointer;
}

.dv-seg {
  display: inline-flex;
  border: 1px solid #28313c;
  border-radius: 4px;
  overflow: hidden;
}

.dv-seg button {
  padding: 4px 9px;
  border: 0;
  background: transparent;
  color: #94a0ae;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.dv-seg button.on {
  background: #1d2530;
  color: #e9eef4;
}

.dv-dim {
  color: #6d7885;
  font-family: ui-monospace, monospace;
  font-size: 11px;
}

.dv-body {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  flex: 1;
  min-height: 0;
}

.dv-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 12px;
  border-right: 1px solid #1e252e;
  overflow-y: auto;
}

.dv-group {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.dv-group.off {
  opacity: 0.45;
}

.dv-label {
  color: #6d7885;
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.dv-scenes {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 5px;
}

.dv-scenes button {
  padding: 5px 4px;
  border: 1px solid #28313c;
  border-radius: 4px;
  background: #141a21;
  color: #cfd7e0;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.dv-scenes button:hover {
  border-color: #3a4756;
  background: #1a222b;
}

.dv-row {
  display: grid;
  grid-template-columns: 34px 1fr 46px;
  align-items: center;
  gap: 7px;
  font-size: 11px;
  color: #aab4c0;
}

.dv-row b {
  color: #e9eef4;
  font-family: ui-monospace, monospace;
  font-weight: 500;
  text-align: right;
}

.dv-row input[type="range"] {
  width: 100%;
  accent-color: #4a8fe0;
}

.dv-flags {
  display: flex;
  gap: 14px;
  padding-top: 2px;
  font-size: 11px;
  color: #aab4c0;
}

.dv-flags label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
}

.dv-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.dv-stage {
  position: relative;
  display: flex;
  flex: 1;
  min-height: 0;
  margin: 12px;
  border: 1px solid #1e252e;
  border-radius: 6px;
  overflow: auto;
}

/* 适应模式：画布铺满；1:1 模式：画布由内联样式钉成 design 尺寸。
   居中用 margin:auto 而不是 justify-content —— 后者在内容比容器大时，
   左上角会溢到不可滚动的位置。 */
.dv-canvas {
  position: relative;
  width: 100%;
  height: 100%;
  flex: 0 0 auto;
}

.dv-canvas.exact {
  margin: auto;
}

.bg-game {
  background:
    radial-gradient(75% 130% at 50% 0%, rgba(126, 146, 168, 0.2), transparent 62%),
    repeating-linear-gradient(45deg, rgba(150, 162, 177, 0.06) 0 5px, transparent 5px 11px),
    #0d1116;
}

.bg-black {
  background: #000;
}

.bg-checker {
  background-color: #101418;
  background-image:
    linear-gradient(45deg, #161c22 25%, transparent 25%, transparent 75%, #161c22 75%),
    linear-gradient(45deg, #161c22 25%, transparent 25%, transparent 75%, #161c22 75%);
  background-size: 16px 16px;
  background-position: 0 0, 8px 8px;
}

.dv-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #6d7885;
  font-size: 12px;
}

.dv-json {
  position: relative;
  padding: 0 12px 12px;
}

.dv-json-toggle,
.dv-json-copy {
  height: 22px;
  padding: 0 8px;
  border: 1px solid #28313c;
  border-radius: 4px;
  background: #141a21;
  color: #94a0ae;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.dv-json-copy {
  margin-left: 6px;
}

.dv-json pre {
  max-height: 26vh;
  margin: 8px 0 0;
  padding: 10px;
  overflow: auto;
  border: 1px solid #1e252e;
  border-radius: 4px;
  background: #0f141a;
  color: #b9c4d0;
  font-family: ui-monospace, "Cascadia Mono", monospace;
  font-size: 11px;
  line-height: 1.45;
}
</style>
