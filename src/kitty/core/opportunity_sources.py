"""Evidence-first primary public providers over the existing paced, cached HTTP client.

Calendar/event start dates never become application deadlines. Missing dates remain
unknown, closed cycles stay closed, and blocked fetches never imply closure.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from icalendar import Calendar

from kitty.core import opportunities as domain
from kitty.core.discovery.http import HttpError, PoliteHttp, RateLimited
from kitty.dates import now_iso
from kitty.db.connection import transaction

DEFAULTS = (
    ('tamu-engineering', 'TAMU Engineering calendar', 'https://calendar.tamu.edu/live/ical/events/group/College%20of%20Engineering', 'ical', 'engineering', 'America/Chicago'),
    ('ieee-tamu', 'IEEE TAMU public events', 'https://ieeetamu.org/api/events', 'ieee', 'engineering', 'America/Chicago'),
    ('mlh', 'Major League Hacking official season', 'https://mlh.com/events', 'mlh', 'hackathon', 'UTC'),
    ('devpost', 'Devpost public hackathons', 'https://devpost.com/hackathons', 'devpost', 'hackathon', 'UTC'),
    ('imc-prosperity', 'IMC Prosperity', 'https://prosperity.imc.com/', 'page', 'quant', 'UTC'),
    ('jane-street', 'Jane Street programs and events', 'https://www.janestreet.com/join-jane-street/programs-and-events/', 'jane', 'quant', 'America/New_York'),
    ('rotman', 'Rotman International Trading Competition', 'https://rotman.utoronto.ca/faculty-and-research/education-labs/bmo-financial-group-finance-research-and-trading-lab/rotman-international-trading-competition/overview/', 'page', 'quant', 'America/Toronto'),
    ('citadel', 'Citadel Datathons', 'https://www.citadel.com/careers/programs-and-events/datathons/', 'page', 'data science', 'America/New_York'),
)


class Page(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.scripts, self.links, self.metadata, self.texts = [], [], {}, []
        self.script = None
        self.anchor = None
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script': self.script = [attrs, []]
        if tag == 'a': self.anchor = [attrs.get('href', ''), []]
        if tag == 'meta': self.metadata[attrs.get('property') or attrs.get('name')] = attrs.get('content', '')

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            self.scripts.append((self.script[0], ''.join(self.script[1])))
            self.script = None
        if tag == 'a' and self.anchor is not None:
            self.links.append((self.anchor[0], ' '.join(self.anchor[1])))
            self.anchor = None

    def handle_data(self, data):
        if self.script is not None: self.script[1].append(data)
        elif data.strip():
            self.texts.append(data.strip())
            if self.anchor is not None: self.anchor[1].append(data.strip())


def clean(value):
    if not value: return ''
    return ' '.join(Page(str(value)).texts)


def parsed_date(value):
    if not value: return None
    try:
        return domain.valid_date(str(value))
    except Exception:
        # A provider's naive/local date or ambiguous text is evidence, not an invented timestamp.
        return None


def base(source, title, url, **facts):
    result = {'title': clean(title), 'url': urljoin(source['url'], str(url or '')),
              'category': source['category'], 'timezone': source['timezone'], 'description': '',
              'location': '', 'remote': None, 'start': None, 'end': None, 'deadline': None,
              'availability': 'unknown', **facts}
    result['availability'] = domain.availability(result)
    result['evidence_url'] = source['url']
    return result


def parse_ical(content, source):
    events = []
    for event in Calendar.from_ical(content).walk('VEVENT'):
        def dt(key):
            prop = event.get(key)
            if prop is None: return None
            value = prop.dt
            return parsed_date(value.isoformat())
        url = str(event.get('URL') or source['url'])
        # UID is retained in evidence; per-event URL is canonical identity.
        if url == source['url']: url += '?uid=' + str(event.get('UID', ''))
        item = base(source, str(event.get('SUMMARY', '')), url,
                    provider_id=str(event.get('UID', '')), description=clean(event.get('DESCRIPTION')),
                    location=str(event.get('LOCATION', '')), start=dt('DTSTART'), end=dt('DTEND'),
                    remote=True if str(event.get('X-LIVEWHALE-IS-ONLINE', '')) == '1' else None,
                    timezone=str(event.get('X-LIVEWHALE-TIMEZONE') or source['timezone']))
        if str(event.get('STATUS', '')).upper() == 'CANCELLED': item['availability'] = 'closed'
        if item['title']: events.append(item)
    return events


def parse_ieee(data, source):
    if not isinstance(data, list): raise ValueError('Public events response is not a list')
    return [base(source, e['title'], f"https://ieeetamu.org/events?event={e['uid']}",
                 provider_id=e['uid'], description=clean(e.get('description')), location=e.get('location', ''),
                 start=parsed_date(e.get('start')), end=parsed_date(e.get('end')),
                 category=e.get('type') or source['category']) for e in data if e.get('title') and e.get('uid')]


def parse_mlh(content, source):
    page = Page(content)
    for attrs, script in page.scripts:
        if attrs.get('data-page') == 'app':
            data = json.loads(script)['props']
            if 'upcomingEvents' not in data: raise ValueError('MLH event data is missing')
            events = []
            for e in data['upcomingEvents'] + data.get('pastEvents', []):
                restrictions = e.get('customFields', {}).get('underserved_types', [])
                item = base(source, e['name'], e.get('websiteUrl') or e['url'],
                            provider_id=e['id'], location=e.get('location', ''),
                            start=parsed_date(e.get('startsAt')), end=parsed_date(e.get('endsAt')),
                            remote=e.get('formatType') in ('digital', 'online', 'virtual'),
                            description='Official MLH listing. ' + ('Provider audience labels: ' + ', '.join(restrictions) + '. ' if restrictions else '') + 'Eligibility not evaluated; review organizer rules.')
                if e.get('status') in ('ended', 'cancelled'): item['availability'] = 'closed'
                events.append(item)
            return events, bool(data.get('scrollProps') or json.loads(script).get('scrollProps', {}).get('pastEvents', {}).get('nextPage'))
    raise ValueError('MLH public event payload unavailable; page format changed')


def _json_events(page, source):
    result = []
    def walk(value):
        if isinstance(value, list):
            for entry in value: walk(entry)
        elif isinstance(value, dict):
            types = value.get('@type', [])
            if isinstance(types, str): types = [types]
            if any(t in ('Event', 'Hackathon', 'BusinessEvent', 'EducationEvent') for t in types) and value.get('name'):
                location = value.get('location') or {}
                location = location if isinstance(location, str) else location.get('name', '')
                item = base(source, value['name'], value.get('url') or source['url'],
                            description=clean(value.get('description')), location=location,
                            start=parsed_date(value.get('startDate')), end=parsed_date(value.get('endDate')),
                            deadline=parsed_date(value.get('applicationDeadline')),
                            remote=True if 'Online' in str(value.get('eventAttendanceMode', '')) else None)
                if 'Cancelled' in str(value.get('eventStatus', '')): item['availability'] = 'closed'
                result.append(item)
            for key in ('@graph', 'itemListElement', 'item'): 
                if key in value: walk(value[key])
    for attrs, script in page.scripts:
        if attrs.get('type') == 'application/ld+json': walk(json.loads(script))
    return result


def explicit_deadline(content):
    """Read a uniquely labeled, year-bearing deadline, never a publication date."""
    month = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    pattern = rf'(?:application\s+deadline|registration\s+deadline|apply\s+by|applications?\s+close(?:s)?)\s*(?:is|:|on)?\s*({month}\s+\d{{1,2}}(?:st|nd|rd|th)?[,]?\s+20\d{{2}}|\d{{4}}-\d{{2}}-\d{{2}})'
    dates = set()
    for match in re.finditer(pattern, content, re.I):
        value = re.sub(r'(?<=\d)(st|nd|rd|th)', '', match[1], flags=re.I).replace(',', '')
        for fmt in ('%Y-%m-%d', '%B %d %Y', '%b %d %Y'):
            try:
                dates.add(datetime.strptime(value, fmt).date().isoformat())
                break
            except ValueError:
                continue
    return next(iter(dates)) if len(dates) == 1 else None


def parse_page(content, source):
    page = Page(content)
    events = _json_events(page, source)
    if events: return events, False
    if source['kind'] == 'devpost':
        # Public directory cards each link to the organizer's event; no private APIs.
        events = [base(source, title, href, description='Public Devpost listing. Deadline unknown; review the organizer rules.')
                  for href, title in page.links if re.match(r'https://[^/]+\.devpost\.com/?$', href) and title]
        if not events: raise ValueError('No public Devpost cards or structured events available')
        return events, True
    if source['kind'] == 'jane':
        for href, label in page.links:
            if re.search(r'/programs-and-events/[^/]+/$', href) and 'PROGRAM' in label:
                title = re.sub(r'^PROGRAM\s*', '', label).split('ACCEPTING APPLICATIONS')[0].strip()
                if not title: continue
                item = base(source, title, href, description=label)
                # Directory badges are not sufficient proof of open application dates.
                events.append(item)
        if not events: raise ValueError('Jane Street program cards unavailable')
        return events, True
    description = ' '.join(page.texts)
    title = page.metadata.get('og:title') or source['name']
    item = base(source, title, source['url'], description=description[:20000],
                deadline=explicit_deadline(description))
    # Closure must be cycle-specific and explicit, never inferred from HTTP failure.
    if source['id'] == 'imc-prosperity' and re.search(r'Prosperity\s+\d+\s+(?:has\s+)?finished', description, re.I):
        item['availability'] = 'closed'
    return [item], True


def parse_rss(content, source):
    root = ElementTree.fromstring(content)
    result = []
    for entry in root.findall('.//item') + root.findall('.//{http://www.w3.org/2005/Atom}entry'):
        def field(name):
            node = entry.find(name)
            if node is None: node = entry.find('{http://www.w3.org/2005/Atom}' + name)
            return '' if node is None else (node.text or node.get('href', ''))
        if field('title') and field('link'):
            result.append(base(source, field('title'), field('link'), description=clean(field('description') or field('summary'))))
    return result


def sources(conn):
    for row in DEFAULTS:
        conn.execute('INSERT OR IGNORE INTO opportunity_sources(id,name,url,kind,category,timezone) VALUES(?,?,?,?,?,?)', row)
    return [dict(r) for r in conn.execute('SELECT * FROM opportunity_sources ORDER BY name')]


def save_source(conn, ident, body):
    if set(body) - {'revision', 'enabled', 'url'}: domain.fail('Source accepts revision, enabled and public URL')
    source = next((s for s in sources(conn) if s['id'] == ident), None)
    if source is None: domain.fail('Source not found', 404)
    changes = {}
    if 'enabled' in body:
        if type(body['enabled']) is not bool: domain.fail('enabled must be boolean')
        changes['enabled'] = body['enabled']
    if 'url' in body:
        url = domain.canonical_url(body['url'])
        original = next(d[2] for d in DEFAULTS if d[0] == ident)
        # Public primary-provider origins only; never fetch authenticated member portals,
        # custom localhost URLs, user-supplied API keys, or unknown network hosts.
        host = urlsplit(url).hostname
        if host != urlsplit(original).hostname or urlsplit(url).query:
            domain.fail('Source URLs must stay on this primary public provider and cannot contain query credentials')
        changes['url'] = url
    with transaction(conn):
        current = conn.execute('SELECT * FROM opportunity_sources WHERE id=?', (ident,)).fetchone()
        domain._revision(current, body.get('revision'))
        changes['revision'] = current['revision'] + 1
        conn.execute(f"UPDATE opportunity_sources SET {','.join(k+'=?' for k in changes)} WHERE id=?", (*changes.values(), ident))
    return next(s for s in sources(conn) if s['id'] == ident)


def fetch_source(http, source):
    if source['kind'] == 'ieee': return parse_ieee(http.get_json(source['url']), source), False
    content = http.get_bytes(source['url'])
    if source['kind'] == 'ical': return parse_ical(content, source), False
    content = content.decode('utf-8')
    if source['kind'] == 'mlh': return parse_mlh(content, source)
    if content.lstrip().startswith('<?xml') or '<rss' in content[:500]: return parse_rss(content, source), True
    return parse_page(content, source)


def discover(conn, cache_dir, selected=None, http_factory=None):
    result = []
    blocked_hosts = set()
    for source in sources(conn):
        if not source['enabled'] or (selected and source['id'] not in selected): continue
        host = urlsplit(source['url']).hostname
        http = (http_factory(source) if http_factory else PoliteHttp(cache_dir / 'opportunities' / source['id'], min_interval=2.0, cache_ttl_minutes=360))
        count, status, error = 0, 'ok', None
        try:
            if host in blocked_hosts: raise RateLimited('Host rate limited earlier in this run')
            items, partial = fetch_source(http, source)
            observed = http.retrieved_at or now_iso()
            count = domain.upsert_discovered(conn, source['id'], items, observed)
            if partial:
                status, error = 'partial', 'Public page has incomplete dates/details or a bounded listing. Unknown dates are not open registrations; consult linked primary evidence.'
        except RateLimited:
            status, error = 'blocked', 'Provider rate limited this run; no bypass or automatic retry.'
            blocked_hosts.add(host)
        except HttpError as exc:
            status = 'blocked' if '403' in str(exc) or '401' in str(exc) else 'failed'
            error = 'Public provider request failed: ' + str(exc)
        except Exception as exc:
            status, error = 'failed', f'Public provider could not be parsed ({type(exc).__name__}); previous records and review choices retained.'
        finally:
            http.close()
        with transaction(conn):
            conn.execute('UPDATE opportunity_sources SET last_run=?,last_success=CASE WHEN ? IN (\'ok\',\'partial\') THEN ? ELSE last_success END,last_status=?,last_error=?,last_count=?,revision=revision+1 WHERE id=?',
                         (now_iso(), status, now_iso(), status, error, count, source['id']))
        result.append({'source': source['id'], 'status': status, 'count': count, 'error': error})
    return {'sources': result, 'count': sum(r['count'] for r in result), 'partial': any(r['status'] != 'ok' for r in result)}
