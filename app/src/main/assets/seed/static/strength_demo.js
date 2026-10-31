/* Animated stick-figure demos for the strength exercises, side and front view.
 *
 *   HectorDemo.mount(element, key)   → draws both views + a pause button
 *   HectorDemo.info[key]             → { name, steps[], mistake }
 *
 * Side view faces right, ground at y=186. Joints: h head, s shoulder, e elbow,
 * w wrist, hp hip, k knee, a ankle; optional far leg k2/a2/f2, toe f, and bar
 * (plate centre, defaults to the wrist). Front view: h is absolute, every other
 * joint is [distance from the centre line, y], mirrored to both sides, and bar
 * is the bar's height.
 */
(function () {
  function pose(base, over) { var p = {}, k; for (k in base) p[k] = base[k]; for (k in over) p[k] = over[k]; return p; }

  var STAND = { h:[102,30], s:[100,44], e:[101,74], w:[102,104], hp:[100,98], k:[101,140], a:[100,180], bar:[102,106] };
  var DL_BOTTOM = { h:[125,95], s:[115,104], e:[111,136], w:[106,168], hp:[72,132], k:[114,148], a:[100,180], bar:[106,170] };
  var RACK = pose(STAND, { h:[100,31], s:[100,46], e:[122,58], w:[108,42], bar:[110,42] });
  var LYING = { h:[34,170], s:[50,176], hp:[108,178], k:[140,148], a:[160,180] };
  var KNEEL = { k:[70,180], a:[42,180], f:[34,186] };

  var F_STAND = { h:[100,30], s:[22,46], e:[25,74], w:[24,102], hp:[14,98], k:[15,140], a:[16,180], bar:104 };
  var F_FLOOR = { h:[100,92], s:[22,104], e:[24,136], w:[24,166], hp:[15,128], k:[22,148], a:[16,180], bar:168 };
  var F_RACK = { h:[100,30], s:[22,46], e:[20,62], w:[27,46], hp:[14,98], k:[15,140], a:[16,180], bar:44 };
  var F_LYING = { h:[100,24], s:[24,46], hp:[16,110], k:[18,138], a:[18,158] };

  var EX = {
    clean: {
      name: 'Clean to shoulders',
      side: [DL_BOTTOM, STAND, RACK],
      front: { poses: [F_FLOOR, F_STAND, F_RACK] },
      steps: ['Stand with the bar over the middle of your feet. Bend your knees, grip just outside your legs, keep your back flat and stand up with the bar.',
              'Slide the bar to mid-thigh, then jump it up: drive with your legs, shrug and pull your elbows high.',
              'Whip your elbows forward under the bar and catch it on the front of your shoulders.',
              'To put it down, lower it to your thighs first, then to the floor.'],
      mistake: 'Curling the bar up with your arms. The legs do the work and the arms only guide it.'
    },
    fsquat: {
      name: 'Front squat',
      side: [RACK, { h:[104,80], s:[100,94], e:[124,104], w:[110,90], hp:[84,148], k:[124,150], a:[100,180], bar:[112,90] }],
      front: { poses: [F_RACK, { h:[100,80], s:[22,96], e:[20,110], w:[27,94], hp:[18,146], k:[30,150], a:[18,180], bar:92 }] },
      steps: ['Clean the bar to your shoulders. Rest it on your shoulders with your fingertips under it and your elbows pointing forward.',
              'Feet shoulder-width apart, toes turned out slightly.',
              'Sit straight down between your heels until your thighs are about level with the floor, keeping your chest up.',
              'Drive up through your whole foot.'],
      mistake: 'Elbows dropping, which rolls the bar forward. Lead the way up with your elbows.'
    },
    floorpress: {
      name: 'Floor press',
      side: [pose(LYING, { e:[52,148], w:[52,120], bar:[52,120] }), pose(LYING, { e:[62,182], w:[64,152], bar:[64,150] })],
      front: { label: 'From above', ground: false,
               poses: [pose(F_LYING, { e:[30,50], w:[28,46], bar:46 }), pose(F_LYING, { e:[44,64], w:[30,52], bar:52 })] },
      steps: ['Lie on your back with your knees bent. Pull the bar over your hips (pick it up, sit down and roll back).',
              'Hands a little wider than shoulder-width, arms straight above your chest.',
              'Lower the bar until your upper arms touch the floor, elbows about 45° from your body.',
              'Pause for a moment, then press it back up.'],
      mistake: 'Bouncing your elbows off the floor. Let them settle and press from a dead stop.'
    },
    row: {
      name: 'Bent-over row',
      side: [{ h:[132,72], s:[122,80], e:[122,110], w:[120,140], hp:[76,108], k:[108,145], a:[100,180], bar:[120,140] },
             { h:[132,72], s:[122,80], e:[98,82], w:[112,106], hp:[76,108], k:[108,145], a:[100,180], bar:[112,106] }],
      front: { poses: [{ h:[100,74], s:[22,84], e:[24,112], w:[26,140], hp:[15,110], k:[16,146], a:[16,180], bar:142 },
                       { h:[100,74], s:[22,84], e:[34,96], w:[26,108], hp:[15,110], k:[16,146], a:[16,180], bar:110 }] },
      steps: ['Pick the bar up from the floor with a flat back, unlock your knees and hinge forward until your torso is about 45° to the floor.',
              'Let the bar hang below your shoulders with your back flat.',
              'Pull the bar to your belly button, driving your elbows back.',
              'Lower it under control without standing up.'],
      mistake: 'Standing more upright on every rep to swing the weight up. Lower the weight if you need to.'
    },
    rdl: {
      name: 'Romanian deadlift',
      side: [STAND, { h:[134,76], s:[122,84], e:[116,112], w:[108,140], hp:[74,102], k:[106,142], a:[100,180], bar:[108,140] }],
      front: { poses: [pose(F_STAND, { w:[22,102] }), { h:[100,80], s:[22,88], e:[23,114], w:[22,140], hp:[15,104], k:[15,142], a:[16,180], bar:142 }] },
      steps: ['Start standing, holding the bar at your thighs, with your knees slightly bent.',
              'Push your hips back as if closing a car door with your bum. The bar slides down your thighs.',
              'Stop at mid-shin, or where you feel a strong stretch behind your thighs, keeping your back flat.',
              'Squeeze your glutes and push your hips forward to stand up.'],
      mistake: 'Rounding your lower back to reach lower. Range of motion ends when your back starts to round.'
    },
    ohp: {
      name: 'Overhead press',
      side: [pose(RACK, { h:[96,31] }), pose(STAND, { s:[100,46], e:[101,18], w:[101,-10], bar:[101,-12] })],
      front: { poses: [F_RACK, pose(F_STAND, { e:[24,18], w:[25,-10], bar:-12 })] },
      steps: ['Clean the bar to your shoulders, with your hands just outside them.',
              'Squeeze your glutes and stomach so your body is one solid piece.',
              'Press the bar straight up, moving your head back to let it pass, then push your head through once it is above you.',
              'Lock your arms out over the middle of your feet, then lower it to your shoulders.'],
      mistake: 'Leaning back and turning it into a standing bench press. Stay tight and lower the weight.'
    },
    rollout: {
      name: 'Barbell rollout',
      side: [pose(KNEEL, { h:[134,108], s:[124,116], e:[124,142], w:[122,168], hp:[80,140], bar:[122,170] }),
             pose(KNEEL, { h:[144,144], s:[132,150], e:[158,158], w:[180,168], hp:[90,158], bar:[180,170] })],
      front: { noFeet: true,
               poses: [{ h:[100,58], s:[22,72], e:[24,120], w:[26,166], hp:[15,122], k:[15,180], a:[15,180], bar:170 },
                       { h:[100,128], s:[24,140], e:[26,156], w:[28,168], hp:[15,150], k:[15,180], a:[15,180], bar:170 }] },
      steps: ['Load small plates and kneel on a mat with your hands on the bar under your shoulders.',
              'Tighten your stomach and squeeze your glutes.',
              'Roll the bar forward as far as you can without your lower back sagging.',
              'Pull it back with your stomach muscles, not your arms.'],
      mistake: 'Your back arching as you go out. Make the rollout shorter until you can keep it flat.'
    }
  };

  var NS = 'http://www.w3.org/2000/svg';
  function el(tag, attrs, parent) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function setLine(l, p, q) { l.setAttribute('x1', p[0]); l.setAttribute('y1', p[1]); l.setAttribute('x2', q[0]); l.setAttribute('y2', q[1]); }

  function buildSide(svg) {
    el('line', { x1: 0, y1: 187, x2: 200, y2: 187, 'class': 'sd-ground' }, svg);
    var g = el('g', { 'class': 'sd-fig' }, svg), parts = {};
    ['thigh2', 'shin2', 'foot2'].forEach(function (n) { parts[n] = el('line', { 'class': 'far' + (n === 'foot2' ? ' foot' : '') }, g); });
    ['torso', 'neck', 'thigh', 'shin', 'foot', 'upper', 'fore'].forEach(function (n) { parts[n] = el('line', { 'class': n === 'foot' ? 'foot' : '' }, g); });
    parts.head = el('circle', { r: 9, 'class': 'head' }, g);
    parts.plate = el('circle', { r: 16, 'class': 'plate' }, g);
    parts.hub = el('circle', { r: 3.5, 'class': 'hub' }, g);
    return parts;
  }
  function drawSide(parts, P) {
    var a = P.a, f = P.f || [a[0] + 12, a[1] + 6], heel = P.f ? a : [a[0] - 4, a[1] + 6];
    var hasFar = !!P.k2;
    parts.thigh2.style.display = parts.shin2.style.display = parts.foot2.style.display = hasFar ? '' : 'none';
    if (hasFar) { setLine(parts.thigh2, P.hp, P.k2); setLine(parts.shin2, P.k2, P.a2); setLine(parts.foot2, P.a2, P.f2 || [P.a2[0] + 12, P.a2[1] + 6]); }
    setLine(parts.torso, P.hp, P.s);
    setLine(parts.neck, P.s, P.h);
    setLine(parts.thigh, P.hp, P.k);
    setLine(parts.shin, P.k, P.a);
    setLine(parts.foot, heel, f);
    setLine(parts.upper, P.s, P.e);
    setLine(parts.fore, P.e, P.w);
    parts.head.setAttribute('cx', P.h[0]); parts.head.setAttribute('cy', P.h[1]);
    var bar = P.bar || P.w;
    parts.plate.setAttribute('cx', bar[0]); parts.plate.setAttribute('cy', bar[1]);
    parts.hub.setAttribute('cx', bar[0]); parts.hub.setAttribute('cy', bar[1]);
  }

  function buildFront(svg, opts) {
    if (opts.ground !== false) el('line', { x1: 0, y1: 187, x2: 200, y2: 187, 'class': 'sd-ground' }, svg);
    var g = el('g', { 'class': 'sd-fig' }, svg), parts = { sides: [] };
    [-1, 1].forEach(function (sign) {
      var p = { sign: sign };
      ['thigh', 'shin', 'foot', 'upper', 'fore'].forEach(function (n) { p[n] = el('line', { 'class': n === 'foot' ? 'foot' : '' }, g); });
      if (opts.noFeet) p.foot.style.display = 'none';
      parts.sides.push(p);
    });
    ['hips', 'shoulders', 'spine', 'neck'].forEach(function (n) { parts[n] = el('line', {}, g); });
    parts.head = el('circle', { r: 9, 'class': 'head' }, g);
    parts.bar = el('line', { 'class': 'barline' }, g);
    parts.plates = [el('rect', { width: 8, height: 32, rx: 2, 'class': 'plate' }, g),
                    el('rect', { width: 8, height: 32, rx: 2, 'class': 'plate' }, g)];
    return parts;
  }
  function drawFront(parts, P) {
    function at(j, sign) { return [100 + sign * P[j][0], P[j][1]]; }
    parts.sides.forEach(function (p) {
      var s = p.sign, a = at('a', s);
      setLine(p.thigh, at('hp', s), at('k', s));
      setLine(p.shin, at('k', s), a);
      setLine(p.foot, [a[0] - s * 2, a[1] + 6], [a[0] + s * 8, a[1] + 6]);
      setLine(p.upper, at('s', s), at('e', s));
      setLine(p.fore, at('e', s), at('w', s));
    });
    setLine(parts.hips, at('hp', -1), at('hp', 1));
    setLine(parts.shoulders, at('s', -1), at('s', 1));
    setLine(parts.spine, [100, P.s[1]], [100, P.hp[1]]);
    setLine(parts.neck, [100, P.s[1]], P.h);
    parts.head.setAttribute('cx', P.h[0]); parts.head.setAttribute('cy', P.h[1]);
    setLine(parts.bar, [24, P.bar], [176, P.bar]);
    parts.plates[0].setAttribute('x', 26); parts.plates[1].setAttribute('x', 166);
    parts.plates.forEach(function (r) { r.setAttribute('y', P.bar - 16); });
  }

  function mix(A, B, t) {
    var out = {};
    for (var k in A) {
      var a = A[k], b = B[k] === undefined ? a : B[k];
      out[k] = typeof a === 'number' ? a + (b - a) * t : [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    }
    if (!A.bar && B.bar) out.bar = B.bar;
    return out;
  }

  var MOVE = 1100, HOLD = 450;
  var demos = [];
  var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  function mount(host, key) {
    var x = EX[key];
    if (!host || !x) return null;
    var fl = x.front.label || 'Front';
    host.classList.add('sd-demo');
    host.innerHTML =
      '<div class="sd-views">' +
        '<figure><svg class="sd-side" viewBox="0 -30 200 222" role="img" aria-label="Side view of the ' + x.name.toLowerCase() + '"></svg><figcaption>Side</figcaption></figure>' +
        '<figure><svg class="sd-front" viewBox="0 -30 200 222" role="img" aria-label="' + fl + ' view of the ' + x.name.toLowerCase() + '"></svg><figcaption>' + fl + '</figcaption></figure>' +
      '</div>' +
      '<button type="button" class="sd-toggle">' + (reduce ? 'Play' : 'Pause') + '</button>';
    var side = host.querySelector('.sd-side'), front = host.querySelector('.sd-front');
    // Ping-pong through the poses: 0 → 1 → … → n-1 → … → 1.
    var seq = x.side.map(function (_, i) { return i; });
    for (var i = x.side.length - 2; i > 0; i--) seq.push(i);
    var d = { host: host, sp: buildSide(side), fp: buildFront(front, x.front), x: x, seq: seq,
              playing: !reduce, t0: performance.now(), elapsed: 0 };
    drawSide(d.sp, x.side[0]);
    drawFront(d.fp, x.front.poses[0]);
    var btn = host.querySelector('.sd-toggle');
    function toggle() {
      d.playing = !d.playing;
      if (d.playing) d.t0 = performance.now() - d.elapsed;
      btn.textContent = d.playing ? 'Pause' : 'Play';
    }
    btn.addEventListener('click', toggle);
    side.addEventListener('click', toggle);
    front.addEventListener('click', toggle);
    demos.push(d);
    return d;
  }

  function ease(t) { return 0.5 - 0.5 * Math.cos(Math.PI * t); }
  function frame(now) {
    requestAnimationFrame(frame);  // first, so one bad frame can never stop the loop
    demos = demos.filter(function (d) { return d.host.isConnected; });
    demos.forEach(function (d) {
      if (!d.playing) return;
      // The frame's timestamp can be older than the moment Play was pressed.
      d.elapsed = Math.max(0, now - d.t0);
      var seg = MOVE + HOLD, n = d.seq.length;
      var pos = d.elapsed % (seg * n);
      var idx = Math.floor(pos / seg), within = pos - idx * seg;
      var i0 = d.seq[idx], i1 = d.seq[(idx + 1) % n];
      var t = within < HOLD ? 0 : ease((within - HOLD) / MOVE);
      drawSide(d.sp, mix(d.x.side[i0], d.x.side[i1], t));
      drawFront(d.fp, mix(d.x.front.poses[i0], d.x.front.poses[i1], t));
    });
  }
  requestAnimationFrame(frame);

  var info = {};
  Object.keys(EX).forEach(function (k) { info[k] = { name: EX[k].name, steps: EX[k].steps, mistake: EX[k].mistake }; });
  window.HectorDemo = { mount: mount, info: info };
})();
