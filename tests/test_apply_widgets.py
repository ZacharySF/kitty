"""Per-widget handlers, branching re-reads, uploads and frame re-acquisition
(proposal-09 Step 7.1 and the upload/branching parts of 7.2).

Every page is a synthetic local fixture served by the SubmitTrap inside a
`<form method=post action=/trap/submit>`, filled in an armed tab (L0 intents,
L1 veto, L2 tripwire) and checked by read-back. Nothing is ever submitted: each
test ends with no write at the trap and an empty veto log.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from kitty.config import AnswerEntry, AnswersFile
from kitty.core import jobs as jobs_core
from kitty.core.apply import page as page_module
from kitty.core.apply import preflight
from kitty.core.apply.bidi import TabClosed, open_tab
from kitty.core.apply.browser import PREFS, _chrome_eval, write_prefs
from kitty.core.apply.fields import plan_for
from kitty.core.apply.filler import _apply_plan, _reacquire, _retained_tab, fill_form, fill_job
from kitty.core.apply.page import ChoiceSelectionError, form_frames, read_fields, settle, uploads_pending
from kitty.core.apply.state import record_attempt
from submit_trap import owner_click
from test_apply import _forms_trap, _js, firefox, page, space  # noqa: F401

_HEAD = "<!doctype html><html><head><meta charset=utf-8><title>Application</title>" \
        "<style>.iti__hide{display:none}</style></head><body>"
_COUNTERS = """<script>
window.submitted = 0; window.enters = 0; window.trusted = [];
document.addEventListener('submit', () => window.submitted++, true);
document.addEventListener('keydown', e => { if (e.key === 'Enter') window.enters++; }, true);
</script>"""


def _serve(page, body: str, script: str = "", *, path: str = "/widget") -> str:  # noqa: F811
    """Serve a synthetic page from the trap and open it in an armed tab; returns its URL."""
    trap = _forms_trap()
    trap.pages[path] = (_HEAD + "<form method=post action=/trap/submit>" + body +
                        "<button type=submit>Submit application</button></form>" + _COUNTERS +
                        "<script>" + script + "</script></body></html>")
    page.arm(family="other")
    page.goto(trap.url(path))
    settle(page)
    return trap.url(path)


def _quiet(page) -> None:  # noqa: F811
    assert _forms_trap().writes() == [], "a submit reached the server"
    assert page.vetoes == [], "the submit guard had to block an action"
    assert _js(page, "() => window.submitted") == 0


def _field(page, **match):  # noqa: F811
    fields = [f for f in read_fields(form_frames(page)) if all(getattr(f, k) == v for k, v in match.items())]
    assert len(fields) == 1, (match, fields)
    return fields[0]


def _with_answers(space, *entries: AnswerEntry):  # noqa: F811
    return replace(space.config, answers=AnswersFile(answers=list(entries)))


# ------------------------------------------------------------ Workday source tree


def _workday_source(page, tree: dict, *, label: str = "How Did You Hear About Us?"):  # noqa: F811
    """The observed Workday owner / promptSearchButton / type=2 category / back-header contract."""
    _serve(page, f'''
      <label for="source--source">{label}*</label>
      <div data-automation-id="multiSelectContainer" id="source-owner">
        <input id="source--source" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="source-owner"
          aria-required="true" style="position:absolute;width:1px;height:1px;opacity:0">
        <span data-automation-id="promptSearchButton" data-uxi-widget-type="selectinputicon"
          data-uxi-selectinputicon-type="promptSearchButton" data-uxi-multiselect-id="source-owner"
          style="display:inline-block;padding:12px;border:1px solid">List</span>
      </div>
      <div data-automation-id="responsiveMonikerPrompt" data-automation-type="singleSelectPrompt"
        data-associated-widget="source-owner" hidden></div>''', '''
      const box=document.querySelector('#source--source'), owner=document.querySelector('#source-owner'),
        portal=document.querySelector('[data-associated-widget]'),
        icon=owner.querySelector('[data-automation-id=promptSearchButton]');
      window.navigation=[];
      const tree=__TREE__;
      const parentOf=name=>Object.keys(tree).find(key=>tree[key].includes(name));
      window.renderMenu=name=>{
        window.menu=name;
        portal.hidden=false;portal.innerHTML='';
        if(name!=='root') {
          const header=document.createElement('div');
          header.setAttribute('data-uxi-widget-type','multiselectlistheader');
          header.setAttribute('data-uxi-multiselect-id','source-owner');
          header.setAttribute('data-uxi-multiselectlistheader-clickable','true');
          header.innerHTML='<span data-automation-id="backButton" style="display:inline-block;padding:12px">Back</span>';
          header.querySelector('span').onclick=e=>{window.navigation.push(['back',e.isTrusted]);if(e.isTrusted)window.renderMenu(parentOf(name));};
          portal.append(header);
        }
        for(const text of tree[name]) {
          const category=Object.hasOwn(tree,text), option=document.createElement('div');
          option.setAttribute('role','option');option.setAttribute('aria-selected','false');
          option.setAttribute('data-automation-id','menuItem');option.setAttribute('data-association-widget','source-owner');
          option.style.cssText='padding:12px;border:1px solid;max-width:400px';
          option.innerHTML='<div data-automation-id="promptLeafNode" data-uxi-widget-type="multiselectlistitem" '+
            'data-uxi-multiselect-id="source-owner" data-uxi-multiselectlistitem-type="'+(category?'2':'1')+'" '+
            'data-uxi-multiselectlistitem-hassidecharm="'+category+'">'+(category?'':'<input type="radio" data-automation-id="radioBtn">')+
            '<div data-automation-id="promptOption" data-automation-label="'+text+'">'+text+'</div></div>';
          option.onclick=e=>{
            if(category){window.navigation.push([text,e.isTrusted]);if(e.isTrusted)window.renderMenu(text);return;}
            window.trusted.push([text,e.isTrusted,window.menu]);if(!e.isTrusted)return;
            const receipt=document.createElement('ul');
            receipt.setAttribute('data-automation-id','selectedItemList');receipt.setAttribute('data-uxi-multiselect-id','source-owner');
            receipt.innerHTML='<li><div role="option"><p data-automation-id="promptOption" data-automation-label="'+text+'">'+text+'</p></div></li>';
            owner.append(receipt);portal.hidden=true;
          };
          portal.append(option);
        }
      };
      icon.onclick=e=>{window.navigation.push(['open',e.isTrusted]);if(e.isTrusted)window.renderMenu('root');};
    '''.replace("__TREE__", json.dumps(tree)))
    return _field(page, element_id="source--source")


def _committed(page) -> list[str]:  # noqa: F811
    return _js(page, "() => Array.from(document.querySelectorAll('#source-owner [data-automation-id=selectedItemList] "
                     "[data-automation-label]')).map(e => e.getAttribute('data-automation-label'))")


TWO_PLACES = {"root": ["Job Board", "Social Media"], "Job Board": ["LinkedIn", "Indeed"],
              "Social Media": ["LinkedIn", "Facebook"]}


def test_workday_source_saved_path_walks_straight_to_a_leaf_repeated_elsewhere(page, space):  # noqa: F811
    field = _workday_source(page, TWO_PLACES)
    config = _with_answers(space, AnswerEntry(question="How did you hear about us?", answer="Social Media > LinkedIn"))
    plan = plan_for(field, config)
    assert plan is not None and plan.pick_source
    assert _apply_plan([page.main], field, plan)
    assert _js(page, "() => window.navigation") == [["open", True], ["Social Media", True]]
    assert _js(page, "() => window.trusted") == [["LinkedIn", True, "Social Media"]]
    assert _committed(page) == ["LinkedIn"]
    final = _field(page, element_id="source--source")
    assert not final.blank and not final.invalid
    _quiet(page)


def test_workday_source_saved_leaf_in_two_places_is_still_the_saved_source(page, space):  # noqa: F811
    field = _workday_source(page, TWO_PLACES)
    config = _with_answers(space, AnswerEntry(question="How did you hear about us?", answer="LinkedIn"))
    assert _apply_plan([page.main], field, plan_for(field, config))
    assert _committed(page) == ["LinkedIn"]
    assert _js(page, "() => window.trusted") == [["LinkedIn", True, "Job Board"]]
    _quiet(page)


def test_workday_saved_path_with_a_missing_category_falls_back_to_the_unique_leaf(page, space):  # noqa: F811
    field = _workday_source(page, {"root": ["Websites", "Events"], "Websites": ["Company Website"],
                                   "Events": ["Career Fair"]})
    config = _with_answers(space, AnswerEntry(question="How did you hear about us?",
                                              answer="Website > Company Website"))
    assert _apply_plan([page.main], field, plan_for(field, config))
    assert _committed(page) == ["Company Website"]
    _quiet(page)


def test_workday_source_without_saved_answer_picks_a_plain_offered_source(page, space):  # noqa: F811
    _workday_source(page, {"root": ["Referral", "Website"], "Referral": ["Employee Referral"],
                           "Website": ["Company Website", "Other"]})
    result = fill_form(page, _with_answers(space), None)
    assert _committed(page) == ["Company Website"]
    assert "How Did You Hear About Us?" not in result.unanswered
    _quiet(page)


def test_non_source_workday_prompt_with_a_repeated_leaf_still_refuses(page, space):  # noqa: F811
    field = _workday_source(page, TWO_PLACES, label="Which platform do you use most?")
    config = _with_answers(space, AnswerEntry(question="Which platform do you use most?", answer="LinkedIn"))
    with pytest.raises(ChoiceSelectionError, match="ambiguous"):
        _apply_plan([page.main], field, plan_for(field, config))
    assert _committed(page) == []
    _quiet(page)


def test_workday_flat_phone_code_list_commits_the_saved_country(page, space):  # noqa: F811
    field = _workday_source(page, {"root": ["Canada (+1)", "United Kingdom (+44)", "United States of America (+1)"]},
                            label="Country Phone Code")
    profile = space.config.profile.model_copy(update={"country": "United States", "phone": "(512) 555-0100"})
    plan = plan_for(field, replace(space.config, profile=profile))
    assert plan is not None
    assert _apply_plan([page.main], field, plan)
    assert _committed(page) == ["United States of America (+1)"]
    _quiet(page)


# ------------------------------------------------------------ react-select


_REACT_SELECT = '''
  <label id="office-label" for="react-select-2-input">Preferred office*</label>
  <div class="select__container">
    <div class="select__control" style="border:1px solid;padding:4px;min-height:30px">
      <div class="select__value-container"><div class="select__placeholder">Select...</div>
        <input id="__ID__" class="select__input" role="combobox" aria-autocomplete="list" aria-expanded="false"
          aria-haspopup="true" aria-labelledby="office-label" aria-required="true" autocomplete="off" style="width:160px">
      </div>
    </div>
    <div id="menu"></div>
  </div>'''

_REACT_SELECT_JS = '''
  const options=['Atlanta','Austin','Boston'], input=document.querySelector('.select__input'), prefix=input.id.replace(/-input$/,'');
  let open=false, focused=-1, shown=[];
  window.commits=[];
  const render=()=>{
    const menu=document.querySelector('#menu');menu.innerHTML='';input.setAttribute('aria-expanded',String(open));
    if(!open){input.removeAttribute('aria-controls');input.removeAttribute('aria-activedescendant');return;}
    const list=document.createElement('div');list.id=prefix+'-listbox';list.setAttribute('role','listbox');
    shown.forEach((text,i)=>{const o=document.createElement('div');o.id=prefix+'-option-'+i;o.setAttribute('role','option');
      o.setAttribute('aria-selected',String(i===focused));o.textContent=text;o.style.padding='6px';
      o.addEventListener('click',e=>{if(e.isTrusted)choose(text,'click');});list.append(o);});
    menu.append(list);input.setAttribute('aria-controls',list.id);
    if(focused>=0)input.setAttribute('aria-activedescendant',prefix+'-option-'+focused);else input.removeAttribute('aria-activedescendant');
  };
  const choose=(text,how)=>{window.commits.push([how,text]);open=false;input.value='';
    const box=document.querySelector('.select__value-container');
    box.querySelector('.select__placeholder')?.remove();box.querySelector('.select__single-value')?.remove();
    const single=document.createElement('div');single.className='select__single-value';single.textContent=text;box.prepend(single);render();};
  // An async select (window.asyncOnly) offers nothing until a query is typed.
  const filter=()=>{shown=window.asyncOnly&&!input.value?[]:options.filter(o=>o.toLowerCase().includes(input.value.toLowerCase()));focused=shown.length?0:-1;};
  input.addEventListener('input',()=>{open=true;filter();render();});
  input.addEventListener('mousedown',()=>{open=true;filter();render();});
  input.addEventListener('keydown',e=>{
    if(e.key==='ArrowDown'){e.preventDefault();if(!open){open=true;filter();}else focused=(focused+1)%shown.length;render();}
    else if(e.key==='Enter'&&open&&focused>=0){e.preventDefault();choose(shown[focused],'key');}
  });
  input.addEventListener('blur',()=>{open=false;render();});
  document.querySelector('#menu').addEventListener('mousedown',e=>e.preventDefault());  // keeps the focus, like react-select
'''


@pytest.mark.parametrize("answer", ["Austin", "Atlanta", "Boston"])
def test_react_select_commits_by_arrow_down_and_list_commit_enter(page, space, answer):  # noqa: F811
    _serve(page, _REACT_SELECT.replace("__ID__", "react-select-2-input"), _REACT_SELECT_JS)
    field = _field(page, element_id="react-select-2-input")
    assert field.kind == "combobox" and field.required and field.blank
    config = _with_answers(space, AnswerEntry(question="Preferred office", answer=answer))
    assert _apply_plan([page.main], field, plan_for(field, config))
    assert _js(page, "() => window.commits") == [["key", answer]]
    assert _js(page, "() => window.enters") == 1             # Enter only under the commit condition
    assert _js(page, "() => document.querySelector('.select__single-value').textContent") == answer
    assert not _field(page, element_id="react-select-2-input").blank
    _quiet(page)


def test_async_react_select_is_typed_then_committed_by_list_commit_enter(page, space):  # noqa: F811
    _serve(page, _REACT_SELECT.replace("__ID__", "react-select-7-input"), "window.asyncOnly=true;" + _REACT_SELECT_JS)
    field = _field(page, element_id="react-select-7-input")
    config = _with_answers(space, AnswerEntry(question="Preferred office", answer="Boston"))
    assert _apply_plan([page.main], field, plan_for(field, config))
    assert _js(page, "() => window.commits") == [["key", "Boston"]]
    assert _js(page, "() => window.enters") == 1
    assert _js(page, "() => document.querySelector('.select__single-value').textContent") == "Boston"
    _quiet(page)


def test_other_comboboxes_are_committed_by_pointer_never_by_enter(page, space):  # noqa: F811
    _serve(page, _REACT_SELECT.replace("__ID__", "office"), _REACT_SELECT_JS)
    field = _field(page, element_id="office")
    config = _with_answers(space, AnswerEntry(question="Preferred office", answer="Boston"))
    assert _apply_plan([page.main], field, plan_for(field, config))
    assert _js(page, "() => window.commits") == [["click", "Boston"]]
    assert _js(page, "() => window.enters") == 0
    _quiet(page)


def test_react_select_that_moves_focus_to_the_next_box_on_commit_still_commits(page, space):  # noqa: F811
    # Greenhouse's Country select focuses Phone inside its Enter keydown handler,
    # so the release of kitty's own Enter lands on the phone box.
    _serve(page, _REACT_SELECT.replace("__ID__", "react-select-3-input") +
           '<label for="next">Phone</label><input id="next" type="tel">',
           _REACT_SELECT_JS + '''
      window.nextEnters=0;
      const next=document.querySelector('#next');
      next.addEventListener('keyup',e=>{if(e.key==='Enter')window.nextEnters++;});
      input.addEventListener('keydown',e=>{if(e.key==='Enter'&&window.commits.length)next.focus();});''')
    field = _field(page, element_id="react-select-3-input")
    config = _with_answers(space, AnswerEntry(question="Preferred office", answer="Boston"))
    assert _apply_plan([page.main], field, plan_for(field, config))
    assert _js(page, "() => window.commits") == [["key", "Boston"]]
    assert _js(page, "() => document.activeElement.id") == "next"
    assert _js(page, "() => window.nextEnters") == 0          # the page never sees the stray release
    assert _js(page, "() => document.querySelector('#next').value") == ""
    _quiet(page)


def test_enter_release_on_another_box_is_still_vetoed_without_kittys_list_commit(page, space):  # noqa: F811
    _serve(page, '<label for="a">Name</label><input id="a"><label for="b">Phone</label><input id="b" type="tel">', '''
      window.bEnters=0;document.querySelector('#b').addEventListener('keyup',e=>{if(e.key==='Enter')window.bEnters++;});''')
    owner_frame = page.main
    b = owner_frame.node("() => document.querySelector('#b')")
    owner_frame.call("() => document.querySelector('#b').focus()")
    from submit_trap import owner_keys
    owner_keys(owner_frame, "\ue007")                           # the owner's own Enter, no kitty keys bound
    assert _js(page, "() => window.bEnters") == 0
    assert [(entry["kind"], entry["reason"]) for entry in page.new_vetoes()] == [("keydown", "enter"), ("keyup", "enter")]
    assert _forms_trap().writes() == [] and _js(page, "() => window.submitted") == 0


# ------------------------------------------------------------ typeahead locations


def _location(page, suggestions: list[str]):  # noqa: F811
    _serve(page, '''
      <label for="loc">Location (City)*</label>
      <div class="field"><input id="loc" name="location" role="combobox" aria-autocomplete="list"
          aria-controls="loc-list" aria-expanded="false" style="width:300px" required>
        <input type="hidden" name="selectedLocation" id="selectedLocation"><div id="loc-list" role="listbox"></div></div>''',
               '''
      const box=document.querySelector('#loc'), list=document.querySelector('#loc-list'), receipt=document.querySelector('#selectedLocation');
      const places=__PLACES__;window.commits=[];let timer=null;
      box.addEventListener('input',()=>{
        receipt.value='';clearTimeout(timer);list.innerHTML='<div role="option" aria-busy="true">Loading...</div>';list.setAttribute('aria-busy','true');
        const query=box.value;
        timer=setTimeout(()=>{
          list.removeAttribute('aria-busy');list.innerHTML='';box.setAttribute('aria-expanded','true');
          for(const place of (query?places.filter(p=>p.toLowerCase().startsWith(query.toLowerCase().slice(0,4))):[])){
            const o=document.createElement('div');o.setAttribute('role','option');o.textContent=place;o.style.padding='6px';
            o.addEventListener('click',e=>{if(!e.isTrusted)return;window.commits.push(place);box.value=place;
              receipt.value=JSON.stringify({name:place});list.innerHTML='';box.setAttribute('aria-expanded','false');});
            list.append(o);
          }
        },500);
      });
    '''.replace("__PLACES__", json.dumps(suggestions)))
    return _field(page, element_id="loc")


@pytest.mark.parametrize("real", ["Arlington, Texas, United States", "Arlington, TX 76010, USA"])
def test_typeahead_location_commits_a_real_suggestion(page, space, real):  # noqa: F811
    field = _location(page, ["Arlington, Virginia, United States", real, "Arlington Heights, Illinois, United States"])
    plan = plan_for(field, space.config)
    assert plan is not None and plan.location and plan.search_text == "Arlington"
    assert _apply_plan([page.main], field, plan)
    assert _js(page, "() => window.commits") == [real]
    assert _js(page, "() => JSON.parse(document.querySelector('#selectedLocation').value).name") == real
    assert not _field(page, element_id="loc").blank
    _quiet(page)


def test_typeahead_location_without_a_matching_suggestion_keeps_no_typed_text(page, space):  # noqa: F811
    field = _location(page, ["Arlington, Virginia, United States"])
    assert not _apply_plan([page.main], field, plan_for(field, space.config))
    assert _js(page, "() => window.commits") == []
    assert _js(page, "() => [document.querySelector('#loc').value, document.querySelector('#selectedLocation').value]") == ["", ""]
    assert _field(page, element_id="loc").blank
    _quiet(page)


# ------------------------------------------------------------ intl-tel-input


def _intl_tel(page):  # noqa: F811
    countries = [("us", "1", "United States"), ("ca", "1", "Canada"), ("gb", "44", "United Kingdom"),
                 ("gg", "44", "Guernsey"), ("je", "44", "Jersey"), ("de", "49", "Germany")]
    items = "".join(
        f'<li class="iti__country iti__standard" id="iti-0__item-{iso}" role="option" data-dial-code="{dial}" '
        f'data-country-code="{iso}" aria-selected="false" style="padding:4px"><div class="iti__flag-box">'
        f'<div class="iti__flag iti__{iso}"></div></div><span class="iti__country-name">{name}</span>'
        f'<span class="iti__dial-code">+{dial}</span></li>' for iso, dial, name in countries)
    _serve(page, f'''
      <label for="phone">Phone*</label>
      <div class="iti iti--allow-dropdown iti--separate-dial-code">
        <div class="iti__flag-container">
          <div class="iti__selected-flag" role="combobox" aria-controls="iti-0__country-listbox" aria-owns="iti-0__country-listbox"
            aria-expanded="false" tabindex="0" title="United States: +1" style="display:inline-block;padding:6px;border:1px solid">
            <div class="iti__flag iti__us"></div><div class="iti__selected-dial-code">+1</div><div class="iti__arrow"></div>
          </div>
          <ul class="iti__country-list iti__hide" id="iti-0__country-listbox" role="listbox" aria-label="List of countries"
            style="list-style:none;padding:0">{items}</ul>
        </div>
        <input type="tel" id="phone" name="phone" autocomplete="off" required style="width:200px">
      </div>''', '''
      const flag=document.querySelector('.iti__selected-flag'), list=document.querySelector('.iti__country-list');
      window.countryClicks=[];
      flag.addEventListener('click',e=>{if(!e.isTrusted)return;const open=list.classList.toggle('iti__hide')===false;flag.setAttribute('aria-expanded',String(open));});
      for(const item of list.children) item.addEventListener('click',e=>{
        if(!e.isTrusted)return;const iso=item.dataset.countryCode;window.countryClicks.push(iso);
        flag.querySelector('.iti__flag').className='iti__flag iti__'+iso;
        flag.querySelector('.iti__selected-dial-code').textContent='+'+item.dataset.dialCode;
        list.classList.add('iti__hide');flag.setAttribute('aria-expanded','false');
      });''')
    return _field(page, element_id="phone")


def _phone_plan(space, field, phone: str, country: str | None):  # noqa: F811
    profile = space.config.profile.model_copy(update={"phone": phone, "country": country})
    return plan_for(field, replace(space.config, profile=profile)), country


def test_intl_tel_input_selects_the_numbers_country_then_types_the_national_number(page, space):  # noqa: F811
    field = _intl_tel(page)
    plan, region = _phone_plan(space, field, "+44 20 7946 0000", "United Kingdom")
    assert _apply_plan([page.main], field, plan, region=region)
    assert _js(page, "() => window.countryClicks") == ["gb"]
    assert _js(page, "() => [document.querySelector('.iti__selected-flag .iti__flag').className, "
                     "document.querySelector('.iti__selected-dial-code').textContent, "
                     "document.querySelector('#phone').value]") == ["iti__flag iti__gb", "+44", "20 7946 0000"]
    _quiet(page)


def test_intl_tel_input_keeps_the_shown_country_for_a_us_number(page, space):  # noqa: F811
    field = _intl_tel(page)
    plan, region = _phone_plan(space, field, "(512) 555-0100", "United States")
    assert _apply_plan([page.main], field, plan, region=region)
    assert _js(page, "() => window.countryClicks") == []
    assert _js(page, "() => document.querySelector('#phone').value") == "(512) 555-0100"
    _quiet(page)


def test_intl_tel_input_never_guesses_among_countries_sharing_a_code(page, space):  # noqa: F811
    field = _intl_tel(page)
    plan, region = _phone_plan(space, field, "+44 20 7946 0000", None)
    assert not _apply_plan([page.main], field, plan, region=region)
    assert _js(page, "() => [window.countryClicks, document.querySelector('#phone').value]") == [[], ""]
    _quiet(page)


# ------------------------------------------------------------ smooth-scrolling pages

# Greenhouse's job board (2026-11 trial) sets `html {scroll-behavior: smooth}`:
# a plain scrollIntoView then scrolls asynchronously, so a pointer hit test taken
# right after it still sees the control far below the viewport. Widgets below the
# fold on such a page must still be reachable and committed.
_SMOOTH = '<style>html{scroll-behavior:smooth}</style><div style="height:2400px">Earlier questions</div>'


@pytest.mark.parametrize("element_id,how", [("react-select-2-input", "key"), ("office", "click")])
def test_comboboxes_below_the_fold_of_a_smooth_scrolling_page_commit(page, space, element_id, how):  # noqa: F811
    _serve(page, _SMOOTH + _REACT_SELECT.replace("__ID__", element_id), _REACT_SELECT_JS)
    field = _field(page, element_id=element_id)
    config = _with_answers(space, AnswerEntry(question="Preferred office", answer="Boston"))
    assert _apply_plan([page.main], field, plan_for(field, config))
    assert _js(page, "() => window.commits") == [[how, "Boston"]]
    assert _js(page, "() => document.querySelector('.select__single-value').textContent") == "Boston"
    _quiet(page)


def test_intl_tel_country_below_the_fold_of_a_smooth_scrolling_page_commits(page, space):  # noqa: F811
    trap = _forms_trap()
    field = _intl_tel(page)
    # Same widget, now below the fold of a smooth-scrolling page.
    trap.pages["/widget"] = trap.pages["/widget"].replace("<form method=post action=/trap/submit>",
                                                          "<form method=post action=/trap/submit>" + _SMOOTH, 1)
    page.goto(trap.url("/widget"))
    settle(page)
    field = _field(page, element_id="phone")
    plan, region = _phone_plan(space, field, "+44 20 7946 0000", "United Kingdom")
    assert _apply_plan([page.main], field, plan, region=region)
    assert _js(page, "() => window.countryClicks") == ["gb"]
    assert _js(page, "() => document.querySelector('#phone').value") == "20 7946 0000"
    _quiet(page)


# ------------------------------------------------------------ locale dates


def test_dates_follow_each_fields_own_format_and_never_invent_a_day(page, space):  # noqa: F811
    _serve(page, '''
      <label for="g1">Expected graduation date</label><input type="month" id="g1">
      <label for="g2">Graduation date (MM/YYYY)</label><input id="g2">
      <label for="g3">Graduation date</label><input id="g3" placeholder="MM.JJJJ">
      <label for="g4">Graduation date</label><input id="g4" placeholder="TT.MM.JJJJ">
      <div data-automation-id="formField-graduationDate"><label>Expected Graduation Date*</label>
        <div data-automation-id="dateInputWrapper">
          <input data-automation-id="dateSectionMonth-input" aria-label="Month" id="wd-month" style="width:40px">
          <input data-automation-id="dateSectionYear-input" aria-label="Year" id="wd-year" style="width:60px">
        </div></div>''')
    assert _field(page, element_id="wd-month").label == "Expected Graduation Date Month"
    fill_form(page, _with_answers(space), None)
    values = _js(page, "() => ['g1','g2','g3','g4','wd-month','wd-year'].map(id => document.getElementById(id).value)")
    assert values == ["2032-05", "05/2032", "05.2032", "", "05", "2032"]
    _quiet(page)


# ------------------------------------------------------------ Oracle pill buttons


def test_oracle_pill_buttons_are_one_choice_question(page, space):  # noqa: F811
    _serve(page, '''
      <div class="input-row"><label class="input-row__label">Are you willing to relocate?</label>
        <div class="cx-select-pills-container">
          <button type="button" class="cx-select-pill-section">Yes</button>
          <button type="button" class="cx-select-pill-section">No</button></div></div>''', '''
      window.pills=[];
      for(const pill of document.querySelectorAll('.cx-select-pill-section')) pill.addEventListener('click',e=>{
        window.pills.push([pill.textContent,e.isTrusted]);if(!e.isTrusted)return;
        for(const other of document.querySelectorAll('.cx-select-pill-section'))other.classList.toggle('cx-select-pill-section--selected',other===pill);
      });''')
    field = _field(page, label="Are you willing to relocate?")
    assert field.kind == "radio" and field.options == ("Yes", "No") and field.blank
    config = _with_answers(space, AnswerEntry(question="Are you willing to relocate?", answer="Yes"))
    assert _apply_plan([page.main], field, plan_for(field, config))
    assert _js(page, "() => window.pills") == [["Yes", True]]
    assert not _field(page, label="Are you willing to relocate?").blank
    _quiet(page)


# ------------------------------------------------------------ branching re-read

BRANCHES = {
    "workday-branching-question": ('''
      <div data-automation-id="formField-preference"><fieldset><legend>Do you have a preferred work location?*</legend>
        <label><input type="radio" name="pref" value="yes">Yes</label><label><input type="radio" name="pref" value="no">No</label>
      </fieldset></div><div id="follow"></div>''', '''
      for(const radio of document.querySelectorAll('[name=pref]')) radio.addEventListener('change',()=>setTimeout(()=>{
        document.querySelector('#follow').innerHTML=radio.value==='yes'?'<div data-automation-id="formField-office">'+
          '<label for="office">Which office do you prefer?*</label><input id="office" required></div>':'';},400));''',
        [AnswerEntry(question="Do you have a preferred work location?", answer="Yes"),
         AnswerEntry(question="Which office do you prefer?", answer="Austin")], "#office", "Austin"),
    "lever-disability-signature": ('''
      <fieldset><legend>Disability status</legend>
        <label><input type="radio" name="eeo[disability]" value="yes">Yes, I have a disability</label>
        <label><input type="radio" name="eeo[disability]" value="no">No, I do not have a disability</label>
        <label><input type="radio" name="eeo[disability]" value="decline">I do not want to answer</label></fieldset>
      <div id="signature" hidden><label for="sig">Your name</label><input id="sig" name="eeo[disabilitySignature]">
        <label for="sigdate">Date</label><input id="sigdate" name="eeo[disabilitySignatureDate]"></div>''', '''
      for(const radio of document.querySelectorAll('[name="eeo[disability]"]'))
        radio.addEventListener('change',()=>{document.querySelector('#signature').hidden=false;});''',
        [AnswerEntry(question="Disability status", answer="I do not want to answer")], "#sig", "Pat Quinn Tester"),
    "greenhouse-eeoc-race": ('''
      <label for="hispanic">Are you Hispanic/Latino?</label>
      <select id="hispanic"><option value=""></option><option>Yes</option><option>No</option></select>
      <div id="race"></div>''', '''
      document.querySelector('#hispanic').addEventListener('change',e=>{
        document.querySelector('#race').innerHTML=e.target.value==='No'?'<label for="race_id">Please identify your race</label>'+
          '<select id="race_id"><option value=""></option><option>White</option><option>Decline To Self Identify</option></select>':'';});''',
        [AnswerEntry(question="Are you Hispanic/Latino?", answer="No"),
         AnswerEntry(question="Please identify your race", answer="Decline To Self Identify")],
        "#race_id", "Decline To Self Identify"),
}


@pytest.mark.parametrize("case", list(BRANCHES))
def test_follow_up_fields_revealed_by_an_answer_are_filled(page, space, case):  # noqa: F811
    body, script, answers, selector, expected = BRANCHES[case]
    _serve(page, body, script)
    assert not _js(page, "(s) => { const e = document.querySelector(s); return !!e && e.getClientRects().length > 0; }", selector)
    result = fill_form(page, _with_answers(space, *answers), None)
    assert _js(page, "(s) => { const e = document.querySelector(s); return e.tagName === 'SELECT' ? "
                     "e.selectedOptions[0].textContent : e.value; }", selector) == expected
    assert result.unanswered == []
    _quiet(page)


# ------------------------------------------------------------ uploads first, parser overwrites restored

_LEVER_PARSER = ('''
  <div class="application-field"><a class="postings-btn" href="#">ATTACH RESUME/CV<input type="file" name="resume"
    id="resume" style="position:absolute;opacity:0;width:1px;height:1px"></a><span class="filename"></span></div>
  <div class="resume-upload-working" hidden style="padding:4px">Analyzing resume...</div>
  <label for="name">Full name</label><input id="name" name="name">
  <label for="email">Email</label><input id="email" name="email">
  <label for="org">Current company</label><input id="org" name="org">''', '''
  window.order=[];window.parsed=0;
  for(const id of ['name','email','org']) document.getElementById(id).addEventListener('input',e=>window.order.push(id+':'+e.target.value));
  document.querySelector('#resume').addEventListener('change',()=>{
    window.order.push('upload');const working=document.querySelector('.resume-upload-working');working.hidden=false;
    setTimeout(()=>{
      for(const [id,value] of [['name','Parsed Name'],['email','parsed@example.invalid'],['org','Parsed Co']]) {
        const e=document.getElementById(id);e.value=value;e.dispatchEvent(new Event('input',{bubbles:true}));
      }
      window.parsed++;working.hidden=true;
    },800);
  });''')


def test_fresh_form_uploads_first_then_puts_back_planned_values_the_parser_replaced(page, space):  # noqa: F811
    _serve(page, *_LEVER_PARSER)
    result = fill_form(page, space.config, space.settings.root / "resume.pdf")
    assert _js(page, "() => Array.from(document.querySelector('#resume').files).map(f => f.name)") == ["resume.pdf"]
    order = _js(page, "() => window.order")
    assert order[0] == "upload" and _js(page, "() => window.parsed") == 1
    assert _js(page, "() => ['name','email','org'].map(id => document.getElementById(id).value)") == [
        "Pat Quinn Tester", "pat@example.invalid", "Parsed Co"]  # no planned company: the parser's stays
    assert set(result.restored) == {"Full name", "Email"} and result.blocker is None
    assert result.resume_uploaded and result.resume_verified
    _quiet(page)


def test_retained_tab_uploads_into_its_empty_control_and_restores_manual_values(page, space):  # noqa: F811
    _serve(page, *_LEVER_PARSER)
    _js(page, "() => { document.querySelector('#name').value = 'My manual answer'; "
              "document.querySelector('#org').value = 'Manual Co'; }")
    result = fill_form(page, space.config, space.settings.root / "resume.pdf")
    assert _js(page, "() => Array.from(document.querySelector('#resume').files).map(f => f.name)") == ["resume.pdf"]
    assert _js(page, "() => window.parsed") == 1
    assert _js(page, "() => ['name','email','org'].map(id => document.getElementById(id).value)") == [
        "My manual answer", "pat@example.invalid", "Manual Co"]
    assert set(result.restored) == {"Full name", "Email", "Current company"}
    assert result.blocker is None and "deferred" not in " ".join(result.unanswered)
    _quiet(page)


def test_a_parser_change_kitty_cannot_put_back_stops_for_review(page, space):  # noqa: F811
    body, script = _LEVER_PARSER
    body += '<label for="team">Team</label><select id="team" multiple><option selected>Data</option><option>Web</option></select>'
    script += "document.querySelector('#resume').addEventListener('change',()=>setTimeout(()=>{" \
              "for(const o of document.querySelectorAll('#team option'))o.selected=!o.selected;},800));"
    _serve(page, body, script)
    result = fill_form(page, space.config, space.settings.root / "resume.pdf")
    assert result.blocker and "Team" in result.blocker and "could not put back" in result.blocker
    _quiet(page)


# ------------------------------------------------------------ frame re-acquisition


def test_a_replaced_iframe_is_reacquired_not_a_closed_tab(page, space, monkeypatch):  # noqa: F811
    _serve(page, '<iframe title="Embedded" srcdoc="<input id=inner>"></iframe><label for="a">Company</label><input id="a">')
    stale = form_frames(page)
    assert len(stale) == 2
    _js(page, "() => document.querySelector('iframe').remove()")
    monkeypatch.setattr(preflight, "form_frames", lambda tab: stale)
    monkeypatch.setattr(page_module, "form_frames", lambda tab: stale)
    snapshot = preflight.inspect_page(page)              # the lost iframe is skipped, not "tab closed"
    assert snapshot.frame.context == page.context
    assert uploads_pending(page) is False
    _quiet(page)


def _discard(bidi, url: str) -> None:
    assert _chrome_eval(bidi, """((url) => {
      const window = Services.wm.getMostRecentWindow('navigator:browser');
      const tab = window.gBrowser.tabs.find(t => t.linkedBrowser.currentURI.spec === url);
      window.gBrowser.discardBrowser(tab);
      return !tab.linkedPanel;
    })(%s)""" % json.dumps(url))


def test_a_tab_firefox_discarded_is_restored_and_reacquired(page, firefox, space):  # noqa: F811
    url = _serve(page, '<label for="a">Company</label><input id="a">', path="/discarded")
    page.release()
    front = open_tab(firefox)                          # the discarded tab must be in the background
    try:
        _discard(firefox, url)
        with pytest.raises(TabClosed, match="no such frame"):
            page.frames()
        assert _reacquire(page, url) is page
        assert page.main.url == url
        assert page.main.call("() => !!document.querySelector('#a')")
        job, _ = jobs_core.upsert_job(space.conn, company="Fixture Co", title="Discarded intern", url=url,
                                      tracks=[space.track.name])
        record_attempt(space.conn, job.id, "partial", context_id=page.context, url=url)
        front.activate()
        _discard(firefox, url)
        retained = _retained_tab(firefox, jobs_core.get_job(space.conn, job.id))
        assert retained is not None and retained.context == page.context
    finally:
        front.close()
    assert _forms_trap().writes() == []


def test_a_tab_the_page_replaced_with_its_own_popup_is_reacquired(firefox, space):  # noqa: F811
    import time

    trap = _forms_trap()
    trap.pages["/next"] = _HEAD + '<label for="b">Company</label><input id="b"></body></html>'
    opener = open_tab(firefox)
    url = _serve(opener, '<button type="button" id="open" onclick="window.open(\'/next\', \'_blank\')">'
                         'Continue in a new tab</button>', path="/opener")
    opener.release()
    before = {c["context"] for c in firefox.command("browsingContext.getTree", {"maxDepth": 0})["contexts"]}
    owner_click(opener.main, opener.main.node("() => document.querySelector('#open')"))
    opened = None
    for _ in range(40):
        contexts = firefox.command("browsingContext.getTree", {"maxDepth": 0})["contexts"]
        opened = next((c for c in contexts if c["context"] not in before and c.get("originalOpener") == opener.context), None)
        if opened and opened["url"].endswith("/next"):
            break
        time.sleep(0.1)
    assert opened is not None and opened["url"].endswith("/next")
    opener.close()
    successor = _reacquire(opener, url)
    try:
        assert successor is not None and successor.context == opened["context"]
        assert successor.main.call("() => !!document.querySelector('#b')")
    finally:
        firefox.command("browsingContext.close", {"context": opened["context"]})
    assert trap.writes() == []


def test_kitty_firefox_never_unloads_its_waiting_tabs(tmp_path):
    assert PREFS["browser.tabs.unloadOnLowMemory"] is False
    write_prefs(tmp_path)
    assert 'user_pref("browser.tabs.unloadOnLowMemory", false);' in (tmp_path / "user.js").read_text()


def test_fill_job_reports_a_vanished_tab_with_its_successor(page, space, monkeypatch):  # noqa: F811
    from kitty.core.apply import filler

    url = _serve(page, '<label for="a">Company</label><input id="a">', path="/vanish")
    job, _ = jobs_core.upsert_job(space.conn, company="Fixture Co", title="Vanish intern", url=url,
                                  tracks=[space.track.name])
    seen = []

    def vanish(*_args, **_kwargs):
        raise TabClosed("browsingContext.getTree: no such frame: Browsing Context with id x not found")

    monkeypatch.setattr(filler, "wait_for_page", vanish)
    monkeypatch.setattr(filler, "_reacquire", lambda tab, last: seen.append(last) or tab)
    report = fill_job(page, space.conn, space.settings, space.config, job)
    assert report.status == "blocked" and "closed or was replaced" in report.note
    assert report.successor is page and seen == [url]
