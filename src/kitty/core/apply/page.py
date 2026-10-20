"""Reading form fields from a page and setting their values. Never submits.

Field writes are guarded against arbitrary buttons and final actions. Explicit
ARIA choices and value-backed application listboxes are fields, not navigation.
Consent policy belongs to the caller; reading a legal choice never accepts it.
Native/ARIA fields in nested open shadow roots use tree-local label and list IDs.
Closed roots remain unsupported; composed ancestor guards apply to every write.

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


#: Shared traversal contract: (selector, root=document) -> unique Element[].
#: Includes a matching Element root and descendants in recursively OPEN shadow
#: roots. Closed roots are intentionally inaccessible. IDs are never resolved
#: with this traversal: their meaning is local to the referencing tree root.
_QUERY_ALL_JS = r"""
(selector, root = document) => {
  const found = [], seen = new Set();
  const visit = node => {
    if (node.nodeType === Node.ELEMENT_NODE && node.matches(selector) && !seen.has(node)) {
      seen.add(node); found.push(node);
    }
    if (node.shadowRoot) visit(node.shadowRoot);
    for (const child of node.children || []) visit(child);
  };
  visit(root);
  return found;
}
"""

_COMPOSED_CLOSEST_JS = r"""
(element, selector) => {
  for (let node = element; node; node = node.assignedSlot || node.parentElement || node.getRootNode()?.host) {
    if (node.matches?.(selector)) return node;
  }
  return null;
}
"""

#: Native fields too can be embedded in a submit/link shadow host. Never write
#: through action, disabled or read-only ancestors, even after DOM mutation.
_FIELD_SAFE_JS = r"""
e => {
  if (!e?.isConnected) return false;
  const closest = (""" + _COMPOSED_CLOSEST_JS + r""");
  const listButton = node => {
    if (!node.matches('button[type=button][aria-haspopup=listbox][name][id][value]') ||
        (node.hasAttribute('role') && node.getAttribute('role') !== 'combobox')) return false;
    const group = closest(node, '[data-automation-id^="formField-"][data-fkit-id]');
    return group?.getAttribute('data-fkit-id') === node.id &&
      Array.from(group.querySelectorAll('label[for]')).some(label => label.htmlFor === node.id) &&
      Array.from(node.parentElement?.children || []).filter(sibling =>
        sibling.matches('input[type=text]') && sibling.value === node.value).length === 1;
  };
  if (closest(e, ':disabled, [disabled], [readonly], [aria-disabled=true], [aria-readonly=true], [inert]')) return false;
  const actions = 'button, a[href], input[type=submit], input[type=image], input[type=reset], [role=button], [role=link]';
  for (let node = e; node; node = node.assignedSlot || node.parentElement || node.getRootNode()?.host) {
    if (node.matches?.(actions) && !(node === e && (listButton(e) || e.matches('button[type=button][role=checkbox], button[type=button][role=radio], button[type=button][role=combobox], input[type=button][role=checkbox], input[type=button][role=radio]'))))
      return false;
  }
  return true;
}
"""

_DOM_HELPERS_JS = (
    "const queryAll = (" + _QUERY_ALL_JS + ");"
    "const closest = (" + _COMPOSED_CLOSEST_JS + ");"
    "const fieldSafe = (" + _FIELD_SAFE_JS + ");"
    r"""
  const parentOf = e => e?.assignedSlot || e?.parentElement || e?.getRootNode()?.host || null;
  const contains = (outer, inner) => {
    for (let n = inner; n; n = parentOf(n)) if (n === outer) return true;
    return false;
  };
  const reference = (e, id) => id ? e.getRootNode().getElementById?.(id) || null : null;
  const labelsOf = e => {
    const labels = Array.from(e.labels || []);
    const root = e.getRootNode();
    if (e.id) for (const label of root.querySelectorAll('label[for]'))
      if (label.htmlFor === e.id && !labels.includes(label)) labels.push(label);
    return labels;
  };
  const composedText = node => {
    if (node.nodeType === Node.TEXT_NODE) return node.nodeValue;
    const assigned = node.matches?.('slot') ? node.assignedNodes({flatten: true}) : [];
    return Array.from(assigned.length ? assigned : (node.shadowRoot || node).childNodes)
      .map(composedText).join(' ');
  };
  const multiselectOf = e => {
    if (!e.matches('input[data-uxi-widget-type=selectinput][data-uxi-multiselect-id]')) return null;
    const owner = e.getAttribute('data-uxi-multiselect-id');
    if (!owner) return null;
    const group = closest(e, '[data-automation-id=multiSelectContainer]');
    return group?.id === owner ? group : null;
  };
  const hitAt = (x, y) => {
    let hit = document.elementFromPoint(x, y);
    while (hit?.shadowRoot) {
      const inner = hit.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === hit) break;
      hit = inner;
    }
    return hit;
  };
"""
)
# Only a single native upload can represent a labelled ARIA upload group.
# Other custom questions containing native descendants still need review.
_UPLOAD_GROUP_JS = r"""
input => {""" + _DOM_HELPERS_JS + r"""
  if (!input.matches('input[type=file]') || !fieldSafe(input)) return null;
  const group = closest(input, '[role=group][aria-labelledby]');
  if (!group || queryAll('input[type=file]', group).length !== 1) return null;
  const visible = e => e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const others = queryAll('input:not([type=file]):not([type=hidden]):not([type=button]):not([type=submit]), textarea, select', group);
  return others.some(visible) ? null : group;
}
"""

# Greenhouse and Oracle replace the native input with an accepted-file receipt.
# A filename alone is insufficient: require the known labelled upload component,
# enabled removal control, and absence of pending/error/editable states.
_UPLOADED_FILE_GROUP_JS = r"""
group => {""" + _DOM_HELPERS_JS + r"""
  const visible = e => !!e && !!e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  if (group.matches('resume-upload-button')) {
    if (!visible(group) || !fieldSafe(group) ||
        closest(group, '[aria-busy=true], [aria-invalid=true], .input-row--invalid') ||
        !closest(group, 'form.apply-flow__content-form') ||
        !closest(group, '.apply-flow-block--file-upload') ||
        !closest(group, '.file-upload-wrapper__section') ||
        queryAll('input, textarea, select', group).length) return false;
    if (queryAll('[role=progressbar], [aria-busy=true], [aria-invalid=true], .input-row--invalid, .attachment-upload-button--uploading, .attachment-upload-button--required', group).length ||
        queryAll('[id^="file-upload-errors-"]', group).some(e => e.textContent.trim())) return false;
    const filled = queryAll('.attachment-upload-button--filled .attachment-upload-button__filled', group);
    if (filled.length !== 1 || !visible(filled[0])) return false;
    const downloads = queryAll('button[type=button].attachment-upload-button__download', filled[0]);
    const removes = queryAll('button[type=button].attachment-upload-button__bottom-button', filled[0]);
    return downloads.length === 1 && removes.length === 1 && visible(downloads[0]) &&
      !!downloads[0].textContent.trim() && !downloads[0].disabled &&
      /(?:^|[,\s])click:\s*downloadAttachment(?:[,\s]|$)/.test(downloads[0].getAttribute('data-bind') || '') &&
      visible(removes[0]) && /^remove resume$/i.test(removes[0].textContent.trim()) &&
      /(?:^|[,\s])click:\s*removeFile(?:[,\s]|$)/.test(removes[0].getAttribute('data-bind') || '') &&
      !removes[0].disabled && removes[0].getAttribute('aria-disabled') !== 'true';
  }
  if (!group.matches('.file-upload[role=group][aria-labelledby]') || !visible(group) ||
      group.getAttribute('aria-invalid') === 'true') return false;
  if (queryAll('input:not([type=hidden]):not([type=button]):not([type=submit]), textarea, select', group).length) return false;
  if (queryAll('[role=progressbar], [aria-busy=true], [aria-invalid=true], .helper-text--error', group)
      .some(visible)) return false;
  const receipts = queryAll('.file-upload__filename', group).filter(visible);
  if (receipts.length !== 1) return false;
  const receipt = receipts[0];
  const filename = queryAll('p.body', receipt)[0];
  const remove = queryAll('button[type=button][aria-label="Remove file"]', receipt);
  return visible(filename) && !!filename.textContent.trim() && remove.length === 1 &&
    visible(remove[0]) && !remove[0].disabled && remove[0].getAttribute('aria-disabled') !== 'true';
}
"""


_RETAINED_RESUME_JS = r"""
input => {""" + _DOM_HELPERS_JS + r"""
  const host = input.getRootNode()?.host;
  if (!host?.matches('spl-dropzone[data-test=resume-upload], spl-dropzone[data-test=apply-with-resume-container]') ||
      !fieldSafe(input) || queryAll('input[type=file]', host).length !== 1 ||
      closest(host, '[aria-invalid=true], [aria-busy=true]') ||
      queryAll('[role=progressbar], [aria-busy=true], [aria-invalid=true]', host).length) return false;
  try {
    const files = JSON.parse(host.getAttribute('files') || 'null');
    return Array.isArray(files) && files.length === 1 &&
      files[0].attachmentType === 'RESUME' && typeof files[0].fileName === 'string' &&
      !!files[0].fileName.trim() && files[0].name === files[0].fileName &&
      /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(files[0].fileId);
  } catch {
    return false;
  }
}
"""

#: ARIA choices and labelled value-backed list buttons extend the native allowlist.
#: A button without type=button, a link, or a field nested in either is never a choice.
#: Other non-editable comboboxes need an owned list with explicit selection state.
_CUSTOM_CONTROL_JS = r"""
e => {""" + _DOM_HELPERS_JS + r"""
  if (!e || e.matches('input:not([type=button]), textarea, select') ||
      queryAll('input, textarea, select, button, a[href], [role=button], [role=link]', e).some(other => other !== e)) return '';
  const buttonList = e.matches('button[type=button][aria-haspopup=listbox]') &&
    !!closest(e, '[data-automation-id^="formField-"][data-fkit-id]');
  const role = buttonList ? 'combobox' : e.getAttribute('role');
  if (!['checkbox', 'radio', 'combobox'].includes(role) || !fieldSafe(e)) return '';
  if (role !== 'combobox') {
    const states = role === 'checkbox' ? ['true', 'false', 'mixed'] : ['true', 'false'];
    return states.includes(e.getAttribute('aria-checked')) ? role : '';
  }
  if (e.getAttribute('aria-haspopup') && e.getAttribute('aria-haspopup') !== 'listbox') return '';
  if (buttonList) return 'combobox';
  const id = e.getAttribute('aria-controls') || e.getAttribute('aria-owns');
  const list = reference(e, id);
  return list?.getAttribute('role') === 'listbox' &&
    queryAll('[role=option][aria-selected=true], [role=option][aria-selected=false]', list).length ? role : '';
}
"""

# A calling code is not a country identity (+1 also belongs to Canada). Carry
# only an owned option's flag/code pair into the same control's value renderer.
_COUNTRY_IDENTITY_JS = r"""
node => {
  const flags = queryAll('.iti__flag', node).filter(e =>
    e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
  if (flags.length !== 1) return null;
  const countries = Array.from(flags[0].classList).filter(name => /^iti__[a-z]{2}$/.test(name));
  const code = (node.innerText || composedText(node)).trim().match(/\+(\d{1,3})$/);
  return countries.length === 1 && code ? countries[0] + ':' + code[1] : null;
}
"""


#: Committed values, never the combobox's editable search query. React Select
#: uses both class-name prefixes and generated CSS suffixes across ATS versions.
_COMBO_VALUES_JS = r"""
box => {""" + _DOM_HELPERS_JS + r"""
  if (!box?.isConnected) return [];
  if (box.matches('button[type=button][aria-haspopup=listbox][value]') &&
      closest(box, '[data-automation-id^="formField-"][data-fkit-id]') && fieldSafe(box)) {
    const text = composedText(box).replace(/\s+/g, ' ').trim();
    return box.value && text ? [text] : [];
  }
  const multiselect = multiselectOf(box);
  if (multiselect) return queryAll('[data-automation-id=selectedItemList] [data-automation-id=promptOption][data-automation-label]', multiselect)
    .filter(e => closest(e, '[data-uxi-multiselect-id]')?.getAttribute('data-uxi-multiselect-id') === box.getAttribute('data-uxi-multiselect-id'))
    .map(e => e.getAttribute('data-automation-label')).filter(Boolean);
  const oracle = box.matches('input.cx-select-input[role=combobox][aria-haspopup=grid]') &&
    closest(box, '.cx-select-container');
  if (oracle) {
    const list = reference(box, box.getAttribute('aria-controls'));
    const selected = list?.getAttribute('role') === 'grid'
      ? queryAll('[role=gridcell][aria-selected=true]', list) : [];
    const clean = value => (value || '').replace(/\s+/g, ' ').trim();
    const value = clean(box.value);
    if (value && box.getAttribute('aria-expanded') === 'false' &&
        box.getAttribute('aria-invalid') !== 'true' &&
        closest(box, '.input-row')?.classList.contains('input-row--filled'))
      return [value];
    const matchesValue = text => value && (text === value ||
      (/^\+\d{1,3}$/.test(value) && text.startsWith(value + ' (')));
    const values = selected.map(e => clean(composedText(e))).filter(matchesValue);
    if (values.length === 1) {
      (globalThis.kittyCommittedCombos ??= new WeakMap()).set(box, {value, text: values[0]});
      return values;
    }
    const committed = globalThis.kittyCommittedCombos?.get(box);
    return committed && value === committed.value && box.getAttribute('aria-invalid') !== 'true'
      ? [committed.text] : [];
  }
  if (!box.matches('input:not([type=button]), textarea') && box.getAttribute('role') === 'combobox') {
    const id = box.getAttribute('aria-controls') || box.getAttribute('aria-owns');
    const list = reference(box, id);
    return list ? queryAll('[role=option][aria-selected=true]', list)
      .map(e => composedText(e).replace(/\s+/g, ' ').trim()).filter(Boolean) : [];
  }
  const shown = e => e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility !== 'hidden';
  const control = closest(box, '[class*=control]') || parentOf(box);
  const receipts = queryAll(
    '[class*=single-value], [class*=multi-value], [class$="-singleValue"], [class$="-multiValue"]', control
  ).filter(e => shown(e) && !closest(e, '[role=option], [role=listbox]') &&
    (closest(e, '[class*=control]') || parentOf(box)) === control);
  const countryIdentity = (""" + _COUNTRY_IDENTITY_JS + r""");
  const observed = globalThis.kittyComboCountryChoices?.get(box);
  const values = receipts.map(e => {
    const text = e.innerText.trim();
    if (!/^\+\d{1,3}$/.test(text)) return text;
    const identity = countryIdentity(e);
    return !box.value && box.getAttribute('aria-expanded') === 'false' &&
      box.getAttribute('aria-invalid') !== 'true' && identity && observed?.has(identity)
      ? observed.get(identity) : text;
  }).filter(Boolean);
  if (values.length) return values;
  const location = queryAll('input[name=selectedLocation]', parentOf(box))[0];
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
() => {""" + _DOM_HELPERS_JS + r"""
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
    const target = lab.htmlFor ? reference(lab, lab.htmlFor) : queryAll('input', lab)[0];
    return !!target && (target.type === 'radio' || target.type === 'checkbox');
  };
  // Visible text only: wrapping <label>s also hold dropdown results, hints and <select> options.
  const textOf = node => {
    const parts = [];
    const seen = new Set();
    const visit = current => {
      if (seen.has(current)) return;
      seen.add(current);
      if (current.nodeType === Node.TEXT_NODE) {
        const parent = current.parentElement || current.getRootNode()?.host;
        if (parent?.getClientRects().length &&
            !closest(parent, 'select, option, textarea, [role=listbox], script, style'))
          parts.push(current.nodeValue);
        return;
      }
      if (current.matches?.('slot')) {
        const assigned = current.assignedNodes({flatten: true});
        if (assigned.length) { assigned.forEach(visit); return; }
      }
      for (const child of (current.shadowRoot || current).childNodes) visit(child);
    };
    visit(node);
    return clean(parts.join(' '));
  };
  const ownLabelEl = el => labelsOf(el)[0] || null;
  const ownLabel = el => {
    const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    const byIds = clean(ids.map(id => { const n = reference(el, id); return n ? textOf(n) : ''; }).join(' '));
    if (byIds) return byIds;
    const lab = ownLabelEl(el);
    if (lab && textOf(lab)) return textOf(lab);
    const aria = clean(el.getAttribute('aria-label'));
    if (aria) return aria;
    if (['checkbox', 'radio'].includes(el.getAttribute('role'))) return textOf(el) || clean(el.value);
    // Phenom's form components use a literal label attribute without <label for>.
    return el.matches('.phw-form-control[form-field]') ? clean(el.getAttribute('label')) : '';
  };
  // The question heading nearest the element, searched only in ancestors that hold no
  // other field (radio / checkbox siblings of the same kind are the same question).
  const foreign = (node, el) => queryAll(
    'input:not([type=hidden]), select, textarea, [role=checkbox], [role=radio], [role=combobox]', node
  ).some(other => other !== el && !(['radio', 'checkbox'].includes(el.getAttribute('role') || el.type) &&
    (other.getAttribute('role') || other.type) === (el.getAttribute('role') || el.type)));
  const questionEl = el => {
    let node = parentOf(el);
    for (let depth = 0; node && depth < 8 && node !== document.body; depth++, node = parentOf(node)) {
      if (foreign(node, el)) return null;
      for (const c of queryAll('legend, label, [class*="label"], [class*="question"], [class*="title"]', node)) {
        if (contains(c, el) || queryAll('input, select, textarea', c).length) continue;
        if (c.tagName === 'LABEL' && isOptionLabel(c)) continue;
        if (closest(c, '[role=option], [role=listbox]')) continue;
        if (textOf(c)) return c;
      }
    }
    return null;
  };
  const looksRequired = node => !!node && (
    /[*✱]\s*$/.test(textOf(node)) ||
    [...node.classList].some(name => /^(?:is[-_])?required$|(?:__|[_-])required(?:-mark|-indicator)?$/i.test(name)) ||
    queryAll('[class*=required]', node).some(marker =>
      /[*✱]/.test(textOf(marker)) || /required/i.test(marker.getAttribute('aria-label') || '')));
  const comboValues = (""" + _COMBO_VALUES_JS + r""");

  const uploadGroupFor = (""" + _UPLOAD_GROUP_JS + r""");
  const customKind = (""" + _CUSTOM_CONTROL_JS + r""");
  const retainedResume = (""" + _RETAINED_RESUME_JS + r""");
  const rows = [];
  for (const el of queryAll(
      'input, textarea, select, [role=checkbox], [role=radio], [role=combobox], button[type=button][aria-haspopup=listbox]')) {
    if (!fieldSafe(el)) continue;
    const tag = el.tagName.toLowerCase();
    const custom = customKind(el);
    if (!el.matches('input, textarea, select') && !custom) continue;
    const type = custom || (el.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search', 'password'].includes(type)) continue;
    const choice = type === 'radio' || type === 'checkbox';
    if (type === 'file') {
      // file inputs are usually hidden behind an "Attach" button; keep them all
    } else if (choice) {
      if (el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true') continue;
      if (custom && (!rendered(el) || getComputedStyle(el).visibility === 'hidden')) continue;
      // An unlabelled hidden backing input does not make submit-button choices safe.
      if (!rendered(el) && !labelsOf(el).length &&
          queryAll('button:not([type=button]), input[type=submit], input[type=image]', parentOf(el)).length) continue;
      if (!rendered(parentOf(el))) continue;
    } else {
      if (!rendered(el) || el.getAttribute('aria-hidden') === 'true' || el.tabIndex < 0) continue;
      if (el.disabled || el.readOnly || el.getAttribute('aria-disabled') === 'true') continue;
    }
    const key = keyOf(el);
    const question = questionEl(el);
    const uploadGroup = type === 'file' ? uploadGroupFor(el) : null;
    const resumeRegion = type === 'file' &&
      closest(el, 'oc-resume-upload, [data-test="resume-upload"], [data-test="resume-upload-container"]');
    const uploadLabel = uploadGroup ? ownLabel(uploadGroup) : '';
    const own = ownLabel(el);
    const group = choice ? closest(el, '[role=group], [role=radiogroup], fieldset') : null;
    const groupLabel = group ? ownLabel(group) : '';
    let kind = tag === 'textarea' ? 'textarea' : tag === 'select' ? 'select' : 'text';
    if (choice || type === 'file') kind = type;
    if (custom === 'combobox' || (tag === 'input' && el.getAttribute('role') === 'combobox')) kind = 'combobox';
    // Lever's location box: a plain input followed by a suggestions dropdown.
    if (tag === 'input' && type === 'text' && parentOf(el) &&
        queryAll('[class*=dropdown-results]', parentOf(el)).length) kind = 'combobox';
    if (multiselectOf(el)) kind = 'combobox';
    const unownedPrompt = el.matches('input[data-uxi-widget-type=selectinput]') && !multiselectOf(el);
    if (unownedPrompt) kind = 'unsupported';
    const label = uploadLabel || (resumeRegion ? 'Resume' : '') || groupLabel || (choice || type === 'file'
      ? (question ? textOf(question) : own)
      : (own || (question ? textOf(question) : '')));
    let blank = !el.value;
    if (choice) blank = custom ? el.getAttribute('aria-checked') !== 'true' : !el.checked;
    if (type === 'file') blank = !(el.files && el.files.length) && !retainedResume(el);
    if (kind === 'combobox') blank = comboValues(el).length === 0;
    const uncommitted = kind === 'combobox' && !!el.value && comboValues(el).length === 0 &&
      (!!multiselectOf(el) || el.matches('input.cx-select-input[aria-haspopup=grid]'));
    if (uncommitted) blank = false;
    if (unownedPrompt) blank = true;
    if (tag === 'select') blank = !el.value || el.selectedIndex < 0;
    rows.push({
      key, kind, type, label, option: choice ? own : '',
      scope: keyOf(group || el.getRootNode()),
      name: el.getAttribute('name') || '', id: el.id || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      required: !!el.required || el.getAttribute('aria-required') === 'true' ||
                uploadGroup?.getAttribute('aria-required') === 'true' ||
                group?.getAttribute('aria-required') === 'true' || group?.hasAttribute('required') ||
                (el.getAttribute('aria-required') !== 'false' &&
                 (looksRequired(ownLabelEl(el)) || looksRequired(question) ||
                  looksRequired(group && queryAll('legend', group)[0]))),
      options: tag === 'select'
        ? Array.from(el.options).filter(o => o.value !== '' && !o.disabled && !o.parentElement.disabled).map(o => clean(o.textContent))
        : [],
      blank,
      invalid: unownedPrompt || uncommitted || el.getAttribute('aria-invalid') === 'true' ||
               (!!el.willValidate && !el.validity.valid),
      autofill: !!closest(el, '[class*=autofill], oc-apply-with-resume, [data-test="apply-with-resume-container"], [class*="apply-flow-profile-import"]'),
      attachmentOnly: type === 'file' && el.name === 'attachment-upload' &&
        el.classList.contains('file-form-element__input') &&
        !!closest(el, 'form.apply-flow__content-form') &&
        queryAll('input.apply-flow-profile-import-awli__file-upload', el.form).length > 0,
    });
  }
  const uploadedGroup = (""" + _UPLOADED_FILE_GROUP_JS + r""");
  for (const group of queryAll('.file-upload[role=group][aria-labelledby], resume-upload-button')) {
    if (!uploadedGroup(group)) continue;
    const label = group.matches('resume-upload-button') ? 'Resume' : ownLabel(group);
    if (!label) continue;
    rows.push({
      key: keyOf(group), kind: 'file', type: 'file', label, option: '',
      name: '', id: '', autocomplete: '',
      required: group.getAttribute('aria-required') === 'true',
      options: [], blank: false, invalid: false,
      autofill: !!closest(group, '[class*=autofill], oc-apply-with-resume, [class*="apply-flow-profile-import"]'),
      attachmentOnly: group.matches('resume-upload-button') &&
        !!queryAll('input.apply-flow-profile-import-awli__file-upload', closest(group, 'form'))[0],
    });
  }
  // Active required questions whose only controls are unsupported actions stay
  // reportable. Never revive a disabled/read-only/action-wrapped subtree.
  for (const group of queryAll('.ashby-application-form-field-entry, fieldset, [role=group], [role=radiogroup]')) {
    if (!fieldSafe(group) || !rendered(group) || getComputedStyle(group).visibility === 'hidden' ||
        rows.some(row => contains(group, byKey.get(row.key)))) continue;
    const heading = queryAll('legend, label, [class*="question-title"]', group)
      .find(node => !isOptionLabel(node));
    const required = group.hasAttribute('required') || group.getAttribute('aria-required') === 'true' ||
      looksRequired(heading);
    const label = ownLabel(group) || (heading ? textOf(heading) : '');
    if (!required || !label ||
        !queryAll('button, input, [role=button], [role=combobox]', group).length) continue;
    rows.push({
      key: keyOf(group), kind: 'unsupported', type: '', label, option: '',
      name: '', id: group.id || '', autocomplete: '', required: true,
      options: [], blank: true, invalid: false, autofill: false,
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
    name_counts: dict[tuple[str, str], int] = {}
    for row in rows:
        if row["kind"] in ("radio", "checkbox") and row["name"]:
            identity = (row.get("scope", ""), row["name"])
            name_counts[identity] = name_counts.get(identity, 0) + 1

    groups: dict[str, list[dict[str, Any]]] = {}
    fields: list[FormField] = []
    for row in rows:
        if row["kind"] not in ("radio", "checkbox"):
            fields.append(_single(row, frame))
            continue
        scope = row.get("scope", "")
        if row["name"] and name_counts[(scope, row["name"])] > 1:
            group_key = f"{scope}:name:{row['name']}"
        elif row["label"] and row["option"] and row["label"] != row["option"]:
            group_key = f"{scope}:q:{row['kind']}:{row['label']}"  # one name per checkbox
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
        attachment_only=bool(row.get("attachmentOnly", False)),
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
_ELEMENT = (
    "(key) => { const e = globalThis.kittyByKey?.get(key); return ("
    + _FIELD_SAFE_JS + ")(e) ? e : null; }"
)


def _guard(frame: Frame, key: str, *, custom: tuple[str, ...] = ()) -> str:
    """Refuse arbitrary actions; custom field kinds require a caller allowlist."""
    found = frame.call(
        f"(key) => {{ const e = ({_ELEMENT})(key);"
        " return e ? [e.tagName.toLowerCase(), (e.type || '').toLowerCase(), "
        f"({_CUSTOM_CONTROL_JS})(e)] : null; }}", key)
    if found is None:
        raise UnsafeAction("the field is detached, disabled, read-only or inside an action")
    tag, kind, aria_kind = found
    if aria_kind in custom:
        return aria_kind
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
(key, value) => {""" + _DOM_HELPERS_JS + r"""
  const el = globalThis.kittyByKey.get(key);
  const writable = () => fieldSafe(el) && el.matches('input:not([type=button]):not([type=submit]):not([type=image]):not([type=reset]), textarea');
  if (!writable()) return el?.value || '';
  globalThis.kittyComboCountryChoices?.delete(el);
  el.focus();
  if (!writable()) return el?.value || '';
  if (typeof el.select === 'function') el.select();
  const typed = document.execCommand(value ? 'insertText' : 'delete', false, value);
  if (!writable()) return el?.value || '';
  if (!typed || el.value !== value) {
    el.value = value;
    el.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
    if (writable()) el.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
  }
  el.blur();
  return el.value;
}
"""

#: Empty a box and leave the focus in it (blurring would let Lever-style boxes
#: run their "typed text alone does not stick" handler while typing starts).
_CLEAR_JS = r"""
(key) => {""" + _DOM_HELPERS_JS + r"""
  const el = globalThis.kittyByKey.get(key);
  const writable = () => fieldSafe(el) && el.matches('input:not([type=button]):not([type=submit]):not([type=image]):not([type=reset]), textarea');
  if (!fieldSafe(el)) return;
  el.focus();
  if (!writable()) return;
  if (!el.value) return;
  el.select();
  if ((!document.execCommand('delete') || el.value) && writable()) {
    el.value = '';
    el.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
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
(key, text) => {""" + _DOM_HELPERS_JS + r"""
  const el = globalThis.kittyByKey.get(key);
  if (!fieldSafe(el) || !el.matches('select')) return false;
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const options = Array.from(el.options).filter(
    o => clean(o.textContent) === text && !o.disabled && !o.parentElement.disabled
  );
  if (options.length !== 1) return false;
  const option = options[0];
  el.focus();
  if (!fieldSafe(el) || !option.isConnected || option.disabled || option.parentElement.disabled) return false;
  option.selected = true;
  el.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
  if (fieldSafe(el)) el.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
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
(key) => {""" + _DOM_HELPERS_JS + r"""
  const el = globalThis.kittyByKey.get(key);
  if (!fieldSafe(el)) return false;
  el.scrollIntoView({block: 'center', inline: 'nearest'});
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return false;
  const hit = hitAt(r.left + r.width / 2, r.top + r.height / 2);
  if (!hit || !fieldSafe(el)) return false;
  const action = closest(hit, 'button, a[href], input[type=submit], input[type=image], [role=button], [role=link]');
  return (!action || action === el) &&
    (contains(el, hit) || labelsOf(el).some(l => contains(l, hit)));
}
"""

_CHECKED_JS = r"""(key) => {
  const el = globalThis.kittyByKey.get(key);
  return el.matches('input[type=checkbox], input[type=radio]') ? !!el.checked : el.getAttribute('aria-checked') === 'true';
}"""

#: Prefer a reachable native label over a synthetic click on its hidden input.
#: Do not click a terms link, another action, or an overlay inside that label.
_CHOICE_TARGET_JS = r"""
(key) => {""" + _DOM_HELPERS_JS + r"""
  const el = globalThis.kittyByKey.get(key);
  if (!fieldSafe(el)) return null;
  for (const target of [el, ...labelsOf(el)]) {
    target.scrollIntoView({block: 'center', inline: 'nearest'});
    const r = target.getBoundingClientRect();
    if (!r.width || !r.height || getComputedStyle(target).visibility === 'hidden') continue;
    const hit = hitAt(r.left + r.width / 2, r.top + r.height / 2);
    if (!hit || !contains(target, hit) || !fieldSafe(el)) continue;
    const action = closest(hit, 'a[href], button, input[type=submit], input[type=image], [role=button], [role=link]');
    if (action && action !== el) continue;
    return target;
  }
  return null;
}
"""


def tick(frame: Frame, key: str) -> bool:
    """Check an explicit native/ARIA choice, verifying the resulting state.

    Consent authorization is the caller's responsibility. This helper never
    changes disabled/read-only attributes or installs event interception.
    """
    if _guard(frame, key, custom=("radio", "checkbox")) not in ("radio", "checkbox"):
        raise UnsafeAction("tick() only checks radio buttons and checkboxes")
    if frame.call(_CHECKED_JS, key):
        return True
    target = frame.node(_CHOICE_TARGET_JS, key)
    if target is not None:
        frame.click(target)
    else:
        # Preserve the exact native-input fallback for CSS-hidden choices. ARIA
        # controls must be reachable: never bypass an overlay with their click().
        frame.call("""(key) => {""" + _DOM_HELPERS_JS + r"""
          const el = globalThis.kittyByKey.get(key);
          if (fieldSafe(el) && el.matches('input[type=checkbox], input[type=radio]') &&
              !labelsOf(el).some(label => closest(label, 'button, a[href], [role=button], [role=link]'))) el.click();
        }""", key)
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
_SUGGESTIONS_JS = (r"""
(key, index, want, expected = null) => {""" + _DOM_HELPERS_JS + r"""
  const box = globalThis.kittyByKey.get(key);
  if (want === 'selected') return (""" + _COMBO_VALUES_JS + r""")(box);
  if (!fieldSafe(box)) return want === 'list' ? {texts: [], loading: false} : null;
  const listId = box.getAttribute('aria-controls') || box.getAttribute('aria-owns');
  const multiselect = multiselectOf(box);
  // Workday portals carry the exact input owner's ID, not an aria-controls ID.
  // Never inspect another question's portal or pick one of duplicate owners.
  const prompts = multiselect ? queryAll(
    '[data-automation-id=responsiveMonikerPrompt][data-automation-type=singleSelectPrompt][data-associated-widget]',
    box.getRootNode()
  ).filter(e => e.getAttribute('data-associated-widget') === multiselect.id && fieldSafe(e)) : [];
  const scope = listId ? reference(box, listId)
    : prompts.length === 1 ? prompts[0] : multiselect || parentOf(box);
  const oracleGrid = scope?.getAttribute('role') === 'grid' &&
    box.matches('input.cx-select-input[role=combobox][aria-haspopup=grid]');
  const selector = listId ? (oracleGrid ? '[role=gridcell]' : '[role=option]') : '[role=option], .__CLASS__';
  const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const options = scope ? queryAll(selector, scope).filter(
    e => shown(e) && fieldSafe(e) && !closest(e, '[data-automation-id=selectedItemList]') &&
      e.getAttribute('aria-disabled') !== 'true' &&
      !queryAll('button, input, a[href], [role=button], [role=link]', e).length
  ) : [];
  if (want === 'list') {
    const busy = scope ? queryAll('__LOADING__', scope) : [];
    return {texts: options.map(o => o.innerText || composedText(o)),
            loading: busy.some(e => e.matches('__LOADING__') && shown(e))};
  }
  const option = options[index];
  if (!option || option.getAttribute('aria-disabled') === 'true') return null;
  const title = e => (e.innerText || composedText(e)).split(/\r?\n/).map(s => s.replace(/\s+/g, ' ').trim()).find(Boolean) || '';
  if (expected !== null && title(option) !== expected) return null;
  const targets = globalThis.kittySuggestionTargets ??= new Map();
  if (want === 'check') {
    targets.set(key, option);
    const countryIdentity = (""" + _COUNTRY_IDENTITY_JS + r""");
    const identity = listId && scope?.getAttribute('role') === 'listbox' && countryIdentity(option);
    if (identity) {
      const choices = globalThis.kittyComboCountryChoices ??= new WeakMap();
      if (!choices.has(box)) choices.set(box, new Map());
      choices.get(box).set(identity, title(option));
    }
  }
  else if (targets.get(key) !== option) return null;
  if (want === 'node') return option;
  if (want === 'click') {
    const role = option.getAttribute('role'), tag = option.tagName.toLowerCase();
    if ((role !== 'option' && !(oracleGrid && role === 'gridcell') && !option.classList.contains('__CLASS__')) ||
        ['button', 'input', 'a'].includes(tag)) return false;
    // the events a mouse click sends: Lever picks a place on mousedown, react-select on click
    for (const type of ['mousedown', 'mouseup', 'click'])
      if (fieldSafe(box) && fieldSafe(option) &&
          !queryAll('button, input, a[href], [role=button], [role=link]', option).length)
        option.dispatchEvent(new MouseEvent(type, {bubbles: true, composed: true, cancelable: true, button: 0}));
    return true;
  }
  option.scrollIntoView({block: 'nearest'});
  const r = option.getBoundingClientRect();
  const hit = r.width && r.height
    ? hitAt(r.left + r.width / 2, r.top + r.height / 2) : null;
  return {
    role: oracleGrid && option.getAttribute('role') === 'gridcell' ? 'option' : option.getAttribute('role'), tag: option.tagName.toLowerCase(),
    css: String(option.className), text: option.innerText || composedText(option),
    pointerOnly: oracleGrid || box.matches('button[type=button][aria-haspopup=listbox], input[data-uxi-widget-type=selectinput]'),
    reachable: !!hit && contains(option, hit) &&
      !closest(hit, 'button, input, a[href], [role=button], [role=link]'),
  };
}
""").replace("__CLASS__", _SUGGESTION_CLASS).replace("__LOADING__", _LOADING_SELECTOR)


#: WebDriver's code for the Down arrow key: opens a dropdown's list without typing.
_ARROW_DOWN = "\ue015"


def pick_from_search(
    frame: Frame, key: str, search_text: str, candidates: tuple[str, ...], *,
    location: bool, prefix: bool = True, graduation: str | None = None,
) -> bool:
    """Type into a search-as-you-type box and click the suggestion that matches.

    The only element ever clicked is a suggestion: a role="option" in the list
    the box controls (react-select, Greenhouse) or a Lever location suggestion
    next to the box. If nothing matches, the typed text is cleared.
    """
    if _guard(frame, key, custom=("combobox",)) == "combobox":
        return False                             # non-editable ARIA field, never type into it
    selected = frame.call(_SUGGESTIONS_JS, key, 0, "selected")
    if selected:
        return match_option(candidates, selected, location=location, prefix=False,
                            graduation=graduation) is not None
    if not frame.call(_REACHABLE_JS, key):
        return False
    typed = " ".join(search_text.split())[:80]     # no newlines: typing one would press Enter
    frame.call(_CLEAR_JS, key)                     # empty it and keep the focus there ...
    _guard(frame, key)
    frame.type(typed)                              # ... then real key presses open the list
    options = _wait_for_options(frame, key)
    return _click_match(frame, key, options, candidates, location=location, prefix=prefix,
                        graduation=graduation)


def pick_from_list(
    frame: Frame, key: str, candidates: tuple[str, ...], *, location: bool,
    graduation: str | None = None,
) -> bool:
    """Open and inspect existing choices before resorting to a typed search.

    Only exact names / configured aliases count in this possibly partial list.
    The short probe avoids the search wait on boxes that stay empty until typed;
    actual searches retain their full loading-indicator wait (notably Lever).
    """
    _guard(frame, key, custom=("combobox",))
    selected = frame.call(_SUGGESTIONS_JS, key, 0, "selected")
    if selected:
        return match_option(candidates, selected, location=location, prefix=False,
                            graduation=graduation) is not None
    if not frame.call(_REACHABLE_JS, key):
        return False
    frame.call(_CLEAR_JS, key)
    if frame.call(_REACHABLE_JS, key):
        frame.click(_node(frame, key))
    _guard(frame, key, custom=("combobox",))
    frame.type(_ARROW_DOWN)                        # open keyboard-only lists; never Enter
    options = _wait_for_options(frame, key, seconds=0.8)
    return _click_match(frame, key, options, candidates, location=location, prefix=False,
                        graduation=graduation)


def _click_match(
    frame: Frame, key: str, options: list[str], candidates: tuple[str, ...], *,
    location: bool, prefix: bool, graduation: str | None = None,
) -> bool:
    """Click a matching suggestion and verify that its selected value sticks."""
    index = match_option(candidates, options, location=location, prefix=prefix,
                         graduation=graduation)
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
    if not found["reachable"] and found.get("pointerOnly"):
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
    frame.call(_SUGGESTIONS_JS, key, 0, "selected")
    frame.call("(key) => globalThis.kittyByKey.get(key).blur()", key)
    deadline = time.monotonic() + 0.8
    while time.monotonic() < deadline:
        selected = frame.call(_SUGGESTIONS_JS, key, 0, "selected")
        if match_option((options[index], *candidates), selected, location=location, prefix=False) is not None:
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
() => {""" + _DOM_HELPERS_JS + r"""
  return queryAll('.resume-upload-working, [role=progressbar]')
    .some(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' &&
      !closest(e, '[data-automation-id=progressBar]'));
}
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
(quietMs, timeoutMs) => new Promise(resolve => {""" + _DOM_HELPERS_JS + r"""
  const start = performance.now();
  let last = start;
  const observed = new WeakSet();
  const options = {subtree: true, childList: true, attributes: true, characterData: true};
  const watchRoots = root => {
    if (!observed.has(root)) { watcher.observe(root, options); observed.add(root); }
    for (const host of queryAll('*', root)) {
      if (host.shadowRoot && !observed.has(host.shadowRoot)) {
        watcher.observe(host.shadowRoot, options);
        observed.add(host.shadowRoot);
      }
    }
  };
  const watcher = new MutationObserver(records => {
    last = performance.now();
    for (const record of records) for (const added of record.addedNodes)
      if (added.nodeType === Node.ELEMENT_NODE) watchRoots(added);
  });
  watchRoots(document);
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
