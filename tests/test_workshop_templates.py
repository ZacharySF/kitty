"""Classic, Modern and Compact resume templates: the family rules, and real compiles.

The compile tests drive the Rust ``kitty-workshop`` engine with whatever compilers the
environment configures (KITTY_BWRAP, KITTY_PDFLATEX, Chromium) and skip when one is missing.
"""
import io
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from kitty.core.workshop import render, templates

ENGINE = Path(__file__).resolve().parents[1] / "workshop-engine" / "target" / "release" / "kitty-workshop"
FAMILY = ["latex-classic", "latex-modern", "latex-compact", "html-classic", "html-modern", "html-compact"]
SAMPLE_PEOPLE = ("Jake Ryan", "jake@su.edu", "Southwestern University", "Gitlytics")


def _source(template_id):
    return templates.get_template(template_id)["source"]


def test_the_family_is_listed_next_to_jakes_resume():
    listed = {item["id"]: item for item in templates.list_templates()}
    assert "latex-jake" in listed and "html-clean" in listed
    for template_id in FAMILY:
        item = listed[template_id]
        assert "source" not in item
        assert item["format"] == template_id.split("-")[0]
        assert item["label"].split(" (")[0] in ("Classic", "Modern", "Compact")
    assert all(listed[f"latex-{name}"]["engine"] == "pdflatex" for name in ("classic", "modern", "compact"))


@pytest.mark.parametrize("template_id", FAMILY)
def test_family_rules_fonts_header_headings_and_no_colour(template_id):
    source = _source(template_id)
    for sample in SAMPLE_PEOPLE:
        assert sample not in source
    assert "Your Name" in source and "you@example.com" in source
    if template_id.startswith("latex"):
        assert r"\usepackage[semibold,lining]{sourceserifpro}" in source
        assert r"\usepackage[default,semibold,lining]{sourcesanspro}" in source
        assert r"\begin{center}" in source and r"\sep" in source and r"\textbar" in source
        assert r"\scshape" in source and r"\titlerule" in source and r"\pdfgentounicode=1" in source
        # Greys only: every colour is a gray level.
        assert re.findall(r"\\definecolor\{\w+\}\{(\w+)\}", source) == ["gray", "gray"]
        assert not re.search(r"\\color\{(?!soft|faint)", source)
    else:
        assert "'Source Serif 4'" in source and "'Source Sans 3'" in source
        assert "font-variant: small-caps" in source and "border-bottom" in source
        assert source.count('<span class="sep">|</span>') >= 3 and "text-align: center" in source
        for colour in re.findall(r"#([0-9a-fA-F]{3,6})\b", source):
            digits = colour if len(colour) == 6 else "".join(c * 2 for c in colour)
            assert digits[0:2] == digits[2:4] == digits[4:6], f"#{colour} is not a grey"
    for heading in ("Education", "Experience", "Projects", "Skills"):
        assert heading in source


@pytest.mark.parametrize("template_id,size,margin", [
    ("latex-classic", "10.5pt", "hmargin=0.6in"), ("latex-modern", "10.5pt", "hmargin=0.6in"),
    ("latex-compact", "9.5pt", "hmargin=0.5in"), ("html-classic", "font-size: 10.5pt", "margin: 0.55in 0.6in"),
    ("html-modern", "font-size: 10.5pt", "margin: 0.55in 0.6in"), ("html-compact", "font-size: 9.5pt", "margin: 0.45in 0.5in")])
def test_density(template_id, size, margin):
    source = _source(template_id)
    assert size in source and margin in source


def _engine_available(monkeypatch, engine_id):
    if not os.environ.get("KITTY_WORKSHOP"):
        if not ENGINE.is_file():
            pytest.skip("Build the workshop engine: cargo build --release --manifest-path workshop-engine/Cargo.toml")
        monkeypatch.setenv("KITTY_WORKSHOP", str(ENGINE))
    engines = {item["id"]: item for item in render.capabilities(None)["engines"]}
    if not engines[engine_id]["available"]:
        pytest.skip(f"{engine_id} is not available: {engines[engine_id]['reason']}")


def _has_source_fonts():
    """Whether the configured pdfLaTeX has the CTAN Source Serif/Sans packages (and ly1)."""
    configured = os.environ.get("KITTY_PDFLATEX") or shutil.which("pdflatex")
    kpsewhich = Path(configured).with_name("kpsewhich") if configured else None
    if not kpsewhich or not kpsewhich.is_file():
        return False
    found = subprocess.run([str(kpsewhich), "sourceserifpro.sty", "sourcesanspro.sty", "ly1enc.def"],
                           capture_output=True, text=True, check=False).stdout.split()
    return len(found) == 3


@pytest.mark.parametrize("template_id", FAMILY)
def test_family_compiles_to_one_page_with_a_clean_ats_report(workspace, monkeypatch, template_id):
    from kitty import web_workshop
    chosen = templates.get_template(template_id)
    _engine_available(monkeypatch, chosen.get("engine") or "chromium")
    app = SimpleNamespace(settings=workspace.settings)
    doc = web_workshop.post(app, workspace.conn, "/api/workshop", {"title": "Mine", "template_id": template_id})["document"]
    assert doc["engine"] == (chosen.get("engine") or "chromium")
    result = render.compile_doc(workspace.settings, doc)
    assert result["ok"], result["diagnostics"]
    assert result["pages"] == 1
    report = result["checks"]["ats"]
    assert report["counts"]["fail"] == 0, report["findings"]
    levels = {finding["id"]: finding["level"] for finding in report["findings"]}
    for check in ("text", "name", "email", "sections", "columns", "characters", "length"):
        assert levels[check] == "pass", (check, report["findings"])
    assert [section["kind"] for section in report["sections"]] == ["education", "experience", "projects", "skills"]
    assert "small_text" not in levels, report["findings"]
    assert report["contact"]["name"] == "Your Name" and report["contact"]["email"] == "you@example.com"
    assert report["contact"]["links"] == ["linkedin.com/in/your-profile", "github.com/your-username"]
    if chosen["format"] == "latex" and _has_source_fonts():
        from pypdf import PdfReader
        page = PdfReader(io.BytesIO(render.compiled_pdf(workspace.settings, doc))).pages[0]
        fonts = {str(font.get_object()["/BaseFont"]) for font in page["/Resources"]["/Font"].values()}
        assert any("SourceSerifPro" in name for name in fonts), fonts
        assert any("SourceSansPro" in name for name in fonts), fonts
