/* GT 座舱 —— 主题侧脚本（主面板用，fit: fluid）。
 *
 * 宿主协议（见 docs/theme-state-contract.md）：
 *   iframe → 宿主：{ type:'acc:ready' }                  主题就绪
 *   宿主 → iframe：{ type:'acc:init', v, mode, design, fit, scale, box, theme, background }
 *   宿主 → iframe：{ type:'acc:ui', theme, background }   切黑白主题 / 悬浮窗背景补发
 *   宿主 → iframe：{ type:'acc:state', payload }          录制中 30Hz / 待机 2Hz
 *
 * 独立打开（双击 index.html）没有宿主 → 跑演示数据，改样式不用开游戏。
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var app = $('app');
  /* 中列只有一排灯点：10 颗，同时当转速进度和换挡灯用（设计稿就是一排圆点） */
  var SHIFT_N = 10;

  /* 安全诊断：Tauri 的 IPC 由 WebView2 对所有 frame 注入，sandbox 挡不住。
   * 期望 'undefined'；若是 'object'，说明命令权限只能靠 ACL 按窗口收紧 —— 别删这条日志。 */
  console.log('[theme] __TAURI_INTERNALS__ =', typeof window.__TAURI_INTERNALS__);

  /* ------------------------------ 静态骨架 ------------------------------ */

  var shiftLeds = Array.prototype.slice.call(document.querySelectorAll('#shift s'));

  /* 四轮：把每块里的 svg 元素缓存下来，逐帧只改属性不查 DOM */
  var tyres = Array.prototype.slice.call(document.querySelectorAll('.tyre')).map(function (el) {
    return {
      el: el,
      arc: el.querySelector('[data-role="arc"]'),
      brake: el.querySelector('[data-role="brake"]'),
      t: el.querySelector('[data-role="t"]'),
      p: el.querySelector('[data-role="p"]'),
      b: el.querySelector('[data-role="b"]')
    };
  });

  /* ------------------------------ 格式化 ------------------------------ */

  function pad(n, w) {
    var s = String(Math.abs(n));
    while (s.length < w) s = '0' + s;
    return s;
  }

  function fmtLap(ms) {
    if (ms == null || !isFinite(ms) || ms <= 0) return '--:--.---';
    return Math.floor(ms / 60000) + ':' + pad(Math.floor((ms % 60000) / 1000), 2) + '.' + pad(Math.floor(ms % 1000), 3);
  }

  /* 剩余时间原来用 fmtClock（只到分秒），REMAIN 换成 EST 后右列全是圈速，
   * 这个格式化器没有调用点了，删掉避免死代码 —— 真要再用，恢复成
   * `Math.floor(ms/60000) + ':' + pad(Math.floor((ms%60000)/1000), 2)` 即可。 */

  function fmtDelta(ms) {
    if (ms == null || !isFinite(ms)) return '--.---';
    return (ms < 0 ? '-' : '+') + (Math.abs(ms) / 1000).toFixed(3);
  }

  /* 字段可能是 null（ACC 没读到时后端留 null）。
   * 别用 isFinite() 判空：Number(null) === 0，isFinite(null) 为 true，
   * 紧接着 null.toFixed() 抛 TypeError 会让整帧渲染中断。 */
  function num(v) {
    return typeof v === 'number' && isFinite(v) ? v : null;
  }

  function tempClass(t) {
    if (t === null) return 't-na';
    if (t < 60) return 't-cold';
    if (t > 105) return 't-hot';
    if (t > 92) return 't-warm';
    return 't-ok';
  }

  /* 刹车盘温度分档：盘温整体比胎温高一个量级，阈值按常见的 250/450/600℃ 切 */
  function brakeLevel(b) {
    if (b === null) return 'na';
    if (b < 250) return 'cold';
    if (b < 450) return 'ok';
    if (b < 600) return 'warm';
    return 'hot';
  }

  function gearText(gear, gearTextRaw) {
    if (typeof gearTextRaw === 'string' && gearTextRaw.length) return gearTextRaw;
    if (gear === -1) return 'R';
    if (gear == null || gear === 0) return 'N';
    return String(gear);
  }

  /* 只动状态类，别用 className 整体覆盖：span 上还挂着 .v（功能色靠它），
   * 一旦被覆盖成 '' 或 'off'，颜色就退回继承的白色 —— 曾经踩过。 */
  function setChip(id, text, cls) {
    var el = $(id);
    if (!el) return;
    el.textContent = text;
    el.classList.remove('off', 'hot');
    if (cls === 'off' || cls === 'hot') el.classList.add(cls);
  }

  /* 写文本 + 状态属性。颜色靠 CSS 的 [data-*] 选，不写 inline style ——
   * 待机时把属性删掉，颜色就回落到纯白，不用记着清哪一条。 */
  function setLvl(id, text, attr, lvl) {
    var el = $(id);
    if (!el) return;
    el.textContent = text;
    if (lvl === null || lvl === undefined) el.removeAttribute(attr);
    else el.setAttribute(attr, String(lvl));
  }

  /* 指示灯四种状态：'0' 灭 / 'blink' 转向灯闪黄 / 'lamp' 大灯黄 / 'wipe' 雨刷蓝。
   * 只在值变了才写 data-on：每帧重写同一个值不会重启动画，但少写一次少一次样式重算。 */
  function setLamp(id, mode) {
    var el = $(id);
    if (el && el.dataset.on !== mode) el.dataset.on = mode;
  }

  /* 顶部信息条七格的占位符标记：文本是 '—' 就挂 data-empty，CSS 据此把字体里的
   * em dash 换成一条统一短横（见 theme.css 的 .strip b[data-empty]）。
   * 在 render 末尾统一扫一遍，而不是改每一处赋值 —— 七格的赋值路径各不相同
   * （直接 textContent / setLvl / setWx），只在一处收口才不会漏掉某个状态；
   * 有数据 / 部分字段为 null / 待机 / 演示数据全都自动跟着走。
   * 只在属性真的变了才写，60ms 一帧不产生多余样式重算。 */
  var STRIP_VALS = ['wx', 'airTemp', 'roadTemp', 'grip', 'track', 'pos', 'lapNum'];
  function syncStripEmpty() {
    for (var i = 0; i < STRIP_VALS.length; i++) {
      var el = $(STRIP_VALS[i]);
      if (!el) continue;
      var empty = el.textContent.trim() === '—';
      var has = el.hasAttribute('data-empty');
      if (empty && !has) el.setAttribute('data-empty', '');
      else if (!empty && has) el.removeAttribute('data-empty');
    }
  }

  /* 天气图标：和 DRY / DRIZZLE / LIGHT / RAIN / HEAVY / STORM 六个词一一对应，
   * 顺序就是 env.rainIntensity 的档位（0..5）。线稿风，颜色继承 #wx[data-w]，
   * 所以下雨天温和单词一起变色，不用两套配色。 */
  var WX_ICONS = [
    /* 0 DRY —— 太阳 */
    '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M18.8 5.2l-1.4 1.4M6.6 17.4l-1.4 1.4"/></svg>',
    /* 1 DRIZZLE —— 云 + 两滴 */
    '<svg viewBox="0 0 24 24"><path d="M7.5 15.5h9a3.5 3.5 0 0 0 .4-6.98A5 5 0 0 0 7.2 8.4a3.6 3.6 0 0 0 .3 7.1z"/><path d="M9.5 18.2l-.7 1.6M13 18.2l-.7 1.6"/></svg>',
    /* 2 LIGHT —— 云 + 三滴 */
    '<svg viewBox="0 0 24 24"><path d="M7.5 15.5h9a3.5 3.5 0 0 0 .4-6.98A5 5 0 0 0 7.2 8.4a3.6 3.6 0 0 0 .3 7.1z"/><path d="M8.6 18.2 8 20M12 18.2 11.4 20M15.4 18.2 14.8 20"/></svg>',
    /* 3 RAIN —— 云 + 三道斜雨 */
    '<svg viewBox="0 0 24 24"><path d="M7.5 14.5h9a3.5 3.5 0 0 0 .4-6.98A5 5 0 0 0 7.2 7.4a3.6 3.6 0 0 0 .3 7.1z"/><path d="M8.4 17.5 7.4 21M12 17.5 11 21M15.6 17.5 14.6 21"/></svg>',
    /* 4 HEAVY —— 云 + 四道雨 */
    '<svg viewBox="0 0 24 24"><path d="M7.5 14.5h9a3.5 3.5 0 0 0 .4-6.98A5 5 0 0 0 7.2 7.4a3.6 3.6 0 0 0 .3 7.1z"/><path d="M7.6 17.4 6.6 21.5M10.6 17.4 9.6 21.5M13.6 17.4 12.6 21.5M16.6 17.4 15.6 21.5"/></svg>',
    /* 5 STORM —— 云 + 闪电 */
    '<svg viewBox="0 0 24 24"><path d="M7.5 14.5h9a3.5 3.5 0 0 0 .4-6.98A5 5 0 0 0 7.2 7.4a3.6 3.6 0 0 0 .3 7.1z"/><path d="M13 16.2l-3.2 4h2.4l-1.2 3"/></svg>'
  ];

  var WX_WORDS = ['DRY', 'DRIZZLE', 'LIGHT', 'RAIN', 'HEAVY', 'STORM'];

  /* 写天气：单词 + 图标 + 档位属性。留空（null）时图标一起清掉，
   * 待机不会挂着一个上一场的太阳。 */
  function setWx(level) {
    var el = $('wx');
    if (!el) return;
    if (level === null) el.removeAttribute('data-w');
    else el.setAttribute('data-w', String(level));
    $('wxText').textContent = level === null ? '—' : WX_WORDS[level];
    $('wxIco').innerHTML = level === null ? '' : WX_ICONS[level];
  }

  /* ------------------------------ 渲染 ------------------------------ */

  function render(st) {
    st = st || {};
    var live = st.status === 'streaming';
    app.dataset.status = st.status || 'idle';

    var car = st.car || {};
    var lap = st.lap || {};
    var track = st.track || {};
    var env = st.env || {};
    var flags = st.flags || {};
    var session = st.session || {};

    /* 非 streaming（待机/等待/错误）时实时字段一律留空，只保留赛道名、PB 这类会话元信息 */
    var P = function (v) { return live ? num(v) : null; };

    /* --- 右上：四格信息条（旗语 / 赛道 / 位置 / 圈数） --- */

    /* 旗语：五个 flag_xxx 都是 0/1 计数，按严重程度取**一个** ——
     * 黄旗和红旗同时亮时只显示红旗，一格只装得下一个。
     * 这一格不写字，只挂 data-f 让 CSS 把颜色填进去（颜色比单词快，
     * 余光里就能看出是黄旗还是红旗）。
     * 旗是实时状态而不是会话元信息 → 非 streaming 一律清空，不能挂着上一场的红旗。 */
    var flagKey = null;
    if (live) {
      if (num(flags.red) > 0) flagKey = 'red';
      else if (num(flags.chequered) > 0) flagKey = 'cheq';
      else if (num(flags.yellow) > 0) flagKey = 'yellow';
      else if (num(flags.white) > 0) flagKey = 'white';
      else if (num(flags.green) > 0) flagKey = 'green';
    }
    setLvl('flag', '', 'data-f', flagKey);

    $('track').textContent = track.title || track.name || '—';

    /* 名次带总数：当前排名是主读数（大字号），参赛车总数只是参照（小字号）。
     * 总数来自 session.driversTotal（graphics.activeCars），拿不到时退回 P + 名次，
     * 这时 P 前缀同样走小字号。
     * HTML 里三个 span 常驻（posPre / posRank / posTotal），逐帧只写 textContent；
     * syncStripEmpty() 看的是 #pos 的整段文本，待机时仍恰好是 '—'，占位横线照常出现。 */
    var pos = P(lap.position);
    var field = P(session.driversTotal);
    if (pos === null || pos <= 0) {
      $('posPre').textContent = '';
      $('posRank').textContent = '—';
      $('posTotal').textContent = '';
    } else if (field && field > 0) {
      $('posPre').textContent = '';
      $('posRank').textContent = String(pos);
      $('posTotal').textContent = '/' + field;
    } else {
      $('posPre').textContent = 'P';
      $('posRank').textContent = String(pos);
      $('posTotal').textContent = '';
    }

    var lapNo = P(lap.num);
    var total = num(session.lapsTotal);
    $('lapNum').textContent = lapNo === null ? '—' : (total && total > 0 ? lapNo + '/' + total : String(lapNo));

    /* 油量那一格已从顶部挪到下方油量组，这里只留变量给后面的 setChip 用 */
    var fuel = P(car.fuel);

    /* --- 左上：天气 / 气温 / 路温 / 抓地 ---
     * 全部来自 env 段，都是实时值，非 streaming 一律占位（和右侧成绩板同一条约定）。
     * 文案是英文，和界面其它文本保持一致；enum 的完整中文释义在
     * contracts/telemetry-state.json 的 enums.rainIntensity / enums.grip 里。
     * 天气词表在文件上方 `WX_WORDS` 里，和图标数组同一处维护。 */
    var GRIP = ['GREEN', 'FAST', 'OPT', 'GREASY', 'DAMP', 'WET'];

    var rain = P(env.rainIntensity);
    var wLvl = rain === null ? null : Math.min(5, Math.max(0, Math.round(rain)));
    setWx(wLvl);

    var grip = P(env.grip);
    var gLvl = grip === null ? null : Math.min(5, Math.max(0, Math.round(grip)));
    setLvl('grip', gLvl === null ? '—' : GRIP[gLvl], 'data-g', gLvl);

    /* 只写数字：度数符号由 theme.css 的 .temp::after 画成几何正圆贴在数字右上，
     * 不进文本、不占布局宽度。原来 '26°' 整串居中时度号也要算一份宽度，
     * 数字会被挤到格中轴左边；而字体里那个 ° 本身是个略斜的小椭圆，
     * 夹在数字串末尾更显得整串歪。现在严格压中轴的只有数字。 */
    var air = P(env.airTemp);
    $('airTemp').textContent = air === null ? '—' : String(Math.round(air));

    var road = P(env.roadTemp);
    $('roadTemp').textContent = road === null ? '—' : String(Math.round(road));

    /* --- 中列下方：四个指示灯 ---
     * 左/右转向 → 闪黄；大灯 lightsStage>0 **或正在闪远光** → 黄常亮；
     * 雨刷 wiperLevel>0 → 蓝常亮。
     * 闪远光（拉灯杆）是瞬时动作，不改变 lightsStage —— 只看档位的话闪灯时
     * 图标完全不亮，正是之前漏掉的那一路。
     * 字段来自 ACC 共享内存的 graphics 块；老版本 sidecar 不推这些字段时
     * car.indicatorLeft 之类是 undefined，一律灭 —— 不会误亮。 */
    if (live) {
      setLamp('lpTurnL', car.indicatorLeft ? 'blink' : '0');
      setLamp('lpTurnR', car.indicatorRight ? 'blink' : '0');
      setLamp('lpLights', num(car.lightsStage) > 0 || car.flashingLights ? 'lamp' : '0');
      setLamp('lpWiper', num(car.wiperLevel) > 0 ? 'wipe' : '0');
    } else {
      setLamp('lpTurnL', '0');
      setLamp('lpTurnR', '0');
      setLamp('lpLights', '0');
      setLamp('lpWiper', '0');
    }

    /* --- 左：四轮 --- */
    var wheels = Array.isArray(st.wheels) ? st.wheels : [];
    for (var i = 0; i < tyres.length; i += 1) {
      var w = wheels[i] || {};
      var tc = tyres[i];
      var tVal = P(w.temp);
      var pVal = P(w.pressure);
      var bVal = P(w.brakeTemp);

      tc.t.textContent = tVal === null ? '–' : Math.round(tVal) + '°';
      /* 胎压不挂单位：这一块的主读数，psi 属于常识、写出来反而和数字抢空间 */
      tc.p.textContent = pVal === null ? '–' : pVal.toFixed(1);
      /* 刹车盘温度前挂 B（Brake），和胎温区分开：两个都是 °，靠前缀一眼看出是盘温 */
      tc.b.textContent = bVal === null ? 'B –' : 'B ' + Math.round(bVal) + '°';

      /* 胎温环：整圈常亮，只靠颜色表达温度（不再按温度延长弧长） */
      tc.arc.setAttribute('class', 'arc-fg ' + tempClass(tVal));

      /* 刹车盘温度环：270° 弧，颜色随盘温走；文字也同步变色 */
      var bLvl = brakeLevel(bVal);
      tc.brake.setAttribute('class', 'brake-fg b-' + bLvl);
      tc.b.dataset.level = bLvl;
    }

    /* --- 中：档位 --- */
    var gearEl = $('gear');
    var gt = live ? gearText(car.gear, car.gearText) : 'N';
    gearEl.textContent = gt;
    gearEl.className = gt === 'R' ? 'r' : gt === 'N' ? 'n' : '';

    /* --- 中：转速灯点（6 绿 → 2 黄 → 2 红，按比例点亮；到红线自然全红） --- */
    var maxRpm = num(car.maxRpmCurrent) || num(car.maxRpm) || 7600;
    var rpm = P(car.rpm);
    var ratio = rpm === null ? 0 : Math.max(0, Math.min(1, rpm / Math.max(1, maxRpm)));

    $('rpmText').textContent = rpm === null ? '—' : String(Math.round(rpm));

    var lit = Math.round(ratio * SHIFT_N);
    for (var k = 0; k < shiftLeds.length; k += 1) {
      var on = k < lit;
      shiftLeds[k].className = on ? (k >= 8 ? 'red' : k >= 6 ? 'warn' : 'on') : '';
    }

    /* 到换挡点（十颗全亮，即 95% 转速以上）整排一起闪。
     * 这才是换挡灯的本职：灯条从「转速进度」切成「该拉了」，
     * 眼睛不用再读数字判断还差多少转。 */
    var shiftRow = $('shift');
    if (shiftRow) shiftRow.classList.toggle('blink', rpm !== null && lit >= SHIFT_N);

    /* --- 中：速度 --- */
    var speed = P(car.speed);
    $('speed').textContent = speed === null ? '—' : String(Math.round(speed));

    /* --- 右：四行时间（赋值顺序跟 DOM 一致：CUR / EST / BEST / LAST） --- */

    /* CUR 是第一行：本圈已跑掉的时间，每帧都在跳。
     * 是实时值 → 走 P()，非 streaming 留占位。 */
    $('lapCur').textContent = fmtLap(P(lap.timeMs));

    /* EST 紧跟 CUR：按当前配速推算的收车成绩，和 CUR 是一对实时联动读数，
     * 中间不隔 BEST/LAST 才读得成一句话。同样非 streaming 留占位。 */
    $('lapEst').textContent = fmtLap(P(lap.estimatedMs));

    /* 本场最佳没有时退回历史 PB（PB 属会话元信息，待机时也保留）。 */
    var bestMs = P(lap.bestMs);
    var bestVal = bestMs !== null ? bestMs : num(lap.pbMs);
    var bestEl = $('lapBest');
    bestEl.textContent = fmtLap(bestVal);
    /* 绿色只在真有成绩时挂：空值染绿会让占位符 --:--.--- 看着像有效读数。
     * 判据要跟 fmtLap 一致（null / 非数 / <=0 都算空），否则 0 会让占位符变绿。 */
    bestEl.classList.toggle('best', bestVal != null && isFinite(bestVal) && bestVal > 0);

    /* 上圈是实时值，非 streaming 必须留空，不能挂着上一场的成绩。 */
    $('lapLast').textContent = fmtLap(P(lap.lastMs));

    /* 差值条：优先用 deltaBestMs（对游戏内本场最佳，和 HUD 的 DELTA 同口径）；
     * 老后端没有该字段时退回 deltaMs（对历史 PB），保证主题单独打开/开发预览也能用。
     * 底色三态 —— 无数据灰、更快绿、更慢红。只切 .delta-pill 的状态类，
     * 文字颜色交给 CSS（绿底上白字看不清，必须转深色）。 */
    var dBest = num(lap.deltaBestMs);
    var dMs = live ? (dBest !== null ? dBest : num(lap.deltaMs)) : null;
    $('delta').textContent = fmtDelta(dMs);
    var dPill = $('deltaPill');
    dPill.classList.remove('is-fast', 'is-slow');
    if (dMs !== null && dMs !== 0) dPill.classList.add(dMs < 0 ? 'is-fast' : 'is-slow');

    /* --- 底：数据方块 ---
     * TC / TC2 / ABS / MAP / 剩余油量 / 平均消耗 / 可跑圈数
     * 非 streaming 或字段为 null 一律留占位，别把上一场的冻结值当当前值。 */
    var tcLv = num(car.tcLevel);
    setChip('vTc', live && tcLv !== null ? (tcLv === 0 ? 'OFF' : String(tcLv)) : '—',
      !live || tcLv === null ? 'off' : num(car.tcActive) > 0.05 ? 'hot' : '');

    /* TC2 = 牵引力控制的第二级（TC Cut / 切断量），字段是 car.tcCutLevel。
     * 后端缺失时留占位；接上以后主题不用改就会显示。 */
    var tcCut = num(car.tcCutLevel);
    setChip('vTc2', live && tcCut !== null ? (tcCut === 0 ? 'OFF' : String(tcCut)) : '—',
      !live || tcCut === null ? 'off' : '');

    var absLv = num(car.absLevel);
    setChip('vAbs', live && absLv !== null ? (absLv === 0 ? 'OFF' : String(absLv)) : '—',
      !live || absLv === null ? 'off' : num(car.absActive) > 0.05 ? 'hot' : '');

    /* engineMap 是 0 基索引：共享内存 0 = 游戏里的 ECU Map 1。
     * 直接显示会整体小 1，所以这里 +1 对齐游戏内的档位编号。 */
    var map = P(car.engineMap);
    setChip('vMap', map === null ? '—' : String(Math.round(map) + 1), map === null ? 'off' : '');

    setChip('vFuel', fuel === null ? '—' : fuel.toFixed(1), fuel === null ? 'off' : '');

    /* 每圈油耗 / 可跑圈数：游戏不给时会回 0，0 当「没有」处理，别显示 0.00 */
    var cons = P(car.fuelPerLap);
    setChip('vCons', cons === null || cons <= 0 ? '—' : cons.toFixed(2),
      cons === null || cons <= 0 ? 'off' : '');

    var lapsLeft = P(car.fuelLapsLeft);
    setChip('vLaps', lapsLeft === null || lapsLeft <= 0 ? '—' : lapsLeft.toFixed(1),
      lapsLeft === null || lapsLeft <= 0 ? 'off' : '');

    syncStripEmpty();
  }

  /* ------------------------------ 数据来源 ------------------------------ */

  var framed = false;
  try { framed = !!(window.parent && window.parent !== window); } catch (e) { framed = false; }

  var gotState = false;

  function sayReady() {
    /* 幂等：宿主每次 init 都回一次 ready，补上「首次 ready 早于宿主监听」丢掉的那条 */
    try { window.parent.postMessage({ type: 'acc:ready' }, '*'); } catch (e) { /* ignore */ }
  }

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
      /* 一帧脏数据不该让面板停更：出错记一条，下一帧继续 */
      try { render(msg.payload); } catch (e) { console.warn('[theme] render failed:', e); }
    }
  });

  if (framed) {
    /* 有宿主就只等真实 state；后端没起来时保持占位，不跑演示数据 */
    sayReady();
  } else {
    startDemo();
  }

  /* ------------------------------ 演示数据 ------------------------------ */

  var demoTick = null;

  function startDemo() {
    stopDemo();
    var n = 0;
    demoTick = setInterval(function () {
      if (gotState) { stopDemo(); return; }
      n += 1;

      var ph = n / 9;
      var wave = Math.sin(ph);
      var speed = 186 + wave * 92;                 /* 94 ~ 278 */
      var decel = -Math.cos(ph);
      var brk = decel > 0.15 ? decel : 0;
      var thr = decel < -0.1 ? -decel : 0.06;
      var gear = speed > 250 ? 6 : speed > 212 ? 5 : speed > 172 ? 4 : speed > 130 ? 3 : 2;
      var rpm = 4600 + ((speed * 17) % 2900);

      /* 一轮 9 秒，圈时累加；胎温/刹车温随节奏呼吸 */
      var heat = 0.5 + 0.5 * Math.sin(n / 21);
      var bt = 330 + heat * 330;

      render({
        status: 'streaming',
        session: { type: 'practice', timeLeftMs: 412000 - n * 130, lapsTotal: 0, driversTotal: 20 },
        track: { title: 'Monza', name: 'monza', lengthM: 5793 },
        car: {
          name: 'Ferrari 488 GT3 Evo',
          speed: speed,
          gear: gear,
          gearText: String(gear),
          rpm: rpm,
          maxRpm: 7600,
          maxRpmCurrent: 7600,
          throttle: thr,
          brake: brk,
          steer: Math.sin(n / 6) * 0.5,
          fuel: 62.5 - n * 0.012,
          fuelPerLap: 2.85,
          fuelLapsLeft: 21.9,
          tcLevel: 3,
          tcCutLevel: 2,
          tcActive: brk > 0.55 ? 0 : 0,
          absLevel: 2,
          absActive: brk > 0.7 ? 0.7 : 0,
          engineMap: 1,
          /* 指示灯（演示数据让它们轮流亮，单开 index.html 就能看全四种状态） */
          indicatorLeft: n % 44 < 14,
          indicatorRight: false,
          lightsStage: n % 80 < 52 ? 1 : 0,
          /* 闪远光：lightsStage 关掉的时间段里闪一下，单开 index.html 能看到
             大灯图标在「档位 0」时也会亮 */
          flashingLights: n % 80 >= 70 && n % 80 < 74,
          wiperLevel: n % 80 >= 62 ? 1 : 0
        },
        lap: {
          num: 4,
          completed: 3,
          timeMs: 41230 + n * 90,
          lastMs: 92310,
          bestMs: 91980,
          pbMs: 91820,
          /* 游戏估算圈时：跟着节奏在最佳圈附近上下摆，单开 index.html 能看到 EST 行有数 */
          estimatedMs: 92160 + Math.round(wave * 760),
          deltaMs: wave * 640,
          deltaBestMs: wave * 640,
          position: 7,
          inPit: false,
          valid: true
        },
        wheels: [
          { corner: 'FL', pressure: 27.4, temp: 78 + heat * 8, brakeTemp: bt },
          { corner: 'FR', pressure: 27.6, temp: 81 + heat * 7, brakeTemp: bt + 22 },
          { corner: 'RL', pressure: 27.1, temp: 74 + heat * 9, brakeTemp: bt - 70 },
          { corner: 'RR', pressure: 27.2, temp: 94 + heat * 12, brakeTemp: bt - 40 }
        ],
        /* 演示里让天气/抓地每隔一段时间跳一档，单开 index.html 能看全三套配色 */
        env: {
          airTemp: 26 + Math.sin(n / 30) * 2,
          roadTemp: 31 + Math.sin(n / 30) * 3,
          rainIntensity: n % 160 < 90 ? 0 : (n % 160 < 130 ? 2 : 4),
          grip: n % 160 < 90 ? 2 : (n % 160 < 130 ? 4 : 3)
        },
        /* 演示数据：轮流亮绿 / 黄 / 红，单开 index.html 能看到旗语格的三套配色 */
        /* 演示数据：绿 → 黄 → 红 → 格子旗 → 无旗 依次轮换，
         * 单开 index.html 能把旗语格的所有填充色都看一遍 */
        flags: {
          green: n % 170 < 80 ? 1 : 0,
          yellow: n % 170 >= 80 && n % 170 < 115 ? 1 : 0,
          red: n % 170 >= 115 && n % 170 < 130 ? 1 : 0,
          chequered: n % 170 >= 130 && n % 170 < 150 ? 1 : 0,
          trackStatus: 'GREEN'
        }
      });
    }, 70);
  }

  function stopDemo() {
    if (demoTick !== null) { clearInterval(demoTick); demoTick = null; }
  }
})();
