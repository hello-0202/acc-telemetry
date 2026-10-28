// 主题资源的地址解析。
//
// 内置主题（`public/themes/<id>/`）编译进二进制，走 WebView 自己的资源协议，
// 前端直接用站点相对路径 `/themes/<id>/` 即可，dev 和打包后都一样。
//
// 用户主题放在 `%LOCALAPPDATA%\acc-telemetry\themes\<id>\`，不在站点根目录下，
// 必须走 Rust 注册的 `theme` 自定义协议。注意协议在不同平台的**实际 URL 形式不同**：
//   Windows / Android → http://theme.localhost/<id>/...
//   macOS / Linux     → theme://localhost/<id>/...
// 手拼 `theme://` 在 Windows 上必定加载失败（WebView2 只认 http(s)），所以统一从这里出。

export function isTauriRuntime() {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

/**
 * @param {string} id 主题目录名
 * @param {'builtin'|'user'} source
 * @returns {string} 以 `/` 结尾的基地址
 */
export function themeBaseUrl(id, source = 'builtin') {
  if (source === 'user' && isTauriRuntime()) {
    const isWindows = /Windows/i.test(navigator.userAgent);
    return isWindows
      ? `http://theme.localhost/${id}/`
      : `theme://localhost/${id}/`;
  }
  return `/themes/${id}/`;
}
