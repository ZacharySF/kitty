"""I-NoHidden′ (research note no-hidden.md §3): never write traps; write hidden-backed
controls only through their painted host.

Must-pass P1-P8: every hidden-backed control a counted family renders is written
through its host (read-back asserted). Must-refuse R1-R11: every decoy is refused
by its clause, its value/checked/files stay unchanged, and it sees no input,
change or focus event. A required decoy is one `hidden_target_refused` handoff and
the job is partial, never ready. Every page is served by a SubmitTrap inside a
`<form method=post action=/trap/submit>`; no fixture sends anything.

All markup and values are synthetic. Markup beyond the cited atsfill/kitty
shapes is [INFERENCE] per no-hidden.md §3.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from kitty.config import AnswerEntry, AnswersFile
from kitty.core import jobs as jobs_core
from kitty.core.apply.bidi import open_tab
from kitty.core.apply.fields import is_resume_input
from kitty.core.apply.filler import fill_form, fill_job
from kitty.core.apply.page import (
    HiddenTargetRefused, choose_select, fill_text, read_fields, settle, tick, upload,
)
from submit_trap import SubmitTrap, self_signed
from test_application_auth import firefox  # noqa: F401  (headless Firefox accepting the trap's certificate)
from test_apply import space  # noqa: F401

_COUNTERS = """<script>
window.__events = {};
for (const type of ['input', 'change', 'focus', 'click'])
  document.addEventListener(type, e => {
    const t = e.target, id = t && (t.id || t.getAttribute?.('name'));
    if (id) (window.__events[id] ??= []).push(type);
  }, true);
</script>"""

ANSWERS = AnswersFile(answers=[
    AnswerEntry(question="Website", answer="https://portfolio.example.invalid"),
    AnswerEntry(question="Company", answer="Example Co"),
    AnswerEntry(question="Phone", answer="+1 512 555 0100"),
    AnswerEntry(question="Leave this field blank", answer="filled"),
    AnswerEntry(question="Email confirmation", answer="pat@example.invalid"),
    AnswerEntry(question="I agree to the terms", answer="Yes"),
    AnswerEntry(question="Do you want to answer?", answer="Yes"),
    AnswerEntry(question="Have you previously worked for Honeywell?", answer="No"),
    AnswerEntry(question="Are you a current HP employee?", answer="No"),
    AnswerEntry(question="Are you willing to relocate?", answer="Yes"),
])


@pytest.fixture(scope="module")
def trap(tmp_path_factory):
    trap = SubmitTrap(tls=self_signed(tmp_path_factory.mktemp("nohidden-tls")))
    yield trap
    trap.close()


@pytest.fixture
def tab(firefox, trap):  # noqa: F811
    trap.reset()
    tab = open_tab(firefox)
    yield tab
    tab.release()
    tab.close()


def _serve(trap, tab, body: str, *, path: str = "/nohidden") -> None:
    trap.pages[path] = ("<!doctype html><html><head><meta charset=utf-8><title>Application</title></head><body>"
                        "<form method=post action=/trap/submit>" + body +
                        "<button type=submit>Submit application</button></form>" + _COUNTERS + "</body></html>")
    tab.arm(family="other")
    tab.goto(trap.url(path))
    settle(tab)


def _page(tab, script: str, *args):
    return tab.main.call(script, *args, page_realm=True)


def _fields(tab) -> dict:
    return {f.element_id or f.name: f for f in read_fields([tab.main])}


def _key(tab, selector: str) -> str:
    """Remember a decoy in kitty's sandbox map, as a stale or replayed read would have."""
    return tab.main.call("""(selector) => {
      const e = document.querySelector(selector);
      (globalThis.kittyByKey ??= new Map()).set('probe:' + selector, e);
      return 'probe:' + selector;
    }""", selector)


def _events(tab, ident: str) -> list[str]:
    return _page(tab, "(id) => window.__events[id] || []", ident)


def _quiet(trap, tab) -> None:
    assert trap.writes() == [], "a submit reached the server"
    assert tab.vetoes == [], "the submit guard had to block an action"


def _resume(space):  # noqa: F811
    return space.settings.root / "resume.pdf"


def _files(tab, selector: str) -> list[str]:
    return _page(tab, "(s) => Array.from(document.querySelector(s).files).map(f => f.name)", selector)


# ------------------------------------------------------------ must-pass


@pytest.mark.parametrize("variant", ["clipped", "display-none"])
def test_p1_greenhouse_attach_group_hosts_its_hidden_resume_input(space, trap, tab, variant):  # noqa: F811
    style = ("position:absolute;width:1px;height:1px;clip:rect(0,0,0,0)" if variant == "clipped"
             else "display:none")
    _serve(trap, tab, f'''
      <div class="file-upload" role="group" aria-labelledby="ul" aria-required="true">
        <div id="ul">Resume/CV*</div><button type="button">Attach</button>
        <label for="resume" style="position:absolute;clip:rect(0,0,0,0)">Attach</label>
        <input id="resume" name="resume" type="file" style="{style}">
        <button type="button">Enter manually</button>
      </div>''')
    field = _fields(tab)["resume"]
    assert is_resume_input(field) and not field.refused and field.host == "group"
    upload(tab.main, field.key, _resume(space))
    assert _files(tab, "#resume") == ["resume.pdf"]
    _quiet(trap, tab)


def test_p2_lever_resume_link_hosts_its_input_and_is_never_clicked(space, trap, tab):  # noqa: F811
    _serve(trap, tab, '''
      <div class="application-field">
        <a class="postings-btn visible-resume-upload" href="#" onclick="window.anchorClicks=(window.anchorClicks||0)+1">ATTACH RESUME/CV
          <input class="application-file-input invisible-resume-upload" type="file" name="resume"
            id="resume-upload-input" style="position:absolute;opacity:0;width:1px;height:1px"></a>
        <span class="filename"></span>
      </div>''')
    field = _fields(tab)["resume-upload-input"]
    assert not field.refused and field.host == "family"
    upload(tab.main, field.key, _resume(space))
    assert _files(tab, "#resume-upload-input") == ["resume.pdf"]
    assert _page(tab, "() => window.anchorClicks || 0") == 0
    _quiet(trap, tab)


def test_p3_ashby_drop_zone_and_hidden_yes_no_radios(space, trap, tab):  # noqa: F811
    _serve(trap, tab, '''
      <div class="ashby-application-form-field-entry">
        <label class="_heading _required" for="_systemfield_resume">Resume</label>
        <div class="_dropzone" style="padding:24px;border:1px dashed">Upload File <span>or drag and drop here</span>
          <input type="file" id="_systemfield_resume" style="display:none" required></div>
      </div>
      <div class="ashby-application-form-field-entry"><label class="_heading">Do you want to answer?</label>
        <div class="_option"><input type="radio" id="y" name="q" style="display:none"><label for="y">Yes</label></div>
        <div class="_option"><input type="radio" id="n" name="q" style="display:none"><label for="n">No</label></div>
      </div>''')
    fields = _fields(tab)
    resume, question = fields["_systemfield_resume"], fields["y"]
    assert resume.host == "label" and not resume.refused
    assert question.options == ("Yes", "No") and not question.refused and question.host == "label"
    upload(tab.main, resume.key, _resume(space))
    assert tick(tab.main, question.option_keys[0])
    assert _files(tab, "#_systemfield_resume") == ["resume.pdf"]
    assert _page(tab, "() => document.querySelector('#y').checked") is True
    _quiet(trap, tab)


def test_p4_workday_file_upload_and_source_prompt_hosts(space, trap, tab):  # noqa: F811
    _serve(trap, tab, '''
      <div data-automation-id="formField-resumeAttachments--attachments" data-fkit-id="resumeAttachments--attachments">
        <label>Resume/CV*</label>
        <div data-automation-id="file-upload-drop-zone">Drop file here
          <button type="button" data-automation-id="select-files">Select files</button>
          <input type="file" id="wd-resume" data-automation-id="file-upload-input-ref" style="display:none"></div>
      </div>
      <label for="source--source">How Did You Hear About Us?*</label>
      <div data-automation-id="multiSelectContainer" id="source-owner">
        <input id="source--source" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="source-owner"
          aria-required="true" style="position:absolute;width:1px;height:1px;opacity:0">
        <span data-automation-id="promptSearchButton" data-uxi-widget-type="selectinputicon"
          data-uxi-selectinputicon-type="promptSearchButton" data-uxi-multiselect-id="source-owner"
          style="display:inline-block;padding:12px;border:1px solid">List</span>
      </div>''')
    fields = _fields(tab)
    resume, source = fields["wd-resume"], fields["source--source"]
    assert is_resume_input(resume) and resume.host == "family" and not resume.refused
    assert source.kind == "combobox" and source.host == "prompt" and not source.refused
    upload(tab.main, resume.key, _resume(space))
    assert _files(tab, "#wd-resume") == ["resume.pdf"]
    _quiet(trap, tab)


def test_p5_oracle_terms_ticked_through_label_and_honeypot_untouched(space, trap, tab):  # noqa: F811
    _serve(trap, tab, '''
      <input name="honey-pot" id="honey-pot" aria-hidden="true" tabindex="-1" style="display:none">
      <input type="checkbox" id="legal-disclaimer-checkbox" required
        aria-labelledby="legal-disclaimer-checkbox-label" style="position:absolute;opacity:0">
      <label for="legal-disclaimer-checkbox" id="legal-disclaimer-checkbox-label">I agree with the terms and conditions</label>''')
    fields = _fields(tab)
    terms = fields["legal-disclaimer-checkbox"]
    assert "honey-pot" not in fields
    assert terms.host == "label" and not terms.refused
    assert tick(tab.main, terms.option_keys[0])
    assert _page(tab, "() => document.querySelector('#legal-disclaimer-checkbox').checked") is True
    assert _page(tab, "() => document.querySelector('[name=honey-pot]').value") == ""
    assert _events(tab, "honey-pot") == []
    _quiet(trap, tab)


def _oracle_slots() -> str:
    slot = ('<{tag}><label>{label}</label><button type="button">Upload {label}</button>'
            '<input type="file" id="attachment-upload-{n}" class="file-form-element__input upload-button" '
            'style="position:absolute;opacity:0;width:1px;height:1px"></{tag}>')
    return ('<div class="apply-flow-block--file-upload">' +
            slot.format(tag="resume-upload-button", label="Resume", n=2) +
            slot.format(tag="cover-letter-upload-button", label="Cover Letter", n=3) + '</div>'
            '<div class="apply-flow-block--misc-attachments">' +
            slot.format(tag="attachment-upload-button", label="Attachments", n=4) +
            '<ul class="misc-attachment-list"></ul></div>'
            # R11: the Oracle Digital Assistant chat's own hostless upload input
            '<div class="oda-chat"><input type="file" id="oda-chat-upload" style="display:none"></div>')


def test_p6_oracle_resume_lands_only_in_its_own_slot(space, trap, tab):  # noqa: F811
    _serve(trap, tab, _oracle_slots())
    fields = _fields(tab)
    assert {name for name in fields if name.startswith("attachment-upload-")} == {
        "attachment-upload-2", "attachment-upload-3", "attachment-upload-4"}
    assert all(fields[f"attachment-upload-{n}"].host == "family" for n in (2, 3, 4))
    assert "oda-chat-upload" not in fields                      # R11: never a resume target
    targets = [f for f in fields.values() if is_resume_input(f)]
    assert [f.element_id for f in targets] == ["attachment-upload-2"]
    upload(tab.main, targets[0].key, _resume(space))
    assert _files(tab, "#attachment-upload-2") == ["resume.pdf"]
    assert _files(tab, "#attachment-upload-3") == _files(tab, "#attachment-upload-4") == []
    assert _files(tab, "#oda-chat-upload") == [] and _events(tab, "oda-chat-upload") == []
    _quiet(trap, tab)


def test_p7_oracle_hidden_radio_inside_its_label(space, trap, tab):  # noqa: F811
    _serve(trap, tab, '''
      <div class="input-row"><div class="input-row__label">Are you willing to relocate?</div>
        <label class="apply-flow-input-radio"><input class="apply-flow-input-radio-control" type="radio"
          name="q1" value="Y" style="position:absolute;opacity:0">Yes</label>
        <label class="apply-flow-input-radio"><input class="apply-flow-input-radio-control" type="radio"
          name="q1" value="N" style="position:absolute;opacity:0">No</label></div>''')
    field = _fields(tab)["q1"]
    assert field.options == ("Yes", "No") and field.host == "label" and not field.refused
    assert tick(tab.main, field.option_keys[0])
    assert _page(tab, "() => document.querySelector('[name=q1][value=Y]').checked") is True
    _quiet(trap, tab)


def test_p8_word_boundaries_keep_honeywell_and_hp_questions(space, trap, tab):  # noqa: F811
    _serve(trap, tab, '''
      <fieldset><legend>Have you previously worked for Honeywell?</legend>
        <label><input type="radio" name="honeywell" value="yes">Yes</label>
        <label><input type="radio" name="honeywell" value="no">No</label></fieldset>
      <label for="hp">Are you a current HP employee?</label>
      <select id="hp" name="hp_employee_status"><option value=""></option><option>Yes</option><option>No</option></select>''')
    config = replace(space.config, answers=ANSWERS)
    result = fill_form(tab, config, None)
    assert _page(tab, "() => document.querySelector('[name=honeywell][value=no]').checked") is True
    assert _page(tab, "() => document.querySelector('#hp').value") == "No"
    assert result.refused == [] and result.unanswered == []
    _quiet(trap, tab)


# ------------------------------------------------------------ must-refuse


def _refused(tab, selector: str, write, clause: str) -> None:
    with pytest.raises(HiddenTargetRefused) as refused:
        write(tab.main, _key(tab, selector))
    assert refused.value.clause == clause


@pytest.mark.parametrize(("style", "value"), [
    ("display:none", ""), ("position:absolute;left:-10000px", ""),
    ("width:200px;height:30px", ""), ("display:none", "untouched"),
])
def test_r1_oracle_honeypot_is_refused_in_every_style(space, trap, tab, style, value):  # noqa: F811
    _serve(trap, tab, f'<input name="honey-pot" id="hp1" aria-hidden="true" tabindex="-1" value="{value}" style="{style}">'
                      '<label for="email">Email</label><input id="email" name="email">')
    assert "hp1" not in _fields(tab)
    _refused(tab, "#hp1", lambda frame, key: fill_text(frame, key, "bot"), "T1")
    assert _page(tab, "() => document.querySelector('#hp1').value") == value
    assert _events(tab, "hp1") == []
    _quiet(trap, tab)


@pytest.mark.parametrize("move", ["position:absolute;left:-10000px;top:0", "transform:translateX(-200vw)"])
def test_r2_off_screen_required_website_is_one_handoff_never_ready(space, trap, tab, move):  # noqa: F811
    path = "/r2" + str(len(move))
    trap.pages[path] = ("<!doctype html><title>Apply</title><form id=application method=post action=/trap/submit>"
                        f'<div style="{move}"><label for="website">Website</label>'
                        '<input id="website" name="website" type="url" required></div>'
                        '<label for="company">Company</label><input id="company" name="company">'
                        "<button type=submit>Submit application</button></form>" + _COUNTERS)
    job, _ = jobs_core.upsert_job(space.conn, company="Fixture Co", title="R2 intern", url=trap.url(path),
                                  tracks=[space.track.name], location="Arlington, TX, United States", country="US")
    space.config = replace(space.config, answers=ANSWERS)
    report = fill_job(tab, space.conn, space.settings, space.config, job)
    assert report.status == "partial", report.note
    assert [item for item in report.unanswered if "hidden_target_refused" in item] == [
        "Website (hidden_target_refused: H)"]
    assert _page(tab, "() => document.querySelector('#website').value") == ""
    assert _page(tab, "() => document.querySelector('#company').value") == "Example Co"
    assert _events(tab, "website") == []
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None
    _quiet(trap, tab)


@pytest.mark.parametrize("markup", [
    '<label for="co2" style="opacity:0">Company</label><input id="co2" name="company2" style="opacity:0">',
    '<div style="opacity:0"><label for="co2">Company</label><input id="co2" name="company2"></div>',
    '<label for="co2" style="opacity:0.01">Company</label><input id="co2" name="company2" style="opacity:0.01">',
])
def test_r3_opacity_zero_company_is_refused(space, trap, tab, markup):  # noqa: F811
    _serve(trap, tab, markup)
    assert "co2" not in _fields(tab)
    _refused(tab, "#co2", lambda frame, key: fill_text(frame, key, "Example Co"), "H")
    assert _page(tab, "() => document.querySelector('#co2').value") == ""
    assert _events(tab, "co2") == []
    _quiet(trap, tab)


@pytest.mark.parametrize("clip", ["position:absolute;width:1px;height:1px;clip:rect(0,0,0,0)", "clip-path:inset(50%)"])
def test_r4_clip_hidden_phone_confirm_shares_p1_css_and_is_refused(space, trap, tab, clip):  # noqa: F811
    _serve(trap, tab, f'<label for="pc" style="{clip}">Phone</label><input id="pc" name="phone_confirm" style="{clip}">'
                      f'<input type="file" id="att" name="attachment" style="{clip}">')
    fields = _fields(tab)
    assert "pc" not in fields and "att" not in fields
    _refused(tab, "#pc", lambda frame, key: fill_text(frame, key, "+1 512 555 0100"), "H")
    _refused(tab, "#att", lambda frame, key: upload(frame, key, _resume(space)), "H")
    assert _page(tab, "() => document.querySelector('#pc').value") == ""
    assert _files(tab, "#att") == []
    assert _events(tab, "pc") == [] and _events(tab, "att") == []
    _quiet(trap, tab)


def test_r5_aria_hidden_required_checkbox_never_gets_the_consent_grant(space, trap, tab):  # noqa: F811
    _serve(trap, tab, '<div aria-hidden="true" style="position:absolute;left:-9999px">'
                      '<input type="checkbox" id="ag2" name="agree" required><label for="ag2">I agree to the terms</label></div>')
    field = _fields(tab)["ag2"]
    assert field.required and field.refused == "H"
    _refused(tab, "#ag2", tick, "H")
    result = fill_form(tab, replace(space.config, answers=ANSWERS), None)
    assert "I agree to the terms (hidden_target_refused: H)" in result.unanswered
    assert _page(tab, "() => document.querySelector('#ag2').checked") is False
    assert _events(tab, "ag2") == []
    _quiet(trap, tab)


def test_r6_visible_workday_beecatcher_is_refused_although_painted(space, trap, tab):  # noqa: F811
    _serve(trap, tab, '<input name="beecatcher" id="bee" type="text" style="width:200px">')
    assert "bee" not in _fields(tab)
    _refused(tab, "#bee", lambda frame, key: fill_text(frame, key, "bot"), "T1")
    assert _page(tab, "() => document.querySelector('#bee').value") == "" and _events(tab, "bee") == []
    _quiet(trap, tab)


def test_r7_phrase_traps_are_refused(space, trap, tab):  # noqa: F811
    _serve(trap, tab, '<label for="x">Leave this field blank</label><input id="x">'
                      '<input id="y" placeholder="Do not fill this in">')
    fields = _fields(tab)
    assert "x" not in fields and "y" not in fields
    for selector in ("#x", "#y"):
        _refused(tab, selector, lambda frame, key: fill_text(frame, key, "filled"), "T1")
    assert _page(tab, "() => [document.querySelector('#x').value, document.querySelector('#y').value]") == ["", ""]
    assert _events(tab, "x") == [] and _events(tab, "y") == []
    _quiet(trap, tab)


def test_r8_hidden_hp_prefixed_input_is_a_weak_token_trap(space, trap, tab):  # noqa: F811
    _serve(trap, tab, '<input name="hp_email" id="hpe" style="position:absolute;left:-9999px">')
    assert "hpe" not in _fields(tab)
    _refused(tab, "#hpe", lambda frame, key: fill_text(frame, key, "pat@example.invalid"), "T2")
    assert _events(tab, "hpe") == []
    _quiet(trap, tab)


@pytest.mark.parametrize("hiding", ["display:none", "visibility:hidden"])
def test_r9_hcaptcha_checkbox_frame_is_refused(space, trap, tab, hiding):  # noqa: F811
    _serve(trap, tab, f'<iframe title="Widget containing checkbox for hCaptcha security challenge" style="{hiding}" '
                      'srcdoc="<input type=checkbox id=hc><label for=hc>I am human</label>"></iframe>')
    frame = next(frame for frame in tab.frames()[1:])
    assert not [f for f in read_fields([frame]) if f.element_id == "hc"]
    key = frame.call("() => { const e = document.querySelector('#hc'); (globalThis.kittyByKey ??= new Map()).set('hc', e); return 'hc'; }")
    with pytest.raises(HiddenTargetRefused) as refused:
        tick(frame, key)
    assert refused.value.clause in ("T1", "H")
    assert frame.call("() => document.querySelector('#hc').checked", page_realm=True) is False
    _quiet(trap, tab)


def test_r10_backing_input_beside_submit_buttons_has_no_host(space, trap, tab):  # noqa: F811
    _serve(trap, tab, '<div class="ashby-application-form-field-entry"><label class="_heading">Do you want to answer?*</label>'
                      '<div class="_yesno"><button type="submit">Yes</button><button type="submit">No</button>'
                      '<input type="checkbox" name="yesno" id="yn" style="display:none"></div></div>')
    fields = read_fields([tab.main])
    assert [f.kind for f in fields if f.label == "Do you want to answer?"] == ["unsupported"]
    _refused(tab, "#yn", tick, "H")
    assert _page(tab, "() => document.querySelector('#yn').checked") is False and _events(tab, "yn") == []
    _quiet(trap, tab)


def test_r11_hostless_chat_upload_is_refused_beside_p6(space, trap, tab):  # noqa: F811
    _serve(trap, tab, _oracle_slots())
    _refused(tab, "#oda-chat-upload", lambda frame, key: upload(frame, key, _resume(space)), "H")
    assert _files(tab, "#oda-chat-upload") == [] and _events(tab, "oda-chat-upload") == []
    _quiet(trap, tab)


def test_write_time_check_covers_a_re_render_between_read_and_write(space, trap, tab):  # noqa: F811
    _serve(trap, tab, '<label for="nick">Nickname</label><input id="nick" name="nick">')
    field = _fields(tab)["nick"]
    assert not field.refused and field.host == "self"
    _page(tab, "() => { const e = document.querySelector('#nick'); e.style.opacity = '0'; }")
    with pytest.raises(HiddenTargetRefused):
        fill_text(tab.main, field.key, "Pat")
    with pytest.raises(HiddenTargetRefused):
        choose_select(tab.main, field.key, "Pat")
    assert _page(tab, "() => document.querySelector('#nick').value") == ""
    _quiet(trap, tab)
