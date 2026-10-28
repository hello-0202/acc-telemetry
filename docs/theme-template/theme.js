/* 最小骨架脚本：接宿主数据 + 无宿主时自己造演示数据。
 *
 * 协议只有三条消息（详见 docs/theme-authoring.md）：
 *   iframe → 宿主：{ type: 'acc:ready' }                       我准备好了，可以喂数据
 *   宿主 → iframe：{ type: 'acc:init',  v, mode, design, scale } 每次 init 都要回一条 ready
 *   宿主 → iframe：{ type: 'acc:state', payload }               数据帧，待机 2Hz / 录制中 30Hz
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  /* ------------------------------ 格式化 ------------------------------ */

  function fmtDelta(ms) {
    if (ms == null || !isFinite(ms)) return '--.---';
    return (ms < 0 ? '-' : '+') + (Math.abs(ms) / 1000).toFixed(3);
  }

  // car.gear 是语义化的：-1 = 倒档，0 = 空档，1..n = 前进档
  function gearText(gear) {
    if (gear === -1) return 'R';
    if (gear === 0 || gear == null) return 'N';
    return String(gear);
  }

  /* ------------------------------- 渲染 ------------------------------- */

  function render(st) {
    var car = st.car || {};
    var lap = st.lap || {};

    $('speed').textContent = Math.round(car.speed || 0);
    $('gear').textContent = gearText(car.gear);

    var delta = $('delta');
    var d = lap.deltaMs;
    if (d == null || !isFinite(d)) {
      delta.textContent = '--.---';
      delta.className = 'delta';
    } else {
      delta.textContent = fmtDelta(d);
      delta.className = 'delta ' + (d < 0 ? 'neg' : 'pos');
    }
  }

  /* ------------------------------ 协议 ------------------------------ */

  var gotState = false;

  function sayReady() {
    // 幂等：宿主每次 init 都回一次 ready。宿主可能漏掉第一帧 ready
    // （主题脚本常比宿主的 message 监听先跑完），所以不要用「只发一次」的开关。
    try {
      window.parent.postMessage({ type: 'acc:ready' }, '*');
    } catch (e) { /* 独立打开时没有 parent */ }
  }

  window.addEventListener('message', function (event) {
    var msg = event.data;
    if (!msg || typeof msg !== 'object') return;

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

  var framed = false;
  try {
    framed = !!(window.parent && window.parent !== window);
  } catch (e) {
    framed = false;
  }

  /* ---------------------------- 离线演示数据 ---------------------------- */

  var demoTick = null;
  var demoTimer = null;

  function startDemo() {
    var t = 0;
    demoTick = setInterval(function () {
      if (gotState) { stopDemo(); return; }
      t += 1;
      var wave = Math.sin(t / 9);
      var speed = 178 + wave * 74;
      render({
        car: { speed: speed, gear: speed > 230 ? 4 : speed > 150 ? 3 : 2 },
        lap: { deltaMs: wave * 640 },
      });
    }, 120);
  }

  function stopDemo() {
    if (demoTick !== null) { clearInterval(demoTick); demoTick = null; }
    if (demoTimer !== null) { clearTimeout(demoTimer); demoTimer = null; }
  }

  if (framed) {
    sayReady();
    // 有宿主但 2 秒还没数据（后端没起来）：先跑演示，别显示一片空
    demoTimer = setTimeout(function () {
      if (!gotState) startDemo();
    }, 2000);
  } else {
    // 双击 index.html 直接打开：纯本地演示，方便调样式
    startDemo();
  }
})();
