"""Local, evidence-backed resume proposals; never unrestricted prose generation.

Only audited phrase equivalents and permutations of existing atoms are allowed.
Offsets retain original HTML byte-for-byte outside patches. No network/model calls.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import difflib
import hashlib
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
import sqlite3
from urllib.parse import unquote, urlsplit

from kitty.config import Config
from kitty.core import jobs as jobs_core
from kitty.core.resume_coach import compile_vocabulary, normalize_text, _mask
from kitty.dates import now_iso
from kitty.settings import Settings

# These shorten wording without changing actors, tense, certainty or achievement.
_EQUIVALENTS = (
    ("in order to", "to"), ("utilized", "used"), ("utilizing", "using"),
    ("approximately", "about"), ("roughly", "about"),
)
_VOID = {"meta", "link", "br", "hr", "wbr"}
_ALLOWED = _VOID | {
    "html", "head", "body", "title", "style", "div", "section", "article",
    "header", "footer", "main", "h1", "h2", "h3", "h4", "h5", "h6", "p",
    "span", "a", "strong", "b", "em", "i", "u", "s", "small", "sub", "sup",
    "ul", "ol", "li", "table", "thead", "tbody", "tfoot", "tr", "td", "th",
    "dl", "dt", "dd", "abbr", "address",
}
_EDIT_SECTIONS = {"projects", "experience", "work experience", "professional experience", "research", "research experience"}
_SKILL_SECTIONS = {"skills", "technical skills", "skills and tools"}
_PROTECTED = re.compile(
    r"\b(?:target\w*|planned|proposed|proposal|submitted|expected|intended|present|"
    r"prototype\w*|prototyp\w*|ongoing|pending|estimated|not|never|no|may|might|could)\b",
    re.IGNORECASE,
)
_NUMBERS = re.compile(r"(?<!\w)[+-]?(?:\d[\d,.]*|\.\d+)(?:%|\b)")
_CONTEXTUAL = re.compile(r"\b(?:then|previously|above|below|former|latter|subsequently|finally)\b|^(?:this|these|that|those|it|they)\b", re.I)
_SKILL_QUALIFIER = re.compile(
    r"\b(?:learning|studying|familiar(?:ity)?|basic|beginner|novice|intermediate|"
    r"advanced|proficien(?:t|cy)|expert(?:ise)?|exposure|introductory|coursework|"
    r"experience|knowledge|understanding|competenc[ey]|comfortable|limited|"
    r"fundamental|working|some|current(?:ly)?|interest(?:ed)?|aspiring)\b", re.I,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _plain(value: str) -> str:
    return " ".join(normalize_text(unescape(value)).split())


def _check_css(value: str) -> None:
    # Escape sequences can obscure url()/@import. Existing plain typography needs none.
    if re.search(r"url\s*\(|@import|expression\s*\(|javascript:|behavior\s*:|-moz-binding|[\\<>]", value, re.I):
        raise ValueError("Unsafe HTML: active or external CSS is not supported")


@dataclass
class _Node:
    tag: str
    start: int
    inner: int
    parent: _Node | None
    attrs: dict[str, str | None]
    children: list[_Node] = field(default_factory=list)
    end: int = 0
    close: int = 0
    text: str = ""
    section: str = ""


class _Document(HTMLParser):
    """Strict HTML subset with source offsets, not a repairing/serializing parser."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.lines = [0]
        self.lines.extend(m.end() for m in re.finditer("\n", source))
        self.nodes: list[_Node] = []
        self.stack: list[_Node] = []
        self.fragments: list[tuple[int, int, str, _Node]] = []
        self.section = ""
        self.feed(source)
        self.close()
        if self.stack or sum(n.tag == "html" for n in self.nodes) != 1 or sum(n.tag == "body" for n in self.nodes) != 1:
            raise ValueError("Malformed HTML: require one complete html/body document")
        if not any(n.text.strip() for n in self.nodes if n.tag == "body"):
            raise ValueError("Resume HTML has no readable body text")

    def _offset(self) -> int:
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED:
            raise ValueError(f"Unsafe HTML: unsupported <{tag}> element")
        values = dict(attrs)
        if len(values) != len(attrs):
            raise ValueError("Malformed HTML: duplicate attributes")
        for name, value in attrs:
            if name.startswith("on") or name in {"src", "srcset", "srcdoc", "action", "formaction", "background", "ping", "contenteditable", "is", "xmlns"}:
                raise ValueError(f"Unsafe HTML attribute: {name}")
            if name == "style":
                _check_css(value or "")
            if name == "href":
                target = unquote(value or "")
                if any(ord(char) < 32 for char in target) or "\\" in target:
                    raise ValueError("Unsafe HTML link")
                parsed = urlsplit(target)
                if tag == "link":
                    if (values.get("rel") or "").lower() != "stylesheet" or parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or target.startswith("/") or ".." in Path(target).parts or Path(target).suffix.lower() != ".css":
                        raise ValueError("Unsafe HTML: stylesheets must be relative local CSS files")
                elif tag != "a" or parsed.scheme.lower() not in {"", "https", "http", "mailto", "tel"} or (not parsed.scheme and not target.startswith("#")):
                    raise ValueError("Unsafe HTML link scheme")
        if tag == "meta" and "http-equiv" in values:
            raise ValueError("Unsafe HTML: meta directives are not supported")
        parent = self.stack[-1] if self.stack else None
        if (tag == "html" and parent) or (tag != "html" and parent is None) or (tag in {"head", "body"} and parent.tag != "html"):
            raise ValueError("Malformed HTML document structure")
        if tag == "li" and (not parent or parent.tag not in {"ul", "ol"}):
            raise ValueError("Malformed HTML list")
        if tag in {"head", "body"} and any(n.tag == tag for n in self.nodes):
            raise ValueError("Malformed HTML: duplicate document container")
        if tag == "a" and any(n.tag == "a" for n in self.stack):
            raise ValueError("Malformed HTML: nested links")
        block = tag in {"div", "section", "article", "header", "footer", "main", "h1", "h2", "h3", "h4", "p", "ul", "ol", "li", "table", "tr", "td", "th", "br"}
        if block and tag != "br" and any(n.tag == "p" for n in self.stack):
            raise ValueError("Malformed HTML: block element inside paragraph")
        if block:
            for ancestor in self.stack:
                ancestor.text += "\n"
        start = self._offset()
        node = _Node(tag, start, start + len(self.get_starttag_text()), parent, values, section=self.section)
        self.nodes.append(node)
        if parent:
            parent.children.append(node)
        if tag in _VOID:
            node.close = node.end = node.inner
        else:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        if tag not in _VOID:
            raise ValueError("Malformed HTML: non-void self-closing element")
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1].tag != tag:
            raise ValueError(f"Malformed HTML: unmatched </{tag}>")
        node = self.stack.pop()
        node.close = self._offset()
        end = self.source.find(">", node.close)
        if end == -1:
            raise ValueError("Malformed HTML closing tag")
        node.end = end + 1
        if tag == "style":
            _check_css(node.text)
        if tag == "h2":
            self.section = _plain(node.text).casefold()

    def _text(self, raw: str, value: str):
        if not self.stack:
            if value.strip():
                raise ValueError("Malformed HTML: text outside document")
            return
        node = self.stack[-1]
        start = self._offset()
        self.fragments.append((start, start + len(raw), value, node))
        for ancestor in self.stack:
            ancestor.text += value

    def handle_data(self, data):
        if "<" in data and self.stack and self.stack[-1].tag != "style":
            raise ValueError("Malformed HTML: unescaped markup")
        self._text(data, data)

    def handle_entityref(self, name):
        raw = f"&{name};"
        if not self.source.startswith(raw, self._offset()) or unescape(raw) == raw:
            raise ValueError("Malformed HTML character reference")
        self._text(raw, unescape(raw))

    def handle_charref(self, name):
        raw = f"&#{name};"
        if not self.source.startswith(raw, self._offset()):
            raise ValueError("Malformed HTML character reference")
        self._text(raw, unescape(raw))

    def handle_decl(self, decl):
        if decl.casefold() != "doctype html":
            raise ValueError("Unsafe HTML declaration")

    def handle_pi(self, data):
        raise ValueError("Unsafe HTML processing instruction")

    def unknown_decl(self, data):
        raise ValueError("Unsafe HTML declaration")


def _canonical(text: str) -> str:
    text = _plain(text)
    for before, after in _EQUIVALENTS:
        text = re.sub(r"\b" + re.escape(before) + r"\b", after, text, flags=re.I)
    return text.casefold()


def _same_fact(before: str, after: str) -> bool:
    return (
        _canonical(before) == _canonical(after)
        and _NUMBERS.findall(before) == _NUMBERS.findall(after)
        and [s.casefold() for s in _PROTECTED.findall(before)] == [s.casefold() for s in _PROTECTED.findall(after)]
    )


def _requirement(quote: str) -> str:
    optional = bool(re.search(r"\b(?:not required|optional|preferred|a plus|nice to have|desirable)\b", quote, re.I))
    affirmative = re.sub(r"\bnot required\b", "", quote, flags=re.I)
    required = bool(re.search(r"\b(?:required|must|requirements|minimum qualifications)\b", affirmative, re.I))
    # A clause mixing requirements and preferences cannot safely attribute either
    # marker to every skill it mentions. Keep the exact evidence; abstain.
    if optional and required:
        return "unknown"
    if optional:
        return "preferred"
    if required and not re.search(r"\b(?:not|no|never|unless|except|may|might|could|depending)\b", quote, re.I):
        return "required"
    return "unknown"


def _evidence(conn, track, matchers, min_postings, timestamp):
    all_jobs = jobs_core.list_jobs(conn, jobs_core.JobFilter(tracks=[track], include_closed=True))
    eligible = [j for j in all_jobs if j.is_active and j.verification_status != "closed" and j.eligibility != "ineligible" and j.queue_state != "not_eligible" and (j.jd_text or "").strip()]
    # The database already merges canonical URLs. Also collapse syndicated identical
    # employer+title+description copies, without merging different employers.
    seen = set()
    jobs = []
    for job in sorted(eligible, key=lambda j: j.id):
        key = (job.company_id, _plain(job.title).casefold(), _hash(_plain(job.jd_text or "").casefold()))
        if key not in seen:
            seen.add(key)
            jobs.append(job)
    if len(jobs) < min_postings:
        raise ValueError(f"Track {track} needs at least {min_postings} distinct active eligible postings with descriptions; found {len(jobs)}")
    employers = {j.company_id for j in jobs}
    records = []
    evidence = defaultdict(list)
    sources = Counter()
    freshness = Counter()
    for job in jobs:
        text = normalize_text(job.jd_text or "")
        masked = _mask(text, job.company_name)
        for matcher in matchers:
            match = matcher.find(masked)
            if match:
                # Keep the full local clause, not an LLM interpretation. Newlines and
                # sentence punctuation delimit requirement/preference attribution.
                start = max(text.rfind("\n", 0, match.start()), text.rfind(". ", 0, match.start()), text.rfind(";", 0, match.start())) + 1
                ends = [p for token in ("\n", ". ", ";") if (p := text.find(token, match.end())) >= 0]
                end = min(ends, default=len(text))
                quote = text[start:end].strip()
                evidence[matcher.term].append({"job_id": job.id, "company": job.company_name, "company_id": job.company_id, "quote": quote, "term": matcher.term, "requirement": _requirement(quote)})
        provenance = [{"source": s.source, "source_job_id": s.source_job_id, "source_url": s.source_url, "last_seen_at": s.last_seen_at} for s in job.sources]
        sources.update({s.source for s in job.sources})
        freshness[job.freshness] += 1
        records.append({"job_id": job.id, "company_id": job.company_id, "company": job.company_name, "title": job.title, "url": job.url, "jd_hash": _hash(job.jd_text or ""), "jd_text": job.jd_text, "freshness": job.freshness, "verification_status": job.verification_status, "verified_at": job.verified_at, "eligibility": job.eligibility, "sources": provenance})
    cohort = {"track": track, "timestamp": timestamp, "total_track_postings": len(all_jobs), "eligible_postings_before_dedup": len(eligible), "postings": len(jobs), "distinct_employers": len(employers), "excluded_postings": len(all_jobs) - len(eligible), "duplicates_removed": len(eligible) - len(jobs), "min_postings": min_postings, "dedup_policy": "database canonical identity, then same employer + normalized title + normalized description; ranking counts each employer once per term", "source_coverage": dict(sources), "freshness": dict(freshness), "jobs": records, "evidence": [e for rows in evidence.values() for e in rows]}
    return cohort, evidence


def propose(conn: sqlite3.Connection, config: Config, settings: Settings, track: str, source: Path, *, min_postings: int = 5) -> dict:
    """Freeze track evidence and propose bounded edits, never changing source or DB."""
    if config.track(track) is None:
        raise ValueError(f"Unknown track {track!r}; choose one of {', '.join(config.track_names)}")
    if isinstance(min_postings, bool) or not isinstance(min_postings, int) or min_postings < 1:
        raise ValueError("min_postings must be a positive integer")
    source = Path(source).expanduser()
    if not source.is_absolute():
        source = settings.root / source
    source = source.resolve()
    if source.suffix.lower() not in {".html", ".htm"}:
        raise ValueError("Resume editing requires an original .html source; PDFs cannot be safely rewritten")
    raw = source.read_bytes()
    original = raw.decode("utf-8")
    document = _Document(original)
    matchers = compile_vocabulary(config.resume_terms)
    if not matchers:
        raise ValueError("Configure resume_terms.yaml before proposing evidence-backed edits")
    timestamp = now_iso()
    cohort, evidence = _evidence(conn, track, matchers, min_postings, timestamp)
    weights = {term: len({e["company_id"] for e in rows}) for term, rows in evidence.items()}

    def terms(text):
        normalized = normalize_text(text)
        return [m.term for m in matchers if m.find(normalized)]

    def score(text):
        return sum(weights.get(term, 0) for term in terms(text))

    facts = []
    node_facts = {}
    # Anchor complete leaf blocks, including immutable contact/education/status.
    blocks = {"li", "p", "div", "h1", "h2", "h3", "h4", "td", "th", "dt", "dd", "address"}
    for node in document.nodes:
        if node.tag not in blocks or not node.text.strip():
            continue
        if any(other.tag in blocks and node.start < other.start < node.end for other in document.nodes):
            continue
        text = _plain(node.text)
        fact = {"id": _hash(f"{node.start}:{node.end}:{text}")[:24], "section": node.section, "text": text, "text_hash": _hash(text), "start": node.start, "end": node.end}
        facts.append(fact)
        node_facts[node.start] = fact

    patches = []
    changes = []

    def record(kind, start, end, after, anchored, reason):
        before = original[start:end]
        if before == after:
            return
        supported = set(terms("\n".join(f["text"] for f in anchored)))
        proof = [e for term in sorted(supported) for e in evidence.get(term, [])]
        if not proof:
            return
        patches.append({"id": _hash(f"{kind}:{start}:{before}:{after}")[:24], "kind": kind, "before": before, "after": after, "fact_ids": [f["id"] for f in anchored], "reason": reason, "evidence": proof})
        changes.append((start, end, after))

    # Whole bullets remain under their original ul (and project/employer).
    for node in document.nodes:
        if node.tag != "ul" or node.section not in _EDIT_SECTIONS:
            continue
        bullets = node.children
        if not bullets or any(b.tag != "li" or b.start not in node_facts or any(c.tag in {"ul", "ol"} for c in b.children) for b in bullets):
            continue
        # Only whitespace between complete bullet nodes may be rearranged.
        boundaries = [node.inner] + [b.end for b in bullets]
        starts = [b.start for b in bullets] + [node.close]
        if any(original[a:b].strip() for a, b in zip(boundaries, starts)):
            continue
        rendered = {}
        for bullet in bullets:
            content = original[bullet.start:bullet.end]
            replacements = []
            if score(bullet.text):
                for start, end, text, owner in document.fragments:
                    if not (bullet.inner <= start < bullet.close) or owner.tag in {"a", "abbr"}:
                        continue
                    ancestor = owner.parent
                    linked = False
                    while ancestor is not None and ancestor is not bullet:
                        linked = linked or ancestor.tag in {"a", "abbr"}
                        ancestor = ancestor.parent
                    if linked:
                        continue
                    # Replacing an encoded fragment could change markup; plain data only.
                    if original[start:end] != text:
                        continue
                    changed = text
                    for before, after in _EQUIVALENTS:
                        changed = re.sub(r"\b" + re.escape(before) + r"\b", lambda m: after.capitalize() if m.group()[0].isupper() else after, changed, flags=re.I)
                    if changed != text:
                        if not _same_fact(text, changed):
                            raise ValueError("Unsafe proposal: wording changed a factual invariant")
                        replacements.append((start - bullet.start, end - bullet.start, changed))
            for start, end, changed in reversed(replacements):
                content = content[:start] + changed + content[end:]
            rendered[bullet.start] = content
        ordered = bullets if any(_CONTEXTUAL.search(_plain(b.text)) for b in bullets) else sorted(bullets, key=lambda b: -score(b.text))
        if ordered != bullets:
            # Preserve inter-item whitespace in its original position.
            after = original[node.inner:bullets[0].start]
            for index, bullet in enumerate(ordered):
                after += rendered[bullet.start]
                after += original[bullets[index].end:(bullets[index + 1].start if index + 1 < len(bullets) else node.close)]
            record("bullet_order_and_clarity", node.inner, node.close, after, [node_facts[b.start] for b in bullets], "Prioritize existing evidence within the same project/employer list using distinct-employer demand; only audited phrase equivalents may change wording.")
        else:
            for bullet in bullets:
                record("clarity", bullet.start, bullet.end, rendered[bullet.start], [node_facts[bullet.start]], "Shorten an audited equivalent phrase without changing numbers, status, certainty or factual claims; JD evidence supports this existing bullet.")

    # Reorder only comma-separated skill atoms in one label's plain-text tail.
    for node in document.nodes:
        if node.section not in _SKILL_SECTIONS or node.start not in node_facts or node.tag not in {"div", "p", "li"}:
            continue
        tails = [(a, b, t) for a, b, t, owner in document.fragments if owner is node and t.strip()]
        if len(tails) != 1:
            continue
        start, end, text = tails[0]
        if original[start:end] != text or "," not in text:
            continue
        prefix = ""
        body = text
        if ":" in body:
            prefix, body = body.split(":", 1)
            prefix += ":"
        atoms = [s.strip() for s in body.split(",")]
        if len(atoms) < 2 or _SKILL_QUALIFIER.search(body) or any(not atom or len(atom.split()) > 7 or re.search(r"[;:!?\n]", atom) for atom in atoms):
            continue
        if any(_PROTECTED.search(atom) or _CONTEXTUAL.search(atom) or re.search(r"\b(?:built|used|measured|developed|only|including|and|or)\b|[.]$", atom, re.I) for atom in atoms):
            continue
        ranked = sorted(atoms, key=lambda atom: -score("Skills: " + atom + ","))
        if ranked == atoms:
            continue
        if Counter(atoms) != Counter(ranked):
            raise ValueError("Unsafe proposal: skills changed")
        leading = body[:len(body) - len(body.lstrip())]
        trailing = body[len(body.rstrip()):]
        after = prefix + leading + ", ".join(ranked) + trailing
        record("skill_order", start, end, after, [node_facts[node.start]], "Order the same skill atoms within their original category by distinct-employer demand; no skill is added or removed.")

    candidate = original
    last = len(original)
    for start, end, after in sorted(changes, reverse=True):
        if end > last:
            raise ValueError("Unsafe proposal: overlapping source anchors")
        candidate = candidate[:start] + after + candidate[end:]
        last = start
    revised = _Document(candidate)
    # Compare each original list separately: a claim may never change owners.
    def bullet_facts(parsed):
        return [
            Counter(_canonical(child.text) for child in node.children if child.tag == "li")
            for node in parsed.nodes if node.tag in {"ul", "ol"} and node.section in _EDIT_SECTIONS
        ]

    if bullet_facts(document) != bullet_facts(revised):
        raise ValueError("Unsafe proposal: original bullet facts or ownership changed")
    original_links = Counter(original[n.start:n.end] for n in document.nodes if n.tag == "a")
    revised_links = Counter(candidate[n.start:n.end] for n in revised.nodes if n.tag == "a")
    if original_links != revised_links:
        raise ValueError("Unsafe proposal: original links changed")
    if Counter(_NUMBERS.findall(document.nodes[0].text)) != Counter(_NUMBERS.findall(revised.nodes[0].text)):
        raise ValueError("Unsafe proposal: numeric facts changed")
    # Numeric equality alone does not protect target-to-achieved or pending status.
    if Counter(s.casefold() for s in _PROTECTED.findall(document.nodes[0].text)) != Counter(s.casefold() for s in _PROTECTED.findall(revised.nodes[0].text)):
        raise ValueError("Unsafe proposal: status or certainty changed")
    original_text = next(n.text for n in document.nodes if n.tag == "body")
    candidate_text = next(n.text for n in revised.nodes if n.tag == "body")
    before_terms, after_terms = set(terms(original_text)), set(terms(candidate_text))
    if before_terms != after_terms:
        raise ValueError("Unsafe proposal: supported vocabulary coverage changed")
    stats = [{"term": m.term, "postings": len(evidence[m.term]), "distinct_employers": weights.get(m.term, 0), "posting_share": len(evidence[m.term]) / cohort["postings"], "employer_share": weights.get(m.term, 0) / cohort["distinct_employers"], "supported_before": m.term in before_terms, "supported_after": m.term in after_terms} for m in matchers if evidence[m.term]]
    stats.sort(key=lambda row: (-row["distinct_employers"], -row["postings"], row["term"]))
    review = [{"term": row["term"], "reason": "Demand appears in this cohort but is not anchored in the source resume. Confirm genuine experience yourself; not inserted.", "evidence": evidence[row["term"]]} for row in stats if not row["supported_before"]]
    for row in stats:
        if not row["supported_before"]:
            continue
        anchors = [fact for fact in facts if row["term"] in terms(fact["text"])]
        uncertain_anchors = [
            fact for fact in anchors
            if re.search(r"\b(?:not|no|never|planned|proposed|target|intended|learning|studying|interested|aspiring)\b", fact["text"], re.I)
        ]
        if not anchors or len(uncertain_anchors) == len(anchors):
            review.append({
                "term": row["term"],
                "reason": "Only qualified or uncertain source evidence was found. Original status is retained; confirm experience before claiming proficiency.",
                "fact_ids": [fact["id"] for fact in anchors],
                "evidence": evidence[row["term"]],
            })
    warnings = ["Local deterministic editorial proposal, not an ATS score or hiring prediction. Missing skills are never inserted."]
    uncertain = cohort["postings"] - cohort["freshness"].get("verified", 0)
    if uncertain:
        warnings.append(f"{uncertain} cohort postings have stale or unverified availability; counts are observed vocabulary, not verified current demand.")
    if cohort["distinct_employers"] < min_postings:
        warnings.append(f"Only {cohort['distinct_employers']} distinct employers in {cohort['postings']} postings; employer-weighted evidence limits repeated-company influence.")
    if not patches:
        warnings.append("No safe evidence-backed transformation was found; candidate text is unchanged.")
    demanded = set(weights)
    denominator = sum(weights.values())

    def coverage(covered):
        return {"supported_terms": sorted(demanded & covered), "demanded_terms": len(demanded), "employer_weighted_share": sum(weights[t] for t in demanded & covered) / denominator if denominator else 0.0}

    return {"track": track, "source_path": str(source), "source_hash": hashlib.sha256(raw).hexdigest(), "created_at": timestamp, "original_html": original, "candidate_html": candidate, "patches": patches, "cohort": cohort, "facts": facts, "review_items": review, "metrics": {"terms": stats, "coverage_before": coverage(before_terms), "coverage_after": coverage(after_terms), "measurement": "Vocabulary presence and distinct-employer frequency, not ATS compatibility or hiring outcomes"}, "diff": "".join(difflib.unified_diff(original.splitlines(keepends=True), candidate.splitlines(keepends=True), fromfile="original.html", tofile="candidate.html")), "warnings": warnings}
