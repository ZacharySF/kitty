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
# Jake's Resume (https://github.com/jakegut/resume): preamble and macros verbatim; the
# sample person is replaced by placeholders, since template text is never an applicant claim.
_JAKE = r"""%-------------------------
% Resume in Latex
% Author : Jake Gutierrez
% Based off of: https://github.com/sb2nov/resume
% Source: https://github.com/jakegut/resume
%
% MIT License
%
% Copyright (c) 2020 Jake Gutierrez
%
% Permission is hereby granted, free of charge, to any person obtaining a copy
% of this software and associated documentation files (the "Software"), to deal
% in the Software without restriction, including without limitation the rights
% to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
% copies of the Software, and to permit persons to whom the Software is
% furnished to do so, subject to the following conditions:
%
% The above copyright notice and this permission notice shall be included in all
% copies or substantial portions of the Software.
%
% THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
% IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
% FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
% AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
% LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
% OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
% SOFTWARE.
%------------------------

\documentclass[letterpaper,11pt]{article}

\usepackage{latexsym}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage{marvosym}
\usepackage[usenames,dvipsnames]{color}
\usepackage{verbatim}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{fancyhdr}
\usepackage[english]{babel}
\usepackage{tabularx}
\input{glyphtounicode}

\pagestyle{fancy}
\fancyhf{} % clear all header and footer fields
\fancyfoot{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}

% Adjust margins
\addtolength{\oddsidemargin}{-0.5in}
\addtolength{\evensidemargin}{-0.5in}
\addtolength{\textwidth}{1in}
\addtolength{\topmargin}{-.5in}
\addtolength{\textheight}{1.0in}

\urlstyle{same}

\raggedbottom
\raggedright
\setlength{\tabcolsep}{0in}

% Sections formatting
\titleformat{\section}{
  \vspace{-4pt}\scshape\raggedright\large
}{}{0em}{}[\color{black}\titlerule \vspace{-5pt}]

% Ensure that generate pdf is machine readable/ATS parsable
\pdfgentounicode=1

%-------------------------
% Custom commands
\newcommand{\resumeItem}[1]{
  \item\small{
    {#1 \vspace{-2pt}}
  }
}

\newcommand{\resumeSubheading}[4]{
  \vspace{-2pt}\item
    \begin{tabular*}{0.97\textwidth}[t]{l@{\extracolsep{\fill}}r}
      \textbf{#1} & #2 \\
      \textit{\small#3} & \textit{\small #4} \\
    \end{tabular*}\vspace{-7pt}
}

\newcommand{\resumeSubSubheading}[2]{
    \item
    \begin{tabular*}{0.97\textwidth}{l@{\extracolsep{\fill}}r}
      \textit{\small#1} & \textit{\small #2} \\
    \end{tabular*}\vspace{-7pt}
}

\newcommand{\resumeProjectHeading}[2]{
    \item
    \begin{tabular*}{0.97\textwidth}{l@{\extracolsep{\fill}}r}
      \small#1 & #2 \\
    \end{tabular*}\vspace{-7pt}
}

\newcommand{\resumeSubItem}[1]{\resumeItem{#1}\vspace{-4pt}}

\renewcommand\labelitemii{$\vcenter{\hbox{\tiny$\bullet$}}$}

\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.15in, label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-5pt}}

%-------------------------------------------
%%%%%%  RESUME STARTS HERE  %%%%%%%%%%%%%%%%%%%%%%%%%%%%

\begin{document}

%----------HEADING----------
\begin{center}
    \textbf{\Huge \scshape Your Name} \\ \vspace{1pt}
    \small Phone $|$ \href{mailto:you@example.com}{\underline{you@example.com}} $|$
    \href{https://linkedin.com/in/your-profile}{\underline{linkedin.com/in/your-profile}} $|$
    \href{https://github.com/your-username}{\underline{github.com/your-username}}
\end{center}

%-----------EDUCATION-----------
\section{Education}
  \resumeSubHeadingListStart
    \resumeSubheading
      {Institution}{City, State}
      {Degree and major}{Start -- End}
  \resumeSubHeadingListEnd

%-----------EXPERIENCE-----------
\section{Experience}
  \resumeSubHeadingListStart
    \resumeSubheading
      {Role}{Start -- End}
      {Organization}{City, State}
      \resumeItemListStart
        \resumeItem{Describe your work, scope, and evidenced outcomes.}
      \resumeItemListEnd
  \resumeSubHeadingListEnd

%-----------PROJECTS-----------
\section{Projects}
    \resumeSubHeadingListStart
      \resumeProjectHeading
          {\textbf{Project name} $|$ \emph{Tools you used}}{Start -- End}
          \resumeItemListStart
            \resumeItem{Describe what you built and what it did.}
          \resumeItemListEnd
    \resumeSubHeadingListEnd

%-----------PROGRAMMING SKILLS-----------
\section{Technical Skills}
 \begin{itemize}[leftmargin=0.15in, label={}]
    \small{\item{
     \textbf{Languages}{: Skills you can substantiate} \\
     \textbf{Frameworks}{: } \\
     \textbf{Developer Tools}{: } \\
     \textbf{Libraries}{: }
    }}
 \end{itemize}

\end{document}
"""
_TEMPLATES = [
    {"id": "html-clean", "label": "Clean HTML", "format": "html", "description": "Readable, multi-page HTML and CSS.", "source": _HTML},
    {"id": "typst-clean", "label": "Clean Typst", "format": "typst", "description": "Compact editable Typst project; local engine required.", "source": _TYPST},
    {"id": "latex-clean", "label": "Clean LaTeX", "format": "latex", "description": "Article layout; offline Tectonic bundle required.", "source": _LATEX},
    {"id": "latex-jake", "label": "Jake's Resume (LaTeX)", "format": "latex", "engine": "pdflatex",
     "description": "Jake Gutierrez's one-page software resume (MIT); compiles with pdfLaTeX.", "source": _JAKE},
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
