// 游戏悬浮窗入口：独立于主窗口的第二个 WebView。
// 当前只挂一个空白面板（不接收数据）；主题宿主接入后由 OverlayView 内部处理。
import { createApp } from 'vue';

import OverlayView from './components/OverlayView.vue';

createApp(OverlayView).mount('#overlay');
