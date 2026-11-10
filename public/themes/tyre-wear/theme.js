/* 轮胎磨损 —— 主题侧脚本。
 *
 * 宿主 → 主题：{ type:'acc:init' } / { type:'acc:state', payload }
 * 主题 → 宿主：{ type:'acc:ready' }
 * 独立打开（双击 index.html）没有宿主，自动跑演示数据。
 *
 * 数据来自 state.wheels[]：
 *   wearEst       估算磨损 0..1（推荐）
 *   wearRatePerLap 上一圈磨损增量
 *   wearLapsLeft  按速率外推的剩余圈数
 *   wearSource    shared / estimated / none
 *   wear          原始 tyreWear（ACC 基本恒 0，只在前两者都缺时兜底）
 */
(function () {
  'use strict';

  var WHEELS = ['FL', 'FR', 'RL', 'RR'];
  var $ = function (id) { return document.getElementById(id); };

  // isFinite(null) === true：真机 null 字段必须先挡掉
  function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }
  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

  var hud = $('hud');
  var gotState = false;
  var demoTick = null;

  function sayReady() {
    try { window.parent.postMessage({ type: 'acc:ready' }, '*'); } catch (e) { /* ignore */ }
  }

  function applyUi(msg) {
    if (msg.background === 'transparent' || msg.background === 'opaque') {
      document.documentElement.dataset.bg = msg.background;
    }
    // 用户选的底板色：不透明时正文底板跟着走，避免和宿主铺的颜色不一致
    if (typeof msg.backgroundColor === 'string') {
      document.documentElement.style.setProperty('--host-bg', msg.backgroundColor);
    }
    // 用户选的文字颜色：标题 / 百分比 / 标签通过 --text-color 跟随
    if (typeof msg.textColor === 'string') {
      document.documentElement.style.setProperty('--text-color', msg.textColor);
    }
  }

  function levelOf(w) {
    if (w == null) return 'ok';
    if (w < 0.30) return 'ok';
    if (w < 0.60) return 'warn';
    return 'bad';
  }

  function normCorner(c) {
    if (!c) return null;
    var s = String(c).toLowerCase().replace(/[^a-z]/g, '');
    if (s === 'fl' || s === 'frontleft') return 'FL';
    if (s === 'fr' || s === 'frontright') return 'FR';
    if (s === 'rl' || s === 'rearleft' || s === 'backleft') return 'RL';
    if (s === 'rr' || s === 'rearright' || s === 'backright') return 'RR';
    return null;
  }

  function paint(key, wheel) {
    var wear = wheel ? num(wheel.wearEst) : null;
    if (wear == null && wheel && wheel.wearSource === 'shared') {
      wear = num(wheel.wear);
    }
    var rate = wheel ? num(wheel.wearRatePerLap) : null;
    var laps = wheel ? num(wheel.wearLapsLeft) : null;

    var pct = $('pct-' + key);
    var bar = $('bar-' + key);
    var rateEl = $('rate-' + key);
    var box = $('c-' + key);

    if (wear == null) {
      pct.textContent = '--';
      bar.style.width = '0';
      rateEl.textContent = '--';
      box.dataset.level = 'ok';
      return { laps: null };
    }

    var w = clamp01(wear);
    pct.textContent = String(Math.round(w * 100));
    bar.style.width = (w * 100).toFixed(1) + '%';
    box.dataset.level = levelOf(w);

    // 每圈损耗显示成百分点：0.021 → +2.1%/圈
    rateEl.textContent = rate == null ? '--' : '+' + (rate * 100).toFixed(1) + '%/圈';
    return { laps: laps };
  }

  function setSource(sources) {
    var badge = $('source');
    var hasShared = false;
    var hasEstimated = false;
    for (var i = 0; i < sources.length; i++) {
      if (sources[i] === 'shared') hasShared = true;
      if (sources[i] === 'estimated') hasEstimated = true;
    }
    var kind = hasShared ? 'shared' : hasEstimated ? 'estimated' : 'none';
    badge.dataset.source = kind;
    badge.textContent = hasShared ? '实测' : hasEstimated ? '估算' : '--';
  }

  function clearAll() {
    for (var i = 0; i < WHEELS.length; i++) paint(WHEELS[i], null);
    $('laps').textContent = '剩余 -- 圈';
    setSource([]);
  }

  function render(st) {
    try {
      var streaming = st && st.status === 'streaming' && Array.isArray(st.wheels);
      hud.dataset.status = (st && st.status) || 'idle';

      if (!streaming) {
        clearAll();
        return;
      }

      var map = {};
      var sources = [];
      var weakest = null;
      for (var i = 0; i < st.wheels.length; i++) {
        var wheel = st.wheels[i];
        var key = normCorner(wheel && wheel.corner);
        if (!key || map[key]) continue;
        map[key] = wheel;
        sources.push(wheel && wheel.wearSource);
      }

      // 一个 corner 都认不出来时按数组顺序兜底（契约顺序 FL/FR/RL/RR）
      if (Object.keys(map).length === 0) {
        for (var j = 0; j < WHEELS.length && j < st.wheels.length; j++) {
          map[WHEELS[j]] = st.wheels[j];
        }
      }

      for (var k = 0; k < WHEELS.length; k++) {
        var out = paint(WHEELS[k], map[WHEELS[k]] || null);
        if (out.laps != null && (weakest === null || out.laps < weakest)) {
          weakest = out.laps;
        }
      }

      setSource(sources);
      $('laps').textContent = weakest == null
        ? '剩余 -- 圈'
        : '剩余 ' + Math.max(0, Math.round(weakest)) + ' 圈';
    } catch (e) {
      console.warn('[tyre-wear] render failed', e);
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
    sayReady();
  } else {
    startDemo();
  }

  /* ------------------------------ 演示数据 ------------------------------ */
  function startDemo() {
    stopDemo();
    var wear = [0.09, 0.11, 0.14, 0.16];
    var rates = [0.019, 0.021, 0.024, 0.026];
    demoTick = setInterval(function () {
      if (gotState) { stopDemo(); return; }
      for (var i = 0; i < 4; i++) {
        wear[i] = Math.min(0.98, wear[i] + rates[i] * 0.01);
        if (wear[i] >= 0.97) wear[i] = 0.05; // 换胎，循环演示
      }
      render({
        status: 'streaming',
        wheels: WHEELS.map(function (k, i) {
          return {
            corner: k,
            wearEst: wear[i],
            wearRatePerLap: rates[i],
            wearLapsLeft: (1 - wear[i]) / rates[i],
            wearSource: 'estimated',
            wear: 0,
          };
        }),
      });
    }, 100);
  }

  function stopDemo() {
    if (demoTick !== null) { clearInterval(demoTick); demoTick = null; }
  }
})();
