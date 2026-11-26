// three.js scene templates for the ytauto HyperFrames renderer (deterministic: every frame is drawn
// from timeline time in the `hf-seek` listener; no clocks, no rAF, no Math.random).
// Templates: servers | globe | stack | bars. Spec per scene comes from the storyboard visual:
//   {"type":"three","template":"servers","nodes":["Users","Load balancer","Server A","Server B"]}
// Lighting/camera follow HyperFrames' 3d.md: warm key, cool rim, dark environment, contact shadow,
// one camera dive (not an orbit), labels with real words, roughness >= 0.45.

const ease = (x) => 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3);
const frac = (x) => x - Math.floor(x);
const hash = (i) => frac(Math.sin(i * 127.1 + 311.7) * 43758.5453);

function base(THREE, canvas, w, h, accents) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(1);
  renderer.setSize(w, h, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, w / h, 0.1, 200);
  if (w / h > 1.5) { camera.zoom = 1.55; camera.updateProjectionMatrix(); }   // wide 16:9 frame: fill it
  scene.add(new THREE.HemisphereLight('#b9ccff', '#140c24', 0.9));
  const key = new THREE.DirectionalLight('#ffd9a8', 2.4); key.position.set(4, 7, 5); scene.add(key);
  const rim = new THREE.DirectionalLight('#7fd4ff', 1.6); rim.position.set(-6, 3, -5); scene.add(rim);
  const shadow = new THREE.Mesh(new THREE.CircleGeometry(6, 48),
    new THREE.MeshBasicMaterial({ color: '#000000', transparent: true, opacity: 0.35 }));
  shadow.rotation.x = -Math.PI / 2; shadow.position.y = -1.6; scene.add(shadow);
  const mat = (c, extra = {}) => new THREE.MeshStandardMaterial({ color: c, roughness: 0.5, metalness: 0.15, ...extra });
  return { renderer, scene, camera, mat, accents };
}

function labeler(THREE, layer, camera, w, h) {
  const items = [];
  return {
    add(text, obj, dy = 0, cls = '') {
      const el = document.createElement('div');
      el.className = 'l3d ' + cls; el.textContent = text; layer.appendChild(el);
      items.push({ el, obj, dy });
      return el;
    },
    update() {
      const v = new THREE.Vector3();
      for (const it of items) {
        it.obj.getWorldPosition(v); v.y += it.dy; v.project(camera);
        it.x = (v.x + 1) / 2 * w; it.y = (1 - v.y) / 2 * h;
      }
      // keep labels apart: a label that overlaps an earlier one moves below it
      const placed = [];
      for (const it of [...items].sort((a, b) => a.y - b.y)) {
        const bw = it.el.offsetWidth || 120, bh = (it.el.offsetHeight || 40) + 6;
        let y = it.y;
        for (const p of placed) {
          if (Math.abs(p.x - it.x) < (p.w + bw) / 2 && Math.abs(p.y - y) < bh) y = p.y + bh;
        }
        placed.push({ x: it.x, y, w: bw });
        it.el.style.left = it.x + 'px'; it.el.style.top = y + 'px';
      }
    },
  };
}

// ---------------------------------------------------------------- servers: traffic through a load balancer
function servers(THREE, ctx, spec, L) {
  const { scene, camera, mat, accents } = ctx;
  const nodes = spec.nodes && spec.nodes.length >= 3 ? spec.nodes : ['Users', 'Load balancer', 'Server A', 'Server B', 'Server C'];
  const users = new THREE.Group(); users.position.set(-3.0, -1, 0); scene.add(users);
  for (let i = 0; i < 6; i++) {
    const p = new THREE.Mesh(new THREE.CapsuleGeometry(0.16, 0.35, 4, 12), mat('#e8ecf7'));
    p.position.set((i % 3) * 0.5 - 0.5, -0.2, Math.floor(i / 3) * 0.55 - 0.3); users.add(p);
  }
  const lb = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.45, 1.1), mat(accents[1]));
  lb.position.set(-0.6, -0.6, 0); scene.add(lb);
  const racks = nodes.slice(2).map((_, i, arr) => {
    const r = new THREE.Mesh(new THREE.BoxGeometry(0.9, 2.2, 0.9), mat('#1c2440', { metalness: 0.3 }));
    const z = (i - (arr.length - 1) / 2) * 2.0;
    r.position.set(2.2, -0.5, z); scene.add(r);
    for (let k = 0; k < 4; k++) {           // LED strips
      const led = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.05, 0.02),
        new THREE.MeshBasicMaterial({ color: accents[0] }));
      led.position.set(0, 0.8 - k * 0.45, 0.46); r.add(led);
    }
    return r;
  });
  L.add(nodes[0], users, 1.0); L.add(nodes[1], lb, 0.6, 'hl');
  racks.forEach((r, i) => L.add(nodes[2 + i], r, 1.35));
  const N = 18, packets = [];
  for (let i = 0; i < N; i++) {
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.09, 16, 12),
      new THREE.MeshBasicMaterial({ color: accents[i % 2 === 0 ? 0 : 2] }));
    scene.add(m); packets.push(m);
  }
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  return (t, dur) => {
    const grow = ease(t / 0.8);
    [lb, ...racks].forEach((o, i) => o.scale.setScalar(Math.max(0.001, ease((t - 0.15 * i) / 0.6))));
    users.scale.setScalar(Math.max(0.001, grow));
    packets.forEach((m, i) => {
      const p = frac(t * 0.55 + i / N);
      const target = racks[i % racks.length];
      a.copy(users.position).add(new THREE.Vector3(0, 0.1, 0));
      b.copy(lb.position).add(new THREE.Vector3(0, 0.3, 0));
      c.copy(target.position);
      if (p < 0.5) m.position.lerpVectors(a, b, p / 0.5); else m.position.lerpVectors(b, c, (p - 0.5) / 0.5);
      m.position.y += Math.sin(p * Math.PI) * 0.35;
      m.visible = t > 0.6;
    });
    // high camera so depth reads as height on a tall frame; one slow dive towards the load balancer
    const d = ease(t / Math.max(dur, 1));
    camera.position.set(-0.2, 7.2 - d * 1.4, 7.8 - d * 1.6);
    camera.lookAt(0, -0.8, 0);
  };
}

// ---------------------------------------------------------------- globe: users around the world
function globe(THREE, ctx, spec, L) {
  const { scene, camera, mat, accents } = ctx;
  const g = new THREE.Group(); scene.add(g);
  g.add(new THREE.Mesh(new THREE.SphereGeometry(2, 48, 32), mat('#1e3263', { roughness: 0.7 })));
  g.add(new THREE.Mesh(new THREE.SphereGeometry(2.01, 24, 16),
    new THREE.MeshBasicMaterial({ color: '#6f93d6', wireframe: true, transparent: true, opacity: 0.45 })));
  const pts = (spec.points && spec.points.length >= 2 ? spec.points : [
    { label: 'Mumbai', lat: 19.1, lon: 72.9 }, { label: 'Singapore', lat: 1.35, lon: 103.8 },
    { label: 'Frankfurt', lat: 50.1, lon: 8.7 }, { label: 'Virginia', lat: 38.9, lon: -77.4 }]);
  const pos = (lat, lon, r = 2.02) => {
    const phi = (90 - lat) * Math.PI / 180, th = (lon + 180) * Math.PI / 180;
    return new THREE.Vector3(-r * Math.sin(phi) * Math.cos(th), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(th));
  };
  const dots = pts.map((p, i) => {
    const d = new THREE.Mesh(new THREE.SphereGeometry(0.11, 12, 8), new THREE.MeshBasicMaterial({ color: accents[i % 4] }));
    d.position.copy(pos(p.lat, p.lon)); g.add(d); L.add(p.label, d, 0.25); return d;
  });
  const arcs = pts.slice(1).map((p, i) => {
    const s = pos(pts[0].lat, pts[0].lon), e = pos(p.lat, p.lon);
    const mid = s.clone().add(e).multiplyScalar(0.5).normalize().multiplyScalar(2.9);
    const curve = new THREE.QuadraticBezierCurve3(s, mid, e);
    const geo = new THREE.TubeGeometry(curve, 64, 0.04, 8, false);
    const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: accents[(i + 1) % 4] }));
    g.add(m);
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.05, 10, 8), new THREE.MeshBasicMaterial({ color: '#ffffff' }));
    g.add(head);
    return { m, geo, curve, head, total: geo.index.count };
  });
  // start turned so the points face the camera: centre between the origin and the mean longitude
  const lonMean = pts.reduce((a, p) => a + p.lon, 0) / pts.length;
  const latMean = pts.reduce((a, p) => a + p.lat, 0) / pts.length;
  const yaw0 = -((pts[0].lon + lonMean) / 2 + 90) * Math.PI / 180;
  g.rotation.x = latMean * 0.6 * Math.PI / 180;
  return (t) => {
    g.rotation.y = yaw0 + 0.25 - t * 0.06;   // slow linear turn (rotation is the motion here)
    arcs.forEach((a, i) => {
      const p = ease((t - 0.5 - i * 0.35) / 1.1);
      a.geo.setDrawRange(0, Math.floor(a.total * p / 6) * 6);
      a.head.visible = p > 0.02;
      a.head.position.copy(a.curve.getPoint(frac(t * 0.4 + i * 0.25)));
    });
    dots.forEach((d, i) => d.scale.setScalar(Math.max(0.001, ease((t - 0.2 - i * 0.15) / 0.4))));
    const d = ease(t / 6);
    camera.position.set(0, 0.6, 7.2 - d * 1.2); camera.lookAt(0, 0, 0);
  };
}

// ---------------------------------------------------------------- stack: exploded architecture layers
function stack(THREE, ctx, spec, L) {
  const { scene, camera, mat, accents } = ctx;
  const layers = spec.layers && spec.layers.length ? spec.layers : ['Client', 'API', 'Cache', 'Database'];
  const slabs = layers.map((name, i) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(3.2, 0.42, 2.2), mat(accents[i % 4]));
    scene.add(m); L.add(name, m, 0.05, 'side'); return m;
  });
  return (t) => {
    const open = ease((t - 0.4) / 1.2);
    slabs.forEach((m, i) => {
      const k = slabs.length - 1 - i;
      const appear = ease((t - 0.12 * k) / 0.5);
      m.position.set(0, -1.2 + k * (0.45 + open * 0.75), 0);
      m.scale.set(1, Math.max(0.001, appear), 1);
      m.rotation.y = -0.6 + open * 0.25;
    });
    camera.position.set(4.6, 2.4 + open * 0.8, 6.4); camera.lookAt(0, 0.4, 0);
  };
}

// ---------------------------------------------------------------- bars: 3D comparison with values
function bars(THREE, ctx, spec, L) {
  const { scene, camera, mat, accents } = ctx;
  const data = spec.bars && spec.bars.length ? spec.bars : [{ label: 'Before', value: 1 }, { label: 'After', value: 2 }];
  const max = Math.max(...data.map((d) => +d.value || 0), 1e-9);
  const cols = data.map((d, i) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(0.9, 1, 0.9), mat(accents[i % 4]));
    m.position.x = (i - (data.length - 1) / 2) * 1.5; scene.add(m);
    const anchor = new THREE.Object3D(); scene.add(anchor);
    const top = L.add('', anchor, 0.35, 'val');
    const base = new THREE.Object3D(); base.position.set(m.position.x, -1.75, 0); scene.add(base);
    L.add(d.label, base, -0.1, 'side');
    return { m, anchor, top, v: +d.value || 0 };
  });
  const unit = spec.unit || '', pre = spec.prefix || '';
  return (t) => {
    cols.forEach((c, i) => {
      const p = ease((t - 0.3 - i * 0.35) / 1.2);
      const hgt = Math.max(0.02, (c.v / max) * 3.2 * p);
      c.m.scale.y = hgt; c.m.position.y = -1.6 + hgt / 2;
      c.anchor.position.set(c.m.position.x, -1.6 + hgt, 0);
      const dec = c.v < 10 ? 1 : 0;
      c.top.textContent = pre + (c.v * p).toFixed(dec) + unit;
    });
    camera.position.set(1.6, 1.2, 7.4); camera.lookAt(0, 0, 0);
  };
}

const TEMPLATES = { servers, globe, stack, bars };

export function mountAll(THREE) {
  const scenes = [];
  document.querySelectorAll('[data-three]').forEach((host) => {
    const spec = JSON.parse(host.getAttribute('data-three'));
    const w = host.clientWidth || +host.dataset.w, h = host.clientHeight || +host.dataset.h;
    const canvas = document.createElement('canvas'); canvas.width = w; canvas.height = h; host.appendChild(canvas);
    const layer = document.createElement('div'); layer.className = 'l3d-layer'; host.appendChild(layer);
    const ctx = base(THREE, canvas, w, h, spec.accents || ['#22d3ee', '#fbbf24', '#f472b6', '#a3e635']);
    const L = labeler(THREE, layer, ctx.camera, w, h);
    const draw = (TEMPLATES[spec.template] || servers)(THREE, ctx, spec, L);
    scenes.push({ spec, ctx, draw, L });
  });
  const render = (time) => {
    for (const s of scenes) {
      const t = time - s.spec.t0;
      if (t < -0.05 || t > s.spec.dur + 0.05) continue;   // only the visible scene draws
      s.draw(Math.max(0, t), s.spec.dur);
      s.ctx.renderer.render(s.ctx.scene, s.ctx.camera);
      s.L.update();
    }
  };
  window.addEventListener('hf-seek', (e) => render(e.detail.time));
  render(0);
  return scenes.length;
}
