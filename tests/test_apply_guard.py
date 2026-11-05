"""The network tripwire's pure request policy (no-submit layer L2, N3 §4.4-4.5).

Every rule of N3 §4.4 and every row of the §4.5 family table has a case here.
Events are shaped like WebDriver BiDi `network.beforeRequestSent` parameters,
with synthetic headers and cookies that the policy must never read or keep.
"""

from __future__ import annotations

import dataclasses

import pytest

from kitty.core.apply.guard import (
    CONTINUE,
    CREDENTIAL,
    ENTRY,
    FAIL,
    FIELD,
    NEXT,
    PROFILE_GATE,
    PROFILES,
    RECORD_SAVE,
    Request,
    Token,
    policy,
    profile_for,
)

WORKDAY = "https://acme.wd5.myworkdayjobs.com"
GREENHOUSE = "https://job-boards.greenhouse.io"
LEVER = "https://jobs.lever.co"
SMARTRECRUITERS = "https://jobs.smartrecruiters.com"
ASHBY = "https://jobs.ashbyhq.com"
ORACLE = "https://eeho.fa.us2.oraclecloud.com"
CUSTOM = "https://careers.example.invalid"


def event(method: str, url: str, *, navigation: bool = False, destination: str = "",
          context: str = "tab-1") -> dict:
    """A beforeRequestSent event carrying a synthetic secret in its headers and cookies."""
    return {
        "context": context,
        "isBlocked": True,
        "navigation": "nav-1" if navigation else None,
        "redirectCount": 0,
        "request": {
            "request": "req-1",
            "url": url,
            "method": method,
            "headers": [{"name": "Authorization", "value": {"type": "string", "value": "synthetic-secret"}}],
            "cookies": [{"name": "session", "value": {"type": "string", "value": "synthetic-secret"}}],
            "headersSize": 0,
            "bodySize": 12,
            "destination": destination,
            "initiatorType": "fetch",
        },
        "timestamp": 0,
        "intercepts": ["intercept-1"],
    }


def decide(method, url, family, tokens=(), **kwargs):
    return policy(event(method, url, **kwargs), family, tokens)


def verdict(method, url, family, tokens=(), **kwargs):
    return decide(method, url, family, tokens, **kwargs).verdict


# ------------------------------------------------------------ request reading


def test_request_keeps_only_method_origin_path_navigation_and_context():
    request = Request.from_event(event(
        "post", "https://User:pw@Careers.Example.Invalid:443/a%2Fb/Submit?token=abc&op=Op#frag",
        navigation=True,
    ))
    assert {field.name for field in dataclasses.fields(Request)} == {
        "method", "origin", "path", "query_keys", "operation", "navigation", "context",
    }
    assert request.method == "POST"
    assert request.origin == "https://careers.example.invalid"
    assert request.path == "/a/b/Submit"
    assert request.query_keys == frozenset({"token", "op"})
    assert request.operation == "Op"
    assert request.navigation is True
    assert request.context == "tab-1"
    assert "synthetic-secret" not in repr(request)
    assert "abc" not in repr(request)


def test_document_destinations_count_as_navigations_even_without_a_navigation_id():
    for destination in ("document", "iframe", "frame"):
        assert Request.from_event(event("POST", CUSTOM + "/x", destination=destination)).navigation


def test_decision_log_entry_has_method_path_endpoint_and_decision_only():
    decision = decide("POST", CUSTOM + "/careers/submit?candidate=pat@example.invalid", "other")
    entry = decision.log_entry()
    assert entry == {"method": "POST", "path": "/careers/submit", "endpoint": "generic.submit",
                     "decision": "fail"}
    assert "pat@example.invalid" not in repr(decision)
    assert "synthetic-secret" not in repr(decision)


def test_policy_accepts_a_parsed_request_too():
    request = Request.from_event(event("GET", CUSTOM + "/"))
    assert policy(request, "other", ()).verdict == CONTINUE


def test_unknown_families_use_the_custom_profile():
    assert profile_for("other") is PROFILES["custom"]
    assert profile_for("workable") is PROFILES["custom"]
    assert profile_for("dayforce") is PROFILES["custom"]
    assert profile_for("") is PROFILES["custom"]
    for family in ("workday", "greenhouse", "lever", "smartrecruiters", "ashby", "oracle"):
        assert profile_for(family) is PROFILES[family]


# ------------------------------------------------------------ §4.4 rules


@pytest.mark.parametrize("family", sorted(PROFILES))
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "get"])
def test_rule1_safe_methods_without_navigation_continue(family, method):
    decision = decide(method, CUSTOM + "/careers/submit?finalize=1", family)
    assert decision.verdict == CONTINUE and decision.rule == "safe_method"


@pytest.mark.parametrize("family", sorted(PROFILES))
def test_rule2_get_navigations_continue(family):
    decision = decide("GET", CUSTOM + "/jobs/123/apply", family, navigation=True)
    assert decision.verdict == CONTINUE and decision.rule == "get_navigation"


def test_rule2_get_navigation_on_the_custom_deny_list_fails():
    # R11: a legacy site whose application submit is a GET navigation.
    decision = decide("GET", CUSTOM + "/apply/sendApplication", "other", navigation=True)
    assert decision.verdict == FAIL and decision.rule == "generic.submit"


@pytest.mark.parametrize("family", ["workday", "greenhouse", "lever", "smartrecruiters", "ashby", "oracle"])
def test_rule2_non_get_deny_rules_do_not_fail_get_navigations(family):
    # Ashby's own application tab is a GET navigation to …/application.
    assert verdict("GET", ASHBY + "/acme/uuid/application", family, navigation=True) == CONTINUE
    assert verdict("GET", WORKDAY + "/wday/cxs/acme/site/job/x/submit", family, navigation=True) == CONTINUE


@pytest.mark.parametrize("family", sorted(PROFILES))
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_rule3_non_get_navigation_without_a_token_fails(family, method):
    decision = decide(method, CUSTOM + "/careers/form", family, navigation=True)
    assert decision.verdict == FAIL and decision.rule == "navigation_post"


@pytest.mark.parametrize("intent", [CREDENTIAL, PROFILE_GATE])
def test_rule3_credential_and_profile_tokens_allow_a_same_origin_navigation_post(intent):
    decision = decide("POST", CUSTOM + "/account/login", "other", (Token(intent, CUSTOM),),
                      navigation=True)
    assert decision.verdict == CONTINUE and decision.rule == f"token.{intent}"


def test_rule3_token_for_another_origin_does_not_cover_the_navigation_post():
    tokens = (Token(CREDENTIAL, "https://login.example.invalid"),)
    assert verdict("POST", CUSTOM + "/account/login", "other", tokens, navigation=True) == FAIL


def test_rule3_token_origin_comparison_ignores_case_and_default_port():
    tokens = (Token(CREDENTIAL, "HTTPS://Careers.Example.Invalid:443/any/path"),)
    assert verdict("POST", CUSTOM + "/account/login", "other", tokens, navigation=True) == CONTINUE


def test_rule3_token_never_covers_a_navigation_post_on_the_deny_list():
    tokens = (Token(CREDENTIAL, CUSTOM), Token(PROFILE_GATE, CUSTOM))
    decision = decide("POST", CUSTOM + "/application/submit", "other", tokens, navigation=True)
    assert decision.verdict == FAIL and decision.rule == "generic.submit"
    tokens = (Token(CREDENTIAL, LEVER),)
    decision = decide("POST", LEVER + "/acme/uuid/apply", "lever", tokens, navigation=True)
    assert decision.verdict == FAIL and decision.rule == "lever.apply"


@pytest.mark.parametrize("family", sorted(PROFILES))
def test_rule3_next_token_does_not_allow_a_navigation_post_unless_the_profile_says_so(family):
    assert not PROFILES[family].next_navigates_by_post
    tokens = (Token(NEXT, CUSTOM),)
    assert verdict("POST", CUSTOM + "/careers/step", family, tokens, navigation=True) == FAIL


def test_rule3_next_token_allows_a_navigation_post_when_the_profile_navigates_by_post():
    profile = dataclasses.replace(PROFILES["custom"], next_navigates_by_post=True)
    tokens = (Token(NEXT, CUSTOM),)
    decision = policy(event("POST", CUSTOM + "/careers/step", navigation=True), profile, tokens)
    assert decision.verdict == CONTINUE and decision.rule == "token.next"
    denied = policy(event("POST", CUSTOM + "/careers/finalize", navigation=True), profile, tokens)
    assert denied.verdict == FAIL


@pytest.mark.parametrize("intent", [FIELD, ENTRY, RECORD_SAVE])
def test_rule3_other_intents_never_allow_a_navigation_post(intent):
    assert verdict("POST", CUSTOM + "/careers/step", "other", (Token(intent, CUSTOM),),
                   navigation=True) == FAIL


def test_rule4_non_get_fetch_is_decided_by_the_family_profile():
    url = CUSTOM + "/api/answers"
    assert decide("POST", url, "other").rule == "custom.unlisted"
    assert decide("POST", url, "greenhouse").rule == "greenhouse.unlisted"
    assert verdict("POST", url, "other") == CONTINUE
    assert verdict("POST", url, "greenhouse") == FAIL


@pytest.mark.parametrize("intent", [CREDENTIAL, PROFILE_GATE])
def test_rule4_credential_tokens_allow_same_origin_fetches_not_on_the_deny_list(intent):
    tokens = (Token(intent, GREENHOUSE),)
    decision = decide("POST", GREENHOUSE + "/users/sign_in", "greenhouse", tokens)
    assert decision.verdict == CONTINUE and decision.rule == f"token.{intent}"
    assert verdict("POST", "https://other.example.invalid/x", "greenhouse", tokens) == FAIL
    assert verdict("POST", ASHBY + "/api/non-user-graphql?op=ApiSubmitSingleApplicationFormAction",
                   "ashby", (Token(intent, ASHBY),)) == FAIL


@pytest.mark.parametrize("family", ["workday", "greenhouse", "lever", "smartrecruiters", "ashby", "oracle"])
def test_rule4_unlisted_non_gets_are_observed_not_failed_in_observe_mode(family):
    request = event("POST", "https://analytics.example.invalid/collect", context="tab-1")
    enforced = policy(request, family, ())
    observed = policy(request, family, (), allowlist="observe")
    assert enforced.verdict == FAIL and not enforced.observed
    assert observed.verdict == CONTINUE and observed.observed
    assert observed.rule == enforced.rule == f"{family}.unlisted"


@pytest.mark.parametrize("family", sorted(PROFILES))
def test_rule4_observe_mode_never_relaxes_a_deny_rule(family):
    submit = event("POST", CUSTOM + "/careers/application/submit")
    assert policy(submit, family, (), allowlist="observe").verdict == FAIL
    navigation = event("POST", CUSTOM + "/careers/step", navigation=True)
    assert policy(navigation, family, (), allowlist="observe").verdict == FAIL


def test_policy_rejects_an_unknown_allowlist_mode():
    with pytest.raises(ValueError):
        policy(event("POST", CUSTOM + "/x"), "other", (), allowlist="off")


# ------------------------------------------------------------ §4.5 Workday


def test_workday_allows_draft_create():
    decision = decide("POST", WORKDAY + "/wday/cxs/acme/External/job/R1/jobapplications", "workday")
    assert decision.verdict == CONTINUE and decision.rule == "workday.draft"


def test_workday_allows_questionnaire_definition():
    decision = decide("POST", WORKDAY + "/wday/cxs/acme/External/questionnaire/abc123/definition", "workday")
    assert decision.verdict == CONTINUE and decision.rule == "workday.questionnaire"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
def test_workday_allows_step_saves_under_wday(method):
    decision = decide(method, WORKDAY + "/wday/cxs/acme/External/jobapplications/A1/myInformation", "workday")
    assert decision.verdict == CONTINUE and decision.rule == "workday.step_save"


def test_workday_step_saves_only_on_workday_hosts():
    assert verdict("POST", CUSTOM + "/wday/cxs/acme/External/save", "workday") == FAIL


@pytest.mark.parametrize("path", ["/wday/authgwy/acme/login", "/wday/authgwy/acme/register"])
def test_workday_login_and_register_only_under_a_credential_token(path):
    without = decide("POST", WORKDAY + path, "workday")
    assert without.verdict == FAIL and without.rule == "workday.credential"
    assert policy(event("POST", WORKDAY + path), "workday", (), allowlist="observe").verdict == FAIL
    assert verdict("POST", WORKDAY + path, "workday", (Token(FIELD, WORKDAY),)) == FAIL
    with_token = decide("POST", WORKDAY + path, "workday", (Token(CREDENTIAL, WORKDAY),))
    assert with_token.verdict == CONTINUE and with_token.rule == "token.credential"


@pytest.mark.parametrize("path", [
    "/wday/cxs/acme/External/job/x/submit",
    "/wday/cxs/acme/External/jobapplications/A1/submit/",
    "/wday/cxs/acme/External/jobapplications/A1/SUBMIT",
])
def test_workday_denies_post_ending_submit_under_wday(path):
    decision = decide("POST", WORKDAY + path, "workday", (Token(CREDENTIAL, WORKDAY),))
    assert decision.verdict == FAIL and decision.rule == "workday.submit"


def test_workday_denies_any_non_get_while_the_snapshot_says_review():
    url = WORKDAY + "/wday/cxs/acme/External/jobapplications/A1/myInformation"
    decision = policy(event("POST", url), "workday", (Token(CREDENTIAL, WORKDAY),), review=True)
    assert decision.verdict == FAIL and decision.rule == "workday.review"
    assert policy(event("GET", url), "workday", (), review=True).verdict == CONTINUE
    assert policy(event("POST", url), "workday", (), review=False).verdict == CONTINUE


# ------------------------------------------------------------ §4.5 Greenhouse


@pytest.mark.parametrize("url", [
    GREENHOUSE + "/embed/job_app/upload",
    "https://boards.greenhouse.io/acme/attachments",
])
def test_greenhouse_allows_file_uploads(url):
    decision = decide("POST", url, "greenhouse")
    assert decision.verdict == CONTINUE and decision.rule == "greenhouse.upload"


@pytest.mark.parametrize("url", [
    GREENHOUSE + "/embed/job_app",
    "https://boards-api.greenhouse.io/v1/boards/acme/jobs/1",
])
def test_greenhouse_fails_every_other_non_get_to_its_hosts(url):
    decision = decide("POST", url, "greenhouse")
    assert decision.verdict == FAIL and decision.rule == "greenhouse.unlisted"


def test_greenhouse_uploads_are_allowed_only_on_greenhouse_hosts():
    assert verdict("POST", CUSTOM + "/upload", "greenhouse") == FAIL


def test_greenhouse_fails_any_navigation_post():
    tokens = (Token(FIELD, GREENHOUSE), Token(NEXT, GREENHOUSE))
    decision = decide("POST", GREENHOUSE + "/embed/job_app", "greenhouse", tokens, navigation=True)
    assert decision.verdict == FAIL and decision.rule == "navigation_post"


def test_greenhouse_application_post_is_denied_even_while_observing():
    request = event("POST", GREENHOUSE + "/v1/boards/acme/jobs/1/applications")
    assert policy(request, "greenhouse", (), allowlist="observe").verdict == FAIL


# ------------------------------------------------------------ §4.5 Lever


@pytest.mark.parametrize("path", ["/parseResume", "/uploadResume", "/acme/upload-resume"])
def test_lever_allows_resume_upload_or_parse(path):
    decision = decide("POST", LEVER + path, "lever")
    assert decision.verdict == CONTINUE and decision.rule == "lever.resume"


@pytest.mark.parametrize("navigation", [True, False])
def test_lever_denies_posts_to_apply(navigation):
    tokens = (Token(CREDENTIAL, LEVER),)
    decision = decide("POST", LEVER + "/acme/0c1d2e3f/apply", "lever", tokens, navigation=navigation)
    assert decision.verdict == FAIL and decision.rule == "lever.apply"


def test_lever_fails_every_other_non_get():
    decision = decide("POST", LEVER + "/acme/0c1d2e3f/questions", "lever")
    assert decision.verdict == FAIL and decision.rule == "lever.unlisted"


# ------------------------------------------------------------ §4.5 SmartRecruiters


def test_smartrecruiters_allows_resume_attachment():
    decision = decide("POST", SMARTRECRUITERS + "/oneclick-ui/api/company/acme/attachment/resume",
                      "smartrecruiters")
    assert decision.verdict == CONTINUE and decision.rule == "smartrecruiters.resume"


@pytest.mark.parametrize("url", [
    SMARTRECRUITERS + "/oneclick-ui/api/company/acme/profile",
    "https://api.smartrecruiters.com/v1/postings/1/candidates",
])
def test_smartrecruiters_fails_every_other_non_get_to_its_hosts(url):
    decision = decide("POST", url, "smartrecruiters")
    assert decision.verdict == FAIL and decision.rule == "smartrecruiters.unlisted"


def test_smartrecruiters_resume_rule_is_exact():
    assert verdict("DELETE", SMARTRECRUITERS + "/oneclick-ui/api/company/acme/attachment/resume",
                   "smartrecruiters") == FAIL


# ------------------------------------------------------------ §4.5 Ashby


@pytest.mark.parametrize("operation,rule", [
    ("ApiSetFormValue", "ashby.set_form_value"),
    ("ApiJobPosting", "ashby.job_posting"),
])
def test_ashby_allows_form_value_and_posting_operations(operation, rule):
    decision = decide("POST", f"{ASHBY}/api/non-user-graphql?op={operation}", "ashby")
    assert decision.verdict == CONTINUE and decision.rule == rule


@pytest.mark.parametrize("operation", [
    "ApiSubmitSingleApplicationFormAction", "ApiSubmitMultipleFormsAction",
])
def test_ashby_denies_submit_operations(operation):
    tokens = (Token(CREDENTIAL, ASHBY),)
    decision = decide("POST", f"{ASHBY}/api/non-user-graphql?op={operation}", "ashby", tokens)
    assert decision.verdict == FAIL and decision.rule == "ashby.submit"
    assert policy(event("POST", f"{ASHBY}/api/non-user-graphql?op={operation}"), "ashby", (),
                  allowlist="observe").verdict == FAIL


def test_ashby_fails_other_operations():
    decision = decide("POST", f"{ASHBY}/api/non-user-graphql?op=ApiOrganizationFromHostedJobsPageName", "ashby")
    assert decision.verdict == FAIL and decision.rule == "ashby.unlisted"


def test_ashby_allowed_operations_only_on_ashby_graphql():
    assert verdict("POST", f"{CUSTOM}/api/non-user-graphql?op=ApiSetFormValue", "ashby") == FAIL


# ------------------------------------------------------------ §4.5 Oracle


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_oracle_allows_application_drafts(method):
    url = ORACLE + "/hcmRestApi/resources/latest/recruitingCEJobApplicationDrafts/300000"
    decision = decide(method, url, "oracle")
    assert decision.verdict == CONTINUE and decision.rule == "oracle.draft"


def test_oracle_allows_attachments():
    url = ORACLE + "/hcmRestApi/resources/latest/recruitingCEJobApplicationDrafts/300000/child/attachments"
    assert verdict("POST", url, "oracle") == CONTINUE


def test_oracle_attachment_rule():
    decision = decide("POST", ORACLE + "/hcmRestApi/resources/latest/recruitingCECandidateAttachments", "oracle")
    assert decision.verdict == CONTINUE and decision.rule == "oracle.attachment"


@pytest.mark.parametrize("path", [
    "/hcmRestApi/resources/latest/recruitingCEJobApplications",
    "/hcmRestApi/resources/latest/recruitingCEJobApplicationDrafts/300000/action/submit",
])
def test_oracle_denies_the_generic_submit_pattern(path):
    decision = decide("POST", ORACLE + path, "oracle")
    assert decision.verdict == FAIL and decision.rule == "generic.submit"


def test_oracle_fails_other_non_gets():
    decision = decide("POST", ORACLE + "/hcmRestApi/resources/latest/recruitingCESites", "oracle")
    assert decision.verdict == FAIL and decision.rule == "oracle.unlisted"


# ------------------------------------------------------------ §4.5 custom / unknown


def test_custom_lets_other_non_gets_continue():
    decision = decide("PUT", CUSTOM + "/api/profile/draft", "other")
    assert decision.verdict == CONTINUE and decision.rule == "custom.unlisted"
    assert not decision.observed


def test_custom_fails_navigation_post_without_a_token():
    assert decide("POST", CUSTOM + "/careers/form", "other", navigation=True).rule == "navigation_post"


@pytest.mark.parametrize("path", [
    "/api/submit", "/api/Submission", "/apply/finalize", "/apply/finalise",
    "/apply/sendApplication", "/api/applications", "/api/application/", "/a%2Fsubmit",
])
def test_custom_denies_the_generic_submit_path_pattern(path):
    decision = decide("POST", CUSTOM + path, "other")
    assert decision.verdict == FAIL and decision.rule == "generic.submit"


@pytest.mark.parametrize("query", ["?submit=1", "?application=1", "?finalize", "?Submission=yes"])
def test_custom_denies_the_generic_submit_query_key(query):
    decision = decide("POST", CUSTOM + "/api/step" + query, "other")
    assert decision.verdict == FAIL and decision.rule == "generic.submit"


def test_custom_generic_pattern_does_not_match_query_values():
    assert verdict("POST", CUSTOM + "/api/step?next=submit", "other") == CONTINUE


@pytest.mark.parametrize("family", ["workday", "greenhouse", "lever", "smartrecruiters", "ashby"])
def test_generic_submit_pattern_backs_up_every_family_profile(family):
    # Stricter than the §4.5 table: an unlisted non-GET that looks like a
    # submit fails even while the family's allowlist is only observed.
    request = event("POST", "https://api.example.invalid/v2/candidates/submit")
    decision = policy(request, family, (), allowlist="observe")
    assert decision.verdict == FAIL and decision.rule == "generic.submit"
