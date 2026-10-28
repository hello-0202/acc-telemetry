// 主题调试页入口（与主窗口 / 悬浮窗完全独立，不加载应用其余部分）。
//
// 用法：`npm run dev` 后打开 http://localhost:1420/dev.html
// 目的：不开游戏、不开悬浮窗，也能把主题放到真实数据下看排版和交互。
import { createApp } from 'vue';

import ThemeDevView from './components/ThemeDevView.vue';

createApp(ThemeDevView).mount('#app');
