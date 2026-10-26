"""Tests for judging the banner's dark-pattern checks from how it looked."""

import pytest

from src.consent_results import BannerObservation, ButtonLook, PhaseObservation
from src.dark_pattern_detector import LABELS, dark_pattern_checks

BUTTON = ButtonLook(area=4000, font_size=14)


def before(**kwargs) -> PhaseObservation:
    fields = {"loaded": True, "banner_found": True, "observed": True, **kwargs}
    return PhaseObservation("before_consent", **fields)


def reject(**kwargs) -> PhaseObservation:
    return PhaseObservation(
        "after_reject", loaded=True, banner_found=True, clicked=True, observed=True, **kwargs
    )


def looked(**kwargs) -> BannerObservation:
    fields = {
        "measured": True,
        "showing": True,
        "accept": BUTTON,
        "reject": BUTTON,
        "dismissed_by_scroll": False,
        "walled_after_reject": False,
    }
    fields.update(kwargs)
    return BannerObservation(**fields)


def results(banner=None, b=None, r=None) -> dict:
    checks = dark_pattern_checks(b or before(), r or reject(), banner or looked())
    return {c.key: c for c in checks}


def test_a_fair_banner_passes_every_check():
    found = results()
    assert list(found) == list(LABELS)
    assert {c.status for c in found.values()} == {"passed"}
    assert {c.phase for c in found.values()} == {"banner"}


class TestRejectButton:
    def test_missing_reject_fails(self):
        check = results(looked(reject=None))["banner.reject_button"]
        assert check.status == "failed"
        assert check.severity == "error"
        assert "Reject" in check.fix

    def test_missing_accept_is_inconclusive(self):
        found = results(looked(accept=None))
        assert found["banner.reject_button"].status == "inconclusive"
        assert found["banner.equal_buttons"].status == "inconclusive"


class TestEqualButtons:
    def test_bigger_accept_is_a_warning(self):
        check = results(looked(accept=ButtonLook(area=8000, font_size=14)))["banner.equal_buttons"]
        assert check.status == "failed"
        assert check.severity == "warning"
        assert "2.0 times the size" in check.detail
        assert check.evidence["area_ratio"] == 2.0

    def test_bigger_accept_text_is_a_warning(self):
        check = results(looked(accept=ButtonLook(area=4000, font_size=20)))["banner.equal_buttons"]
        assert check.status == "failed"
        assert "20px against Reject's 14px" in check.detail

    @pytest.mark.parametrize("accept", [ButtonLook(5900, 18), ButtonLook(3000, 12)])
    def test_small_differences_pass(self, accept):
        assert results(looked(accept=accept))["banner.equal_buttons"].status == "passed"

    def test_missing_reject_is_inconclusive_not_a_second_failure(self):
        check = results(looked(reject=None))["banner.equal_buttons"]
        assert check.status == "inconclusive"
        assert check.severity == "warning"


class TestPreTicked:
    def test_ticked_optional_category_fails(self):
        check = results(looked(pre_ticked=["analytics", "marketing"]))["banner.pre_ticked"]
        assert check.status == "failed"
        assert check.detail == "analytics, marketing"
        assert check.evidence == {"categories": ["analytics", "marketing"]}

    @pytest.mark.parametrize("mode", ["opt_out", "informational"])
    def test_does_not_apply_where_tracking_is_on_until_opt_out(self, mode):
        banner = looked(pre_ticked=["analytics"], blocking_mode=mode)
        assert results(banner)["banner.pre_ticked"].status == "not_applicable"

    def test_applies_in_opt_in(self):
        banner = looked(pre_ticked=["analytics"], blocking_mode="opt_in")
        assert results(banner)["banner.pre_ticked"].status == "failed"


class TestScroll:
    def test_dismissed_by_scrolling_fails(self):
        assert results(looked(dismissed_by_scroll=True))["banner.scroll"].status == "failed"

    def test_scroll_that_could_not_be_tried_is_inconclusive(self):
        check = results(looked(dismissed_by_scroll=None))["banner.scroll"]
        assert check.status == "inconclusive"


class TestCookieWall:
    def test_page_covered_after_reject_fails(self):
        check = results(looked(walled_after_reject=True))["banner.cookie_wall"]
        assert check.status == "failed"
        assert "cookie wall" in check.fix

    def test_without_reject_it_is_inconclusive(self):
        r = PhaseObservation("after_reject", loaded=True, banner_found=True)
        check = results(looked(walled_after_reject=None), r=r)["banner.cookie_wall"]
        assert check.status == "inconclusive"
        assert "Reject button" in check.detail

    def test_unchecked_page_is_inconclusive(self):
        check = results(looked(walled_after_reject=None))["banner.cookie_wall"]
        assert check.status == "inconclusive"


class TestNothingMeasuredNeverPasses:
    @pytest.mark.parametrize(
        ("b", "banner", "detail"),
        [
            (PhaseObservation("before_consent", error="timeout"), BannerObservation(), "timeout"),
            (before(banner_found=False), BannerObservation(), "The banner didn't appear."),
            (before(), BannerObservation(), "The check stopped before this step."),
            (before(), BannerObservation(measured=True), "The banner didn't appear."),
        ],
    )
    def test_every_check_is_inconclusive(self, b, banner, detail):
        found = results(banner, b=b, r=PhaseObservation("after_reject"))
        assert {c.status for c in found.values()} == {"inconclusive"}
        assert found["banner.scroll"].detail == detail
        assert found["banner.equal_buttons"].severity == "warning"

    def test_cookie_wall_is_still_judged_from_the_reject_visit(self):
        banner = BannerObservation(walled_after_reject=True)
        found = results(banner, b=before(banner_found=False))
        assert found["banner.cookie_wall"].status == "failed"
