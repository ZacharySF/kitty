"""Loading and validating the YAML config files.

Every file has a Pydantic model with `extra="forbid"`, so a typo is an error
rather than a silently ignored key. Errors are reported as
`config/tracks.yaml:14  tracks.0.weekly_targt  ...` by mapping Pydantic's error
location back onto the YAML node tree, which is why the raw text is composed
(`yaml.compose`) as well as parsed.
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
import re
from typing import Any, Iterable, Literal, Sequence
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel, ConfigDict, Field, PrivateAttr, StrictBool, ValidationError, field_validator, model_validator,
)

AtsKind = Literal["greenhouse", "lever", "ashby", "smartrecruiters", "workday", "workable"]
#: "none" = no board I can poll; the reason (and the real ATS, if known) goes in notes.
WatchlistAts = Literal["greenhouse", "lever", "ashby", "smartrecruiters", "workday", "workable", "none"]
TriageDepth = Literal["none", "rules", "llm"]

# ------------------------------------------------------------------ problems


@dataclass(frozen=True)
class ConfigProblem:
    """One validation failure, with the file line when it can be located."""

    file: str
    line: int | None
    location: str
    message: str
    hint: str | None = None

    def __str__(self) -> str:
        where = f"{self.file}:{self.line}" if self.line else self.file
        head = f"{where}  {self.location}: {self.message}" if self.location else f"{where}  {self.message}"
        return f"{head}\n    fix: {self.hint}" if self.hint else head


class ConfigError(Exception):
    """Raised when a config file is missing, unparseable or invalid."""

    def __init__(self, problems: Sequence[ConfigProblem]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(str(p) for p in self.problems))


# ------------------------------------------------------------------- loading


def _yaml_line(node: Any, loc: Iterable[Any]) -> int | None:
    """Walk a composed YAML node tree along a Pydantic error location."""
    if node is None:
        return None
    current = node
    line = current.start_mark.line + 1
    for part in loc:
        if isinstance(current, yaml.MappingNode) and isinstance(part, str):
            for key_node, value_node in current.value:
                if key_node.value == part:
                    line = key_node.start_mark.line + 1
                    current = value_node
                    break
            else:
                break
        elif isinstance(current, yaml.SequenceNode) and isinstance(part, int):
            if part >= len(current.value):
                break
            current = current.value[part]
            line = current.start_mark.line + 1
        else:
            break
    return line


def _hint_for(error: dict[str, Any]) -> str | None:
    """Turn a Pydantic error into an instruction for fixing the YAML."""
    kind = error.get("type", "")
    if kind == "extra_forbidden":
        return "unknown key - remove it or check the spelling"
    if kind == "missing":
        return "this key is required; add it"
    if kind == "literal_error":
        expected = error.get("ctx", {}).get("expected")
        return f"use one of {expected}" if expected else None
    if kind.startswith("int_"):
        return "must be a whole number"
    if kind.startswith("bool_"):
        return "must be true or false"
    if kind.startswith("list_"):
        return "must be a list (use '- item' lines)"
    return None


def load_yaml(path: Path) -> tuple[Any, Any]:
    """Return (parsed data, composed node tree) for a YAML file.

    The node tree is only used to attach line numbers to validation errors.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError([
            ConfigProblem(
                file=str(path), line=None, location="", message="file not found",
                hint=f"copy {path.name}.example to {path.name}, or run `kitty init`",
            )
        ]) from exc

    try:
        data = yaml.safe_load(text)
        node = yaml.compose(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise ConfigError([
            ConfigProblem(
                file=str(path),
                line=(mark.line + 1) if mark else None,
                location="",
                message=f"YAML syntax error: {getattr(exc, 'problem', exc)}",
                hint="check indentation and that every ':' has a space after it",
            )
        ]) from exc
    return (data if data is not None else {}), node


def parse_file(path: Path, model: type[BaseModel]) -> Any:
    """Load a YAML file and validate it against `model`, or raise ConfigError."""
    data, node = load_yaml(path)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        problems = [
            ConfigProblem(
                file=str(path),
                line=_yaml_line(node, error["loc"]),
                location=".".join(str(part) for part in error["loc"]),
                message=error["msg"],
                hint=_hint_for(error),
            )
            for error in exc.errors()
        ]
        raise ConfigError(problems) from exc


class _Base(BaseModel):
    """Shared settings: unknown keys are errors, not surprises later."""

    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------- profile


class Target(_Base):
    kind: str
    season: str | None = None
    note: str | None = None


class Profile(_Base):
    """config/profile.yaml - facts about me. Nothing here is ever invented.

    The form filler (Stage 7) copies these into application forms as written.
    Optional fields left empty are simply not filled.
    """

    name: str
    first_name: str | None = None      # forms split the name; defaults to the first word of name
    last_name: str | None = None       # defaults to the last word of name
    email: str
    phone: str | None = None
    linkedin: str | None = None
    github: str | None = None
    website: str | None = None
    school: str
    school_city: str | None = None     # campus city; distinguishes campuses in school dropdowns
    degree: str | None = None          # e.g. "B.S. Engineering"
    major: str
    #: What forms' Major / Discipline / Field of study questions get; defaults to `major`.
    discipline: str | None = None
    #: Track name -> discipline for jobs in that track (e.g. cpu: Computer Engineering).
    discipline_by_track: dict[str, str] = Field(default_factory=dict)
    standing: str
    graduation: str = Field(description="YYYY-MM")
    home_city: str
    home_state: str
    #: Track name -> city for that track's applications; same state.
    city_by_track: dict[str, str] = Field(default_factory=dict)
    country: str | None = None         # e.g. "United States"; filled into Country fields
    commute_radius_miles: int = 50
    open_to_remote: bool = True
    work_authorization: str | None = None
    targets: list[Target] = Field(default_factory=list)

    @property
    def given_name(self) -> str:
        return self.first_name or next(iter(self.name.split()), "")

    @property
    def family_name(self) -> str:
        return self.last_name or (self.name.split()[-1] if self.name.strip() else "")

    def discipline_for(self, tracks: Sequence[str] = ()) -> str:
        """The discipline to put on a job's form: its first track's override, else the default.

        Forms ask one Major / Discipline question; which one I give depends on what
        the job is about, so the job's tracks pick it.
        """
        for track in tracks:
            if track in self.discipline_by_track:
                return self.discipline_by_track[track]
        return self.discipline or self.major

    def city_for(self, tracks: Sequence[str] = ()) -> str:
        """The city forms' location boxes get: a job track's override, else home_city."""
        for track in tracks:
            if track in self.city_by_track:
                return self.city_by_track[track]
        return self.home_city


# -------------------------------------------------------------------- tracks


class NearRule(_Base):
    city: str
    state: str
    radius_miles: int = 40
    #: What JobSpy searches here; empty = the track's own search terms.
    terms: list[str] = Field(default_factory=list)


class LocationRules(_Base):
    allow_remote: bool = True
    countries: list[str] = Field(default_factory=lambda: ["US"])
    prefer_cities: list[str] = Field(default_factory=list)
    allow_cities: list[str] = Field(default_factory=list)
    #: More cities, each only for employers whose name contains one of the listed
    #: phrases (e.g. Springfield: [Example Coffee]). Only read when allow_cities is set.
    allow_city_employers: dict[str, list[str]] = Field(default_factory=dict)
    near: NearRule | None = None
    #: Extra JobSpy searches around other places;
    #: what they find still has to pass allow_cities / allow_city_employers.
    also_near: list[NearRule] = Field(default_factory=list)


class EligibilityRules(_Base):
    """Phrases the Stage 2 rules filter looks for. No LLM involved."""

    drop_if_requires: list[str] = Field(default_factory=list)
    drop_if_grad_year_required: list[int] = Field(default_factory=list)
    boost_phrases: list[str] = Field(default_factory=list)
    #: Drop postings whose listed hourly pay tops out below this; when set, a listed
    #: rate at or above it makes the posting eligible. Unlisted pay changes nothing.
    min_hourly_pay: float | None = Field(default=None, ge=0)


class Track(_Base):
    name: str
    weekly_target: int = Field(default=0, ge=0)
    triage_depth: TriageDepth = "rules"
    default_resume: str | None = None
    search_terms: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    location_rules: LocationRules = Field(default_factory=LocationRules)
    eligibility_rules: EligibilityRules = Field(default_factory=EligibilityRules)


class TracksFile(_Base):
    tracks: list[Track]

    @field_validator("tracks")
    @classmethod
    def _unique_names(cls, tracks: list[Track]) -> list[Track]:
        names = [t.name for t in tracks]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"duplicate track names: {sorted(duplicates)}")
        return tracks


# ----------------------------------------------------------------- watchlist


class WatchlistCompany(_Base):
    name: str
    ats: WatchlistAts
    slug: str = ""
    tracks: list[str] = Field(default_factory=list)
    school_selective: bool = False
    notes: str = ""
    expected_open_months: list[int] = Field(default_factory=list)
    priority: int = 0

    @field_validator("expected_open_months")
    @classmethod
    def _valid_months(cls, months: list[int]) -> list[int]:
        bad = [m for m in months if not 1 <= m <= 12]
        if bad:
            raise ValueError(f"months must be 1-12, got {bad}")
        return months

    @model_validator(mode="after")
    def _slug_fits_ats(self) -> "WatchlistCompany":
        """Workday needs 'tenant/wdN/site'; catching it here beats a 404 in Stage 2."""
        if self.ats == "none":
            if self.slug:
                raise ValueError("ats is 'none', so slug must be empty")
            if not self.notes.strip():
                raise ValueError("ats is 'none', so notes must say why (and the real ATS if known)")
        elif not self.slug:
            raise ValueError(f"slug is required when ats is '{self.ats}'")
        elif self.ats == "workday" and len(self.slug.split("/")) != 3:
            raise ValueError("workday slug must be 'tenant/wdN/site', e.g. example/wd1/External")
        return self


class WatchlistFile(_Base):
    companies: list[WatchlistCompany] = Field(default_factory=list)


# ------------------------------------------------------------------- sources


class SourceConfig(_Base):
    kind: str
    enabled: bool = True
    fragile: bool = False
    min_interval_seconds: float = Field(default=2.0, ge=0)
    cache_ttl_minutes: int = Field(default=180, ge=0)
    options: dict[str, Any] = Field(default_factory=dict)


class SourcesFile(_Base):
    sources: dict[str, SourceConfig]
    #: A job closes after this many clean runs of all its sources without it.
    close_after_missed_runs: int = Field(default=3, ge=1)


# ------------------------------------------------------------------- answers

# Only permission to work depends on the job's jurisdiction. Citizenship,
# nationality, birth country and US-person/export-control identity are facts,
# not interchangeable evidence of permission to work.
_WORK_PERMISSION = re.compile(
    r"\b(sponsor\w*|visa\w*|(?:work|employment) authori[sz]ation|work permit|"
    r"(?:authori[sz]ed|authori[sz]ation|eligible|entitled|permitted|permission|right|legally able)"
    r"(?: \w+)* (?:work|employment))\b"
)
# If an unfamiliar place is explicitly named, absence from our country table
# is not evidence that a relative answer belongs to the job's country.
# Nested "in which you are applying" is relative boilerplate, not a named place.
_UNRESOLVED_PERMISSION_PLACE = re.compile(
    r"\b(?:in|within) (?!"
    r"(?:the )?future\b|advance\b|order to\b|"
    r"this (?:country|location|position|role|job)\b|"
    r"(?:the )?country (?:of employment|of application|where|in which)\b|"
    r"the (?:job|work) location\b|"
    r"which (?:you are applying|(?:this|the) (?:position|role|job))\b"
    r")\w+"
)



class AnswerEntry(_Base):
    question: str
    answer: str
    category: str | None = None
    #: Phrases I write that make this answer apply to differently worded questions:
    #: any question containing one of them (as whole words) gets this answer.
    match: list[str] = Field(default_factory=list)
    #: The same answer as some forms word it in a dropdown that has no plain choice for
    #: `answer` (e.g. "I am authorized to work in the United States for any employer").
    #: Only ever used to pick a choice from a list; tried in order after `answer`.
    choices: list[str] = Field(default_factory=list)
    #: Limit an answer to jobs whose company name contains one of these phrases.
    #: Empty means every company. This prevents company-specific history answers
    #: (for example, "Have you interviewed here?") leaking into other applications.
    companies: list[str] = Field(default_factory=list)
    #: Do not use this answer for jobs whose company name contains one of these phrases.
    exclude_companies: list[str] = Field(default_factory=list)
    #: Limit an answer to jobs in one of these tracks (e.g. a part-time job's city).
    tracks: list[str] = Field(default_factory=list)
    #: Do not use this answer for jobs in these tracks (the general answer stays for the rest).
    exclude_tracks: list[str] = Field(default_factory=list)

    @field_validator("answer", mode="before")
    @classmethod
    def _stringify(cls, value: Any) -> Any:
        """Accept unquoted YAML scalars: `answer: Yes` parses as a bool, not text."""
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, (int, float)):
            return str(value)
        return value


class AnswersFile(_Base):
    answers: list[AnswerEntry] = Field(default_factory=list)


# -------------------------------------------------------------------- skills


class SkillArea(_Base):
    name: str
    keywords: list[str] = Field(default_factory=list)
    per_run_quota: int = Field(default=3, ge=0)


class SkillsFile(_Base):
    areas: list[SkillArea] = Field(default_factory=list)


class Conference(_Base):
    name: str
    url: str | None = None
    areas: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)


class ConferencesFile(_Base):
    conferences: list[Conference] = Field(default_factory=list)


# -------------------------------------------------------------- resume terms


class ResumeTerm(_Base):
    """One thing postings ask for, e.g. "SPI / I2C", and the strings that mean it.

    `aliases` match whole words in any case. `cased` aliases must match the case
    exactly too; they exist for short tokens like "C", "ARM" or "CAN" that would
    otherwise match ordinary English words.
    """

    term: str
    aliases: list[str] = Field(default_factory=list)
    cased: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_an_alias(self) -> "ResumeTerm":
        if not self.aliases and not self.cased:
            raise ValueError(f"term {self.term!r} needs at least one alias (aliases or cased)")
        if any(not alias.strip() for alias in self.aliases + self.cased):
            raise ValueError(f"term {self.term!r} has a blank alias")
        return self


class ResumeTermGroup(_Base):
    name: str
    terms: list[ResumeTerm] = Field(default_factory=list)


class ResumeTermsFile(_Base):
    """config/resume_terms.yaml - the vocabulary the resume coach counts."""

    groups: list[ResumeTermGroup] = Field(default_factory=list)


# --------------------------------------------------------------------- views


class ViewFilters(_Base):
    """Filters a saved view applies. Anything left out is not filtered on."""

    track: list[str] | None = None
    tier: list[str] | None = None
    status: list[str] | None = None
    queue_state: list[str] | None = None
    source: list[str] | None = None
    eligibility: list[str] | None = None
    school_selective: bool | None = None
    posted_within_days: int | None = None
    has_deadline: bool | None = None
    next_action_due: bool | None = None
    not_applied: bool | None = None
    include_closed: bool | None = None
    text: str | None = None


class View(_Base):
    key: int = Field(ge=0, le=9)
    title: str
    filters: ViewFilters = Field(default_factory=ViewFilters)


class ViewsFile(_Base):
    views: list[View] = Field(default_factory=list)


#: Used when config/views.yaml is absent, so list screens always have views.
DEFAULT_VIEWS = ViewsFile(
    views=[
        View(key=1, title="New - last 14 days",
             filters=ViewFilters(posted_within_days=14, queue_state=["new"])),
        View(key=2, title="A-tier not applied", filters=ViewFilters(tier=["A"], not_applied=True)),
        View(key=3, title="Follow-ups due", filters=ViewFilters(next_action_due=True)),
        View(key=4, title="All jobs", filters=ViewFilters(include_closed=True)),
        View(key=5, title="Apply queue - filled, submit then s",
             filters=ViewFilters(queue_state=["queued"], not_applied=True)),
    ]
)


# -------------------------------------------------------------------- triage


class Prices(_Base):
    """USD per million tokens for the triage model (claude.com/pricing). Used for the
    estimate shown before a run and for the hard cost cap, so keep them current."""

    input: float = Field(default=2.0, ge=0)
    output: float = Field(default=10.0, ge=0)
    cache_write_1h: float = Field(default=4.0, ge=0)
    cache_read: float = Field(default=0.20, ge=0)
    batch_discount: float = Field(default=0.5, ge=0, le=1)   # Batch API price multiplier


class TriageFile(_Base):
    """config/triage.yaml - the Stage 3 LLM triage settings."""

    provider: Literal["anthropic", "openrouter"] = "anthropic"
    model: str = "claude-sonnet-5-5"
    effort: Literal["low", "medium", "high"] = "low"
    #: A run stops (or a batch is trimmed) before its cost could pass this.
    max_cost_usd_per_run: float = Field(default=5.0, ge=0, allow_inf_nan=False)
    #: Job descriptions are cut to this many characters before they are sent.
    max_description_chars: int = Field(default=8000, ge=500)
    #: Hard limit per answer; the estimate assumes `est_output_tokens`.
    max_output_tokens: int = Field(default=2000, ge=256)
    est_output_tokens: int = Field(default=600, ge=1)
    prices: Prices = Field(default_factory=Prices)
    #: OpenRouter routing ceilings, USD per million tokens (not catalog estimates).
    openrouter_max_input_price: float = Field(default=2.0, ge=0, allow_inf_nan=False)
    openrouter_max_output_price: float = Field(default=10.0, ge=0, allow_inf_nan=False)
    #: Optional UTC-day ceiling shared by all OpenRouter runs in this workspace.
    openrouter_daily_budget_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


# --------------------------------------------------------------------- apply


def _authentication_origin(url: str, *, origin_only: bool = False) -> str | None:
    """Use a deliberately narrow URL grammar, avoiding browser/parser disagreement."""
    if not isinstance(url, str) or not url or any(
        char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\"
        for char in url
    ):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or "@" in parsed.netloc:
            return None
        if origin_only and (parsed.path not in ("", "/") or "?" in url or "#" in url):
            return None
        host = parsed.hostname
        if not host or not host.isascii() or "%" in host:
            return None
        if ":" in host:
            host = f"[{IPv6Address(host).compressed}]"
            if not parsed.netloc.startswith("["):
                return None
        else:
            if len(host) > 253 or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.split(".")
            ):
                return None
            # Browsers interpret abbreviated, octal and hexadecimal IPv4 hosts.
            # Require canonical dotted decimal rather than blessing a spelling
            # that another URL parser can send to a different host.
            last_label = host.rsplit(".", 1)[-1]
            if last_label.isdigit() or re.fullmatch(r"0x[0-9a-f]+", last_label):
                if str(IPv4Address(host)) != host:
                    return None
        port = parsed.port
        if parsed.netloc.endswith(":") or (port is not None and not 1 <= port <= 65535):
            return None
        return f"https://{host}" + (f":{port}" if port not in (None, 443) else "")
    except ValueError:
        return None


class AuthenticationSettings(_Base):
    """Explicit local approval only; neither discovered jobs nor ATS names opt in."""

    enabled: StrictBool = False
    allow_account_creation: StrictBool = False
    approved_origins: list[str] = Field(default_factory=list)
    #: Auto selects the site's recognized form; explicit policies may restrict the flow.
    account_mode: Literal["auto", "recognized", "login", "create", "google"] = "auto"
    _credential_binding: str | None = PrivateAttr(default=None)
    # Google session reuse is authorized only by a reviewed website run.
    _google_email: str | None = PrivateAttr(default=None)

    @field_validator("approved_origins")
    @classmethod
    def _exact_origins(cls, value: list[str]) -> list[str]:
        origins: list[str] = []
        for item in value:
            origin = _authentication_origin(item, origin_only=True)
            if origin is None:
                raise ValueError("approved_origins must contain exact HTTPS origins only")
            if origin not in origins:
                origins.append(origin)
        return origins

    @model_validator(mode="after")
    def _explicit_opt_in(self) -> AuthenticationSettings:
        if self.enabled and not self.approved_origins:
            raise ValueError("enabled authentication requires approved_origins")
        if self.allow_account_creation and not self.enabled:
            raise ValueError("account creation requires enabled authentication")
        return self

    def approves(self, url: str) -> bool:
        """Approve only an enabled, exact HTTPS origin, including nondefault port."""
        origin = _authentication_origin(url)
        return self.enabled and origin is not None and origin in self.approved_origins


class ApplyFile(_Base):
    """config/apply.yaml - the Stage 4 daily-flow settings (reminders and the digest)."""

    #: Marking a job applied creates a follow-up reminder this many days later, for each entry.
    followup_days: list[int] = Field(default_factory=lambda: [7, 14, 21])
    #: An application quiet this many days shows in the digest as "suggest archiving".
    archive_after_days: int = Field(default=21, ge=1)
    #: The digest lists deadlines of jobs I have not applied to within this many days.
    deadline_window_days: int = Field(default=7, ge=0)
    #: After filling, `kitty apply` and the TUI watch the tabs for a submission this
    #: many hours (0 = do not watch).
    watch_hours: float = Field(default=4.0, ge=0)
    #: `kitty autofill` (and F on the Apply tab) fills at most this many favorites per run.
    autofill_cap: int = Field(default=10, ge=1)
    #: Seconds autofill waits between one job's form and the next: polite over fast.
    autofill_pause_seconds: float = Field(default=15.0, ge=2)
    #: Login and account creation remain off unless explicitly approved locally.
    authentication: AuthenticationSettings = Field(default_factory=AuthenticationSettings)

    @field_validator("followup_days")
    @classmethod
    def _positive_days(cls, value: list[int]) -> list[int]:
        """A follow-up on (or before) the day I applied makes no sense."""
        if any(day < 1 for day in value):
            raise ValueError("every followup_days entry must be 1 or more")
        return sorted(set(value))


# ------------------------------------------------------------ the whole set


#: Every config file: (filename, model, required, has_example).
#: `kitty init` copies the examples, `kitty doctor` validates them all.
CONFIG_FILES: tuple[tuple[str, type[BaseModel], bool, bool], ...] = (
    ("profile.yaml", Profile, True, True),
    ("tracks.yaml", TracksFile, True, True),
    ("sources.yaml", SourcesFile, True, True),
    ("watchlist.yaml", WatchlistFile, False, True),
    ("answers.yaml", AnswersFile, False, True),
    ("skills.yaml", SkillsFile, False, True),
    ("conferences.yaml", ConferencesFile, False, True),
    ("views.yaml", ViewsFile, False, True),
    ("resume_terms.yaml", ResumeTermsFile, False, True),
    ("triage.yaml", TriageFile, False, True),
    ("apply.yaml", ApplyFile, False, True),
)


@dataclass(frozen=True)
class Config:
    """Every config file, parsed. Optional files fall back to empty defaults."""

    profile: Profile
    tracks: TracksFile
    sources: SourcesFile
    watchlist: WatchlistFile
    answers: AnswersFile
    skills: SkillsFile
    conferences: ConferencesFile
    views: ViewsFile
    resume_terms: ResumeTermsFile
    triage: TriageFile
    apply: ApplyFile

    def track(self, name: str) -> Track | None:
        """The track with this name, or None."""
        return next((t for t in self.tracks.tracks if t.name == name), None)

    @property
    def track_names(self) -> list[str]:
        return [t.name for t in self.tracks.tracks]

    @property
    def prefer_cities(self) -> list[str]:
        """Every track's preferred cities, once each, in file order (for best-first sorting)."""
        seen: dict[str, None] = {}
        for track in self.tracks.tracks:
            seen.update(dict.fromkeys(track.location_rules.prefer_cities))
        return list(seen)

    def answer_for(
        self, question: str, company: str | None = None, tracks: Sequence[str] = (),
        *, jurisdiction: str | None = None, options: Sequence[str] = (),
    ) -> AnswerEntry | None:
        """The answers.yaml answer for a screening question, or None.

        First an exact match on the normalized question; then an entry whose
        `match` phrases (written by me) appear in it as whole words. Company- or
        track-scoped entries override general entries; `exclude_tracks` keeps a
        general answer away from jobs in those tracks (`tracks` = the job's). When
        two entries at the same specificity match, nothing is returned: a tie is
        not an answer. "U.S.", "USA" and "United States" count as the same words.
        Country-specific work-permission answers require matching explicit field
        country evidence (question/options), or `jurisdiction` from the job's
        strict location evidence. This gates exact and phrase matches alike,
        including countries named only in saved choice aliases. Unresolved
        permission scopes still count toward ambiguity; only proven mismatches
        are discarded. Identity facts are not work permission. Nothing is ever
        generated.
        """
        from kitty.normalize import norm_screening
        from kitty.core.discovery.text import authorization_countries

        company_words = f" {norm_screening(company or '')} "
        job_tracks = set(tracks)
        field_countries = authorization_countries(" ".join((question, *options)))

        def permission_applies(entry: AnswerEntry) -> bool | None:
            """True: applicable; False: proven mismatch; None: unresolved."""
            if not _WORK_PERMISSION.search(norm_screening(entry.question)):
                return True
            countries = authorization_countries(" ".join((entry.question, *entry.choices)))
            if not countries:
                if _UNRESOLVED_PERMISSION_PLACE.search(norm_screening(
                    " ".join((entry.question, *entry.choices)),
                )):
                    return None
                return True
            if len(countries) != 1 or len(field_countries) > 1:
                return None
            # Explicit wording asks about that country even on an overseas or
            # unknown job. Mixed field evidence must not fall back to the job.
            if field_countries:
                return field_countries == countries
            if _UNRESOLVED_PERMISSION_PLACE.search(norm_screening(" ".join((question, *options)))):
                return None
            return countries == {jurisdiction} if jurisdiction is not None else None

        def applies(entry: AnswerEntry) -> bool:
            if not company and (entry.companies or entry.exclude_companies):
                return False
            if entry.tracks and not job_tracks & set(entry.tracks):
                return False
            if job_tracks & set(entry.exclude_tracks):
                return False
            included = [
                phrase for phrase in entry.companies
                if f" {norm_screening(phrase)} " in company_words
            ]
            excluded = any(
                f" {norm_screening(phrase)} " in company_words
                for phrase in entry.exclude_companies
            )
            return not excluded and (not entry.companies or bool(included))

        def choose(entries: list[tuple[AnswerEntry, bool | None]]) -> AnswerEntry | None:
            scoped = [item for item in entries if item[0].companies or item[0].tracks]
            candidates = scoped or entries
            if len(candidates) != 1:
                return None
            entry, permission = candidates[0]
            return entry if permission is True else None

        wanted = norm_screening(question)
        available = [
            (entry, permission)
            for entry in self.answers.answers
            if applies(entry) and (permission := permission_applies(entry)) is not False
        ]
        exact = [
            (entry, permission) for entry, permission in available
            if norm_screening(entry.question) == wanted
        ]
        if exact or not wanted:
            return choose(exact)
        padded = f" {wanted} "
        phrased = [
            (entry, permission) for entry, permission in available
            if any(
                f" {norm_screening(phrase)} " in padded
                for phrase in entry.match
                if norm_screening(phrase)
            )
        ]
        return choose(phrased)


def load_config(config_dir: Path) -> Config:
    """Load every config file from `config_dir`.

    Problems from all files are collected into a single ConfigError so one run of
    `kitty doctor` shows everything that needs fixing.
    """
    parsed: dict[str, Any] = {}
    problems: list[ConfigProblem] = []
    empties: dict[str, Any] = {
        "watchlist.yaml": WatchlistFile(),
        "answers.yaml": AnswersFile(),
        "skills.yaml": SkillsFile(),
        "conferences.yaml": ConferencesFile(),
        "views.yaml": DEFAULT_VIEWS,
        "resume_terms.yaml": ResumeTermsFile(),
        "triage.yaml": TriageFile(),
        "apply.yaml": ApplyFile(),
    }

    for filename, model, required, _ in CONFIG_FILES:
        path = config_dir / filename
        key = filename.removesuffix(".yaml")
        if not path.exists() and not required:
            parsed[key] = empties[filename]
            continue
        try:
            parsed[key] = parse_file(path, model)
        except ConfigError as exc:
            problems.extend(exc.problems)

    if problems:
        raise ConfigError(problems)
    return Config(**parsed)
