"""Tests for the Consent Mode checks."""

import pytest

from src.consent_mode import gcs_states, gcs_value
from src.consent_results import Expectations, PhaseObservation
from src.consent_validator import CONSENT_COOKIE, judge

GCM = Expectations(gcm_enabled=True)
DENIED = {
    "ad_storage": "denied",
    "ad_user_data": "denied",
    "ad_personalization": "denied",
    "analytics_storage": "denied",
    "wait_for_update": 500,
}
GRANTED = {k: "granted" for k in DENIED if k != "wait_for_update"}
COLLECT = "https://www.google-analytics.com/g/collect?v=2&gcs="


def default(state=None, t=1.0) -> dict:
    return {"command": "default", "state": DENIED if state is None else state, "t": t}


def update(state=None, t=100.0) -> dict:
    return {"command": "update", "state": GRANTED if state is None else state, "t": t}


def before(**kwargs) -> PhaseObservation:
    kwargs.setdefault("consent_commands", [default()])
    kwargs.setdefault("consent_state_requests", [COLLECT + "G100"])
    return PhaseObservation(
        "before_consent", loaded=True, banner_found=True, observed=True, **kwargs
    )


def accept(**kwargs) -> PhaseObservation:
    kwargs.setdefault("consent_commands", [default(), update()])
    kwargs.setdefault("consent_state_requests", [COLLECT + "G111"])
    return PhaseObservation(
        "after_accept",
        loaded=True,
        banner_found=True,
        clicked=True,
        observed=True,
        click_time=50.0,
        cookies=[{"name": CONSENT_COOKIE, "domain": "example.com"}],
        **kwargs,
    )


def reject(**kwargs) -> PhaseObservation:
    denied = {k: v for k, v in DENIED.items() if k != "wait_for_update"}
    kwargs.setdefault("consent_commands", [default(), update(denied)])
    kwargs.setdefault("consent_state_requests", [COLLECT + "G100"])
    return PhaseObservation(
        "after_reject",
        loaded=True,
        banner_found=True,
        clicked=True,
        observed=True,
        click_time=50.0,
        cookies=[{"name": CONSENT_COOKIE, "domain": "example.com"}],
        **kwargs,
    )


def checks(expect=GCM, b=None, a=None, r=None) -> dict:
    found = judge(expect, b or before(), a or accept(), r or reject())
    return {c.key: c for c in found}


KEYS = (
    "before_consent.gcm_default",
    "before_consent.gcm_denied",
    "before_consent.gcm_wait",
    "after_accept.gcm_update",
    "after_reject.gcm_denied",
    "gcm_received",
)


class TestGcs:
    def test_reads_the_parameter(self):
        assert gcs_value(COLLECT + "G110") == "G110"
        assert gcs_value("https://www.google-analytics.com/g/collect?v=2") is None

    @pytest.mark.parametrize(("value", "states"), [("G100", "00"), ("G111", "11"), ("G1--", "--")])
    def test_reads_the_states(self, value, states):
        assert gcs_states(value) == states

    def test_ignores_other_formats(self):
        assert gcs_states("G2") is None


class TestCorrectSetup:
    def test_every_consent_mode_check_passes(self):
        found = checks()
        for key in KEYS:
            assert found[key].status == "passed", (key, found[key])

    def test_checks_do_not_apply_when_consent_mode_is_off(self):
        found = checks(expect=Expectations())
        for key in KEYS:
            assert found[key].status == "not_applicable", key


class TestDefaults:
    def test_no_default_fails(self):
        found = checks(b=before(consent_commands=[]))
        assert found["before_consent.gcm_default"].status == "failed"
        assert found["before_consent.gcm_denied"].status == "inconclusive"
        assert found["before_consent.gcm_wait"].status == "inconclusive"

    def test_default_after_a_google_tag_starts_fails(self):
        tag = {"command": "tag", "name": "gtm.js", "t": 0.5}
        check = checks(b=before(consent_commands=[tag, default()]))["before_consent.gcm_default"]
        assert check.status == "failed"
        assert check.evidence == {"started": ["gtm.js"]}
        assert "Move the ConsentOS script above" in check.fix

    def test_google_tag_after_the_default_passes(self):
        tag = {"command": "tag", "name": "config", "t": 5.0}
        b = before(consent_commands=[default(), tag])
        assert checks(b=b)["before_consent.gcm_default"].status == "passed"

    def test_granted_default_fails(self):
        state = {**DENIED, "analytics_storage": "granted"}
        check = checks(b=before(consent_commands=[default(state)]))["before_consent.gcm_denied"]
        assert check.status == "failed"
        assert check.evidence == {"types": ["analytics_storage"]}

    def test_a_type_left_out_of_the_default_fails(self):
        state = {k: v for k, v in DENIED.items() if k != "ad_user_data"}
        check = checks(b=before(consent_commands=[default(state)]))["before_consent.gcm_denied"]
        assert check.status == "failed"
        assert check.evidence == {"types": ["ad_user_data"]}

    def test_no_wait_for_update_is_a_warning(self):
        state = {k: v for k, v in DENIED.items() if k != "wait_for_update"}
        check = checks(b=before(consent_commands=[default(state)]))["before_consent.gcm_wait"]
        assert check.status == "failed"
        assert check.severity == "warning"


class TestAccept:
    def test_no_update_after_accept_fails(self):
        check = checks(a=accept(consent_commands=[default()]))["after_accept.gcm_update"]
        assert check.status == "failed"

    def test_an_update_before_the_click_does_not_count(self):
        a = accept(consent_commands=[default(), update(t=10.0)])
        assert checks(a=a)["after_accept.gcm_update"].status == "failed"

    def test_partial_grant_fails(self):
        a = accept(consent_commands=[default(), update({"analytics_storage": "granted"})])
        check = checks(a=a)["after_accept.gcm_update"]
        assert check.status == "failed"
        assert "ad_storage" in check.evidence["types"]

    def test_missing_accept_button_is_inconclusive(self):
        a = PhaseObservation("after_accept", loaded=True, banner_found=True)
        assert checks(a=a)["after_accept.gcm_update"].status == "inconclusive"


class TestReject:
    def test_granted_after_reject_fails(self):
        r = reject(consent_commands=[default(), update()])
        check = checks(r=r)["after_reject.gcm_denied"]
        assert check.status == "failed"
        assert "Tag Manager" in check.fix

    def test_no_commands_is_inconclusive(self):
        assert checks(r=reject(consent_commands=[]))["after_reject.gcm_denied"].status == (
            "inconclusive"
        )

    def test_tags_starting_are_not_consent_commands(self):
        r = reject(consent_commands=[{"command": "tag", "name": "js", "t": 1.0}])
        assert checks(r=r)["after_reject.gcm_denied"].status == "inconclusive"


class TestReceived:
    def test_no_google_requests_is_inconclusive_not_passed(self):
        found = checks(
            b=before(consent_state_requests=[]),
            a=accept(consent_state_requests=[]),
            r=reject(consent_state_requests=[]),
        )
        assert found["gcm_received"].status == "inconclusive"

    def test_google_receiving_granted_after_reject_fails(self):
        check = checks(r=reject(consent_state_requests=[COLLECT + "G111"]))["gcm_received"]
        assert check.status == "failed"
        assert check.evidence == {"gcs": ["after Reject: G111"]}

    def test_google_receiving_granted_before_consent_fails(self):
        check = checks(b=before(consent_state_requests=[COLLECT + "G110"]))["gcm_received"]
        assert check.status == "failed"

    def test_unset_consent_state_fails(self):
        check = checks(b=before(consent_state_requests=[COLLECT + "G1--"]))["gcm_received"]
        assert check.status == "failed"

    def test_some_phases_without_requests_still_pass(self):
        found = checks(a=accept(consent_state_requests=[]))
        assert found["gcm_received"].status == "passed"
        assert found["gcm_received"].detail == "Checked before the visitor chose, after Reject."
