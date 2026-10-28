/* 极简 HUD —— 主题侧脚本。
 *
 * 与宿主的约定（postMessage，见 docs/theme-state-contract.md）：
 *   iframe → 宿主：{ type: 'acc:ready' }                  主题加载完成，可以开始喂数据
 *   宿主 → iframe：{ type: 'acc:init',  v, mode, design, scale, background }
 *   宿主 → iframe：{ type: 'acc:ui',    theme?, background? }  切黑白 / 透明背景时补发
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

  // 宿主告知的界面状态：背景 transparent | opaque。
  // 写到 html[data-bg]，CSS 据此决定底板是半透明还是实心。
  function applyUi(msg) {
    if (msg.background === 'transparent' || msg.background === 'opaque') {
      document.documentElement.dataset.bg = msg.background;
    }
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

    // 契约：非 streaming 时后端仍保留上一场的最后值。主题要自己显示占位，
    // 否则没开游戏 / 已停止监测时，旧的圈速和速度看起来像还在实时采集。
    var live = st.status === 'streaming';
    var car = live ? (st.car || {}) : null;
    var lap = live ? (st.lap || {}) : null;
    var track = live ? (st.track || {}) : null;

    $('speed').textContent = car ? Math.round(car.speed || 0) : '--';
    $('gear').textContent = car ? gearText(car.gear) : '–';
    $('rpm').textContent = car && car.rpm != null ? Math.round(car.rpm) : '--';
    $('lap').textContent = lap ? fmtLap(lap.timeMs) : '--:--.---';

    var delta = $('delta');
    var d = lap ? lap.deltaMs : null;
    if (d == null || !isFinite(d)) {
      delta.textContent = '--.---';
      delta.className = 'v mono delta none';
    } else {
      delta.textContent = fmtDelta(d);
      delta.className = 'v mono delta ' + (d < 0 ? 'neg' : 'pos');
    }

    $('track').textContent = (track && (track.title || track.name)) || '—';
  }

  /* ------------------------------- 数据来源 ------------------------------- */

  window.addEventListener('message', function (event) {
    var msg = event.data;
    if (!msg || typeof msg !== 'object') return;

    // 宿主主动握手：说明父页面在线。回一条 ready，避免「脚本先跑完、宿主后挂监听」丢消息
    if (msg.type === 'acc:init') {
      applyUi(msg);
      sayReady();
      return;
    }

    // 宿主切换黑/白主题、悬浮窗透明/不透明时补发的轻量通知
    if (msg.type === 'acc:ui') {
      applyUi(msg);
      return;
    }

    if (msg.type === 'acc:state' && msg.payload) {
      gotState = true;
      stopDemo();
      render(msg.payload);
    }
  });

  if (framed) {
    // 告诉宿主「我准备好了」；宿主收到后才开始推 state，否则首帧会丢。
    // 有宿主但后端还没起来时保持占位画面 —— 不能跑演示数据，否则没开游戏也会看起来像在实时采集。
    sayReady();
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
