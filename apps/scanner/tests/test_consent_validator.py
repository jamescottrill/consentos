"""Tests for judging consent checks from what each phase looked like."""

import pytest

from src.consent_validator import (
    CONSENT_COOKIE,
    Expectations,
    ExpectedCookie,
    PhaseObservation,
    carries_consent_state,
    judge,
    tracker_host,
)

EXPECT = Expectations(
    cookies=[
        ExpectedCookie("session_id", "necessary"),
        ExpectedCookie("_ga", "analytics"),
        ExpectedCookie("_ga_*", "analytics"),
        ExpectedCookie("pending", None),
    ]
)


def cookie(name: str) -> dict:
    return {"name": name, "domain": "example.com"}


def before(**kwargs) -> PhaseObservation:
    return PhaseObservation(
        "before_consent", loaded=True, banner_found=True, observed=True, **kwargs
    )


def accept(**kwargs) -> PhaseObservation:
    kwargs.setdefault("cookies", [cookie(CONSENT_COOKIE)])
    return PhaseObservation(
        "after_accept", loaded=True, banner_found=True, clicked=True, observed=True, **kwargs
    )


def reject(**kwargs) -> PhaseObservation:
    kwargs.setdefault("cookies", [cookie(CONSENT_COOKIE)])
    return PhaseObservation(
        "after_reject", loaded=True, banner_found=True, clicked=True, observed=True, **kwargs
    )


def results(expect=EXPECT, b=None, a=None, r=None) -> dict[str, tuple[str, object]]:
    checks = judge(expect, b or before(), a or accept(), r or reject())
    return {c.key: (c.status, c) for c in checks}


def status(key: str, **kwargs) -> str:
    return results(**kwargs)[key][0]


class TestTrackerHost:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.google-analytics.com/g/collect",
            "https://connect.facebook.net/en_US/fbevents.js",
            "https://ad.doubleclick.net/pixel",
            "https://static.hotjar.com/c/hotjar.js",
        ],
    )
    def test_known_trackers(self, url):
        assert tracker_host(url) is not None

    @pytest.mark.parametrize(
        "url", ["https://example.com/style.css", "https://notfacebook.com/x", ""]
    )
    def test_other_hosts(self, url):
        assert tracker_host(url) is None

    def test_spots_consent_state_parameter(self):
        assert carries_consent_state("https://www.google-analytics.com/g/collect?gcs=G100")
        assert not carries_consent_state("https://www.google-analytics.com/g/collect?v=2")


class TestCleanSite:
    def test_every_check_passes_or_does_not_apply(self):
        statuses = {key: s for key, (s, _) in results().items()}
        assert set(statuses.values()) <= {"passed", "not_applicable"}
        assert statuses["before_consent.cookies"] == "passed"
        assert statuses["after_accept.saved"] == "passed"

    def test_tcf_checks_do_not_apply_when_tcf_is_off(self):
        for key in ("before_consent.tcf", "after_accept.tcf", "after_reject.tcf"):
            assert status(key) == "not_applicable"


class TestCookies:
    def test_optional_cookie_before_consent_fails(self):
        check = results(b=before(cookies=[cookie("_ga"), cookie("session_id")]))[
            "before_consent.cookies"
        ][1]
        assert check.status == "failed"
        assert check.evidence == {"cookies": ["_ga"]}
        assert "_ga" in check.fix

    def test_patterns_match(self):
        assert status("before_consent.cookies", b=before(cookies=[cookie("_ga_ABC123")])) == (
            "failed"
        )

    def test_necessary_and_own_cookies_pass(self):
        b = before(cookies=[cookie("session_id"), cookie(CONSENT_COOKIE), cookie("euconsent-v2")])
        assert status("before_consent.cookies", b=b) == "passed"

    @pytest.mark.parametrize("name", ["unknown_cookie", "pending"])
    def test_unclassified_cookie_is_inconclusive_not_passed(self, name):
        assert status("before_consent.cookies", b=before(cookies=[cookie(name)])) == (
            "inconclusive"
        )

    def test_optional_cookie_after_reject_fails(self):
        r = reject(cookies=[cookie(CONSENT_COOKIE), cookie("_ga")])
        assert status("after_reject.cookies", r=r) == "failed"


class TestTrackers:
    def test_tracker_before_consent_fails(self):
        b = before(tracker_requests=["https://connect.facebook.net/en_US/fbevents.js"])
        check = results(b=b)["before_consent.trackers"][1]
        assert check.status == "failed"
        assert check.evidence == {"hosts": ["facebook.net"]}

    def test_google_requests_reporting_consent_state_are_left_to_consent_mode(self):
        b = before(tracker_requests=["https://www.google-analytics.com/g/collect?gcs=G100"])
        expect = Expectations(gcm_enabled=True, cookies=EXPECT.cookies)
        assert status("before_consent.trackers", expect=expect, b=b) == "passed"

    def test_without_consent_mode_google_requests_count_as_trackers(self):
        b = before(tracker_requests=["https://www.google-analytics.com/g/collect?gcs=G100"])
        assert status("before_consent.trackers", b=b) == "failed"

    def test_tracker_after_reject_fails(self):
        r = reject(tracker_requests=["https://bat.bing.com/action/0"])
        assert status("after_reject.trackers", r=r) == "failed"


class TestSaved:
    def test_choice_not_saved_fails(self):
        assert status("after_accept.saved", a=accept(cookies=[])) == "failed"
        assert status("after_reject.saved", r=reject(cookies=[])) == "failed"


class TestMissingControlsNeverPass:
    def test_page_that_did_not_load_makes_every_check_inconclusive(self):
        failed = PhaseObservation("before_consent", error="net::ERR_NAME_NOT_RESOLVED")
        checks = judge(
            EXPECT,
            failed,
            PhaseObservation("after_accept", error=failed.error),
            PhaseObservation("after_reject", error=failed.error),
        )
        applicable = [c for c in checks if c.status != "not_applicable"]
        assert applicable
        assert {c.status for c in applicable} == {"inconclusive"}
        assert all(c.detail == "net::ERR_NAME_NOT_RESOLVED" for c in applicable)

    def test_missing_reject_button_makes_after_reject_checks_inconclusive(self):
        r = PhaseObservation("after_reject", loaded=True, banner_found=True, clicked=False)
        found = results(r=r)
        for key in ("after_reject.saved", "after_reject.cookies", "after_reject.trackers"):
            assert found[key][0] == "inconclusive"
            assert "Reject button" in found[key][1].detail
        assert found["after_accept.saved"][0] == "passed"

    def test_missing_banner_makes_choice_checks_inconclusive(self):
        found = results(
            a=PhaseObservation("after_accept", loaded=True),
            r=PhaseObservation("after_reject", loaded=True),
        )
        assert found["after_accept.saved"][0] == "inconclusive"
        assert found["after_accept.saved"][1].detail == "The banner didn't appear."

    def test_a_step_the_browser_never_reached_is_inconclusive(self):
        a = PhaseObservation("after_accept", loaded=True, banner_found=True, clicked=True)
        check = results(a=a)["after_accept.saved"][1]
        assert check.status == "inconclusive"
        assert check.detail == "The check stopped before this step."

    def test_no_check_passes_without_observing_anything(self):
        checks = judge(
            Expectations(tcf_enabled=True),
            PhaseObservation("before_consent"),
            PhaseObservation("after_accept"),
            PhaseObservation("after_reject"),
        )
        assert not [c for c in checks if c.status == "passed"]


TCF = Expectations(tcf_enabled=True)


def tcf(consents: dict | None = None, success: bool = True) -> dict:
    return {"success": success, "data": {"purpose": {"consents": consents or {}}}}


class TestTcf:
    def test_correct_tcf_passes(self):
        found = results(
            expect=TCF,
            b=before(tcf=tcf()),
            a=accept(tcf=tcf({"1": True, "2": True})),
            r=reject(tcf=tcf({"1": False})),
        )
        for key in ("before_consent.tcf", "after_accept.tcf", "after_reject.tcf"):
            assert found[key][0] == "passed", key

    def test_missing_api_fails(self):
        assert status("before_consent.tcf", expect=TCF, b=before(tcf=None)) == "failed"

    def test_consent_before_choice_fails(self):
        check = results(expect=TCF, b=before(tcf=tcf({"1": True})))["before_consent.tcf"][1]
        assert check.status == "failed"
        assert check.evidence == {"purposes": ["1"]}

    def test_no_consent_after_accept_fails(self):
        assert status("after_accept.tcf", expect=TCF, a=accept(tcf=tcf())) == "failed"

    def test_unanswered_api_is_inconclusive(self):
        assert status("before_consent.tcf", expect=TCF, b=before(tcf={"timedOut": True})) == (
            "inconclusive"
        )
        assert status("before_consent.tcf", expect=TCF, b=before(tcf=tcf(success=False))) == (
            "inconclusive"
        )
