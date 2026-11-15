"""Builds /tmp/design-labs/tour/index.html from shots.json: one section per area, every screenshot with its name."""
import html
import json
from pathlib import Path

import sys
out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/design-labs/tour")
heading = sys.argv[2] if len(sys.argv) > 2 else "rika! — the whole app"
shots = json.loads((out / "shots.json").read_text())
sections: dict[str, list[dict]] = {}
for shot in shots:
    sections.setdefault(shot["section"], []).append(shot)
parts = ["<!doctype html><meta charset=utf-8><title>rika!</title>",
         "<style>body{font:17px system-ui,sans-serif;margin:0;background:#000;color:#f5f5f7}main{max-width:1300px;margin:0 auto;padding:40px}"
         "h1,h2,h3{font-weight:500}h2{margin:56px 0 6px;font-size:28px}h3{margin:30px 0 10px;font-size:19px;color:#a1a1a6}"
         "nav{display:flex;flex-wrap:wrap;gap:8px 18px;margin:14px 0 0}a{color:#f5f5f7}img{width:100%;border:1px solid #2c2c2e;border-radius:12px}</style>",
         f"<main><h1>{html.escape(heading)} ({len(shots)} screenshots, demo data)</h1><nav>"]
parts += [f'<a href="#{html.escape(name.lower().replace(" ", "-"))}">{html.escape(name)} ({len(items)})</a>' for name, items in sections.items()]
parts.append("</nav>")
for name, items in sections.items():
    parts.append(f'<h2 id="{html.escape(name.lower().replace(" ", "-"))}">{html.escape(name)}</h2>')
    for shot in items:
        parts.append(f'<h3>{html.escape(shot["title"])}</h3><img loading="lazy" src="{html.escape(shot["file"])}" alt="{html.escape(shot["title"])}">')
parts.append("</main>")
(out / "index.html").write_text("\n".join(parts))
print(len(shots), "shots in", len(sections), "sections")
