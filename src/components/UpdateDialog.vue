<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue';
import { openUrl } from '@tauri-apps/plugin-opener';

const props = defineProps({
  checking: { type: Boolean, default: false },
  update: { type: Object, default: null },
  error: { type: String, default: '' },
});

const emit = defineEmits(['close', 'retry']);

const RELEASES_URL = 'https://github.com/hello-0202/acc-telemetry/releases/latest';

// 非模态浮层：捕获阶段监听 Esc，避免被 App 的全局快捷键抢走
function onKey(e) {
  if (e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation();
    emit('close');
  }
}
onMounted(() => window.addEventListener('keydown', onKey, true));
onBeforeUnmount(() => window.removeEventListener('keydown', onKey, true));

const state = computed(() => {
  if (props.checking) return 'checking';
  if (props.update) return 'update';
  if (props.error) return 'error';
  return 'latest';
});

const title = computed(() => ({
  checking: '正在检查更新',
  update: '发现新版本',
  error: '检查失败',
  latest: '已是最新版本',
}[state.value]));

async function openReleases() {
  try {
    await openUrl(RELEASES_URL);
  } catch (e) {
    console.error('open releases page failed:', e);
  }
}
</script>

<template>
  <Teleport to="body">
    <div class="dlg-mask dlg-notify">
      <div class="dlg" role="dialog" aria-labelledby="upd-dlg-title">
        <div class="dlg-head">
          <span class="dlg-ico" :class="{ danger: state === 'error' }" aria-hidden="true">
            <svg v-if="state === 'checking'" class="spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 12a9 9 0 1 1-2.64-6.36" />
              <path d="M21 3v6h-6" />
            </svg>
            <svg v-else-if="state === 'update'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3.2 13.8 8l4.8 1.8-4.8 1.8L12 16.4l-1.8-4.8L5.4 9.8 10.2 8 12 3.2z" />
              <path d="M18.6 15.2l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8.8-2z" />
            </svg>
            <svg v-else-if="state === 'error'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 4.2 3.2 19.8h17.6L12 4.2z" />
              <path d="M12 10.2v3.9" />
              <circle cx="12" cy="17.2" r="1.05" fill="currentColor" stroke="none" />
            </svg>
            <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="9" />
              <path d="m8 12.2 2.8 2.8L16.4 9.4" />
            </svg>
          </span>
          <div class="dlg-ttl">
            <h3 id="upd-dlg-title">{{ title }}</h3>
          </div>
        </div>

        <div class="dlg-body">
          <template v-if="state === 'checking'">
            <p class="dlg-line">正在连接更新服务器，请稍候…</p>
          </template>
          <template v-else-if="state === 'update'">
            <p class="dlg-line">当前版本 <strong>v{{ update.currentVersion }}</strong>，最新版本 <strong>v{{ update.version }}</strong>。</p>
            <div v-if="update.body" class="dlg-notes">{{ update.body }}</div>
            <div class="dlg-keep"><b>说明</b>应用内暂不提供自动安装，请前往 GitHub Releases 下载新版安装包。</div>
          </template>
          <template v-else-if="state === 'error'">
            <p class="dlg-line">{{ error }}</p>
            <div class="dlg-keep"><b>提示</b>GitHub 访问不稳定时可以稍后重试。</div>
          </template>
          <template v-else>
            <p class="dlg-line">当前已是最新版本，无需更新。</p>
          </template>
        </div>

        <div class="dlg-foot">
          <button v-if="state === 'error'" class="btn btn-ghost" @click="emit('retry')">重试</button>
          <button v-if="state === 'update'" class="btn btn-ghost" @click="emit('close')">稍后</button>
          <button v-if="state === 'update'" class="btn btn-primary" @click="openReleases">前往下载</button>
          <button v-else-if="state !== 'checking'" class="btn btn-primary" @click="emit('close')">{{ state === 'error' ? '关闭' : '好的' }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
