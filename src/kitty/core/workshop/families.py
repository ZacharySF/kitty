"""Classic, Modern and Compact: one black-and-white resume family in LaTeX and HTML.

Source Serif headings over Source Sans text, a centred name over a pipe-separated contact
line, small-caps section headings over full-width rules, no colour. Classic and Modern set
10.5pt on US letter with 0.6in side margins; Compact sets 9.5pt with tight lists so a long
history fits one page. Every page is one column (the ATS check reads it in one order) and
uses only the standard headings parsers file content under. The text is placeholders, never
an applicant claim.

LaTeX fonts come from the CTAN packages ``sourceserifpro`` and ``sourcesanspro`` (SIL OFL,
pdfLaTeX Type 1); without them the page falls back to Latin Modern. HTML compiles offline,
so it names installed faces (Source Serif 4 / Source Sans 3, older Pro names) and falls back
to Liberation.
"""

# --- LaTeX -------------------------------------------------------------------------------


def _latex_preamble(summary, *, size, leading, small, small_leading, hmargin, vmargin):
    return rf"""% {summary}
% Source Serif Pro headings over Source Sans Pro text, black and greys only, US letter.
\documentclass[letterpaper,11pt]{{article}}

\usepackage[letterpaper,hmargin={hmargin},vmargin={vmargin}]{{geometry}}
\usepackage[T1]{{fontenc}}
% CTAN sourceserifpro, sourcesanspro and ly1 (fonts: SIL Open Font License). Without them
% the page falls back to Latin Modern.
\newif\ifsourcefonts
\IfFileExists{{sourceserifpro.sty}}{{\IfFileExists{{sourcesanspro.sty}}{{%
  \IfFileExists{{ly1enc.def}}{{\sourcefontstrue}}{{}}}}{{}}}}{{}}
\ifsourcefonts
  \usepackage[semibold,lining]{{sourceserifpro}}
  \usepackage[default,semibold,lining]{{sourcesanspro}}
\else
  \usepackage{{lmodern}}\renewcommand{{\familydefault}}{{\sfdefault}}
\fi
\usepackage{{xcolor}}
\usepackage{{titlesec}}
\usepackage{{enumitem}}
\usepackage[hidelinks]{{hyperref}}
\input{{glyphtounicode}}
% Map every glyph (ligatures, small caps) to text, so the PDF is machine readable.
\pdfgentounicode=1

\renewcommand{{\normalsize}}{{\fontsize{{{size}pt}}{{{leading}pt}}\selectfont}}
\renewcommand{{\small}}{{\fontsize{{{small}pt}}{{{small_leading}pt}}\selectfont}}
\definecolor{{soft}}{{gray}}{{0.33}}
\definecolor{{faint}}{{gray}}{{0.55}}
\pagestyle{{empty}}
\setlength{{\parindent}}{{0pt}}
\raggedright
\urlstyle{{same}}
\newcommand{{\sep}}{{\unskip\hspace{{0.5em}}{{\color{{faint}}\textbar}}\hspace{{0.5em}}\ignorespaces}}
"""


_LATEX_BODY_HEAD = r"""
\begin{document}

%----------HEADING----------
\begin{center}
  {\rmfamily\NAMESTYLE Your Name}\par\vspace{\NAMEGAP}
  {\small Phone \sep \href{mailto:you@example.com}{you@example.com} \sep
  \href{https://linkedin.com/in/your-profile}{linkedin.com/in/your-profile} \sep
  \href{https://github.com/your-username}{github.com/your-username}}
\end{center}
"""


def _latex_body(name_style, name_gap, sections):
    return (_LATEX_BODY_HEAD.replace(r"\NAMESTYLE", name_style).replace(r"\NAMEGAP", name_gap)
            + sections + "\n\\end{document}\n")


# Classic: Jake's Resume refined. Two-line entries, place and dates set right.
_LATEX_CLASSIC = _latex_preamble(
    "Classic: Jake's Resume layout, refined.",
    size="10.5", leading="13.6", small="9.5", small_leading="12", hmargin="0.6in", vmargin="0.55in") + r"""
% Section headings: small caps over a full-width rule.
\titleformat{\section}{\rmfamily\bfseries\scshape\fontsize{12.5pt}{15pt}\selectfont}{}{0em}{}[\vspace{1pt}{\titlerule[0.6pt]}]
\titlespacing*{\section}{0pt}{10pt}{5pt}
\setlist[itemize]{leftmargin=1.2em,topsep=2pt,itemsep=1.5pt,parsep=0pt,label={\color{faint}\textbullet}}

% \entry{what}{when or where}{under what}{under when or where}
\newcommand{\entry}[4]{\par\addvspace{6pt}%
  \textbf{#1}\hfill #2\par
  {\small\itshape\color{soft}#3\hfill #4}\par}
% \project{name}{tools}{dates}
\newcommand{\project}[3]{\par\addvspace{6pt}\textbf{#1}\sep\emph{#2}\hfill #3\par}
""" + _latex_body(r"\bfseries\fontsize{25pt}{29pt}\selectfont", "3pt", r"""
%-----------EDUCATION-----------
\section{Education}
\entry{Institution}{City, State}{Degree and major}{Start -- End}

%-----------EXPERIENCE-----------
\section{Experience}
\entry{Role}{Start -- End}{Organization}{City, State}
\begin{itemize}
  \item Describe your work, scope, and evidenced outcomes.
\end{itemize}

%-----------PROJECTS-----------
\section{Projects}
\project{Project name}{Tools you used}{Start -- End}
\begin{itemize}
  \item Describe what you built and what it did.
\end{itemize}

%-----------SKILLS-----------
\section{Skills}
\textbf{Languages}: Skills you can substantiate\par
\textbf{Frameworks}: \par
\textbf{Tools}: \par
""")

# Modern: one airy column, a bold name, hairline grey rules, role and dates on one line.
_LATEX_MODERN = _latex_preamble(
    "Modern: one clean sans column, a bold name, thin rules.",
    size="10.5", leading="14.2", small="9.75", small_leading="12.5", hmargin="0.6in", vmargin="0.55in") + r"""
% Section headings: small caps over a thin full-width rule.
\titleformat{\section}{\rmfamily\scshape\fontsize{12.5pt}{15pt}\selectfont}{}{0em}{}[\vspace{1.5pt}{\color{faint}\titlerule[0.4pt]}]
\titlespacing*{\section}{0pt}{13pt}{6pt}
\setlist[itemize]{leftmargin=1.1em,topsep=3pt,itemsep=2.5pt,parsep=0pt,label={\color{soft}\textendash}}

% \entry{what}{dates}{organization or degree}{place}
\newcommand{\entry}[4]{\par\addvspace{8pt}%
  {\fontseries{b}\selectfont #1}\hfill{\small\color{soft}#2}\par
  {\color{soft}#3 \textperiodcentered{} #4}\par}
% \project{name}{tools}{dates}
\newcommand{\project}[3]{\par\addvspace{8pt}%
  {\fontseries{b}\selectfont #1}\hspace{0.6em}{\color{soft}#2}\hfill{\small\color{soft}#3}\par}
""" + _latex_body(r"\fontseries{b}\selectfont\fontsize{27pt}{31pt}\selectfont", "5pt", r"""
%-----------EDUCATION-----------
\section{Education}
\entry{Degree and major}{Start -- End}{Institution}{City, State}

%-----------EXPERIENCE-----------
\section{Experience}
\entry{Role}{Start -- End}{Organization}{City, State}
\begin{itemize}
  \item Describe your work, scope, and evidenced outcomes.
\end{itemize}

%-----------PROJECTS-----------
\section{Projects}
\project{Project name}{Tools you used}{Start -- End}
\begin{itemize}
  \item Describe what you built and what it did.
\end{itemize}

%-----------SKILLS-----------
\section{Skills}
{\fontseries{b}\selectfont Languages}: Skills you can substantiate\par\vspace{2pt}
{\fontseries{b}\selectfont Frameworks}: \par\vspace{2pt}
{\fontseries{b}\selectfont Tools}: \par
""")

# Compact: dense one page for long histories. One line per role, tight lists.
_LATEX_COMPACT = _latex_preamble(
    "Compact: a dense single page for long histories.",
    size="9.5", leading="11.4", small="8.75", small_leading="10.5", hmargin="0.5in", vmargin="0.45in") + r"""
% Section headings: small caps over a full-width rule, close to their content.
\titleformat{\section}{\rmfamily\bfseries\scshape\fontsize{11pt}{13pt}\selectfont}{}{0em}{}[\vspace{0.5pt}{\titlerule[0.5pt]}]
\titlespacing*{\section}{0pt}{6pt}{3pt}
\setlist[itemize]{leftmargin=1em,topsep=0.5pt,itemsep=0pt,parsep=0pt,label={\color{soft}\textbullet}}

% \entry{what}{where from}{place}{dates}: one line.
\newcommand{\entry}[4]{\par\addvspace{3.5pt}%
  \textbf{#1}, #2 {\color{soft}\textemdash{} #3}\hfill #4\par}
% \project{name}{tools}{dates}
\newcommand{\project}[3]{\par\addvspace{3.5pt}\textbf{#1}\sep\emph{#2}\hfill #3\par}
""" + _latex_body(r"\bfseries\fontsize{19pt}{22pt}\selectfont", "2pt", r"""
%-----------EDUCATION-----------
\section{Education}
\entry{Degree and major}{Institution}{City, State}{Start -- End}

%-----------EXPERIENCE-----------
\section{Experience}
\entry{Role}{Organization}{City, State}{Start -- End}
\begin{itemize}
  \item Describe your work, scope, and evidenced outcomes.
\end{itemize}

%-----------PROJECTS-----------
\section{Projects}
\project{Project name}{Tools you used}{Start -- End}
\begin{itemize}
  \item Describe what you built and what it did.
\end{itemize}

%-----------SKILLS-----------
\section{Skills}
\textbf{Languages}: Skills you can substantiate \sep \textbf{Frameworks}: \sep \textbf{Tools}: \par
""")

# --- HTML --------------------------------------------------------------------------------

_SERIF = "'Source Serif 4', 'Source Serif Pro', 'Liberation Serif', Georgia, serif"
_SANS = "'Source Sans 3', 'Source Sans Pro', 'Liberation Sans', Arial, sans-serif"

_HTML_BASE = """@page { size: letter; margin: MARGIN; }
* { box-sizing: border-box; }
html { color: #000; background: #fff; }
body { margin: 0; font-family: SANS; font-size: SIZE; line-height: LEADING; }
h1, h2 { font-family: SERIF; font-weight: 600; margin: 0; }
h1 { text-align: center; line-height: 1.1; }
.contact { text-align: center; margin: 0; font-size: 0.92em; }
.sep { color: #8c8c8c; margin: 0 0.45em; }
a { color: inherit; text-decoration: none; }
h2 { font-variant: small-caps; line-height: 1.25; padding-bottom: 1pt; border-bottom: RULE; break-after: avoid; }
p, ul { margin: 0; }
ul { padding-left: 1.2em; list-style: disc outside; }
li::marker { color: #777; }
.row { display: flex; justify-content: space-between; align-items: baseline; gap: 10pt; }
.row > :last-child { flex: none; white-space: nowrap; }
.entry { break-inside: avoid; }
"""


def _html(title, base, css, body):
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>" + title
            + "</title>\n<style>\n" + base + css + "</style></head><body>\n"
            + """<h1>Your Name</h1>
<p class="contact">Phone<span class="sep">|</span><a href="mailto:you@example.com">you@example.com</a><span class="sep">|</span><a href="https://linkedin.com/in/your-profile">linkedin.com/in/your-profile</a><span class="sep">|</span><a href="https://github.com/your-username">github.com/your-username</a></p>
""" + body + "</body></html>\n")


def _base(*, margin, size, leading, rule):
    return (_HTML_BASE.replace("MARGIN", margin).replace("SANS", _SANS).replace("SERIF", _SERIF)
            .replace("SIZE", size).replace("LEADING", leading).replace("RULE", rule))


_HTML_CLASSIC = _html("Resume", _base(margin="0.55in 0.6in", size="10.5pt", leading="1.3", rule="0.6pt solid #000"), """h1 { font-size: 25pt; margin-bottom: 3pt; }
h2 { font-size: 12.5pt; font-weight: 700; margin: 10pt 0 4pt; }
.entry + .entry { margin-top: 6pt; }
.sub { font-size: 0.905em; font-style: italic; color: #545454; }
ul { margin-top: 2pt; }
li + li { margin-top: 1.5pt; }
.skills p + p { margin-top: 1.5pt; }
""", """<h2>Education</h2>
<div class="entry">
<p class="row"><strong>Institution</strong><span>City, State</span></p>
<p class="row sub"><span>Degree and major</span><span>Start – End</span></p>
</div>
<h2>Experience</h2>
<div class="entry">
<p class="row"><strong>Role</strong><span>Start – End</span></p>
<p class="row sub"><span>Organization</span><span>City, State</span></p>
<ul><li>Describe your work, scope, and evidenced outcomes.</li></ul>
</div>
<h2>Projects</h2>
<div class="entry">
<p class="row"><span><strong>Project name</strong><span class="sep">|</span><em>Tools you used</em></span><span>Start – End</span></p>
<ul><li>Describe what you built and what it did.</li></ul>
</div>
<h2>Skills</h2>
<div class="skills">
<p><strong>Languages</strong>: Skills you can substantiate</p>
<p><strong>Frameworks</strong>: </p>
<p><strong>Tools</strong>: </p>
</div>
""")

_HTML_MODERN = _html("Resume", _base(margin="0.55in 0.6in", size="10.5pt", leading="1.36", rule="0.4pt solid #8c8c8c"), """h1 { font-size: 27pt; font-weight: 700; margin-bottom: 5pt; }
.contact { color: #545454; }
h2 { font-size: 12.5pt; font-weight: 400; margin: 13pt 0 5pt; }
strong { font-weight: 700; }
.entry + .entry { margin-top: 8pt; }
.date, .meta, .tools { color: #545454; }
.date { font-size: 0.93em; }
.tools { margin-left: 0.6em; }
ul { margin-top: 3pt; padding-left: 1.1em; list-style: square outside; }
li + li { margin-top: 2.5pt; }
.skills p + p { margin-top: 2pt; }
""", """<h2>Education</h2>
<div class="entry">
<p class="row"><strong>Degree and major</strong><span class="date">Start – End</span></p>
<p class="meta">Institution · City, State</p>
</div>
<h2>Experience</h2>
<div class="entry">
<p class="row"><strong>Role</strong><span class="date">Start – End</span></p>
<p class="meta">Organization · City, State</p>
<ul><li>Describe your work, scope, and evidenced outcomes.</li></ul>
</div>
<h2>Projects</h2>
<div class="entry">
<p class="row"><span><strong>Project name</strong><span class="tools">Tools you used</span></span><span class="date">Start – End</span></p>
<ul><li>Describe what you built and what it did.</li></ul>
</div>
<h2>Skills</h2>
<div class="skills">
<p><strong>Languages</strong>: Skills you can substantiate</p>
<p><strong>Frameworks</strong>: </p>
<p><strong>Tools</strong>: </p>
</div>
""")

_HTML_COMPACT = _html("Resume", _base(margin="0.45in 0.5in", size="9.5pt", leading="1.2", rule="0.5pt solid #000"), """h1 { font-size: 19pt; margin-bottom: 2pt; }
h2 { font-size: 11.5pt; font-weight: 700; margin: 6pt 0 2.5pt; }
.entry + .entry { margin-top: 3.5pt; }
.place { color: #545454; }
ul { padding-left: 1em; margin-top: 0.5pt; }
""", """<h2>Education</h2>
<div class="entry">
<p class="row"><span><strong>Degree and major</strong>, Institution <span class="place">— City, State</span></span><span>Start – End</span></p>
</div>
<h2>Experience</h2>
<div class="entry">
<p class="row"><span><strong>Role</strong>, Organization <span class="place">— City, State</span></span><span>Start – End</span></p>
<ul><li>Describe your work, scope, and evidenced outcomes.</li></ul>
</div>
<h2>Projects</h2>
<div class="entry">
<p class="row"><span><strong>Project name</strong><span class="sep">|</span><em>Tools you used</em></span><span>Start – End</span></p>
<ul><li>Describe what you built and what it did.</li></ul>
</div>
<h2>Skills</h2>
<p><strong>Languages</strong>: Skills you can substantiate<span class="sep">|</span><strong>Frameworks</strong>: <span class="sep">|</span><strong>Tools</strong>: </p>
""")

_PDFLATEX = " Compiles with pdfLaTeX."
_FONTS_HTML = " Uses Source Serif 4 and Source Sans 3 when installed."

TEMPLATES = [
    {"id": "latex-classic", "label": "Classic (LaTeX)", "format": "latex", "engine": "pdflatex",
     "description": "Jake's layout, refined: serif headings, sans text, black and grey." + _PDFLATEX,
     "source": _LATEX_CLASSIC},
    {"id": "latex-modern", "label": "Modern (LaTeX)", "format": "latex", "engine": "pdflatex",
     "description": "One clean sans column, a bold name and thin rules." + _PDFLATEX,
     "source": _LATEX_MODERN},
    {"id": "latex-compact", "label": "Compact (LaTeX)", "format": "latex", "engine": "pdflatex",
     "description": "A dense single page for long histories, 9.5pt with tight lists." + _PDFLATEX,
     "source": _LATEX_COMPACT},
    {"id": "html-classic", "label": "Classic (HTML)", "format": "html",
     "description": "Jake's layout, refined, in HTML and CSS." + _FONTS_HTML, "source": _HTML_CLASSIC},
    {"id": "html-modern", "label": "Modern (HTML)", "format": "html",
     "description": "One clean sans column, a bold name and thin rules, in HTML and CSS." + _FONTS_HTML,
     "source": _HTML_MODERN},
    {"id": "html-compact", "label": "Compact (HTML)", "format": "html",
     "description": "A dense single page for long histories, in HTML and CSS." + _FONTS_HTML,
     "source": _HTML_COMPACT},
]
