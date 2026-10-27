<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { openUrl } from '@tauri-apps/plugin-opener';
import { invoke } from '@tauri-apps/api/core';

const props = defineProps({
  checking: { type: Boolean, default: false },
  update: { type: Object, default: null },
  error: { type: String, default: '' },
});

const emit = defineEmits(['close', 'retry']);

// 发版版本号每次都变，下载页不能用写死的 latest，按检测到的版本拼对应 tag
const REPO_URL = 'https://github.com/hello-0202/acc-telemetry';
const releasesUrl = computed(() => {
  const v = String(props.update?.version || '').replace(/^v/i, '');
  return v ? `${REPO_URL}/releases/tag/v${v}` : `${REPO_URL}/releases/latest`;
});

// 更新流程：下载 → 杀 sidecar → 安装退出，不能直接用 downloadAndInstall。
// downloadAndInstall 下载完会立刻结束进程，RunEvent::Exit 不触发，
// telemetry_core 会残留并占住 8008 端口，导致新版本起不来。
const downloading = ref(false);
const installed = ref(false);
const progress = ref(0);
const hasTotal = ref(false);
const statusText = ref('');
const updateError = ref('');
const cancelled = ref(false);   // 本轮是否已被用户取消
const cancelable = ref(false);  // 只有下载阶段可以取消，进入安装阶段就不能反悔了
let totalBytes = 0;
let receivedBytes = 0;

const MB = 1024 * 1024;
const fmtMB = (n) => (n / MB).toFixed(2);

// 非模态浮层：捕获阶段监听 Esc，避免被 App 的全局快捷键抢走
function onKey(e) {
  if (e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation();
    requestClose();
  }
}
onMounted(() => window.addEventListener('keydown', onKey, true));
onBeforeUnmount(() => window.removeEventListener('keydown', onKey, true));

// 下载阶段关闭 = 取消更新；进入杀后端/安装阶段后关闭只是收起弹窗，安装会继续
function requestClose() {
  if (downloading.value && cancelable.value) {
    cancelUpdate();
    return;
  }
  emit('close');
}

// 取消更新：立即恢复界面，不再杀 sidecar、不再安装。
// 注意 updater 插件没有中断 HTTP 请求的接口，已发出的下载会在后台自然结束、结果被丢弃；
// 重新点击「立即更新」会直接开始新一轮下载（旧请求即使还在也只是白跑，不再阻塞操作）。
function cancelUpdate() {
  if (!cancelable.value) return;
  cancelled.value = true;
  cancelable.value = false;
  downloading.value = false;
  statusText.value = '';
  progress.value = 0;
  hasTotal.value = false;
}

// 重新检查后 update 会换成新实例，清掉上一轮遗留的失败/进度状态
watch(() => props.update, () => {
  if (downloading.value) return;
  installed.value = false;
  cancelled.value = false;
  updateError.value = '';
  statusText.value = '';
  progress.value = 0;
  hasTotal.value = false;
});

const state = computed(() => {
  if (props.checking) return 'checking';
  if (props.update) return 'update';
  if (props.error) return 'error';
  return 'latest';
});

const title = computed(() => {
  if (state.value === 'update' && installed.value) return '更新完成';
  if (state.value === 'update' && downloading.value) return '正在更新';
  return {
    checking: '正在检查更新',
    update: '发现新版本',
    error: '检查失败',
    latest: '已是最新版本',
  }[state.value];
});

// release.yml 里 releaseBody 固定写成这句占位文案，没有实际信息，不展示
const PLACEHOLDER_BODY = /^\s*see the changelog\.md for details\.?\s*$/i;
const notes = computed(() => {
  const body = (props.update?.body || '').trim();
  return PLACEHOLDER_BODY.test(body) ? '' : body;
});

async function openReleases() {
  try {
    await openUrl(releasesUrl.value);
  } catch (e) {
    console.error('open releases page failed:', e);
  }
}

async function startUpdate() {
  if (!props.update || downloading.value) return;
  const update = props.update;
  downloading.value = true;
  cancelable.value = true;
  cancelled.value = false;
  installed.value = false;
  updateError.value = '';
  progress.value = 0;
  hasTotal.value = false;
  totalBytes = 0;
  receivedBytes = 0;
  statusText.value = '正在连接更新服务器…';

  try {
    // 1. 下载安装包，把进度同步到进度条
    await update.download((event) => {
      // 取消后请求仍在后台收尾，忽略后续进度事件，避免覆盖“已取消”提示
      if (cancelled.value) return;
      switch (event.event) {
        case 'Started':
          totalBytes = event.data.contentLength || 0;
          hasTotal.value = totalBytes > 0;
          statusText.value = hasTotal.value
            ? `开始下载…（共 ${fmtMB(totalBytes)} MB）`
            : '开始下载…';
          break;
        case 'Progress':
          receivedBytes += event.data.chunkLength || 0;
          if (hasTotal.value) {
            progress.value = Math.min(99, Math.round((receivedBytes / totalBytes) * 100));
            statusText.value = `正在下载 ${progress.value}%（${fmtMB(receivedBytes)} / ${fmtMB(totalBytes)} MB）`;
          } else {
            statusText.value = `正在下载…（已下载 ${fmtMB(receivedBytes)} MB）`;
          }
          break;
        case 'Finished':
          progress.value = 100;
          statusText.value = '下载完成，正在关闭后台服务…';
          break;
      }
    });

    // 下载结束，过了可取消的窗口
    cancelable.value = false;
    if (cancelled.value) return; // 用户已取消：不杀 sidecar、不安装，丢弃下载结果

    // 2. 先杀 sidecar、释放 8008 端口，再让安装程序接管进程
    await invoke('stop_sidecar');

    // 3. 启动安装程序：Windows 上会直接结束当前进程，安装完自动拉起新版本
    statusText.value = '正在启动安装程序，应用即将退出…';
    await update.install();

    // 只有非 Windows 平台会走到这里，需要手动重启
    downloading.value = false;
    installed.value = true;
    statusText.value = '安装完成，请手动重启应用。';
  } catch (e) {
    downloading.value = false;
    if (!cancelled.value) {
      updateError.value = '更新失败：' + (e?.message || e);
      statusText.value = '';
    }
  } finally {
    cancelable.value = false;
  }
}
</script>

<template>
  <Teleport to="body">
    <div class="dlg-mask dlg-notify">
      <div class="dlg" role="dialog" aria-labelledby="upd-dlg-title">
        <button class="dlg-x" type="button" aria-label="关闭" @click="requestClose">×</button>
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
            <div v-if="notes" class="dlg-notes">{{ notes }}</div>

            <div v-if="downloading || installed" class="upd-prog">
              <div class="upd-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="hasTotal ? progress : undefined">
                <i :class="{ indet: !hasTotal && !installed }" :style="installed ? { width: '100%' } : (hasTotal ? { width: progress + '%' } : null)"></i>
              </div>
              <p class="upd-prog-txt">{{ statusText }}</p>
            </div>

            <div v-if="updateError" class="dlg-warn"><b>失败</b>{{ updateError }}</div>
            <div v-else-if="installed" class="dlg-keep"><b>完成</b>安装已完成，重启应用即可使用新版本。</div>
            <div v-else-if="cancelled && !downloading" class="dlg-keep"><b>已取消</b>本次更新已取消，未安装任何内容，可随时重新开始。</div>
            <div v-else-if="!downloading" class="dlg-keep"><b>说明</b>点击「立即更新」会自动下载并安装；安装程序启动后应用会退出，安装完成后再自动打开。自动更新失败时可改用手动下载。</div>
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
          <template v-if="state === 'update'">
            <template v-if="downloading">
              <button v-if="cancelable" class="btn btn-ghost" @click="cancelUpdate">取消</button>
              <button v-else class="btn btn-ghost" @click="emit('close')">关闭</button>
              <button class="btn btn-primary" disabled>正在更新…</button>
            </template>
            <template v-else>
              <button v-if="!installed" class="btn btn-ghost" @click="emit('close')">稍后</button>
              <button v-if="!installed" class="btn btn-ghost" @click="openReleases">手动下载</button>
              <button v-if="!installed" class="btn btn-primary" @click="startUpdate">立即更新</button>
              <button v-else class="btn btn-primary" @click="emit('close')">好的</button>
            </template>
          </template>
          <template v-else-if="state === 'error'">
            <button class="btn btn-ghost" @click="emit('retry')">重试</button>
            <button class="btn btn-primary" @click="emit('close')">关闭</button>
          </template>
          <button v-else-if="state === 'latest'" class="btn btn-primary" @click="emit('close')">好的</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
