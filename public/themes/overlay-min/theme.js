/* 极简 HUD —— 主题侧脚本。
 *
 * 与宿主的约定（postMessage，见 docs/theme-state-contract.md）：
 *   iframe → 宿主：{ type: 'acc:ready' }                  主题加载完成，可以开始喂数据
 *   宿主 → iframe：{ type: 'acc:init',  v, mode, design, scale }
 *   宿主 → iframe：{ type: 'acc:state', payload }         每秒 2~30 次
 *
 * 独立打开时（双击 index.html）没有宿主，2 秒后自动跑演示数据，
 * 方便调样式：不用开游戏、不用起后端。
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var card = $('card');

  /* 安全诊断：Tauri 的 IPC 是 WebView2 的「对所有 frame 注入脚本」实现的，
   * 所以 sandbox iframe 里的主题很可能也能直接 invoke 命令（sandbox 挡不住注入对象）。
   * 期望值 'undefined'；若是 'object' 说明命令只能靠 ACL 按窗口收紧 —— 别删这条日志，
   * 以后换 Tauri 版本 / 改 capabilities 时它就是回归信号。 */
  console.log('[theme] __TAURI_INTERNALS__ =', typeof window.__TAURI_INTERNALS__);

  var framed = false;
  try { framed = !!(window.parent && window.parent !== window); } catch (e) { framed = false; }

  var gotState = false;
  var demoTimer = null;

  function sayReady() {
    // 幂等：宿主每次 init 都回一次 ready（宿主自己会忽略重复），
    // 这样即使首次 ready 比宿主的监听先发出、丢了，也能补上。
    try { window.parent.postMessage({ type: 'acc:ready' }, '*'); } catch (e) { /* ignore */ }
  }

  /* ------------------------------- 格式化 ------------------------------- */

  function fmtLap(ms) {
    if (ms == null || !isFinite(ms) || ms <= 0) return '--:--.---';
    var m = Math.floor(ms / 60000);
    var s = Math.floor((ms % 60000) / 1000);
    var r = Math.floor(ms % 1000);
    return m + ':' + pad(s, 2) + '.' + pad(r, 3);
  }

  function fmtDelta(ms) {
    if (ms == null || !isFinite(ms)) return '--.---';
    return (ms < 0 ? '-' : '+') + (Math.abs(ms) / 1000).toFixed(3);
  }

  function pad(n, width) {
    var s = String(Math.abs(n));
    while (s.length < width) s = '0' + s;
    return s;
  }

  function gearText(gear) {
    if (gear === -1) return 'R';
    if (gear === 0) return 'N';
    if (gear == null) return 'N';
    return String(gear);
  }

  /* ------------------------------- 渲染 ------------------------------- */

  function render(st) {
    card.dataset.status = st.status || 'idle';

    var car = st.car || {};
    var lap = st.lap || {};
    var track = st.track || {};

    $('speed').textContent = Math.round(car.speed || 0);
    $('gear').textContent = gearText(car.gear);
    $('rpm').textContent = car.rpm != null ? Math.round(car.rpm) : 0;
    $('lap').textContent = fmtLap(lap.timeMs);

    var delta = $('delta');
    var d = lap.deltaMs;
    if (d == null || !isFinite(d)) {
      delta.textContent = '--.---';
      delta.className = 'v mono delta none';
    } else {
      delta.textContent = fmtDelta(d);
      delta.className = 'v mono delta ' + (d < 0 ? 'neg' : 'pos');
    }

    $('track').textContent = track.title || track.name || '—';
  }

  /* ------------------------------- 数据来源 ------------------------------- */

  window.addEventListener('message', function (event) {
    var msg = event.data;
    if (!msg || typeof msg !== 'object') return;

    // 宿主主动握手：说明父页面在线。回一条 ready，避免「脚本先跑完、宿主后挂监听」丢消息
    if (msg.type === 'acc:init') {
      sayReady();
      return;
    }

    if (msg.type === 'acc:state' && msg.payload) {
      gotState = true;
      stopDemo();
      render(msg.payload);
    }
  });

  if (framed) {
    // 告诉宿主「我准备好了」；宿主收到后才开始推 state，否则首帧会丢
    sayReady();
    // 有宿主但迟迟没有数据（后端没起来 / 不是通过宿主打开）：兜底演示，别显示一片空
    demoTimer = setTimeout(function () {
      if (!gotState) startDemo();
    }, 2000);
  } else {
    startDemo();
  }

  /* ------------------------------- 演示数据 ------------------------------- */

  var demoTick = null;

  function startDemo() {
    stopDemo();
    var t = 0;
    demoTick = setInterval(function () {
      if (gotState) { stopDemo(); return; }
      t += 1;
      var wave = Math.sin(t / 9);
      var speed = 178 + wave * 74;
      render({
        status: 'streaming',
        car: {
          speed: speed,
          gear: speed > 230 ? 4 : speed > 150 ? 3 : 2,
          rpm: 4200 + wave * 2600,
        },
        lap: {
          timeMs: 41230 + t * 30,
          deltaMs: wave * 640,
        },
        track: { title: 'Monza' },
      });
    }, 60);
  }

  function stopDemo() {
    if (demoTick !== null) {
      clearInterval(demoTick);
      demoTick = null;
    }
    if (demoTimer !== null) {
      clearTimeout(demoTimer);
      demoTimer = null;
    }
  }
})();
