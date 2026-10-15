"""Reading form fields from a page and setting their values. Never submits.

Field writes are guarded against buttons and final actions. Step navigation
is separate in preflight.py and limited to recognized non-final Workday
controls; arbitrary buttons, submit, consent and legal actions are never clicked.

All JavaScript here runs in the filler's private sandbox (see bidi.py). The
reader remembers each element it found under a key such as "e12" in a
sandbox-only map, so the page's own HTML is never marked.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from kitty.core.apply.bidi import BidiError, Frame, Tab
from kitty.core.apply.fields import FormField, clean_label, match_option

#: Frames that never hold the application form.
_SKIP_FRAMES = ("recaptcha", "hcaptcha", "googleapis.com", "about:blank", "google.com/maps")

# Only a single native upload can represent a labelled ARIA upload group.
# Other custom questions containing native descendants still need review.
_UPLOAD_GROUP_JS = r"""
input => {
  if (!input.matches('input[type=file]') || input.disabled) return null;
  const group = input.closest('[role=group][aria-labelledby]');
  if (!group || group.querySelectorAll('input[type=file]').length !== 1) return null;
  const visible = e => e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const others = group.querySelectorAll('input:not([type=file]):not([type=hidden]):not([type=button]):not([type=submit]), textarea, select');
  return [...others].some(visible) ? null : group;
}
"""

# Greenhouse replaces its native file input with this receipt after upload.
# A filename alone is insufficient: require the labelled upload group, removal
# control, and absence of pending/error/editable states.
_UPLOADED_FILE_GROUP_JS = r"""
group => {
  const visible = e => !!e && !!e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  if (!group.matches('.file-upload[role=group][aria-labelledby]') || !visible(group) ||
      group.getAttribute('aria-invalid') === 'true') return false;
  if (group.querySelector('input:not([type=hidden]):not([type=button]):not([type=submit]), textarea, select')) return false;
  if ([...group.querySelectorAll('[role=progressbar], [aria-busy=true], [aria-invalid=true], .helper-text--error')]
      .some(visible)) return false;
  const receipts = [...group.querySelectorAll('.file-upload__filename')].filter(visible);
  if (receipts.length !== 1) return false;
  const receipt = receipts[0];
  const filename = receipt.querySelector('p.body');
  const remove = receipt.querySelectorAll('button[type=button][aria-label="Remove file"]');
  return visible(filename) && !!filename.textContent.trim() && remove.length === 1 &&
    visible(remove[0]) && !remove[0].disabled && remove[0].getAttribute('aria-disabled') !== 'true';
}
"""

#: Committed values, never the combobox's editable search query. React Select
#: uses both class-name prefixes and generated CSS suffixes across ATS versions.
_COMBO_VALUES_JS = r"""
box => {
  if (!box?.isConnected) return [];
  const shown = e => e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility !== 'hidden';
  const control = box.closest('[class*=control]') || box.parentElement;
  const values = [...control.querySelectorAll(
    '[class*=single-value], [class*=multi-value], [class$="-singleValue"], [class$="-multiValue"]'
  )].filter(shown).map(e => e.innerText.trim()).filter(Boolean);
  if (values.length) return values;
  const location = box.parentElement.querySelector('input[name=selectedLocation]');
  if (location?.value && box.value) {
    try {
      const selected = JSON.parse(location.value);
      return selected.name === box.value ? [box.value] : [];
    } catch {
      return location.value === box.value ? [box.value] : [];
    }
  }
  return [];
}
"""


#: Collects every input / textarea / select with its label and state.
#: Labels come from aria-labelledby, <label>, aria-label, then the nearest
#: question heading above the element (Lever and Ashby put the question in a
#: separate div). Placeholders are ignored: "Type here..." is not a question.
_READ_FIELDS_JS = r"""
() => {
  // Sandbox-only memory: element <-> key, kept across reads of the same document.
  const keys = globalThis.kittyKeys ??= new WeakMap();
  const byKey = globalThis.kittyByKey ??= new Map();
  const keyOf = el => {
    if (!keys.has(el)) {
      globalThis.kittyCounter = (globalThis.kittyCounter ?? 0) + 1;
      const key = 'e' + globalThis.kittyCounter;
      keys.set(el, key);
      byKey.set(key, el);
    }
    return keys.get(el);
  };
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const rendered = el => !!(el && el.getClientRects().length);
  const isOptionLabel = lab => {
    const target = lab.htmlFor ? document.getElementById(lab.htmlFor) : lab.querySelector('input');
    return !!target && (target.type === 'radio' || target.type === 'checkbox');
  };
  // Visible text only: wrapping <label>s also hold dropdown results, hints and <select> options.
  const textOf = node => {
    const parts = [];
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const parent = walker.currentNode.parentElement;
      if (!parent || parent.closest('select, option, textarea, [role=listbox], script, style')) continue;
      if (!parent.getClientRects().length) continue;
      parts.push(walker.currentNode.nodeValue);
    }
    return clean(parts.join(' '));
  };
  const ownLabelEl = el => (el.labels && el.labels.length ? el.labels[0] : null);
  const ownLabel = el => {
    const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    const byIds = clean(ids.map(id => { const n = document.getElementById(id); return n ? textOf(n) : ''; }).join(' '));
    if (byIds) return byIds;
    const lab = ownLabelEl(el);
    if (lab && textOf(lab)) return textOf(lab);
    return clean(el.getAttribute('aria-label'));
  };
  // The question heading nearest the element, searched only in ancestors that hold no
  // other field (radio / checkbox siblings of the same kind are the same question).
  const foreign = (node, el) => Array.from(
    node.querySelectorAll('input:not([type=hidden]), select, textarea')
  ).some(other => other !== el && !(['radio', 'checkbox'].includes(el.type) && other.type === el.type));
  const questionEl = el => {
    let node = el.parentElement;
    for (let depth = 0; node && depth < 6 && node !== document.body; depth++, node = node.parentElement) {
      if (foreign(node, el)) return null;
      for (const c of node.querySelectorAll('legend, label, [class*="label"], [class*="question"], [class*="title"]')) {
        if (c.contains(el) || c.querySelector('input, select, textarea')) continue;
        if (c.tagName === 'LABEL' && isOptionLabel(c)) continue;
        if (c.closest('[role=option], [role=listbox]')) continue;
        if (textOf(c)) return c;
      }
    }
    return null;
  };
  const looksRequired = node => !!node && (
    /[*✱]\s*$/.test(clean(node.textContent)) ||
    /required/i.test(String(node.className)) ||
    !!node.querySelector('[class*=required]'));
  const comboValues = (""" + _COMBO_VALUES_JS + r""");

  const uploadGroupFor = (""" + _UPLOAD_GROUP_JS + r""");
  const rows = [];
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search', 'password'].includes(type)) continue;
    const choice = type === 'radio' || type === 'checkbox';
    if (type === 'file') {
      // file inputs are usually hidden behind an "Attach" button; keep them all
    } else if (choice) {
      if (!rendered(el.parentElement)) continue;
    } else {
      if (!rendered(el) || el.getAttribute('aria-hidden') === 'true' || el.tabIndex < 0) continue;
      if (el.disabled || el.readOnly) continue;
    }
    const key = keyOf(el);
    const question = questionEl(el);
    const uploadGroup = type === 'file' ? uploadGroupFor(el) : null;
    const uploadLabel = uploadGroup ? ownLabel(uploadGroup) : '';
    const own = ownLabel(el);
    let kind = tag === 'textarea' ? 'textarea' : tag === 'select' ? 'select' : 'text';
    if (choice || type === 'file') kind = type;
    if (tag === 'input' && el.getAttribute('role') === 'combobox') kind = 'combobox';
    // Lever's location box: a plain input followed by a suggestions dropdown.
    if (tag === 'input' && type === 'text' && el.parentElement &&
        el.parentElement.querySelector('[class*=dropdown-results]')) kind = 'combobox';
    const label = uploadLabel || (choice || type === 'file'
      ? (question ? textOf(question) : own)
      : (own || (question ? textOf(question) : '')));
    let blank = !el.value;
    if (choice) blank = !el.checked;
    if (type === 'file') blank = !(el.files && el.files.length);
    if (kind === 'combobox') blank = comboValues(el).length === 0;
    if (tag === 'select') blank = !el.value || el.selectedIndex < 0;
    rows.push({
      key, kind, type, label, option: choice ? own : '',
      name: el.getAttribute('name') || '', id: el.id || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      required: !!el.required || el.getAttribute('aria-required') === 'true' ||
                uploadGroup?.getAttribute('aria-required') === 'true' ||
                looksRequired(ownLabelEl(el)) || looksRequired(question),
      options: tag === 'select'
        ? Array.from(el.options).filter(o => o.value !== '' && !o.disabled && !o.parentElement.disabled).map(o => clean(o.textContent))
        : [],
      blank,
      invalid: el.getAttribute('aria-invalid') === 'true' ||
               (!!el.willValidate && !el.validity.valid),
      autofill: !!el.closest('[class*=autofill]'),
    });
  }
  const uploadedGroup = (""" + _UPLOADED_FILE_GROUP_JS + r""");
  for (const group of document.querySelectorAll('.file-upload[role=group][aria-labelledby]')) {
    if (!uploadedGroup(group)) continue;
    const label = ownLabel(group);
    if (!label) continue;
    rows.push({
      key: keyOf(group), kind: 'file', type: 'file', label, option: '',
      name: '', id: '', autocomplete: '',
      required: group.getAttribute('aria-required') === 'true',
      options: [], blank: false, invalid: false,
      autofill: !!group.closest('[class*=autofill]'),
    });
  }
  return rows;
}
"""


class UnsafeAction(RuntimeError):
    """Raised when a fill helper is pointed at something that is not a form field."""


# ------------------------------------------------------------------ reading


def form_frames(tab: Tab) -> list[Frame]:
    """The tab's top document plus any iframes that can hold the form (embedded boards)."""
    main, *children = tab.frames()
    return [main] + [frame for frame in children if not any(s in frame.url for s in _SKIP_FRAMES)]


def read_fields(frames: list[Frame]) -> list[FormField]:
    """Every fillable field in these frames, radios and checkboxes grouped by question."""
    fields: list[FormField] = []
    for index, frame in enumerate(frames):
        try:
            rows = frame.call(_READ_FIELDS_JS)
        except BidiError:
            continue                    # a frame that navigated away or was detached
        fields.extend(_group(rows, index))
    return fields


def wait_for_form(tab: Tab, *, seconds: float = 15.0) -> tuple[list[Frame], list[FormField]]:
    """Frames and fields once the form has rendered: some fields, same count twice in a row.

    Single-page boards (Ashby) show an empty shell first and draw the form when
    their API answers, which can be well after the page itself went quiet.
    Gives up after `seconds` and returns whatever is there (maybe nothing).
    """
    deadline = time.monotonic() + seconds
    frames = form_frames(tab)
    fields = read_fields(frames)
    while time.monotonic() < deadline:
        time.sleep(0.7)
        frames, previous = form_frames(tab), fields
        fields = read_fields(frames)
        if fields and len(fields) == len(previous):
            break
    return frames, fields


def _group(rows: list[dict[str, Any]], frame: int) -> list[FormField]:
    """Turn raw element rows into FormFields; choices sharing a question become one field."""
    name_counts: dict[str, int] = {}
    for row in rows:
        if row["kind"] in ("radio", "checkbox") and row["name"]:
            name_counts[row["name"]] = name_counts.get(row["name"], 0) + 1

    groups: dict[str, list[dict[str, Any]]] = {}
    fields: list[FormField] = []
    for row in rows:
        if row["kind"] not in ("radio", "checkbox"):
            fields.append(_single(row, frame))
            continue
        if row["name"] and name_counts[row["name"]] > 1:
            group_key = f"name:{row['name']}"
        elif row["label"] and row["option"] and row["label"] != row["option"]:
            group_key = f"q:{row['kind']}:{row['label']}"      # Ashby: one name per checkbox
        else:
            group_key = f"el:{row['key']}"
        if group_key not in groups:
            groups[group_key] = []
            fields.append(FormField(key=group_key, kind=row["kind"], label="", frame=frame))
        groups[group_key].append(row)

    return [_choice(groups[f.key], frame) if f.key in groups else f for f in fields]


def _single(row: dict[str, Any], frame: int) -> FormField:
    """A text box, select, search box or file input."""
    return FormField(
        key=row["key"], kind=row["kind"], label=clean_label(row["label"]), frame=frame,
        input_type=row["type"], name=row["name"], element_id=row["id"],
        autocomplete=row["autocomplete"], required=bool(row["required"]),
        options=tuple(row["options"]), blank=bool(row["blank"]), in_autofill=bool(row["autofill"]),
        invalid=bool(row.get("invalid", False)),
    )


def _choice(rows: list[dict[str, Any]], frame: int) -> FormField:
    """A radio group or a set of checkboxes answering one question."""
    first = rows[0]
    return FormField(
        key=first["key"], kind=first["kind"],
        label=clean_label(first["label"] or first["option"]), frame=frame,
        input_type=first["type"], name=first["name"], element_id=first["id"],
        required=any(row["required"] for row in rows),
        options=tuple(clean_label(row["option"]) for row in rows),
        option_keys=tuple(row["key"] for row in rows),
        blank=all(row["blank"] for row in rows),
        invalid=any(row.get("invalid", False) for row in rows),
    )


# ------------------------------------------------------------------ writing


#: Shared start of the writer scripts: the element the reader remembered under `key`.
_ELEMENT = "(key) => { const e = globalThis.kittyByKey?.get(key); return e && e.isConnected ? e : null; }"


def _guard(frame: Frame, key: str) -> str:
    """Refuse anything but an input / textarea / select that is not a button.

    Returns the element's type (e.g. 'text', 'checkbox', 'select-one').
    """
    found = frame.call(
        f"(key) => {{ const e = ({_ELEMENT})(key);"
        " return e ? [e.tagName.toLowerCase(), (e.type || '').toLowerCase()] : null; }", key)
    if found is None:
        raise UnsafeAction("the field is no longer on the page")
    tag, kind = found
    if tag not in ("input", "textarea", "select") or kind in ("submit", "button", "image", "reset"):
        raise UnsafeAction(f"refusing to touch <{tag} type={kind}>")
    return kind


def _node(frame: Frame, key: str) -> str:
    """The BiDi reference of a remembered element, for clicks and file uploads."""
    shared_id = frame.node(_ELEMENT, key)
    if shared_id is None:
        raise UnsafeAction("the field is no longer on the page")
    return shared_id


#: Replace a box's text the way typing or pasting would: execCommand("insertText")
#: fires the same input events, so React-style forms keep the value. If the page
#: refuses, the value is set directly (the sandbox calls the browser's own value
#: setter, which frameworks still notice) and input/change are fired by hand.
_FILL_JS = r"""
(key, value) => {
  const el = globalThis.kittyByKey.get(key);
  el.focus();
  if (typeof el.select === 'function') el.select();
  const typed = document.execCommand(value ? 'insertText' : 'delete', false, value);
  if (!typed || el.value !== value) {
    el.value = value;
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  }
  el.blur();
  return el.value;
}
"""

#: Empty a box and leave the focus in it (blurring would let Lever-style boxes
#: run their "typed text alone does not stick" handler while typing starts).
_CLEAR_JS = r"""
(key) => {
  const el = globalThis.kittyByKey.get(key);
  el.focus();
  if (!el.value) return;
  el.select();
  if (!document.execCommand('delete') || el.value) {
    el.value = '';
    el.dispatchEvent(new Event('input', {bubbles: true}));
  }
}
"""


def fill_text(frame: Frame, key: str, value: str) -> None:
    """Set a text box or textarea. It never presses Enter.

    The box is blurred afterwards so date pickers and suggestion lists close.
    """
    _guard(frame, key)
    frame.call(_FILL_JS, key, value)


_SELECT_JS = r"""
(key, text) => {
  const el = globalThis.kittyByKey.get(key);
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const options = Array.from(el.options).filter(
    o => clean(o.textContent) === text && !o.disabled && !o.parentElement.disabled
  );
  if (options.length !== 1) return false;
  const option = options[0];
  el.focus();
  option.selected = true;
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  el.blur();
  return el.isConnected && option.isConnected && option.selected &&
    el.selectedOptions.length === 1 && el.selectedOptions[0] === option;
}
"""


def choose_select(frame: Frame, key: str, option_text: str) -> bool:
    """Pick one native option and verify its selected state after blur."""
    _guard(frame, key)
    return bool(frame.call(_SELECT_JS, key, option_text))


#: Scroll an element into view and say whether a click at its centre would land
#: on it (or on its own <label>) rather than on something drawn over it.
_REACHABLE_JS = r"""
(key) => {
  const el = globalThis.kittyByKey.get(key);
  el.scrollIntoView({block: 'center', inline: 'nearest'});
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return false;
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  if (!hit) return false;
  return hit === el || el.contains(hit) || Array.from(el.labels || []).some(l => l.contains(hit));
}
"""

_CHECKED_JS = "(key) => !!globalThis.kittyByKey.get(key).checked"


def tick(frame: Frame, key: str) -> bool:
    """Check a radio button or checkbox; return whether it ended up checked.

    A real mouse click when the box (or its label) is what a click at its
    centre would hit. Custom-styled choices often hide the real input; then the
    input's own click() is called instead, never a click at some coordinate,
    so it cannot land on a button.
    """
    if _guard(frame, key) not in ("radio", "checkbox"):
        raise UnsafeAction("tick() only checks radio buttons and checkboxes")
    if frame.call(_CHECKED_JS, key):
        return True
    if frame.call(_REACHABLE_JS, key):
        frame.click(_node(frame, key))
    if not frame.call(_CHECKED_JS, key):
        frame.call("(key) => globalThis.kittyByKey.get(key).click()", key)
    return bool(frame.call(_CHECKED_JS, key))


def upload(frame: Frame, key: str, path: Path) -> None:
    """Attach a file to a file input."""
    if _guard(frame, key) != "file":
        raise UnsafeAction("upload() only sets file inputs")
    frame.set_files(_node(frame, key), path)


#: Class of Lever's location suggestions (plain divs, no ARIA roles).
_SUGGESTION_CLASS = "dropdown-location"

#: What a suggestion list shows while its answers are still on the way
#: (Lever's location box: a "Loading" row while it asks its server).
_LOADING_SELECTOR = ".dropdown-loading-results, [aria-busy=true]"

#: The suggestions of a search box: role="option" elements in the list the box
#: controls (react-select, Greenhouse), else role="option" or Lever location
#: suggestions next to the box. Only suggestions that are shown count: Lever
#: keeps adding the answer of an old search to its closed (hidden) list.
#: `want` picks what to return about them.
_SUGGESTIONS_JS = r"""
(key, index, want, expected = null) => {
  const box = globalThis.kittyByKey.get(key);
  if (want === 'selected') return (""" + _COMBO_VALUES_JS + r""")(box);
  if (!box?.isConnected) return want === 'list' ? {texts: [], loading: false} : null;
  const listId = box.getAttribute('aria-controls') || box.getAttribute('aria-owns');
  const scope = listId ? document.getElementById(listId) : box.parentElement;
  const selector = listId ? '[role=option]' : '[role=option], .__CLASS__';
  const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const options = scope ? Array.from(scope.querySelectorAll(selector)).filter(
    e => shown(e) && e.getAttribute('aria-disabled') !== 'true' && !e.disabled
  ) : [];
  if (want === 'list') {
    const busy = scope ? [scope, ...scope.querySelectorAll('__LOADING__')] : [];
    return {texts: options.map(o => o.innerText),
            loading: busy.some(e => e.matches('__LOADING__') && shown(e))};
  }
  const option = options[index];
  if (!option || option.getAttribute('aria-disabled') === 'true') return null;
  const title = e => e.innerText.split(/\r?\n/).map(s => s.replace(/\s+/g, ' ').trim()).find(Boolean) || '';
  if (expected !== null && title(option) !== expected) return null;
  const targets = globalThis.kittySuggestionTargets ??= new Map();
  if (want === 'check') targets.set(key, option);
  else if (targets.get(key) !== option) return null;
  if (want === 'node') return option;
  if (want === 'click') {
    const role = option.getAttribute('role'), tag = option.tagName.toLowerCase();
    if ((role !== 'option' && !option.classList.contains('__CLASS__')) ||
        ['button', 'input', 'a'].includes(tag)) return false;
    // the events a mouse click sends: Lever picks a place on mousedown, react-select on click
    for (const type of ['mousedown', 'mouseup', 'click'])
      option.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, button: 0}));
    return true;
  }
  option.scrollIntoView({block: 'nearest'});
  const r = option.getBoundingClientRect();
  const hit = r.width && r.height
    ? document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) : null;
  return {
    role: option.getAttribute('role'), tag: option.tagName.toLowerCase(),
    css: String(option.className), text: option.innerText,
    reachable: !!hit && (hit === option || option.contains(hit)),
  };
}
""".replace("__CLASS__", _SUGGESTION_CLASS).replace("__LOADING__", _LOADING_SELECTOR)


#: WebDriver's code for the Down arrow key: opens a dropdown's list without typing.
_ARROW_DOWN = "\ue015"


def pick_from_search(
    frame: Frame, key: str, search_text: str, candidates: tuple[str, ...], *,
    location: bool, prefix: bool = True,
) -> bool:
    """Type into a search-as-you-type box and click the suggestion that matches.

    The only element ever clicked is a suggestion: a role="option" in the list
    the box controls (react-select, Greenhouse) or a Lever location suggestion
    next to the box. If nothing matches, the typed text is cleared.
    """
    _guard(frame, key)
    typed = " ".join(search_text.split())[:80]     # no newlines: typing one would press Enter
    frame.call(_CLEAR_JS, key)                     # empty it and keep the focus there ...
    frame.type(typed)                              # ... then real key presses open the list
    options = _wait_for_options(frame, key)
    return _click_match(frame, key, options, candidates, location=location, prefix=prefix)


def pick_from_list(
    frame: Frame, key: str, candidates: tuple[str, ...], *, location: bool
) -> bool:
    """Open and inspect existing choices before resorting to a typed search.

    Only exact names / configured aliases count in this possibly partial list.
    The short probe avoids the search wait on boxes that stay empty until typed;
    actual searches retain their full loading-indicator wait (notably Lever).
    """
    _guard(frame, key)
    frame.call(_CLEAR_JS, key)
    if frame.call(_REACHABLE_JS, key):
        frame.click(_node(frame, key))
    frame.type(_ARROW_DOWN)                        # open keyboard-only lists; never Enter
    options = _wait_for_options(frame, key, seconds=0.8)
    return _click_match(frame, key, options, candidates, location=location, prefix=False)


def _click_match(
    frame: Frame, key: str, options: list[str], candidates: tuple[str, ...], *,
    location: bool, prefix: bool,
) -> bool:
    """Click a matching suggestion and verify that its selected value sticks."""
    index = match_option(candidates, options, location=location, prefix=prefix)
    if index is None:
        frame.call(_FILL_JS, key, "")
        return False
    found = frame.call(_SUGGESTIONS_JS, key, index, "check", options[index])
    if found is None or _option_title(found["text"]) != options[index]:
        frame.call(_FILL_JS, key, "")
        return False
    role, tag, css = found["role"], found["tag"], found["css"]
    if (role != "option" and _SUGGESTION_CLASS not in css) or tag in ("button", "input", "a"):
        raise UnsafeAction(f"refusing to click <{tag} role={role}>")
    option = frame.node(_SUGGESTIONS_JS, key, index, "node", options[index])
    if option is None:
        frame.call(_FILL_JS, key, "")
        return False
    try:
        if found["reachable"]:
            frame.click(option)
        else:
            frame.call(_SUGGESTIONS_JS, key, index, "click", options[index])
    except BidiError:
        frame.call(_FILL_JS, key, "")
        raise
    frame.call("(key) => globalThis.kittyByKey.get(key).blur()", key)
    deadline = time.monotonic() + 0.8
    while time.monotonic() < deadline:
        selected = frame.call(_SUGGESTIONS_JS, key, 0, "selected")
        if match_option((options[index],), selected, location=location, prefix=False) is not None:
            return True
        time.sleep(0.1)
    frame.call(_FILL_JS, key, "")
    return False


def _option_title(text: str) -> str:
    """An option's name: its first non-empty line. Ashby adds country and domain lines below."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return next((line for line in lines if line), "")


def _wait_for_options(frame: Frame, key: str, seconds: float = 12.0) -> list[str]:
    """The name of each suggestion once the list has loaded and stopped changing.

    While the list shows a loading row its answer is still on the way: Lever's
    location box asks its server 0.5 s after the last key press and the answer
    has taken over 1.5 s. Giving up then would clear the box, and Lever's blur
    handler would clear its hidden selectedLocation with it. An empty list that
    has not changed for 2 seconds with nothing loading counts as "no suggestions".
    """
    start = changed_at = time.monotonic()
    previous: list[str] = []
    while time.monotonic() - start < seconds:
        shown = frame.call(_SUGGESTIONS_JS, key, 0, "list")
        texts = [_option_title(t) for t in shown["texts"]]
        if texts != previous or shown["loading"]:
            changed_at = time.monotonic()
        elif texts:
            return texts
        elif time.monotonic() - changed_at >= 2.0:
            return []
        previous = texts
        time.sleep(0.4)
    return previous


#: True while a board shows a spinner for an upload or resume parse (Lever: "Analyzing resume...").
_BUSY_JS = r"""
() => Array.from(document.querySelectorAll('.resume-upload-working, [role=progressbar]'))
  .some(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' &&
    !e.closest('[data-automation-id=progressBar]'))
"""


def uploads_pending(tab: Tab) -> bool:
    """Whether any application frame still reports an upload or resume parse in progress."""
    return any(frame.call(_BUSY_JS) for frame in form_frames(tab))


def wait_for_uploads(tab: Tab, *, timeout_ms: int = 30000) -> bool:
    """Return completion evidence; timeout or a detached frame is not upload success."""
    settle(tab, timeout_ms=5000)
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        try:
            if not uploads_pending(tab):
                settle(tab, timeout_ms=5000)
                if not uploads_pending(tab):
                    return True
        except BidiError:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


#: Resolves once the document has finished loading and its DOM has not changed
#: for `quietMs`, or after `timeoutMs` whatever happens (pages with endless
#: animations never go quiet).
_SETTLE_JS = r"""
(quietMs, timeoutMs) => new Promise(resolve => {
  const start = performance.now();
  let last = start;
  const watcher = new MutationObserver(() => { last = performance.now(); });
  watcher.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
  const check = () => {
    const now = performance.now();
    const quiet = document.readyState === 'complete' && now - last >= quietMs;
    if (quiet || now - start >= timeoutMs) { watcher.disconnect(); resolve(true); }
    else setTimeout(check, 100);
  };
  check();
})
"""


def settle(tab: Tab, *, timeout_ms: int = 10000) -> None:
    """Wait until the page has stopped loading and changing (or give up quietly after a while)."""
    try:
        tab.main.call(_SETTLE_JS, 500, timeout_ms, timeout=timeout_ms / 1000)
    except BidiError:
        pass                            # navigated meanwhile, or the page is gone
