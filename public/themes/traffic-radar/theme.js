/* 交通雷达 HUD —— 主题侧脚本。
 *
 * 宿主 → 主题：{ type:'acc:init' } / { type:'acc:ui' } / { type:'acc:state', payload }
 * 主题 → 宿主：{ type:'acc:ready' }（每次 init 都要回一条，别用「只发一次」开关）
 *
 * 数据只用契约里已有的字段，不改后端、不连 WS、不读文件：
 *   对手：standings.rows[].x / .z（广播，默认 ≈10Hz，米制世界坐标）
 *   本车：position.x / .z（共享内存，30Hz 信封，同一坐标系）
 *   真机探针 python-server/tests/fixtures/broadcast/capture-01/report.md 实测
 *   bcX = +sm.x、bcY = +sm.z，残差 0.000m —— 直接相减就是真实米数，
 *   不需要 splinePosition、赛道长度或赛道几何。
 *
 * 方向：不用 position.heading 的符号约定（acc_provider.py:187-189 的注释明写
 * 「符号约定以曲线图观感为准，仅用于历史展示」，不能当真值）。这里改用
 * **本车相邻两帧的坐标差分**当车头前向，一阶低通平滑后归一化；静止 / 首帧
 * 才拿 heading 兜底。这样自动绕开 ACC heading 的正负与轴向歧义，
 * 低速时保持上一次结果，位移 >60m 视为传送重置、不参与校准。
 * 左右镜像无法从位移自动推出，留常量 MIRROR，真机若发现左右反了改成 -1。
 *
 * 三个坑（都在这里处理）：
 *   1) isFinite(null) === true → 统一 num() 判空，stale / 坐标为 null 的车直接隐藏，
 *      绝不画成 (0,0) 变成「贴身车」；render() 整体包 try/catch。
 *   2) 广播 10Hz vs 本车 30Hz（200km/h 下最坏约 5.5m 时差）→ 相对位置 + 距离
 *      一阶低通（alpha 由主题设置「跟随速度」给出，默认 0.3），50m 警戒做滞回（进 alert / 出 alert+5），
 *      不做逐帧 CSS 动画，避免箭头抖、警戒闪。
 *   3) 折返赛道：同圈的两车直线很近但赛道距离差很大 → 用 |gapToSelfM| 过滤，
 *      阈值 = max(180, arrowDist + 60)m；维修区 / 车库车也排除。
 *      **套圈车不能用 gap 过滤**：gapToSelfM = (圈差 + spline差) × 赛道长度，
 *      差一圈就是 ±数千米，贴着你跑的套圈车会被永久过滤掉 → 圈数不同的车只受
 *      直线距离（arrowDist）约束。
 *
 * 背景不自己画：颜色 / 不透明度 / 模糊都作用于宿主窗口层，主题只在
 * 「不透明度 ≈100%」时把底板并成同一块 --host-bg；按亮度 × 不透明度
 * 写 html[data-host-light]，浅底把中性环线和对手车换成深灰。
 *
 * 双击 index.html（没有宿主）会自动跑一段演示车流：本车匀速前进、
 * 四辆对手车在 20~150m 之间靠近 / 远离，能直接看到圆环淡入、箭头出现、
 * 圆内小车、50m 警戒变红和中心数量变化。
 */
(function () {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var $ = function (id) { return document.getElementById(id); };
  function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

  var R = 150;                       // 外圆半径（圆心固定在 200,200，见 index.html）
  var MIN_MODEL_R = 42;              // 车模型最小半径：两车半高各 17 + 间隙 8
  var MAX_VISIBLE = 24;              // 一次最多画这么多支箭头
  // 相对位置 / 距离的一阶低通系数由主题设置「跟随速度」给出（默认 0.3，10Hz 下 ~90ms）
  var WARN_HYSTERESIS = 5;           // 警戒退出比进入多 5m
  var MIRROR = 1;                    // 真机左右镜像时改成 -1

  // 八套「实心主色 + 同色系稍亮描边 + 杆内数字 + 外圆」成对预设（v9 定稿）
  var PALETTES = {
    violet: { f: '#9B59B6', e: '#B07BC6', t: '#22112A', r: '#A66FC4' },
    blue: { f: '#4F8CFF', e: '#77A6FF', t: '#0C1F3D', r: '#4F8CFF' },
    red: { f: '#FF5A5F', e: '#FF8185', t: '#3A1012', r: '#FF5A5F' },
    teal: { f: '#00B894', e: '#33CFAE', t: '#062A22', r: '#00B894' },
    orange: { f: '#FF9F43', e: '#FFB96B', t: '#3A2208', r: '#FF9F43' },
    pink: { f: '#FD79A8', e: '#FD97BB', t: '#3A1225', r: '#FD79A8' },
    white: { f: '#F1F5F9', e: '#C9D2E0', t: '#2D3436', r: '#64748B' },
    ink: { f: '#1E272E', e: '#44505E', t: '#F5F7FA', r: '#64748B' }
  };
  // 警戒独立珊瑚红，不随所选配色变（安全语义）
  var WARN = { f: '#FF4D4F', e: '#FF8185', t: '#3A1012' };
  var FAR_TEXT = '#334155';

  // 箭头 path 以尖端 (0,0) 为原点、杆向负 x 延伸；整支 g 用
  // rotate(θ) translate(R,0) 让尖端恒落在圆周上、以圆心为轴转。
  // 形状改了，数字锚点 x 必须一起改（杆区间中点），否则距离文字会飘离箭杆。
  var SHAPES = {
    stubby: { d: 'M -80,-14 L -32,-14 L -32,-22 L 0,0 L -32,22 L -32,14 L -80,14 Z', x: -56 },
    slim: { d: 'M -96,-10 L -36,-10 L -36,-17 L 0,0 L -36,17 L -36,10 L -96,10 Z', x: -66 }
  };

  // 宿主会按 theme.json 的 schema 补齐默认值；这里再写一份，离线演示也能用。
  var cfg = {
    arrowPalette: 'violet',
    arrowOpacity: 100,
    arrowShape: 'stubby',
    arrowResponse: 30,
    unitSize: 'small',
    arrowDist: 150,
    modelDist: 60,
    alertDist: 50,
    firstLap: 'show',
    countPos: 'inside'
  };

  var REASONS = {
    'port-not-configured': '请在 ACC 的 broadcasting.json 开启 UDP 广播端口',
    'disabled': '遥测设置里已关闭广播',
    'connecting': '正在连接 ACC 广播…',
    'timeout': '广播超时，请确认游戏在赛道上',
    'no-session': '等待进入赛道',
    'no-data': '等待广播数据…',
    'error': '广播连接异常，请查看后端日志'
  };

  var root = $('radar');
  var outer = $('outer'), mid = $('mid'), inner = $('inner');
  var arrowsG = $('arrows'), carsG = $('cars');
  var selfCount = $('self-count'), badgeText = $('badge-text');
  var noticeGroup = $('notice-group'), notice = $('notice');

  var pool = [];                 // 复用的箭头 / 小车节点，避免 30Hz 重建 DOM
  var smooth = new Map();        // carId → { right, fwd, d, warn, t }：低通 + 警戒滞回
  var F = { x: 0, z: 1 };        // 本车前向单位向量（世界坐标）
  var vel = { x: 0, z: 0 };      // 前向的低通累积
  var lastPos = null;

  /* ---------------------------- 节点池 ---------------------------- */

  function createNode() {
    var sh = SHAPES[cfg.arrowShape];

    var g = document.createElementNS(SVG_NS, 'g');
    var path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', sh.d);
    path.setAttribute('stroke-width', '3');
    path.setAttribute('stroke-linejoin', 'round');
    g.appendChild(path);

    // 数字 14px、单位 m 11px 拆两个 tspan；transform 挂在 text 上，
    // tspan 自动继承反向旋转，切单位字号只要改 font-size。
    var text = document.createElementNS(SVG_NS, 'text');
    // 锚点必须跟着箭头形状走（杆区间中点）；applySettings 只在池里已有节点时
    // 更新，新建节点要在这里按当前 cfg 自己设一次，否则首帧数字会跑到尖端。
    text.setAttribute('x', sh.x);
    text.setAttribute('y', '5');
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('font-weight', '500');
    var tn = document.createElementNS(SVG_NS, 'tspan');
    tn.setAttribute('font-size', '14');
    tn.textContent = '0';
    var tu = document.createElementNS(SVG_NS, 'tspan');
    tu.setAttribute('font-size', cfg.unitSize === 'same' ? '14' : '11');
    tu.setAttribute('dx', '1');
    tu.textContent = 'm';
    text.appendChild(tn);
    text.appendChild(tu);
    g.appendChild(text);
    arrowsG.appendChild(g);

    // 圆内对手车：18×34 竖向圆角矩形 + 顶部风挡短线，与本车完全同尺寸
    var cg = document.createElementNS(SVG_NS, 'g');
    var rect = document.createElementNS(SVG_NS, 'rect');
    rect.setAttribute('class', 'opponent-body');
    rect.setAttribute('x', '-9');
    rect.setAttribute('y', '-17');
    rect.setAttribute('width', '18');
    rect.setAttribute('height', '34');
    rect.setAttribute('rx', '5');
    rect.setAttribute('fill', '#888780');
    rect.setAttribute('stroke', '#475569');
    rect.setAttribute('stroke-opacity', '0.5');
    rect.setAttribute('stroke-width', '1');
    var wind = document.createElementNS(SVG_NS, 'path');
    wind.setAttribute('d', 'M -5,-11 L 5,-11');
    wind.setAttribute('stroke', '#2C2C2A');
    wind.setAttribute('stroke-opacity', '0.5');
    wind.setAttribute('stroke-width', '1.5');
    wind.setAttribute('stroke-linecap', 'round');
    cg.appendChild(rect);
    cg.appendChild(wind);
    carsG.appendChild(cg);

    return { g: g, path: path, text: text, num: tn, unit: tu, car: cg, shapeX: sh.x };
  }

  function ensurePool(n) {
    while (pool.length < n) pool.push(createNode());
  }

  /* ---------------------------- 设置项 ---------------------------- */

  // 静态属性（path / 锚点 / 单位字号 / 环半径）只在设置变化时改，不占每帧开销；
  // 角度、颜色、距离这些逐帧量留给 render()。
  function applySettings(s) {
    if (!s || typeof s !== 'object') return;
    if (PALETTES[s.arrowPalette]) cfg.arrowPalette = s.arrowPalette;
    if (SHAPES[s.arrowShape]) cfg.arrowShape = s.arrowShape;
    cfg.unitSize = s.unitSize === 'same' ? 'same' : 'small';
    cfg.countPos = s.countPos === 'label' ? 'label' : 'inside';
    cfg.firstLap = s.firstLap === 'hide' ? 'hide' : 'show';

    var op = num(s.arrowOpacity);
    var ad = num(s.arrowDist), md = num(s.modelDist), wd = num(s.alertDist);
    var ar = num(s.arrowResponse);
    cfg.arrowOpacity = op == null ? 100 : clamp(op, 0, 100);
    cfg.arrowDist = ad == null ? 150 : clamp(ad, 40, 400);
    cfg.modelDist = md == null ? 60 : clamp(md, 10, 400);
    cfg.alertDist = wd == null ? 50 : clamp(wd, 10, 400);
    // 跟随速度 = 低通 alpha ×100。下限 0.2 保证不至于「粘住」，上限 0.9 基本等于直出。
    cfg.arrowResponse = ar == null ? 30 : clamp(ar, 20, 90);

    var sh = SHAPES[cfg.arrowShape];
    var unitSize = cfg.unitSize === 'same' ? '14' : '11';
    for (var i = 0; i < pool.length; i++) {
      var p = pool[i];
      p.path.setAttribute('d', sh.d);
      p.text.setAttribute('x', sh.x);
      p.unit.setAttribute('font-size', unitSize);
      p.shapeX = sh.x;
    }

    root.setAttribute('data-count-pos', cfg.countPos);
    outer.setAttribute('stroke', PALETTES[cfg.arrowPalette].r);
    // 外圆 r 恒 150 且恒等于 arrowDist → 比例尺 k = 150 / arrowDist
    var k = R / cfg.arrowDist;
    mid.setAttribute('r', clamp(Math.min(cfg.modelDist, cfg.arrowDist) * k, 30, R - 4).toFixed(1));
    inner.setAttribute('r', clamp(Math.min(cfg.alertDist, cfg.modelDist, cfg.arrowDist) * k, 26, R - 5).toFixed(1));
  }

  /* ------------------------ 宿主明暗 / 协议 ------------------------ */

  var hostBgColor = '#0b0f14';
  var hostBgOpacity = 0;
  var hostLight = null;

  function luminance(hex) {
    var m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return 0;
    var h = m[1];
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    return 0.2126 * parseInt(h.slice(0, 2), 16) / 255
      + 0.7152 * parseInt(h.slice(2, 4), 16) / 255
      + 0.0722 * parseInt(h.slice(4, 6), 16) / 255;
  }

  function syncHostTone() {
    var light = hostBgOpacity >= 0.3 && luminance(hostBgColor) > 0.6;
    if (light === hostLight) return;
    hostLight = light;
    document.documentElement.dataset.hostLight = light ? '1' : '0';
  }

  function applyUi(m) {
    if (m.theme) document.documentElement.dataset.theme = m.theme;
    if (m.settings) applySettings(m.settings);
    if (m.background === 'transparent' || m.background === 'opaque') {
      document.documentElement.dataset.bg = m.background;
    }
    if (typeof m.backgroundColor === 'string') {
      hostBgColor = m.backgroundColor;
      document.documentElement.style.setProperty('--host-bg', m.backgroundColor);
    }
    if (typeof m.backgroundOpacity === 'number') {
      hostBgOpacity = m.backgroundOpacity;
      document.documentElement.style.setProperty('--host-bg-opacity', String(m.backgroundOpacity));
    }
    if (typeof m.textColor === 'string') {
      document.documentElement.style.setProperty('--text-color', m.textColor);
    }
    syncHostTone();
  }

  /* --------------------- 本车前向（速度矢量） --------------------- */

  function updateForward(px, pz, heading) {
    if (px == null || pz == null) return;
    if (lastPos === null) {
      // 首帧还没有位移，用 heading 的 (cos, sin) 兜底；几帧后就被速度矢量替换
      var h0 = num(heading);
      if (h0 != null) { F.x = Math.cos(h0); F.z = Math.sin(h0); }
      lastPos = { x: px, z: pz };
      return;
    }
    var dx = px - lastPos.x, dz = pz - lastPos.z;
    lastPos = { x: px, z: pz };
    var len = Math.sqrt(dx * dx + dz * dz);
    if (len < 0.05) return;        // 静止：保持上一次前向，不把噪声当方向
    if (len > 60) return;          // 传送 / 换场：只重置基准，不污染前向
    vel.x += (dx / len - vel.x) * 0.3;
    vel.z += (dz / len - vel.z) * 0.3;
    var l = Math.sqrt(vel.x * vel.x + vel.z * vel.z);
    if (l > 0.15) { F.x = vel.x / l; F.z = vel.z / l; }
  }

  /* ------------------------------ 渲染 ------------------------------ */

  function setAttrIfChanged(el, name, value) {
    if (el.getAttribute(name) !== value) el.setAttribute(name, value);
  }

  function setActive(on) {
    setAttrIfChanged(root, 'data-active', on ? '1' : '0');
  }

  function setStatus(s) {
    setAttrIfChanged(root, 'data-status', s || 'idle');
  }

  function showNotice(text) {
    notice.textContent = text;
    noticeGroup.style.display = '';
  }

  function hideNotice() {
    noticeGroup.style.display = 'none';
  }

  // 只把车收干净，保留本车前向基准：第一圈隐藏期间不能清 lastPos / vel，
  // 否则每帧都回到「首帧用 heading 兜底」，第二圈第一帧的朝向会跳一下。
  function hideCars() {
    for (var i = 0; i < pool.length; i++) {
      pool[i].g.style.display = 'none';
      pool[i].car.style.display = 'none';
    }
    setActive(false);
    selfCount.textContent = '0';
    badgeText.textContent = '0';
  }

  function hideAll() {
    hideCars();
    lastPos = null;
    vel.x = 0; vel.z = 0;
    smooth.clear();
  }

  function reasonText(reason) {
    return REASONS[reason] || '广播不可用，请检查 ACC 的 UDP 广播设置';
  }

  function render(st) {
    try {
      var live = st.status === 'streaming';
      setStatus(st.status);
      var stg = live ? (st.standings || null) : null;
      var pos = live ? (st.position || {}) : {};
      var px = num(pos.x), pz = num(pos.z);

      if (!live) { showNotice('等待遥测数据'); hideAll(); return; }
      if (!stg || stg.available === false) {
        showNotice(reasonText(stg && stg.reason));
        hideAll();
        return;
      }
      if (px == null || pz == null) { showNotice('本车坐标不可用'); hideAll(); return; }
      hideNotice();

      updateForward(px, pz, pos.heading);

      // 第一圈（还没跑完一圈）：发车密集，箭头糊成一片 → 设置里可整段不显示。
      // 只收车、不清前向基准，跨过起跑线后第一帧朝向就是对的。
      var lapState = st.lap || {};
      if (cfg.firstLap === 'hide' && num(lapState.completed) === 0) {
        showNotice('第一圈不显示');
        hideCars();
        return;
      }

      var rows = Array.isArray(stg.rows) ? stg.rows : [];
      var selfRow = stg.self || null;
      var selfId = selfRow ? num(selfRow.carId) : null;
      var selfLaps = selfRow ? num(selfRow.laps) : null;
      var selfSpline = selfRow ? num(selfRow.spline) : null;
      var trackM = num(stg.trackMeters);
      var arrowDist = cfg.arrowDist;
      var modelDist = Math.min(cfg.modelDist, arrowDist);
      var alert = Math.min(cfg.alertDist, modelDist);
      var gapLimit = Math.max(180, arrowDist + 60);
      var pal = PALETTES[cfg.arrowPalette];
      var opa = cfg.arrowOpacity / 100;

      var vis = [];
      for (var i = 0; i < rows.length && vis.length < MAX_VISIBLE; i++) {
        var row = rows[i];
        if (!row || row.stale === true) continue;
        var rx = num(row.x), rz = num(row.z);
        // stale 5s 后坐标被置 null：隐藏这辆车，绝不能画成 (0,0)
        if (rx == null || rz == null) continue;
        if (row.inPit === true) continue;
        var loc = row.location;
        if (loc && loc !== 'track' && loc !== 'unknown') continue;

        var gap = num(row.gapToSelfM);
        var cid = num(row.carId);
        if (selfId != null && cid === selfId) continue;
        // self 可能为 null（名册没回来）：自己那行 gap 恒 0，用它兜底排除
        if (selfId == null && gap === 0) continue;
        // 折返赛道 / 发卡弯两侧：同圈的车直线近但赛道距离差很大，不当「身边有车」。
        // 套圈车（圈数不同）gap 里含整圈差（±赛道长度），永远过不了阈值 →
        // 改用「归一化进度差 × 赛道长度」，只看两者在赛道上的相对位置。
        var rowSpline = num(row.spline);
        var splineM = null;
        if (selfSpline != null && rowSpline != null && trackM != null && trackM > 0) {
          var sd = rowSpline - selfSpline;
          if (sd > 0.5) sd -= 1; else if (sd < -0.5) sd += 1;
          splineM = Math.abs(sd) * trackM;
        }
        var lapGap = selfLaps != null && num(row.laps) != null ? num(row.laps) - selfLaps : 0;
        var trackGap = lapGap === 0 ? (gap != null ? Math.abs(gap) : splineM) : splineM;
        if (trackGap != null && trackGap > gapLimit) continue;

        var dx = rx - px, dz = rz - pz;
        var d = Math.sqrt(dx * dx + dz * dz);
        if (d > arrowDist || d < 0.5) continue;

        var fwd = dx * F.x + dz * F.z;
        var right = (dx * -F.z + dz * F.x) * MIRROR;
        vis.push({
          key: cid != null ? 'c' + cid : 'i' + i,
          d: d, fwd: fwd, right: right
        });
      }

      // 10Hz 广播的台阶用低通抹平；警戒做滞回，避免 50m 附近来回闪
      var now = Date.now();
      var seen = Object.create(null);
      for (var j = 0; j < vis.length; j++) {
        var v = vis[j];
        var s = smooth.get(v.key);
        if (!s) s = { right: v.right, fwd: v.fwd, d: v.d, warn: false, t: now };
        var a = cfg.arrowResponse / 100;
        s.right += (v.right - s.right) * a;
        s.fwd += (v.fwd - s.fwd) * a;
        s.d += (v.d - s.d) * a;
        s.t = now;
        if (!s.warn && v.d <= alert) s.warn = true;
        else if (s.warn && v.d > alert + WARN_HYSTERESIS) s.warn = false;
        smooth.set(v.key, s);
        v.s = s;
        seen[v.key] = 1;
      }
      smooth.forEach(function (s, key) {
        if (!seen[key] && now - s.t > 5000) smooth.delete(key);
      });

      ensurePool(vis.length);
      var farLine = arrowDist * 0.8;
      for (var k = 0; k < pool.length; k++) {
        var p = pool[k];
        if (k >= vis.length) {
          p.g.style.display = 'none';
          p.car.style.display = 'none';
          continue;
        }
        var item = vis[k], sm = item.s;
        var warn = sm.warn;
        var far = !warn && item.d >= farLine;
        var c = warn ? WARN : pal;
        // 屏幕 y 向下：px = cx + right·k，py = cy − fwd·k
        var ang = Math.atan2(-sm.fwd, sm.right) * 180 / Math.PI;

        p.g.style.display = '';
        p.g.setAttribute('transform', 'rotate(' + ang.toFixed(1) + ') translate(' + R + ',0)');
        // 「箭头不透明度」作用于整支箭头 + 杆内数字；警戒箭头留 0.8 下限，
        // 免得用户把透明度调低后连 50m 内的红色警示都看不清。
        p.g.style.opacity = warn ? Math.max(opa, 0.8) : opa;
        p.path.setAttribute('fill', c.f);
        p.path.setAttribute('stroke', c.e);
        p.path.setAttribute('fill-opacity', far ? '0.45' : '1');
        p.path.setAttribute('stroke-opacity', far ? '0.6' : '1');
        p.num.textContent = String(Math.round(sm.d));
        p.text.setAttribute('transform', 'rotate(' + (-ang).toFixed(1) + ' ' + p.shapeX + ' 0)');
        p.text.setAttribute('fill', far ? FAR_TEXT : c.t);

        var showCar = item.d <= modelDist;
        p.car.style.display = showCar ? '' : 'none';
        if (showCar) {
          var rr = Math.max(MIN_MODEL_R, R * sm.d / arrowDist);
          var rad = ang * Math.PI / 180;
          p.car.setAttribute('transform',
            'translate(' + (rr * Math.cos(rad)).toFixed(1) + ',' + (rr * Math.sin(rad)).toFixed(1) + ')');
        }
      }

      setActive(vis.length > 0);
      var countText = String(vis.length);
      selfCount.textContent = countText;
      badgeText.textContent = countText;
      // 两位数在 18px 车宽里偏紧，自动缩一档字号
      selfCount.setAttribute('font-size', vis.length >= 10 ? '11' : '14');
    } catch (e) {
      console.warn('[traffic-radar] render failed', e);
    }
  }

  /* ------------------------------ 协议 ------------------------------ */

  var hostSeen = false;
  var gotState = false;
  var lastSeq = null;
  var demoTick = null;
  var demoTimer = null;

  function sayReady() {
    try { window.parent.postMessage({ type: 'acc:ready' }, '*'); } catch (e) { /* 独立打开没有 parent */ }
  }

  window.addEventListener('message', function (ev) {
    var m = ev.data;
    if (!m || typeof m !== 'object') return;

    if (m.type === 'acc:init') {
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
      try {
        if (window.parent !== window) delete document.documentElement.dataset.standalone;
      } catch (e) { /* 取不到 parent 就保持原样，等 acc:init 来撤 */ }
      var seq = m.payload.seq == null ? null : m.payload.seq;
      if (seq != null && seq === lastSeq) return;
      lastSeq = seq;
      render(m.payload);
    }
  });

  /* ---------------------------- 离线演示 ---------------------------- */

  // 本车沿 +z 匀速前进 → 速度矢量前向 = (0,1)，屏幕上方就是车头方向。
  // 四辆对手车在 20~150m 之间靠近 / 远离，方位角轻微摆动。
  function demoFrame(t) {
    var selfX = 0, selfZ = t * 0.6;
    var defs = [
      { carId: 1, ang: -90, d: 42 },
      { carId: 2, ang: 35, d: 56 },
      { carId: 3, ang: 155, d: 128 },
      { carId: 4, ang: -20, d: 95 }
    ];
    var rows = [];
    for (var i = 0; i < defs.length; i++) {
      var o = defs[i];
      var dd = o.d + Math.sin((t + i * 40) / 55) * 26;
      if (dd < 12) dd = 12;
      var aa = (o.ang + Math.sin(t / 70 + i) * 12) * Math.PI / 180;
      var right = dd * Math.cos(aa);
      var fwd = -dd * Math.sin(aa);
      // F=(0,1)、R=(-1,0) → rel.x = -right，rel.z = fwd
      rows.push({
        carId: o.carId,
        raceNumber: 100 + i,
        x: selfX - right,
        z: selfZ + fwd,
        stale: false,
        location: 'track',
        inPit: false,
        gapToSelfM: fwd,
        laps: 3,
        speedKmh: 180
      });
    }
    return {
      status: 'streaming',
      position: { x: selfX, z: selfZ, heading: Math.PI / 2 },
      standings: {
        available: true,
        reason: null,
        self: { carId: 999 },
        rows: rows
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

  document.documentElement.dataset.standalone = '1';
  applySettings(cfg);
  render({ status: 'idle' });
  sayReady();
  demoTimer = setTimeout(function () {
    demoTimer = null;
    if (!hostSeen && !gotState) startDemo();
  }, 700);
})();
