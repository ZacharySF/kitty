"""Required Oracle timeline records and Workday Work Experience panels.

Uses explicit facts and scoped local saves. Selecting a card is not opening its
editor. Existing invalid/empty cards are inspected before Add; unknown layouts,
conflicting values and missing facts leave the retained draft for review.
Workday panels are filled in place only: Add is never clicked and Workday's own
Save and Continue remains with the preparation loop. This module never submits
an application.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import time
from typing import Callable, Literal, Sequence

from kitty.config import Config, EducationRecord, EmploymentRecord
from kitty.core.apply.bidi import BidiError, Frame, Tab
from kitty.core.apply.fields import FormField, Plan, clean_label, match_option
from kitty.core.apply.guard import RECORD_SAVE, UnsafeAction
from kitty.core.apply.page import (
    _DOM_HELPERS_JS, _SUGGESTIONS_JS, fill_text, form_frames, read_fields, tick,
)
from kitty.normalize import norm_question


RecordCause = Literal["missing_applicant_fact", "unsupported_controls", "browser_error"]


@dataclass
class RecordResult:
    handled: bool = False
    changed: bool = False
    blocker: str | None = None
    filled: list[str] = field(default_factory=list)
    cause: RecordCause = "unsupported_controls"


# Only the provider's native timeline structures establish action ownership.
# Remember DOM identities in the private sandbox, not in page attributes.
_HELPERS = _DOM_HELPERS_JS + r"""
  const shown = e => !!e?.isConnected && Array.from(e.getClientRects()).some(r => r.width > 0 && r.height > 0) &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none' &&
    !closest(e, '[hidden], [aria-hidden=true], [inert]');
  const text = e => (e?.innerText || '').replace(/\s+/g, ' ').trim();
  const safe = e => shown(e) && !closest(e, ':disabled, [disabled], [aria-disabled=true], [readonly]');
  const final = e => e.matches('input[type=submit], button:not([type]), button[type=submit]') ||
    /^(?:submit(?: application)?|send application|finish(?: application)?|complete application)$/i.test(text(e));
  const sections = kind => queryAll('form.apply-flow__content-form apply-flow-section[data-scroll-spy="apply-flow-sections"]')
    .filter(section => shown(section) && queryAll('button', section).some(e =>
      e.id === 'timeline-add-' + kind + '-button' && e.type === 'button' &&
      new RegExp('^add ' + kind + '$', 'i').test(text(e))));
  const cards = (section, kind) => queryAll('[data-profile-item-tile="true"]', section).filter(e => {
    const owner = closest(e, 'beautiful-timeline-item');
    const types = kind === 'experience' ? ['experiences'] : ['educations', 'education'];
    return e.isConnected && types.some(type =>
      new RegExp("(?:^|[,\\s])type:\\s*['\"]" + type + "['\"]").test(owner?.getAttribute('params') || '') &&
      [...e.classList].some(c => new RegExp('^beautiful-timeline-item--' + type + '__\\d+$').test(c)));
  });
  const content = tile => {
    const copy = tile.cloneNode(true);
    copy.querySelectorAll('button, [role=button], script, style').forEach(e => e.remove());
    return (copy.textContent || '').replace(/\s+/g, ' ').trim();
  };
  const keys = globalThis.kittyRecordKeys ??= new WeakMap();
  const keyOf = e => {
    if (!keys.has(e)) {
      const key = 'record' + (globalThis.kittyRecordCounter = (globalThis.kittyRecordCounter || 0) + 1);
      keys.set(e, key);
    }
    return keys.get(e);
  };
  const saves = (panel, kind) => queryAll('button[type=button]', panel).filter(e =>
    safe(e) && e.id !== 'timeline-add-' + kind + '-button' &&
    new RegExp('^(?:save|add ' + kind + ')$', 'i').test(text(e)));
  const editor = (section, kind, anchors) => {
    const matches = anchors.map(key => globalThis.kittyByKey?.get(key))
      .filter(e => e?.isConnected && shown(e) && fieldSafe(e) && contains(section, e));
    if (matches.length !== 1) return null;
    for (let panel = parentOf(matches[0]); panel && panel !== section; panel = parentOf(panel)) {
      if (!/timeline/i.test(panel.className || panel.tagName || '')) continue;
      if (queryAll('button, input[type=submit]', panel).some(final)) return null;
      const local = saves(panel, kind);
      if (local.length === 1) return {panel, save: local[0]};
      if (local.length > 1) return null;
    }
    return null;
  };
"""

_SNAPSHOT = r"""kind => {""" + _HELPERS + r"""
  return sections(kind).map(section => ({
    key: keyOf(section),
    // Editing can temporarily hide the invalid tile. Its owned marker still
    // matters: hiding it must not authorize a duplicate Add or prove completion.
    required: queryAll('[data-profile-item-invalid=true]', section).length > 0,
    cards: cards(section, kind).map(e => ({key: keyOf(e), text: content(e),
      invalid: e.getAttribute('data-profile-item-invalid') === 'true'}))
  }));
}"""

_ACTION = r"""(kind, sectionKey, action, cardKey) => {""" + _HELPERS + r"""
  const found = sections(kind);
  if (found.length !== 1 || keyOf(found[0]) !== sectionKey) return null;
  const section = found[0], entries = cards(section, kind);
  if (action === 'add') {
    // Add is allowed only for an observed missing record, never as a repair for
    // an existing invalid or empty card.
    if (entries.some(e => e.getAttribute('data-profile-item-invalid') === 'true' || !content(e))) return null;
    const add = queryAll('button', section).filter(e => safe(e) && e.type === 'button' &&
      e.id === 'timeline-add-' + kind + '-button' && e.getAttribute('data-profile-item-invalid') === 'true' &&
      new RegExp('^add ' + kind + '$', 'i').test(text(e)));
    return add.length === 1 ? add[0] : null;
  }
  const tile = entries.find(e => keyOf(e) === cardKey);
  if (!tile) return null;
  if (action === 'select') {
    if (!safe(tile) || tile.matches('a[href], button, input') ||
        closest(tile, 'a[href], button, [role=link]') ||
        queryAll('input, textarea, select', tile).some(shown)) return null;
    return tile;
  }
  const edits = queryAll('button[type=button][aria-label="Edit"]', tile).filter(safe);
  return action === 'edit' && edits.length === 1 ? edits[0] : null;
}"""

_EDITOR = r"""(kind, sectionKey, anchors) => {""" + _HELPERS + r"""
  const found = sections(kind);
  if (found.length !== 1 || keyOf(found[0]) !== sectionKey) return null;
  const owned = editor(found[0], kind, anchors);
  if (!owned) return null;
  const values = {};
  for (const [key, e] of globalThis.kittyByKey || []) {
    if (!e.isConnected || !contains(owned.panel, e)) continue;
    values[key] = {value: e.value || e.getAttribute('aria-valuetext') || '',
      checked: e.matches('[role=checkbox], [role=radio]') ? e.getAttribute('aria-checked') === 'true' : !!e.checked,
      valid: !e.willValidate || e.validity.valid};
  }
  const represented = Object.keys(values).map(key => globalThis.kittyByKey.get(key)).filter(fieldSafe);
  const unsupported = queryAll('[aria-required=true]', owned.panel).some(e =>
    shown(e) && !represented.some(input => contains(e, input)));
  return {values, unsupported, save: keyOf(owned.save)};
}"""

_SAVE = r"""(kind, sectionKey, anchors, saveKey) => {""" + _HELPERS + r"""
  const found = sections(kind);
  if (found.length !== 1 || keyOf(found[0]) !== sectionKey) return null;
  const owned = editor(found[0], kind, anchors);
  if (!owned || keyOf(owned.save) !== saveKey ||
      queryAll('input, textarea, select', owned.panel).some(e => shown(e) && e.willValidate && !e.validity.valid) ||
      queryAll('[aria-invalid=true], .input-row--invalid', owned.panel).some(shown)) return null;
  return owned.save;
}"""

_MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July',
           'August', 'September', 'October', 'November', 'December')
Record = EducationRecord | EmploymentRecord


def _identity(record: Record) -> tuple[str, ...]:
    if isinstance(record, EducationRecord):
        return (record.school,)
    return (record.employer, record.title)


def _matches(text: str, record: Record) -> bool:
    words = ' ' + norm_question(text) + ' '
    return all(' ' + norm_question(value) + ' ' in words for value in _identity(record))


def _plan(form_field: FormField, record: Record) -> Plan | bool | None:
    """Exact record field meanings only; no title/classification or degree inference."""
    label = norm_question(clean_label(form_field.label))
    values: dict[str, str | bool | None]
    if isinstance(record, EducationRecord):
        values = {
            'school': record.school, 'school name': record.school,
            'degree': record.degree, 'major': record.field_of_study,
            'field of study': record.field_of_study, 'comments': record.comments,
            'graduated': record.graduated if record.graduated is not None else (
                False if record.current is True else None),
            'current education': record.current, 'currently attending': record.current,
        }
        end = record.end_date or record.expected_end_date
    else:
        values = {
            'employer name': record.employer, 'employer': record.employer,
            'job title': record.title, 'title': record.title,
            'current job': record.current, 'current employment': record.current,
            'employment type': record.employment_type, 'type of employment': record.employment_type,
            'achievements': record.description, 'description': record.description,
            'city': record.city, 'state': record.state, 'country': record.country,
        }
        end = record.end_date
    dates = {'start date': record.start_date, 'end date': end,
             'expected end date': getattr(record, 'expected_end_date', None)}
    for name, date in dates.items():
        if label == name or label in (name + ' month', name + ' year'):
            if not date:
                return None
            year, month = date.split('-')
            if label.endswith(' month'):
                candidates = (_MONTHS[int(month) - 1], month, str(int(month)))
            elif label.endswith(' year'):
                candidates = (year,)
            elif form_field.input_type == 'month':
                candidates = (date,)
            else:
                return None             # never invent a day or date format
            return Plan(name, candidates, exact_choices=True)
    value = values.get(label)
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        if form_field.kind == 'checkbox' and len(form_field.options) == 1:
            return value
        return Plan(label, ('Yes' if value else 'No',), exact_choices=True)
    return Plan(label, (value,), exact_choices=True)


def _editor_state(frame: Frame, kind: str, section: str) -> tuple[list[FormField], dict | None, list[str]]:
    fields = read_fields([frame])
    labels = ('school', 'school name') if kind == 'education' else ('employer', 'employer name')
    anchors = [f.key for f in fields if norm_question(f.label) in labels]
    state = frame.call(_EDITOR, kind, section, anchors)
    return ([f for f in fields if state and f.key in state['values']], state, anchors)


def _accepted(frame: Frame, form_field: FormField, plan: Plan | bool, value: dict) -> bool:
    if isinstance(plan, bool):
        return value['checked'] is plan and value['valid']
    if form_field.kind == 'combobox':
        selected = frame.call(_SUGGESTIONS_JS, form_field.key, 0, 'selected') or []
        return any(norm_question(actual) == norm_question(wanted)
                   for actual in selected for wanted in plan.candidates)
    if form_field.kind == 'select':
        selected = frame.call("""key => {
          const e = globalThis.kittyByKey.get(key);
          return e?.isConnected ? [...e.selectedOptions].map(o => o.textContent.trim()) : [];
        }""", form_field.key)
        return match_option(plan.candidates, selected, prefix=False) is not None
    if form_field.kind in ('radio', 'checkbox'):
        index = match_option(plan.candidates, form_field.options, prefix=False)
        if index is None:
            return False
        return bool(frame.call("""key => {
          const e = globalThis.kittyByKey.get(key);
          return e?.isConnected && (e.checked || e.getAttribute('aria-checked') === 'true');
        }""", form_field.option_keys[index]))
    return value['value'].replace('\r\n', '\n') == plan.candidates[0].replace('\r\n', '\n')


def _fill_editor(frame: Frame, kind: str, section: str, record: Record,
                 checkpoint: Callable[[], None]) -> tuple[str | None, list[str], list[str], str | None, RecordCause]:
    # Local import: filler owns the common chooser/date transaction and imports
    # this adapter during preparation.
    from kitty.core.apply.filler import _apply_plan

    fields, state, anchors = _editor_state(frame, kind, section)
    if not state or not fields:
        return 'records: the selected card has not opened a uniquely scoped editor', [], [], None, 'unsupported_controls'
    if state['unsupported']:
        return 'records: this editor contains an unsupported required control', [], [], None, 'unsupported_controls'
    if isinstance(record, EducationRecord) and record.degree and record.graduated is None and record.current is not True:
        return 'records: education requires explicit current or graduated facts before entering a degree', [], [], None, 'missing_applicant_fact'
    if isinstance(record, EducationRecord) and record.degree and record.graduated is not True:
        status_fields = [f for f in fields if norm_question(f.label) in
                         ('graduated', 'current education', 'currently attending')]
        if not any(isinstance(_plan(f, record), (Plan, bool)) for f in status_fields):
            return 'records: this editor cannot preserve the explicit in-progress degree status', [], [], None, 'unsupported_controls'
    plans = [(f, _plan(f, record)) for f in fields]
    # Check the entire editor before entering any values, including unknown
    # required contact/supervisor fields. Existing unrelated data is never cleared.
    for form_field, plan in plans:
        value = state['values'][form_field.key]
        if form_field.kind == 'unsupported' and (form_field.required or form_field.invalid):
            return 'records: the editor has an unsupported required field', [], [], None, 'unsupported_controls'
        if plan is None:
            if (form_field.kind in ('checkbox', 'radio') and norm_question(form_field.label) in
                    ('graduated', 'current education', 'currently attending', 'current job', 'current employment')):
                return f'records: missing explicit status fact for {form_field.label}', [], [], None, 'missing_applicant_fact'
            if form_field.required and (form_field.blank or form_field.invalid):
                return f'records: missing explicit fact for required {form_field.label}', [], [], None, 'missing_applicant_fact'
            continue
        if not form_field.blank and not _accepted(frame, form_field, plan, value):
            return f'records: existing {form_field.label} conflicts with the configured record', [], [], None, 'unsupported_controls'
    filled: list[str] = []
    # Re-resolve after each write; Oracle date/choice changes can redraw controls.
    for original, plan in plans:
        if plan is None:
            continue
        checkpoint()
        current, state, anchors = _editor_state(frame, kind, section)
        matches = [f for f in current if (f.label, f.name, f.element_id) ==
                   (original.label, original.name, original.element_id)]
        if not state or len(matches) != 1:
            return 'records: editor ownership changed while entering a record', filled, [], None, 'unsupported_controls'
        form_field = matches[0]
        value = state['values'][form_field.key]
        if _accepted(frame, form_field, plan, value):
            continue
        if not form_field.blank:
            return f'records: preserving an existing {form_field.label} value', filled, [], None, 'unsupported_controls'
        if isinstance(plan, bool):
            ok = not plan and not value['checked'] or plan and tick(frame, form_field.option_keys[0])
        else:
            ok = _apply_plan([frame], form_field, plan)
        if not ok:
            return f'records: no committed choice or value for {form_field.label}', filled, [], None, 'unsupported_controls'
        filled.append(f'{kind}: {form_field.label}')
    current, state, anchors = _editor_state(frame, kind, section)
    if not state:
        return 'records: editor disappeared before the local save', filled, [], None, 'unsupported_controls'
    if state['unsupported']:
        return 'records: a new unsupported required control appeared before saving', filled, [], None, 'unsupported_controls'
    for form_field in current:
        plan = _plan(form_field, record)
        if ((plan is not None and not _accepted(frame, form_field, plan, state['values'][form_field.key])) or
                form_field.invalid or (form_field.required and
                                       (form_field.blank or not state['values'][form_field.key]['valid']))):
            return f'records: {form_field.label} has no valid committed receipt', filled, [], None, 'unsupported_controls'
    return None, filled, anchors, state['save'], 'unsupported_controls'


# Workday renders each Work Experience entry as an already-open panel whose
# controls carry stable `<prefix>--<slot>` IDs. Only these exact slots are
# recognized; any other required blank control is an unknown layout.
_WORKDAY_PANELS = r"""() => {""" + _HELPERS + r"""
  const known = /--(jobTitle|companyName|location|currentlyWorkHere|roleDescription|(?:start|end)Date-dateSection(?:Month|Year)-input)$/;
  // Workday date segments can be writable with tabIndex=-1, so the general
  // reader omits them. Register recognized segments for guarded primitives.
  const fieldKeys = globalThis.kittyKeys ??= new WeakMap();
  const fieldsByKey = globalThis.kittyByKey ??= new Map();
  const fieldKey = e => {
    if (!known.test(e.id || '') || !fieldSafe(e)) return null;
    if (!fieldKeys.has(e)) {
      const key = 'e' + (globalThis.kittyCounter = (globalThis.kittyCounter ?? 0) + 1);
      fieldKeys.set(e, key);
      fieldsByKey.set(key, e);
    }
    return fieldKeys.get(e);
  };
  const fieldLabel = e => {
    const owner = closest(e, '[data-automation-id^="formField-"]');
    return owner ? text(queryAll('label, legend', owner)[0]) : '';
  };
  // Live Workday labels each record group with aria-labelledby, not an automation id.
  return queryAll('[role=group][aria-labelledby]').filter(e =>
    /^Work-Experience-\d+-panel$/.test(e.getAttribute('aria-labelledby')) && shown(e)
  ).map(panel => ({
    key: keyOf(panel),
    controls: queryAll('input, textarea, select', panel).filter(e => shown(e) && e.type !== 'hidden').map(e => {
      const checkbox = e.type === 'checkbox';
      const value = checkbox ? '' : (e.value || '');
      return {
        id: e.id || '', slot: (known.exec(e.id || '') || [])[1] || null,
        key: fieldKey(e),
        label: fieldLabel(e) || e.getAttribute('aria-label') || e.id || '',
        value, checked: checkbox ? !!e.checked : false, checkbox, safe: fieldSafe(e),
        blank: checkbox ? false : !value.trim(),
        required: e.required || e.getAttribute('aria-required') === 'true' || /\*/.test(fieldLabel(e)),
        invalid: e.getAttribute('aria-invalid') === 'true' || (e.willValidate && !e.validity.valid && !!value),
      };
    }),
  }));
}"""

_WORKDAY_TEXT = ('jobTitle', 'companyName', 'location',
                 'startDate-dateSectionMonth-input', 'startDate-dateSectionYear-input',
                 'endDate-dateSectionMonth-input', 'endDate-dateSectionYear-input', 'roleDescription')


def _workday_values(record: EmploymentRecord) -> dict[str, str | bool | None]:
    """Exact Workday meanings from one explicit employment record; never inferred."""
    def date(value: str | None, month: bool) -> str | None:
        if not value:
            return None
        year, number = value.split('-')
        return f'{int(number):02d}' if month else year
    location = ', '.join(p for p in (record.city, record.state, record.country) if p) or None
    ended = record.end_date if record.current is not True else None
    return {
        'jobTitle': record.title, 'companyName': record.employer, 'location': location,
        'currentlyWorkHere': record.current, 'roleDescription': record.description or None,
        'startDate-dateSectionMonth-input': date(record.start_date, True),
        'startDate-dateSectionYear-input': date(record.start_date, False),
        'endDate-dateSectionMonth-input': date(ended, True),
        'endDate-dateSectionYear-input': date(ended, False),
    }


def _workday_same(slot: str, actual: str, wanted: str) -> bool:
    if 'dateSection' in slot:
        return actual.strip().isdigit() and int(actual) == int(wanted)
    if slot == 'roleDescription':
        return actual.replace('\r\n', '\n').strip() == wanted.replace('\r\n', '\n').strip()
    return norm_question(actual) == norm_question(wanted)


def _workday_label(control: dict) -> str:
    label = control['label'].rstrip('*').strip()
    if 'dateSection' in (control['slot'] or ''):
        label += ' month' if 'Month' in control['slot'] else ' year'
    return label


def _workday_panel(frame: Frame, panel_key: str) -> dict | None:
    read_fields([frame])           # registers sandbox keys for redrawn controls
    panels = frame.call(_WORKDAY_PANELS) or []
    return panels[0] if len(panels) == 1 and panels[0]['key'] == panel_key else None


def _workday_slots(state: dict) -> dict[str, dict] | str:
    slots: dict[str, dict] = {}
    for control in state['controls']:
        if control['slot'] is None:
            if control['required'] and control['blank']:
                return f"records: unsupported required Workday control {control['label']}"
            continue
        if control['slot'] in slots or not control['key'] or not control['safe']:
            return 'records: Workday Work Experience controls are not uniquely writable'
        slots[control['slot']] = control
    if 'jobTitle' not in slots or 'companyName' not in slots:
        return 'records: unrecognized Workday Work Experience panel layout'
    return slots


def _fill_workday_experience(page: Tab, frame: Frame, records: Sequence[EmploymentRecord],
                             check: Callable[[], None]) -> RecordResult | None:
    """Fill one open, required Workday Work Experience panel from the sole record.

    Returns None when no incomplete Workday panel exists. Never clicks Add,
    Save or navigation; never overwrites or clears an existing value.
    """
    read_fields([frame])
    panels = frame.call(_WORKDAY_PANELS) or []
    if not any(c['required'] and c['blank'] for p in panels for c in p['controls']):
        return None
    result = RecordResult(handled=True)
    if len(panels) != 1:
        result.blocker = 'records: multiple Workday Work Experience panels need review'
        return result
    if not records:
        result.blocker = 'records: Workday Work Experience requires an explicit employment record'
        result.cause = 'missing_applicant_fact'
        return result
    if len(records) != 1:
        result.blocker = 'records: Workday Work Experience needs one unambiguous explicit profile record'
        return result
    record, panel = records[0], panels[0]
    wanted = _workday_values(record)
    slots = _workday_slots(panel)
    if isinstance(slots, str):
        result.blocker = slots
        return result
    # Check the whole panel before entering anything: a different existing
    # value means another (manual) record, never something to overwrite.
    box = slots.get('currentlyWorkHere')
    if box and box['checked'] and wanted['currentlyWorkHere'] is not True:
        result.blocker = 'records: Workday panel marks a current job the configured record does not'
        return result
    for slot, control in slots.items():
        value = wanted.get(slot)
        if control['checkbox'] or value is None:
            if (control['required'] and control['blank'] and
                    not (slot.startswith('endDate') and wanted['currentlyWorkHere'] is True and box)):
                result.blocker = f'records: missing explicit fact for required Workday {_workday_label(control)}'
                result.cause = 'missing_applicant_fact'
                return result
            continue
        if not control['blank'] and not _workday_same(slot, control['value'], value):
            result.blocker = 'records: Workday Work Experience panel already holds different values'
            return result
    page.activate()
    if box and wanted['currentlyWorkHere'] is True and not box['checked']:
        check()
        if not tick(frame, box['key']):
            result.blocker = 'records: no committed Workday current job checkbox'
            return result
        result.filled.append('experience: I currently work here')
    for slot in _WORKDAY_TEXT:
        value = wanted[slot]
        if value is None:
            continue
        check()
        state = _workday_panel(frame, panel['key'])
        current = _workday_slots(state) if state else 'records: Workday panel ownership changed while entering a record'
        if isinstance(current, str):
            result.blocker = current
            return result
        control = current.get(slot)
        if control is None or not control['blank']:
            continue                   # absent (To while current) or already matching
        fill_text(frame, control['key'], value)   # receipt verified below, incl. MM/YYYY segments
        result.filled.append(f'experience: {_workday_label(control)}')
    # Workday's own Save and Continue (owned by the preparation loop) persists
    # the panel; here only verify committed values after blur handlers.
    state = _workday_panel(frame, panel['key'])
    final = _workday_slots(state) if state else 'records: Workday panel disappeared before verification'
    if isinstance(final, str):
        result.blocker = final
        return result
    for slot, control in final.items():
        value = wanted.get(slot)
        if control['invalid']:
            result.blocker = f'records: Workday {_workday_label(control)} has no valid committed value'
            return result
        if control['checkbox']:
            if value is True and not control['checked']:
                result.blocker = 'records: Workday current job checkbox was not committed'
                return result
            continue
        if value is not None and not _workday_same(slot, control['value'], value):
            result.blocker = f'records: Workday {_workday_label(control)} has no committed receipt'
            return result
        if control['required'] and control['blank']:
            result.blocker = f'records: missing explicit fact for required Workday {_workday_label(control)}'
            result.cause = 'missing_applicant_fact'
            return result
    result.changed = bool(result.filled)
    return result


def fill_required_records(page: Tab, config: Config, *, company: str = '', tracks: Sequence[str] = (),
                          checkpoint: Callable[[], None] | None = None) -> RecordResult:
    """Commit at most one required record; the owning preparation loop reinspects.

    No flat-profile fallback exists: record dates, qualification status and job
    classification must be explicit. ``changed`` means a matching committed card
    and cleared section markers, not merely successful typing or a Save click.
    """
    check = checkpoint or (lambda: None)
    check()
    try:
        for frame in form_frames(page):
            for kind, records in (('education', config.profile.education_records),
                                  ('experience', config.profile.employment_records)):
                check()
                snapshots = frame.call(_SNAPSHOT, kind)
                if not snapshots or not any(s['required'] for s in snapshots):
                    continue
                result = RecordResult(handled=True)
                if len(snapshots) != 1:
                    result.blocker = f'records: ambiguous required {kind} sections'
                    return result
                before = snapshots[0]
                entries = before['cards']
                invalid = [c for c in entries if c['invalid']]
                empty = [c for c in entries if not c['text']]
                targets = invalid or empty
                if len(targets) > 1:
                    result.blocker = f'records: multiple incomplete {kind} cards need review'
                    return result
                target = targets[0] if targets else None
                candidates = [r for r in records if not target or not target['text'] or _matches(target['text'], r)]
                if len(candidates) != 1:
                    result.blocker = f'records: {kind} needs one unambiguous explicit profile record'
                    if not records:
                        result.cause = 'missing_applicant_fact'
                    return result
                record = candidates[0]
                if any(_matches(c['text'], record) for c in entries if not target or c['key'] != target['key']):
                    result.blocker = f'records: a matching {kind} card already exists; refusing a duplicate'
                    return result
                page.activate()
                fields, state, anchors = _editor_state(frame, kind, before['key'])
                if not state:
                    action = 'edit' if target else 'add'
                    node = frame.node(_ACTION, kind, before['key'], action, target['key'] if target else '')
                    if target and node is None:
                        # Selection only reveals native Edit/Remove controls. It
                        # is never accepted as proof that the editor is open.
                        node = frame.node(_ACTION, kind, before['key'], 'select', target['key'])
                        if node is None:
                            result.blocker = f'records: cannot safely select the existing {kind} card'
                            return result
                        check()
                        frame.click(node, intent=RECORD_SAVE)
                        node = frame.node(_ACTION, kind, before['key'], 'edit', target['key'])
                    if node is None:
                        result.blocker = f'records: no unique native {kind} editor action'
                        return result
                    check()
                    frame.click(node, intent=RECORD_SAVE)
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        check()
                        fields, state, anchors = _editor_state(frame, kind, before['key'])
                        if state:
                            break
                        time.sleep(0.1)
                blocker, result.filled, anchors, save, result.cause = _fill_editor(
                    frame, kind, before['key'], record, check)
                if blocker:
                    result.blocker = blocker
                    return result
                check()
                node = frame.node(_SAVE, kind, before['key'], anchors, save)
                if node is None:
                    result.blocker = f'records: cannot prove one valid scoped local {kind} save'
                    return result
                frame.click(node, intent=RECORD_SAVE)   # exactly once; never retry Save
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    check()
                    after = frame.call(_SNAPSHOT, kind)
                    _, open_editor, _ = _editor_state(frame, kind, before['key'])
                    if len(after) == 1 and not after[0]['required'] and not open_editor:
                        cards = after[0]['cards']
                        matching = [c for c in cards if _matches(c['text'], record) and not c['invalid']]
                        unrelated = Counter(c['text'] for c in entries if not target or c['key'] != target['key'])
                        retained = Counter(c['text'] for c in cards if c not in matching)
                        expected_count = len(entries) + (0 if target else 1)
                        if len(matching) == 1 and len(cards) == expected_count and retained == unrelated:
                            result.changed = True
                            return result
                    time.sleep(0.1)
                result.blocker = f'records: local {kind} save lacks a matching committed card and cleared required markers'
                return result
            check()
            workday = _fill_workday_experience(page, frame, config.profile.employment_records, check)
            if workday is not None:
                return workday
    except UnsafeAction:
        return RecordResult(handled=True, blocker='records: an unsafe record interaction was refused; preserve the draft for review')
    except BidiError:
        return RecordResult(handled=True, blocker='records: guarded record interaction failed; preserve the draft for review',
                            cause='browser_error')
    return RecordResult()
