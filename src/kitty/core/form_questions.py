"""Read application-form questions from public ATS endpoints, without a browser.

`kitty questions collect` uses this to list every question my queue's forms
ask, so I can answer each one once in config/answers.yaml before the filler
meets it. Nothing here answers anything: it only reads questions and choices.

Where each form comes from (one polite, cached request per job):
  * Greenhouse: the public job board API with ?questions=true (JSON);
  * Lever: the job's /apply page (HTML; robots.txt allows it, crawl-delay 1 s);
  * Ashby, Workday and anything else: skipped with a reason. Ashby's form only
    comes from jobs.ashbyhq.com/api/..., which its robots.txt disallows, and
    Workday shows its form only after a login. Their questions still reach the
    queue when the filler logs what it could not answer.

Questions profile.yaml already covers (name, email, school, ...) and file
uploads are dropped here; the rest are stored by `core.questions`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Sequence
from urllib.parse import parse_qs, urlsplit

from kitty.config import Config, Profile
from kitty.core import questions as questions_core
from kitty.core.apply.fields import FormField, clean_label, is_demographic, lookup_answer, profile_plan
from kitty.core.apply.urls import detect_ats
from kitty.core.discovery.http import HttpError, PoliteHttp, RateLimited
from kitty.core.discovery.text import authorization_jurisdiction
from kitty.db.connection import transaction
from kitty.normalize import norm_question
from kitty.settings import Settings

#: ATSs whose forms can be read without a browser.
SUPPORTED = ("greenhouse", "lever")

#: Why a job's form is not read, by ATS.
SKIP_REASONS = {
    "ashby": "ashby: robots.txt disallows /api/",
    "workday": "workday: form needs a login",
    "smartrecruiters": "smartrecruiters: not supported",
    "workable": "workable: not supported",
    "other": "other site: not a known ATS",
}

#: Seconds between requests to one host (Lever's robots.txt asks for 1).
MIN_INTERVAL = 1.0
#: Forms change rarely; a week of cache makes re-runs free.
CACHE_TTL_MINUTES = 7 * 24 * 60

#: Placeholder that replaces the company's own name, so "Why Etched?" and
#: "Why Anduril?" count as one question.
COMPANY = "{company}"

#: Field kinds that take typed text (as opposed to a choice or a file).
FREE_TEXT = ("text", "textarea", "date", "number")

#: Words that mark a long free-text answer I write myself (an essay, not a fact).
_ESSAY = re.compile(
    r"\b(why|describe|description|tell us|tell me|explain (why|how|what)|share|anything else|"
    r"summary|summari[sz]e|accomplish\w*|achievements?|impressive|projects?|examples?|"
    r"cover letter|motivat\w*|passionate|passion|interests you|excites? you|excited|"
    r"challenges?|proud|bullet points|elaborate|in \d+ words|words or less|walk us through)\b"
)

#: Field names Lever uses for its EEO and diversity-survey sections.
_LEVER_DEMOGRAPHIC_NAMES = ("eeo[", "surveysResponses", "surveys[")


@dataclass(frozen=True)
class FormQuestion:
    """One question as a form defines it, before any classification."""

    label: str
    kind: str                         # text | textarea | select | multiselect | radio | checkbox | boolean | date | number | file | hidden
    required: bool = False
    choices: tuple[str, ...] = ()
    name: str = ""                    # the form's own field name, a hint for profile matching
    demographic: bool = False         # asked in an EEO / self-identification section


@dataclass(frozen=True)
class FormTarget:
    """Where one job's form lives on its ATS."""

    ats: str                          # greenhouse | lever
    board: str                        # board token / company slug on the ATS
    posting: str                      # the ATS's own posting id
    host: str = ""                    # Lever: jobs.lever.co or jobs.eu.lever.co

    @property
    def url(self) -> str:
        """The public URL the form is read from."""
        if self.ats == "greenhouse":
            return f"https://boards-api.greenhouse.io/v1/boards/{self.board}/jobs/{self.posting}?questions=true"
        return f"https://{self.host or 'jobs.lever.co'}/{self.board}/{self.posting}/apply"


# ------------------------------------------------------------------ targets


def _path_parts(url: str) -> list[str]:
    return [part for part in urlsplit(url).path.split("/") if part]


def _target_from_source_id(ats: str, source_job_id: str, url: str) -> FormTarget | None:
    """Discovery stores 'board:posting' as the source id for each ATS adapter."""
    board, _, posting = source_job_id.partition(":")
    if not board or not posting:
        return None
    host = urlsplit(url).netloc.lower()
    return FormTarget(ats, board, posting, host if ats == "lever" and host.endswith("lever.co") else "")


def _target_from_url(url: str, ats_slug: str | None) -> FormTarget | None:
    """Board and posting id from a Greenhouse or Lever URL (None when not recognised)."""
    ats = detect_ats(url)
    parts = _path_parts(url)
    query = parse_qs(urlsplit(url).query)
    if ats == "greenhouse":
        board = query["for"][0] if "for" in query else ats_slug
        if "token" in query and board:                     # embed/job_app?for=board&token=id
            return FormTarget("greenhouse", board, query["token"][0])
        if len(parts) >= 3 and parts[1] == "jobs" and parts[2].isdigit():
            return FormTarget("greenhouse", parts[0], parts[2])
        if "gh_jid" in query and ats_slug:                # a company page that embeds the board
            return FormTarget("greenhouse", ats_slug, query["gh_jid"][0])
        return None
    if ats == "lever" and len(parts) >= 2:
        return FormTarget("lever", parts[0], parts[1], urlsplit(url).netloc.lower())
    return None


def form_target(
    url: str, apply_url: str | None, source_ids: dict[str, str], ats_slug: str | None = None
) -> FormTarget | str:
    """Where to read this job's form, or the reason it is skipped.

    `source_ids` maps a discovery source ('greenhouse', 'lever', 'ashby') to its
    'board:posting' id; that wins over URL parsing because Greenhouse jobs often
    link to the company's own careers page.
    """
    for ats in SUPPORTED:
        if ats in source_ids and (target := _target_from_source_id(ats, source_ids[ats], url)):
            return target
    for candidate in (apply_url, url):
        if candidate and (target := _target_from_url(candidate, ats_slug)):
            return target
    if "ashby" in source_ids:
        return SKIP_REASONS["ashby"]
    ats = detect_ats(apply_url or url)
    if ats in SUPPORTED:
        return f"{ats}: unrecognised URL"
    return SKIP_REASONS.get(ats, SKIP_REASONS["other"])


# --------------------------------------------------------------- greenhouse

_GREENHOUSE_KINDS = {
    "input_text": "text", "textarea": "textarea", "input_file": "file", "input_hidden": "hidden",
    "multi_value_single_select": "select", "multi_value_multi_select": "multiselect",
}


def _greenhouse_question(item: dict, *, demographic: bool = False) -> FormQuestion:
    """One Greenhouse question (it may have several fields: a file plus a paste-in textarea)."""
    fields = item.get("fields") or []
    types = [f.get("type", "") for f in fields]
    kind = "file" if "input_file" in types else _GREENHOUSE_KINDS.get(types[0] if types else "", "text")
    choices = tuple(
        str(value.get("label")).strip()
        for f in fields for value in f.get("values") or [] if value.get("label")
    )
    return FormQuestion(
        label=clean_label(item.get("label") or ""), kind=kind, required=bool(item.get("required")),
        choices=choices, name=(fields[0].get("name") or "") if fields else "", demographic=demographic,
    )


def parse_greenhouse(payload: dict) -> list[FormQuestion]:
    """Questions from a Greenhouse job (boards-api ...?questions=true).

    `questions` and `location_questions` are the form; `compliance` (EEOC) and
    `demographic_questions` are self-identification, so they are marked demographic.
    """
    found = [_greenhouse_question(item) for item in payload.get("questions") or []]
    found += [_greenhouse_question(item) for item in payload.get("location_questions") or []]
    for section in payload.get("compliance") or []:
        found += [_greenhouse_question(item, demographic=True) for item in section.get("questions") or []]
    demographic = payload.get("demographic_questions") or {}
    for item in demographic.get("questions") or []:
        choices = tuple(o["label"].strip() for o in item.get("answer_options") or [] if o.get("label"))
        kind = "multiselect" if item.get("type") == "multi_value_multi_select" else "select"
        found.append(FormQuestion(clean_label(item.get("label") or ""), kind, bool(item.get("required")),
                                  choices, demographic=True))
    return [q for q in found if q.label]


# -------------------------------------------------------------------- lever


class _LeverFormReader(HTMLParser):
    """Walks Lever's #application-form and records each named control under its label.

    A label is the text of the nearest `.application-label` before the control.
    Radio buttons and checkboxes sharing a name are one question with choices.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_form = False
        self.label_parts: list[str] | None = None    # collecting a label's text
        self.label = ""                               # the label for the next control
        self.select: dict | None = None               # the <select> being read
        self.option: list[str] | None = None          # the <option> being read
        self.controls: dict[str, dict] = {}           # name -> label, kind, required, choices

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "form" and values.get("id") == "application-form":
            self.in_form = True
        if not self.in_form:
            return
        css = values.get("class", "")
        if "application-label" in css:
            self.label_parts = []
        elif "application-field" in css:
            self._finish_label()
        if tag in ("input", "select", "textarea"):
            self._finish_label()
            self._control(tag, values)
        elif tag == "option" and self.select is not None:
            self.option = [] if values.get("value") else None

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.in_form = False
        elif tag == "option" and self.option is not None and self.select is not None:
            text = " ".join("".join(self.option).split())
            if text:
                self.select["choices"].append(text)
            self.option = None
        elif tag == "select":
            self.select = None

    def handle_data(self, data: str) -> None:
        if self.option is not None:
            self.option.append(data)
        elif self.label_parts is not None:
            self.label_parts.append(data)

    def _finish_label(self) -> None:
        if self.label_parts is not None:
            self.label = " ".join("".join(self.label_parts).split())
            self.label_parts = None

    def _control(self, tag: str, values: dict[str, str]) -> None:
        """Record one input / select / textarea, grouping radios and checkboxes by name."""
        kind = values.get("type", "text").lower() if tag == "input" else tag
        name = values.get("name", "")
        if not name or kind in ("hidden", "submit", "button"):
            return
        if kind in ("email", "tel", "url", "search"):
            kind = "text"
        entry = self.controls.get(name)
        if entry is None:
            label = self.label or values.get("placeholder", "") or name
            entry = {"label": label, "kind": kind, "required": "✱" in label, "choices": []}
            self.controls[name] = entry
            self.label = ""                               # a label belongs to one control
        entry["required"] = entry["required"] or "required" in values
        if kind in ("radio", "checkbox") and values.get("value"):
            entry["choices"].append(" ".join(values["value"].split()))
        if tag == "select":
            self.select = entry


def parse_lever(markup: str) -> list[FormQuestion]:
    """Questions from a Lever /apply page. EEO and survey fields are marked demographic."""
    reader = _LeverFormReader()
    reader.feed(markup)
    reader.close()
    return [
        FormQuestion(
            label=clean_label(entry["label"]), kind=entry["kind"], required=entry["required"],
            choices=tuple(dict.fromkeys(entry["choices"])), name=name,
            demographic=name.startswith(_LEVER_DEMOGRAPHIC_NAMES),
        )
        for name, entry in reader.controls.items()
        if clean_label(entry["label"])
    ]


# --------------------------------------------------------------------- fetch


def fetch_questions(http: PoliteHttp, target: FormTarget) -> list[FormQuestion]:
    """Read one job's form questions (raises HttpError / RateLimited / ValueError)."""
    if target.ats == "greenhouse":
        return parse_greenhouse(http.get_json(target.url))
    return parse_lever(http.get_bytes(target.url).decode("utf-8", errors="replace"))


# ------------------------------------------------------------ classification

#: Question kinds `classify` returns, in the order `kitty questions list` shows them.
KINDS = ("choice", "factual", "essay", "demographic", "answered", "profile")


def is_profile_field(question: FormQuestion, profile: Profile) -> bool:
    """True when profile.yaml fills this field, or it is a file / hidden field.

    Uses the filler's own `profile_plan`, so "covered" means exactly what the
    filler would type; a profile value that is still empty does not count.
    Uploads are left out because answers.yaml cannot answer them.
    """
    if question.kind in ("file", "hidden"):
        return True
    if question.demographic or is_demographic(question.label):
        return False
    probe = FormField(key="", kind=question.kind, label=question.label, name=question.name,
                      options=question.choices)
    return profile_plan(probe, profile) is not None


def is_essay(label: str, kind: str, has_choices: bool) -> bool:
    """True for a long free-text answer I write myself ("Why do you want to join us?").

    Only typed fields whose label has an essay word ("why", "describe", "tell us")
    count, so "If other, please specify" stays factual and choices never are essays.
    """
    if has_choices or kind not in FREE_TEXT:
        return False
    return bool(_ESSAY.search(norm_question(label)))


def classify(
    label: str, kind: str, choices: tuple[str, ...], demographic: bool, config: Config,
    *, company: str | None = None, tracks: Sequence[str] = (), jurisdiction: str | None = None,
) -> str:
    """One of KINDS for a stored question: what I still have to do about it.

    answers.yaml first (I already answered it), then the profile, then
    demographic, essay, and finally choice (has options) or factual (typed fact).
    """
    if lookup_answer(
        config, label, company=company, tracks=tracks, jurisdiction=jurisdiction, options=choices,
    ) is not None:
        return "answered"
    question = FormQuestion(label, kind or "text", choices=choices, demographic=demographic)
    if kind and kind != "file" and is_profile_field(question, config.profile):
        return "profile"
    if demographic or is_demographic(label):
        return "demographic"
    if is_essay(label, kind or "text", bool(choices)):
        return "essay"
    return "choice" if choices else "factual"


def generic_label(label: str, company_names: tuple[str, ...]) -> str:
    """Replace the company's own name with {company}, so repeats across companies merge.

    Matches the name as written (case-sensitive, whole words) to avoid turning
    ordinary words into the placeholder.
    """
    for name in sorted({n for n in company_names if n and len(n) >= 3}, key=len, reverse=True):
        label = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", COMPANY, label)
    return label


# ------------------------------------------------------------------- collect


@dataclass
class CollectResult:
    """What one `questions collect` run did."""

    considered: int = 0
    fetched: int = 0                  # forms read (network or cache)
    from_cache: int = 0
    skipped: Counter = field(default_factory=Counter)    # reason -> jobs
    failed: Counter = field(default_factory=Counter)     # reason -> jobs
    questions_seen: int = 0           # questions kept across all forms read
    new_links: int = 0                # (question, job) pairs not stored before
    new_questions: int = 0            # distinct questions not stored before
    distinct_questions: int = 0       # distinct questions across the forms read


@dataclass(frozen=True)
class QueueJob:
    """A job from the queue with what is needed to find its form."""

    id: int
    company: str
    title: str
    url: str
    apply_url: str | None
    ats_slug: str | None
    source_ids: dict[str, str]


def queue_jobs(
    conn: sqlite3.Connection, *, states: list[str], tiers: list[str], limit: int | None = None
) -> list[QueueJob]:
    """Active jobs in these queue states and tiers, best tier first."""
    marks = lambda values: ",".join("?" for _ in values)         # noqa: E731
    rows = conn.execute(
        f"""
        SELECT j.id, j.title, j.url, j.apply_url, c.display_name, c.ats_slug
        FROM jobs j JOIN companies c ON c.id = j.company_id
        WHERE j.is_active = 1 AND j.queue_state IN ({marks(states)}) AND j.tier IN ({marks(tiers)})
        ORDER BY j.tier, j.id
        """,
        (*states, *tiers),
    ).fetchall()
    sources: dict[int, dict[str, str]] = {}
    for row in conn.execute(
        "SELECT job_id, source, source_job_id FROM job_sources WHERE source IN ('greenhouse','lever','ashby')"
    ):
        sources.setdefault(row["job_id"], {})[row["source"]] = row["source_job_id"] or ""
    jobs = [
        QueueJob(row["id"], row["display_name"], row["title"], row["url"], row["apply_url"],
                 row["ats_slug"], sources.get(row["id"], {}))
        for row in rows
    ]
    return jobs[:limit] if limit else jobs


def _interleave(pairs: list[tuple[QueueJob, FormTarget]]) -> list[tuple[QueueJob, FormTarget]]:
    """Alternate between ATSs so one host's pacing overlaps the other's."""
    by_ats: dict[str, list[tuple[QueueJob, FormTarget]]] = {}
    for pair in pairs:
        by_ats.setdefault(pair[1].ats, []).append(pair)
    queues = list(by_ats.values())
    ordered = []
    while any(queues):
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
    return ordered


def _failure_reason(exc: Exception) -> str:
    """A short, countable reason: 'HTTP 404', 'ConnectError', 'unreadable form'."""
    if isinstance(exc, RateLimited):
        return "rate limited (429)"
    if isinstance(exc, HttpError):
        match = re.match(r"HTTP (\d+)", str(exc))
        return f"HTTP {match.group(1)}" if match else str(exc).split(" after ")[0]
    return f"unreadable form ({type(exc).__name__})"


def _store_form(
    conn: sqlite3.Connection, job: QueueJob, found: list[FormQuestion], profile: Profile
) -> tuple[int, int, int, set[str]]:
    """Store one form's questions; return (kept, new links, new questions, their texts)."""
    kept = new_links = new_questions = 0
    texts: set[str] = set()
    with transaction(conn):
        for question in found:
            if is_profile_field(question, profile):
                continue
            text = generic_label(question.label, (job.company,))
            stored = questions_core.record_form_question(
                conn, text, job.id, field_kind=question.kind, required=question.required,
                choices=question.choices, demographic=question.demographic,
            )
            kept += 1
            new_links += int(stored.new_link)
            new_questions += int(stored.new_question)
            texts.add(stored.question.norm_hash)
    return kept, new_links, new_questions, texts


def collect(
    conn: sqlite3.Connection,
    settings: Settings,
    config: Config,
    *,
    states: list[str],
    tiers: list[str],
    limit: int | None = None,
    http: PoliteHttp | None = None,
    progress: Callable[[str], None] | None = None,
) -> CollectResult:
    """Read the forms of the queue's jobs and store their questions.

    Re-running is safe: responses come from a week-long disk cache and each
    (question, job) pair is stored once, so counts never grow on a re-run.
    One host answering 429 stops requests to that ATS for the rest of the run.
    """
    say = progress or (lambda _message: None)
    client = http or PoliteHttp(settings.cache_dir / "http" / "form-questions",
                                min_interval=MIN_INTERVAL, cache_ttl_minutes=CACHE_TTL_MINUTES)
    result = CollectResult()
    pairs: list[tuple[QueueJob, FormTarget]] = []
    for job in queue_jobs(conn, states=states, tiers=tiers, limit=limit):
        result.considered += 1
        target = form_target(job.url, job.apply_url, job.source_ids, job.ats_slug)
        if isinstance(target, str):
            result.skipped[target] += 1
        else:
            pairs.append((job, target))

    hashes: set[str] = set()
    blocked: set[str] = set()
    try:
        for index, (job, target) in enumerate(_interleave(pairs), start=1):
            head = f"[{index}/{len(pairs)}] {job.company} - {job.title}"
            if target.ats in blocked:
                result.failed[f"rate limited (429), {target.ats} skipped"] += 1
                continue
            hits_before = client.cache_hits
            try:
                found = fetch_questions(client, target)
            except RateLimited as exc:
                blocked.add(target.ats)
                result.failed[_failure_reason(exc)] += 1
                say(f"{head}: {exc}; no more {target.ats} requests this run")
                continue
            except (HttpError, ValueError, KeyError, TypeError) as exc:
                result.failed[_failure_reason(exc)] += 1
                say(f"{head}: {_failure_reason(exc)}")
                continue
            if not found:
                result.failed["form has no questions"] += 1
                say(f"{head}: no questions found")
                continue
            cached = client.cache_hits > hits_before
            result.fetched += 1
            result.from_cache += int(cached)
            kept, new_links, new_questions, seen = _store_form(conn, job, found, config.profile)
            result.questions_seen += kept
            result.new_links += new_links
            result.new_questions += new_questions
            hashes |= seen
            say(f"{head}: {len(found)} fields, {kept} kept" + (" (cache)" if cached else ""))
    finally:
        if http is None:
            client.close()
    result.distinct_questions = len(hashes)
    return result


# ---------------------------------------------------------------------- list


@dataclass(frozen=True)
class QuestionSummary:
    """One merged question with the jobs that ask it, for `questions list`."""

    id: int
    text: str
    kind: str                                        # one of KINDS
    field_kind: str | None                           # the most common field kind, None if only logged
    job_ids: tuple[int, ...]
    companies: tuple[str, ...]                       # most frequent first
    required_jobs: int                               # jobs where the form requires it
    choices: tuple[str, ...]                         # the most common choice set
    choice_sets: tuple[tuple[tuple[str, ...], int], ...]   # every choice set with its job count

    @property
    def job_count(self) -> int:
        return len(self.job_ids)


def _summary(question_id: int, text: str, links: list[sqlite3.Row], config: Config) -> QuestionSummary:
    """Merge one question's job links into a summary and classify it."""
    jobs = [link for link in links if link["job_id"] is not None]
    kinds = Counter(link["field_kind"] for link in jobs if link["field_kind"])
    sets = Counter(tuple(json.loads(link["choices"])) for link in jobs if link["choices"])
    companies = Counter(link["company"] for link in jobs if link["company"])
    field_kind = kinds.most_common(1)[0][0] if kinds else None
    choices = sets.most_common(1)[0][0] if sets else ()
    demographic = any(link["demographic"] for link in jobs)
    # A merged question is answered only when every associated form is covered.
    # A US answer must not hide a relative question also asked by an overseas or
    # unresolved job, and each form's own choices can name a different country.
    classifications = [
        classify(
            text, link["field_kind"] or "", tuple(json.loads(link["choices"] or "[]")),
            bool(link["demographic"]), config, company=link["company"],
            tracks=tuple((link["tracks"] or "").split(";")) if link["tracks"] else (),
            jurisdiction=authorization_jurisdiction(link["location"], link["country"]),
        )
        for link in jobs
    ]
    kind = (
        next((value for value in classifications if value != "answered"), "answered")
        if classifications else classify(text, field_kind or "", choices, demographic, config)
    )
    return QuestionSummary(
        id=question_id, text=text,
        kind=kind,
        field_kind=field_kind,
        job_ids=tuple(dict.fromkeys(link["job_id"] for link in jobs)),
        companies=tuple(name for name, _ in companies.most_common()),
        required_jobs=sum(1 for link in jobs if link["required"]),
        choices=choices,
        choice_sets=tuple(sets.most_common()),
    )


def summarize(conn: sqlite3.Connection, config: Config) -> list[QuestionSummary]:
    """Every stored question, classified against today's answers.yaml and profile.yaml.

    Includes questions the filler or I logged (no choices) next to collected
    ones. Most-shared first, so answering the top of the list covers the most jobs.
    """
    rows = conn.execute(
        """
        SELECT q.id, q.text, qj.job_id, qj.field_kind, qj.required, qj.choices, qj.demographic,
               c.display_name AS company, j.location, j.country,
               (SELECT group_concat(jt.track, ';') FROM job_tracks jt WHERE jt.job_id = j.id) AS tracks
        FROM screening_questions q
        LEFT JOIN question_jobs qj ON qj.question_id = q.id
        LEFT JOIN jobs j ON j.id = qj.job_id
        LEFT JOIN companies c ON c.id = j.company_id
        ORDER BY q.id
        """
    ).fetchall()
    grouped: dict[int, list[sqlite3.Row]] = {}
    texts: dict[int, str] = {}
    for row in rows:
        grouped.setdefault(row["id"], []).append(row)
        texts[row["id"]] = row["text"]
    found = [_summary(qid, texts[qid], links, config) for qid, links in grouped.items()]
    return sorted(found, key=lambda s: (-s.job_count, -s.required_jobs, s.text.lower()))
