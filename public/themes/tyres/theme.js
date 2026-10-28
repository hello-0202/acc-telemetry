/* 轮胎数据 —— 主题侧脚本。
 *
 * 宿主 → 主题：{ type:'acc:init' } / { type:'acc:state', payload }
 * 主题 → 宿主：{ type:'acc:ready' }
 * 独立打开（双击 index.html）没有宿主，2 秒后自动跑演示数据。
 *
 * 圆环用 pathLength="100" 归一化：270° 整圈 = 75 个单位，满圆磨损环 = 100 个单位。
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  // isFinite(null) === true 是个坑：真机 null 字段必须先挡掉
  function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }
  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

  var WHEELS = ['FL', 'FR', 'RL', 'RR'];

  var hud = $('hud');
  var gotState = false;
  var demoTimer = null;
  var demoTick = null;

  function sayReady() {
    try { window.parent.postMessage({ type: 'acc:ready' }, '*'); } catch (e) { /* ignore */ }
  }

  // 宿主告知的悬浮窗背景：transparent | opaque（本主题没底板，靠宿主铺底；写 data-bg 备用）
  function applyUi(msg) {
    if (msg.background === 'transparent' || msg.background === 'opaque') {
      document.documentElement.dataset.bg = msg.background;
    }
  }

  /* 胎温 → 颜色档（40℃ 起算，110℃ 满环） */
  function heatLevel(t) {
    if (t == null) return 'na';
    if (t < 60) return 'cold';
    if (t < 85) return 'ok';
    if (t < 100) return 'warm';
    return 'hot';
  }

  function wearClass(w) {
    if (w == null) return 'w-ok';
    if (w < 0.25) return 'w-ok';
    if (w < 0.55) return 'w-warn';
    return 'w-bad';
  }

  /* corner 可能是 'FL' / 'frontLeft' 等写法，统一归一化 */
  function normCorner(c) {
    if (!c) return null;
    var s = String(c).toLowerCase().replace(/[^a-z]/g, '');
    if (s === 'fl' || s === 'frontleft') return 'FL';
    if (s === 'fr' || s === 'frontright') return 'FR';
    if (s === 'rl' || s === 'rearleft' || s === 'backleft') return 'RL';
    if (s === 'rr' || s === 'rearright' || s === 'backright') return 'RR';
    return null;
  }

  function paint(k, w) {
    var p = num(w && w.pressure);
    var t = num(w && w.temp);
    var b = num(w && w.brakeTemp);
    var we = num(w && w.wear);

    $('psi-' + k).textContent = p == null ? '--' : p.toFixed(1);
    $('temp-' + k).textContent = t == null ? '--°C' : Math.round(t) + '°C';
    $('brake-' + k).textContent = b == null ? 'B --' : 'B ' + Math.round(b) + '°';

    // 胎温 270° 环（dasharray 归零时 round cap 会留一个圆点，所以 0 直接隐藏）
    var arc = $('arc-' + k);
    var tp = t == null ? 0 : clamp01((t - 40) / 70);
    arc.style.strokeDasharray = (75 * tp).toFixed(2) + ' 100';
    arc.style.opacity = tp > 0.002 ? '1' : '0';
    // 档位没变就不要重设 class：每帧 setAttribute 会让样式失效重算（配合过渡会把颜色卡住）
    var ac = 'arc-fg t-' + heatLevel(t);
    if (arc.getAttribute('class') !== ac) arc.setAttribute('class', ac);

    // 磨损细环（满圆）
    var wi = $('wear-' + k);
    var wp = we == null ? 0 : clamp01(we);
    wi.style.strokeDasharray = (wp * 100).toFixed(2) + ' 100';
    wi.style.opacity = wp > 0.002 ? '1' : '0';
    var wc = 'wear-fg ' + wearClass(we);
    if (wi.getAttribute('class') !== wc) wi.setAttribute('class', wc);
  }

  function render(st) {
    try {
      hud.dataset.status = st.status || 'idle';

      // 非 streaming 时后端会保留上一场的最后值（契约 stability），按无数据处理显示占位
      var list = st.status === 'streaming' && Array.isArray(st.wheels) ? st.wheels : [];
      var map = {};

      for (var i = 0; i < list.length; i++) {
        var k = normCorner(list[i] && list[i].corner);
        if (k && !map[k]) map[k] = list[i];
      }

      // 一个 corner 都认不出来时，按数组顺序兜底（契约里顺序是 FL/FR/RL/RR）
      var hit = 0;
      for (var j = 0; j < WHEELS.length; j++) if (map[WHEELS[j]]) hit++;
      if (hit === 0) {
        for (var m = 0; m < WHEELS.length && m < list.length; m++) map[WHEELS[m]] = list[m];
      }

      for (var n = 0; n < WHEELS.length; n++) paint(WHEELS[n], map[WHEELS[n]]);
    } catch (e) {
      console.warn('[tyres] render failed', e);
    }
  }

  window.addEventListener('message', function (ev) {
    var m = ev.data;
    if (!m || typeof m !== 'object') return;
    if (m.type === 'acc:init') { applyUi(m); sayReady(); return; }
    if (m.type === 'acc:ui') { applyUi(m); return; }
    if (m.type === 'acc:state' && m.payload) {
      gotState = true;
      stopDemo();
      render(m.payload);
    }
  });

  var framed = false;
  try { framed = !!(window.parent && window.parent !== window); } catch (e) { framed = false; }

  if (framed) {
    // 有宿主就只等真实 state；后端没起来时保持占位，不跑演示数据
    sayReady();
  } else {
    startDemo();
  }

  /* ------------------------------ 演示数据 ------------------------------ */
  function startDemo() {
    stopDemo();
    var t = 0;
    demoTick = setInterval(function () {
      if (gotState) { stopDemo(); return; }
      t += 1;
      render({
        status: 'streaming',
        wheels: [
          { corner: 'FL', pressure: 27.4, temp: 78 + 8 * Math.sin(t / 9), brakeTemp: 410 + 60 * Math.sin(t / 7), wear: 0.14 },
          { corner: 'FR', pressure: 27.6, temp: 84 + 8 * Math.sin(t / 9 + 1), brakeTemp: 425 + 60 * Math.sin(t / 7 + 1), wear: 0.14 },
          { corner: 'RL', pressure: 27.0, temp: 72 + 8 * Math.sin(t / 9 + 2), brakeTemp: 355 + 60 * Math.sin(t / 7 + 2), wear: 0.31 },
          { corner: 'RR', pressure: 27.2, temp: 76 + 8 * Math.sin(t / 9 + 3), brakeTemp: 362 + 60 * Math.sin(t / 7 + 3), wear: 0.31 },
        ],
      });
    }, 60);
  }

  function stopDemo() {
    if (demoTick !== null) { clearInterval(demoTick); demoTick = null; }
    if (demoTimer !== null) { clearTimeout(demoTimer); demoTimer = null; }
  }
})();
