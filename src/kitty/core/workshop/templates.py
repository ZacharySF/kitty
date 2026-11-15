"""Small, readable local templates; blank fields are not applicant claims."""
from .store import WorkshopError

_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Resume</title>
<style>
@page { size: A4; margin: 18mm; }
body { font-family: sans-serif; font-size: 11pt; line-height: 1.4; color: #172033; }
h1 { font-size: 24pt; margin-bottom: 4pt; }
h2 { font-size: 13pt; border-bottom: 1px solid #718096; margin-top: 18pt; }
p, li { margin: 5pt 0; }
h1, h2, h3 { break-after: avoid; }
</style></head><body>
<h1>Your name</h1>
<p>Contact details</p>
<h2>Summary</h2><p>Write a factual summary of your experience.</p>
<h2>Experience</h2><h3>Role — Organization</h3><p>Dates</p>
<ul><li>Describe your work, scope, and evidenced outcomes.</li></ul>
<h2>Education</h2><p>Qualification — Institution</p>
<h2>Skills</h2><p>List skills you can substantiate.</p>
</body></html>
"""
_TYPST = """#set page(paper: "a4", margin: 18mm)
#set text(size: 11pt)
#set par(leading: 0.65em)
= Your name
Contact details

== Summary
Write a factual summary of your experience.

== Experience
*Role — Organization* \\ Dates
- Describe your work, scope, and evidenced outcomes.

== Education
Qualification — Institution

== Skills
List skills you can substantiate.
"""
_LATEX = r"""\documentclass[11pt,a4paper]{article}
\usepackage[margin=18mm]{geometry}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\begin{document}
{\LARGE\bfseries Your name}\par
Contact details
\section*{Summary}
Write a factual summary of your experience.
\section*{Experience}
\textbf{Role --- Organization}\par Dates
\begin{itemize}
\item Describe your work, scope, and evidenced outcomes.
\end{itemize}
\section*{Education}
Qualification --- Institution
\section*{Skills}
List skills you can substantiate.
\end{document}
"""
_TEMPLATES = [
    {"id": "html-clean", "label": "Clean HTML", "format": "html", "description": "Readable, multi-page HTML and CSS.", "source": _HTML},
    {"id": "typst-clean", "label": "Clean Typst", "format": "typst", "description": "Compact editable Typst project; local engine required.", "source": _TYPST},
    {"id": "latex-clean", "label": "Clean LaTeX", "format": "latex", "description": "Article layout; offline Tectonic bundle required.", "source": _LATEX},
]


def list_templates():
    return [{key: value for key, value in item.items() if key != "source"} for item in _TEMPLATES]


def get_template(template_id):
    for item in _TEMPLATES:
        if item["id"] == template_id:
            return dict(item)
    raise WorkshopError("Unknown resume template")


def html_page(body):
    """A complete page around already-escaped HTML, using the clean template's styles."""
    return _HTML.split("<h1>", 1)[0] + body + "\n</body></html>\n"


def text_html(paragraphs):
    """Escape reconstructed text without inventing formatting or credentials."""
    import html
    return html_page("\n".join("<p>" + html.escape(line).replace("\n", "<br>") + "</p>"
                               for line in paragraphs if line.strip()))
