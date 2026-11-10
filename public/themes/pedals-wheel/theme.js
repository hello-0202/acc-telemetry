/* 踏板 + 方向盘 —— 主题侧脚本。
 *
 * 宿主 → 主题：{ type:'acc:init' } / { type:'acc:state', payload }
 * 主题 → 宿主：{ type:'acc:ready' }
 * 独立打开（双击 index.html）没有宿主，2 秒后自动跑演示数据。
 *
 * 踏板条：填充高度 = 开度百分比（从底部往上长）；条上方是纯数字读数（无 %），
 *   颜色跟随红 / 绿语义色 —— 与 pedal-trace 的踏板是同一套写法。
 * 转向弧：pathLength="100" 归一化，270° 整圈 = 75 单位，满舵 37.5 单位 = 135°。
 *   长度不是线性 |steer|，先过 steerVisual() 视觉增益（与 pedal-trace 同一条曲线）：
 *   0~200° 放大到满舵的 82%，200~450° 压进末段 18%。真机很少打满舵，线性映射
 *   会让 180° 只画出窄窄一截颜色。
 * 盘面：用同一个增益后的值旋转环心的 F1 方向盘，盘顶小矩形指针就始终落在弧端。
 *   不按真车 ±450° 转 —— 转过 135° 后指针会跑出环外，指不到颜色。
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
  var stWheel = $('st-wheel');
  var brakeFill = $('brake-fill');
  var thrFill = $('thr-fill');

  // 满舵（steer ±1）对应的弧长与盘转角。两个数必须同源：37.5 单位 × 3.6°/单位 = 135°。
  var ARC_UNITS_FULL = 37.5;
  var WHEEL_DEG_FULL = 135;

  // 视觉增益：与 pedal-trace 的 steerVisual() 一字不差（两套 HUD 读同一个手感的量）。
  //   STEER_DEG_FULL   契约约定归一化 1 = 满舵 450°
  //   STEER_VIS_KNEE   常用弯角上限 200°
  //   STEER_VIS_SHARE  常用区间占满舵幅度的比例：0~200° 吃满 82%
  // 只改显示的弧长 / 盘转角度，不动任何真值（真值只用来算角度）。
  var STEER_DEG_FULL = 450;
  var STEER_VIS_KNEE = 200;
  var STEER_VIS_SHARE = 0.82;
  // ≈9° 死区：增益把 1° 也放大了，真机方向盘微抖会让蓝 / 橙两色来回闪，直行压成 0
  var STEER_DEAD = 0.02;

  function steerVisual(v) {
    var deg = Math.abs(v) * STEER_DEG_FULL;
    if (deg <= STEER_VIS_KNEE) return (deg / STEER_VIS_KNEE) * STEER_VIS_SHARE;
    return STEER_VIS_SHARE
      + ((deg - STEER_VIS_KNEE) / (STEER_DEG_FULL - STEER_VIS_KNEE)) * (1 - STEER_VIS_SHARE);
  }

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
    // 用户在设置里选的文字颜色：标签 / 数值 / 方向盘线稿通过 --text-color 跟随
    if (typeof msg.textColor === 'string') {
      document.documentElement.style.setProperty('--text-color', msg.textColor);
    }
  }

  /* 踏板条填充高度：p 0..1 */
  function setFill(el, p) {
    el.style.height = (p * 100).toFixed(1) + '%';
  }

  /* 转向：s -1..1。先算增益后的幅度 vis（0..1，只表示"画多长"），
   * 弧长 = vis × 37.5 单位，盘转角 = sign × vis × 135° —— 两者同源，
   * 盘顶指针就精确停在彩色弧的末端（改一处必须改另一处，否则指针会脱离弧端）。 */
  function setSteer(s) {
    var vis = steerVisual(s);
    var len = vis * ARC_UNITS_FULL;
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
    var turn = (s < 0 ? -1 : s > 0 ? 1 : 0) * vis * WHEEL_DEG_FULL;
    stWheel.setAttribute('transform', 'rotate(' + turn.toFixed(2) + ' 84 84)');
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
      // 纯数字、无 %：条本身已经在表达百分比，符号是多余的噪声
      $('thr-val').textContent = thr == null ? '--' : String(Math.round(thrP * 100));
      $('brake-val').textContent = brk == null ? '--' : String(Math.round(brkP * 100));

      if (steer == null) {
        setSteer(0);
        return;
      }

      // 约定：steer -1..1，±1 是满舵。先夹紧，再过死区（增益会把 1° 也放大成可见的弧长，
      // 不压死区的话真机微抖会让蓝 / 橙两色在直线上来回闪）
      var sv = steer < -1 ? -1 : steer > 1 ? 1 : steer;
      if (Math.abs(sv) < STEER_DEAD) sv = 0;
      setSteer(sv);
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
