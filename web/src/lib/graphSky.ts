import type * as Three from "three";

/** Colours the sky is painted from: the theme's void and two graph colours, plus star tints. */
export type SkyColours = { void: string; a: string; b: string; stars: readonly string[] };

export type Sky = {
  object: Three.Object3D;
  setColours(colours: SkyColours): void;
  setReduced(reduced: boolean): void;
  dispose(): void;
};

const STAR_COUNT = 650;
const DOME_RADIUS = 9000;
const STAR_RADIUS = 6000;

/** Deterministic random so the star field is the same on every mount. */
function seeded(seed: number) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
}

/**
 * A procedural pastel nebula dome with twinkling stars, drawn behind the 3D graph.
 * It is centred on the camera every frame, so it reads as an infinitely distant sky
 * that turns as you orbit. Time freezes under reduced motion.
 */
export function createSky(T: typeof Three, colours: SkyColours, reduced: boolean): Sky {
  let frozen = reduced;
  const dome = new T.Mesh(
    new T.SphereGeometry(DOME_RADIUS, 48, 32),
    new T.ShaderMaterial({
      side: T.BackSide,
      depthWrite: false,
      depthTest: false,
      uniforms: {
        uTime: { value: 0 },
        uVoid: { value: new T.Color() },
        uA: { value: new T.Color() },
        uB: { value: new T.Color() },
      },
      vertexShader: /* glsl */ `
        varying vec3 vDir;
        void main() {
          vDir = normalize(position);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }`,
      fragmentShader: /* glsl */ `
        uniform float uTime; uniform vec3 uVoid; uniform vec3 uA; uniform vec3 uB;
        varying vec3 vDir;
        float hash(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }
        float noise(vec3 p) {
          vec3 i = floor(p); vec3 f = fract(p); f = f * f * (3.0 - 2.0 * f);
          return mix(mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x), mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
                     mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x), mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z);
        }
        float fbm(vec3 p) { float v = 0.0; float a = 0.5; for (int i = 0; i < 5; i++) { v += a * noise(p); p *= 2.03; a *= 0.5; } return v; }
        void main() {
          vec3 d = normalize(vDir);
          float n = fbm(d * 2.4 + vec3(0.0, uTime * 0.012, 0.0));
          float m = fbm(d * 3.6 - vec3(4.0, 1.0, uTime * 0.008));
          vec3 col = uVoid;
          col = mix(col, uA, smoothstep(0.48, 0.82, n) * 0.85);
          col = mix(col, uB, smoothstep(0.52, 0.86, m) * 0.7);
          gl_FragColor = vec4(col, 1.0);
          #include <colorspace_fragment>
        }`,
    }),
  );
  dome.renderOrder = -2;
  dome.frustumCulled = false;
  const domeMaterial = dome.material as Three.ShaderMaterial;

  const random = seeded(31);
  const positions = new Float32Array(STAR_COUNT * 3);
  const seeds = new Float32Array(STAR_COUNT * 2);
  for (let index = 0; index < STAR_COUNT; index += 1) {
    const u = random() * 2 - 1;
    const angle = random() * Math.PI * 2;
    const ring = Math.sqrt(1 - u * u);
    positions.set([Math.cos(angle) * ring * STAR_RADIUS, u * STAR_RADIUS, Math.sin(angle) * ring * STAR_RADIUS], index * 3);
    seeds.set([random(), 0.7 + random() ** 3 * 2.2], index * 2);
  }
  const geometry = new T.BufferGeometry();
  geometry.setAttribute("position", new T.BufferAttribute(positions, 3));
  geometry.setAttribute("aSeed", new T.BufferAttribute(seeds, 2));
  geometry.setAttribute("aColor", new T.BufferAttribute(new Float32Array(STAR_COUNT * 3), 3));
  const starMaterial = new T.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    depthTest: false,
    blending: T.AdditiveBlending,
    uniforms: { uTime: { value: 0 }, uDpr: { value: Math.min(window.devicePixelRatio || 1, 1.5) } },
    vertexShader: /* glsl */ `
      attribute vec2 aSeed; attribute vec3 aColor;
      uniform float uTime; uniform float uDpr;
      varying vec3 vColor; varying float vAlpha;
      void main() {
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = aSeed.y * uDpr;
        float twinkle = 0.55 + 0.45 * sin(uTime * (0.6 + aSeed.x * 1.4) + aSeed.x * 40.0);
        vColor = aColor; vAlpha = (0.25 + 0.55 * aSeed.x) * twinkle;
      }`,
    fragmentShader: /* glsl */ `
      varying vec3 vColor; varying float vAlpha;
      void main() {
        float d = length(gl_PointCoord - 0.5) * 2.0;
        gl_FragColor = vec4(vColor, smoothstep(1.0, 0.2, d) * vAlpha);
        #include <colorspace_fragment>
      }`,
  });
  const stars = new T.Points(geometry, starMaterial);
  stars.renderOrder = -1;
  stars.frustumCulled = false;

  const object = new T.Group();
  object.name = "kitty-sky";
  object.add(dome, stars);
  // Keep the sky centred on the camera so the graph can never leave it.
  dome.onBeforeRender = (_renderer, _scene, camera) => {
    object.position.copy(camera.position);
    object.updateMatrixWorld(true);
    const time = frozen ? 0 : performance.now() / 1000;
    domeMaterial.uniforms.uTime.value = time;
    starMaterial.uniforms.uTime.value = time;
  };

  const sky: Sky = {
    object,
    setColours(next) {
      (domeMaterial.uniforms.uVoid.value as Three.Color).set(next.void);
      // The two nebula tints sit a third of the way from the void toward their graph colour.
      (domeMaterial.uniforms.uA.value as Three.Color).set(next.void).lerp(new T.Color(next.a), 0.34);
      (domeMaterial.uniforms.uB.value as Three.Color).set(next.void).lerp(new T.Color(next.b), 0.3);
      const tints = next.stars.length ? next.stars : [next.a];
      const attribute = geometry.getAttribute("aColor") as Three.BufferAttribute;
      const colour = new T.Color();
      for (let index = 0; index < STAR_COUNT; index += 1) {
        colour.set(tints[index % tints.length]);
        attribute.setXYZ(index, colour.r, colour.g, colour.b);
      }
      attribute.needsUpdate = true;
    },
    setReduced(value) {
      frozen = value;
    },
    dispose() {
      object.removeFromParent();
      dome.geometry.dispose();
      domeMaterial.dispose();
      geometry.dispose();
      starMaterial.dispose();
    },
  };
  sky.setColours(colours);
  return sky;
}
