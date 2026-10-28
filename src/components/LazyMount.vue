<script setup>
// 懒挂载：元素进入视口后才渲染 slot。
// 主题预览是活的 iframe，一个主题一个 WebView frame；主题多了（或者设置页藏在
// 后台）没必要一次性全起来，滚到哪儿加载到哪儿。
import { onBeforeUnmount, onMounted, ref } from 'vue';

const props = defineProps({
  /** 提前多少像素开始加载 */
  rootMargin: { type: String, default: '160px 0px' },
});

const el = ref(null);
const shown = ref(false);
let io = null;

function stop() {
  if (io) {
    io.disconnect();
    io = null;
  }
}

onMounted(() => {
  if (!el.value || typeof IntersectionObserver === 'undefined') {
    shown.value = true;
    return;
  }
  io = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        shown.value = true;
        stop();
      }
    },
    { rootMargin: props.rootMargin },
  );
  io.observe(el.value);
});

onBeforeUnmount(stop);
</script>

<template>
  <div ref="el" class="lm-root">
    <slot v-if="shown" />
  </div>
</template>

<style scoped>
.lm-root {
  width: 100%;
  height: 100%;
}
</style>
