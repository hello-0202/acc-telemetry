/* speed-delta（速度 / 挡位 / 转速环 / 差值 / 圈速）—— 主题侧脚本。
 *
 * 与宿主约定（postMessage，见 docs/theme-state-contract.md）：
 *   iframe → 宿主：{ type: 'acc:ready' }
 *   宿主 → iframe：{ type: 'acc:init', v, mode, design, scale, background, backgroundColor, backgroundOpacity, textColor }
 *   宿主 → iframe：{ type: 'acc:ui',    theme?, background?, backgroundColor?, backgroundOpacity?, textColor? }
 *   宿主 → iframe：{ type: 'acc:state', payload }          录制中 30Hz / 待机 2Hz
 *
 * 独立打开（双击 index.html）没有宿主时跑演示数据，方便调样式。
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var hud = $('hud');
  var gearEl = $('gearText');
  var rpmEl = $('rpmText');
  var speedEl = $('speed');
  var deltaEl = $('delta');
  var deltaBox = $('deltaBox');
  var tCurEl = $('tCur');
  var tBestEl = $('tBest');
  var tLastEl = $('tLast');
  var maskArc = $('maskArc');
  var glowEl = $('dialGlow');
  var ringSegs = $('ringSegs');

  /* isFinite(null) === true 是个坑：真机 null 字段必须先挡掉 */
  function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }

  // 主色 hex：宿主通过 textColor 注入到 --text-color；这里另存一份给渐变计算用
  var accentHex = '#7db6c7';
  var gradDir = 'deep'; // 'deep' = 越转越重（更饱和）；'bright' = 越转越亮
  var glowOn = true;

  /* ---------- 颜色：越转越「重」= 提高饱和度、略压明度，色相纯净 ----------
     绝不混黑 / 混白（那会降饱和、发灰），主色仍由 textColor 唯一决定。 */
  function hexToRgb(hex) {
    var h = String(hex).replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16) };
  }
  function css(c, a) { return 'rgba(' + c.r + ',' + c.g + ',' + c.b + ',' + (a == null ? 1 : a) + ')'; }
  function clamp01(v) { return Math.max(0, Math.min(1, v)); }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function rgbToHsl(c) {
    var r = c.r / 255, g = c.g / 255, b = c.b / 255;
    var max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    var h = 0, s = 0, l = (max + min) / 2;
    if (d) {
      s = l > .5 ? d / (2 - max - min) : d / (max + min);
      if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h /= 6;
    }
    return { h: h, s: s, l: l };
  }
  function hslToRgb(h, s, l) {
    function hue(p, q, t) {
      if (t < 0) t += 1; if (t > 1) t -= 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    }
    var r, g, b;
    if (s === 0) { r = g = b = l; }
    else {
      var q = l < .5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
      r = hue(p, q, h + 1 / 3); g = hue(p, q, h); b = hue(p, q, h - 1 / 3);
    }
    return { r: Math.round(r * 255), g: Math.round(g * 255), b: Math.round(b * 255) };
  }
  /* 沿弧第 t 段：deep = 淡 → 浓重（更饱和），bright = 淡暗 → 明亮 */
  function segColor(t) {
    var hsl = rgbToHsl(hexToRgb(accentHex));
    var sLight = hsl.s * .45, sHeavy = clamp01(hsl.s * 1.45 + .18);
    var l0, l1;
    if (gradDir === 'deep') {
      l0 = clamp01(hsl.l + .22);
      l1 = clamp01(hsl.l - .18); // 明度压下去、饱和度拉满 => 深而浓重，不是灰
    } else {
      l0 = clamp01(hsl.l - .16);
      l1 = clamp01(hsl.l + .18);
    }
    return css(hslToRgb(hsl.h, lerp(sLight, sHeavy, t), lerp(l0, l1, t)));
  }
  var SEG_N = 30, SEG_LEN = 1000 / SEG_N, segEls = [];
  function paintSegments() {
    for (var i = 0; i < segEls.length; i++) {
      segEls[i].setAttribute('stroke', segColor(i / (SEG_N - 1)));
    }
  }
  function paintGlow(ratio) {
    if (!glowOn) { glowEl.style.background = 'none'; return; }
    var hsl = rgbToHsl(hexToRgb(accentHex));
    var l = gradDir === 'deep' ? clamp01(hsl.l - .16) : clamp01(hsl.l + .10);
    var heavy = hslToRgb(hsl.h, clamp01(hsl.s * 1.35 + .14), l);
    var a = .05 + ratio * .25;
    glowEl.style.background = 'radial-gradient(circle at 50% 50%, ' +
      css(heavy, a) + ' 0%, ' + css(heavy, a * .55) + ' 42%, ' + css(heavy, 0) + ' 72%)';
  }

  /* 预生成 30 段渐变带：每段画整条弧，靠 dashoffset 只露一小段，0.8 重叠消掉 butt 接缝 */
  (function buildSegments() {
    var ns = 'http://www.w3.org/2000/svg';
    var ARC_D = 'M 44.95 175.05 A 92 92 0 1 1 175.05 175.05';
    for (var i = 0; i < SEG_N; i++) {
      var p = document.createElementNS(ns, 'path');
      p.setAttribute('d', ARC_D);
      p.setAttribute('class', 'ring-seg');
      p.setAttribute('pathLength', '1000');
      p.setAttribute('stroke-width', '11');
      p.setAttribute('stroke-dasharray', (SEG_LEN + .8).toFixed(3) + ' ' + (1000 - SEG_LEN).toFixed(3));
      p.setAttribute('stroke-dashoffset', (-i * SEG_LEN).toFixed(3));
      ringSegs.appendChild(p);
      segEls.push(p);
    }
    paintSegments();
  })();

  /* ------------------------------- 格式化 ------------------------------- */
  function fmtLap(ms) {
    if (ms == null || !isFinite(ms) || ms <= 0) return '--:--.---';
    var m = Math.floor(ms / 60000);
    var s = Math.floor((ms % 60000) / 1000);
    var f = Math.floor(ms % 1000);
    return m + ':' + String(s).padStart(2, '0') + '.' + String(f).padStart(3, '0');
  }
  function fmtDelta(ms) {
    if (ms == null || !isFinite(ms)) return '—';
    var v = Math.abs(ms) / 1000;
    return (ms < 0 ? '−' : '+') + v.toFixed(3); // − = 更快
  }
  function gearText(gear) {
    if (gear == null) return '–';
    if (gear === -1) return 'R';
    if (gear === 0) return 'N';
    return String(gear);
  }

  /* ------------------------------- 换挡提示 ------------------------------- */
  var shiftOn = false;
  function setShift(on) {
    if (on === shiftOn) return;
    shiftOn = on;
    hud.dataset.shift = on ? '1' : '0';
  }
  // 允许转速：优先 maxRpmCurrent，缺失或为 0 退回 maxRpm（契约写明 fallback）
  function maxRpmOf(car) {
    if (!car) return null;
    var max = num(car.maxRpmCurrent);
    if (max == null || max <= 0) max = num(car.maxRpm);
    return max != null && max > 0 ? max : null;
  }
  function needShift(car) {
    if (!car) return false;
    var rpm = num(car.rpm);
    var max = maxRpmOf(car);
    var g = num(car.gear);
    if (rpm == null || max == null || g == null || g < 1) return false;
    return rpm / max >= 0.96;
  }

  /* ------------------------------- 渲染 ------------------------------- */
  var lastGear = null, lastRpm = null;
  function render(st) {
    // 契约：非 streaming 时后端仍保留上一场最后值，主题要显示占位，否则没开游戏也像在采集
    var live = st.status === 'streaming';
    var car = live ? (st.car || {}) : null;
    var lap = live ? (st.lap || {}) : null;

    var rpm = num(car ? car.rpm : null);
    var max = maxRpmOf(car);
    var ratio = 0;
    if (rpm != null && max != null) {
      ratio = rpm / max;
      if (ratio < 0) ratio = 0;
      if (ratio > 1) ratio = 1;
    }
    // 只改遮罩长度：露出的渐变段越多、越靠深色端，圆内光晕也同步加深
    maskArc.setAttribute('stroke-dasharray', (ratio * 1000).toFixed(1) + ' 1000');
    paintGlow(ratio);

    var g = num(car ? car.gear : null);
    if (g !== lastGear) {
      lastGear = g;
      lastRpm = null; // 强制重画转速（与挡位无关，但简化逻辑）
      gearEl.textContent = gearText(g);
    }
    var rTxt = rpm == null ? '----' : String(Math.round(rpm));
    if (rTxt !== lastRpm) {
      lastRpm = rTxt;
      rpmEl.textContent = rTxt;
    }

    var speed = num(car ? car.speed : null);
    speedEl.innerHTML = (speed == null ? '--' : String(Math.round(speed))) + '<span class="u">KM/H</span>';

    var d = num(lap ? lap.deltaBestMs : null);
    if (d == null || !isFinite(d)) {
      deltaEl.textContent = '—';
    } else {
      deltaEl.textContent = fmtDelta(d);
      deltaBox.dataset.sign = d >= 0 ? 'pos' : 'neg'; // 负 = 更快 → 绿
    }

    tCurEl.textContent = fmtLap(num(lap ? lap.timeMs : null));
    tBestEl.textContent = fmtLap(num(lap ? lap.bestMs : null));
    tLastEl.textContent = fmtLap(num(lap ? lap.lastMs : null));

    setShift(live && needShift(car));
  }

  /* ------------------------------- 协议 ------------------------------- */
  var gotState = false;
  function sayReady() {
    // 幂等：宿主每次 init 都回一条 ready（宿主自己会忽略重复）
    try { window.parent.postMessage({ type: 'acc:ready' }, '*'); } catch (e) { /* 独立打开时无 parent */ }
  }
  // 宿主告知的界面状态：文字颜色（主数字 / 标签通过 --text-color 跟随）、背景等
  function applyUi(msg) {
    if (msg.theme) document.documentElement.dataset.theme = msg.theme;
    if (msg.background === 'transparent' || msg.background === 'opaque') {
      document.documentElement.dataset.bg = msg.background;
    }
    if (typeof msg.backgroundColor === 'string') {
      document.documentElement.style.setProperty('--host-bg', msg.backgroundColor);
    }
    if (typeof msg.backgroundOpacity === 'number') {
      document.documentElement.style.setProperty('--host-bg-opacity', String(msg.backgroundOpacity));
    }
    if (typeof msg.textColor === 'string') {
      accentHex = msg.textColor;
      document.documentElement.style.setProperty('--text-color', msg.textColor);
      paintSegments();
      // 重新铺一遍当前光晕（颜色变了）
      var cur = parseFloat((maskArc.getAttribute('stroke-dasharray') || '0 1000').split(' ')[0]) || 0;
      paintGlow(cur / 1000);
    }
  }

  window.addEventListener('message', function (event) {
    var msg = event.data;
    if (!msg || typeof msg !== 'object') return;
    if (msg.type === 'acc:init') { applyUi(msg); sayReady(); return; }
    if (msg.type === 'acc:ui') { applyUi(msg); return; }
    if (msg.type === 'acc:state' && msg.payload) {
      gotState = true;
      stopDemo();
      try { render(msg.payload); } catch (err) { console.warn('render failed', err); }
    }
  });

  var framed = false;
  try { framed = !!(window.parent && window.parent !== window); } catch (e) { framed = false; }

  /* ------------------------------- 演示数据（无宿主时） ------------------------------- */
  var demoTick = null;
  var MAX_RPM = 7600, MIN_RPM = 2800;
  function startDemo() {
    stopDemo();
    var t = 0;
    demoTick = setInterval(function () {
      if (gotState) { stopDemo(); return; }
      t += 1;
      var cycle = t % 150;
      var ramp = Math.min(1, cycle / 118);
      var rpm = MIN_RPM + (MAX_RPM * 0.995 - MIN_RPM) * Math.pow(ramp, 0.72);
      if (cycle >= 122) rpm = MIN_RPM + 900; // 换挡后掉转速
      var gear = 3 + Math.floor(t / 150) % 3;
      var speed = 118 + ramp * 148 - (cycle >= 122 ? 34 : 0);
      var delta = -180 + Math.sin(t / 13) * 480;
      render({
        status: 'streaming',
        car: { speed: speed, gear: gear, rpm: rpm, maxRpmCurrent: MAX_RPM },
        lap: { deltaBestMs: delta, timeMs: 42000 + cycle * 620, bestMs: 138432, lastMs: 139106 },
      });
    }, 60);
  }
  function stopDemo() {
    if (demoTick !== null) { clearInterval(demoTick); demoTick = null; }
  }

  if (framed) {
    // 有宿主就只等真实 state；后端没起来时保持占位，不跑演示数据
    sayReady();
  } else {
    // 双击 index.html 直接打开：纯本地演示，方便调样式
    startDemo();
  }
})();
