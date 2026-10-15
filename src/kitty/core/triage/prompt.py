"""What the triage model sees, the shape of its answer, and how that answer is checked.

One system block (marked for prompt caching) holds the rules, who I am, the
tracks and every resume; each job is a short user message. The answer is JSON
constrained by a schema, then checked here:
  * the resume must be one of my registered labels;
  * every edit note must quote a phrase that really is in the job description,
    and a line that really is in the chosen resume (or no line), or it is dropped.
Notes point at text; they never contain resume text to paste in.

My name, email, phone and profile links are redacted from the resume text before
it is sent: the model does not need them to judge fit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from kitty.config import Config, Profile
from kitty.core.resume_coach import normalize_text
from kitty.models import Job

#: Bump when the prompt or schema changes, so cached answers are asked again.
PROMPT_VERSION = 2
MAX_NOTES = 3
MAX_NOTE_CHARS = 300

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")

_RULES = """\
You triage internship postings for one student. For each posting answer with the JSON schema.

Tiers:
- A: strong fit worth a careful, tailored application. The work matches a configured track,
  the applicant's stated education and standing meet its requirements, and it is in a
  preferred location, remote when wanted, or at a company marked as a priority.
- B: decent fit. Apply with the track's default resume after reviewing the posting.
- C: poor fit or unlikely: different field, requires experience or standing absent from
  the stated profile, very school-selective without supporting evidence, or an unwanted
  location. Always say why; unknown profile facts are not qualifications.
The rules filter already removed ineligible postings; do not re-judge eligibility
unless the description clearly contradicts it.

Resume: pick the registered resume label that fits this posting best and give one sentence why.
When two labels share one resume, pick the label of the posting's track.

Edit notes (0 to 3): short notes on what to check or emphasise before applying.
- jd_phrase: copy a phrase from the job description exactly, character for character.
- resume_line: copy the resume line it relates to exactly, or "" if the resume has none.
- note: one sentence, at most 30 words, saying what to look at. Never write resume text,
  bullet points or wording to paste in, and never suggest adding anything that is not
  already true; when a skill is missing, say "add only if true".
Return no notes when there is no description.
"""


@dataclass(frozen=True)
class ResumeText:
    """A registered resume as the model sees it."""

    label: str
    description: str | None
    tracks: list[str]
    lines: list[str]


@dataclass(frozen=True)
class EditNote:
    jd_phrase: str
    resume_line: str
    note: str

    def as_text(self) -> str:
        """How the note is stored on the job and shown in Job Detail."""
        where = f' | resume: "{self.resume_line}"' if self.resume_line else ""
        return f'JD: "{self.jd_phrase}"{where} | {self.note}'


@dataclass(frozen=True)
class Verdict:
    """A checked triage answer."""

    tier: str
    tier_reason: str
    resume_label: str
    resume_reason: str
    edit_notes: list[EditNote] = field(default_factory=list)
    dropped_notes: int = 0


class AnswerError(ValueError):
    """The model's answer could not be used (bad JSON, unknown resume, missing field)."""


# ------------------------------------------------------------------ redaction


def redact(text: str, profile: Profile) -> str:
    """Remove my name, email, phone and profile links from text sent to the model."""
    out = _EMAIL.sub("[email]", text)
    out = _PHONE.sub("[phone]", out)
    for secret in (profile.email, profile.phone, profile.linkedin, profile.github, profile.website):
        if secret:
            bare = re.sub(r"^https?://", "", secret)
            out = out.replace(secret, "[link]").replace(bare, "[link]")
    for name in {profile.name, profile.given_name, profile.family_name}:
        if name and len(name) > 1:
            out = re.sub(rf"\b{re.escape(name)}\b", "[name]", out, flags=re.IGNORECASE)
    return out


# --------------------------------------------------------------------- prompts


def _distinct(resumes: list[ResumeText]) -> list[tuple[list[str], list[str], list[str], list[str]]]:
    """Resumes grouped by identical text: (labels, tracks, descriptions, lines).

    Two labels can share one file (embedded and general do); it is sent once, and
    either label is a valid pick.
    """
    groups: dict[tuple[str, ...], tuple[list[str], list[str], list[str], list[str]]] = {}
    for resume in resumes:
        labels, tracks, descriptions, _ = groups.setdefault(tuple(resume.lines), ([], [], [], resume.lines))
        labels.append(resume.label)
        tracks.extend(resume.tracks)
        if resume.description:
            descriptions.append(resume.description)
    return list(groups.values())


def system_prompt(config: Config, resumes: list[ResumeText]) -> str:
    """The cached system block: rules, who I am (no contact details), tracks, resumes."""
    profile = config.profile
    me = (f"Student: {profile.standing} at {profile.school}, {profile.degree or ''} "
          f"{profile.major}, graduating {profile.graduation}. Based in {profile.home_city}, "
          f"{profile.home_state}; {'open to remote' if profile.open_to_remote else 'on-site only'}. "
          f"Work authorization: {profile.work_authorization or 'not stated'}.")
    tracks = []
    selected = {track: resume.label for resume in resumes for track in resume.tracks}
    for track in config.tracks.tracks:
        where = ", ".join(track.location_rules.prefer_cities) or "anywhere in the US"
        if track.location_rules.allow_cities:
            where = "only in " + ", ".join(track.location_rules.allow_cities)
        tracks.append(f"- {track.name}: {'; '.join(track.search_terms)}. Prefers: {where}. "
                      f"Default resume: {selected.get(track.name, track.default_resume) or '-'}.")
    blocks = []
    for labels, sent_by, descriptions, lines in _distinct(resumes):
        text = "\n".join(redact(line, profile) for line in lines)
        blocks.append(f'<resume labels="{", ".join(labels)}" tracks="{", ".join(sent_by)}">\n'
                      f"Description: {' / '.join(descriptions) or '-'}\n{text}\n</resume>")
    return "\n\n".join([_RULES, me, "Tracks:\n" + "\n".join(tracks), "\n\n".join(blocks)])


def user_prompt(job: Job, config: Config, *, company_priority: int, company_notes: str | None) -> str:
    """One posting, with what the tool already knows about it."""
    description = (job.jd_text or "").strip()
    limit = config.triage.max_description_chars
    if len(description) > limit:
        description = description[:limit] + " [cut]"
    lines = [
        f"Company: {job.company_name}",
        f"Title: {job.title}",
        f"Location: {job.location or '-'}{' (remote)' if job.is_remote else ''}",
        f"Tracks it was routed to: {', '.join(job.tracks) or '-'}",
        f"Eligibility filter: {job.eligibility} ({job.eligibility_reason or 'no reason recorded'})",
        f"Season: {job.season or '-'}",
    ]
    if company_priority:
        lines.append(f"He marked this company as a priority ({company_priority}).")
    if job.school_selective:
        lines.append(f"School-selective: {job.school_selective_note or 'yes'}")
    if company_notes:
        lines.append(f"His notes on the company: {company_notes}")
    lines.append("Job description:\n" + (description or "(no description available)"))
    return "\n".join(lines)


def answer_schema(labels: list[str]) -> dict:
    """JSON schema the answer is constrained to (structured outputs)."""
    note = {
        "type": "object",
        "properties": {
            "jd_phrase": {"type": "string"},
            "resume_line": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["jd_phrase", "resume_line", "note"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "tier": {"type": "string", "enum": ["A", "B", "C"]},
            "tier_reason": {"type": "string"},
            "resume": {"type": "string", "enum": labels},
            "resume_reason": {"type": "string"},
            "edit_notes": {"type": "array", "items": note},
        },
        "required": ["tier", "tier_reason", "resume", "resume_reason", "edit_notes"],
        "additionalProperties": False,
    }


# ------------------------------------------------------------------- checking


def _flat(text: str) -> str:
    """Compare text the way a reader would: same characters, any whitespace, any case."""
    return " ".join(normalize_text(text).split()).casefold()


def parse_answer(text: str, jd_text: str | None, resumes: dict[str, list[str]], profile: Profile) -> Verdict:
    """Check the model's JSON and keep only notes that quote real text.

    `resumes` maps label -> resume lines as stored; they are redacted here the
    same way as in the prompt, because the model quotes the redacted text.
    """
    try:
        data = json.loads(text)
        tier, label = data["tier"], data["resume"]
        tier_reason, resume_reason = str(data["tier_reason"]).strip(), str(data["resume_reason"]).strip()
        raw_notes = data.get("edit_notes") or []
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AnswerError(f"unusable answer: {exc}") from exc
    if tier not in ("A", "B", "C"):
        raise AnswerError(f"unknown tier {tier!r}")
    if label not in resumes:
        raise AnswerError(f"unknown resume {label!r}")

    description = _flat(jd_text or "")
    resume = _flat(redact("\n".join(resumes[label]), profile))
    kept: list[EditNote] = []
    for raw in raw_notes:
        try:
            note = EditNote(str(raw["jd_phrase"]).strip(), str(raw["resume_line"]).strip(),
                            str(raw["note"]).strip())
        except (KeyError, TypeError):
            continue
        quoted = bool(note.jd_phrase) and _flat(note.jd_phrase) in description
        line_ok = not note.resume_line or _flat(note.resume_line) in resume
        if quoted and line_ok and note.note and len(note.note) <= MAX_NOTE_CHARS and len(kept) < MAX_NOTES:
            kept.append(note)
    return Verdict(tier, tier_reason, label, resume_reason, kept, dropped_notes=len(raw_notes) - len(kept))
