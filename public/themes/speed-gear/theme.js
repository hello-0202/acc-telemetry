/* speed-gear（速度 / 挡位 / 转速）—— 主题侧脚本。
 *
 * 与宿主的约定（postMessage，见 docs/theme-state-contract.md）：
 *   iframe → 宿主：{ type: 'acc:ready' }                  主题加载完成，可以开始喂数据
 *   宿主 → iframe：{ type: 'acc:init',  v, mode, design, scale, background }
 *   宿主 → iframe：{ type: 'acc:ui',    theme?, background? }  切黑白 / 透明背景时补发
 *   宿主 → iframe：{ type: 'acc:state', payload }         每秒 2~30 次
 *
 * 布局照 迈速表demo.html：顶部 14 格转速灯带，下面一行左速度 / 中挡位 / 右转速。
 * 灯带按 rpm / maxRpm 依次点亮；逼近允许红线（≥96%）时整条闪红 —— 该换挡了。
 *
 * 独立打开时（双击 index.html）没有宿主，2 秒后自动跑演示数据，
 * 方便调样式：不用开游戏、不用起后端。
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var card = $('card');
  var speedEl = $('speed');
  var gearEl = $('gear');
  var rpmEl = $('rpm');
  var ledsEl = $('leds');

  /* 14 格灯带：前 9 格绿、中间 3 格黄、最后 2 格红（与 demo 的 14 个方块一致） */
  var LED_COUNT = 14;
  var ledEls = [];

  (function buildLeds() {
    for (var i = 0; i < LED_COUNT; i++) {
      var led = document.createElement('i');
      led.className = 'led';
      led.dataset.zone = i < 9 ? 'g' : i < 12 ? 'y' : 'r';
      ledsEl.appendChild(led);
      ledEls.push(led);
    }
  })();

  /* 安全诊断：Tauri 的 IPC 是 WebView2 的「对所有 frame 注入脚本」实现的，
   * 所以 sandbox iframe 里的主题很可能也能直接 invoke 命令（sandbox 挡不住注入对象）。
   * 期望值 'undefined'；若是 'object' 说明命令只能靠 ACL 按窗口收紧 —— 别删这条日志，
   * 以后换 Tauri 版本 / 改 capabilities 时它就是回归信号。 */
  console.log('[theme] __TAURI_INTERNALS__ =', typeof window.__TAURI_INTERNALS__);

  // isFinite(null) === true 是个坑：真机 null 字段必须先挡掉
  function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }

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
  // 写到 html[data-bg]，主题想按模式改配色时可以查它。
  // 这个主题完全透明、不画底板：透明时直接透出游戏，不透明时由宿主在窗口层铺深色底。
  function applyUi(msg) {
    if (msg.background === 'transparent' || msg.background === 'opaque') {
      document.documentElement.dataset.bg = msg.background;
    }
  }

  function gearText(gear) {
    if (gear == null) return '-';
    if (gear === -1) return 'R';
    if (gear === 0) return 'N';
    return String(gear);
  }

  function gearKind(gear) {
    if (gear == null) return '';
    if (gear === -1) return 'r';
    if (gear === 0) return 'n';
    return 'd';
  }

  /* 允许转速：优先当前车/赛道的 maxRpmCurrent，缺失或为 0 时退回 maxRpm（契约里写明的 fallback）。 */
  function maxRpmOf(car) {
    if (!car) return null;
    var max = num(car.maxRpmCurrent);
    if (max == null || max <= 0) max = num(car.maxRpm);
    return max != null && max > 0 ? max : null;
  }

  /* --------------------------- 换挡提示（整条闪红） ---------------------------
   * 只在状态翻转时改一次 DOM。每帧都去设 class 会把 animation 反复重启，
   * 闪烁看起来就像卡住不动（本项目在 stroke-dasharray 上踩过同类坑）。 */
  var shiftOn = false;

  function setShift(on) {
    if (on === shiftOn) return;
    shiftOn = on;
    card.dataset.shift = on ? '1' : '0';
  }

  /* 换挡判定：转速 / 当前允许最大转速 ≥ 96%。
   * 空挡 / 倒挡不提示 —— 那两种挡位踩到红线不是"该升挡"。 */
  function needShift(car) {
    if (!car) return false;
    var rpm = num(car.rpm);
    var max = maxRpmOf(car);
    var g = num(car.gear);
    if (rpm == null || max == null || g == null || g < 1) return false;
    return rpm / max >= 0.96;
  }

  /* --------------------------- 灯带 --------------------------- */
  var lastLit = -1;

  /* 点亮前 n 格。按整数格跳变，不做小数插值：
   * 灯带是离散的换挡指示，半格亮反而像接触不良。 */
  function setLeds(n) {
    if (n === lastLit) return;
    lastLit = n;
    for (var i = 0; i < LED_COUNT; i++) {
      ledEls[i].classList.toggle('on', i < n);
    }
  }

  /* ------------------------------- 渲染 ------------------------------- */

  var lastGear = null;

  function render(st) {
    card.dataset.status = st.status || 'idle';

    // 契约：非 streaming 时后端仍保留上一场的最后值。主题要自己显示占位，
    // 否则没开游戏 / 已停止监测时，旧的速度和转速看起来像还在实时采集。
    var live = st.status === 'streaming';
    var car = live ? (st.car || {}) : null;

    var speed = car ? num(car.speed) : null;
    var rpm = car ? num(car.rpm) : null;
    var max = maxRpmOf(car);

    speedEl.textContent = speed == null ? '--' : String(Math.round(speed));
    rpmEl.textContent = rpm == null ? '--' : String(Math.round(rpm));

    var g = car ? num(car.gear) : null;
    gearEl.textContent = gearText(g);
    if (g !== lastGear) {
      lastGear = g;
      gearEl.dataset.g = gearKind(g);
    }

    var lit = 0;
    if (rpm != null && max != null) {
      var ratio = rpm / max;
      if (ratio < 0) ratio = 0;
      if (ratio > 1) ratio = 1;
      lit = Math.round(ratio * LED_COUNT);
    }
    // 到换挡点就整条点亮再闪红，避免 96% 时最后一格还灰着
    var shift = needShift(car);
    if (shift) lit = LED_COUNT;
    setLeds(lit);
    setShift(shift);
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
      // 转速故意扫到红线附近，这样能直接看到灯带全亮 + 闪红的效果
      render({
        status: 'streaming',
        car: {
          speed: speed,
          gear: speed > 230 ? 4 : speed > 150 ? 3 : 2,
          rpm: 5200 + Math.sin(t / 5) * 2500,
          maxRpmCurrent: 7600,
        },
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
