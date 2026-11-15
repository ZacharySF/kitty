"""The Kitty theme's three stills (web/src/lib/themes/aero/scenes/): drawn here, not photographed.

Each is an SVG built from a few large shapes, pushed through the things a cheap compact camera
and cross-processed slide film did to a picture: motion blur, bloom round the lights, one
colour cast over everything, a vignette and grain. `--out DIR` writes platform.svg,
corridor.svg and beams.svg; scripts/kitty-theme-render.mjs turns them into the JPEGs the
theme ships. Seeded, so a rerun gives the same pictures.

    python3 scripts/kitty-theme-scenes.py --out /tmp/kitty-scenes
    node scripts/kitty-theme-render.mjs /tmp/kitty-scenes web/src/lib/themes/aero/scenes
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

W, H = 1920, 1200


def poly(points, fill, extra=""):
    return f'<polygon points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}" fill="{fill}" {extra}/>'


def finish(seed: int, cast: str, vignette: str = "0.72") -> str:
    """Grain, the cast and the dark corners: the last layers of every still."""
    return f"""
  <use href="#scene" filter="url(#halation)" opacity="0.5" style="mix-blend-mode:screen"/>
  <rect width="{W}" height="{H}" filter="url(#mottle{seed})" opacity="0.55" style="mix-blend-mode:soft-light"/>
  <rect width="{W}" height="{H}" fill="{cast}" style="mix-blend-mode:soft-light"/>
  <rect width="{W}" height="{H}" fill="url(#vignette)" opacity="{vignette}"/>
  <rect width="{W}" height="{H}" filter="url(#grain{seed})" opacity="0.62" style="mix-blend-mode:overlay"/>
"""


def defs(seed: int, extra: str = "") -> str:
    return f"""<defs>
    <filter id="grain{seed}" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">
      <feTurbulence type="fractalNoise" baseFrequency="0.62" numOctaves="3" seed="{seed}"/>
      <feColorMatrix type="saturate" values="0"/>
      <feComponentTransfer><feFuncR type="linear" slope="1.7" intercept="-0.35"/><feFuncG type="linear" slope="1.7" intercept="-0.35"/><feFuncB type="linear" slope="1.7" intercept="-0.35"/></feComponentTransfer>
    </filter>
    <filter id="mottle{seed}" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">
      <feTurbulence type="fractalNoise" baseFrequency="0.0035 0.006" numOctaves="3" seed="{seed + 5}"/>
      <feColorMatrix type="saturate" values="0"/>
      <feComponentTransfer><feFuncR type="linear" slope="2.4" intercept="-0.7"/><feFuncG type="linear" slope="2.4" intercept="-0.7"/><feFuncB type="linear" slope="2.4" intercept="-0.7"/></feComponentTransfer>
    </filter>
    <filter id="halation" x="-10%" y="-10%" width="120%" height="120%" color-interpolation-filters="sRGB">
      <feComponentTransfer><feFuncR type="linear" slope="3.2" intercept="-2.1"/><feFuncG type="linear" slope="3.2" intercept="-2.1"/><feFuncB type="linear" slope="3.2" intercept="-2.1"/></feComponentTransfer>
      <feGaussianBlur stdDeviation="38"/>
    </filter>
    <filter id="far"><feGaussianBlur stdDeviation="1.6"/></filter>
    <filter id="near"><feGaussianBlur stdDeviation="14"/></filter>
    <filter id="smear" x="-20%" y="-5%" width="140%" height="110%"><feGaussianBlur stdDeviation="90 2.5"/></filter>
    <filter id="smear2" x="-20%" y="-5%" width="140%" height="110%"><feGaussianBlur stdDeviation="34 1.2"/></filter>
    <filter id="soft"><feGaussianBlur stdDeviation="3.2"/></filter>
    <filter id="softer"><feGaussianBlur stdDeviation="9"/></filter>
    <filter id="bloom" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="26"/></filter>
    <radialGradient id="vignette" cx="50%" cy="46%" r="75%"><stop offset="0.45" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity="0.9"/></radialGradient>
    {extra}
  </defs>"""


def platform(seed: int = 11) -> str:
    """A train leaving a station: the platform holds still, the carriages smear past."""
    rng = random.Random(seed)
    vx, vy = 210.0, 548.0  # where the platform edge and the train's lines meet

    def band(y1, y2, fill, extra=""):
        # A strip of the carriage side: wide at the right edge, closing on the vanishing point.
        return poly([(vx, vy - 2), (W + 200, y1), (W + 200, y2), (vx, vy + 2)], fill, extra)

    train = [
        band(40, 190, "#0b2a1f"),      # roof in shadow
        band(190, 300, "#9fd8bd"),     # roof line catching the tubes
        band(300, 470, "#dff5e6"),     # upper body
        band(470, 700, "#123d2f"),     # windows
        band(700, 850, "#e6f7ea"),     # lower body
        band(850, 915, "#b5452c"),     # the line's red stripe
        band(915, 1010, "#cfe9d8"),
        band(1010, 1120, "#0a211a"),   # underframe
    ]
    # Lit windows and door gaps, smeared into dashes.
    for _ in range(26):
        x = rng.uniform(520, W + 100)
        t = (x - vx) / (W + 200 - vx)
        top, bottom = vy + (470 - vy) * t, vy + (700 - vy) * t
        width = rng.uniform(60, 260) * t
        shade = rng.choice(["#d9f7e4", "#8fd3b0", "#f4fff6", "#1f6a52", "#06170f"])
        train.append(poly([(x, top + 14 * t), (x + width, top + 14 * t), (x + width, bottom - 14 * t), (x, bottom - 14 * t)], shade, f'opacity="{rng.uniform(0.35, 0.9):.2f}"'))
    for _ in range(40):  # streaks along the whole side
        y = rng.uniform(200, 1010)
        x = rng.uniform(500, W)
        t = (x - vx) / (W + 200 - vx)
        yy = vy + (y - vy) * t
        train.append(f'<rect x="{x:.0f}" y="{yy:.1f}" width="{rng.uniform(200, 900):.0f}" height="{rng.uniform(1.5, 9) * t:.1f}" fill="{rng.choice(["#ffffff", "#0c2f24", "#c8f0d8"])}" opacity="{rng.uniform(0.15, 0.5):.2f}"/>')

    tubes = []
    for i in range(13):  # the ceiling's fluorescent tubes, shrinking into the tunnel
        t = i / 12
        x = 1180 - 1040 * (t ** 0.82)
        y = 22 + 372 * (t ** 1.25)
        w, h = 150 * (1 - t) ** 1.5 + 12, 30 * (1 - t) ** 1.4 + 5
        tubes.append(f'<ellipse cx="{x:.0f}" cy="{y:.0f}" rx="{w / 2:.1f}" ry="{h / 2:.1f}" transform="rotate(-24 {x:.0f} {y:.0f})" fill="#f4fff4"/>')

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
  {defs(seed, '<linearGradient id="hall" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#04130d"/><stop offset="0.42" stop-color="#1b5a44"/><stop offset="1" stop-color="#0d3327"/></linearGradient>'
              '<linearGradient id="floor" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2f8a6a"/><stop offset="1" stop-color="#0a2a20"/></linearGradient>')}
  <g id="scene">
  <rect width="{W}" height="{H}" fill="url(#hall)"/>
  <!-- the tunnel mouth -->
  <ellipse cx="150" cy="520" rx="210" ry="250" fill="#020a07" filter="url(#softer)"/>
  <g filter="url(#smear)">{"".join(train)}</g>
  <g filter="url(#smear2)" opacity="0.55">{"".join(train[8:])}</g>
  <!-- ceiling -->
  {poly([(0, 0), (W, 0), (W, 60), (vx, vy - 120), (0, 300)], "#06190f", 'filter="url(#softer)" opacity="0.92"')}
  <g filter="url(#bloom)" opacity="0.9">{"".join(tubes)}</g>
  <g filter="url(#soft)">{"".join(tubes)}</g>
  <!-- platform: the still part of the picture -->
  <g filter="url(#soft)">
    {poly([(vx, vy + 6), (760, H), (0, H), (0, 640)], "url(#floor)")}
    {poly([(vx, vy + 6), (760, H), (850, H), (vx + 3, vy + 6)], "#e9f7e9")}
    {poly([(vx - 8, vy + 10), (330, H), (455, H), (vx - 5, vy + 8)], "#c9b63a")}
    {poly([(vx - 30, vy + 18), (0, 900), (0, 760)], "#0b2e22", 'opacity="0.7"')}
  </g>
  <!-- the red signal lamp at the tunnel mouth -->
  <circle cx="92" cy="596" r="26" fill="#ff5a2a" filter="url(#bloom)"/><circle cx="92" cy="596" r="7" fill="#ffd9c2" filter="url(#soft)"/>
  <!-- the tubes again, in the wet floor -->
  <g filter="url(#near)" opacity="0.22" transform="translate(-120 1180) scale(1 -0.9)">{"".join(tubes)}</g>
  </g>
  {finish(seed, "#1fae7a")}
</svg>"""


def corridor(seed: int = 23) -> str:
    """An institutional corridor under daylight tubes, everything gone green."""
    vx, vy = 1010.0, 600.0
    near = [(0, 0), (W, 0), (W, H), (0, H)]
    far = [(880, 470), (1140, 470), (1140, 760), (880, 760)]

    def wall(a, b, c, d, fill, extra=""):
        return poly([a, b, c, d], fill, extra)

    def along(side_x, y_near, depth):
        # A point on a wall edge: from the near frame (depth 0) to the far doorway (depth 1).
        x_far = 880 if side_x == 0 else 1140
        y_far = 470 + (y_near / H) * (760 - 470)
        return (side_x + (x_far - side_x) * depth, y_near + (y_far - y_near) * depth)

    parts = [
        wall(near[0], near[1], far[1], far[0], "url(#ceiling)"),                # ceiling
        wall(near[3], near[2], far[2], far[3], "url(#lino)"),                   # floor
        wall(near[0], far[0], far[3], near[3], "url(#leftwall)"),               # left wall
        wall(near[1], far[1], far[2], near[2], "url(#rightwall)"),              # right wall
        poly(far, "#0c2a24"),                                                   # the far end
        f'<rect x="930" y="520" width="150" height="240" fill="#06150f"/>',
        f'<rect x="952" y="556" width="62" height="26" fill="#9fd0b6" opacity="0.8"/>',
    ]
    # Left: a run of windows, burnt out by the daylight behind them.
    for depth0, depth1 in [(0.02, 0.4), (0.44, 0.63), (0.66, 0.78)]:
        a, b = along(0, 170, depth0), along(0, 170, depth1)
        c, d = along(0, 770, depth1), along(0, 770, depth0)
        parts.append(poly([a, b, c, d], "url(#day)"))
        mid_top, mid_bottom = along(0, 170, (depth0 + depth1) / 2), along(0, 770, (depth0 + depth1) / 2)
        parts.append(f'<line x1="{mid_top[0]:.0f}" y1="{mid_top[1]:.0f}" x2="{mid_bottom[0]:.0f}" y2="{mid_bottom[1]:.0f}" stroke="#16382c" stroke-width="9"/>')
        parts.append(poly([a, b, (b[0], b[1] + 10), (a[0], a[1] + 16)], "#0f2e24"))
    parts.append(poly([along(0, 770, 0.0), along(0, 770, 0.8), along(0, 850, 0.8), along(0, 850, 0.0)], "#0c2a21"))   # sill
    # Right: a dark rail at waist height and two felt notice boards.
    parts.append(poly([along(W, 610, 0.0), along(W, 610, 1.0), along(W, 668, 1.0), along(W, 668, 0.0)], "#0b241d"))
    for depth0, depth1 in [(0.18, 0.4), (0.5, 0.64)]:
        parts.append(poly([along(W, 190, depth0), along(W, 190, depth1), along(W, 560, depth1), along(W, 560, depth0)], "#8fb5a2"))
    # Ceiling tubes.
    tubes = "".join(f'<rect x="{vx - w / 2 + dx:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="{h / 2:.0f}" fill="#fbfff8"/>'
                    for y, w, h, dx in [(92, 420, 30, -30), (300, 220, 16, -18), (398, 130, 9, -10), (444, 80, 6, -6)])
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
  {defs(seed, '<linearGradient id="lino" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5e9a82"/><stop offset="0.5" stop-color="#1d4a3c"/><stop offset="1" stop-color="#0a211b"/></linearGradient>'
              '<linearGradient id="day" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f6fff2"/><stop offset="0.6" stop-color="#bfe6cf"/><stop offset="1" stop-color="#6fae93"/></linearGradient>'
              '<linearGradient id="ceiling" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1c4a3c"/><stop offset="0.55" stop-color="#7fb49c"/><stop offset="1" stop-color="#d7efdf"/></linearGradient>'
              '<linearGradient id="leftwall" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#143a2f"/><stop offset="0.45" stop-color="#5f9a84"/><stop offset="1" stop-color="#b4d9c5"/></linearGradient>'
              '<linearGradient id="rightwall" x1="1" y1="0" x2="0" y2="0"><stop offset="0" stop-color="#2a5c4c"/><stop offset="0.4" stop-color="#9cc9b3"/><stop offset="1" stop-color="#e4f4e6"/></linearGradient>')}
  <g id="scene">
  <g filter="url(#far)">{"".join(parts)}</g>
  <g filter="url(#bloom)" opacity="0.85">{tubes}</g>
  <g filter="url(#soft)">{tubes}</g>
  <!-- the tubes in the polished floor, and the windows' light across it -->
  <g filter="url(#near)" opacity="0.5" transform="translate(0 {2 * 760 - 60}) scale(1 -1.9)">{tubes}</g>
  {poly([(0, 900), (760, 790), (900, 800), (300, H), (0, H)], "#e6fbea", 'opacity="0.28" filter="url(#near)"')}
  {poly([(820, 780), (1180, 780), (1500, H), (520, H)], "#d8f2e2", 'opacity="0.12" filter="url(#softer)"')}
  <!-- someone at the far end, a long way off -->
  <g filter="url(#soft)" fill="#04120d"><ellipse cx="1004" cy="652" rx="9" ry="10"/><path d="M990 668 q14 -10 28 0 l5 62 h-10 l-3 28 h-12 l-3 -28 h-10 z"/></g>
  </g>
  {finish(seed, "#17a58c", "0.86")}
</svg>"""


def beams(seed: int = 37) -> str:
    """Looking up between the decks of a car park at dusk: nothing but blue."""
    rng = random.Random(seed)
    parts = [f'<rect width="{W}" height="{H}" fill="url(#sky)"/>']
    front: list[str] = []
    # The structure on the right: slabs stepping back, each with a dark underside.
    for i in range(7):
        y = 330 + i * 118
        x = 900 + i * 34
        parts.append(poly([(x, y), (W, y - 150 - i * 6), (W, y - 96 - i * 6), (x + 12, y + 44)], "#9db8f2"))
        parts.append(poly([(x + 12, y + 44), (W, y - 96 - i * 6), (W, y - 30 - i * 6), (x + 30, y + 96)], "#0b2c9a"))
        for j in range(5):  # the tubes under each deck
            lx = x + 150 + j * 190 + rng.uniform(-20, 20)
            ly = y + 70 - (lx - x) * 0.16
            parts.append(f'<rect x="{lx:.0f}" y="{ly:.0f}" width="{rng.uniform(46, 70):.0f}" height="5" fill="#f3f7ff" transform="rotate(-9 {lx:.0f} {ly:.0f})"/>')
    # Two great beams crossing the frame, lit on top.
    beams_ = [
        ([(-40, 250), (W + 40, 20), (W + 40, 170), (-40, 410)], "url(#face)"),
        ([(-40, 410), (W + 40, 170), (W + 40, 250), (-40, 500)], "url(#under)"),
        ([(-40, 930), (W + 40, 560), (W + 40, 700), (-40, 1090)], "url(#face)"),
        ([(-40, 1090), (W + 40, 700), (W + 40, 800), (-40, 1200)], "url(#under)"),
    ]
    front += [poly(points, fill) for points, fill in beams_]
    parts.append(poly([(0, 560), (760, 500), (760, 520), (0, 584)], "#0c33ad", 'opacity="0.8"'))
    # A lamp standard and a far tower, small against the sky.
    parts.append('<rect x="318" y="640" width="5" height="560" fill="#092a9c"/><rect x="286" y="634" width="70" height="9" rx="4" fill="#dfe9ff"/>')
    parts.append('<rect x="560" y="820" width="120" height="380" fill="#1b46c4" opacity="0.7"/>')
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
  {defs(seed, '<linearGradient id="sky" x1="0" y1="0" x2="0.3" y2="1"><stop offset="0" stop-color="#0a2fb0"/><stop offset="0.55" stop-color="#2f66e6"/><stop offset="1" stop-color="#9dbcf8"/></linearGradient>'
              '<linearGradient id="face" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#6f93ee"/><stop offset="0.5" stop-color="#c9d9fc"/><stop offset="1" stop-color="#8eaaf2"/></linearGradient>'
              '<linearGradient id="under" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#061f7a"/><stop offset="1" stop-color="#0e37b4"/></linearGradient>')}
  <g id="scene">
  <g filter="url(#far)">{"".join(parts)}</g>
  <g filter="url(#near)">{"".join(front)}</g>
  </g>
  {finish(seed, "#1437c8", "0.7")}
</svg>"""


SCENES = {"platform": platform, "corridor": corridor, "beams": beams}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    out = parser.parse_args().out
    out.mkdir(parents=True, exist_ok=True)
    for name, draw in SCENES.items():
        (out / f"{name}.svg").write_text(draw(), encoding="utf-8")
        print(out / f"{name}.svg")


if __name__ == "__main__":
    main()
