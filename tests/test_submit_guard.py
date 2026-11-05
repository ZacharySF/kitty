"""The submit-proof action layer, L0 and L1 (research note N3 §4.2-4.3, §5-§7),
and the network tripwire (L2) where an L0/L1 fixture also needs it (E1, E2, W1, W2).

Every adversarial fixture is served by a SubmitTrap, the ground truth of what
left the browser, and runs with the layer on and off. Off, the provocation is
sent raw (WebDriver input that bypasses kitty's admission and no in-page veto)
and must reach the trap: a fixture that cannot fail proves nothing. On, kitty's
own admitted action is refused (UnsafeAction) or the in-page veto blocks it,
the trap stays empty and the veto log names what was blocked.

All fixture pages and values are synthetic.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from kitty.core import jobs as jobs_core
from kitty.core.apply import bidi as bidi_module
from kitty.core.apply import guard, preflight
from kitty.core.apply.bidi import Frame, open_tab
from kitty.core.apply.filler import fill_job
from kitty.core.apply.guard import (
    CREDENTIAL, FIELD, LIST_COMMIT, MAILBOX, NEXT, UnsafeAction, untypeable, veto_cause,
)
from kitty.core.apply.residence_gate import submit_profile_bootstrap
from submit_trap import SubmitTrap, owner_click, owner_keys, self_signed
from test_application_auth import firefox  # noqa: F401  (headless Firefox accepting the trap's certificate)
from test_apply import space  # noqa: F401

SRC = Path(__file__).resolve().parents[1] / "src" / "kitty"
LAYERS = pytest.mark.parametrize("layer", ["on", "off"])
SUBMIT = ("POST", "/trap/submit")


@pytest.fixture(scope="module")
def trap(tmp_path_factory):
    trap = SubmitTrap(tls=self_signed(tmp_path_factory.mktemp("trap-tls")))
    yield trap
    trap.close()


@pytest.fixture
def tab(firefox, trap):  # noqa: F811
    trap.reset()
    tab = open_tab(firefox)
    yield tab
    tab.release()
    tab.close()


def _serve(trap, tab, body: str, *, layer: str = "on", path: str = "/fixture", family="custom"):
    """Serve a synthetic page and open it, armed before navigation when the layer is on."""
    trap.pages[path] = "<!doctype html><html><body>" + body + "</body></html>"
    if layer == "on":
        tab.arm(family=family)
    tab.goto(trap.url(path))
    return tab.main


def _node(frame: Frame, selector: str) -> str:
    node = frame.node("(s) => document.querySelector(s)", selector)
    assert node is not None, selector
    return node


def _page(frame: Frame, script: str, *args):
    return frame.call(script, *args, page_realm=True)


def _quiet(trap) -> list[tuple[str, str]]:
    """What reached the trap within a second (a blocked submit never arrives)."""
    return trap.wait_for_write(1.0)


# ------------------------------------------------------------ L0 without a browser


def test_type_filter_refuses_every_key_that_could_submit():
    for text in ("Austin\ue007", "Austin\ue006", "line\n", "a\rb", "a\tb", "\x00", "\x1b", "\x85", "\ue004", "\ue00d"):
        assert untypeable(text), repr(text)
    for text in ("Austin", "New York", "São Paulo", "O'Neil-Smith 3/4", "東京"):
        assert untypeable(text) is None, text


def test_refusals_happen_before_anything_is_sent():
    def command(*_args, **_kwargs):
        raise AssertionError("a refused action reached the browser")

    frame = Frame(SimpleNamespace(command=command), "context", "https://example.invalid/")
    with pytest.raises(UnsafeAction, match="refusing to type"):
        frame.type("Austin\ue007", into="node")
    with pytest.raises(UnsafeAction, match="not an allowed key"):
        frame.press("Tab", intent=FIELD, into="node")
    with pytest.raises(UnsafeAction, match="not an allowed key"):
        frame.press(" ", intent=FIELD, into="node")
    with pytest.raises(UnsafeAction, match="for intent"):
        frame.press("Enter", intent=FIELD, into="node")
    with pytest.raises(UnsafeAction, match="for intent"):
        frame.press("ArrowDown", intent=LIST_COMMIT, into="node")
    with pytest.raises(UnsafeAction, match="undeclared intent"):
        frame.click("node", intent="submit")


def test_veto_cause_tells_the_owner_from_kitty_and_the_page():
    owner = {"kind": "click", "trusted": True, "token": False}
    kitty = {"kind": "click", "trusted": True, "token": True}
    page = {"kind": "click", "trusted": False, "token": False}
    submit = {"kind": "submit", "trusted": True, "token": False}
    assert veto_cause([]) is None
    assert veto_cause([kitty]) == veto_cause([page]) == veto_cause([submit]) == guard.SUBMIT_VETOED
    assert veto_cause([kitty, owner]) == guard.OWNER_TOOK_OVER
    with pytest.raises(guard.OwnerTookOver):
        guard.stop_for([page, owner])
    with pytest.raises(guard.SubmitVetoed):
        guard.stop_for([page])
    guard.stop_for([])


# ------------------------------------------------------------ R2: one choke point


_ACTIVATION = re.compile(r"\.click\(\)|new (?:Mouse|Pointer|Keyboard)Event\(")


def test_only_bidi_sends_input_and_every_in_page_activation_sets_a_token():
    """N3 §5 R2: no module but bidi.py sends input.performActions; every kitty
    `.click()` or synthetic mouse/key event in a page script goes through
    kittyActivate (the one-shot token helper); no script submits a form; every
    Python click/press declares an intent and every typed text names its field."""
    sources = sorted(SRC.rglob("*.py"))
    assert sources
    for path in sources:
        text = path.read_text()
        relative = path.relative_to(SRC).as_posix()
        if relative != "core/apply/bidi.py":
            assert "input.performActions" not in text, relative
        for number, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue                               # a Python comment describing the rule
            if _ACTIVATION.search(line):
                assert "kittyActivate(" in line, f"{relative}:{number} activates outside kittyActivate"
            if relative != "core/apply/guard.py":     # guard.py only documents them
                assert not re.search(r"\.requestSubmit\(|\.submit\(\)", line), f"{relative}:{number}"
        for node in ast.walk(ast.parse(text)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("click", "press", "type") and node.args):
                required = "into" if node.func.attr == "type" else "intent"
                assert required in {keyword.arg for keyword in node.keywords}, (
                    f"{relative}:{node.lineno} {node.func.attr}() without {required}=")


# ------------------------------------------------------------ T: typed text and Enter

T1 = """<form method="post" action="/trap/submit">
  <label>City <input id="city" name="city"></label><button>Go</button></form>"""


@LAYERS
def test_t1_enter_and_newline_in_a_saved_answer_never_type(tab, trap, layer):
    frame = _serve(trap, tab, T1, layer=layer)
    city = _node(frame, "#city")
    frame.call("() => document.querySelector('#city').focus()")
    for answer in ("Austin\ue007", "line\n", "\ue006"):
        with pytest.raises(UnsafeAction, match="refusing to type"):
            frame.type(answer, into=city)
    assert frame.call("() => document.querySelector('#city').value") == ""
    owner_keys(frame, "\ue007")                    # the same Enter, raw
    if layer == "off":
        assert trap.wait_for_write() == [SUBMIT]
    else:
        assert _quiet(trap) == []
        assert {entry["reason"] for entry in tab.new_vetoes()} == {"enter"}


T2 = """<form method="post" action="/trap/submit">
  <label>City <input id="city" name="city"></label><button id="submit">Submit</button></form>
<script>
  const city = document.querySelector('#city');
  city.addEventListener('input', () => { if (city.value.length >= 3) document.querySelector('#submit').focus(); });
</script>"""


@LAYERS
def test_t2_focus_stolen_mid_typing_is_vetoed(tab, trap, layer):
    frame = _serve(trap, tab, T2, layer=layer)
    frame.call("() => document.querySelector('#city').focus()")
    if layer == "off":
        owner_keys(frame, "New York")              # Space reaches the focused Submit button
        assert trap.wait_for_write() == [SUBMIT]
        return
    with pytest.raises(UnsafeAction, match="submit guard blocked"):
        frame.type("New York", into=_node(frame, "#city"))
    assert _quiet(trap) == []
    vetoes = tab.vetoes
    assert vetoes and {entry["reason"] for entry in vetoes} <= {"focus_moved", "space"}
    assert all(entry["token"] for entry in vetoes)  # kitty's own keys: a defect, not the owner
    assert veto_cause(vetoes) == guard.SUBMIT_VETOED


T3 = """<form method="post" action="/trap/submit"><label>Code <input id="code" name="code"></label></form>"""


@LAYERS
def test_t3_enter_in_a_form_without_a_button_is_cancelled(tab, trap, layer):
    frame = _serve(trap, tab, T3, layer=layer)
    frame.call("() => document.querySelector('#code').focus()")
    owner_keys(frame, "12\ue007")                  # implicit submission: no button, one field
    if layer == "off":
        assert trap.wait_for_write() == [SUBMIT]
    else:
        assert _quiet(trap) == []
        assert [entry["reason"] for entry in tab.new_vetoes() if entry["kind"] == "keydown"] == ["enter"]


# ------------------------------------------------------------ K: Enter in a list

COMBO = """<form method="post" action="/trap/submit">
  <label for="state-input">State</label>
  <input id="state-input" role="combobox" aria-expanded="false" aria-controls="state-list"
    aria-activedescendant="" autocomplete="off">
  <div id="state-list" role="listbox" hidden>
    <div role="option" id="state-1">Texas</div><div role="option" id="state-2">Utah</div>
  </div>
  <input type="hidden" name="state" id="state">
  <button type="submit">Submit application</button>
</form>
<script>
  window.__enterPresses = 0;
  const box = document.querySelector('#state-input'), list = document.querySelector('#state-list');
  window.openList = focused => {
    list.hidden = false;
    box.setAttribute('aria-expanded', 'true');
    box.setAttribute('aria-activedescendant', focused ? 'state-1' : '');
  };
  // react-select: Enter selects, and is cancelled, only with the menu open and an option focused.
  box.addEventListener('keydown', event => {
    if (event.key !== 'Enter') return;
    window.__enterPresses++;
    const id = box.getAttribute('aria-activedescendant');
    if (box.getAttribute('aria-expanded') !== 'true' || !id) return;
    event.preventDefault();
    document.querySelector('#state').value = document.getElementById(id).textContent;
    list.hidden = true;
    box.setAttribute('aria-expanded', 'false');
    box.setAttribute('aria-activedescendant', '');
  });
</script>"""


@LAYERS
@pytest.mark.parametrize("menu", ["open", "closed"])
def test_k1_k2_enter_without_a_focused_option_is_refused(tab, trap, layer, menu):
    frame = _serve(trap, tab, COMBO, layer=layer)
    _page(frame, "(open) => { if (open) window.openList(false); document.querySelector('#state-input').focus(); }",
          menu == "open")
    with pytest.raises(UnsafeAction, match="Enter outside an open list"):
        frame.press("Enter", intent=LIST_COMMIT, into=_node(frame, "#state-input"))
    owner_keys(frame, "\ue007")                    # the same Enter, raw
    if layer == "off":
        assert trap.wait_for_write() == [SUBMIT]
        return
    assert _quiet(trap) == []
    keydowns = [entry for entry in tab.new_vetoes() if entry["kind"] == "keydown"]
    assert [entry["reason"] for entry in keydowns] == ["enter"]
    assert _page(frame, "() => window.__enterPresses") == 0


def test_k3_list_commit_enter_commits_the_focused_option(tab, trap):
    frame = _serve(trap, tab, COMBO)
    _page(frame, "() => { window.openList(true); document.querySelector('#state-input').focus(); }")
    frame.press("Enter", intent=LIST_COMMIT, into=_node(frame, "#state-input"))
    assert _page(frame, "() => [document.querySelector('#state').value, window.__enterPresses]") == ["Texas", 1]
    assert _quiet(trap) == []
    assert tab.new_vetoes() == []


def test_arrow_keys_reach_the_list_and_nothing_else(tab, trap):
    frame = _serve(trap, tab, COMBO + """<script>
      box.addEventListener('keydown', event => { if (event.key === 'ArrowDown') window.openList(true); });
    </script>""")
    frame.call("() => document.querySelector('#state-input').focus()")
    frame.press("ArrowDown", intent=FIELD, into=_node(frame, "#state-input"))
    assert _page(frame, "() => document.querySelector('#state-input').getAttribute('aria-expanded')") == "true"
    frame.call("() => document.querySelector('button').focus()")
    with pytest.raises(UnsafeAction, match="focus is not on the field"):
        frame.press("ArrowDown", intent=FIELD, into=_node(frame, "#state-input"))
    assert tab.new_vetoes() == [] and _quiet(trap) == []


# ------------------------------------------------------------ C: clicks

C1 = """<form method="post" action="/trap/submit">
  <div id="wrap" style="position:relative;width:240px">
    <div role="option" id="option" style="height:40px">Texas</div>
  </div>
</form>
<script>
  // After kitty's inspection: a transparent submit button (no type, inside the form) over the option.
  document.querySelector('#option').addEventListener('mousemove', () => {
    if (document.querySelector('#swap')) return;
    const swap = document.createElement('button');
    swap.id = 'swap';
    swap.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:40px;opacity:0;z-index:5';
    document.querySelector('#wrap').append(swap);
  });
</script>"""


@LAYERS
def test_c1_overlay_swapped_in_after_inspection_is_vetoed(tab, trap, layer):
    frame = _serve(trap, tab, C1, layer=layer)
    option = _node(frame, "#option")
    if layer == "off":
        owner_click(frame, option)
        assert trap.wait_for_write() == [SUBMIT]
        return
    with pytest.raises(UnsafeAction, match="submit guard blocked"):
        frame.click(option, intent=FIELD)
    assert _quiet(trap) == []
    assert [entry["reason"] for entry in tab.vetoes] == ["missed_target"]
    # kitty's own diverted click is a defect, never mistaken for the owner acting
    assert veto_cause(tab.vetoes) == guard.SUBMIT_VETOED


def test_owner_click_while_kitty_types_is_the_owners_not_kittys(tab, trap):
    frame = _serve(trap, tab, T1)
    frame.call("() => { const city = document.querySelector('#city'); city.focus();"
               " globalThis.kittyGuard.keys(city, 'field'); }")      # kitty's keys are bound, as while typing
    owner_click(frame, _node(frame, "button"))
    assert _quiet(trap) == []
    vetoes = tab.new_vetoes()
    assert [(entry["reason"], entry["token"]) for entry in vetoes] == [("unguarded", False)]
    assert veto_cause(vetoes) == guard.OWNER_TOOK_OVER
    frame.call("() => globalThis.kittyGuard.settle()")


_C_CASES = {
    # C2: an option whose centre is a submit button (light DOM, and inside an open shadow root)
    "submit inside option": ("""<form method="post" action="/trap/submit">
        <div role="option" id="target" style="width:240px;height:40px">
          <button style="width:240px;height:40px">Texas</button></div></form>""",
                             "() => document.querySelector('#target')", "POST"),
    "submit inside shadow option": ("""<div id="host"></div><script>
        document.querySelector('#host').attachShadow({mode: 'open'}).innerHTML =
          '<form method="post" action="/trap/submit"><div role="option" id="target" style="width:240px;height:40px">' +
          '<button style="width:240px;height:40px">Texas</button></div></form>';</script>""",
                                    "() => document.querySelector('#host').shadowRoot.querySelector('#target')", "POST"),
    # C3: a label whose first labelable descendant is a submit button, beside a checkbox
    "label of a submit button": ("""<form method="post" action="/trap/submit">
        <input type="checkbox" id="terms"><label id="target" style="display:inline-block;width:240px;height:40px">
          I agree <button style="width:200px;height:36px">to the terms</button></label></form>""",
                                 "() => document.querySelector('#target')", "POST"),
    # C4: a link styled as an option
    "link styled as option": ("""<a role="option" id="target" href="/trap/submit?x=1"
        style="display:block;width:240px;height:40px">Texas</a>""",
                              "() => document.querySelector('#target')", "GET"),
}


@LAYERS
@pytest.mark.parametrize("case", list(_C_CASES))
def test_c2_c4_field_click_never_activates_a_submit_or_link(tab, trap, layer, case):
    body, locate, method = _C_CASES[case]
    frame = _serve(trap, tab, body, layer=layer)
    target = frame.node(locate)
    assert target is not None
    if layer == "off":
        owner_click(frame, target)
        deadline = time.monotonic() + 3
        while not trap.hits("/trap/submit") and time.monotonic() < deadline:
            time.sleep(0.05)
        assert trap.hits("/trap/submit") == [method]
        return
    with pytest.raises(UnsafeAction, match="field click would activate"):
        frame.click(target, intent=FIELD)
    time.sleep(0.8)
    assert trap.hits("/trap/submit") == []
    assert tab.new_vetoes() == []                  # refused before anything was sent


# ------------------------------------------------------------ E: exceptions

E1 = """<form id="login" method="post" action="/trap/login">
  <label>Email <input name="email" value="pat.synthetic@example.invalid"></label>
  <button id="signin" type="submit">Sign in</button>
</form>
<form id="application" method="post" action="/trap/submit" style="position:absolute;left:-9999px">
  <input name="answer" value="synthetic"><button id="apply" type="submit">Submit application</button>
</form>
<script>
  document.querySelector('#signin').addEventListener('click', event => {
    event.preventDefault();
    fetch('/trap/login', {method: 'POST', body: 'email=synthetic'});
    document.querySelector('#apply').click();      // the hidden application rides along
  });
</script>"""


@LAYERS
def test_e1_a_credential_click_cannot_carry_an_application_submit(tab, trap, layer):
    frame = _serve(trap, tab, E1, layer=layer)
    signin = _node(frame, "#signin")
    if layer == "off":
        owner_click(frame, signin)
        deadline = time.monotonic() + 3
        while SUBMIT not in trap.writes() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert SUBMIT in trap.writes()
        return
    with pytest.raises(UnsafeAction, match="submit guard blocked"):
        frame.click(signin, intent=CREDENTIAL)
    time.sleep(0.8)
    assert trap.writes() == [("POST", "/trap/login")]
    assert [entry["reason"] for entry in tab.vetoes] == ["nested"]


@LAYERS
def test_e1_login_handler_fetch_to_the_application_endpoint_is_blocked(tab, trap, layer):
    """L1 sees no activation in the handler's own fetch; the network tripwire (L2) fails it."""
    frame = _serve(trap, tab, E1.replace("document.querySelector('#apply').click();",
                                         "fetch('/trap/submit', {method: 'POST'});"), layer=layer)
    if layer == "off":
        owner_click(frame, _node(frame, "#signin"))
        deadline = time.monotonic() + 3
        while SUBMIT not in trap.writes() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert sorted(trap.writes()) == [("POST", "/trap/login"), SUBMIT]
        return
    with contextlib.suppress(guard.SubmitBlockedNetwork):
        frame.click(_node(frame, "#signin"), intent=CREDENTIAL)
    time.sleep(1.0)
    assert trap.writes() == [("POST", "/trap/login")]
    assert [(d.method, d.path, d.rule) for d in tab.network_blocks] == [("POST", "/trap/submit", "generic.submit")]
    with pytest.raises(guard.SubmitBlockedNetwork):
        tab.check_network()                        # the fill stops at its next step
    assert tab.vetoes == []


LOGIN = """<h1>Candidate sign in</h1><form method="post" action="/trap/login">
  <label>Email <input name="email" value="pat.synthetic@example.invalid"></label>
  <button id="signin" type="submit">Sign in</button></form>"""


def test_a_credential_token_lets_exactly_the_proven_native_submit_through(tab, trap):
    frame = _serve(trap, tab, LOGIN)
    signin = _node(frame, "#signin")
    with pytest.raises(UnsafeAction, match="field click would activate a submit control"):
        frame.click(signin, intent=FIELD)
    frame.click(signin, intent=CREDENTIAL)
    assert trap.wait_for_write() == [("POST", "/trap/login")]
    assert tab.vetoes == []


def _profile_page(extra: str, action: str = "/trap/submit") -> str:
    return f"""<h1>Create your profile</h1><p>Profile Bootstrapping</p>{extra}
      <form method="post" action="{action}">
        <label>First name <input name="first" value="Pat" required></label>
        <button id="submit" type="submit">Submit</button>
      </form>"""


@LAYERS
def test_e2_profile_gate_is_refused_on_a_page_that_also_submits_an_application(tab, trap, layer):
    frame = _serve(trap, tab, _profile_page("<p>Submit application for this role.</p>"),
                   layer=layer, path="/careers/apply")
    if layer == "off":
        owner_click(frame, _node(frame, "#submit"))
        assert trap.wait_for_write() == [SUBMIT]
        return
    config = SimpleNamespace(profile=SimpleNamespace(country="United States"))
    assert not submit_profile_bootstrap(tab, config, allow_terms=True)
    assert _quiet(trap) == []
    assert tab.new_vetoes() == []


def test_e2_control_the_same_profile_gate_alone_is_submitted_once_by_its_token(tab, trap):
    _serve(trap, tab, _profile_page("", action="/careers/profile"), path="/careers/apply")
    config = SimpleNamespace(profile=SimpleNamespace(country="United States"))
    submit_profile_bootstrap(tab, config, allow_terms=True)
    assert trap.wait_for_write() == [("POST", "/careers/profile")]
    assert tab.vetoes == [] and tab.network_blocks == []


def test_a_profile_gate_token_never_lets_a_submit_looking_post_through(tab, trap):
    """The token's same-origin allowance never outranks the deny list (N3 §4.2)."""
    _serve(trap, tab, _profile_page(""), path="/careers/apply")
    config = SimpleNamespace(profile=SimpleNamespace(country="United States"))
    with contextlib.suppress(guard.SubmitBlockedNetwork):
        submit_profile_bootstrap(tab, config, allow_terms=True)
    assert _quiet(trap) == []
    assert [(d.path, d.rule) for d in tab.network_blocks] == [("/trap/submit", "generic.submit")]


# ------------------------------------------------------------ W: Workday Review by position

WORKDAY = "https://synthetic.wd1.myworkdayjobs.com/en-US/External/job/City/Intern_R-1/apply/applyManually"


def _workday(active: int, labels: list[str], button: str) -> str:
    items = "".join(
        f'<li data-automation-id="progressBar{"Completed" if i < active else "Active" if i == active else "Inactive"}Step">'
        + (f"aktueller Schritt {i + 1} von {len(labels)} " if i == active else "") + label + "</li>"
        for i, label in enumerate(labels))
    return f"""<div data-automation-id="progressBar"><ol>{items}</ol></div>
      <label>Vorname <input name="first" value="Pat"></label>
      <button data-automation-id="pageFooterNextButton">{button}</button>
      <script>
        window.__clicks = 0;
        document.querySelector('button').addEventListener('click', () => {{
          window.__clicks++;
          fetch('/wday/cxs/t/job/x/submit', {{method: 'POST'}});
        }});
      </script>"""


_STEPS = ["Meine Informationen", "Meine Erfahrung", "Bewerbungsfragen", "Freiwillige Angaben", "Überprüfen"]


def _workday_snapshot(monkeypatch, *, steps_rule: bool) -> None:
    script = preflight._SNAPSHOT.replace("() => {", "() => { const location = new URL(" + repr(WORKDAY) + ");", 1)
    if not steps_rule:
        script = script.replace(preflight._WORKDAY_STEPS_JS, "() => ({index: 0, count: 2})")
    monkeypatch.setattr(preflight, "_SNAPSHOT", script)


@pytest.mark.parametrize(("steps_rule", "layer"), [("on", "on"), ("off", "on"), ("off", "off")])
def test_w1_localized_workday_review_is_refused_by_step_position(tab, trap, monkeypatch, steps_rule, layer):
    _workday_snapshot(monkeypatch, steps_rule=steps_rule == "on")
    if layer == "off":
        monkeypatch.setattr(bidi_module.Tab, "_arm_network", lambda self, guard: None)   # L1 only
    frame = _serve(trap, tab, _workday(len(_STEPS) - 1, _STEPS, "Next"), family="workday")
    snapshot = preflight.inspect_page(tab)
    if steps_rule == "off":
        # Without the step-position rule a localized Review looks like a normal step,
        # and Workday's Submit is its "Next": L1 admits the proven NEXT click.
        assert snapshot.safe_next and not snapshot.review
        with contextlib.suppress(guard.SubmitBlockedNetwork):
            preflight.advance(snapshot, "workday")
        if layer == "off":                          # control: nothing else stops the POST
            assert trap.wait_for_write() == [("POST", "/wday/cxs/t/job/x/submit")]
            return
        assert _quiet(trap) == []                   # L2: Workday's deny list fails it
        assert [(d.method, d.path, d.rule) for d in tab.network_blocks] == [
            ("POST", "/wday/cxs/t/job/x/submit", "workday.submit")]
        with pytest.raises(guard.SubmitBlockedNetwork):
            tab.check_network()
        return
    assert snapshot.review and not snapshot.safe_next
    with pytest.raises(UnsafeAction):
        preflight.advance(snapshot, "workday")
    assert _quiet(trap) == []
    assert _page(frame, "() => window.__clicks") == 0


def _local(family: str) -> guard.Profile:
    """A family profile whose host-bound rules also match the trap's 127.0.0.1 (fixtures only)."""
    profile = guard.PROFILES[family]

    def local(rules):
        return tuple(dataclasses.replace(rule, hosts=("127.0.0.1",)) if rule.hosts else rule for rule in rules)

    return dataclasses.replace(profile, deny=local(profile.deny), allow=local(profile.allow),
                               credential_only=local(profile.credential_only))


def test_workday_next_on_a_localized_earlier_step_is_still_allowed(tab, trap, monkeypatch):
    """W2: a normal step's draft save is on Workday's allowlist; the deny rule does not block it."""
    _workday_snapshot(monkeypatch, steps_rule=True)
    frame = _serve(trap, tab, _workday(1, _STEPS, "Save and Continue").replace(
        "/wday/cxs/t/job/x/submit", "/wday/cxs/t/job/x/jobapplications"), family=_local("workday"))
    snapshot = preflight.inspect_page(tab)
    assert snapshot.safe_next and not snapshot.review
    preflight.advance(snapshot, "workday")
    assert trap.wait_for_write() == [("POST", "/wday/cxs/t/job/x/jobapplications")]
    assert _page(frame, "() => window.__clicks") == 1
    assert tab.new_vetoes() == [] and tab.network_blocks == []
    assert {"method": "POST", "path": "/wday/cxs/t/job/x/jobapplications", "endpoint": "workday.draft",
            "decision": "continue"} in tab.network_log


def _with_bar(bar: str, extra: str = "", button: str = "Next") -> str:
    """A Workday-like step page whose progress bar is `bar` (plus other page markup)."""
    return re.sub(r'<div data-automation-id="progressBar">.*?</div>', lambda _: bar,
                  _workday(1, _STEPS, button), count=1, flags=re.S) + extra


_UNREADABLE = '<div data-automation-id="progressBar"><span data-automation-id="progressBarActiveStep">Angaben</span></div>'


def test_workday_next_refuses_an_unreadable_progress_bar_and_a_review_container(tab, trap, monkeypatch):
    _workday_snapshot(monkeypatch, steps_rule=True)
    _serve(trap, tab, _with_bar(_UNREADABLE))
    assert not preflight.inspect_page(tab).safe_next      # one marked item, no list, no numbers
    _serve(trap, tab, _workday(1, _STEPS, "Next") +
           '<section data-automation-id="reviewJobApplicationSection"><h2>Angaben</h2></section>')
    snapshot = preflight.inspect_page(tab)
    assert not snapshot.safe_next
    with pytest.raises(UnsafeAction):
        preflight.advance(snapshot, "workday")
    assert _quiet(trap) == []


@pytest.mark.parametrize("marker", ['aria-current="step"', 'aria-selected="true"'])
@pytest.mark.parametrize(("active", "safe"), [(1, True), (4, False)])
def test_workday_step_position_from_aria_marker_among_plain_list_items(tab, trap, monkeypatch, marker, active, safe):
    _workday_snapshot(monkeypatch, steps_rule=True)
    items = "".join(f"<li{' ' + marker if i == active else ''}><span>{label}</span></li>"
                    for i, label in enumerate(_STEPS))
    _serve(trap, tab, _with_bar(f'<div data-automation-id="progressBar"><ol>{items}</ol></div>'))
    snapshot = preflight.inspect_page(tab)
    assert (snapshot.safe_next, snapshot.review) == (safe, not safe)


def test_workday_step_numbers_are_read_as_rendered_not_from_hidden_text(tab, trap, monkeypatch):
    _workday_snapshot(monkeypatch, steps_rule=True)
    bar = ('<div data-automation-id="progressBar"><span data-automation-id="progressBarActiveStep">'
           '<span style="display:none">Schritt 1 von 5</span>Schritt 5 von 5 Überprüfen</span></div>')
    _serve(trap, tab, _with_bar(bar))
    snapshot = preflight.inspect_page(tab)
    assert snapshot.review and not snapshot.safe_next


@pytest.mark.parametrize("page", preflight._WORKDAY_STEP_PAGES)
def test_workday_earlier_step_page_id_allows_next_when_the_bar_is_unreadable(tab, trap, monkeypatch, page):
    _workday_snapshot(monkeypatch, steps_rule=True)
    _serve(trap, tab, _with_bar(_UNREADABLE, f'<div data-automation-id="{page}"><h2>Angaben</h2></div>'))
    snapshot = preflight.inspect_page(tab)
    assert snapshot.safe_next and not snapshot.review


@pytest.mark.parametrize("page", preflight._WORKDAY_REVIEW_PAGES)
def test_workday_review_page_id_is_final_whatever_the_bar_says(tab, trap, monkeypatch, page):
    _workday_snapshot(monkeypatch, steps_rule=True)
    _serve(trap, tab, _workday(1, _STEPS, "Next") + f'<div data-automation-id="{page}"><h2>Angaben</h2></div>')
    snapshot = preflight.inspect_page(tab)
    assert snapshot.review and not snapshot.safe_next
    with pytest.raises(UnsafeAction):
        preflight.advance(snapshot, "workday")
    assert _quiet(trap) == []


_FINAL_WORDS = ["Invia", "Prześlij", "送信", "Enviar", "Envoyer", "Absenden", "Senden", "Verzenden",
                "Indsend", "Skicka", "Lähetä", "Odeslat", "提交", "제출", "Bewerbung absenden", "Submit"]


@LAYERS
def test_final_labels_in_common_locales_are_never_field_clicks(tab, trap, layer):
    buttons = "".join(f'<button type="button" id="b{i}" style="display:block;width:200px;height:30px">{word}</button>'
                      for i, word in enumerate(_FINAL_WORDS))
    frame = _serve(trap, tab, buttons + '<button type="button" id="benign" style="display:block;width:200px;height:30px">'
                   'Weiter</button>', layer=layer)
    for i, word in enumerate(_FINAL_WORDS):
        with pytest.raises(UnsafeAction, match="final action"):
            frame.click(_node(frame, f"#b{i}"), intent=FIELD)
    frame.click(_node(frame, "#benign"), intent=FIELD)
    if layer == "on":                               # the owner's own click on one is vetoed too
        owner_click(frame, _node(frame, "#b0"))
        assert [entry["reason"] for entry in tab.new_vetoes()] == ["unguarded"]


# ------------------------------------------------------------ lifting the veto (N3 §7)

# No required field: a click on its Submit would submit at once unless something blocks it.
APPLICATION = """<form id="application" method="post" action="/trap/submit">
  <label>First name <input name="first_name" autocomplete="given-name"></label>
  <button id="submit" type="submit">Submit application</button>
</form>"""


def _application_job(space, trap):  # noqa: F811
    trap.pages["/apply"] = "<!doctype html><html><body>" + APPLICATION + "</body></html>"
    job, _ = jobs_core.upsert_job(space.conn, company="Synthetic Co", title="Guarded intern",
                                  url=trap.url("/apply"), tracks=[space.track.name],
                                  location="Arlington, TX, United States", country="US")
    return job


def test_l1_after_a_fill_the_owners_submit_reaches_the_server_exactly_once(space, tab, trap):  # noqa: F811
    report = fill_job(tab, space.conn, space.settings, space.config, _application_job(space, trap))
    assert report.guard_released and not tab.armed
    assert _page(tab.main, "() => document.querySelector('[name=first_name]').value") == "Pat"
    assert tab.vetoes == [] and trap.writes() == []
    owner_click(tab.main, _node(tab.main, "#submit"))
    assert trap.wait_for_write() == [SUBMIT]
    time.sleep(0.5)
    assert trap.writes() == [SUBMIT]


@pytest.mark.parametrize("outcome", ["ready", "blocked"])
def test_l2_owner_click_during_a_fill_stops_it_and_releases_the_tab(space, tab, trap, monkeypatch, outcome):  # noqa: F811
    from kitty.core.apply import filler

    real = filler.fill_form

    def owner_presses_submit_mid_fill(page, *args, **kwargs):
        owner_click(page.main, _node(page.main, "#submit"))
        result = real(page, *args, **kwargs)
        # "ready": the page would otherwise be handed over as ready; "blocked": it
        # stops for its own reason (no verified resume), which keeps its cause.
        result.resume_verified = outcome == "ready"
        return result

    monkeypatch.setattr(filler, "fill_form", owner_presses_submit_mid_fill)
    report = fill_job(tab, space.conn, space.settings, space.config, _application_job(space, trap))
    cause = space.conn.execute("SELECT cause FROM apply_attempts WHERE id = ?", (report.attempt_id,)).fetchone()[0]
    assert report.status == "blocked"
    if outcome == "ready":
        assert report.note == guard.OWNER_NOTE and report.cause == "human_boundary"
        assert cause == guard.OWNER_TOOK_OVER
    else:
        assert report.note.startswith("Resume upload is not verified") and report.note.endswith(guard.OWNER_HINT)
        assert cause == "resume_unverified"
    assert report.guard_released and not tab.armed
    assert veto_cause(tab.vetoes) == guard.OWNER_TOOK_OVER
    assert trap.writes() == []
    owner_click(tab.main, _node(tab.main, "#submit"))     # "press Submit again"
    assert trap.wait_for_write() == [SUBMIT]
    time.sleep(0.5)
    assert trap.writes() == [SUBMIT]


def test_the_veto_lapses_by_itself_when_kitty_stops_renewing_it(tab, trap, monkeypatch):
    monkeypatch.setattr(bidi_module, "LEASE_MS", 1500)
    monkeypatch.setattr(bidi_module, "HEARTBEAT_SECONDS", 60.0)
    # L1's lease alone: an orphaned L2 intercept would hold the request (test_submit_network).
    monkeypatch.setattr(bidi_module.Tab, "_arm_network", lambda self, guard: None)
    frame = _serve(trap, tab, APPLICATION, layer="off")
    tab.arm(family="custom")                       # current frame, 1.5 s lease, no heartbeat yet
    submit = _node(frame, "#submit")               # kitty's last sandbox call renews it once
    owner_click(frame, submit)
    assert trap.wait_for_write(0.5) == []
    time.sleep(2.0)                                # kitty "died": nothing renews the lease
    owner_click(frame, submit)
    assert trap.wait_for_write() == [SUBMIT]


def test_kitty_never_acts_in_a_released_tab_and_never_clicks_mail_in_an_armed_one(tab, trap):
    frame = _serve(trap, tab, T1)
    city = _node(frame, "#city")
    with pytest.raises(UnsafeAction, match="mailbox"):
        frame.click(city, intent=MAILBOX)
    with pytest.raises(UnsafeAction, match="next click would activate a submit control"):
        frame.click(_node(frame, "button"), intent=NEXT)
    tab.release()
    assert not tab.armed
    with pytest.raises(UnsafeAction, match="released"):
        frame.click(city, intent=FIELD)
    with pytest.raises(UnsafeAction, match="released"):
        frame.type("Austin", into=city)
    assert _quiet(trap) == []


def test_new_documents_and_iframes_are_armed_before_their_scripts_run(tab, trap):
    trap.pages["/inner"] = "<!doctype html>" + APPLICATION
    frame = _serve(trap, tab, '<iframe src="/inner" style="width:600px;height:300px"></iframe>')
    deadline = time.monotonic() + 5
    while len(tab.frames()) < 2 and time.monotonic() < deadline:
        time.sleep(0.1)
    inner = tab.frames()[1]
    assert inner.call("() => !!globalThis.kittyGuard && globalThis.kittyGuard.armed()")
    assert frame.call("() => !!globalThis.kittyGuard && globalThis.kittyGuard.armed()")
    assert inner.call("() => typeof window.kittyGuard", page_realm=True) == "undefined"   # never page-visible
    owner_click(inner, _node(inner, "#submit"))
    assert _quiet(trap) == []
    assert veto_cause(tab.new_vetoes()) == guard.OWNER_TOOK_OVER
