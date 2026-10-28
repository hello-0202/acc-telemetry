// 主题预览窗口入口（独立 WebviewWindow，label: theme-preview）。
//
// 安全边界：这个窗口的 capability 只列 core 事件权限、不含任何 app 命令
// （见 src-tauri/capabilities/preview.json）。第三方主题在这里被加载、渲染，
// 却调不到 stop_sidecar / delete_user_theme 等 —— 即便它尝试 invoke 也会被 ACL 拒绝。
// 这是「主题市场」分发前的硬性安全前置。
//
// 数据用演示帧（mock-state.js），不连后端也能预览；演示数据渲染与真机同名字段一一对应。
import { createApp } from 'vue';

import './style.css';
import PreviewView from './components/PreviewView.vue';

createApp(PreviewView).mount('#app');
