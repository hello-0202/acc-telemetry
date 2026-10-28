/* 踏板 + 方向盘 —— 主题侧脚本。
 *
 * 宿主 → 主题：{ type:'acc:init' } / { type:'acc:state', payload }
 * 主题 → 宿主：{ type:'acc:ready' }
 * 独立打开（双击 index.html）没有宿主，2 秒后自动跑演示数据。
 *
 * 踏板条：填充高度 = 开度百分比（从底部往上长）。
 * 转向弧：pathLength="100" 归一化，270° 整圈 = 75 单位，
 *   ±450° 映射到 ±135°，即 37.5 个 dasharray 单位。
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  // isFinite(null) === true 是个坑：真机 null 字段必须先挡掉
  function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }
  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

  var hud = $('hud');
  var stLeft = $('st-left');
  var stRight = $('st-right');
  var brakeFill = $('brake-fill');
  var thrFill = $('thr-fill');

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

  /* 踏板条填充高度：p 0..1 */
  function setFill(el, p) {
    el.style.height = (p * 100).toFixed(1) + '%';
  }

  /* 转向弧：len 是 dasharray 单位，0..37.5（37.5 = 135°） */
  function setSteerArcs(s) {
    var len = Math.abs(s) * 37.5;
    if (s > 0.004) {
      // 右：从正上方顺时针长
      stRight.setAttribute('transform', 'rotate(-90 84 84)');
      stRight.style.strokeDasharray = len.toFixed(2) + ' 100';
      stRight.style.opacity = '1';
      stLeft.style.opacity = '0';
    } else if (s < -0.004) {
      // 左：起点沿弧往回退 len，顺时针画 len 正好收在正上方
      stLeft.setAttribute('transform', 'rotate(' + (-90 - len * 3.6).toFixed(2) + ' 84 84)');
      stLeft.style.strokeDasharray = len.toFixed(2) + ' 100';
      stLeft.style.opacity = '1';
      stRight.style.opacity = '0';
    } else {
      stLeft.style.opacity = '0';
      stRight.style.opacity = '0';
    }
  }

  function render(st) {
    try {
      hud.dataset.status = st.status || 'idle';
      // 非 streaming 时后端会保留上一场的最后值（契约 stability），这里按空值渲染，
      // 避免没开游戏 / 已停止监测时踏板还停在上次的位置。
      var live = st.status === 'streaming';
      var car = live ? (st.car || {}) : {};

      var thr = num(car.throttle);
      var brk = num(car.brake);
      var steer = num(car.steer);

      var thrP = thr == null ? 0 : clamp01(thr);
      var brkP = brk == null ? 0 : clamp01(brk);

      setFill(thrFill, thrP);
      setFill(brakeFill, brkP);
      $('thr-val').textContent = thr == null ? '--' : Math.round(thrP * 100) + '%';
      $('brake-val').textContent = brk == null ? '--' : Math.round(brkP * 100) + '%';

      var degEl = $('steer-deg');

      if (steer == null) {
        setSteerArcs(0);
        degEl.textContent = '--°';
        return;
      }

      // 约定：steer -1..1，乘 450 得度数（本项目约定满舵 ±450°）
      var s = steer < -1 ? -1 : steer > 1 ? 1 : steer;
      var deg = s * 450;

      setSteerArcs(s);
      // 只显角度，不显方向文字（方向看进度弧的颜色：蓝=左、橙=右）
      degEl.textContent = Math.round(Math.abs(deg)) + '°';
    } catch (e) {
      console.warn('[pedals-wheel] render failed', e);
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
      var w = Math.sin(t / 10);
      render({
        status: 'streaming',
        car: {
          throttle: clamp01(0.55 + w * 0.45),
          brake: Math.max(0, 0.5 - w * 0.5),
          steer: Math.sin(t / 23) * 0.7,
        },
      });
    }, 60);
  }

  function stopDemo() {
    if (demoTick !== null) { clearInterval(demoTick); demoTick = null; }
    if (demoTimer !== null) { clearTimeout(demoTimer); demoTimer = null; }
  }

  setFill(brakeFill, 0);
  setFill(thrFill, 0);
})();
