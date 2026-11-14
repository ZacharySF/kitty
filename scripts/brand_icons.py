#!/usr/bin/env python3
"""rika! mark: the poured cat. A cat behaves like a liquid: it takes the shape of its container (Fardin, 'On the rheology
of cats', Ig Nobel 2017). The tile is the container; the cat fills it to a level; only ears and tail break the surface.

Construction on a 1024 canvas, 8-unit grid:
- Tile: Apple's continuous-corner squircle, 824 wide at (100, 100).
- Surface at y = 600 (61 % down the tile: the lower, calmer split).
- Head: crown at y = 520 (one 80 rise), cheeks at x = 256 / 600 (head width 344, centre 428).
- Ears: outer edge one straight line from the cheek's foot to the tip; tips at y = 376, over the middle of each base,
  leaning out 16; inner edges land on the crown, leaving a crown 112 wide (a third of the head).
- Meniscus: where the cheek meets the surface, a wide shallow concave curve (76 across, 30 up): liquid wetting a wall.
- Tail: stroke 48 with round caps at x = 792, rising to the ear tips' height and hooking 72 toward the edge.
- Corners of ears and crown: radius 24 (half the stroke), so every round in the mark is related to the stroke.
"""
import math

APPLE = ("M383.41 100L640.59 100C722.19 100 763 100 806.92 113.89C854.88 131.34 892.66 169.12 910.11 217.08C924 261 924 "
         "301.81 924 383.41L924 640.59C924 722.19 924 763 910.11 806.92C892.66 854.88 854.88 892.66 806.92 910.11C763 924 "
         "722.19 924 640.59 924L383.41 924C301.81 924 261 924 217.08 910.11C169.12 892.66 131.34 854.88 113.89 806.92C100 "
         "763 100 722.19 100 640.59L100 383.41C100 301.81 100 261 113.89 217.08C131.34 169.12 169.12 131.34 217.08 113.89C261 "
         "100 301.81 100 383.41 100Z")

G = dict(level=600, crown=504, cx=428, half=172, crown_half=56, tip_y=368, lean=10, r=24, men=76, men_y=30, temple=24, tip_r=30,
         tail_x=792, hook=72, stroke=48)


def _toward(a, b, d):
    L = math.dist(a, b)
    return (a[0] + (b[0] - a[0]) * d / L, a[1] + (b[1] - a[1]) * d / L)


def _f(p):
    return f"{p[0]:.1f} {p[1]:.1f}"


def surface(g=G):
    """Open path along the liquid's top edge, left wall to right wall. Per side: surface, meniscus (concave, radius men)
    into a vertical cheek, a rounded temple (radius r) into the ear's outer edge, the rounded tip, the inner edge into
    the crown (radius r), across the crown."""
    L, C, cx, h, ch, r, m = g["level"], g["crown"], g["cx"], g["half"], g["crown_half"], g["r"], g["men"]
    temple_y = C + g.get("temple", 16)
    tip_r = g.get("tip_r", 34)
    segs = []
    for s in (-1, 1):
        cheek = (cx + s * h, L)
        temple = (cx + s * h, temple_y)
        tip = (cx + s * ((h + ch) / 2 + g["lean"]), g["tip_y"])
        inner = (cx + s * ch, C)
        segs.append((cheek, temple, tip, inner))
    def side(cheek, temple, tip, inner, s):
        m0, m1 = (cheek[0] - s * m, cheek[1]), (cheek[0], cheek[1] - g.get("men_y", 28))
        t0, t1 = _toward(temple, cheek, r), _toward(temple, tip, r)
        a, b = _toward(tip, temple, tip_r), _toward(tip, inner, tip_r)
        c0, c1 = _toward(inner, tip, r), (inner[0] + s * r, inner[1])
        return m0, m1, cheek, t0, temple, t1, a, tip, b, c0, inner, c1
    lm0, lm1, lch, lt0, lte, lt1, la, ltip, lb, lc0, lin, lc1 = side(*segs[0], 1)
    rm0, rm1, rch, rt0, rte, rt1, ra, rtip, rb, rc0, rin, rc1 = side(*segs[1], -1)
    # the right side is walked in reverse: crown -> inner -> tip -> temple -> cheek -> surface
    return (f"M0 {L} H{lm0[0]:.1f} Q{_f(lch)} {_f(lm1)} L{_f(lt0)} Q{_f(lte)} {_f(lt1)} L{_f(la)} Q{_f(ltip)} {_f(lb)} "
            f"L{_f(lc0)} Q{_f(lin)} {_f(lc1)} H{rc1[0]:.1f} Q{_f(rin)} {_f(rc0)} L{_f(rb)} Q{_f(rtip)} {_f(ra)} "
            f"L{_f(rt1)} Q{_f(rte)} {_f(rt0)} L{_f(rm1)} Q{_f(rch)} {_f(rm0)} H1024")


def tail(g=G, below=0):
    x, top, k = g["tail_x"], g["tip_y"] + g["stroke"] / 2, g["hook"]
    return f"M{x} {g['level'] + below} V{top + k:.1f} C{x} {top + k * 0.28:.1f} {x + k * 0.28:.1f} {top:.1f} {x + k} {top:.1f}"


def icon(ink="#111214", paper="#FFFFFF", edge=True, g=G):
    """The black and white icon: ink above the surface, paper below (the cat)."""
    body = surface(g) + " V1024 H0 Z"
    hair = (f'<path d="{APPLE}" fill="none" stroke="#000" stroke-opacity="0.14" stroke-width="2"/>' if edge else "")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024"><title>rika!</title>'
            f'<defs><clipPath id="t"><path d="{APPLE}"/></clipPath><clipPath id="o"><rect width="1024" height="{g["level"]}"/></clipPath></defs>'
            f'<path d="{APPLE}" fill="{ink}"/><g clip-path="url(#t)"><path d="{body}" fill="{paper}"/>'
            f'<g clip-path="url(#o)"><path d="{tail(g, 8)}" fill="none" stroke="{paper}" stroke-width="{g["stroke"]}" stroke-linecap="round"/></g></g>'
            f'{hair}</svg>')


def glyph(ink="#111214", g=G):
    """Symbolic glyph (no tile): the cat above a surface bar one stroke thick with round ends, for 16 px, tray, in-app."""
    w = g["stroke"]
    x0, x1 = 112, g["tail_x"] + g["hook"] + w / 2
    body = surface(g) + f" V{g['level'] + w} H0 Z"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0 - 8} {g["tip_y"] - 24} {x1 - x0 + 16} {g["level"] + w - g["tip_y"] + 32}"><title>rika!</title>'
            f'<defs><clipPath id="b"><rect x="{x0}" y="0" width="{x1 - x0}" height="{g["level"] + w}" rx="{w / 2}"/></clipPath></defs>'
            f'<path d="{body}" fill="{ink}" clip-path="url(#b)"/>'
            f'<path d="{tail(g)}" fill="none" stroke="{ink}" stroke-width="{w}" stroke-linecap="round"/></svg>')


def hairline(g=G):
    """The mark as a 24-unit hairline icon for web/src/lib/icons.ts: the surface line rising into the cat, and the tail."""
    x0, x1, y0 = 176, g["tail_x"] + g["hook"] + g["stroke"] / 2, g["tip_y"]
    k = 20 / (x1 - x0)
    def tx(x): return 2 + (x - x0) * k
    def ty(y): return 12 - (g["level"] - y0) * k / 2 + (y - y0) * k
    out, tokens = [], surface(g).replace("M", " M ").replace("H", " H ").replace("Q", " Q ").replace("L", " L ").split()
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "H":
            out.append(f"H{max(2, min(22, tx(float(tokens[i + 1])))):.2f}"); i += 2
        elif t in "MQL":
            n = 2 if t in "ML" else 4
            nums = [float(v) for v in tokens[i + 1:i + 1 + n]]
            pts = [f"{max(2, min(22, tx(nums[j]))):.2f} {ty(nums[j + 1]):.2f}" for j in range(0, n, 2)]
            out.append(t + " ".join(pts)); i += 1 + n
        else:
            i += 1
    x, top, h = tx(g["tail_x"]), ty(g["tip_y"] + g["stroke"] / 2), g["hook"] * k
    tail = f"M{x:.2f} {ty(g['level']):.2f}V{top + h:.2f}C{x:.2f} {top + h * 0.28:.2f} {x + h * 0.28:.2f} {top:.2f} {x + h:.2f} {top:.2f}"
    return "".join(out) + tail


def main():
    """Write every icon source and raster. Needs rsvg-convert and magick on PATH
    (nix shell nixpkgs#librsvg nixpkgs#imagemagick)."""
    import subprocess
    import tempfile
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sources = {root / "desktop/icons/icon.svg": icon(), root / "desktop/icons/symbolic.svg": glyph(),
               root / "web/public/favicon.svg": icon(edge=False)}
    for path, text in sources.items():
        path.write_text(text)
    rasters = [(root / "desktop/icons/icon.png", 256)] + [
        (root / f"integrations/browser/icons/icon-{n}.png", n) for n in (16, 32, 48, 96, 128)]
    for path, size in rasters:
        subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), str(root / "desktop/icons/icon.svg"),
                        "-o", str(path)], check=True)
    # Windows wants one .ico holding every size its shell draws (tauri-build embeds it in the executable).
    with tempfile.TemporaryDirectory() as folder:
        sizes = []
        for size in (16, 24, 32, 48, 64, 256):
            sizes.append(str(Path(folder) / f"{size}.png"))
            subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), str(root / "desktop/icons/icon.svg"),
                            "-o", sizes[-1]], check=True)
        subprocess.run(["magick", *sizes, str(root / "desktop/icons/icon.ico")], check=True)
    print("kitty icon path for web/src/lib/icons.ts:\n" + hairline())


if __name__ == "__main__":
    main()
