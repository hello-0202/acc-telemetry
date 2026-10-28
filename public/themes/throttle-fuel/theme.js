/* 油量策略 —— 主题侧脚本。
 *
 * 宿主 → 主题：{ type:'acc:init' } / { type:'acc:state', payload }
 * 主题 → 宿主：{ type:'acc:ready' }
 * 独立打开（双击 index.html）没有宿主，2 秒后自动跑演示数据。
 *
 * 四行都是「标签贴左 / 数值贴右」：
 *   FUEL           剩余油量            car.fuel
 *   LAPS LEFT      还能跑多少圈        car.fuelLapsLeft（缺失则 fuel / fuelPerLap）
 *   PER LAP        平均每圈油耗        car.fuelPerLap
 *   SUGGESTED ADD  建议加注量（算出来） 见 suggestAdd()
 *
 * 建议加油量的口径（纯前端算，不依赖后端新增字段）：
 *   需要跑完的圈数 × 每圈油耗 × (1 + 安全余量) − 当前油量
 *   圈数来源二选一：
 *     · 计时赛：session.timeLeftMs / 平均圈时   ← 这就是「当局还剩多少时间」
 *     · 圈数赛：session.lapsTotal − lap.completed
 *   平均圈时优先用上一圈 lap.lastMs（和游戏 fuelPerLap 的口径最接近），
 *   没有就退到 lap.bestMs，再退到 lap.estimatedMs。
 *   两者都拿不到（练习/无圈速）→ 显示 `--`，不瞎猜。
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  // isFinite(null) === true 是个坑：真机 null 字段必须先挡掉
  function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }
  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

  // 安全余量：多备 3%，抵轮胎退化 / 进站那圈的额外消耗
  var FUEL_MARGIN = 1.03;

  var hud = $('hud');
  var fuelEl = $('fuel-val');
  var lapsRow = $('laps-row');
  var lapsEl = $('laps-val');
  var perEl = $('per-val');
  var addRow = $('add-row');
  var addEl = $('add-val');

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

  /* 平均圈时（ms）：上一圈 > 本场最佳 > 游戏估算 */
  function avgLapMs(lap) {
    var l = num(lap.lastMs);
    if (l != null && l > 0) return l;
    var b = num(lap.bestMs);
    if (b != null && b > 0) return b;
    var e = num(lap.estimatedMs);
    if (e != null && e > 0) return e;
    return null;
  }

  /* 还没跑完的圈数：计时赛按剩余时间折算，圈数赛按总圈数差 */
  function lapsToGo(st) {
    var s = st.session || {};
    var lap = st.lap || {};

    var tLeft = num(s.timeLeftMs);
    if (tLeft != null && tLeft > 0) {
      var avg = avgLapMs(lap);
      if (avg == null) return null; // 有剩余时间但还没跑出过圈速
      return tLeft / avg;
    }

    var total = num(s.lapsTotal);
    if (total != null && total > 0) {
      var done = num(lap.completed);
      var left = total - (done == null ? 0 : done);
      return left > 0 ? left : 0;
    }

    return null; // 练习/自由跑：没有终点，也就没有「该加多少」
  }

  /* 建议加油量（L）：>0 表示还要补这么多；<=0 表示油够 */
  function suggestAdd(st, fuel, per) {
    if (fuel == null || per == null || per <= 0) return null;
    var toGo = lapsToGo(st);
    if (toGo == null) return null;
    return toGo * per * FUEL_MARGIN - fuel;
  }

  function render(st) {
    try {
      hud.dataset.status = st.status || 'idle';
      // 非 streaming 时后端会保留上一场的最后值（契约 stability），按空值渲染显示占位
      var live = st.status === 'streaming';
      var car = live ? (st.car || {}) : {};

      /* ---------- FUEL ---------- */
      var fuel = num(car.fuel);
      fuelEl.textContent = fuel == null ? '--' : fuel.toFixed(1);

      /* ---------- LAPS LEFT ---------- */
      var per = num(car.fuelPerLap);
      var laps = num(car.fuelLapsLeft);
      // 主字段缺失时用 油量 / 每圈油耗 兜底
      if (laps == null && fuel != null && per != null && per > 0) laps = fuel / per;

      if (laps == null) {
        lapsEl.textContent = '--';
        lapsRow.dataset.level = '';
      } else {
        var lp = laps < 0 ? 0 : laps;
        lapsEl.textContent = lp >= 100 ? String(Math.round(lp)) : lp.toFixed(1);
        lapsRow.dataset.level = lp < 2 ? 'low' : lp < 5 ? 'warn' : '';
      }

      /* ---------- PER LAP ---------- */
      perEl.textContent = per == null || per <= 0 ? '--' : per.toFixed(2);

      /* ---------- SUGGESTED ADD ---------- */
      var add = suggestAdd(st, fuel, per);
      if (add == null) {
        addEl.textContent = '--';
        addRow.dataset.level = '';
      } else if (add > 0.05) {
        addEl.textContent = '+' + add.toFixed(1);
        addRow.dataset.level = 'need';
      } else {
        // 油够：显示 0.0 而不是负数，避免误读成「要抽油」
        addEl.textContent = '0.0';
        addRow.dataset.level = 'ok';
      }
    } catch (e) {
      console.warn('[throttle-fuel] render failed', e);
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
    var fuel0 = 68.0;
    demoTick = setInterval(function () {
      if (gotState) { stopDemo(); return; }
      t += 1;
      var per = 2.85;
      var fuel = fuel0 - t * 0.012; // 慢慢烧
      if (fuel < 20) { fuel0 = 68.0; t = 0; fuel = fuel0; }
      render({
        status: 'streaming',
        session: { type: 'race', timeLeftMs: 2700000 - t * 400, lapsTotal: 0 },
        lap: { lastMs: 105400, completed: 12 },
        car: { fuel: fuel, fuelPerLap: per, fuelLapsLeft: fuel / per },
      });
    }, 60);
  }

  function stopDemo() {
    if (demoTick !== null) { clearInterval(demoTick); demoTick = null; }
    if (demoTimer !== null) { clearTimeout(demoTimer); demoTimer = null; }
  }
})();
