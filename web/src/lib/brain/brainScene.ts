// The Brain renderer: one glowing THREE.Points cloud (every record and edit),
// an optional faint scaffold and edit→record lines, on a transparent canvas so the
// stage's backdrop shows through. Loaded lazily with three.
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

/** Per-node attributes; positions are in layout units and get centred here. */
export type BrainCloud = {
  positions: Float32Array;
  colors: Float32Array;
  sizes: Float32Array;
  seeds: Float32Array;
  births: Float32Array;
  /** Shown group per node; -2 hides it. */
  groups: Float32Array;
};
export type BrainFrame = {
  center: [number, number, number];
  radius: number;
  turnAxis: "y" | "z";
  /** Home camera direction, radians. */
  view: { azimuth: number; elevation: number };
  lineAlpha: number;
  /** xyz per group: label positions in layout units. */
  anchors: Float32Array;
  /** xyz + radius per group when groups are spheres: hovering anywhere inside one finds it. */
  regions: Float32Array | null;
};
/**
 * How points meet what is behind the canvas: `cover` 0 adds light (a dark backdrop),
 * 1 lays soft opaque dots over it (a light one). `ink` tints the scaffold.
 */
export type BrainBackdrop = { cover: number; ink: [number, number, number] };
export type BrainSceneOptions = {
  reducedMotion: boolean;
  /** `group` is the shown group under the pointer: the sphere it is over, or the nearest point's. */
  onHover: (index: number | null, group: number | null, clientX: number, clientY: number) => void;
  onPick: (index: number | null, group: number | null) => void;
  /** After each drawn frame: overlays (labels) follow the camera here. */
  onFrame: () => void;
  onBirth: (done: boolean) => void;
};
export interface BrainScene {
  /** `morph` eases from the current layout instead of jumping. */
  setCloud(cloud: BrainCloud, frame: BrainFrame, morph: boolean): void;
  setLines(positions: Float32Array | null, colors: Float32Array | null): void;
  setScaffold(positions: Float32Array | null): void;
  setFocus(group: number | null): void;
  /** Highlights one group (others dim) while the pointer is over it. */
  setHover(group: number | null): void;
  setBackdrop(backdrop: BrainBackdrop): void;
  setMotion(turning: boolean, speed: number): void;
  setPointSize(scale: number): void;
  /** Pixels covered on the right (the open View panel): the figure is framed in the stage left of them. */
  setInset(right: number): void;
  setActive(active: boolean): void;
  /** Viewport position of node `index`, or null when it is behind the camera. */
  screenPoint(index: number): { x: number; y: number } | null;
  anchorPoint(group: number): { x: number; y: number } | null;
  resetView(): void;
  destroy(): void;
}

const BIRTH_MS = 2500;
const MORPH_MS = 1100;
const PICK_RADIUS = 10;
/** World size of a record point at pointSize 1; edits are a little smaller. */
const POINT_WORLD_SIZE = 0.038;
/** The scaffold is a faint shell, fainter still while a group is focused. */
const SCAFFOLD_ALPHA = 0.2;
const SCAFFOLD_FOCUSED_ALPHA = 0.07;

const VERTEX = /* glsl */ `
attribute vec3 from;
attribute vec3 color;
attribute float size;
attribute float seed;
attribute float birth;
attribute float grp;
uniform float uTime;
uniform float uMorph;
uniform float uBirth;
uniform float uSize;
uniform float uScale;
uniform float uFocus;
uniform float uHover;
uniform float uTwinkle;
uniform float uAlpha;
varying vec3 vColor;
varying float vAlpha;
void main() {
  vec3 p = mix(from, position, uMorph);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mv;
  float born = smoothstep(birth, birth + 0.05, uBirth);
  // A point flares briefly as it is born, then settles.
  float flare = 1.0 + 1.6 * born * (1.0 - smoothstep(birth + 0.02, birth + 0.16, uBirth));
  float focus = (uFocus < 0.0 || grp < -0.5 || abs(grp - uFocus) < 0.5) ? 1.0 : 0.06;
  float hover = (uHover < 0.0 || grp < -0.5) ? 1.0 : abs(grp - uHover) < 0.5 ? 1.3 : 0.28;
  float shown = grp < -1.5 ? 0.0 : 1.0;
  float twinkle = 1.0 + uTwinkle * 0.45 * sin(uTime * 2.1 + seed * 43.0);
  float px = size * uSize * uScale / max(0.001, -mv.z);
  // Sub-pixel points fade instead of aliasing into a grid of hard dots.
  float fade = clamp(px / 1.5, 0.0, 1.0);
  gl_PointSize = max(px, 1.5);
  vColor = color;
  vAlpha = uAlpha * born * flare * focus * hover * shown * twinkle * fade;
}`;
// Premultiplied output: with uCover 0 the alpha stays empty and colour adds up like light;
// with uCover 1 each point is a soft dot that covers (and is a little deeper than) a light backdrop.
const FRAGMENT = /* glsl */ `
uniform float uCover;
varying vec3 vColor;
varying float vAlpha;
void main() {
  vec2 c = gl_PointCoord - 0.5;
  float d = dot(c, c) * 4.0;
  if (d > 1.0) discard;
  float glow = (1.0 - d) * (1.0 - d);
  float a = min(1.0, glow * vAlpha);
  gl_FragColor = vec4(vColor * (1.0 - 0.3 * uCover) * a, a * uCover);
}`;

function cloudMaterial(alpha: number, twinkle: number) {
  return new THREE.ShaderMaterial({
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    uniforms: {
      uTime: { value: 0 }, uMorph: { value: 1 }, uBirth: { value: 2 }, uSize: { value: POINT_WORLD_SIZE },
      uScale: { value: 1 }, uFocus: { value: -1 }, uHover: { value: -1 }, uTwinkle: { value: twinkle }, uAlpha: { value: alpha },
      uCover: { value: 0 },
    },
    transparent: true,
    premultipliedAlpha: true,
    blending: THREE.CustomBlending,
    blendEquation: THREE.AddEquation,
    blendSrc: THREE.OneFactor,
    blendDst: THREE.OneMinusSrcAlphaFactor,
    blendSrcAlpha: THREE.OneFactor,
    blendDstAlpha: THREE.OneMinusSrcAlphaFactor,
    depthWrite: false,
    depthTest: false,
  });
}
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export function createBrainScene(host: HTMLElement, opts: BrainSceneOptions): BrainScene {
  const reduced = opts.reducedMotion;
  // Transparent: the stage (the theme's backdrop, or black) shows through.
  const renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true, premultipliedAlpha: true, powerPreference: "high-performance" });
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(Math.min(2, Math.max(1, window.devicePixelRatio || 1)));
  const canvas = renderer.domElement;
  canvas.style.cssText = "display:block;width:100%;height:100%;touch-action:none;outline:none";
  canvas.setAttribute("aria-hidden", "true");
  canvas.dataset.clearAlpha = String(renderer.getClearAlpha());
  host.append(canvas);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.05, 200);
  const pivot = new THREE.Group();
  scene.add(pivot);
  const controls = new OrbitControls(camera, canvas);
  controls.enablePan = false;
  controls.enableDamping = !reduced;
  controls.dampingFactor = 0.08;
  controls.rotateSpeed = 0.7;
  controls.zoomSpeed = 0.8;

  const material = cloudMaterial(0.9, reduced ? 0 : 1);
  const geometry = new THREE.BufferGeometry();
  const points = new THREE.Points(geometry, material);
  points.frustumCulled = false;
  pivot.add(points);
  let scaffold: THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial> | null = null;
  let lines: THREE.LineSegments<THREE.BufferGeometry, THREE.LineBasicMaterial> | null = null;

  let count = 0;
  let target = new Float32Array(0);
  let previous = new Float32Array(0);
  let births: Float32Array = new Float32Array(0);
  let groups: Float32Array = new Float32Array(0);
  let frame: BrainFrame = { center: [0, 0, 0], radius: 1, turnAxis: "y", view: { azimuth: 0, elevation: 0 }, lineAlpha: 0, anchors: new Float32Array(0), regions: null };
  let focus = -1;
  let hoverGroup = -1;
  let backdrop: BrainBackdrop = { cover: 0, ink: [0.62, 0.62, 0.62] };
  let turning = false;
  let speed = 1;
  let spin = 0;
  let spinAxis: "y" | "z" = "y";
  let birthStart: number | null = null;
  let birthDone = reduced;
  let morphStart: number | null = null;
  let morphSpin = 0;
  let cameraFrom: THREE.Vector3 | null = null;
  let cameraTo = new THREE.Vector3();
  let fitted = false;
  let relayout = false;
  let inset = 0;
  let viewWidth = 1;
  let viewHeight = 1;

  let active = true;
  let intersecting = true;
  let invalidated = true;
  let raf = 0;
  let last = 0;
  let pointer: { x: number; y: number } | null = null;
  let hoverDirty = false;
  let hovered: number | null = null;
  let hoveredGroup: number | null = null;
  let press: { x: number; y: number } | null = null;
  let destroyed = false;

  function fitDistance() {
    const vertical = THREE.MathUtils.degToRad(camera.fov) / 2;
    // Fit the width left of the inset, not the whole canvas.
    const horizontal = Math.atan(Math.tan(vertical) * Math.max(0.2, (viewWidth - Math.min(inset, viewWidth * 0.6)) / viewHeight));
    return (frame.radius / Math.sin(Math.min(vertical, horizontal))) * 1.02;
  }
  function homePosition() {
    const distance = fitDistance();
    const { azimuth, elevation } = frame.view;
    return new THREE.Vector3(
      distance * Math.cos(elevation) * Math.sin(azimuth),
      distance * Math.sin(elevation),
      distance * Math.cos(elevation) * Math.cos(azimuth),
    );
  }
  function applyCameraLimits() {
    controls.minDistance = frame.radius * 0.25;
    controls.maxDistance = fitDistance() * 3;
  }

  function resize() {
    const width = Math.max(1, host.clientWidth);
    const height = Math.max(1, host.clientHeight);
    viewWidth = width;
    viewHeight = height;
    renderer.setSize(width, height, false);
    // With an inset, the canvas shows the right part of a wider frame whose centre is the middle
    // of the uncovered area, so the figure sits there instead of under the panel.
    const shift = Math.min(inset, width * 0.6);
    camera.aspect = (width + shift) / height;
    if (shift > 0) camera.setViewOffset(width + shift, height, shift, 0, width, height);
    else camera.clearViewOffset();
    camera.updateProjectionMatrix();
    const scale = renderer.getDrawingBufferSize(new THREE.Vector2()).y / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2));
    material.uniforms.uScale.value = scale;
    if (scaffold) scaffold.material.uniforms.uScale.value = scale;
    applyCameraLimits();
    invalidate();
  }

  const continuous = () =>
    !reduced || (morphStart !== null) || (!birthDone && birthStart !== null);
  function invalidate() {
    invalidated = true;
    schedule();
  }
  function schedule() {
    if (raf || destroyed || !active || !intersecting || document.hidden) return;
    raf = requestAnimationFrame(render);
  }

  function setAttribute(name: string, array: Float32Array, size: number) {
    geometry.setAttribute(name, new THREE.BufferAttribute(array, size));
  }

  function render(now: number) {
    invalidated = false;
    raf = 0;
    const dt = last ? Math.min(0.1, (now - last) / 1000) : 0;
    last = now;
    if (count && !birthDone) {
      birthStart ??= now;
      const progress = ((now - birthStart) / BIRTH_MS) * 1.12;
      material.uniforms.uBirth.value = progress;
      if (progress >= 1.12) {
        birthDone = true;
        material.uniforms.uBirth.value = 2;
        opts.onBirth(true);
      }
    }
    if (morphStart !== null) {
      const t = Math.min(1, (now - morphStart) / MORPH_MS);
      const e = ease(t);
      material.uniforms.uMorph.value = e;
      if (relayout) spin = morphSpin * (1 - e);
      else if (turning && !reduced) spin += dt * 0.22 * speed;
      if (cameraFrom) camera.position.lerpVectors(cameraFrom, cameraTo, e);
      if (t >= 1) {
        morphStart = null;
        cameraFrom = null;
        if (relayout) {
          spinAxis = frame.turnAxis;
          spin = 0;
        }
      }
    } else if (turning && !reduced) spin += dt * 0.22 * speed;
    pivot.rotation.set(0, 0, 0);
    pivot.rotation[spinAxis] = spin;
    if (lines) lines.material.opacity = (birthDone ? frame.lineAlpha * (1 + 2 * backdrop.cover) : 0) * (focus >= 0 || hoverGroup >= 0 ? 0.35 : 1);
    material.uniforms.uTime.value = reduced ? 0 : now / 1000;
    controls.update();
    renderer.render(scene, camera);
    if (hoverDirty) {
      hoverDirty = false;
      pick();
    }
    opts.onFrame();
    if (continuous() || invalidated) schedule();
  }

  /** Screen-space nearest drawn point within PICK_RADIUS of the pointer. */
  function nearest(clientX: number, clientY: number): number | null {
    if (!count) return null;
    pivot.updateMatrixWorld();
    camera.updateMatrixWorld();
    const e = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse).multiply(points.matrixWorld).elements;
    const rect = canvas.getBoundingClientRect();
    const morph = material.uniforms.uMorph.value as number;
    const birth = material.uniforms.uBirth.value as number;
    let best: number | null = null;
    let bestDistance = PICK_RADIUS * PICK_RADIUS;
    let bestDepth = Infinity;
    for (let i = 0; i < count; i += 1) {
      if (births[i] > birth || groups[i] < -1.5 || (focus >= 0 && groups[i] !== focus)) continue;
      const j = i * 3;
      const x = previous[j] + (target[j] - previous[j]) * morph;
      const y = previous[j + 1] + (target[j + 1] - previous[j + 1]) * morph;
      const z = previous[j + 2] + (target[j + 2] - previous[j + 2]) * morph;
      const w = e[3] * x + e[7] * y + e[11] * z + e[15];
      if (w <= 0) continue;
      const sx = rect.left + ((e[0] * x + e[4] * y + e[8] * z + e[12]) / w * 0.5 + 0.5) * rect.width;
      const sy = rect.top + (0.5 - (e[1] * x + e[5] * y + e[9] * z + e[13]) / w * 0.5) * rect.height;
      const d = (sx - clientX) ** 2 + (sy - clientY) ** 2;
      if (d < bestDistance || (d === bestDistance && w < bestDepth)) {
        best = i;
        bestDistance = d;
        bestDepth = w;
      }
    }
    return best;
  }
  /** The sphere under the pointer (the nearest centre among those it is inside), when groups are spheres. */
  function regionAt(clientX: number, clientY: number): number | null {
    const regions = frame.regions;
    if (!regions || !count) return null;
    pivot.updateMatrixWorld();
    camera.updateMatrixWorld();
    const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
    let best: number | null = null;
    let bestDistance = Infinity;
    for (let g = 0; g * 4 < regions.length; g += 1) {
      if (focus >= 0 && g !== focus) continue;
      const centre = new THREE.Vector3(regions[g * 4] - frame.center[0], regions[g * 4 + 1] - frame.center[1], regions[g * 4 + 2] - frame.center[2]).applyMatrix4(pivot.matrixWorld);
      const middle = toScreen(centre);
      const edge = toScreen(centre.clone().addScaledVector(right, regions[g * 4 + 3] * 1.04));
      if (!middle || !edge) continue;
      const distance = Math.hypot(middle.x - clientX, middle.y - clientY);
      if (distance <= Math.hypot(edge.x - middle.x, edge.y - middle.y) && distance < bestDistance) {
        best = g;
        bestDistance = distance;
      }
    }
    return best;
  }
  function groupAt(index: number | null, clientX: number, clientY: number) {
    return regionAt(clientX, clientY) ?? (index === null ? null : groups[index] >= 0 ? groups[index] : null);
  }
  function pick() {
    const next = pointer ? nearest(pointer.x, pointer.y) : null;
    const group = pointer ? groupAt(next, pointer.x, pointer.y) : null;
    if (next !== hovered || next !== null || group !== hoveredGroup) {
      hovered = next;
      hoveredGroup = group;
      opts.onHover(next, group, pointer?.x ?? 0, pointer?.y ?? 0);
    }
  }
  function toScreen(world: THREE.Vector3) {
    const v = world.clone().project(camera);
    if (v.z > 1) return null;
    const rect = canvas.getBoundingClientRect();
    return { x: rect.left + (v.x * 0.5 + 0.5) * rect.width, y: rect.top + (0.5 - v.y * 0.5) * rect.height };
  }
  function project(x: number, y: number, z: number) {
    pivot.updateMatrixWorld();
    camera.updateMatrixWorld();
    return toScreen(new THREE.Vector3(x, y, z).applyMatrix4(pivot.matrixWorld));
  }

  const onPointerMove = (event: PointerEvent) => {
    pointer = { x: event.clientX, y: event.clientY };
    if (event.buttons) return;
    hoverDirty = true;
    invalidate();
  };
  const clearHover = () => {
    if (hovered === null && hoveredGroup === null) return;
    hovered = null;
    hoveredGroup = null;
    opts.onHover(null, null, 0, 0);
  };
  const onPointerLeave = () => {
    pointer = null;
    clearHover();
  };
  const onPointerDown = (event: PointerEvent) => {
    press = event.button === 0 ? { x: event.clientX, y: event.clientY } : null;
    clearHover();
  };
  const onPointerUp = (event: PointerEvent) => {
    if (press && Math.hypot(event.clientX - press.x, event.clientY - press.y) < 5) {
      const index = nearest(event.clientX, event.clientY);
      opts.onPick(index, groupAt(index, event.clientX, event.clientY));
    }
    press = null;
  };
  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerleave", onPointerLeave);
  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointerup", onPointerUp);
  controls.addEventListener("change", invalidate);
  const onContextLost = (event: Event) => event.preventDefault();
  canvas.addEventListener("webglcontextlost", onContextLost);

  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(host);
  const visibility = new IntersectionObserver((entries) => {
    intersecting = entries.some((entry) => entry.isIntersecting);
    if (intersecting) invalidate();
  });
  visibility.observe(host);
  const onVisibility = () => {
    last = 0;
    if (!document.hidden) invalidate();
  };
  document.addEventListener("visibilitychange", onVisibility);
  resize();

  const shellColours = (n: number) => {
    const out = new Float32Array(n * 3);
    for (let i = 0; i < n; i += 1) out.set(backdrop.ink, i * 3);
    return out;
  };
  const lineBlending = () => (backdrop.cover > 0.5 ? THREE.NormalBlending : THREE.AdditiveBlending);

  const centred = (positions: Float32Array) => {
    const out = new Float32Array(positions.length);
    const [cx, cy, cz] = frame.center;
    for (let i = 0; i < positions.length; i += 3) {
      out[i] = positions[i] - cx;
      out[i + 1] = positions[i + 1] - cy;
      out[i + 2] = positions[i + 2] - cz;
    }
    return out;
  };

  return {
    setCloud(cloud, nextFrame, morph) {
      const canMorph = morph && !reduced && fitted && cloud.births.length === count;
      const shown = new Float32Array(target.length);
      const blend = material.uniforms.uMorph.value as number;
      for (let i = 0; i < target.length; i += 1) shown[i] = previous[i] + (target[i] - previous[i]) * blend;
      // A new layout eases the camera home; regrouping the same layout keeps the user's view.
      relayout = nextFrame.turnAxis !== frame.turnAxis || nextFrame.view.azimuth !== frame.view.azimuth || nextFrame.view.elevation !== frame.view.elevation;
      // Opening one group (or returning) reframes: the camera flies in or out along its current direction.
      const reframed = nextFrame.radius !== frame.radius || nextFrame.center.some((value, axis) => value !== frame.center[axis]);
      frame = nextFrame;
      count = cloud.births.length;
      target = centred(cloud.positions);
      previous = canMorph ? shown : target;
      births = cloud.births;
      groups = cloud.groups;
      setAttribute("position", target, 3);
      setAttribute("from", previous, 3);
      setAttribute("color", cloud.colors, 3);
      setAttribute("size", cloud.sizes, 1);
      setAttribute("seed", cloud.seeds, 1);
      setAttribute("birth", cloud.births, 1);
      setAttribute("grp", cloud.groups, 1);
      geometry.setDrawRange(0, count);
      canvas.dataset.shown = String(groups.reduce((shown, group) => shown + (group > -1.5 ? 1 : 0), 0));
      applyCameraLimits();
      if (canMorph) {
        material.uniforms.uMorph.value = 0;
        morphStart = performance.now();
        morphSpin = spin;
        cameraFrom = camera.position.clone();
        cameraTo = relayout ? homePosition() : cameraFrom.clone().setLength(fitDistance());
      } else {
        material.uniforms.uMorph.value = 1;
        morphStart = null;
        // A refresh or regrouping keeps the camera; a new layout starts from its home view.
        if (!fitted || relayout) {
          spin = 0;
          spinAxis = frame.turnAxis;
          camera.position.copy(homePosition());
        } else if (reframed) camera.position.setLength(fitDistance());
      }
      controls.target.set(0, 0, 0);
      fitted = true;
      // Nothing to build (or no motion wanted): the cloud is complete at once.
      if (reduced || !count) {
        material.uniforms.uBirth.value = 2;
        birthDone = true;
      }
      if (!birthDone && birthStart === null) material.uniforms.uBirth.value = 0;
      canvas.dataset.points = String(count);
      opts.onBirth(birthDone);
      hoverDirty = !!pointer;
      invalidate();
    },
    setLines(positions, colors) {
      if (lines) {
        pivot.remove(lines);
        lines.geometry.dispose();
        lines.material.dispose();
        lines = null;
      }
      if (positions && colors && positions.length) {
        const lineGeometry = new THREE.BufferGeometry();
        lineGeometry.setAttribute("position", new THREE.BufferAttribute(centred(positions), 3));
        lineGeometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
        lines = new THREE.LineSegments(lineGeometry, new THREE.LineBasicMaterial({
          vertexColors: true, transparent: true, opacity: 0, blending: lineBlending(), depthWrite: false, depthTest: false,
        }));
        lines.frustumCulled = false;
        pivot.add(lines);
      }
      invalidate();
    },
    setScaffold(positions) {
      if (scaffold) {
        pivot.remove(scaffold);
        scaffold.geometry.dispose();
        scaffold.material.dispose();
        scaffold = null;
      }
      if (positions && positions.length) {
        const n = positions.length / 3;
        const shell = new THREE.BufferGeometry();
        const centredShell = centred(positions);
        shell.setAttribute("position", new THREE.BufferAttribute(centredShell, 3));
        shell.setAttribute("from", new THREE.BufferAttribute(centredShell, 3));
        shell.setAttribute("color", new THREE.BufferAttribute(shellColours(n), 3));
        shell.setAttribute("size", new THREE.BufferAttribute(new Float32Array(n).fill(0.9), 1));
        shell.setAttribute("seed", new THREE.BufferAttribute(Float32Array.from({ length: n }, (_, i) => (i * 0.618034) % 1), 1));
        shell.setAttribute("birth", new THREE.BufferAttribute(new Float32Array(n).fill(0.95), 1));
        shell.setAttribute("grp", new THREE.BufferAttribute(new Float32Array(n).fill(-1), 1));
        const shellMaterial = cloudMaterial(focus >= 0 ? SCAFFOLD_FOCUSED_ALPHA : SCAFFOLD_ALPHA, reduced ? 0 : 0.6);
        shellMaterial.uniforms.uScale = material.uniforms.uScale;
        shellMaterial.uniforms.uTime = material.uniforms.uTime;
        shellMaterial.uniforms.uBirth = material.uniforms.uBirth;
        shellMaterial.uniforms.uSize = material.uniforms.uSize;
        shellMaterial.uniforms.uCover = material.uniforms.uCover;
        scaffold = new THREE.Points(shell, shellMaterial);
        scaffold.frustumCulled = false;
        pivot.add(scaffold);
      }
      invalidate();
    },
    setFocus(group) {
      focus = group ?? -1;
      material.uniforms.uFocus.value = focus;
      if (scaffold) scaffold.material.uniforms.uAlpha.value = focus >= 0 ? SCAFFOLD_FOCUSED_ALPHA : SCAFFOLD_ALPHA;
      hoverDirty = !!pointer;
      invalidate();
    },
    setHover(group) {
      hoverGroup = group ?? -1;
      material.uniforms.uHover.value = hoverGroup;
      invalidate();
    },
    setBackdrop(next) {
      backdrop = next;
      material.uniforms.uCover.value = next.cover;
      if (scaffold) {
        const colours = scaffold.geometry.getAttribute("color") as THREE.BufferAttribute;
        colours.array.set(shellColours(colours.count));
        colours.needsUpdate = true;
      }
      if (lines) {
        lines.material.blending = lineBlending();
        lines.material.needsUpdate = true;
      }
      invalidate();
    },
    setMotion(on, nextSpeed) {
      turning = on;
      speed = nextSpeed;
      invalidate();
    },
    setInset(right) {
      const next = Math.max(0, Math.round(right));
      if (next === inset) return;
      inset = next;
      resize();
      if (fitted) {
        cameraFrom = null;
        camera.position.setLength(fitDistance());
      }
      invalidate();
    },
    setPointSize(scale) {
      material.uniforms.uSize.value = POINT_WORLD_SIZE * scale;
      invalidate();
    },
    setActive(next) {
      active = next;
      last = 0;
      if (active) invalidate();
      else if (raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
    },
    screenPoint(index) {
      if (index < 0 || index >= count) return null;
      const morph = material.uniforms.uMorph.value as number;
      const j = index * 3;
      return project(
        previous[j] + (target[j] - previous[j]) * morph,
        previous[j + 1] + (target[j + 1] - previous[j + 1]) * morph,
        previous[j + 2] + (target[j + 2] - previous[j + 2]) * morph,
      );
    },
    anchorPoint(group) {
      const j = group * 3;
      if (j + 2 >= frame.anchors.length) return null;
      return project(frame.anchors[j] - frame.center[0], frame.anchors[j + 1] - frame.center[1], frame.anchors[j + 2] - frame.center[2]);
    },
    resetView() {
      spin = 0;
      spinAxis = frame.turnAxis;
      camera.position.copy(homePosition());
      controls.target.set(0, 0, 0);
      invalidate();
    },
    destroy() {
      destroyed = true;
      if (raf) cancelAnimationFrame(raf);
      resizeObserver.disconnect();
      visibility.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerleave", onPointerLeave);
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("webglcontextlost", onContextLost);
      controls.removeEventListener("change", invalidate);
      controls.dispose();
      geometry.dispose();
      material.dispose();
      if (scaffold) {
        scaffold.geometry.dispose();
        scaffold.material.dispose();
      }
      if (lines) {
        lines.geometry.dispose();
        lines.material.dispose();
      }
      renderer.dispose();
      renderer.forceContextLoss();
      canvas.remove();
    },
  };
}
