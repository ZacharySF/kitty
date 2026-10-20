"""Owned selector commits and retained values in isolated headless Firefox."""
from dataclasses import replace
from urllib.parse import quote
import json

import pytest

from test_apply import FORMS, _js, firefox, page, space
from kitty.core.apply.fields import is_resume_input, plan_for
from kitty.core.apply.page import (
    ChoiceSelectionError, UnsafeAction, fill_text, pick_from_list, read_fields, tick, upload,
)


def _load(page, markup, script="", *, native_form=True):
    body = ('<form onsubmit="window.submitted++;return false">' + markup +
            '<button type="submit">Submit application</button></form>') if native_form else markup
    page.goto("data:text/html," + quote(
        body + '<script>window.submitted=0;window.trusted=[];' + script + '</script>'
    ))


def _oracle(page, *, code=False, commit=True, covered=False):
    label = "Country code" if code else "Country"
    option = "+44 (United Kingdom)" if code else "United States"
    value = "+44" if code else option
    _load(page, f'''
      <div class="input-row">
        <label for="country">{label}<span class="input-row__label--required-star"></span></label>
        <div class="cx-select-container">
          <input class="cx-select-input" id="country" role="combobox" aria-haspopup="grid"
            aria-controls="owned" aria-expanded="false" aria-required="true">
          <div id="owned" role="grid" hidden>
            <div role="row"><div id="choice" role="gridcell" tabindex="-1" aria-selected="false">{option}</div></div>
          </div>
        </div>
      </div>
      <div role="grid"><div role="gridcell" onclick="window.submitted++">{option}</div></div>
    ''', f'''
      const box=document.querySelector('#country'), list=document.querySelector('#owned');
      box.onclick=() => {{list.hidden=false;box.setAttribute('aria-expanded','true');}};
      box.onkeydown=e => {{if(e.key==='ArrowDown') {{list.hidden=false;box.setAttribute('aria-expanded','true');}}}};
      document.querySelector('#choice').onclick=e => {{
        window.trusted.push(e.isTrusted);
        if (!e.isTrusted || {str(not commit).lower()}) return;
        box.value={value!r}; document.querySelector('.input-row').classList.add('input-row--filled');
        list.remove();box.setAttribute('aria-expanded','false');
      }};
      {'document.querySelector("#choice").insertAdjacentHTML("afterend", `<div style="position:fixed;inset:0;z-index:999" onclick="window.submitted++"></div>`);' if covered else ''}
    ''')
    return next(f for f in read_fields([page.main]) if f.element_id == "country")


@pytest.mark.parametrize("code", [False, True])
def test_oracle_owned_grid_commits_model_value_not_query(page, space, code):
    field = _oracle(page, code=code)
    profile = space.config.profile.model_copy(update={"country": "United States", "phone": "+44 20 7946 0000"})
    plan = plan_for(field, replace(space.config, profile=profile))
    assert field.kind == "combobox" and field.required and field.blank
    assert plan is not None
    assert pick_from_list(page.main, field.key, plan.candidates, location=False)
    final = next(f for f in read_fields([page.main]) if f.element_id == "country")
    assert not final.blank and not final.invalid
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("covered", [False, True])
def test_oracle_grid_requires_real_commit_and_reachable_pointer(page, covered):
    field = _oracle(page, commit=False, covered=covered)
    assert not pick_from_list(page.main, field.key, ("United States",), location=False)
    assert next(f for f in read_fields([page.main]) if f.element_id == "country").blank
    assert _js(page, "() => window.submitted") == 0
    assert _js(page, "() => window.trusted") == ([] if covered else [True])


def _workday(page):
    _load(page, '''
      <div data-automation-id="formField-country" data-fkit-id="country--country">
        <label for="country--country">Country / Territory*</label>
        <div><button type="button" aria-haspopup="listbox" value="us" name="country" id="country--country">United States of America</button><input type="text" hidden value="us"></div>
      </div>
      <div data-automation-id="formField-phoneType" data-fkit-id="phoneNumber--phoneType">
        <label for="phoneNumber--phoneType">Phone Device Type*</label>
        <div><button type="button" aria-haspopup="listbox" value="" name="phoneType" id="phoneNumber--phoneType">Select One</button><input type="text" hidden value=""></div>
      </div>
      <div data-automation-id="formField-suffix" data-fkit-id="suffix">
        <label for="suffix">Suffix</label>
        <div><button type="button" aria-haspopup="listbox" value="" name="suffix" id="suffix">Select One</button><input type="text" hidden value=""></div>
      </div>
      <button type="button" aria-haspopup="listbox" id="utility">Settings</button>
      <ul id="unrelated" role="listbox"><li role="option" data-value="bad" onclick="window.submitted++">Mobile</li></ul>
    ''', '''
      const button=document.querySelector('#phoneNumber--phoneType');
      button.onclick=() => {
        const list=document.createElement('ul');list.id='phone-options';list.setAttribute('role','listbox');
        list.innerHTML='<li role="option" data-value="mobile">Mobile</li><li role="option" data-value="landline">Landline</li>';
        document.body.append(list);button.setAttribute('aria-controls',list.id);button.setAttribute('aria-expanded','true');
        for (const option of list.children) option.onclick=e => {
          window.trusted.push(e.isTrusted);if(!e.isTrusted)return;
          button.value=option.dataset.value;button.nextElementSibling.value=button.value;
          button.textContent=option.textContent;button.setAttribute('aria-expanded','false');list.remove();
        };
      };
    ''', native_form=False)


def test_workday_backed_buttons_own_popup_and_preserve_manual_country(page, space):
    _workday(page)
    fields = {f.element_id: f for f in read_fields([page.main])}
    country, phone = fields["country--country"], fields["phoneNumber--phoneType"]
    assert country.label == "Country / Territory" and country.required and not country.blank
    assert phone.kind == "combobox" and phone.label == "Phone Device Type" and phone.required and phone.blank
    assert not fields["suffix"].required
    assert "utility" not in fields
    assert plan_for(phone, space.config) is None
    assert not pick_from_list(page.main, country.key, ("United Kingdom",), location=False)
    assert pick_from_list(page.main, phone.key, ("Mobile",), location=False)
    assert not next(f for f in read_fields([page.main]) if f.element_id == phone.element_id).blank
    assert _js(page, "() => document.querySelector('#country--country').value") == "us"
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => window.submitted") == 0


def test_workday_button_cannot_mutate_to_submit(page):
    _workday(page)
    phone = next(f for f in read_fields([page.main]) if f.element_id == "phoneNumber--phoneType")
    _js(page, "() => document.querySelector('#phoneNumber--phoneType').type='submit'")
    with pytest.raises(UnsafeAction):
        pick_from_list(page.main, phone.key, ("Mobile",), location=False)
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("selected", [False, True])
def test_workday_phone_prompt_reads_receipt_not_search_text(page, space, selected):
    receipt = '''<ul data-automation-id="selectedItemList" role="listbox" data-uxi-multiselect-id="phone-owner">
      <li><div data-uxi-multiselect-id="phone-owner" data-automation-id="selectedItem" role="option">
        <p data-automation-id="promptOption" data-automation-label="United States of America (+1)">United States of America (+1)</p>
      </div></li></ul>''' if selected else ""
    _load(page, '''<label for="phone-code">Country / Territory Phone Code*</label>
      <div data-automation-id="multiSelectContainer" id="phone-owner">
        <div data-automation-id="multiselectInputContainer">
          <input id="phone-code" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="phone-owner" value="uncommitted query" aria-required="true">
        </div>''' + receipt + '</div>')
    field = next(f for f in read_fields([page.main]) if f.element_id == "phone-code")
    assert field.kind == "combobox" and field.required and not field.blank
    assert field.invalid is not selected
    profile = space.config.profile.model_copy(update={"country": "United States", "phone": "(555) 010-0000"})
    assert plan_for(field, replace(space.config, profile=profile)) is None
    assert _js(page, "() => document.querySelector('#phone-code').value") == "uncommitted query"


def test_oracle_required_pills_do_not_confuse_native_button_type_with_role(page, space):
    markup = ""
    for ident, label, required in (("history", "Have you worked for Example Employer?", True), ("title", "Title", False)):
        css = ' input-row__label--required' if required else ''
        markup += f'''<div class="input-row"><form-element-label>
          <label class="input-row__label{css}" for="{ident}">{label}<span class="c-spl-form-field-label-required-group"></span></label>
          </form-element-label><div class="input-row__control-container"><div>
          <ul role="radiogroup" aria-label="{label}">
            <li><button type="button" role="radio" aria-checked="false">Yes</button></li>
            <li><button type="button" role="radio" aria-checked="false">No</button></li>
          </ul></div></div></div>'''
    _load(page, markup)
    fields = {f.label: f for f in read_fields([page.main])}
    assert fields["Have you worked for Example Employer?"].required
    assert fields["Have you worked for Example Employer?"].blank
    assert plan_for(fields["Have you worked for Example Employer?"], space.config) is None
    assert not fields["Title"].required


@pytest.mark.parametrize("valid", [False, True])
def test_smartrecruiters_retained_resume_metadata_does_not_reupload_parser(page, valid):
    _load(page, '''<spl-dropzone data-test="apply-with-resume-container"></spl-dropzone>
      <spl-dropzone data-test="resume-upload"></spl-dropzone>''', '''
      for(const host of document.querySelectorAll('spl-dropzone')) {
        host.attachShadow({mode:'open'}).innerHTML='<label for="upload">Resume</label><input type="file" id="upload">';
        host.setAttribute('files',JSON.stringify([{fileName:'saved-resume.pdf',name:'saved-resume.pdf',attachmentType:'RESUME',fileId:'''
        + ("'12345678-1234-1234-1234-123456789abc'" if valid else "''") + '''}]));
      }
    ''')
    fields = [f for f in read_fields([page.main]) if f.kind == "file"]
    assert len(fields) == 2
    assert all(f.blank is not valid for f in fields)
    assert sum(f.in_autofill for f in fields) == 1
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize(("phone", "region", "expected"), [
    ("+442079460000", None, "+44"),
    ("+44 (20) 7946-0000", "United States", "+44"),
    ("+1 (202) 555-0123", None, "+1"),
    ("(202) 555-0123", "United States", "+1"),
    ("2025550123", "US", "+1"),
    ("2025550123", None, None),
    ("2025550123", "United Kingdom", None),
    ("(555) 010-0000", "United States", None),
    ("+999 123456789", "United States", None),
    ("+44 call me", "United States", None),
])
def test_phone_calling_code_uses_number_not_residence(page, space, phone, region, expected):
    _load(page, '<label for="code">Country / Territory Phone Code*</label>'
          '<input id="code" autocomplete="tel-country-code" required>')
    field = read_fields([page.main])[0]
    profile = space.config.profile.model_copy(update={"phone": phone, "country": region})
    plan = plan_for(field, replace(space.config, profile=profile))
    assert (plan.candidates[0] if plan else None) == expected
    assert _js(page, "() => document.querySelector('#code').value") == ""


def _oracle_receipt():
    return '''<div class="attachment-upload-button attachment-upload-button--filled">
      <div class="attachment-upload-button__container">
        <div class="attachment-upload-button__filled">
          <button type="button" class="attachment-upload-button__download"
            data-bind="text: model().fileName, click: downloadAttachment">saved-resume.pdf</button>
          <button type="button" class="attachment-upload-button__bottom-button"
            data-bind="text: removeButtonText, click: removeFile, 'aria-label': removeButtonText">Remove Resume</button>
        </div>
      </div>
    </div>'''


def _oracle_attachment(page, contents):
    _load(page, '''<input type="file" class="apply-flow-profile-import-awli__file-upload"
          aria-label="Autofill from resume">
      <div class="apply-flow-block apply-flow-block--file-upload">
        <div role="region" aria-label="Supporting Documents and URLs">
          <div class="file-upload-wrapper"><div class="file-upload-wrapper__section">
            <resume-upload-button>''' + contents + '''</resume-upload-button>
          </div></div>
          <label for="cover">Cover Letter</label><input id="cover" type="file">
        </div>
      </div>''')
    _js(page, "() => document.querySelector('form').className='apply-flow__content-form'")


def test_oracle_replaced_input_receipt_verifies_resume_without_parser(page, space, tmp_path):
    _oracle_attachment(page, '<label for="resume">Resume</label>'
                       '<input type="file" id="resume" name="attachment-upload" class="file-form-element__input">')
    fields = read_fields([page.main])
    attachment = next(f for f in fields if f.element_id == "resume")
    assert attachment.blank and attachment.attachment_only and is_resume_input(attachment)
    _js(page, """receipt => {
      document.querySelector('#resume').onchange=() => {
        document.querySelector('resume-upload-button').innerHTML=receipt;
      };
    }""", _oracle_receipt())
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\naccepted test attachment")
    upload(page.main, attachment.key, resume)
    final = read_fields([page.main])
    accepted = [f for f in final if is_resume_input(f)]
    assert len(accepted) == 1
    assert not accepted[0].blank and not accepted[0].invalid and accepted[0].attachment_only
    assert next(f for f in final if f.in_autofill).blank
    assert next(f for f in final if f.element_id == "cover").blank
    assert _js(page, "() => document.querySelector('#resume')") is None
    with pytest.raises(UnsafeAction):
        upload(page.main, accepted[0].key, resume)
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("state", [
    "filename-only", "missing-remove", "disabled-remove", "pending", "error", "cover-letter",
])
def test_oracle_resume_receipt_rejects_incomplete_or_other_attachment(page, state):
    receipt = _oracle_receipt()
    if state == "filename-only":
        receipt = "<p>saved-resume.pdf</p>"
    elif state == "missing-remove":
        receipt = receipt.replace("attachment-upload-button__bottom-button", "unrelated")
    elif state == "disabled-remove":
        receipt = receipt.replace('class="attachment-upload-button__bottom-button"',
                                  'disabled class="attachment-upload-button__bottom-button"')
    elif state == "pending":
        receipt += '<div role="progressbar">Uploading</div>'
    elif state == "error":
        receipt += '<div id="file-upload-errors-resume">Upload failed</div>'
    else:
        receipt = receipt.replace("Remove Resume", "Remove Cover Letter")
    _oracle_attachment(page, receipt)
    assert not any(is_resume_input(f) for f in read_fields([page.main]))
    assert _js(page, "() => window.submitted") == 0


def test_ashby_required_questions_remain_reportable_without_submit_choices(page, space):
    page.goto((FORMS / "ashby.html").as_uri())
    fields = {f.label: f for f in read_fields([page.main])}
    auth = fields["What is your work authorization status?"]
    in_person = fields["Are you able to work in-person in San Jose this summer?"]
    assert auth.required and auth.blank and auth.kind == "radio"
    assert in_person.required and in_person.blank and in_person.kind == "unsupported"
    assert plan_for(in_person, space.config) is None
    with pytest.raises(UnsafeAction):
        tick(page.main, in_person.key)
    with pytest.raises(UnsafeAction):
        fill_text(page.main, in_person.key, "Yes")
    assert not _js(page, "() => document.querySelector('input[name=yesno]').checked")


def test_unknown_required_button_question_is_report_only_even_with_profile_label(page, space):
    _load(page, '''<div role="group" aria-label="First Name" aria-required="true">
      <button type="submit">Yes</button><button type="submit">No</button>
    </div>''')
    field = read_fields([page.main])[0]
    assert field.kind == "unsupported" and field.required and field.label == "First Name"
    assert plan_for(field, space.config) is None
    with pytest.raises(UnsafeAction):
        pick_from_list(page.main, field.key, ("Yes",), location=False)
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("state", ["pending", "invalid", "wrong-kind", "mismatched-name", "duplicate"])
def test_smartrecruiters_retained_resume_requires_accepted_matching_metadata(page, state):
    _load(page, '<spl-dropzone data-test="resume-upload"></spl-dropzone>', '''
      const host=document.querySelector('spl-dropzone');
      host.attachShadow({mode:'open'}).innerHTML='<label for="resume">Resume</label><input id="resume" type="file">';
      host.setAttribute('files',JSON.stringify([{fileName:'saved-resume.pdf',name:'saved-resume.pdf',
        attachmentType:'RESUME',fileId:'12345678-1234-1234-1234-123456789abc'}]));
    ''')
    _js(page, """state => {
      const host=document.querySelector('spl-dropzone'), files=JSON.parse(host.getAttribute('files'));
      if(state==='pending')host.setAttribute('aria-busy','true');
      if(state==='invalid')host.shadowRoot.innerHTML+='<p aria-invalid="true">Upload failed</p>';
      if(state==='wrong-kind')files[0].attachmentType='COVER_LETTER';
      if(state==='mismatched-name')files[0].name='other.pdf';
      if(state==='duplicate')files.push({...files[0]});
      host.setAttribute('files',JSON.stringify(files));
    }""", state)
    field = next(f for f in read_fields([page.main]) if f.kind == "file")
    assert field.blank
    assert _js(page, "() => window.submitted") == 0


def _graduation_picker(page, options, *, label="What is your graduation date?", commit=True):
    choices = "".join(f'<div role="option" aria-selected="false">{option}</div>' for option in options)
    _load(page, f'''<label for="graduation">{label}*</label>
      <div class="select-shell"><div class="select__control">
        <div class="select__input-container"><input id="graduation" class="select__input"
          role="combobox" aria-haspopup="true" aria-expanded="false" aria-required="true"></div>
      </div><div id="graduation-options" role="listbox" hidden>{choices}</div></div>
    ''', f'''
      const box=document.querySelector('#graduation'), list=document.querySelector('#graduation-options');
      box.onclick=() => {{list.hidden=false;box.setAttribute('aria-controls',list.id);box.setAttribute('aria-expanded','true');}};
      box.onkeydown=e => {{if(e.key==='ArrowDown')box.onclick();}};
      for(const option of list.children)option.onclick=e => {{
        window.trusted.push(e.isTrusted);
        if(!e.isTrusted || {str(not commit).lower()})return;
        const value=document.createElement('div');value.className='select__single-value';
        value.textContent=option.textContent;document.querySelector('.select__control').prepend(value);
        box.value='';box.setAttribute('aria-expanded','false');list.remove();
      }};
    ''')
    return next(f for f in read_fields([page.main]) if f.element_id == "graduation")


def test_databricks_graduation_commits_unique_offered_range_bucket(page, space):
    from kitty.core.apply.filler import _apply_plan

    field = _graduation_picker(page, [
        "Earlier than Fall 2026", "Fall 2026", "Spring 2027", "Later than Summer 2027",
    ])
    profile = space.config.profile.model_copy(update={"graduation": "2030-05"})
    plan = plan_for(field, replace(space.config, profile=profile))
    assert plan is not None and plan.graduation == "2030-05"
    assert _apply_plan([page.main], field, plan)
    final = next(f for f in read_fields([page.main]) if f.element_id == "graduation")
    assert not final.blank and not final.invalid
    assert _js(page, "() => document.querySelector('.select__single-value').textContent") == "Later than Summer 2027"
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize(("date", "options", "commit"), [
    ("2027-06", ["Earlier than Fall 2026", "Spring 2027", "Later than Summer 2027"], True),
    ("2027-08", ["Earlier than Fall 2026", "Later than Summer 2027"], True),
    ("2026-09", ["Earlier than Fall 2026", "Later than Summer 2027"], True),
    ("2030-05", ["Later than Spring 2027", "Later than Summer 2027"], True),
    ("2030-05", ["Later than Summer 2027", "Later than Summer 2027"], True),
    ("2030-05", ["2028", "2029"], True),
    ("2030-05", ["Later than Summer 2027"], False),
])
def test_graduation_buckets_leave_gaps_boundaries_ambiguity_and_uncommitted_manual(
    page, space, date, options, commit,
):
    field = _graduation_picker(page, options, commit=commit)
    profile = space.config.profile.model_copy(update={"graduation": date})
    plan = plan_for(field, replace(space.config, profile=profile))
    assert plan is not None
    assert not pick_from_list(page.main, field.key, plan.candidates,
                              location=False, graduation=plan.graduation)
    assert next(f for f in read_fields([page.main]) if f.element_id == "graduation").blank
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("label", ["End date month", "End date year", "Employment end date", "GPA"])
def test_graduation_profile_never_supplies_unproven_education_or_employment_dates(page, space, label):
    field = _graduation_picker(page, ["May", "2030", "Later than Summer 2027"], label=label)
    profile = space.config.profile.model_copy(update={"graduation": "2030-05"})
    assert plan_for(field, replace(space.config, profile=profile)) is None
    assert _js(page, "() => window.trusted") == []
    assert _js(page, "() => window.submitted") == 0


def test_workday_phone_search_without_matching_owner_is_report_only(page, space):
    _load(page, '''<label for="code">Country / Territory Phone Code*</label>
      <div data-automation-id="multiSelectContainer" id="another-owner">
        <input id="code" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="phone-owner"
          value="United States" aria-required="true">
        <ul data-automation-id="selectedItemList" role="listbox" data-uxi-multiselect-id="another-owner">
          <li><p data-automation-id="promptOption" data-automation-label="United States (+1)">United States (+1)</p></li>
        </ul>
      </div>''')
    field = next(f for f in read_fields([page.main]) if f.element_id == "code")
    profile = space.config.profile.model_copy(update={"phone": "+1 202 555 0123"})
    assert field.kind == "unsupported" and field.required and field.blank and field.invalid
    assert plan_for(field, replace(space.config, profile=profile)) is None
    assert _js(page, "() => document.querySelector('#code').value") == "United States"
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("mutation", [
    "host.setAttribute('aria-disabled','true')",
    "host.setAttribute('aria-readonly','true')",
    "host.setAttribute('disabled','')",
    "host.setAttribute('readonly','')",
    "host.setAttribute('inert','')",
    "const action=document.createElement('button');action.type='submit';host.replaceWith(action);action.append(host)",
    "const action=document.createElement('a');action.href='#submit';host.replaceWith(action);action.append(host)",
])
def test_inactive_shadow_questions_are_not_revived_as_unsupported(page, mutation):
    _load(page, '<application-shell></application-shell>', '''
      const host=document.querySelector('application-shell');
      host.attachShadow({mode:'open'}).innerHTML=`
        <fieldset aria-required="true"><legend>Contact details*</legend>
          <label for="name">First name</label><input id="name" required>
        </fieldset>
        <div role="group" aria-label="Available in person?" aria-required="true">
          <button type="submit">Yes</button><button type="submit">No</button>
        </div>`;
    ''')
    fields = read_fields([page.main])
    assert any(f.label == "First name" and f.kind == "text" for f in fields)
    assert any(f.label == "Available in person?" and f.kind == "unsupported" for f in fields)
    _js(page, "() => {const host=document.querySelector('application-shell');" + mutation + ";}")
    assert read_fields([page.main]) == []
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("phone", [None, ""])
def test_country_code_without_saved_phone_has_no_plan(space, phone):
    from kitty.core.apply.fields import FormField

    field = FormField(key="code", kind="select", label="Country / Territory Phone Code",
                      autocomplete="tel-country-code", options=("+1", "+44"), required=True)
    profile = space.config.profile.model_copy(update={"phone": phone, "country": "United States"})
    assert plan_for(field, replace(space.config, profile=profile)) is None


@pytest.mark.parametrize(("saved", "offered"), [("TX", "Texas"), ("Texas", "TX")])
@pytest.mark.parametrize(("label", "autocomplete"), [
    ("State", ""), ("Home state", ""), ("Region", "address-level1"),
])
def test_contact_state_uses_saved_alias_and_existing_offered_choice(
    page, space, saved, offered, label, autocomplete,
):
    from kitty.core.apply.filler import _apply_plan

    _load(page, f'''<label for="state">{label}*</label>
      <select id="state" required autocomplete="{autocomplete}">
        <option value="">Select One</option><option value="ca">California</option>
        <option value="tx">{offered}</option>
      </select>''')
    field = next(f for f in read_fields([page.main]) if f.element_id == "state")
    profile = space.config.profile.model_copy(update={"home_state": saved})
    plan = plan_for(field, replace(space.config, profile=profile))
    assert _apply_plan([page.main], field, plan)
    final = next(f for f in read_fields([page.main]) if f.element_id == "state")
    assert not final.blank and not final.invalid
    assert _js(page, "() => document.querySelector('#state').value") == "tx"
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("label", [
    "State of birth", "Birth state", "Nationality state", "Employment state",
    "Employer state", "Work state", "Job state", "School state",
    "Street address", "ZIP code", "Phone Device Type",
])
@pytest.mark.parametrize("autocomplete", ["", "address-level1"])
def test_contact_state_does_not_invent_unrelated_facts(space, label, autocomplete):
    from kitty.core.apply.fields import FormField

    field = FormField(key="state", kind="select", label=label, autocomplete=autocomplete,
                      options=("Texas", "California"), required=True)
    profile = space.config.profile.model_copy(update={"home_state": "TX"})
    assert plan_for(field, replace(space.config, profile=profile)) is None


@pytest.mark.parametrize("saved", [None, "", "TX"])
def test_contact_state_needs_saved_fact_and_exact_offered_state(space, saved):
    from kitty.core.apply.fields import FormField, match_option

    field = FormField(key="state", kind="select", label="State",
                      options=("California", "Texas County"), required=True)
    profile = space.config.profile.model_copy(update={"home_state": saved})
    plan = plan_for(field, replace(space.config, profile=profile))
    if not saved:
        assert plan is None
    else:
        assert plan is not None
        assert match_option(field.options, plan.candidates, location=plan.location,
                            prefix=not plan.exact_choices) is None


def test_address_state_is_selected_before_dependent_city(page, space):
    from kitty.core.apply.filler import fill_form

    _load(page, '''
      <div class="input-row">
        <label for="city">City<span class="input-row__label--required-star"></span></label>
        <div class="cx-select-container">
          <input class="cx-select-input" id="city" role="combobox" aria-haspopup="grid"
            aria-controls="cities" aria-expanded="false" aria-required="true">
          <div id="cities" role="grid" hidden></div>
        </div>
      </div>
      <label for="state">State*</label>
      <select id="state" required><option value="">Select state</option><option>TX</option></select>
    ''', '''
      const city=document.querySelector('#city'), cities=document.querySelector('#cities'),
        state=document.querySelector('#state');
      city.onclick=() => {cities.hidden=false;city.setAttribute('aria-expanded','true');};
      state.onchange=() => {
        city.value='';city.closest('.input-row').classList.remove('input-row--filled');
        cities.innerHTML=state.value==='TX'
          ? '<div role="row"><div role="gridcell" tabindex="-1" aria-selected="false">Arlington, TX</div></div>'
          : '';
      };
      cities.onclick=e => {
        if (!e.isTrusted || !e.target.matches('[role=gridcell]') || state.value!=='TX') return;
        window.trusted.push(e.isTrusted);
        city.value=e.target.innerText;city.closest('.input-row').classList.add('input-row--filled');
        cities.remove();city.setAttribute('aria-expanded','false');
      };
    ''')

    result = fill_form(page, space.config, None)

    assert _js(page, "() => document.querySelector('#city').value") == "Arlington, TX"
    assert _js(page, "() => document.querySelector('#state').value") == "TX"
    assert result.unanswered == []
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => window.submitted") == 0


def test_saved_graduation_answer_authorizes_bucket_without_replacing_profile_date(page, space):
    from kitty.config import AnswerEntry, AnswersFile
    from kitty.core.apply.filler import _apply_plan

    field = _graduation_picker(page, ["Earlier than Fall 2026", "Later than Summer 2027"])
    config = replace(space.config, answers=AnswersFile(answers=[
        AnswerEntry(question="What is your graduation date?", answer="May 2030"),
    ]))
    plan = plan_for(field, config)
    assert plan is not None and plan.from_answers and plan.graduation == "2030-05"
    assert config.profile.graduation == "2032-05"
    assert _apply_plan([page.main], field, plan)
    assert _js(page, "() => document.querySelector('.select__single-value').textContent") == "Later than Summer 2027"
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize(("container_owner", "receipt_owner", "kind", "committed"), [
    ("phone-owner", "phone-owner", "combobox", True),
    ("other-owner", "phone-owner", "unsupported", False),
    ("phone-owner", "other-owner", "combobox", False),
])
def test_workday_unroled_phone_search_requires_matching_container_and_receipt_owner(
    page, space, container_owner, receipt_owner, kind, committed,
):
    _load(page, f'''<label for="code">Country / Territory Phone Code*</label>
      <div data-automation-id="multiSelectContainer" id="{container_owner}">
        <div data-automation-id="multiselectInputContainer">
          <div><input id="code" data-uxi-widget-type="selectinput"
            data-uxi-multiselect-id="phone-owner" aria-required="true"></div>
          <span data-automation-id="promptIcon" data-uxi-widget-type="selectinputicon"
            data-uxi-multiselect-id="phone-owner"></span>
          <ul data-automation-id="selectedItemList" role="listbox" data-uxi-multiselect-id="{receipt_owner}">
            <li><div role="option" data-automation-id="selectedItem">
              <p data-automation-id="promptOption" data-automation-label="United States of America (+1)">United States of America (+1)</p>
            </div></li>
          </ul>
        </div>
      </div>''', native_form=False)
    field = next(f for f in read_fields([page.main]) if f.element_id == "code")
    assert field.kind == kind and field.required and field.blank is not committed
    profile = space.config.profile.model_copy(update={"country": "United States", "phone": "2025550123"})
    plan = plan_for(field, replace(space.config, profile=profile))
    assert (plan.candidates[0] if plan else None) == ("+1" if kind == "combobox" else None)
    assert _js(page, "() => document.querySelector('#code').value") == ""
    assert _js(page, "() => window.submitted") == 0


def _flag_country_picker(page, *, receipt="us", query="", expanded=False, invalid=False):
    _load(page, '''
      <style>.iti__flag{display:inline-block;width:16px;height:10px}</style>
      <label for="flag-country">Country*</label>
      <div class="select__control" id="country-control">
        <input id="flag-country" role="combobox" aria-expanded="false" aria-required="true">
        <div class="iti__flag iti__us" id="unrelated-flag"></div>
      </div>
      <div class="select__single-value"><div class="iti__flag iti__us"></div>+1</div>
      <div id="country-options" role="listbox" hidden>
        <div role="option" aria-selected="false"><div class="iti__flag iti__us"></div>United States +1</div>
        <div role="option" aria-selected="false"><div class="iti__flag iti__ca"></div>Canada +1</div>
      </div>
    ''', f'''
      const box=document.querySelector('#flag-country'), list=document.querySelector('#country-options');
      box.onclick=() => {{list.hidden=false;box.setAttribute('aria-controls',list.id);box.setAttribute('aria-expanded','true');}};
      box.onkeydown=e => {{if(e.key==='ArrowDown')box.onclick();}};
      list.firstElementChild.onclick=e => {{
        window.trusted.push(e.isTrusted);if(!e.isTrusted)return;
        const value=document.createElement('div');value.className='select__single-value';
        value.innerHTML={('<div class="iti__flag iti__' + receipt + '"></div>' if receipt not in ('none', 'popup') else '')!r}+'<span>+1</span>';
        document.querySelector('#country-control').prepend(value);
        box.value={query!r};
        box.setAttribute('aria-expanded',{str(expanded).lower()!r});
        box.setAttribute('aria-invalid',{str(invalid).lower()!r});
        {'list.hidden=false;' if receipt == 'popup' else 'list.remove();'}
      }};
    ''')
    return next(f for f in read_fields([page.main]) if f.element_id == "flag-country")


def test_country_choice_proves_owned_flag_and_code_in_committed_renderer(page):
    from kitty.core.apply.page import _SUGGESTIONS_JS

    field = _flag_country_picker(page)
    assert field.blank
    assert pick_from_list(page.main, field.key, ("United States +1",), location=False)
    assert page.main.call(_SUGGESTIONS_JS, field.key, 0, "selected") == ["United States +1"]
    assert not next(f for f in read_fields([page.main]) if f.element_id == field.element_id).blank
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => window.submitted") == 0
    # A later manual country change with the same +1 code invalidates the proof.
    _js(page, "() => document.querySelector('#country-control .select__single-value .iti__flag').className='iti__flag iti__ca'")
    assert not pick_from_list(page.main, field.key, ("United States +1",), location=False)


@pytest.mark.parametrize(("receipt", "query", "expanded", "invalid"), [
    ("ca", "", False, False),
    ("none", "", False, False),
    ("popup", "", False, False),
    ("us", "United States", False, False),
    ("us", "", True, False),
    ("us", "", False, True),
])
def test_country_code_queries_unrelated_flags_and_popup_choices_are_not_identity(
    page, receipt, query, expanded, invalid,
):
    from kitty.core.apply.page import _SUGGESTIONS_JS

    field = _flag_country_picker(page, receipt=receipt, query=query, expanded=expanded, invalid=invalid)
    assert field.blank
    assert not pick_from_list(page.main, field.key, ("United States +1",), location=False)
    assert "United States +1" not in page.main.call(_SUGGESTIONS_JS, field.key, 0, "selected")
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("change", ["none", "user-selection", "closed"])
def test_full_fill_focuses_background_choice_and_preserves_new_user_focus(page, space, monkeypatch, change):
    from test_apply import _job
    import kitty.core.apply.filler as filler
    from kitty.core.apply.bidi import open_tab

    field = _graduation_picker(page, ["Later than Summer 2027"])
    _js(page, """() => {
      // Exercise the production flow on a recognized application, not an arbitrary form.
      document.querySelector('form').id='application';
      const option=document.querySelector('[role=option]'), commit=option.onclick;
      option.onclick=e => {if(document.visibilityState==='visible')commit(e);};
    }""")
    foreground = open_tab(page.bidi)
    replacement = None
    original = filler.fill_form

    def fill_then_change(*args, **kwargs):
        nonlocal replacement
        result = original(*args, **kwargs)
        if change == "user-selection":
            replacement = open_tab(page.bidi)
        elif change == "closed":
            foreground.close()
        return result

    monkeypatch.setattr(filler, "fill_form", fill_then_change)
    try:
        assert page.main.call("() => document.visibilityState") == "hidden"
        report = filler.fill_job(page, space.conn, space.settings, space.config,
                                 _job(space, "greenhouse"), resume_tab=True)
        assert report.pages == 1
        assert "What is your graduation date?" not in report.unanswered
        final = next(f for f in read_fields([page.main]) if f.key == field.key)
        assert not final.blank and not final.invalid
        assert _js(page, "() => document.querySelector('.select__single-value').textContent") == "Later than Summer 2027"
        assert _js(page, "() => window.trusted") == [True]
        assert _js(page, "() => window.submitted") == 0
        assert report.status != "ready"  # a committed choice cannot prove the missing resume
        visible = replacement if change == "user-selection" else page if change == "closed" else foreground
        assert visible.main.call("() => document.visibilityState") == "visible"
    finally:
        if replacement is not None:
            replacement.close()
        if change != "closed":
            foreground.close()


@pytest.mark.parametrize(("label", "company", "expected"), [
    ("Have you ever worked for PwC, one of our current brands, or any of our predecessor firms?",
     "PricewaterhouseCoopers (PwC)", True),
    ("Have you ever worked for Fixture Co?", "Fixture Co", True),
    ("Have you ever worked for Other Co?", "Fixture Co", False),
    ("Have you ever worked for a government agency?", "Fixture Co", False),
    ("Have you ever worked for Fixture Co as a government official?", "Fixture Co", False),
    ("Have you ever served in the military?", "Fixture Co", False),
    ("Do you plan to work for Fixture Co?", "Fixture Co", False),
])
def test_named_previous_employer_uses_only_explicit_generic_saved_history(space, label, company, expected):
    from kitty.config import AnswerEntry, AnswersFile
    from kitty.core.apply.fields import FormField

    config = replace(space.config, answers=AnswersFile(answers=[
        AnswerEntry(question="Have you previously been employed by this company?", answer="No",
                    match=["worked for this company"], exclude_companies=["Excluded Co"]),
    ]))
    field = FormField(key="history", kind="radio", label=label, options=("Yes", "No"), required=True)
    plan = plan_for(field, config, company=company)
    assert bool(plan) is expected
    if plan:
        assert plan.from_answers and plan.candidates == ("No",)
    assert plan_for(field, replace(config, answers=AnswersFile(answers=[])), company=company) is None
    assert plan_for(FormField(key="excluded", kind="radio", label="Have you ever worked for Excluded Co?"),
                    config, company="Excluded Co") is None


@pytest.mark.parametrize("scoped", [False, True])
def test_named_employer_history_preserves_specific_answers_and_ambiguous_exact_matches(space, scoped):
    from kitty.config import AnswerEntry, AnswersFile
    from kitty.core.apply.fields import FormField

    question = "Have you ever worked for Fixture Co?"
    entries = [
        AnswerEntry(question="Generic employer history", answer="No", match=["worked for this company"]),
        AnswerEntry(question=question, answer="Yes", companies=["Fixture Co"] if scoped else []),
    ]
    field = FormField(key="history", kind="radio", label=question, options=("Yes", "No"))
    config = replace(space.config, answers=AnswersFile(answers=entries))
    assert plan_for(field, config, company="Fixture Co").candidates == ("Yes",)
    ambiguous = replace(config, answers=AnswersFile(answers=[
        *entries, entries[-1].model_copy(update={"answer": "No"}),
    ]))
    assert plan_for(field, ambiguous, company="Fixture Co") is None


def _portal_source_picker(page, *, owner="source-owner", duplicate=False, commit=True):
    portal = f'''<div data-automation-id="responsiveMonikerPrompt"
      data-automation-type="singleSelectPrompt" data-associated-widget="{owner}" aria-busy="true">
      <div role="option" aria-selected="false" hidden>Company careers page</div></div>'''
    _load(page, '''<label for="source--source">How Did You Hear About Us?*</label>
      <div data-automation-id="multiSelectContainer" id="source-owner">
        <input id="source--source" data-uxi-widget-type="selectinput"
          data-uxi-multiselect-id="source-owner" aria-required="true">
      </div>''' + portal + (portal if duplicate else ""), '''
      const box=document.querySelector('input'), portals=document.querySelectorAll('[data-associated-widget]');
      for(const portal of portals) {
        // Stay pending beyond the initial list probe and empty-search grace period.
        setTimeout(() => {portal.setAttribute('aria-busy','false');portal.firstElementChild.hidden=false;},4000);
        portal.firstElementChild.onclick=e => {
          window.trusted.push(e.isTrusted);if(!e.isTrusted || !__COMMIT__)return;
          const receipt=document.createElement('ul');
          receipt.setAttribute('data-automation-id','selectedItemList');
          receipt.setAttribute('data-uxi-multiselect-id','source-owner');
          receipt.innerHTML='<li><div role="option"><p data-automation-id="promptOption" data-automation-label="Company careers page">Company careers page</p></div></li>';
          document.querySelector('#source-owner').append(receipt);box.value='';portal.remove();
        };
      }
    '''.replace("__COMMIT__", str(commit).lower()))
    return next(f for f in read_fields([page.main]) if f.element_id == "source--source")


def _radio_portal_source_picker(page):
    field = _portal_source_picker(page)
    _js(page, """() => {
      const portal=document.querySelector('[data-associated-widget]');
      portal.setAttribute('aria-busy','false');
      const option=portal.firstElementChild;option.hidden=false;
      option.innerHTML=`<div data-automation-id="promptLeafNode"
        data-uxi-widget-type="multiselectlistitem" data-uxi-multiselect-id="source-owner"
        data-uxi-multiselectlistitem-hassidecharm="false">
          <input type="radio" data-automation-id="radioBtn">
          <div data-automation-id="promptOption">Company careers page</div>
        </div>`;
    }""")
    return field


def test_workday_owned_radio_option_commits_saved_choice_with_real_pointer(page, space):
    from kitty.config import AnswerEntry, AnswersFile
    from kitty.core.apply.filler import _apply_plan

    field = _radio_portal_source_picker(page)
    config = replace(space.config, answers=AnswersFile(answers=[
        AnswerEntry(question=field.label, answer="Company careers page"),
    ]))

    assert _apply_plan([page.main], field, plan_for(field, config))
    final = next(f for f in read_fields([page.main]) if f.element_id == field.element_id)
    assert not final.blank and not final.invalid
    assert _js(page, """() => document.querySelector(
      '#source-owner [data-automation-id=promptOption]'
    ).getAttribute('data-automation-label')""") == "Company careers page"
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("mutation", [
    "leaf.setAttribute('data-uxi-multiselect-id','another-question')",
    "radio.removeAttribute('data-automation-id')",
    "radio.type='submit'",
    "radio.type='checkbox'",
    "radio.disabled=true",
    "leaf.insertAdjacentHTML('beforeend','<button>Submit</button>')",
])
def test_workday_radio_options_reject_foreign_or_action_controls(page, mutation):
    field = _radio_portal_source_picker(page)
    _js(page, """() => {
      const leaf=document.querySelector('[data-automation-id=promptLeafNode]'),
        radio=leaf.querySelector('input');
      """ + mutation + ";}")

    assert not pick_from_list(page.main, field.key, ("Company careers page",), location=False)
    assert next(f for f in read_fields([page.main]) if f.element_id == field.element_id).blank
    assert _js(page, "() => window.trusted") == []
    assert _js(page, "() => window.submitted") == 0


def test_workday_source_waits_for_exact_owned_portal_and_requires_selection_receipt(page, space):
    from kitty.config import AnswerEntry, AnswersFile
    from kitty.core.apply.filler import _apply_plan

    field = _portal_source_picker(page)
    config = replace(space.config, answers=AnswersFile(answers=[
        AnswerEntry(question="How Did You Hear About Us?", answer="Company careers page"),
    ]))
    assert field.blank and _apply_plan([page.main], field, plan_for(field, config))
    assert _js(page, """() => document.querySelector(
      '#source-owner [data-automation-id=promptOption]'
    ).getAttribute('data-automation-label')""") == "Company careers page"
    assert _js(page, "() => document.querySelector('#source--source').value") == ""
    final = next(f for f in read_fields([page.main]) if f.element_id == field.element_id)
    assert not final.blank and not final.invalid
    assert not _js(page, "() => !!document.querySelector('[data-associated-widget]')")
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => window.submitted") == 0


def test_workday_source_owned_portal_click_without_receipt_does_not_fill(page, space):
    from kitty.config import AnswerEntry, AnswersFile
    from kitty.core.apply.filler import _apply_plan

    field = _portal_source_picker(page, commit=False)
    config = replace(space.config, answers=AnswersFile(answers=[
        AnswerEntry(question="How Did You Hear About Us?", answer="Company careers page"),
    ]))
    assert not _apply_plan([page.main], field, plan_for(field, config))
    assert next(f for f in read_fields([page.main]) if f.element_id == field.element_id).blank
    assert _js(page, "() => document.querySelector('#source--source').value") == ""
    assert not _js(page, "() => !!document.querySelector('#source-owner [data-automation-id=selectedItemList]')")
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize(("owner", "duplicate"), [("another-owner", False), ("source-owner", True)])
def test_workday_source_rejects_unrelated_and_ambiguous_portal_owners(page, owner, duplicate):
    field = _portal_source_picker(page, owner=owner, duplicate=duplicate)
    _js(page, """() => {
      for (const portal of document.querySelectorAll('[data-associated-widget]')) {
        portal.setAttribute('aria-busy','false');portal.firstElementChild.hidden=false;
      }
    }""")
    assert not pick_from_list(page.main, field.key, ("Company careers page",), location=False)
    assert next(f for f in read_fields([page.main]) if f.element_id == field.element_id).blank
    assert not _js(page, "() => !!document.querySelector('#source-owner [data-automation-id=selectedItemList]')")
    assert _js(page, "() => document.querySelector('#source--source').value") == ""
    assert _js(page, "() => window.trusted") == []
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize(("label", "options"), [
    ("What is your GPA?", ["2.4 or below", "2.5 - 2.7 (out of 4.0)", "3.6 or above (out of 4.0)"]),
    ("Discipline", ["Engineering", "Engineering, Other", "Electrical Engineering Technology"]),
])
def test_saved_unmapped_gpa_and_specific_major_never_commit_broader_choices(page, space, label, options):
    from kitty.config import AnswerEntry, AnswersFile
    from kitty.core.apply.fields import match_option

    field = _graduation_picker(page, options, label=label)
    profile = space.config.profile.model_copy(update={
        "major": "Electrical Engineering", "discipline": "Electrical Engineering",
    })
    config = replace(space.config, profile=profile, answers=AnswersFile(answers=[
        AnswerEntry(question="What is your GPA?", answer="N/A (first semester, no GPA yet)",
                    choices=["Not Applicable", "N/A"]),
    ]))
    plan = plan_for(field, config)
    assert plan is not None
    assert match_option(plan.candidates, options, prefix=not plan.exact_choices) is None
    assert not pick_from_list(page.main, field.key, plan.candidates, location=False)
    assert next(f for f in read_fields([page.main]) if f.key == field.key).blank
    assert _js(page, "() => window.trusted") == []
    assert _js(page, "() => window.submitted") == 0


def _native_workday_source(page, *, commit=True, tree=None):
    """Observed Workday owner/icon/type=2 category/clickable-header contract."""
    if tree is None:
        tree = {
            "root": ["Website", "PwC Job Board"],
            "Website": ["I found the job on a job board", "I was referred by a search engine"],
            "PwC Job Board": ["Company careers page"],
        }
    _load(page, '''
      <label for="source--source">How Did You Hear About Us?*</label>
      <div data-automation-id="multiSelectContainer" id="source-owner">
        <input id="source--source" data-uxi-widget-type="selectinput"
          data-uxi-multiselect-id="source-owner" value="stale query" aria-required="true"
          style="position:absolute;width:1px;height:1px;opacity:0">
        <span data-automation-id="promptSearchButton" data-uxi-widget-type="selectinputicon"
          data-uxi-selectinputicon-type="promptSearchButton" data-uxi-multiselect-id="source-owner"
          style="display:inline-block;padding:12px;border:1px solid">List</span>
      </div>
      <label for="manual">Manual answer</label><input id="manual" value="Do not replace">
      <div data-automation-id="responsiveMonikerPrompt" data-automation-type="singleSelectPrompt"
        data-associated-widget="source-owner" hidden></div>
    ''', '''
      const box=document.querySelector('#source--source'),
        owner=document.querySelector('#source-owner'),
        portal=document.querySelector('[data-associated-widget]'),
        icon=owner.querySelector('[data-automation-id=promptSearchButton]');
      window.navigation=[];window.queryChanges=0;
      const tree=__TREE__;
      window.renderMenu=name => {
        portal.hidden=false;portal.innerHTML='';
        portal.removeAttribute('aria-busy');
        if(name!=='root') {
          const header=document.createElement('div');
          header.setAttribute('data-uxi-widget-type','multiselectlistheader');
          header.setAttribute('data-uxi-multiselect-id','source-owner');
          header.setAttribute('data-uxi-multiselectlistheader-clickable','true');
          header.innerHTML='<span data-automation-id="backButton" style="display:inline-block;padding:12px">Back</span>'+
            '<div data-automation-id="promptTitle">'+name+'</div>';
          header.querySelector('span').onclick=e=>{
            window.navigation.push(['back',e.isTrusted]);
            const parent=Object.keys(tree).find(key=>tree[key].includes(name));
            if(e.isTrusted && parent)window.renderMenu(parent);
          };
          portal.append(header);
        }
        for(const text of tree[name]) {
          const category=Object.hasOwn(tree,text), option=document.createElement('div');
          option.setAttribute('role','option');option.setAttribute('aria-selected','false');
          option.setAttribute('data-automation-id','menuItem');
          option.setAttribute('data-association-widget','source-owner');
          option.style.cssText='padding:12px;border:1px solid;max-width:400px';
          option.innerHTML='<div data-automation-id="promptLeafNode" data-uxi-widget-type="multiselectlistitem" '+
            'data-uxi-multiselect-id="source-owner" data-uxi-multiselectlistitem-type="'+(category?'2':'1')+'" '+
            'data-uxi-multiselectlistitem-hassidecharm="'+category+'">'+
            (category?'':'<input type="radio" data-automation-id="radioBtn">')+
            '<div data-automation-id="promptOption" data-automation-label="'+text+'">'+text+'</div></div>';
          option.onclick=e=>{
            if(category) {
              window.navigation.push([text,e.isTrusted]);
              if(e.isTrusted)window.renderMenu(text);
              return;
            }
            window.trusted.push(e.isTrusted);
            if(!e.isTrusted || !__COMMIT__)return;
            const receipt=document.createElement('ul');
            receipt.setAttribute('data-automation-id','selectedItemList');
            receipt.setAttribute('data-uxi-multiselect-id','source-owner');
            receipt.innerHTML='<li><div role="option"><p data-automation-id="promptOption" '+
              'data-automation-label="'+text+'">'+text+'</p></div></li>';
            owner.append(receipt);box.value='';portal.hidden=true;
          };
          portal.append(option);
        }
      };
      // This widget's text-query endpoint fails; only its native list opens.
      box.oninput=()=>{window.queryChanges++;portal.hidden=false;portal.innerHTML='';};
      icon.onclick=e=>{
        window.navigation.push(['open',e.isTrusted]);
        if(e.isTrusted)window.renderMenu('root');
      };
    '''.replace("__COMMIT__", str(commit).lower()).replace("__TREE__", json.dumps(tree)))
    return next(f for f in read_fields([page.main]) if f.element_id == "source--source")


@pytest.mark.parametrize("query", ["Company careers page", ""])
def test_workday_native_list_recovers_failed_search_and_commits_exact_configured_alias(page, space, query):
    from kitty.config import AnswerEntry, AnswersFile
    from kitty.core.apply.filler import _apply_plan

    field = _native_workday_source(page)
    # Reproduce the failed text-query path before using the native list control.
    fill_text(page.main, field.key, query)
    assert _js(page, "() => window.queryChanges") >= 1
    assert _js(page, "() => document.querySelector('[data-associated-widget]').children.length") == 0
    # Close the failed query popup exactly as a user can, without clearing text.
    _js(page, "() => document.querySelector('[data-associated-widget]').hidden=true")
    changes = _js(page, "() => window.queryChanges")
    config = replace(space.config, answers=AnswersFile(answers=[
        AnswerEntry(question=field.label, answer="Employer careers site", choices=["Company careers page"]),
    ]))

    assert _apply_plan([page.main], field, plan_for(field, config))
    final = next(f for f in read_fields([page.main]) if f.element_id == field.element_id)
    assert not final.blank and not final.invalid
    assert _js(page, "() => window.navigation") == [
        ["open", True], ["Website", True], ["back", True], ["PwC Job Board", True],
        ["back", True], ["PwC Job Board", True],
    ]
    assert _js(page, "() => window.queryChanges") == changes
    assert _js(page, "() => document.querySelector('#source-owner [data-automation-label]').getAttribute('data-automation-label')") == "Company careers page"
    assert _js(page, "() => document.querySelector('#manual').value") == "Do not replace"
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => window.submitted") == 0


def test_workday_native_list_returns_from_existing_child_without_clearing_query(page):
    field = _native_workday_source(page)
    _js(page, "() => window.renderMenu('Website')")
    assert pick_from_list(page.main, field.key, ("Company careers page",), location=False)
    assert _js(page, "() => window.navigation")[0] == ["back", True]
    assert _js(page, "() => window.queryChanges") == 0
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("commit", [False, True])
def test_workday_native_list_never_substitutes_category_or_unconfigured_leaf(page, commit):
    from kitty.core.apply.page import pick_from_search

    field = _native_workday_source(page, commit=commit)
    candidates = ("Company careers page",) if not commit else ("Website",)
    reason = "fully inspected hierarchy" if commit else "selection was not committed"
    with pytest.raises(ChoiceSelectionError, match=reason):
        pick_from_search(page.main, field.key, "Website", candidates, location=False)
    final = next(f for f in read_fields([page.main]) if f.element_id == field.element_id)
    assert final.invalid  # Preserved query text is not a committed answer.
    assert not _js(page, "() => !!document.querySelector('#source-owner [data-automation-id=selectedItemList]')")
    assert _js(page, "() => document.querySelector('#source--source').value") == "stale query"
    assert _js(page, "() => document.querySelector('#manual').value") == "Do not replace"
    assert _js(page, "() => window.queryChanges") == 0
    assert _js(page, "() => window.trusted") == ([] if commit else [True])
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("mutation", [
    "icon.setAttribute('data-uxi-multiselect-id','foreign-owner')",
    "icon.after(icon.cloneNode(true))",
    "icon.setAttribute('role','button')",
    "icon.innerHTML='<button type=submit>Submit</button>'",
    "icon.outerHTML='<button type=submit '+Array.from(icon.attributes).map(a=>a.name+'=\"'+a.value+'\"').join(' ')+'>Submit</button>'",
    "icon.insertAdjacentHTML('beforebegin','<button type=submit id=wrapper></button>');document.querySelector('#wrapper').append(icon)",
    "owner.after(owner.cloneNode(true))",
    "icon.style.position='relative';icon.insertAdjacentHTML('afterend','<div style=\"position:fixed;inset:0;background:white;z-index:1000\">Overlay</div>')",
])
def test_workday_native_opener_rejects_foreign_ambiguous_actions_and_overlays(page, mutation):
    field = _native_workday_source(page)
    _js(page, """() => {
      const owner=document.querySelector('#source-owner'),
        icon=owner.querySelector('[data-automation-id=promptSearchButton]');
      """ + mutation + ";}")
    with pytest.raises(ChoiceSelectionError, match="hierarchy is unavailable or incomplete"):
        pick_from_list(page.main, field.key, ("Company careers page",), location=False)
    assert _js(page, "() => window.navigation") == []
    assert _js(page, "() => window.queryChanges") == 0
    assert _js(page, "() => document.querySelector('#source--source').value") == "stale query"
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("mutation", [
    "owner.setAttribute('aria-disabled','true')",
    "owner.setAttribute('inert','')",
    "box.readOnly=true",
])
def test_workday_native_opener_preserves_disabled_and_readonly_widgets(page, mutation):
    field = _native_workday_source(page)
    _js(page, """() => {
      const owner=document.querySelector('#source-owner'),box=owner.querySelector('input');
      """ + mutation + ";}")
    with pytest.raises(UnsafeAction):
        pick_from_list(page.main, field.key, ("Company careers page",), location=False)
    assert _js(page, "() => window.navigation") == []
    assert _js(page, "() => window.queryChanges") == 0
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("mutation", [
    "for(const option of portal.querySelectorAll('[role=option]'))option.setAttribute('data-association-widget','foreign')",
    "for(const leaf of portal.querySelectorAll('[data-automation-id=promptLeafNode]'))leaf.setAttribute('data-uxi-multiselect-id','foreign')",
    "for(const leaf of portal.querySelectorAll('[data-automation-id=promptLeafNode]'))leaf.insertAdjacentHTML('beforeend','<input type=checkbox aria-label=Consent>')",
    "for(const leaf of portal.querySelectorAll('[data-automation-id=promptLeafNode]'))leaf.insertAdjacentHTML('beforeend','<button type=submit>Submit</button>')",
    "for(const leaf of portal.querySelectorAll('[data-automation-id=promptLeafNode]'))leaf.insertAdjacentHTML('beforeend','<textarea>Other answer</textarea>')",
    "for(const leaf of portal.querySelectorAll('[data-automation-id=promptLeafNode]'))leaf.setAttribute('data-uxi-multiselectlistitem-hassidecharm','false')",
    "for(const option of portal.querySelectorAll('[role=option]'))option.setAttribute('aria-disabled','true')",
    "portal.after(portal.cloneNode(true))",
])
def test_workday_native_categories_reject_foreign_ambiguous_and_action_controls(page, mutation):
    field = _native_workday_source(page)
    _js(page, """() => {
      window.renderMenu('root');const portal=document.querySelector('[data-associated-widget]');
      """ + mutation + ";}")
    with pytest.raises(ChoiceSelectionError, match="hierarchy is unavailable or incomplete"):
        pick_from_list(page.main, field.key, ("Company careers page",), location=False)
    assert _js(page, "() => window.navigation") == []
    assert _js(page, "() => window.trusted") == []
    assert _js(page, "() => window.queryChanges") == 0
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("mutation", [
    "header.setAttribute('data-uxi-multiselect-id','foreign')",
    "header.setAttribute('data-uxi-multiselectlistheader-clickable','false')",
    "header.setAttribute('role','button')",
    "header.setAttribute('aria-disabled','true')",
    "header.after(header.cloneNode(true))",
    "header.querySelector('span').outerHTML='<button type=submit data-automation-id=backButton>Back</button>'",
])
def test_workday_native_back_requires_owned_unambiguous_nonaction_header(page, mutation):
    field = _native_workday_source(page)
    _js(page, """() => {
      window.renderMenu('Website');
      const header=document.querySelector('[data-uxi-widget-type=multiselectlistheader]');
      """ + mutation + ";}")
    with pytest.raises(ChoiceSelectionError, match="hierarchy is unavailable or incomplete"):
        pick_from_list(page.main, field.key, ("Company careers page",), location=False)
    assert _js(page, "() => window.navigation") == []
    assert _js(page, "() => window.queryChanges") == 0
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("error", [False, True])
def test_workday_native_terminal_empty_or_error_does_not_clear_or_retry(page, error):
    field = _native_workday_source(page)
    _js(page, """error => {
      const portal=document.querySelector('[data-associated-widget]');
      portal.hidden=false;portal.innerHTML=error?'<div role=alert>Unable to load options</div>':'';
    }""", error)
    with pytest.raises(ChoiceSelectionError, match="hierarchy is unavailable or incomplete"):
        pick_from_list(page.main, field.key, ("Company careers page",), location=False)
    assert _js(page, "() => window.navigation") == []
    assert _js(page, "() => window.queryChanges") == 0
    assert _js(page, "() => document.querySelector('#source--source').value") == "stale query"
    assert _js(page, "() => window.submitted") == 0


def test_workday_native_committed_receipt_prevents_reopening_or_overwriting(page):
    from kitty.core.apply.page import pick_from_search

    field = _native_workday_source(page)
    assert pick_from_list(page.main, field.key, ("Company careers page",), location=False)
    navigation = _js(page, "() => window.navigation")
    with pytest.raises(ChoiceSelectionError, match="different committed selection"):
        pick_from_list(page.main, field.key, ("Another source",), location=False)
    assert pick_from_search(page.main, field.key, "unrelated query", ("Company careers page",), location=False)
    assert _js(page, "() => window.navigation") == navigation
    assert _js(page, "() => window.queryChanges") == 0
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => document.querySelector('#manual').value") == "Do not replace"
    assert _js(page, "() => window.submitted") == 0


def test_workday_native_existing_matching_leaf_requires_complete_root_discovery(page):
    field = _native_workday_source(page)
    _js(page, "() => window.renderMenu('PwC Job Board')")
    assert pick_from_list(page.main, field.key, ("Company careers page",), location=False)
    assert _js(page, "() => window.navigation") == [
        ["back", True], ["Website", True], ["back", True], ["PwC Job Board", True],
        ["back", True], ["PwC Job Board", True],
    ]
    assert _js(page, "() => window.queryChanges") == 0
    assert _js(page, "() => window.trusted") == [True]
    assert not next(f for f in read_fields([page.main]) if f.element_id == field.element_id).blank
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("start", ["root", "Saved"])
def test_workday_native_duplicate_leaf_in_separate_categories_never_commits(page, start):
    field = _native_workday_source(page, tree={
        "root": ["Saved", "Other source"],
        "Saved": ["Company careers page"],
        "Other source": ["Company careers page"],
    })
    _js(page, "name => window.renderMenu(name)", start)

    with pytest.raises(ChoiceSelectionError, match="saved choice is ambiguous"):
        pick_from_list(page.main, field.key, ("Company careers page",), location=False)
    final = next(f for f in read_fields([page.main]) if f.element_id == field.element_id)
    assert final.invalid
    assert not _js(page, "() => !!document.querySelector('#source-owner [data-automation-id=selectedItemList]')")
    assert _js(page, "() => window.trusted") == []
    assert _js(page, "() => window.queryChanges") == 0
    assert _js(page, "() => document.querySelector('#source--source').value") == "stale query"
    assert _js(page, "() => document.querySelector('#manual').value") == "Do not replace"
    assert all(trusted for _, trusted in _js(page, "() => window.navigation"))
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize("failure", [
    "error", "loading", "empty", "inaccessible", "hidden-branch", "missing-back", "depth-limit",
])
def test_workday_native_matching_leaf_cannot_commit_before_complete_discovery(page, failure):
    tree = {"root": ["Company careers page", "Other"], "Other": ["Unrelated"]}
    if failure == "depth-limit":
        tree["Other"] = ["Level 1"]
        for depth in range(1, 9):
            tree[f"Level {depth}"] = [f"Level {depth + 1}"] if depth < 8 else ["Unrelated"]
    field = _native_workday_source(page, tree=tree)
    _js(page, """failure => {
      const render=window.renderMenu;
      window.renderMenu=name=>{
        render(name);
        const portal=document.querySelector('[data-associated-widget]');
        if(name==='root' && failure==='inaccessible')
          Array.from(portal.children).find(e=>e.textContent==='Other').style.pointerEvents='none';
        if(name==='root' && failure==='hidden-branch')
          Array.from(portal.children).find(e=>e.textContent==='Other').hidden=true;
        if(name!=='Other')return;
        if(failure==='error')
          portal.insertAdjacentHTML('beforeend','<div role=alert>Unable to load options</div>');
        if(failure==='loading')portal.setAttribute('aria-busy','true');
        if(failure==='empty')
          portal.querySelectorAll('[role=option]').forEach(e=>e.remove());
        if(failure==='missing-back')portal.querySelector('[data-automation-id=backButton]').remove();
      };
    }""", failure)

    with pytest.raises(ChoiceSelectionError, match="hierarchy is unavailable or incomplete"):
        pick_from_list(page.main, field.key, ("Company careers page",), location=False)
    final = next(f for f in read_fields([page.main]) if f.element_id == field.element_id)
    assert final.invalid
    assert not _js(page, "() => !!document.querySelector('#source-owner [data-automation-id=selectedItemList]')")
    assert _js(page, "() => window.trusted") == []
    assert _js(page, "() => window.queryChanges") == 0
    assert _js(page, "() => document.querySelector('#source--source').value") == "stale query"
    assert _js(page, "() => document.querySelector('#manual').value") == "Do not replace"
    assert all(trusted for _, trusted in _js(page, "() => window.navigation"))
    assert _js(page, "() => window.submitted") == 0


def test_workday_native_unique_saved_leaf_commits_only_after_later_branch_is_checked(page, space):
    from kitty.config import AnswerEntry, AnswersFile
    from kitty.core.apply.filler import _apply_plan

    field = _native_workday_source(page, tree={
        "root": ["Saved", "Later source"],
        "Saved": ["Company careers page"],
        "Later source": ["A different answer"],
    })
    config = replace(space.config, answers=AnswersFile(answers=[
        AnswerEntry(question=field.label, answer="Company careers page"),
    ]))

    assert _apply_plan([page.main], field, plan_for(field, config))
    assert _js(page, "() => window.navigation") == [
        ["open", True], ["Saved", True], ["back", True],
        ["Later source", True], ["back", True], ["Saved", True],
    ]
    final = next(f for f in read_fields([page.main]) if f.element_id == field.element_id)
    assert not final.blank and not final.invalid
    assert _js(page, "() => document.querySelector('#source-owner [data-automation-label]').getAttribute('data-automation-label')") == "Company careers page"
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => window.queryChanges") == 0
    assert _js(page, "() => document.querySelector('#manual').value") == "Do not replace"
    assert _js(page, "() => window.submitted") == 0


def test_workday_native_uncommitted_saved_choice_stops_plan_without_retrying(page, space):
    from kitty.config import AnswerEntry, AnswersFile
    from kitty.core.apply.filler import _apply_plan

    field = _native_workday_source(page, commit=False)
    config = replace(space.config, answers=AnswersFile(answers=[
        AnswerEntry(question=field.label, answer="Employer careers site",
                    choices=["Company careers page"]),
    ]))

    with pytest.raises(ChoiceSelectionError, match="selection was not committed"):
        _apply_plan([page.main], field, plan_for(field, config))
    final = next(f for f in read_fields([page.main]) if f.element_id == field.element_id)
    assert final.invalid
    assert not _js(page, "() => !!document.querySelector('#source-owner [data-automation-id=selectedItemList]')")
    assert _js(page, "() => window.navigation") == [
        ["open", True], ["Website", True], ["back", True],
        ["PwC Job Board", True], ["back", True], ["PwC Job Board", True],
    ]
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => window.queryChanges") == 0
    assert _js(page, "() => document.querySelector('#source--source').value") == "stale query"
    assert _js(page, "() => document.querySelector('#manual').value") == "Do not replace"
    assert _js(page, "() => window.submitted") == 0
