/* 网格仪表盘 —— 主题侧脚本（主面板用，fit: fluid）。
 *
 * 与宿主的约定（postMessage，见 docs/theme-state-contract.md）：
 *   iframe → 宿主：{ type: 'acc:ready' }                   主题就绪，可以喂数据
 *   宿主 → iframe：{ type: 'acc:init', v, mode, design, fit, scale, box, theme }
 *   宿主 → iframe：{ type: 'acc:ui', theme }               宿主切黑白主题时补发
 *   宿主 → iframe：{ type: 'acc:state', payload }          录制中 30Hz / 待机 2Hz
 *
 * 独立打开（双击 index.html）没有宿主，直接跑演示波形，方便调样式。
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var RPM_SEGS = 24;
  var bar = $('rpmBar');
  var segs = [];
  for (var i = 0; i < RPM_SEGS; i += 1) {
    var seg = document.createElement('span');
    seg.className = i < 14 ? 's1' : i < 18 ? 's2' : 's3';
    bar.appendChild(seg);
    segs.push(seg);
  }

  /* 安全诊断：Tauri 的 IPC 由 WebView2 对所有 frame 注入，sandbox 挡不住。
   * 期望 'undefined'；若是 'object'，说明命令权限只能靠 ACL 按窗口收紧 —— 别删这条日志。 */
  console.log('[theme] __TAURI_INTERNALS__ =', typeof window.__TAURI_INTERNALS__);

  var framed = false;
  try { framed = !!(window.parent && window.parent !== window); } catch (e) { framed = false; }

  var gotState = false;
  var demoTimer = null;
  var demoTick = null;

  function sayReady() {
    // 幂等：宿主每次 init 都回一次 ready，补上「首次 ready 早于宿主监听」丢掉的那条
    try { window.parent.postMessage({ type: 'acc:ready' }, '*'); } catch (e) { /* ignore */ }
  }

  /* ------------------------------- 格式化 ------------------------------- */

  function pad(n, w) {
    var s = String(Math.abs(n));
    while (s.length < w) s = '0' + s;
    return s;
  }

  function fmtLap(ms) {
    if (ms == null || !isFinite(ms) || ms <= 0) return '--:--.---';
    return Math.floor(ms / 60000) + ':' + pad(Math.floor((ms % 60000) / 1000), 2) + '.' + pad(Math.floor(ms % 1000), 3);
  }

  function fmtDelta(ms) {
    if (ms == null || !isFinite(ms)) return '--.---';
    return (ms < 0 ? '-' : '+') + (Math.abs(ms) / 1000).toFixed(3);
  }

  function gearText(gear) {
    if (gear === -1) return 'R';
    if (gear == null || gear === 0) return 'N';
    return String(gear);
  }

  /* 注意：真机数据里字段可能是 null（ACC 没读到时后端就留 null）。
   * 直接 `isFinite(x)` 判断是错的 —— Number(null) === 0，isFinite(null) 为 true，
   * 紧接着 x.toFixed() 就会抛 TypeError，整帧渲染中断（表现为「速度一直是 0、胎温却正常」）。
   * 所以统一走 num()：只有真正的数字才放行。 */
  function num(v) {
    return typeof v === 'number' && isFinite(v) ? v : null;
  }

  function tireClass(t) {
    if (t === null) return '';
    if (t < 60) return 'cold';
    if (t > 105) return 'hot';
    return 'ok';
  }

  /* ------------------------------- 渲染 ------------------------------- */

  var tires = Array.prototype.slice.call(document.querySelectorAll('.tire'));

  function render(st) {
    var car = st.car || {};
    var lap = st.lap || {};
    var track = st.track || {};
    var env = st.env || {};

    $('speed').textContent = Math.round(car.speed || 0);
    $('gear').textContent = gearText(car.gear);

    var maxRpm = car.maxRpmCurrent || car.maxRpm || 7600;
    var rpm = car.rpm || 0;
    $('rpmText').textContent = String(Math.round(rpm)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' rpm';
    $('rpmMax').textContent = '红线 ' + maxRpm;
    var lit = Math.round((rpm / Math.max(1, maxRpm)) * RPM_SEGS);
    for (var i = 0; i < segs.length; i += 1) {
      if (i < lit) segs[i].classList.add('on');
      else segs[i].classList.remove('on');
    }

    var d = lap.deltaMs;
    var delta = $('delta');
    if (d == null || !isFinite(d)) {
      delta.textContent = '--.---';
      delta.className = 'mono none';
    } else {
      delta.textContent = fmtDelta(d);
      delta.className = 'mono ' + (d < 0 ? 'neg' : 'pos');
    }

    $('lapCur').textContent = fmtLap(lap.timeMs);
    $('lapLast').textContent = fmtLap(lap.lastMs);
    $('lapPb').textContent = fmtLap(lap.pbMs);

    var thr = Math.round((car.throttle || 0) * 100);
    var brk = Math.round((car.brake || 0) * 100);
    var steer = (car.steer || 0) * 450;
    $('thrFill').style.width = thr + '%';
    $('brkFill').style.width = brk + '%';
    $('thrVal').textContent = thr + '%';
    $('brkVal').textContent = brk + '%';
    $('strVal').textContent = Math.round(steer) + '°';
    var sw = Math.abs((steer / 450) * 50);
    $('strFill').style.width = sw + '%';
    $('strFill').style.left = (steer >= 0 ? 50 : 50 - sw) + '%';

    var wheels = Array.isArray(st.wheels) ? st.wheels : [];
    tires.forEach(function (el, idx) {
      var w = wheels[idx] || {};
      var p = num(w.pressure);
      var t = num(w.temp);
      var bt = num(w.brakeTemp);
      el.querySelector('[data-k="p"]').textContent = p === null ? '–' : p.toFixed(1);
      el.querySelector('[data-k="t"]').textContent = t === null ? '–' : Math.round(t) + '°';
      el.className = 'tire ' + tireClass(t);
      var bfill = el.querySelector('[data-k="bbar"]');
      bfill.style.width = Math.max(3, Math.min(100, ((bt || 0) / 800) * 100)).toFixed(0) + '%';
      bfill.className = bt === null ? '' : bt > 640 ? 'hot' : bt < 380 ? 'cold' : '';
      el.querySelector('[data-k="bnum"]').textContent = bt === null ? '–' : Math.round(bt) + '°';
    });

    $('track').textContent = track.title || track.name || '—';
    var at = num(env.airTemp);
    var rt = num(env.roadTemp);
    $('temps').textContent = (at === null ? '–' : at.toFixed(0)) + ' / ' + (rt === null ? '–' : rt.toFixed(0)) + ' °C';

    var status = $('status');
    var map = { streaming: '已连接', waiting: '等待游戏', connecting: '连接中', error: '连接断开', idle: '待机', stopped: '已停止' };
    status.textContent = map[st.status] || '待机';
    status.className = 'state ' + (st.status || 'idle');
  }

  /* ------------------------------- 数据来源 ------------------------------- */

  window.addEventListener('message', function (event) {
    var msg = event.data;
    if (!msg || typeof msg !== 'object') return;

    if (msg.type === 'acc:init') {
      if (msg.theme) document.documentElement.dataset.theme = msg.theme;
      sayReady();
      return;
    }

    if (msg.type === 'acc:ui') {
      if (msg.theme) document.documentElement.dataset.theme = msg.theme;
      return;
    }

    if (msg.type === 'acc:state' && msg.payload) {
      gotState = true;
      stopDemo();
      // 单帧脏数据不能让整个面板停止更新：渲染出错就记一条，下一帧继续
      try { render(msg.payload); } catch (e) { console.warn('[theme] render failed:', e); }
    }
  });

  if (framed) {
    sayReady();
    demoTimer = setTimeout(function () {
      if (!gotState) startDemo();
    }, 2000);
  } else {
    startDemo();
  }

  /* ------------------------------- 演示数据 ------------------------------- */

  function startDemo() {
    stopDemo();
    var t = 0;
    demoTick = setInterval(function () {
      if (gotState) { stopDemo(); return; }
      t += 1;
      var wave = Math.sin(t / 11);
      var speed = 196 + wave * 82;
      var brk = speed < 150 ? 42 : 0;
      render({
        status: 'streaming',
        car: {
          speed: speed,
          gear: speed > 240 ? 5 : speed > 190 ? 4 : speed > 140 ? 3 : 2,
          rpm: 4300 + wave * 2400,
          maxRpmCurrent: 7600,
          throttle: brk ? 0 : 0.94,
          brake: brk / 100,
          steer: Math.sin(t / 7) * 0.62,
        },
        lap: { timeMs: 41230 + t * 70, lastMs: 92480, pbMs: 91820, deltaMs: wave * 620 },
        wheels: [
          { corner: 'FL', pressure: 27.4, temp: 78, brakeTemp: 412 },
          { corner: 'FR', pressure: 27.6, temp: 81, brakeTemp: 428 },
          { corner: 'RL', pressure: 27.1, temp: 75, brakeTemp: 355 },
          { corner: 'RR', pressure: 27.2, temp: 106, brakeTemp: 662 },
        ],
        env: { airTemp: 26, roadTemp: 31 },
        track: { title: 'Monza' },
      });
    }, 70);
  }

  function stopDemo() {
    if (demoTick !== null) { clearInterval(demoTick); demoTick = null; }
    if (demoTimer !== null) { clearTimeout(demoTimer); demoTimer = null; }
  }
})();
