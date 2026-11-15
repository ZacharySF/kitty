"""Untrusted resume imports: editable sources, Overleaf-style ZIPs and reconstructions.

Source formats (HTML, Typst, LaTeX) import as-is with their assets. PDF and DOCX are
reconstructed as plain editable HTML text: layout, columns and fonts are not recovered,
so the original file is retained under ``originals/`` for side-by-side review. Every
binary parser runs in the sandboxed worker; archives are bounded before extraction.
"""
from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile

from kitty.core.workshop import render, store, templates
from kitty.core.workshop.store import WorkshopError

MAX_FILES_PER_IMPORT = 32
MAX_MEMBERS = store.MAX_FILES
MAX_RATIO = 200  # compressed-to-uncompressed bound against archive bombs
SOURCE_TYPES = {".html": "html", ".htm": "html", ".typ": "typst", ".tex": "latex"}
CONVERTED = {".pdf", ".docx", ".md", ".markdown", ".json", ".txt"}
_ENTRYPOINTS = ("resume", "main", "cv", "index")
_LATEX_PROGRAM = re.compile(r"^\s*%\s*!\s*TeX\s+(?:TS-)?program\s*=\s*(\w+)", re.I | re.M)


def _decode_text(raw: bytes, name: str) -> str:
    try:
        value = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise WorkshopError(f"{name} is not UTF-8 text") from None
    if "\x00" in value or len(value.encode()) > store.MAX_SOURCE:
        raise WorkshopError(f"{name} is not text or exceeds 1 MiB")
    return value


def _title(name: str) -> str:
    stem = PurePosixPath(name).stem.replace("_", " ").replace("-", " ").strip()
    return (stem or "Imported resume")[:200]


def _engine(source: str, settings) -> str:
    """A `% !TEX program =` magic comment wins; otherwise an installed LaTeX engine."""
    match = _LATEX_PROGRAM.search(source[:4000])
    program = match[1].lower() if match else ""
    return {"pdflatex": "pdflatex", "lualatex": "lualatex"}.get(
        program, render.preferred_engine(settings, "latex"))


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _members(raw: bytes, name: str) -> dict[str, bytes]:
    """Every regular member, bounded and path-checked before any byte is extracted."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise WorkshopError(f"{name} is not a valid ZIP archive") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS + 64:
            raise WorkshopError(f"{name} has too many entries")
        files, total, seen = [], 0, set()
        for info in infos:
            mode = info.external_attr >> 16
            if info.create_system == 3 and stat.S_ISLNK(mode):
                raise WorkshopError(f"{name} contains a symbolic link; links are not imported")
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                raise WorkshopError(f"{name} is encrypted; provide an unlocked archive")
            member = info.filename
            if member.startswith("__MACOSX/") or PurePosixPath(member).name == ".DS_Store":
                continue
            store.safe_path(member)
            if member in seen:
                raise WorkshopError(f"{name} contains duplicate entry {member}")
            seen.add(member)
            total += info.file_size
            if total > store.MAX_UPLOAD or info.file_size > MAX_RATIO * max(info.compress_size, 1):
                raise WorkshopError(f"{name} expands beyond the 16 MiB project limit")
            files.append(info)
        if not files:
            raise WorkshopError(f"{name} contains no files")
        if len(files) > MAX_MEMBERS:
            raise WorkshopError(f"{name} has more than {MAX_MEMBERS} files")
        result = {}
        for info in files:
            with archive.open(info) as stream:
                data = stream.read(info.file_size + 1)
            if len(data) != info.file_size:
                raise WorkshopError(f"{name} entry sizes do not match their contents")
            result[info.filename] = data
    # GitHub-style archives wrap the project in one folder; strip it.
    roots = {PurePosixPath(path).parts[0] for path in result}
    if len(roots) == 1 and all(len(PurePosixPath(path).parts) > 1 for path in result):
        prefix = next(iter(roots)) + "/"
        result = {path[len(prefix):]: data for path, data in result.items()}
    return result


def _choose_entrypoint(paths: list[str], name: str) -> str:
    sources = [p for p in paths if PurePosixPath(p).suffix.lower() in SOURCE_TYPES]
    if not sources:
        raise WorkshopError(f"{name} has no .html, .typ or .tex resume source")
    if len(sources) == 1:
        return sources[0]
    shallow = min(len(PurePosixPath(p).parts) for p in sources)
    for stem in _ENTRYPOINTS:
        found = [p for p in sources if len(PurePosixPath(p).parts) == shallow and PurePosixPath(p).stem.lower() == stem]
        if len(found) == 1:
            return found[0]
    raise WorkshopError(f"{name} has several possible entrypoints ({', '.join(sorted(sources)[:6])}); "
                        "rename the main file to resume.* or main.*")


def _project(files: dict[str, bytes], name: str, original: tuple[str, bytes] | None, settings) -> dict:
    entrypoint = _choose_entrypoint(sorted(files), name)
    suffix = PurePosixPath(entrypoint).suffix.lower()
    fmt = SOURCE_TYPES[suffix]
    source = _decode_text(files[entrypoint], entrypoint)
    if fmt == "latex" and "\\documentclass" not in source:
        raise WorkshopError(f"{entrypoint} is a LaTeX fragment; import the project's main file")
    assets = {path: _b64(data) for path, data in files.items() if path != entrypoint}
    if original:
        assets["originals/" + original[0]] = _b64(original[1])
    return {"title": _title(name), "format": fmt, "source": source, "assets": assets,
            "entrypoint": entrypoint, "engine": _engine(source, settings) if fmt == "latex" else None,
            "warnings": []}


def _inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", escaped)


def markdown_html(text: str) -> str:
    """Headings, lists, emphasis and paragraphs; raw HTML is escaped, never rendered."""
    out, items, paragraph = [], [], []

    def flush():
        if paragraph:
            out.append("<p>" + "<br>".join(_inline(line) for line in paragraph) + "</p>")
            paragraph.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + "</ul>")
            items.clear()

    for line in text.splitlines():
        stripped = line.strip()
        heading = re.match(r"(#{1,6})\s+(.*)", stripped)
        bullet = re.match(r"[-*+]\s+(.*)", stripped)
        if not stripped:
            flush()
        elif heading:
            flush()
            level = min(len(heading[1]), 3)
            out.append(f"<h{level}>{_inline(heading[2])}</h{level}>")
        elif bullet:
            if paragraph:
                flush()
            items.append(bullet[1])
        else:
            if items:
                flush()
            paragraph.append(stripped)
    flush()
    return templates.html_page("\n".join(out))


def _label(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", key).replace("_", " ").strip().capitalize()


def _json_value(value, depth=0) -> str:
    if depth > 8:
        raise WorkshopError("JSON Resume nesting is too deep")
    if isinstance(value, dict):
        rows = "".join(f"<li><strong>{html.escape(_label(k))}:</strong> {_json_value(v, depth + 1)}</li>"
                       for k, v in value.items() if v not in (None, "", [], {}))
        return f"<ul>{rows}</ul>" if rows else ""
    if isinstance(value, list):
        rows = "".join(f"<li>{_json_value(v, depth + 1)}</li>" for v in value if v not in (None, "", [], {}))
        return f"<ul>{rows}</ul>" if rows else ""
    if isinstance(value, bool) or value is None:
        return html.escape(str(value).lower())
    return html.escape(str(value))


def json_resume_html(data) -> str:
    """Every JSON Resume section is kept, including custom ones; nothing is dropped."""
    if not isinstance(data, dict):
        raise WorkshopError("JSON Resume must be an object")
    basics = data.get("basics") if isinstance(data.get("basics"), dict) else {}
    parts = []
    if basics.get("name"):
        parts.append(f"<h1>{html.escape(str(basics['name']))}</h1>")
    if basics.get("label"):
        parts.append(f"<p>{html.escape(str(basics['label']))}</p>")
    contact = [str(basics[key]) for key in ("email", "phone", "url") if basics.get(key)]
    if contact:
        parts.append("<p>" + " · ".join(html.escape(item) for item in contact) + "</p>")
    rest = {k: v for k, v in basics.items() if k not in ("name", "label", "email", "phone", "url")}
    if rest:
        parts.append("<h2>Profile</h2>" + _json_value(rest))
    for key, value in data.items():
        if key in ("basics", "$schema", "meta") or value in (None, "", [], {}):
            continue
        parts.append(f"<h2>{html.escape(_label(key))}</h2>" + _json_value(value))
    return templates.html_page("\n".join(parts))


def _reconstruct(name: str, raw: bytes, settings) -> dict:
    """PDF/DOCX text through the Rust engine's sandbox; OCR only when a page has no text."""
    suffix = PurePosixPath(name).suffix.lower()
    with tempfile.TemporaryDirectory(prefix="kitty-workshop-import-") as temporary:
        folder = Path(temporary)
        if suffix == ".pdf":
            if not raw.startswith(b"%PDF-"):
                raise WorkshopError(f"{name} is not a PDF")
            (folder / "input.pdf").write_bytes(raw)
            checks = render.run_engine("inspect", {"folder": str(folder), "input": "input.pdf", "ocr": True},
                                       timeout=120)["checks"]
            text, warnings = checks["extracted_text"], list(checks["warnings"])
        else:
            if not raw.startswith(b"PK"):
                raise WorkshopError(f"{name} is not a DOCX file")
            (folder / "input.docx").write_bytes(raw)
            text = render.run_engine("docx", {"folder": str(folder)}, timeout=120)["text"]
            warnings = []
    if not text.strip():
        raise WorkshopError(f"{name} contains no extractable text; provide the editable source instead")
    paragraphs = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    warnings.append(f"{name} was reconstructed as plain text. Layout, columns and fonts were not "
                    "recovered; compare against originals/{0} before using it.".format(name))
    return {"title": _title(name), "format": "html", "source": templates.text_html(paragraphs),
            "assets": {"originals/" + name: _b64(raw)}, "entrypoint": None, "engine": None,
            "warnings": warnings}


def _convert(name: str, raw: bytes, settings) -> dict:
    suffix = PurePosixPath(name).suffix.lower()
    original = {"originals/" + name: _b64(raw)}
    if suffix in (".md", ".markdown", ".txt"):
        text = _decode_text(raw, name)
        source = markdown_html(text) if suffix != ".txt" else templates.text_html(re.split(r"\n\s*\n", text))
        return {"title": _title(name), "format": "html", "source": source, "assets": original,
                "entrypoint": None, "engine": None, "warnings": []}
    if suffix == ".json":
        try:
            data = json.loads(_decode_text(raw, name))
        except json.JSONDecodeError:
            raise WorkshopError(f"{name} is not valid JSON") from None
        return {"title": _title(name), "format": "html", "source": json_resume_html(data),
                "assets": original, "entrypoint": None, "engine": None,
                "warnings": [f"{name} was converted from JSON Resume; every section was kept as text."]}
    return _reconstruct(name, raw, settings)


def prepare(files, settings) -> list[dict]:
    """Parse every upload before anything is written, so a bad file aborts the whole import."""
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES_PER_IMPORT:
        raise WorkshopError(f"Choose 1–{MAX_FILES_PER_IMPORT} files")
    decoded, total = [], 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"name", "data"}:
            raise WorkshopError("Each file needs exactly name and data")
        name = store.safe_path(item["name"])
        if "/" in name:
            raise WorkshopError("Upload file names cannot contain folders; use a ZIP for projects")
        raw = store.decode_data(item["data"])
        total += len(raw)
        if total > store.MAX_UPLOAD:
            raise WorkshopError("Import exceeds 16 MiB")
        decoded.append((name, raw))
    if len({name for name, _ in decoded}) != len(decoded):
        raise WorkshopError("Two uploads share a file name")
    projects = []
    loose = {}
    for name, raw in decoded:
        suffix = PurePosixPath(name).suffix.lower()
        if suffix == ".zip":
            projects.append(_project(_members(raw, name), name, (name, raw), settings))
        elif suffix in CONVERTED:
            projects.append(_convert(name, raw, settings))
        else:
            loose[name] = raw
    if loose:
        # Loose files are one project: exactly one source plus its assets.
        named = next((n for n in loose if PurePosixPath(n).suffix.lower() in SOURCE_TYPES), next(iter(loose)))
        projects.append(_project(loose, named, None, settings))
    return projects


def import_files(conn, settings, files) -> dict:
    projects = prepare(files, settings)
    documents, warnings = [], []
    with store.atomic(conn):
        for project in projects:
            documents.append(store.create_doc(
                conn, title=project["title"], format=project["format"], source=project["source"],
                assets=project["assets"], entrypoint=project["entrypoint"], engine=project["engine"],
                author="import"))
            warnings.extend(project["warnings"])
    return {"documents": documents, "warnings": warnings}


def source_zip(doc) -> bytes:
    """The exact saved project: entrypoint plus every asset, with fixed timestamps."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        entries = {doc["entrypoint"]: doc["source"].encode("utf-8"),
                   **{name: base64.b64decode(data) for name, data in doc["assets"].items()}}
        for name in sorted(entries):
            info = zipfile.ZipInfo(store.safe_path(name), date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, entries[name])
    return stream.getvalue()
