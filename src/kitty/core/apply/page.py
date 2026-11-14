"""Reading form fields from a page and setting their values. Never submits.

Field writes are guarded against arbitrary buttons and final actions. Explicit
ARIA choices and value-backed application listboxes are fields, not navigation.
Consent policy belongs to the caller; reading a legal choice never accepts it.
Native/ARIA fields in nested open shadow roots use tree-local label and list IDs.
Closed roots remain unsupported; composed ancestor guards apply to every write.
Workday's owned native list icon, category rows and back header are navigation
only; a saved 'Category > Leaf' path, complete hierarchy discovery, unique leaf
matching and committed receipts remain the answer boundary.
Oracle date parts can also retain an observed trusted option click's changed
native value before the shared date row is complete; later edits revoke it.
Every writer first applies I-NoHidden′ to the element itself: traps (honey-pot,
beecatcher, captcha, "leave this blank") are never written, and an element that
is not painted is written only through its painted host (a label, upload group,
family attach control, styled choice box or Workday prompt). The reader reports
a required refused element (`FormField.refused`) and skips an optional one.

All JavaScript here runs in the filler's private sandbox (see bidi.py). The
reader remembers each element it found under a key such as "e12" in a
sandbox-only map, so the page's own HTML is never marked.
"""

from __future__ import annotations

import random
import re
import time
from pathlib import Path
from typing import Any

from kitty.core.apply.bidi import BidiError, ConnectionLost, Frame, Tab
from kitty.core.apply.fields import FormField, clean_label, match_option, pick_source_option
from kitty.core.apply.guard import ACTIVATE_JS, FIELD, LIST_COMMIT, UnsafeAction
from kitty.normalize import norm_location, norm_question

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
    // Questionnaire questions label the single list button with a fieldset legend.
    const legendOwned = group?.getAttribute('data-fkit-id') === node.id &&
      group.querySelectorAll('fieldset > legend').length === 1 &&
      group.querySelectorAll('button[aria-haspopup=listbox]').length === 1;
    return group?.getAttribute('data-fkit-id') === node.id &&
      (Array.from(group.querySelectorAll('label[for]')).some(label => label.htmlFor === node.id) || legendOwned) &&
      Array.from(node.parentElement?.children || []).filter(sibling =>
        sibling.matches('input[type=text]') && sibling.value === node.value).length === 1;
  };
  if (closest(e, ':disabled, [disabled], [readonly], [aria-disabled=true], [aria-readonly=true], [inert]')) return false;
  const actions = 'button, a[href], input[type=submit], input[type=image], input[type=reset], [role=button], [role=link]';
  // Lever wraps its resume input in a non-navigating <a href="#"> trigger; set_files
  // never activates that ancestor, so it is not an action barrier for file inputs.
  const fileTrigger = node => e.matches('input[type=file]') && node !== e &&
    node.matches('a[href="#"]:not([target]):not([download])');
  // Oracle renders some choice questions as type=button pills (fastapply notes):
  // a pill only selects its own option, like an ARIA radio button.
  const pill = node => node.matches('button[type=button].cx-select-pill-section') &&
    !!closest(node, '.cx-select-pills-container');
  for (let node = e; node; node = node.assignedSlot || node.parentElement || node.getRootNode()?.host) {
    if (node.matches?.(actions) && !fileTrigger(node) && !(node === e && (listButton(e) || pill(e) || e.matches('button[type=button][role=checkbox], button[type=button][role=radio], button[type=button][role=combobox], input[type=button][role=checkbox], input[type=button][role=radio]'))))
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
    if (group?.id !== owner) return null;
    const owners = queryAll('[data-automation-id=multiSelectContainer]', e.getRootNode())
      .filter(candidate => candidate.id === owner);
    const inputs = queryAll('input[data-uxi-widget-type=selectinput][data-uxi-multiselect-id]', group)
      .filter(candidate => candidate.getAttribute('data-uxi-multiselect-id') === owner);
    return owners.length === 1 && inputs.length === 1 && inputs[0] === e ? group : null;
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
  // An Oracle pill's selected state lives in its class (or aria-pressed/selected).
  const pillOf = e => e?.matches?.('button[type=button].cx-select-pill-section') &&
    closest(e, '.cx-select-pills-container') ? e : null;
  const ariaChecked = e => pillOf(e)
    ? e.classList.contains('cx-select-pill-section--selected') || ['aria-pressed', 'aria-selected', 'aria-checked']
      .some(name => e.getAttribute(name) === 'true')
    : e.getAttribute('aria-checked') === 'true';
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
  // Oracle pills (no role): one choice each; a multi-select container says so.
  if (pillOf(e) && !e.hasAttribute('role'))
    return !fieldSafe(e) ? '' :
      closest(e, '.cx-select-pills-container').getAttribute('aria-multiselectable') === 'true' ? 'checkbox' : 'radio';
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

#: I-NoHidden′ (research note no-hidden.md §2): kitty writes, ticks, chooses or
#: uploads to an element only if it is not a trap (T) and is painted itself or is
#: reached through a painted host from a closed list (H). An expression for a
#: scope with the DOM helpers; `refusal(e, label)` gives {trap, host, refused}.
#: Style alone cannot separate a decoy from a hidden backing input (Greenhouse's
#: 1 px resume input is shaped like a clipped decoy): the host decides.
_NO_HIDDEN_JS = r"""(() => {
  const T1_NAME = /honey[-_ ]?pot|captcha|turnstile/i;
  const T1_PHRASE = /(?:leave|keep) (?:this |it |the )?(?:field |box |input )?(?:blank|empty)|do not (?:fill|enter|type|complete|change)|don'?t (?:fill|enter)|if you are (?:a )?human|robots? only|for (?:bots|robots|spam)/i;
  const T2 = [/(?:^|[-_ ])honey(?:$|[-_ ])/i, /^hp[-_]|[-_]hp$/i, /(?:^|[-_])(?:trap|bot[-_]?check|nospam|anti[-_]?spam)(?:$|[-_])/i];
  const FILE_WORDS = /attach|upload|browse|choose file|select files?|drag|drop|resume|\bcv\b|cover letter/i;
  const chain = e => { const out = []; for (let n = e; n; n = parentOf(n)) if (n.nodeType === 1) out.push(n); return out; };
  const clipped = (n, style, own) => {
    const r = n.getBoundingClientRect();
    if (style.clip && style.clip !== 'auto' && /^(?:absolute|fixed)$/.test(style.position)) {
      const [top, right, bottom, left] = (style.clip.match(/-?[\d.]+/g) || []).map(Number);
      if (right - left <= 1 || bottom - top <= 1) return true;
    }
    const path = style.clipPath || 'none';
    const inset = path.match(/^inset\(([^)]*)\)/);
    if (inset && inset[1].split(/\s+/).some(v => /%$/.test(v) && Number.parseFloat(v) >= 50)) return true;
    if (/^circle\(\s*0(?:px|%)?(?:\s|\))/.test(path)) return true;
    return !own && (style.overflowX !== 'visible' || style.overflowY !== 'visible') && (r.width < 4 || r.height < 4);
  };
  // painted(e): '' when painted, else the failing clause (§2.1). `small` skips
  // the element's own size test (a react-select input shrinks to its query).
  const unpainted = (e, small = false) => {
    if (!e?.isConnected || e.nodeType !== 1) return 'detached';
    if (e.matches('input[type=hidden]') || !e.getClientRects().length) return 'shown';
    if (typeof e.checkVisibility === 'function' &&
        !e.checkVisibility({visibilityProperty: true, opacityProperty: true})) return 'shown';
    let opacity = 1;
    for (const [depth, n] of chain(e).entries()) {
      if (n.hasAttribute('hidden') || n.hasAttribute('inert') || n.getAttribute('aria-hidden') === 'true') return 'ancestor';
      const style = getComputedStyle(n);
      opacity *= Number.parseFloat(style.opacity);
      if (!(opacity >= 0.1)) return 'opacity';
      if (depth <= 8 && clipped(n, style, n === e)) return 'clip';
    }
    const r = e.getBoundingClientRect();
    if (!small && (r.width < 4 || r.height < 4)) return 'size';
    const doc = document.documentElement;
    const width = Math.max(doc.scrollWidth, innerWidth), height = Math.max(doc.scrollHeight, innerHeight);
    const left = r.left + scrollX, top = r.top + scrollY;
    if (left + r.width <= 0 || top + r.height <= 0 || left >= width || top >= height) return 'offscreen';
    return '';
  };
  const painted = e => !unpainted(e);
  const idsText = (e, name) => (e.getAttribute(name) || '').split(/\s+/).filter(Boolean)
    .map(id => reference(e, id)).filter(Boolean).map(composedText).join(' ');
  const tokens = e => [e.getAttribute('name') || '', e.id || '', ...Array.from(e.classList)].filter(Boolean);
  const trap = (e, label = '') => {
    let frame = null;
    try { frame = window.frameElement; } catch { frame = null; }
    const names = [...tokens(e), e.getAttribute('autocomplete'), e.getAttribute('aria-label'),
      e.getAttribute('placeholder'), e.getAttribute('title'),
      frame && [frame.title, frame.name, frame.id, frame.getAttribute('aria-label')].join(' ')].filter(Boolean).join(' ');
    const phrases = [e.getAttribute('placeholder'), e.getAttribute('title'), e.getAttribute('aria-label'),
      idsText(e, 'aria-describedby'), idsText(e, 'aria-labelledby'), ...labelsOf(e).map(composedText), label]
      .filter(Boolean).join(' ').replace(/\s+/g, ' ');
    if (T1_NAME.test(names) || tokens(e).some(t => /^beecatcher$/i.test(t)) || T1_PHRASE.test(phrases)) return 'T1';
    if (!painted(e) && tokens(e).some(t => T2.some(pattern => pattern.test(t)))) return 'T2';
    return '';
  };
  // A host must itself pass T; family hosts are painted or show a painted control.
  const hostOk = h => !!h && !trap(h) && (painted(h) ||
    queryAll('button, [role=button], label, a', h).some(painted));
  const onlyFile = (h, e) => { const files = queryAll('input[type=file]', h); return files.length === 1 && files[0] === e; };
  const fileHost = e => {
    if (labelsOf(e).some(label => painted(label) && !trap(label))) return 'label';
    const group = closest(e, '[role=group][aria-labelledby]');
    if (group && onlyFile(group, e) && painted(group)) return 'group';
    const lever = closest(e, 'a[href="#"]');
    const family = [
      closest(e, '.file-upload'), closest(e, '[data-role=dropzone]'),
      closest(e, '.ashby-application-form-field-entry'),
      lever && queryAll('.filename', parentOf(lever) || lever).length ? lever : null,
      e.matches('[data-automation-id=file-upload-input-ref]') ? closest(e, '[data-automation-id^="formField-"]') : null,
      closest(e, '[class*="apply-flow-block--"]') &&
        closest(e, 'resume-upload-button, cover-letter-upload-button, attachment-upload-button'),
      e.getRootNode()?.host?.matches?.('spl-dropzone') ? e.getRootNode().host : null,
    ].find(h => h && onlyFile(h, e) && hostOk(h));
    if (family) return 'family';
    let node = parentOf(e);
    for (let depth = 0; node && depth < 4; depth++, node = parentOf(node)) {
      if (!queryAll('input[type=file]', node).includes(e)) continue;
      if (!onlyFile(node, e)) return '';
      if (painted(node) && queryAll('button, [role=button], label, a', node).some(control =>
          painted(control) && !trap(control) &&
          FILE_WORDS.test((control.innerText || composedText(control)) + ' ' + (control.getAttribute('aria-label') || ''))))
        return 'generic';
    }
    return '';
  };
  const host = e => {
    if (!e?.isConnected) return '';
    if (painted(e)) return 'self';
    if (e.matches('input[type=file]')) return fileHost(e);
    if (e.matches('input[type=checkbox], input[type=radio]')) {
      if (labelsOf(e).some(label => painted(label) && !trap(label))) return 'label';
      // A styled box shows the option's own text; submit buttons are never hosts.
      const box = parentOf(e);
      return box && painted(box) && !trap(box) && (box.innerText || composedText(box)).trim() &&
        !queryAll('button:not([type=button]), input[type=submit], input[type=image]', box).length ? 'box' : '';
    }
    if (!e.matches('input, textarea')) return '';   // ARIA controls and native selects: never substituted
    const owner = multiselectOf(e);
    if (owner && painted(owner) && queryAll('[data-automation-id=promptSearchButton]', owner).some(painted)) return 'prompt';
    const control = closest(e, '[class*=control]');
    if (e.matches('[role=combobox]') && !unpainted(e, true) && control && contains(control, e) &&
        painted(control) && chain(e).indexOf(control) <= 4) return 'control';
    return '';
  };
  const refusal = (e, label = '') => {
    const via = host(e), clause = trap(e, label);
    return {trap: clause, host: via, refused: clause || (via ? '' : 'H')};
  };
  return {painted, unpainted, trap, host, refusal};
})()"""


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


#: Oracle's split date fields share a row, so its filled class is not a per-part
#: receipt. Keep any observed click proof tied to the exact native field/owner.
_ORACLE_DATE_PART_JS = r"""
box => {
  if (!box?.isConnected ||
      !box.matches('input.cx-select-input[role=combobox][aria-haspopup=grid]')) return null;
  const part = box.id.split('-')[0], name = box.getAttribute('name');
  if (!['month', 'year'].includes(part) || !name || !box.id.startsWith(part + '-' + name + '-')) return null;
  const owner = closest(box, '.cx-select-container');
  const row = closest(box, '.datepicker-row.quick-apply-flow-datepicker-row.input-row.input-row--has-picker');
  const listId = box.getAttribute('aria-controls');
  if (!owner || !row || !contains(row, owner) || !listId ||
      queryAll('input.cx-select-input[role=combobox][aria-haspopup=grid]', owner).length !== 1) return null;
  return {owner, row, id: box.id, name, listId, root: box.getRootNode()};
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
  if (box.matches('input[data-uxi-widget-type=selectinput]') && !multiselect) return [];
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
    if (committed?.date) {
      const date = (""" + _ORACLE_DATE_PART_JS + r""")(box);
      if (!date || !fieldSafe(box) || value !== committed.value ||
          box.getAttribute('aria-expanded') !== 'false' || box.getAttribute('aria-invalid') === 'true' ||
          date.owner !== committed.date.owner || date.row !== committed.date.row ||
          date.id !== committed.date.id || date.name !== committed.date.name ||
          date.listId !== committed.date.listId || date.root !== committed.date.root) {
        globalThis.kittyCommittedCombos.delete(box);
        return [];
      }
      return [committed.text];
    }
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
    if (['checkbox', 'radio'].includes(el.getAttribute('role')) || pillOf(el)) return textOf(el) || clean(el.value);
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
  const noHidden = """ + _NO_HIDDEN_JS + r""";
  const rows = [];
  for (const el of queryAll(
      'input, textarea, select, [role=checkbox], [role=radio], [role=combobox], button[type=button][aria-haspopup=listbox], ' +
      'button.cx-select-pill-section')) {
    if (!fieldSafe(el)) continue;
    const tag = el.tagName.toLowerCase();
    const custom = customKind(el);
    if (!el.matches('input, textarea, select') && !custom) continue;
    const type = custom || (el.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search', 'password'].includes(type)) continue;
    const choice = type === 'radio' || type === 'checkbox';
    // Elements without any layout are not part of the shown form (closed sections,
    // unrevealed follow-ups). Laid-out ones go to the I-NoHidden′ check below.
    if (type === 'file') {
      // File inputs are usually hidden behind an "Attach" control: the host check
      // decides. A blank one in a region without layout (an inactive step) is not
      // part of the shown form; an attached one stays as upload evidence.
      if (!rendered(el) && !rendered(parentOf(el)) && !(el.files && el.files.length) && !retainedResume(el)) continue;
    } else if (choice) {
      if (el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true') continue;
      if (custom && !rendered(el)) continue;
      // An unlabelled hidden backing input does not make submit-button choices safe.
      if (!rendered(el) && !labelsOf(el).length &&
          queryAll('button:not([type=button]), input[type=submit], input[type=image]', parentOf(el)).length) continue;
      if (!rendered(parentOf(el))) continue;
    } else {
      // T3: a text-like aria-hidden / tabindex -1 input is never a field (Oracle's
      // honey-pot, react-select's required proxy); no text host can carry one.
      if (!rendered(el) || el.getAttribute('aria-hidden') === 'true' || el.tabIndex < 0) continue;
      if (el.disabled || el.readOnly || el.getAttribute('aria-disabled') === 'true') continue;
    }
    const key = keyOf(el);
    const question = questionEl(el);
    const uploadGroup = type === 'file' ? uploadGroupFor(el) : null;
    // Eightfold's post-signup "Profile Bootstrapping": one file input beside
    // its "Enter information" (manual) alternative is the resume upload.
    const eightfoldOnboarding = !!document.querySelector('[data-testid=onboarding-manual-information-button]') &&
      queryAll('input[type=file]').length === 1;
    const resumeRegion = type === 'file' && (eightfoldOnboarding ||
      closest(el, 'oc-resume-upload, [data-test="resume-upload"], [data-test="resume-upload-container"]'));
    const uploadLabel = uploadGroup ? ownLabel(uploadGroup) : '';
    const own = ownLabel(el);
    const group = choice ? (pillOf(el) ? closest(el, '.cx-select-pills-container') :
      closest(el, '[role=group], [role=radiogroup], fieldset')) : null;
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
    // Workday questionnaire list buttons say only "Select One Required"; the
    // question is the group's fieldset legend.
    const listLegend = tag === 'button' && el.matches('[aria-haspopup=listbox]') &&
      /^select one\b/i.test(own) ? closest(el, '[data-automation-id^="formField-"]')?.querySelector('fieldset > legend') : null;
    // Workday date sections (dateSectionMonth-input, ...) say only "Month"; the
    // question is the field's label. The section stays in the label: "... Month".
    const dateSection = tag === 'input' &&
      (el.getAttribute('data-automation-id') || '').match(/^dateSection(Day|Month|Year)-input$/);
    const dateLabel = dateSection ? closest(el, '[data-automation-id^="formField-"]')?.querySelector('label, legend') : null;
    const label = uploadLabel || (resumeRegion ? 'Resume' : '') || groupLabel || (listLegend ? textOf(listLegend) : '') ||
      (dateLabel && textOf(dateLabel) ? clean(textOf(dateLabel).replace(/[*✱]\s*$/, '') + ' ' + dateSection[1]) : '') ||
      (choice || type === 'file'
      ? (question ? textOf(question) : own)
      : (own || (question ? textOf(question) : '')));
    let blank = !el.value;
    if (choice) blank = custom ? !ariaChecked(el) : !el.checked;
    if (type === 'file') blank = !(el.files && el.files.length) && !retainedResume(el);
    if (kind === 'combobox') blank = comboValues(el).length === 0;
    const uncommitted = kind === 'combobox' && !!el.value && comboValues(el).length === 0 &&
      (!!multiselectOf(el) || el.matches('input.cx-select-input[aria-haspopup=grid]'));
    if (uncommitted) blank = false;
    if (unownedPrompt) blank = true;
    if (tag === 'select') blank = !el.value || el.selectedIndex < 0;
    const required = !!el.required || el.getAttribute('aria-required') === 'true' ||
      uploadGroup?.getAttribute('aria-required') === 'true' ||
      group?.getAttribute('aria-required') === 'true' || group?.hasAttribute('required') ||
      (el.getAttribute('aria-required') !== 'false' &&
       (looksRequired(ownLabelEl(el)) || looksRequired(question) || looksRequired(dateLabel) ||
        looksRequired(group && queryAll('legend', group)[0]) || looksRequired(listLegend) ||
        (!!listLegend && /\brequired$/i.test(el.getAttribute('aria-label') || ''))));
    // I-NoHidden′: a trap, or a blank hidden element without a painted host, is
    // never written. An optional one is skipped; a required one is reported
    // (refused). A filled hidden element is only evidence (an earlier upload).
    const verdict = noHidden.refusal(el, [label, own, question ? textOf(question) : ''].join(' '));
    const refused = verdict.trap || (blank && !verdict.host ? 'H' : '');
    if (refused && !required) continue;
    rows.push({
      key, kind, type, label, option: choice ? own : '',
      scope: keyOf(group || el.getRootNode()),
      name: el.getAttribute('name') || '', id: el.id || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      required, refused, host: verdict.host,
      options: tag === 'select'
        ? Array.from(el.options).filter(o => o.value !== '' && !o.disabled && !o.parentElement.disabled).map(o => clean(o.textContent))
        : [],
      blank,
      invalid: unownedPrompt || uncommitted || el.getAttribute('aria-invalid') === 'true' ||
               (!!el.willValidate && !el.validity.valid),
      autofill: !!closest(el, '[class*=autofill], oc-apply-with-resume, [data-test="apply-with-resume-container"], [class*="apply-flow-profile-import"]'),
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


class ChoiceSelectionError(BidiError):
    """A known choice widget failed terminally; do not retry with typed queries."""


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
        refused=row.get("refused") or "", host=row.get("host") or "",
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
        # One refused option refuses the question: kitty never writes part of it.
        refused=next((row["refused"] for row in rows if row.get("refused")), ""),
        host=",".join(dict.fromkeys(row.get("host") or "" for row in rows)),
    )


# ------------------------------------------------------------------ writing


#: Shared start of the writer scripts: the element the reader remembered under `key`.
_ELEMENT = (
    "(key) => { const e = globalThis.kittyByKey?.get(key); return ("
    + _FIELD_SAFE_JS + ")(e) ? e : null; }"
)


class HiddenTargetRefused(UnsafeAction):
    """I-NoHidden′ at write time: a trap (T1/T2/T3) or no painted host (H)."""

    def __init__(self, clause: str) -> None:
        self.clause = clause
        super().__init__(f"hidden_target_refused: {clause}")


#: Every writer's first check, on the element itself at write time (a re-render
#: between read and write is covered): its kind, and the I-NoHidden′ verdict.
_GUARD_JS = (
    "(key) => {" + _DOM_HELPERS_JS + "const e = (" + _ELEMENT + ")(key); if (!e) return null;"
    " return [e.tagName.toLowerCase(), (e.type || '').toLowerCase(), (" + _CUSTOM_CONTROL_JS + ")(e),"
    " (" + _NO_HIDDEN_JS + ").refusal(e).refused]; }"
)


def _guard(frame: Frame, key: str, *, custom: tuple[str, ...] = ()) -> str:
    """Refuse arbitrary actions and hidden targets; custom kinds need a caller allowlist."""
    found = frame.call(_GUARD_JS, key)
    if found is None:
        raise UnsafeAction("the field is detached, disabled, read-only or inside an action")
    tag, kind, aria_kind, refused = found
    if refused:
        raise HiddenTargetRefused(refused)
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


# Only discard text owned by this attempt. Native Workday navigation never
# writes a query, so its failures must preserve the existing/manual text.
_DISCARD_QUERY_JS = r"""
(key, preserve) => {
  if (!preserve) return (""" + _FILL_JS + r""")(key, '');
}
"""


def fill_text(frame: Frame, key: str, value: str) -> bool:
    """Write without Enter and verify the connected field after its blur handlers."""
    _guard(frame, key)
    frame.call(_FILL_JS, key, value)
    return bool(frame.call(r"""(key, expected) => {""" + _DOM_HELPERS_JS + r"""
      const el = globalThis.kittyByKey.get(key);
      if (!fieldSafe(el) || !el.matches('input:not([type=password]),textarea')) return false;
      const actual = el.value;
      if (actual === expected.replace(/\r\n?/g, String.fromCharCode(10))) return true;
      if (el.type === 'tel' || el.inputMode === 'tel') {
        const digits = value => value.replace(/[^0-9]/g, '');
        return !!digits(expected) && digits(actual) === digits(expected);
      }
      return el.type === 'number' && expected.trim() !== '' &&
        Number.isSafeInteger(Number(expected)) && el.valueAsNumber === Number(expected);
    }""", key, value))


def can_recommit_text(frame: Frame, key: str, value: str) -> bool:
    """Observe an exact saved invalid value without changing a manual value."""
    return frame.call(r"""(key, value) => {""" + _DOM_HELPERS_JS + r"""
      const el = globalThis.kittyByKey.get(key);
      return fieldSafe(el) && el.matches('input:not([type=password]), textarea') &&
        el.value === value && !el.readOnly &&
        (el.getAttribute('aria-invalid') === 'true' || !el.checkValidity());
    }""", key, value) is True


def recommit_text(frame: Frame, key: str, value: str) -> bool:
    """Re-enter a value the field already shows verbatim so the framework records it.

    Some ATSs (Eightfold) restore saved DOM values that their own state still
    treats as blank. Only an exact current match is retyped; any other existing
    value is never touched.
    """
    _guard(frame, key)
    if frame.call(r"""(key, value) => {""" + _DOM_HELPERS_JS + r"""
      const el = globalThis.kittyByKey.get(key);
      return fieldSafe(el) && el.matches('input:not([type=password]), textarea') && el.value === value;
    }""", key, value) is not True:
        return False
    # Clear first: frameworks ignore an input event whose value matches their tracker.
    frame.call(_FILL_JS, key, "")
    return fill_text(frame, key, value)


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


# ------------------------------------------------------------ phone and dates


#: intl-tel-input (v17 `.iti__selected-flag[role=combobox]`, v18+ `button.iti__selected-country`):
#: the country list carries `data-country-code` / `data-dial-code` on each item.
#: `want`: 'state' (what the widget shows), 'open' (its country control) or
#: 'option' (the shown, unobstructed list item of `country`).
_ITI_JS = r"""
(key, want, country = '') => {""" + _DOM_HELPERS_JS + r"""
  const box = globalThis.kittyByKey.get(key);
  const none = want === 'state' ? {iti: false} : null;
  const wrapper = box && fieldSafe(box) ? closest(box, '.iti') : null;
  if (!wrapper) return none;
  const inputs = queryAll('input', wrapper).filter(e => !e.matches('[type=hidden], [type=search], .iti__search-input'));
  const controls = queryAll('.iti__selected-flag, .iti__selected-country', wrapper);
  if (inputs.length !== 1 || inputs[0] !== box || controls.length !== 1) return none;
  const control = controls[0];
  if (!control.matches('[role=combobox], button[type=button]')) return none;
  const list = reference(control, control.getAttribute('aria-controls') || control.getAttribute('aria-owns')) ||
    queryAll('.iti__country-list', wrapper)[0] || null;
  const items = list ? queryAll('li.iti__country[data-country-code][data-dial-code]', list) : [];
  const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const flag = queryAll('.iti__flag', control)[0];
  const code = flag ? (Array.from(flag.classList).find(name => /^iti__[a-z]{2}$/.test(name)) || '').slice(5) : '';
  const dialText = queryAll('.iti__selected-dial-code', control).map(e => e.textContent.trim()).find(Boolean) || '';
  if (want === 'state') {
    const countries = [], seen = new Set();
    for (const item of items.sort((a, b) => a.classList.contains('iti__preferred') - b.classList.contains('iti__preferred'))) {
      const iso = item.getAttribute('data-country-code');
      if (seen.has(iso)) continue;
      seen.add(iso);
      countries.push([iso, item.getAttribute('data-dial-code'),
        (queryAll('.iti__country-name', item)[0]?.textContent || '').trim()]);
    }
    const current = countries.find(entry => entry[0] === code);
    return {iti: true, country: code, dial: dialText.replace(/^\+/, '') || (current ? current[1] : ''),
      separate: wrapper.classList.contains('iti--separate-dial-code') ||
        queryAll('.iti__selected-dial-code', control).some(e => shown(e) && e.textContent.trim()),
      open: control.getAttribute('aria-expanded') === 'true' && !!list && shown(list), countries};
  }
  const reachable = e => {
    e.scrollIntoView({block: 'nearest', inline: 'nearest', behavior: 'instant'});
    const r = e.getBoundingClientRect();
    const hit = r.width && r.height ? hitAt(r.left + r.width / 2, r.top + r.height / 2) : null;
    return !!hit && contains(e, hit) && shown(e);
  };
  if (want === 'open') return control.getAttribute('aria-expanded') !== 'true' && reachable(control) ? control : null;
  if (want === 'option') {
    const matches = items.filter(item => item.getAttribute('data-country-code') === country && shown(item) &&
      !queryAll('button, input, a[href], [role=button], [role=link]', item).length);
    const item = matches.find(e => !e.classList.contains('iti__preferred')) || matches[0];
    return item && reachable(item) ? item : null;
  }
  return null;
}
"""


def _iti_country(state: dict[str, Any], code: str, region: str | None) -> str | None:
    """The widget country for a dial code: the shown one, the saved region's, or the only one."""
    offered = [entry for entry in state["countries"] if entry[1] == code]
    if state["country"] in {entry[0] for entry in offered}:
        return state["country"]
    wanted = norm_question(region or "")
    if wanted in {"us", "usa", "united states of america"}:
        wanted = "united states"
    named = [entry[0] for entry in offered if norm_question(entry[2]) == wanted or entry[0] == wanted]
    if len(named) == 1:
        return named[0]
    return offered[0][0] if len(offered) == 1 else None


def fill_phone(frame: Frame, key: str, phone: str, region: str | None = None) -> bool:
    """Write a phone number; through intl-tel-input, its country and dial code first.

    The country comes from the number's own calling code (never from residence
    alone): the shown country when it has that code, the saved region's country
    among those sharing it (+1: United States or Canada), or the only one. A
    widget that shows the dial code separately gets the national number.
    """
    from kitty.core.apply.fields import _phone_country_code

    _guard(frame, key)
    state = frame.call(_ITI_JS, key, "state")
    if not state["iti"]:
        return fill_text(frame, key, phone)
    codes = _phone_country_code(phone, region)
    if not codes:
        return False                    # no calling code: the widget's country would be a guess
    code = codes[1]
    country = _iti_country(state, code, region)
    if country is None:
        return False
    if country != state["country"]:
        opener = frame.node(_ITI_JS, key, "open")
        if opener is None and not state["open"]:
            return False
        if opener is not None:
            frame.click(opener, intent=FIELD)
        deadline = time.monotonic() + 3.0
        item = None
        while item is None and time.monotonic() < deadline:
            item = frame.node(_ITI_JS, key, "option", country)
            if item is None:
                time.sleep(0.15)
        if item is None:
            return False
        frame.click(item, intent=FIELD)
        state = frame.call(_ITI_JS, key, "state")
        if not state["iti"] or state["country"] != country or state["dial"] != code:
            return False
    national = (re.sub(r"^\s*\+\s*" + code + r"[\s.()\-]*", "", phone)
                if phone.lstrip().startswith("+") else phone.strip())
    return fill_text(frame, key, national if state["separate"] else f"+{code} {national}")


#: What a date-like text box expects: its type, placeholder, label hint and,
#: on Workday, which date section (dateSectionMonth-input, ...) it is.
_DATE_SHAPE_JS = r"""
(key) => {""" + _DOM_HELPERS_JS + r"""
  const e = globalThis.kittyByKey.get(key);
  if (!fieldSafe(e)) return null;
  const labelled = (e.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
    .map(id => reference(e, id)).filter(Boolean);
  return {type: (e.getAttribute('type') || '').toLowerCase(), placeholder: e.getAttribute('placeholder') || '',
    automation: e.getAttribute('data-automation-id') || '',
    label: [...labelsOf(e), ...labelled].map(composedText).concat([e.getAttribute('aria-label') || ''])
      .join(' ').replace(/\s+/g, ' ')};
}
"""

_DATE_PATTERN = re.compile(r"\s*([A-Za-z]{2,4})([./\- ])([A-Za-z]{2,4})(?:\2([A-Za-z]{2,4}))?\s*")


def _from_pattern(hint: str, year: str, month: str) -> str | None | bool:
    """A month in a field's own pattern ('MM/YYYY', 'MM.JJJJ', 'AAAA-MM'); None if it
    needs a day (never invented: 'DD/MM/YYYY', 'TT.MM.JJJJ'); False if not a pattern."""
    found = _DATE_PATTERN.fullmatch(hint)
    if not found:
        return False
    out = []
    for part in (part.upper() for part in (found[1], found[3], found[4]) if part):
        if part in ("YYYY", "JJJJ", "AAAA"):
            out.append(year)
        elif part == "MM":
            out.append(month)
        elif part in ("YY", "AA"):
            out.append(year[2:])
        elif part in ("DD", "TT", "JJ"):
            return None
        else:
            return False
    return found[2].join(out) if month in out or year in out else False


def _date_value(shape: dict[str, Any], iso_month: str) -> tuple[bool, str | None]:
    """(known shape, value): the saved YYYY-MM as this field writes it; None refuses."""
    year, _, month = iso_month.partition("-")
    section = {"dateSectionMonth-input": month, "dateSectionYear-input": year, "dateSectionDay-input": None}
    if shape["automation"] in section:
        return True, section[shape["automation"]]
    if shape["type"] == "month":
        return True, iso_month
    if shape["type"] in ("date", "datetime-local", "week"):
        return True, None
    for hint in (shape["placeholder"], *re.findall(r"\(([^()]*)\)", shape["label"])):
        value = _from_pattern(hint, year, month)
        if value is not False:
            return True, value
    return False, None


def fill_date(frame: Frame, key: str, iso_month: str) -> bool | None:
    """Write a saved month (YYYY-MM) in the field's own locale format.

    The format comes from the field itself: its type, placeholder, a label hint
    such as '(MM/YYYY)' or Workday's date section (locale-independent: sections
    are named, not ordered). None: no known shape (the caller's own spelling
    applies); False: the field needs a day, which is never invented.
    """
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", iso_month or ""):
        return None
    _guard(frame, key)
    shape = frame.call(_DATE_SHAPE_JS, key)
    if shape is None:
        return False
    known, value = _date_value(shape, iso_month)
    if not known:
        return None
    return value is not None and fill_text(frame, key, value)


#: What each remembered element shows now: [text value, committed choices, checked].
#: Read in kitty's sandbox and kept in memory only (never logged or stored).
_VALUES_JS = r"""
(keys) => {""" + _DOM_HELPERS_JS + r"""
  const comboValues = (""" + _COMBO_VALUES_JS + r""");
  const out = {};
  for (const key of keys) {
    const e = globalThis.kittyByKey.get(key);
    if (!e?.isConnected || e.matches('input[type=password]')) { out[key] = null; continue; }
    const native = e.matches('input[type=checkbox], input[type=radio]');
    out[key] = [e.matches('select') ? Array.from(e.selectedOptions).map(o => o.textContent.replace(/\s+/g, ' ').trim()).join('\n')
        : e.matches('input, textarea') && !native && e.type !== 'file' ? e.value : '',
      comboValues(e).join('\n'), native ? e.checked : ariaChecked(e)];
  }
  return out;
}
"""


def field_values(frames: list[Frame], fields: list[FormField]) -> dict[tuple[int, str], tuple]:
    """The value each field shows, keyed by (frame, key); a lost frame is skipped."""
    values: dict[tuple[int, str], tuple] = {}
    for index, frame in enumerate(frames):
        own = [f for f in fields if f.frame == index and f.kind != "file"]
        try:
            shown = frame.call(_VALUES_JS, [k for f in own for k in (f.option_keys or (f.key,))])
        except BidiError:
            continue
        for f in own:
            values[(index, f.key)] = tuple(
                tuple(shown[k]) if shown.get(k) is not None else None for k in (f.option_keys or (f.key,)))
    return values


#: Scroll an element into view and say whether a click at its centre would land
#: on it (or on its own <label>) rather than on something drawn over it.
_REACHABLE_JS = r"""
(key) => {""" + _DOM_HELPERS_JS + r"""
  const el = globalThis.kittyByKey.get(key);
  if (!fieldSafe(el)) return false;
  el.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return false;
  const hit = hitAt(r.left + r.width / 2, r.top + r.height / 2);
  if (!hit || !fieldSafe(el)) return false;
  const action = closest(hit, 'button, a[href], input[type=submit], input[type=image], [role=button], [role=link]');
  return (!action || action === el) &&
    (contains(el, hit) || labelsOf(el).some(l => contains(l, hit)));
}
"""

_CHECKED_JS = r"""(key) => {""" + _DOM_HELPERS_JS + r"""
  const el = globalThis.kittyByKey.get(key);
  return el.matches('input[type=checkbox], input[type=radio]') ? !!el.checked : ariaChecked(el);
}"""

#: Prefer a reachable native label over a synthetic click on its hidden input.
#: Do not click a terms link, another action, or an overlay inside that label.
_CHOICE_TARGET_JS = r"""
(key) => {""" + _DOM_HELPERS_JS + r"""
  const el = globalThis.kittyByKey.get(key);
  if (!fieldSafe(el)) return null;
  for (const target of [el, ...labelsOf(el)]) {
    target.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
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
        frame.click(target, intent=FIELD)
    else:
        # Preserve the exact native-input fallback for CSS-hidden choices; _guard
        # proved their painted host (I-NoHidden′). ARIA controls must be
        # reachable: never bypass an overlay with their click().
        frame.call("""(key) => {""" + _DOM_HELPERS_JS + r"""
          const kittyActivate = (""" + ACTIVATE_JS + r""");
          const el = globalThis.kittyByKey.get(key);
          if (fieldSafe(el) && el.matches('input[type=checkbox], input[type=radio]') &&
              !labelsOf(el).some(label => closest(label, 'button, a[href], [role=button], [role=link]')))
            kittyActivate(el, 'field', () => el.click());
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
  if (want === 'commit') {
    const attempts = globalThis.kittyOracleDateClicks;
    const attempt = attempts?.get(box);
    attempts?.delete(box);
    if (attempt?.observed) {
      const date = (""" + _ORACLE_DATE_PART_JS + r""")(box), before = attempt.date;
      const value = (box.value || '').replace(/\s+/g, ' ').trim();
      // Only the just-completed native click can mint this receipt. Closing a
      // typed query or clicking a no-op option is not a value transition.
      if (date && fieldSafe(box) && date.owner === before.owner && date.row === before.row &&
          date.id === before.id && date.name === before.name && date.listId === before.listId &&
          date.root === before.root && attempt.before !== value && value === attempt.text &&
          box.getAttribute('aria-expanded') === 'false' && box.getAttribute('aria-invalid') !== 'true')
        (globalThis.kittyCommittedCombos ??= new WeakMap()).set(box, {value, text: attempt.text, date});
    }
    return (""" + _COMBO_VALUES_JS + r""")(box);
  }
  if (want === 'selected') return (""" + _COMBO_VALUES_JS + r""")(box);
  if (!fieldSafe(box)) return want === 'list' ? {texts: [], loading: false} : null;
  const listId = box.getAttribute('aria-controls') || box.getAttribute('aria-owns');
  const multiselect = multiselectOf(box);
  if (box.matches('input[data-uxi-widget-type=selectinput]') && !multiselect)
    return want === 'list' ? {texts: [], loading: false} : null;
  // Workday portals carry the exact input owner's ID, not an aria-controls ID.
  // Never inspect another question's portal or pick one of duplicate owners.
  const prompts = multiselect ? queryAll(
    '[data-automation-id=responsiveMonikerPrompt][data-automation-type=singleSelectPrompt][data-associated-widget]',
    box.getRootNode()
  ).filter(e => e.getAttribute('data-associated-widget') === multiselect.id) : [];
  const scope = multiselect ? (prompts.length === 1 && fieldSafe(prompts[0]) ? prompts[0] : null)
    : listId ? reference(box, listId) : parentOf(box);
  const oracleGrid = scope?.getAttribute('role') === 'grid' &&
    box.matches('input.cx-select-input[role=combobox][aria-haspopup=grid]');
  const selector = listId ? (oracleGrid ? '[role=gridcell]' : '[role=option]') : '[role=option], .__CLASS__';
  const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  // Workday renders single-choice leaves with a radio inside the option.
  // Allow only that control in this input's unique portal, never arbitrary inputs.
  const workdayOptionRadio = (option, control) => {
    const leaf = closest(control, '[data-automation-id=promptLeafNode][data-uxi-widget-type=multiselectlistitem]');
    return !!multiselect && prompts.length === 1 && scope === prompts[0] &&
      control.matches('input[type=radio][data-automation-id=radioBtn]') && fieldSafe(control) &&
      closest(control, '[role=option]') === option &&
      leaf?.getAttribute('data-uxi-multiselect-id') === multiselect.id &&
      leaf.getAttribute('data-uxi-multiselectlistitem-hassidecharm') === 'false' &&
      queryAll('input', option).length === 1;
  };
  const hasActions = option => queryAll('button, input, textarea, select, a[href], [role=button], [role=link], [role=checkbox], [role=radio], [role=combobox]', option)
    .some(control => !(control === option && listButton(option)) && !workdayOptionRadio(option, control));
  const workdayLeaf = option => {
    if (!multiselect) return true;
    const items = queryAll('[data-uxi-widget-type=multiselectlistitem]', option);
    if (!items.length) return true; // Older plain role=option prompts.
    return items.length === 1 && items[0].matches('[data-automation-id=promptLeafNode]') &&
      items[0].getAttribute('data-uxi-multiselect-id') === multiselect.id &&
      items[0].getAttribute('data-uxi-multiselectlistitem-hassidecharm') === 'false' &&
      (!items[0].hasAttribute('data-uxi-multiselectlistitem-type') ||
        items[0].getAttribute('data-uxi-multiselectlistitem-type') === '1');
  };
  // Eightfold renders options as <button type=button role=option> inside the
  // listbox named by aria-controls; such a button only chooses that option.
  const listButton = e => !!listId && scope?.getAttribute('role') === 'listbox' &&
    e.matches('button[type=button][role=option]') && !closest(e, '[aria-disabled=true], [inert]');
  const options = scope ? queryAll(selector, scope).filter(
    e => shown(e) && (fieldSafe(e) || listButton(e)) && !closest(e, '[data-automation-id=selectedItemList]') &&
      e.getAttribute('aria-disabled') !== 'true' && !hasActions(e) && workdayLeaf(e)
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
    const date = oracleGrid && (""" + _ORACLE_DATE_PART_JS + r""")(box);
    if (date && box.getAttribute('aria-expanded') === 'true') {
      const receipts = globalThis.kittyCommittedCombos ??= new WeakMap();
      const watched = globalThis.kittyOracleDateInputs ??= new WeakSet();
      if (!watched.has(box)) {
        watched.add(box);
        for (const type of ['input', 'change'])
          box.addEventListener(type, () => receipts.delete(box), {capture: true});
      }
      const attempts = globalThis.kittyOracleDateClicks ??= new WeakMap();
      const attempt = {date, text: expected, observed: false};
      attempts.set(box, attempt);
      option.addEventListener('click', event => {
        if (attempts.get(box) !== attempt || !event.isTrusted ||
            !fieldSafe(box) || !fieldSafe(option) || hasActions(option) ||
            reference(box, date.listId) !== scope || !contains(scope, option) ||
            box.getAttribute('aria-expanded') !== 'true' || title(option) !== expected) return;
        attempt.before = box.value.replace(/\s+/g, ' ').trim();
        attempt.observed = true;
      }, {capture: true, once: true});
    }
  }
  else if (targets.get(key) !== option) return null;
  if (want === 'node') return option;
  if (want === 'click') {
    const role = option.getAttribute('role'), tag = option.tagName.toLowerCase();
    if ((role !== 'option' && !(oracleGrid && role === 'gridcell') && !option.classList.contains('__CLASS__')) ||
        ['button', 'input', 'a'].includes(tag)) return false;
    // the events a mouse click sends: Lever picks a place on mousedown, react-select on click
    const kittyActivate = (""" + ACTIVATE_JS + r""");
    for (const type of ['mousedown', 'mouseup', 'click'])
      if (fieldSafe(box) && fieldSafe(option) && !hasActions(option))
        kittyActivate(option, 'field', () => option.dispatchEvent(new MouseEvent(type, {bubbles: true, composed: true, cancelable: true, button: 0})));
    return true;
  }
  option.scrollIntoView({block: 'nearest', behavior: 'instant'});
  const r = option.getBoundingClientRect();
  const hit = r.width && r.height
    ? hitAt(r.left + r.width / 2, r.top + r.height / 2) : null;
  const actionHit = closest(hit, 'button, input, a[href], [role=button], [role=link]');
  // Eightfold renders each option as <button type=button role=option> inside the
  // listbox the box controls (aria-controls); such a button only chooses.
  const controlled = box.getAttribute('aria-controls') && box.getRootNode().getElementById?.(box.getAttribute('aria-controls'));
  // type=button never submits its form, so form ownership is harmless here.
  const ownedButton = option.matches('button[type=button][role=option]') &&
    !!controlled && controlled.getAttribute('role') === 'listbox' && controlled.contains(option) &&
    !option.querySelector('button, input, a[href], [role=button], [role=link]');
  return {
    ownedButton,
    role: oracleGrid && option.getAttribute('role') === 'gridcell' ? 'option' : option.getAttribute('role'), tag: option.tagName.toLowerCase(),
    css: String(option.className), text: option.innerText || composedText(option),
    pointerOnly: oracleGrid || box.matches('button[type=button][aria-haspopup=listbox], input[data-uxi-widget-type=selectinput]'),
    reachable: !!hit && contains(option, hit) &&
      (!actionHit || workdayOptionRadio(option, actionHit) || (ownedButton && actionHit === option)),
  };
}
""").replace("__CLASS__", _SUGGESTION_CLASS).replace("__LOADING__", _LOADING_SELECTOR)


# Workday's native list icon and hierarchical prompt have explicit owner IDs.
# Only these proven navigation controls may be clicked; category titles never
# become answer candidates. All clicks still use real, unobstructed pointers.
_WORKDAY_MENU_JS = r"""
(key, want = 'state', expected = '') => {""" + _DOM_HELPERS_JS + r"""
  const box = globalThis.kittyByKey.get(key);
  if (!box?.matches('input[data-uxi-widget-type=selectinput][data-uxi-multiselect-id]'))
    return want === 'state' ? {mode: 'other'} : null;
  const owner = multiselectOf(box);
  const blocked = () => want === 'state' ? {mode: 'blocked'} : null;
  if (!owner || !fieldSafe(box) || !fieldSafe(owner)) return blocked();
  const icons = queryAll('[data-automation-id=promptSearchButton][data-uxi-widget-type=selectinputicon][data-uxi-selectinputicon-type=promptSearchButton][data-uxi-multiselect-id]', box.getRootNode())
    .filter(e => e.getAttribute('data-uxi-multiselect-id') === owner.id);
  // Older prompt variants without this icon retain the existing input path.
  if (!icons.length) return want === 'state'
    ? {mode: queryAll('[data-automation-id=promptSearchButton]', owner).length ? 'blocked' : 'other'} : null;
  const actions = 'button, input, textarea, select, a[href], [role=button], [role=link], [role=checkbox], [role=radio], [role=combobox]';
  const safe = e => !!e && fieldSafe(e) && !e.matches(actions) &&
    !queryAll(actions, e).length;
  if (icons.length !== 1 || !icons[0].matches('span') ||
      !contains(owner, icons[0]) || !safe(icons[0])) return blocked();
  if ((""" + _COMBO_VALUES_JS + r""")(box).length) return blocked();
  const shown = e => !!e?.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const reachable = e => {
    if (!safe(e) || !shown(e)) return false;
    e.scrollIntoView({block: 'nearest', inline: 'nearest', behavior: 'instant'});
    const r = e.getBoundingClientRect(), hit = hitAt(r.left + r.width / 2, r.top + r.height / 2);
    return !!hit && contains(e, hit) && !closest(hit, actions) && safe(e) && fieldSafe(box);
  };
  const prompts = queryAll('[data-automation-id=responsiveMonikerPrompt][data-automation-type=singleSelectPrompt][data-associated-widget]', box.getRootNode())
    .filter(e => e.getAttribute('data-associated-widget') === owner.id);
  if (prompts.length > 1 || (prompts.length && !fieldSafe(prompts[0]))) return blocked();
  const prompt = prompts.length && shown(prompts[0]) ? prompts[0] : null;
  if (want === 'open') return !prompt && reachable(icons[0]) ? icons[0] : null;
  const title = e => (e.innerText || composedText(e)).replace(/\s+/g, ' ').trim();
  // Newer Workday tenants omit data-association-widget on category rows; the
  // single owned leaf node (multiselect-id === owner.id) still binds the row.
  const categories = prompt ? queryAll('[role=option][data-automation-id=menuItem]', prompt)
    .filter(e => {
      const leaves = queryAll('[data-automation-id=promptLeafNode][data-uxi-widget-type=multiselectlistitem]', e);
      const association = e.getAttribute('data-association-widget');
      return (association === null || association === owner.id) && safe(e) && shown(e) &&
        leaves.length === 1 && leaves[0].getAttribute('data-uxi-multiselect-id') === owner.id &&
        leaves[0].getAttribute('data-uxi-multiselectlistitem-type') === '2' &&
        leaves[0].getAttribute('data-uxi-multiselectlistitem-hassidecharm') === 'true';
    }) : [];
  const headers = prompt ? queryAll('[data-uxi-widget-type=multiselectlistheader][data-uxi-multiselectlistheader-clickable=true][data-uxi-multiselect-id]', prompt)
    .filter(e => e.getAttribute('data-uxi-multiselect-id') === owner.id && safe(e) && shown(e) &&
      !closest(e, '[role=option]')) : [];
  const backs = headers.length === 1 ? queryAll('span[data-automation-id=backButton]', headers[0]) : [];
  const back = backs.length === 1 && safe(backs[0]) ? backs[0] : null;
  if (want === 'category') {
    const matches = categories.filter(e => title(e) === expected);
    return matches.length === 1 && reachable(matches[0]) ? matches[0] : null;
  }
  if (want === 'back') return back && reachable(back) ? back : null;
  const suggestions = (""" + _SUGGESTIONS_JS + r""")(key, 0, 'list');
  const rows = prompt ? queryAll('[role=option]', prompt) : [];
  const allHeaders = prompt ? queryAll('[data-uxi-widget-type=multiselectlistheader]', prompt) : [];
  // A filtered-out row/header is an unexplored branch, not evidence that the
  // remaining visible leaf is unique. Disabled, foreign, hidden and malformed
  // branches must all prevent committing a hierarchy-wide answer.
  const incomplete = rows.length !== categories.length + suggestions.texts.length ||
    allHeaders.length !== headers.length || (headers.length > 0 && !back);
  const error = prompt && queryAll('[role=alert]', prompt).some(e => shown(e) && title(e));
  const names = categories.map(title);
  const header = headers.length === 1 ? title(headers[0]) : '';
  return {mode: 'native', open: !!prompt, categories: names, back: !!back,
    texts: suggestions.texts, loading: suggestions.loading || prompt?.getAttribute('aria-busy') === 'true',
    error: !!error, incomplete, signature: JSON.stringify([header, names, suggestions.texts])};
}
"""


def _wait_workday_menu(frame: Frame, key: str, previous: str | None = None) -> dict[str, Any] | None:
    """Wait for an owned root/child menu, never turn an error into a retry."""
    start = changed = time.monotonic()
    last = None
    while time.monotonic() - start < 8.0:
        state = frame.call(_WORKDAY_MENU_JS, key)
        if state["mode"] != "native" or state["error"]:
            return None
        signature = state["signature"]
        if not state["open"] or state["loading"] or signature == previous:
            last = None
            changed = time.monotonic()
        elif state["incomplete"]:
            return None
        elif signature != last:
            last, changed = signature, time.monotonic()
        elif state["texts"] or state["categories"] or time.monotonic() - changed >= 2.0:
            return state
        time.sleep(0.2)
    return None


def _pick_workday_list(
    frame: Frame, key: str, candidates: tuple[str, ...], *, location: bool,
    graduation: str | None, pick_source: bool = False,
) -> bool | None:
    """Use the native list, raising terminal failures; None means another variant."""
    state = frame.call(_WORKDAY_MENU_JS, key)
    if state["mode"] == "other":
        return None
    if state["mode"] != "native":
        raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete")
    try:
        return _choose_workday_leaf(frame, key, candidates, state, location=location,
                                    graduation=graduation, pick_source=pick_source)
    except ChoiceSelectionError:
        raise
    except (BidiError, UnsafeAction) as exc:
        raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete") from exc


#: A saved answer may name its place in a hierarchical list: "Job Board > LinkedIn".
_PATH_SEPARATOR = re.compile(r"\s+>\s+")


def saved_path(candidate: str) -> tuple[str, ...] | None:
    """The categories and leaf of a saved 'A > B > Leaf' answer, else None."""
    parts = tuple(" ".join(part.split()) for part in _PATH_SEPARATOR.split(candidate.strip()))
    return parts if len(parts) >= 2 and all(parts) else None


def _same_place(path: tuple[str, ...], saved: tuple[str, ...]) -> bool:
    return len(path) == len(saved) and all(
        norm_question(a) == norm_question(b) for a, b in zip(path, saved))


_COMPANY_SITE_ANSWER = re.compile(r"company (?:careers? )?(?:web ?site|site|page)")
_BRANDED_COMPANY_SITE = re.compile(
    r"(?:[\w&.\-]+ ){0,3}(?:corporate|company)(?: careers?)? (?:web ?site|site|page)", re.I)


def _menu_control(frame: Frame, key: str, want: str, expected: str = "") -> str | None:
    """Resolve an owned menu control, waiting out Workday's row slide animation.

    Rows are hit-tested; during the back/forward transition another row covers
    the target. Only resolution is retried, never a click.
    """
    deadline = time.monotonic() + 2.0
    while True:
        target = frame.node(_WORKDAY_MENU_JS, key, want, expected)
        if target is not None or time.monotonic() >= deadline:
            return target
        time.sleep(0.15)


def _walk_saved_path(
    frame: Frame, key: str, root: dict[str, Any], path: tuple[str, ...], *,
    location: bool, graduation: str | None,
) -> bool:
    """Follow a saved 'Category > ... > Leaf' from the root and commit its leaf.

    The owner named the place, so a leaf repeated in other categories is not
    ambiguous here. Each category must be offered exactly once; the leaf must
    be unique in its own menu. A miss returns to the root through owned back
    controls (False); an inconsistent menu or an uncommitted click raises.
    """
    current, depth = root, 0
    for category in path[:-1]:
        named = [name for name in current["categories"] if norm_question(name) == norm_question(category)]
        if len(named) != 1:
            break
        target = _menu_control(frame, key, "category", named[0])
        if target is None:
            raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete")
        frame.click(target, intent=FIELD)
        child = _wait_workday_menu(frame, key, current["signature"])
        if child is None or not child["back"]:
            raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete")
        current, depth = child, depth + 1
    else:
        options = [_option_title(text) for text in current["texts"]]
        index = match_option((path[-1],), options, location=location, prefix=False, graduation=graduation)
        if index is not None:
            try:
                committed = _click_match(frame, key, options, (options[index],), location=location,
                                         prefix=False, graduation=graduation, preserve_query=True)
            except (BidiError, UnsafeAction) as exc:
                raise ChoiceSelectionError("Workday choice selection was not committed") from exc
            if not committed:
                raise ChoiceSelectionError("Workday choice selection was not committed")
            return True
    for _ in range(depth):
        back = _menu_control(frame, key, "back")
        if back is None:
            raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete")
        frame.click(back, intent=FIELD)
        current = _wait_workday_menu(frame, key, current["signature"])
        if current is None:
            raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete")
    if current["signature"] != root["signature"]:
        raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete")
    return False


def _choose_workday_leaf(
    frame: Frame, key: str, candidates: tuple[str, ...], state: dict[str, Any], *,
    location: bool, graduation: str | None, pick_source: bool = False,
) -> bool:
    """Commit the saved choice of an owned (possibly hierarchical) Workday list.

    A saved path ('Job Board > LinkedIn') is walked directly. Otherwise the
    entire owned hierarchy is discovered before committing a saved leaf; a leaf
    offered in several categories is resolved by a saved path. On a source
    question (`pick_source`) a saved leaf offered in several places is still the
    saved source (the first place is taken, not nothing), and when no saved
    candidate is offered a plain offered source is picked, as on flat lists.
    """
    if not state["open"]:
        opener = frame.node(_WORKDAY_MENU_JS, key, "open")
        if opener is None:
            raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete")
        frame.click(opener, intent=FIELD)
    state = _wait_workday_menu(frame, key)
    # An existing child, even one containing a matching leaf, cannot establish
    # uniqueness across its siblings. Reach the root through owned back controls
    # before discovering any answer; never reset the query to navigate.
    for _ in range(8):
        if state is None:
            raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete")
        if not state["back"]:
            break
        back = _menu_control(frame, key, "back")
        if back is None:
            raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete")
        frame.click(back, intent=FIELD)
        state = _wait_workday_menu(frame, key, state["signature"])
    if state is None or state["back"]:
        raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete")
    paths = [path for candidate in candidates if (path := saved_path(candidate))]
    for path in paths:
        if _walk_saved_path(frame, key, state, path, location=location, graduation=graduation):
            return True
    leaf_candidates = tuple(dict.fromkeys(
        [candidate for candidate in candidates if saved_path(candidate) is None] + [path[-1] for path in paths]))
    remaining = 64
    menus: dict[tuple[str, ...], dict[str, Any]] = {}
    leaves: list[tuple[tuple[str, ...], str]] = []

    def visit(menu: dict[str, Any], path: tuple[str, ...]) -> bool:
        nonlocal remaining
        if not menu["texts"] and not menu["categories"]:
            return False  # No loaded rows prove that this branch was explored.
        menus[path] = menu
        leaves.extend((path, _option_title(text)) for text in menu["texts"])
        categories = menu["categories"]
        if len(set(categories)) != len(categories) or any(not name for name in categories):
            return False
        if categories and len(path) >= 8:
            return False
        for category in categories:
            if remaining <= 0:
                return False
            target = _menu_control(frame, key, "category", category)
            if target is None:
                return False
            remaining -= 1
            frame.click(target, intent=FIELD)
            child = _wait_workday_menu(frame, key, menu["signature"])
            if child is None or not child["back"] or not visit(child, (*path, category)):
                return False
            # Only an explicitly owned clickable header proves "back". Never
            # click an answer or guess a breadcrumb from its visible text.
            target = _menu_control(frame, key, "back")
            if target is None:
                return False
            frame.click(target, intent=FIELD)
            restored = _wait_workday_menu(frame, key, child["signature"])
            if restored is None or restored["signature"] != menu["signature"]:
                return False
        return True

    if not visit(state, ()):
        raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete")
    # Count candidate occurrences across every category, not just the first
    # matching menu. Even two configured aliases cannot identify which source
    # category the user intended when both are offered, unless a path says so.
    # A leaf named exactly as a candidate precedes looser matches: "United
    # States of America (+1)" is the saved region, not "+1"'s "Canada (+1)".
    exact = {norm_question(candidate) for candidate in leaf_candidates} - {""}
    matches = [(path, text) for path, text in leaves if not location and norm_question(text) in exact] or [
        (path, text) for path, text in leaves
        if leaf_candidates and match_option(leaf_candidates, [text], location=location, prefix=False,
                                            graduation=graduation) is not None
    ]
    if not matches and any(_COMPANY_SITE_ANSWER.fullmatch(" ".join(c.casefold().split())) for c in leaf_candidates):
        # Tenants brand the company-site source ("AV Corporate Website"). A saved
        # "company careers site" answer means that leaf only when it is unique.
        matches = [(path, text) for path, text in leaves if _BRANDED_COMPANY_SITE.fullmatch(text.strip())]
    if len(matches) > 1:
        placed = [match for match in matches if any(_same_place(match[0], path[:-1]) for path in paths)]
        if len(placed) == 1:
            matches = placed
        elif pick_source and len({norm_question(text) for _, text in matches}) == 1:
            matches = matches[:1]
        else:
            raise ChoiceSelectionError("Workday saved choice is ambiguous")
    if not matches and pick_source:
        picked = pick_source_option([text for _, text in leaves], random.Random(0))
        matches = [next(leaf for leaf in leaves if leaf[1] == picked)] if picked else []
    if not matches:
        raise ChoiceSelectionError("Workday saved choice was not found in the fully inspected hierarchy")
    path, text = matches[0]
    current = _wait_workday_menu(frame, key)
    if current is None or current["signature"] != state["signature"]:
        raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete")
    for depth, category in enumerate(path, 1):
        target = _menu_control(frame, key, "category", category)
        if target is None:
            raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete")
        frame.click(target, intent=FIELD)
        current = _wait_workday_menu(frame, key, current["signature"])
        if current is None or current["signature"] != menus[path[:depth]]["signature"]:
            raise ChoiceSelectionError("Workday choice hierarchy is unavailable or incomplete")
    options = [_option_title(value) for value in current["texts"]]
    try:
        committed = _click_match(frame, key, options, (text,), location=location,
                                 prefix=False, graduation=graduation, preserve_query=True)
    except (BidiError, UnsafeAction) as exc:
        raise ChoiceSelectionError("Workday choice selection was not committed") from exc
    if not committed:
        raise ChoiceSelectionError("Workday choice selection was not committed")
    return True


def pick_from_search(
    frame: Frame, key: str, search_text: str, candidates: tuple[str, ...], *,
    location: bool, prefix: bool = True, graduation: str | None = None, pick_source: bool = False,
) -> bool:
    """Type into a search-as-you-type box and click the suggestion that matches.

    Generic suggestions must be owned options or adjacent Lever locations;
    failed queries are cleared. Workday's native list variant instead uses its
    owned icon/category/back controls without typing or clearing query text.
    Native failures raise ChoiceSelectionError, never a retryable search miss;
    do not retry its failed endpoint or substitute a different answer.
    """
    if _guard(frame, key, custom=("combobox",)) == "combobox":
        return False                             # non-editable ARIA field, never type into it
    selected = frame.call(_SUGGESTIONS_JS, key, 0, "selected")
    if selected:
        matches = match_option(candidates, selected, location=location, prefix=False,
                               graduation=graduation) is not None
        if not matches and frame.call(_WORKDAY_MENU_JS, key)["mode"] != "other":
            raise ChoiceSelectionError("Workday choice already has a different committed selection")
        return matches
    native = _pick_workday_list(frame, key, candidates, location=location, graduation=graduation,
                                pick_source=pick_source)
    if native is not None:
        return native
    if not frame.call(_REACHABLE_JS, key):
        return False
    typed = " ".join(search_text.split())[:80]     # no newlines: typing one would press Enter
    frame.call(_CLEAR_JS, key)                     # empty it and keep the focus there ...
    _guard(frame, key)
    frame.type(typed, into=_node(frame, key))     # ... then real key presses open the list
    options = _wait_for_options(frame, key)
    return _click_match(frame, key, options, candidates, location=location, prefix=prefix,
                        graduation=graduation)


def pick_from_list(
    frame: Frame, key: str, candidates: tuple[str, ...], *, location: bool,
    graduation: str | None = None, pick_source: bool = False,
) -> bool:
    """Open and inspect existing choices before resorting to a typed search.

    Only exact names / configured aliases count in this possibly partial list.
    The short probe avoids the search wait on boxes that stay empty until typed;
    actual searches retain their full loading-indicator wait (notably Lever).
    Workday native lists use bounded, structurally proven category navigation,
    never category names as answers; only a matching committed leaf succeeds.
    Native terminal failures raise ChoiceSelectionError rather than returning a
    retryable False, including a fully inspected but unmatched saved choice.
    `pick_source` (a recruitment-source question whose choices show only once
    opened) picks a plain offered source when no candidate is offered.
    """
    _guard(frame, key, custom=("combobox",))
    selected = frame.call(_SUGGESTIONS_JS, key, 0, "selected")
    if selected:
        matches = match_option(candidates, selected, location=location, prefix=False,
                               graduation=graduation) is not None
        if not matches and frame.call(_WORKDAY_MENU_JS, key)["mode"] != "other":
            raise ChoiceSelectionError("Workday choice already has a different committed selection")
        return matches
    native = _pick_workday_list(frame, key, candidates, location=location, graduation=graduation,
                                pick_source=pick_source)
    if native is not None:
        return native
    if not frame.call(_REACHABLE_JS, key):
        return False
    frame.call(_CLEAR_JS, key)
    if frame.call(_REACHABLE_JS, key):
        frame.click(_node(frame, key), intent=FIELD)
    _guard(frame, key, custom=("combobox",))
    frame.press("ArrowDown", intent=FIELD, into=_node(frame, key))  # open keyboard-only lists
    options = _wait_for_options(frame, key, seconds=0.8)
    return _click_match(frame, key, options, candidates, location=location, prefix=False,
                        graduation=graduation, pick_source=pick_source)


#: react-select's own commit condition (Select.tsx; guard.listCommitReady): an
#: open, focused combobox input whose aria-activedescendant is the chosen option
#: of the listbox it controls. Returns null when the box is not that widget. The
#: option is found again by its id: lists re-render as the focus moves.
_LIST_KEYS_JS = r"""
(key, optionId, expected) => {""" + _DOM_HELPERS_JS + r"""
  const box = globalThis.kittyByKey.get(key);
  const option = optionId ? reference(box, optionId) : globalThis.kittySuggestionTargets?.get(key);
  if (!box || !option?.isConnected || !fieldSafe(box) || !option.id) return null;
  const title = (option.innerText || composedText(option)).split(/\r?\n/)
    .map(s => s.replace(/\s+/g, ' ').trim()).find(Boolean) || '';
  const reactSelect = box.matches('input[role=combobox][aria-autocomplete=list]') &&
    (/^react-select-.+-input$/.test(box.id) || /^react-select-.+-option-\d+$/.test(option.id));
  const list = reference(box, box.getAttribute('aria-controls') || box.getAttribute('aria-owns'));
  let active = document.activeElement;
  while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
  if (!reactSelect || title !== expected || list?.getAttribute('role') !== 'listbox' || !contains(list, option) ||
      active !== box || box.getAttribute('aria-expanded') !== 'true') return null;
  return {active: box.getAttribute('aria-activedescendant') === option.id, id: option.id};
}
"""


def _commit_by_keys(frame: Frame, key: str, rows: int, expected: str) -> bool | None:
    """Move react-select's focused option to the chosen one, then Enter (LIST_COMMIT).

    None: not a react-select under its commit condition (use the pointer).
    False: the condition stopped holding after a key, so nothing else is tried.
    Enter is pressed only while the condition holds; L0 and L1 check it again.
    """
    state = frame.call(_LIST_KEYS_JS, key, "", expected)
    if state is None:
        return None
    option = state["id"]
    for _ in range(rows + 2):
        if state["active"]:
            frame.press("Enter", intent=LIST_COMMIT, into=_node(frame, key))
            return True
        frame.press("ArrowDown", intent=FIELD, into=_node(frame, key))
        state = frame.call(_LIST_KEYS_JS, key, option, expected)
        if state is None:
            return False
    return False


def _place_index(candidates: tuple[str, ...], options: list[str]) -> int | None:
    """A unique suggestion naming a saved place with a postal code or country tail
    ("Arlington, TX 76010, USA" for "Arlington, TX"); never a nearest place."""
    def place(text: str) -> str:
        return " ".join(word for word in norm_location(text).split() if not word.isdigit())
    wanted = {place(candidate) for candidate in candidates} - {""}
    found = [index for index, option in enumerate(options) if place(option) in wanted]
    return found[0] if len(found) == 1 else None


def _click_match(
    frame: Frame, key: str, options: list[str], candidates: tuple[str, ...], *,
    location: bool, prefix: bool, graduation: str | None = None, preserve_query: bool = False,
    pick_source: bool = False,
) -> bool:
    """Commit a matching suggestion (react-select by keys, else a click) and verify it sticks.

    Only a real suggestion counts: typed text alone is never a value.
    """
    index = match_option(candidates, options, location=location, prefix=prefix,
                         graduation=graduation)
    if index is None and location:
        index = _place_index(candidates, options)
    if index is None and pick_source and options:
        picked = pick_source_option(options, random.Random(0))
        index = options.index(picked) if picked is not None else None
    if index is None:
        frame.call(_DISCARD_QUERY_JS, key, preserve_query)
        return False
    found = frame.call(_SUGGESTIONS_JS, key, index, "check", options[index])
    if found is None or _option_title(found["text"]) != options[index]:
        frame.call(_DISCARD_QUERY_JS, key, preserve_query)
        return False
    role, tag, css = found["role"], found["tag"], found["css"]
    if (role != "option" and _SUGGESTION_CLASS not in css) or tag in ("input", "a") or (
            tag == "button" and not found.get("ownedButton")):
        raise UnsafeAction(f"refusing to click <{tag} role={role}>")
    option = frame.node(_SUGGESTIONS_JS, key, index, "node", options[index])
    if option is None:
        frame.call(_DISCARD_QUERY_JS, key, preserve_query)
        return False
    # Workday slides child menus in; a pointer-only row is briefly covered.
    settle_until = time.monotonic() + 2.0
    while not found["reachable"] and found.get("pointerOnly") and time.monotonic() < settle_until:
        time.sleep(0.15)
        again = frame.call(_SUGGESTIONS_JS, key, index, "check", options[index])
        if again is None or _option_title(again["text"]) != options[index]:
            break
        found = again
    if not found["reachable"] and found.get("pointerOnly"):
        frame.call(_DISCARD_QUERY_JS, key, preserve_query)
        return False
    try:
        keyed = _commit_by_keys(frame, key, len(options), options[index])
        if keyed is None:                # not a react-select under its commit condition
            if found["reachable"]:
                frame.click(option, intent=FIELD)
            else:
                frame.call(_SUGGESTIONS_JS, key, index, "click", options[index])
    except BidiError:
        frame.call(_DISCARD_QUERY_JS, key, preserve_query)
        raise
    if keyed is False:                   # the list changed under the keys: commit nothing
        frame.call(_DISCARD_QUERY_JS, key, preserve_query)
        return False
    frame.call(_SUGGESTIONS_JS, key, 0, "commit")
    frame.call("(key) => globalThis.kittyByKey.get(key).blur()", key)
    deadline = time.monotonic() + 0.8
    while time.monotonic() < deadline:
        selected = frame.call(_SUGGESTIONS_JS, key, 0, "selected")
        if match_option((options[index], *candidates), selected, location=location, prefix=False) is not None:
            return True
        time.sleep(0.1)
    frame.call(_DISCARD_QUERY_JS, key, preserve_query)
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
      !closest(e, '[data-automation-id=progressBar]') &&
      // Oracle lookup skeletons load choices, not a resume. Keep explicit
      // resume-analysis indicators blocking even inside a lookup container.
      !(e.matches('.cx-select--loading') && closest(e, '.cx-select-container') &&
        !e.matches('.resume-upload-working')));
}
"""


def uploads_pending(tab: Tab) -> bool:
    """Whether any application frame still reports an upload or resume parse in progress.

    An iframe replaced or removed between the tree read and its call ("no such
    frame") is re-acquired by the next read, not a closed tab; only the tab's
    own document failing is an error. Readiness still needs a verified resume.
    """
    for index, frame in enumerate(form_frames(tab)):
        try:
            if frame.call(_BUSY_JS):
                return True
        except ConnectionLost:
            raise
        except BidiError:
            if not index:
                raise
    return False


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
