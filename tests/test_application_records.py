"""Required timeline records in isolated synthetic Firefox, never live applicant data."""
from dataclasses import replace
import json
from urllib.parse import quote

import pytest

from test_apply import firefox, page
from kitty.config import EducationRecord, EmploymentRecord
from kitty.core.apply.records import fill_required_records


EDUCATION = dict(school='Example Technical College', degree="Bachelor's Degree",
                 field_of_study='Applied Systems', expected_end_date='2034-06',
                 current=True, graduated=False, comments='Program in progress; degree not yet earned.')
EMPLOYMENT = dict(employer='Synthetic Workshop', title='Lab Assistant',
                  start_date='2030-02', end_date='2031-07', current=False,
                  employment_type='Part-time', description='Maintained synthetic test equipment.')


def _config(workspace, kind, records=None):
    model, field = ((EducationRecord, 'education_records') if kind == 'education'
                    else (EmploymentRecord, 'employment_records'))
    values = records if records is not None else [EDUCATION if kind == 'education' else EMPLOYMENT]
    profile = workspace.config.profile.model_copy(update={field: [model(**r) for r in values]})
    return replace(workspace.config, profile=profile)


def _load(page, kind, *, cards=(), mode='save', required=True, extra='', initial=None,
          embedded=False, save_type='button'):
    """Mirror the native card-selection/Edit/editor/Save distinction and date grids."""
    options = dict(kind=kind, cards=list(cards), mode=mode, required=required,
                   extra=extra, initial=initial or {}, saveType=save_type)
    script = r'''
      window.counts={select:0,edit:0,add:0,save:0,submit:0};
      window.saved=[];window.trusted=[];
      const opts=OPTIONS;
      const section=document.querySelector('apply-flow-section');
      const add=document.querySelector('#timeline-add-'+opts.kind+'-button');
      if(opts.required)add.setAttribute('data-profile-item-invalid','true');
      const plural=opts.kind==='education'?'educations':'experiences';
      const cards=document.querySelector('#cards');
      const editor=document.querySelector('#editor');
      function card(info,index) {
        const host=document.createElement('beautiful-timeline-item');
        host.setAttribute('params',"type: '"+plural+"'");
        const tile=document.createElement('div');
        tile.className='beautiful-timeline-item--'+plural+'__'+index;
        tile.setAttribute('data-profile-item-tile','true');
        if(info.invalid)tile.setAttribute('data-profile-item-invalid','true');
        const body=document.createElement('p');body.textContent=info.text||'';tile.append(body);
        const edit=document.createElement('button');edit.type='button';edit.setAttribute('aria-label','Edit');
        edit.textContent='Edit';edit.hidden=true;tile.append(edit);
        tile.onclick=e=>{
          if(e.target===edit)return;
          window.counts.select++;edit.hidden=false;
          if(opts.mode==='selection-only')return;
        };
        edit.onclick=e=>{
          window.trusted.push(e.isTrusted);window.counts.edit++;
          if(opts.mode!=='selection-only')open(host);
        };
        host.append(tile);cards.append(host);return host;
      }
      opts.cards.forEach(card);
      const field=(id,label,type='text',required=false)=>
        `<label for="${id}">${label}</label><input id="${id}" name="${id}" type="${type}" ${required?'required':''}>`;
      function date(name,label,month,year,required) {
        let out='<div class="datepicker-row quick-apply-flow-datepicker-row input-row input-row--has-picker">';
        for(const [part,value] of [['month',month],['year',year]]) {
          const id=part+'-'+name+'-72';
          out+=`<div class="cx-select-container"><label for="${id}">${label} ${part==='month'?'Month':'Year'}</label>
            <input id="${id}" name="${name}" class="cx-select-input" role="combobox" aria-haspopup="grid"
              aria-controls="${id}-listbox" aria-expanded="false" ${required?'required':''}>
            <div id="${id}-listbox" role="grid" hidden><div role="row">
              <div role="gridcell" tabindex="-1" aria-selected="false">${value}</div></div></div></div>`;
        }
        return out+'</div>';
      }
      function open(target) {
        editor.hidden=false;
        let markup='';
        if(opts.kind==='education') {
          markup=`<div class="input-row"><label for="school">School</label><div class="cx-select-container">
            <input id="school" name="school" class="cx-select-input" role="combobox" aria-haspopup="grid"
              aria-controls="schools" aria-expanded="false" required>
            <div id="schools" role="grid" hidden><div role="row"><div role="gridcell" tabindex="-1"
              aria-selected="false">Example Technical College</div></div></div></div></div>
            <label for="degree">Degree</label><select id="degree" required><option value="">Select</option>
              <option>Bachelor's Degree</option></select>
            <label for="major">Major</label><select id="major" required><option value="">Select</option>
              <option>Applied Systems</option></select>`+
            date('startDate','Start Date','September','2030',false)+
            date('endDate','End Date','June','2034',true)+
            field('graduated','Graduated','checkbox')+field('comments','Comments');
        } else {
          markup=field('employer','Employer Name','text',true)+field('title','Job Title','text',true)+
            date('startDate','Start Date','February','2030',true)+
            date('endDate','End Date','July','2031',true)+
            field('current','Current Job','checkbox')+
            `<label for="employment-type">Employment Type</label><select id="employment-type" required>
              <option value="">Select</option><option>Part-time</option><option>Volunteer</option></select>`+
            field('description','Achievements');
        }
        editor.innerHTML=markup+opts.extra+
          `<button id="local-save" type="${opts.saveType}">${target?'SAVE':'ADD '+opts.kind.toUpperCase()}</button>`;
        if(opts.mode==='no-status')editor.querySelector('#graduated')?.remove();
        for(const [id,value] of Object.entries(opts.initial)) {
          const el=editor.querySelector('#'+id);
          if(el.type==='checkbox')el.checked=value;else el.value=value;
        }
        for(const box of editor.querySelectorAll('input[role=combobox]')) {
          const list=document.getElementById(box.getAttribute('aria-controls'));
          const open=()=>{list.hidden=false;box.setAttribute('aria-expanded','true');};
          box.onclick=open;box.onkeydown=e=>{if(e.key==='ArrowDown')open();};
          list.querySelector('[role=gridcell]').onclick=e=>{
            window.trusted.push(e.isTrusted);if(!e.isTrusted)return;
            box.value=e.currentTarget.textContent;box.dispatchEvent(new Event('input',{bubbles:true}));
            list.remove();box.setAttribute('aria-expanded','false');
            const row=box.closest('.input-row');
            const boxes=[...row.querySelectorAll('input[role=combobox]')];
            if(boxes.every(b=>b.value))row.classList.add('input-row--filled');
          };
        }
        document.querySelector('#local-save').onclick=e=>{
          window.trusted.push(e.isTrusted);window.counts.save++;
          if(!e.isTrusted||opts.mode==='noop')return;
          const snapshot={};
          editor.querySelectorAll('input,select,textarea').forEach(el=>snapshot[el.id]=
            el.type==='checkbox'?el.checked:el.value);
          window.saved.push(snapshot);
          if(opts.mode==='clear-only') {
            add.removeAttribute('data-profile-item-invalid');editor.hidden=true;return;
          }
          if(target)target.remove();
          card({text:opts.kind==='education'?snapshot.school+' '+snapshot.degree+' '+snapshot.major:
            snapshot.employer+' '+snapshot.title},90);
          if(opts.mode!=='markers-remain')add.removeAttribute('data-profile-item-invalid');
          editor.innerHTML='';editor.hidden=true;
        };
      }
      add.onclick=e=>{window.trusted.push(e.isTrusted);window.counts.add++;open(null);};
      document.querySelector('form').onsubmit=e=>{e.preventDefault();window.counts.submit++;};
    '''.replace('OPTIONS', json.dumps(options))
    body = f'''<!doctype html><style>
      beautiful-timeline-item,apply-flow-section{{display:block}}
      [data-profile-item-tile=true]{{min-height:48px;padding:8px;box-sizing:border-box}}
      button{{margin:8px}}</style>
      <form class="apply-flow__content-form">
        <apply-flow-section data-scroll-spy="apply-flow-sections">
          <div class="beautiful-timeline"><div id="cards"></div>
            <div class="beautiful-timeline-editor" id="editor" hidden></div>
            <button type="button" id="timeline-add-{kind}-button">ADD {kind.upper()}</button>
          </div>
        </apply-flow-section>
        <button type="submit">SUBMIT</button>
      </form><script>{script}</script>'''
    if embedded:
        body = ('<!doctype html><iframe style="width:100%;height:1100px" '
                'onload="window.recordFixtureReady=true" src="data:text/html,' +
                quote(body, safe='') + '"></iframe>')
    page.goto('data:text/html,' + quote(body, safe=''))
    if embedded:
        # goto waits for the parent to become interactive, not this separate
        # data document. Observe its actual load before returning its Frame.
        page.main.call("""async () => {
          if (window.recordFixtureReady) return;
          await new Promise((resolve, reject) => {
            const timer = setTimeout(() => reject(new Error('Synthetic record frame did not load')), 5000);
            document.querySelector('iframe').addEventListener('load', () => {
              clearTimeout(timer); resolve();
            }, {once: true});
          });
        }""", page_realm=True)
    return page.frames()[1] if embedded else page.main


def _state(frame):
    return frame.call("""() => ({counts:window.counts,saved:window.saved,trusted:window.trusted,
      cards:[...document.querySelectorAll('[data-profile-item-tile] p')].map(e=>e.textContent),
      values:[...document.querySelectorAll('#editor input,#editor select')].map(e=>e.value)})""", page_realm=True)


@pytest.mark.parametrize('kind', ['education', 'experience'])
@pytest.mark.parametrize('embedded', [False, True])
def test_required_records_use_native_edit_and_commit_once(workspace, page, kind, embedded):
    unrelated = 'Other College Completed Certificate' if kind == 'education' else 'Other Workshop Technician'
    frame = _load(page, kind, cards=[{'text': unrelated}, {'text': '', 'invalid': True}], embedded=embedded)
    config = _config(workspace, kind)
    checkpoints = []
    result = fill_required_records(page, config, checkpoint=lambda: checkpoints.append(True))
    assert result.handled and result.changed and not result.blocker
    state = _state(frame)
    assert state['counts'] == {'select': 1, 'edit': 1, 'add': 0, 'save': 1, 'submit': 0}
    assert state['trusted'] and all(state['trusted'])
    assert len(state['cards']) == 2 and unrelated in state['cards']
    saved = state['saved'][0]
    if kind == 'education':
        assert saved['school'] == EDUCATION['school'] and saved['degree'] == EDUCATION['degree']
        assert saved['major'] == EDUCATION['field_of_study']
        assert saved['graduated'] is False
        assert saved['month-startDate-72'] == saved['year-startDate-72'] == ''
        assert (saved['month-endDate-72'], saved['year-endDate-72']) == ('June', '2034')
    else:
        assert saved['employer'] == EMPLOYMENT['employer'] and saved['title'] == EMPLOYMENT['title']
        assert saved['employment-type'] == 'Part-time' and saved['current'] is False
        assert (saved['month-startDate-72'], saved['year-startDate-72']) == ('February', '2030')
        assert (saved['month-endDate-72'], saved['year-endDate-72']) == ('July', '2031')
    assert checkpoints
    again = fill_required_records(page, config)
    assert not again.changed and not again.handled
    assert _state(frame)['counts'] == state['counts']


@pytest.mark.parametrize('kind', ['education', 'experience'])
def test_required_records_add_only_when_no_incomplete_card_exists(workspace, page, kind):
    frame = _load(page, kind, cards=[{'text': 'Unrelated retained record'}])
    result = fill_required_records(page, _config(workspace, kind))
    assert result.changed and not result.blocker
    assert _state(frame)['counts'] == {'select': 0, 'edit': 0, 'add': 1, 'save': 1, 'submit': 0}
    assert 'Unrelated retained record' in _state(frame)['cards']


@pytest.mark.parametrize('kind', ['education', 'experience'])
def test_empty_card_is_edited_even_when_only_add_has_required_marker(workspace, page, kind):
    frame = _load(page, kind, cards=[{'text': ''}])
    assert fill_required_records(page, _config(workspace, kind)).changed
    assert _state(frame)['counts']['add'] == 0
    assert _state(frame)['counts']['edit'] == 1


@pytest.mark.parametrize('kind', ['education', 'experience'])
def test_card_selection_without_editor_never_fills_or_saves(workspace, page, kind):
    frame = _load(page, kind, cards=[{'text': '', 'invalid': True}], mode='selection-only')
    result = fill_required_records(page, _config(workspace, kind))
    assert result.handled and not result.changed and result.blocker
    assert result.cause == 'unsupported_controls'
    assert _state(frame)['counts'] == {'select': 1, 'edit': 1, 'add': 0, 'save': 0, 'submit': 0}
    assert _state(frame)['saved'] == []


@pytest.mark.parametrize('kind', ['education', 'experience'])
def test_nonempty_unrelated_invalid_card_is_preserved(workspace, page, kind):
    frame = _load(page, kind, cards=[{'text': 'Unrelated manual record', 'invalid': True}])
    result = fill_required_records(page, _config(workspace, kind))
    assert result.blocker and not result.changed
    assert result.cause == 'unsupported_controls'
    assert _state(frame)['counts'] == {'select': 0, 'edit': 0, 'add': 0, 'save': 0, 'submit': 0}
    assert _state(frame)['cards'] == ['Unrelated manual record']


@pytest.mark.parametrize('kind', ['education', 'experience'])
def test_matching_existing_record_cannot_be_duplicated_to_clear_marker(workspace, page, kind):
    text = EDUCATION['school'] if kind == 'education' else EMPLOYMENT['employer']+' '+EMPLOYMENT['title']
    frame = _load(page, kind, cards=[{'text': text}])
    result = fill_required_records(page, _config(workspace, kind))
    assert result.blocker and not result.changed
    assert _state(frame)['counts']['add'] == _state(frame)['counts']['save'] == 0
    assert _state(frame)['cards'] == [text]


@pytest.mark.parametrize('reason', ['empty-config', 'ambiguous-records', 'ambiguous-cards', 'unknown-start',
                                   'unknown-type', 'unknown-status', 'unknown-contact', 'unsupported', 'conflict'])
def test_missing_or_conflicting_facts_leave_record_for_review(workspace, page, reason):
    records = [dict(EMPLOYMENT)]
    cards = [{'text': '', 'invalid': True}]
    extra = ''
    initial = {}
    if reason == 'empty-config':
        records = []
    elif reason == 'ambiguous-records':
        records.append(dict(EMPLOYMENT, employer='Another Synthetic Workshop'))
    elif reason == 'ambiguous-cards':
        cards.append({'text': '', 'invalid': True})
    elif reason == 'unknown-start':
        records[0]['start_date'] = None
    elif reason == 'unknown-type':
        records[0]['employment_type'] = None
    elif reason == 'unknown-status':
        records[0]['current'] = None
    elif reason == 'unknown-contact':
        extra = '<label for="supervisor">Supervisor phone</label><input id="supervisor" required>'
    elif reason == 'unsupported':
        extra = '<div role="combobox" aria-required="true">Unsupported required record fact</div>'
    elif reason == 'conflict':
        initial = {'title': 'Unrelated manual title'}
    frame = _load(page, 'experience', cards=cards, extra=extra, initial=initial)
    result = fill_required_records(page, _config(workspace, 'experience', records))
    assert result.handled and result.blocker and not result.changed
    expected_cause = ('missing_applicant_fact' if reason in
                      ('empty-config', 'unknown-start', 'unknown-type', 'unknown-status', 'unknown-contact')
                      else 'unsupported_controls')
    assert result.cause == expected_cause
    state = _state(frame)
    assert not state['saved'] and state['counts']['save'] == state['counts']['submit'] == 0
    assert not result.filled
    if reason == 'conflict':
        assert 'Unrelated manual title' in state['values']


@pytest.mark.parametrize('mode', ['noop', 'markers-remain', 'clear-only'])
def test_save_needs_matching_card_and_cleared_markers_not_click_echo(workspace, page, mode):
    frame = _load(page, 'education', cards=[{'text': '', 'invalid': True}], mode=mode)
    result = fill_required_records(page, _config(workspace, 'education'))
    assert result.handled and not result.changed and result.blocker
    assert _state(frame)['counts']['save'] == 1
    assert _state(frame)['counts']['submit'] == 0


def test_submit_disguised_as_local_save_is_never_clicked(workspace, page):
    frame = _load(page, 'education', cards=[{'text': '', 'invalid': True}], save_type='submit')
    result = fill_required_records(page, _config(workspace, 'education'))
    assert result.blocker and not result.changed
    assert _state(frame)['counts']['save'] == _state(frame)['counts']['submit'] == 0


def test_flat_profile_does_not_synthesize_an_earned_or_in_progress_record(workspace, page):
    frame = _load(page, 'education')
    result = fill_required_records(page, workspace.config)
    assert result.handled and result.blocker and not result.changed
    assert result.cause == 'missing_applicant_fact'
    assert _state(frame)['counts']['add'] == 0


def test_degree_without_explicit_status_does_not_write_or_save(workspace, page):
    frame = _load(page, 'education', cards=[{'text': '', 'invalid': True}])
    record = dict(EDUCATION, current=None, graduated=None)
    result = fill_required_records(page, _config(workspace, 'education', [record]))
    assert result.blocker and not result.changed and not result.filled
    assert result.cause == 'missing_applicant_fact'
    assert _state(frame)['counts']['save'] == 0


def test_matching_nonempty_invalid_record_keeps_manual_optional_values(workspace, page):
    record = dict(EMPLOYMENT, description=None)
    text = record['employer'] + ' ' + record['title']
    frame = _load(page, 'experience', cards=[{'text': text, 'invalid': True}],
                  initial={'employer': record['employer'], 'title': record['title'],
                           'description': 'Manually authored detail to retain.'})
    result = fill_required_records(page, _config(workspace, 'experience', [record]))
    assert result.changed and not result.blocker
    assert 'experience: Employer Name' not in result.filled
    assert 'experience: Job Title' not in result.filled
    assert _state(frame)['saved'][0]['description'] == 'Manually authored detail to retain.'
    assert _state(frame)['counts'] == {'select': 1, 'edit': 1, 'add': 0, 'save': 1, 'submit': 0}


@pytest.mark.parametrize('mode', ['no-status', 'earned-conflict'])
def test_in_progress_degree_needs_representable_non_earned_status(workspace, page, mode):
    frame = _load(page, 'education', cards=[{'text': '', 'invalid': True}], mode=mode,
                  initial={'graduated': True} if mode == 'earned-conflict' else {})
    result = fill_required_records(page, _config(workspace, 'education'))
    assert result.blocker and not result.changed and not result.filled
    assert result.cause == 'unsupported_controls'
    assert _state(frame)['counts']['save'] == 0


def test_zero_area_record_tile_is_not_a_clickable_editor_action(workspace, page):
    frame = _load(page, 'experience', cards=[{'text': '', 'invalid': True}])
    frame.call("""() => {
      const tile = document.querySelector('[data-profile-item-tile]');
      tile.style.cssText = 'height:0;min-height:0;padding:0;overflow:hidden';
      tile.querySelector('p').style.margin = '0';
    }""", page_realm=True)
    result = fill_required_records(page, _config(workspace, 'experience'))
    assert result.handled and result.blocker and not result.changed
    assert result.cause == 'unsupported_controls'
    assert _state(frame)['counts'] == {'select': 0, 'edit': 0, 'add': 0, 'save': 0, 'submit': 0}


def test_visible_native_edit_does_not_require_clickable_card_wrapper(workspace, page):
    frame = _load(page, 'experience', cards=[{'text': '', 'invalid': True}])
    frame.call("""() => {
      const tile = document.querySelector('[data-profile-item-tile]');
      tile.style.display = 'contents';
      tile.querySelector('button[aria-label=Edit]').hidden = false;
    }""", page_realm=True)
    result = fill_required_records(page, _config(workspace, 'experience'))
    assert result.changed and not result.blocker
    assert _state(frame)['counts'] == {'select': 0, 'edit': 1, 'add': 0, 'save': 1, 'submit': 0}


@pytest.mark.parametrize('failure', ['transport', 'unsafe'])
def test_record_interaction_failures_have_typed_non_fact_causes(workspace, monkeypatch, failure):
    from kitty.core.apply import records

    def fail(_page):
        error = records.BidiError if failure == 'transport' else records.UnsafeAction
        raise error('Synthetic guarded interaction failure')

    monkeypatch.setattr(records, 'form_frames', fail)
    result = records.fill_required_records(object(), workspace.config)
    assert result.handled and result.blocker and not result.changed
    assert result.cause == ('browser_error' if failure == 'transport' else 'unsupported_controls')
