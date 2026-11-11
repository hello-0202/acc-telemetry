/* 踏板时序 HUD —— 主题侧脚本。
 *
 * 宿主 → 主题：{ type:'acc:init' } / { type:'acc:ui' } / { type:'acc:state', payload }
 * 主题 → 宿主：{ type:'acc:ready' }（每次 init 都要回一条，别用「只发一次」开关）
 *
 * 背景不自己画：颜色 / 不透明度 / 模糊都作用于宿主窗口层（设置里同一组选项），
 * 主题只在「不透明度 ≈100%」时把底板并成同一块 --host-bg，避免叠出更深的色；
 * 另外按背景明暗（亮度 × 不透明度）切换中性分割线的深浅。
 *
 * 时序曲线的数据来自主题自己在 iframe 里攒的帧历史：acc:state 每来一帧就把
 * throttle / brake 压进 150 帧环形缓冲，画成 SVG 面积 + 折线。契约里没有踏板
 * 历史字段，也不该往 state 里加——主题不连 WS、不读文件，只收 state。
 * 30Hz 下 150 帧 ≈ 5 秒，够看完整一个「刹车点 → 弯中 → 出弯给油」。
 *
 * 转向横条直接读 car.steer（-1..1，负=左蓝 / 正=右橙），亮色从中心向对应
 * 一侧长出；与踏板历史无关，不需要环形缓冲。填充长度走 steerVisual() 视觉增益
 * 曲线（真机转角普遍偏小，线性映射会让颜色永远只是窄窄一截）；条下方的度数
 * 按契约约定乘 450（本项目约定满舵 ±450°），取绝对值、不写正负号，方向只靠颜色。
 * 度数的数字居中、° 绝对定位挂在右侧，不参与居中计算。
 * 换挡提示与 speed-gear 同判定（rpm ÷ maxRpmCurrent||maxRpm ≥ 0.96 && gear ≥ 1），
 * 命中时挡位数字同色硬切闪烁；只按状态翻转改 hud[data-shift]，不每帧重挂动画。
 *
 * 踏板数值全卡片只出现一次：曲线右侧那两根输入条上方的纯数字（无 %）。
 * 曲线框外不再有独立读数栏，所以 brake-pct / thr-pct 是唯一的数值节点。
 *
 * 双击 index.html（或在没有宿主的预览 iframe 里）会自动跑一段演示驾驶数据
 * （含左弯 / 出弯反打，方便一眼看到左右两种颜色）。
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  // isFinite(null) === true 是坑：真机 null 字段必须先挡掉，否则 toFixed 抛错、
  // 整帧渲染中断，表现是「一半字段在动、另一半永远不动」。
  function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }
  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
  function gearText(g) {
    if (g === -1) return 'R';
    if (g === 0) return 'N';
    return String(g);
  }

  var TRACE_H = 52;
  var X_RIGHT = 396;              // 右端留 4px，折线不会被裁
  var HISTORY = 150;
  var STEP = X_RIGHT / (HISTORY - 1);
  var hist = [];                   // [{ b: 0..1, t: 0..1 }]

  // 转向填充的「视觉增益」：真机方向盘很少打到满舵，常见弯角在 180° 上下，
  // 按线性 |steer| 画的话 180° 只占半条 40%，颜色永远窄窄一截、看不清方向。
  // 把常用区间 0..200° 线性放大到占半条的 82%，剩下 200..450° 压进末段 18%，
  // 满舵仍能顶到边、全程单调不跳变。条只作视觉指示，精确角度看下方度数；
  // 增益曲线只影响填充长度，不篡改任何真值。
  var STEER_DEG_FULL = 450;       // 契约：归一化 1 = 满舵 450°
  var STEER_VIS_KNEE = 200;       // 常用弯角上限（度）
  var STEER_VIS_SHARE = 0.82;     // 常用区间占半条的比例

  function steerVisual(v) {
    var deg = Math.abs(v) * STEER_DEG_FULL;
    if (deg <= STEER_VIS_KNEE) return (deg / STEER_VIS_KNEE) * STEER_VIS_SHARE;
    return STEER_VIS_SHARE
      + ((deg - STEER_VIS_KNEE) / (STEER_DEG_FULL - STEER_VIS_KNEE)) * (1 - STEER_VIS_SHARE);
  }

  var hud = $('hud');
  var els = {
    brakeArea: $('brake-area'), brakeLine: $('brake-line'),
    thrArea: $('thr-area'), thrLine: $('thr-line'),
    brakeFill: $('brake-fill'), thrFill: $('thr-fill'),
    brakePct: $('brake-pct'), thrPct: $('thr-pct'),
    steerLeft: $('steer-left'), steerRight: $('steer-right'), steerDeg: $('steer-deg'),
    steerDegNum: $('steer-deg-num'), steerDegUnit: $('steer-deg-unit'),
    gear: $('gear'), speed: $('speed')
  };

  // 换挡判定：与 speed-gear / HUD 设计稿同一约定，别各写一套阈值。
  // maxRpmCurrent 为 0 或缺失时退回 car.maxRpm（契约 desc 里写明的 fallback）。
  var SHIFT_RATIO = 0.96;

  function maxRpmOf(car) {
    var max = num(car.maxRpmCurrent);
    if (max == null || max <= 0) max = num(car.maxRpm);
    return max;
  }

  function needShift(car) {
    var rpm = num(car.rpm);
    var max = maxRpmOf(car);
    var g = num(car.gear);
    if (rpm == null || max == null || max <= 0 || g == null || g < 1) return false;
    return rpm / max >= SHIFT_RATIO;
  }

  // 只在状态翻转时改 DOM：30Hz 每帧写同一个值会不断重挂动画，闪烁节奏会乱。
  var shiftOn = false;
  function setShift(on) {
    if (on === shiftOn) return;
    shiftOn = on;
    hud.dataset.shift = on ? '1' : '0';
  }

  /* ------------------------------ 时序绘制 ------------------------------ */

  function drawTrace(key, area, line) {
    var len = hist.length;
    if (!len) {
      area.setAttribute('points', '');
      line.setAttribute('points', '');
      return;
    }

    var pts = [];
    for (var i = 0; i < len; i++) {
      var v = hist[i][key];
      var x = X_RIGHT - (len - 1 - i) * STEP;  // 最新点恒定在右端
      var y = 1 + (1 - v) * (TRACE_H - 2);     // v=1 → 1，v=0 → 51
      pts.push(x.toFixed(1) + ',' + y.toFixed(1));
    }

    var firstX = X_RIGHT - (len - 1) * STEP;
    var points = pts.join(' ');
    line.setAttribute('points', points);
    // 面积：从底部起 → 沿折线 → 回到底部右端
    area.setAttribute('points',
      firstX.toFixed(1) + ',' + TRACE_H + ' ' + points + ' ' + X_RIGHT + ',' + TRACE_H);
  }

  /* -------------------------------- 渲染 -------------------------------- */

  function render(st) {
    try {
      var live = st.status === 'streaming';
      hud.dataset.status = st.status || 'idle';

      // 契约：非 streaming 时后端仍保留上一场最后值，这里按空值渲染并清空曲线，
      // 免得没开游戏时也看到踏板停在上次的位置。
      var car = live ? (st.car || {}) : {};
      var thr = num(car.throttle);
      var brk = num(car.brake);
      var tp = thr == null ? 0 : clamp01(thr);
      var bp = brk == null ? 0 : clamp01(brk);

      if (!live) {
        hist.length = 0;
      } else {
        // 字段偶尔为 null：不画一根假掉到 0 的线，沿用上一帧值
        var b = brk == null && hist.length ? hist[hist.length - 1].b : bp;
        var t = thr == null && hist.length ? hist[hist.length - 1].t : tp;
        hist.push({ b: b, t: t });
        if (hist.length > HISTORY) hist.shift();
      }

      drawTrace('b', els.brakeArea, els.brakeLine);
      drawTrace('t', els.thrArea, els.thrLine);

      // 唯一的踏板数值：曲线右侧两根条的上方，纯数字、无 %
      els.brakePct.textContent = (live && brk != null) ? String(Math.round(bp * 100)) : '--';
      els.thrPct.textContent = (live && thr != null) ? String(Math.round(tp * 100)) : '--';

      // 两根输入条：从底部往上填充，条内只有纯色、不画刻度线
      els.brakeFill.style.height = (live && brk != null ? bp * 100 : 0).toFixed(1) + '%';
      els.thrFill.style.height = (live && thr != null ? tp * 100 : 0).toFixed(1) + '%';

      // 转向：契约 car.steer -1..1，负=左 / 正=右（与 pedals-wheel 转向弧同约定）。
      // null / 非 streaming 归零；0.02 死区（约 9°）避免真机方向盘微抖让横条与
      // 度数读数在左右两色之间来回闪，也让「直行」稳定显示中性的 0°。
      var steer = live ? num(car.steer) : null;
      var sv = steer == null ? 0 : (steer < -1 ? -1 : steer > 1 ? 1 : steer);
      if (Math.abs(sv) < 0.02) sv = 0;
      // 半条 = 整条的 50%；长度走视觉增益，180° 也能占到大半截而不是窄窄一点
      var half = (steerVisual(sv) * 50).toFixed(1) + '%';
      els.steerLeft.style.width = sv < 0 ? half : '0%';
      els.steerRight.style.width = sv > 0 ? half : '0%';

      // 度数：契约约定满舵 ±450°（telemetry-state.json 里 car.steer 的 desc），
      // 归一化 steer × 450 取绝对值得到角度。不写正负号，方向只靠颜色
      // （左蓝 / 右橙）；字段缺失 / 非 streaming 显示 '--' 并隐藏单位，
      // 直行回中性文字色。数字与单位拆成两个节点：只有数字进布局、参与居中。
      var deg = Math.round(Math.abs(sv) * STEER_DEG_FULL);
      els.steerDegNum.textContent = steer == null ? '--' : String(deg);
      els.steerDegUnit.textContent = steer == null ? '' : '°';
      els.steerDeg.style.color = sv < 0 ? 'var(--steer-left)'
        : sv > 0 ? 'var(--steer-right)' : '';

      var g = live ? num(car.gear) : null;
      var sp = live ? num(car.speed) : null;
      els.gear.textContent = g == null ? '–' : gearText(g);
      els.speed.textContent = sp == null ? '--' : String(Math.max(0, Math.round(sp)));

      // 换挡提示：非 streaming / 空挡 / 倒挡 / 转速字段缺失一律停闪
      setShift(live && needShift(car));
    } catch (e) {
      console.warn('[pedal-trace] render failed', e);
    }
  }

  /* -------------------------------- 协议 -------------------------------- */

  // 宿主背景 = 设置里的「背景颜色 + 不透明度」，由窗口层铺在 iframe 后面，
  // 主题自己不画底（见 theme.css 顶部说明）。这里只做两件跟随：
  //   1. 把颜色 / 不透明度存进 CSS 变量，供不透明度拉满时并成同一块底板；
  //   2. 算「这层底亮不亮」，浅色底上把中性分割线换成深色，否则白线直接消失。
  var hostBgColor = '#0b0f14';
  var hostBgOpacity = 0;
  var hostLight = null;

  // 近似相对亮度：够判断「浅底 / 深底」，不需要精确色度学
  function luminance(hex) {
    var m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return 0;
    var h = m[1];
    if (h.length === 3) {
      h = h.charAt(0) + h.charAt(0) + h.charAt(1) + h.charAt(1) + h.charAt(2) + h.charAt(2);
    }
    var r = parseInt(h.slice(0, 2), 16) / 255;
    var g = parseInt(h.slice(2, 4), 16) / 255;
    var b = parseInt(h.slice(4, 6), 16) / 255;
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }

  // 不透明度太低时底下仍是游戏画面，不能因为颜色是白的就当成浅底
  function syncHostTone() {
    var light = hostBgOpacity >= 0.3 && luminance(hostBgColor) > 0.6;
    if (light === hostLight) return;
    hostLight = light;
    document.documentElement.dataset.hostLight = light ? '1' : '0';
  }

  var hostSeen = false;
  var gotState = false;
  var lastSeq = null;
  var demoTick = null;
  var demoTimer = null;

  function sayReady() {
    try { window.parent.postMessage({ type: 'acc:ready' }, '*'); } catch (e) { /* 独立打开没有 parent */ }
  }

  // 内置主题自己的配置项（theme.json 的 settings）。宿主在 acc:init / acc:ui 里给
  // 已经补好默认值的对象；这里只认白名单值，避免脏数据把布局写坏。
  //   traceLayout: split = 刹车 / 油门上下两条；merged = 两条叠在同一张图里
  //   showVehicle: show / hide = 右侧转向角度、挡位、速度是否显示
  function applyThemeSettings(s) {
    if (!s || typeof s !== 'object') return;
    if (s.traceLayout === 'split' || s.traceLayout === 'merged') {
      hud.dataset.traceLayout = s.traceLayout;
    }
    if (s.showVehicle === 'show' || s.showVehicle === 'hide') {
      hud.dataset.vehicle = s.showVehicle;
    }
  }

  function applyUi(msg) {
    if (msg.theme) document.documentElement.dataset.theme = msg.theme;
    if (msg.settings) applyThemeSettings(msg.settings);
    if (msg.background === 'transparent' || msg.background === 'opaque') {
      document.documentElement.dataset.bg = msg.background;
    }
    if (typeof msg.backgroundColor === 'string') {
      hostBgColor = msg.backgroundColor;
      document.documentElement.style.setProperty('--host-bg', msg.backgroundColor);
    }
    if (typeof msg.backgroundOpacity === 'number') {
      hostBgOpacity = msg.backgroundOpacity;
      document.documentElement.style.setProperty('--host-bg-opacity', String(msg.backgroundOpacity));
    }
    syncHostTone();
    if (typeof msg.textColor === 'string') {
      document.documentElement.style.setProperty('--text-color', msg.textColor);
    }
  }

  window.addEventListener('message', function (ev) {
    var m = ev.data;
    if (!m || typeof m !== 'object') return;

    if (m.type === 'acc:init') {
      // 收到握手 = 确实在宿主里：撤掉本地预览舞台，恢复 100% 铺满 + 透明
      hostSeen = true;
      stopDemo();
      delete document.documentElement.dataset.standalone;
      applyUi(m);
      sayReady();
      return;
    }

    if (m.type === 'acc:ui') { applyUi(m); return; }

    if (m.type === 'acc:state' && m.payload) {
      hostSeen = true;
      gotState = true;
      stopDemo();
      // 极少数情况下宿主会先推 state 再握手：确认确实嵌在宿主里才撤掉预览舞台。
      // 只比较 window.parent 引用不触碰跨域属性，sandbox iframe 里也安全。
      try {
        if (window.parent !== window) delete document.documentElement.dataset.standalone;
      } catch (e) { /* 取不到 parent 就保持原样，等 acc:init 来撤 */ }
      // seq 相同代表同一帧，跳过重复渲染（也避免把同一帧压进历史两次）
      var seq = m.payload.seq == null ? null : m.payload.seq;
      if (seq != null && seq === lastSeq) return;
      lastSeq = seq;
      render(m.payload);
    }
  });

  /* ---------------------------- 离线演示数据 ---------------------------- */

  // 一段像真驾驶的循环：全油门直线 → 重刹 → 弯中维持 → 出弯渐给油
  function demoFrame(t) {
    var phase = (t % 170) / 170;
    var thr, brk, speed, gear, steer, rpm;

    if (phase < 0.30) {
      // 直线：几乎不打方向，微抖压在直行死区（0.02 ≈ 9°）以内，
      // 免得演示里明明是直线却一直显示十几度、在蓝橙之间来回跳
      thr = 1; brk = 0;
      speed = 235 + (phase / 0.30) * 35; gear = 6;
      steer = Math.sin(t * 0.28) * 0.012;
    } else if (phase < 0.42) {
      // 刹车入左弯：方向逐渐打到 -0.5
      var k = (phase - 0.30) / 0.12;
      thr = 0; brk = Math.sin(k * Math.PI) * 0.95;
      speed = 270 - k * 125; gear = k < 0.5 ? 5 : 4;
      steer = -0.12 - k * 0.38;
    } else if (phase < 0.62) {
      // 弯中维持左舵
      var q = (phase - 0.42) / 0.20;
      thr = 0.18 + q * 0.22; brk = 0;
      speed = 145 + q * 12; gear = 3;
      steer = -0.50 + q * 0.08;
    } else {
      // 出弯回正，中途带一段反打（右舵），让预览里左右两色都看得到
      var r = (phase - 0.62) / 0.38;
      thr = 0.40 + r * 0.60; brk = 0;
      speed = 157 + r * 78; gear = r < 0.35 ? 3 : r < 0.70 ? 4 : 5;
      steer = -0.42 + r * 0.42 + Math.sin(r * Math.PI) * 0.55;
    }

    // 演示转速：两段全油门的尾段逼近红线（0.96 × 7600 = 7296），
    // 换挡闪烁在预览里就能看到，不用手动造数据
    if (phase < 0.30) {
      rpm = 5900 + (phase / 0.30) * 1650;
    } else if (phase < 0.42) {
      rpm = 7550 - ((phase - 0.30) / 0.12) * 2600;
    } else if (phase < 0.62) {
      rpm = 4950 + ((phase - 0.42) / 0.20) * 900;
    } else {
      rpm = 5850 + ((phase - 0.62) / 0.38) * 1900;
    }

    var jitter = Math.sin(t * 0.7) * 0.012;  // 让曲线不是一条死直线
    return {
      status: 'streaming',
      car: {
        throttle: clamp01(thr + (thr > 0 ? jitter : 0)),
        brake: clamp01(brk + (brk > 0 ? jitter : 0)),
        speed: speed,
        gear: gear,
        rpm: rpm,
        maxRpm: 7600,
        maxRpmCurrent: 7600,
        steer: steer < -1 ? -1 : steer > 1 ? 1 : steer
      }
    };
  }

  function startDemo() {
    stopDemo();
    var t = 0;
    demoTick = setInterval(function () {
      if (gotState || hostSeen) { stopDemo(); return; }
      t += 1;
      render(demoFrame(t));
    }, 60);
  }

  function stopDemo() {
    if (demoTick !== null) { clearInterval(demoTick); demoTick = null; }
    if (demoTimer !== null) { clearTimeout(demoTimer); demoTimer = null; }
  }

  /* ------------------------------ 启动 ------------------------------ */

  // 一开始按「本地预览」处理：固定 600×134 居中 + 模拟游戏画面底色。
  // 真宿主会在 400ms 内发 acc:init，届时撤掉。这样双击、预览面板 iframe、
  // 真悬浮窗三种环境都不用手动切换。
  document.documentElement.dataset.standalone = '1';
  render({ status: 'idle' });
  sayReady();

  // 没等到握手就当没有宿主，跑演示驾驶数据（独立打开 / 静态预览都是这条路）
  demoTimer = setTimeout(function () {
    demoTimer = null;
    if (!hostSeen && !gotState) startDemo();
  }, 700);
})();
