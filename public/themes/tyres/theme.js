/* 轮胎数据 —— 主题侧脚本。
 *
 * 宿主 → 主题：{ type:'acc:init' } / { type:'acc:state', payload }
 * 主题 → 宿主：{ type:'acc:ready' }
 * 独立打开（双击 index.html）没有宿主，2 秒后自动跑演示数据。
 *
 * 圆环用 pathLength="100" 归一化：270° 弧 = 75 个单位。
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
    // 用户在设置里选的文字颜色：环心读数 / 轮胎角标通过 --text-color 跟随
    if (typeof msg.textColor === 'string') {
      document.documentElement.style.setProperty('--text-color', msg.textColor);
    }
  }

  /* 胎温 → 颜色档（和 gt-cockpit 一致） */
  function heatLevel(t) {
    if (t == null) return 'na';
    if (t < 60) return 'cold';
    if (t < 92) return 'ok';
    if (t < 105) return 'warm';
    return 'hot';
  }

  /* 刹车盘温度 → 颜色档（和 gt-cockpit 一致） */
  function brakeLevel(b) {
    if (b == null) return 'na';
    if (b < 250) return 'cold';
    if (b < 450) return 'ok';
    if (b < 600) return 'warm';
    return 'hot';
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

    $('psi-' + k).textContent = p == null ? '--' : p.toFixed(1);
    $('temp-' + k).textContent = t == null ? '--°C' : Math.round(t) + '°C';
    var brakeEl = $('brake-' + k);
    brakeEl.textContent = b == null ? 'B --' : 'B ' + Math.round(b) + '°';

    // 胎温：整圈常亮，只改颜色
    var tLvl = heatLevel(t);
    var ac = 'arc-fg t-' + tLvl;
    var arc = $('arc-' + k);
    if (arc.getAttribute('class') !== ac) arc.setAttribute('class', ac);

    // 刹车盘温度：270° 弧，颜色随盘温走；文字同步变色
    var bLvl = brakeLevel(b);
    var bc = 'brake-fg b-' + bLvl;
    var barc = $('barc-' + k);
    if (barc.getAttribute('class') !== bc) barc.setAttribute('class', bc);
    if (brakeEl.dataset.level !== bLvl) brakeEl.dataset.level = bLvl;
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
          { corner: 'FL', pressure: 27.4, temp: 78 + 8 * Math.sin(t / 9), brakeTemp: 410 + 60 * Math.sin(t / 7) },
          { corner: 'FR', pressure: 27.6, temp: 84 + 8 * Math.sin(t / 9 + 1), brakeTemp: 425 + 60 * Math.sin(t / 7 + 1) },
          { corner: 'RL', pressure: 27.0, temp: 72 + 8 * Math.sin(t / 9 + 2), brakeTemp: 355 + 60 * Math.sin(t / 7 + 2) },
          { corner: 'RR', pressure: 27.2, temp: 76 + 8 * Math.sin(t / 9 + 3), brakeTemp: 362 + 60 * Math.sin(t / 7 + 3) },
        ],
      });
    }, 60);
  }

  function stopDemo() {
    if (demoTick !== null) { clearInterval(demoTick); demoTick = null; }
    if (demoTimer !== null) { clearTimeout(demoTimer); demoTimer = null; }
  }
})();
