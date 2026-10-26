"""Check the banner for dark patterns that nudge visitors into accepting.

The banner is measured before the visitor chooses, and the page again
after Reject:

- a first layer with Accept but no Reject;
- Accept made bigger, or in bigger text, than Reject;
- optional categories ticked before the visitor chooses;
- a banner that scrolling dismisses, since scrolling isn't consent;
- a page still covered after Reject, a cookie wall.

Unequal buttons are warnings, since how much bigger is too big is a
judgement. The rest fail.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.consent_results import (
    BannerObservation,
    ButtonLook,
    CheckResult,
    PhaseObservation,
    names,
    not_observed,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

_HOST = "#consentos-banner-host"
# Playwright's CSS selectors reach into the banner's open shadow root.
BANNER_SELECTOR = f"{_HOST} .consentos-banner"
_ACCEPT = f'{_HOST} [data-action="accept"]'
_REJECT = f'{_HOST} [data-action="reject"]'
_CATEGORIES = f"{_HOST} input[data-category]"

# Accept more than this many times Reject's area, or text size, is flagged.
MAX_AREA_RATIO = 1.5
MAX_FONT_RATIO = 1.3
SCROLL_PX = 500

# In these modes tracking is on until the visitor opts out, so the
# banner ticks the categories on purpose.
_PRE_TICKED_MODES = frozenset({"opt_out", "informational"})

LABELS = {
    "banner.reject_button": "The banner offers Reject alongside Accept",
    "banner.equal_buttons": "Accept and Reject are equally prominent",
    "banner.pre_ticked": "Optional categories start unticked",
    "banner.scroll": "Scrolling doesn't dismiss the banner",
    "banner.cookie_wall": "The page can be used after Reject",
}

_BUTTON_SCRIPT = """
(el) => {
  const rect = el.getBoundingClientRect();
  return { area: rect.width * rect.height, fontSize: parseFloat(getComputedStyle(el).fontSize) };
}
"""

# A cookie wall: the body locked in place, or a full-screen layer over
# the middle of the page. The banner's own host is left out.
_WALL_SCRIPT = """
() => {
  const body = getComputedStyle(document.body);
  if (body.overflow === 'hidden' && body.position === 'fixed') return true;
  const top = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
  for (let el = top; el && el !== document.body; el = el.parentElement) {
    if (el.id === 'consentos-banner-host') return false;
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    if (cs.position === 'fixed'
        && rect.width >= innerWidth * 0.9 && rect.height >= innerHeight * 0.9
        && parseInt(cs.zIndex, 10) > 100
        && cs.visibility !== 'hidden' && parseFloat(cs.opacity) > 0) {
      return true;
    }
  }
  return false;
}
"""


# ── Measuring ────────────────────────────────────────────────────────


async def _visible(page: Page, selector: str) -> bool:
    try:
        return await page.locator(selector).first.is_visible()
    except Exception:
        return False


async def _button(page: Page, selector: str) -> ButtonLook | None:
    if not await _visible(page, selector):
        return None
    try:
        look = await page.locator(selector).first.evaluate(_BUTTON_SCRIPT)
    except Exception:
        logger.debug("Couldn't measure %s", selector, exc_info=True)
        return None
    return ButtonLook(area=look["area"], font_size=look["fontSize"])


async def measure_banner(
    page: Page, banner: BannerObservation, *, settle_ms: int, blocking_mode: str | None
) -> None:
    """Measure the banner as a first-time visitor sees it, before they choose.

    Scrolls the page, so call it after everything else before the choice
    has been observed.
    """
    banner.measured = True
    banner.blocking_mode = blocking_mode
    banner.showing = await _visible(page, BANNER_SELECTOR)
    if not banner.showing:
        return
    banner.accept = await _button(page, _ACCEPT)
    banner.reject = await _button(page, _REJECT)
    try:
        banner.pre_ticked = await page.locator(_CATEGORIES).evaluate_all(
            "(els) => els.filter((e) => e.checked && !e.disabled).map((e) => e.dataset.category)"
        )
    except Exception:
        logger.debug("Couldn't read the banner's categories", exc_info=True)
    try:
        await page.mouse.wheel(0, SCROLL_PX)
        await page.wait_for_timeout(settle_ms)
        banner.dismissed_by_scroll = not await _visible(page, BANNER_SELECTOR)
    except Exception:
        logger.debug("Couldn't scroll the page", exc_info=True)


async def measure_wall(page: Page, banner: BannerObservation) -> None:
    """Record whether anything still covers the page after Reject."""
    try:
        banner.walled_after_reject = bool(await page.evaluate(_WALL_SCRIPT))
    except Exception:
        logger.debug("Couldn't check the page for a cookie wall", exc_info=True)


# ── Judging ──────────────────────────────────────────────────────────


def _not_measured(before: PhaseObservation, banner: BannerObservation) -> str | None:
    if not before.loaded:
        return before.error or "The page didn't load."
    if not before.banner_found:
        return "The banner didn't appear."
    if not banner.measured:
        return before.error or "The check stopped before this step."
    if not banner.showing:
        return "The banner didn't appear."
    return None


def _reject_check(banner: BannerObservation) -> CheckResult:
    key = "banner.reject_button"
    label = LABELS[key]
    if banner.accept is None:
        return CheckResult(
            key,
            "banner",
            label,
            "inconclusive",
            detail="We couldn't find the banner's Accept button.",
        )
    if banner.reject is not None:
        return CheckResult(key, "banner", label, "passed")
    return CheckResult(
        key,
        "banner",
        label,
        "failed",
        fix=(
            "The banner shows Accept but no Reject button, so refusing is harder than "
            "accepting. Visitors must be able to refuse as easily as they accept. Check your "
            "site's CSS isn't hiding the banner's Reject button."
        ),
    )


def _equal_buttons_check(banner: BannerObservation) -> CheckResult:
    key = "banner.equal_buttons"
    label = LABELS[key]
    accept, reject = banner.accept, banner.reject
    if accept is None or reject is None or reject.area <= 0 or reject.font_size <= 0:
        return CheckResult(
            key,
            "banner",
            label,
            "inconclusive",
            detail="We couldn't measure both the Accept and Reject buttons.",
            severity="warning",
        )
    area_ratio = accept.area / reject.area
    font_ratio = accept.font_size / reject.font_size
    problems = []
    if area_ratio > MAX_AREA_RATIO:
        problems.append(f"Accept is {area_ratio:.1f} times the size of Reject")
    if font_ratio > MAX_FONT_RATIO:
        problems.append(
            f"Accept's text is {accept.font_size:g}px against Reject's {reject.font_size:g}px"
        )
    if not problems:
        return CheckResult(key, "banner", label, "passed", severity="warning")
    return CheckResult(
        key,
        "banner",
        label,
        "failed",
        detail="; ".join(problems),
        fix=(
            f"{'; '.join(problems)}. Making Accept stand out nudges visitors towards it. Give "
            "both buttons the same size and text size in the banner's appearance settings, "
            "and check your site's CSS doesn't change them."
        ),
        evidence={"area_ratio": round(area_ratio, 2), "font_ratio": round(font_ratio, 2)},
        severity="warning",
    )


def _pre_ticked_check(banner: BannerObservation) -> CheckResult:
    key = "banner.pre_ticked"
    label = LABELS[key]
    if banner.blocking_mode in _PRE_TICKED_MODES:
        return CheckResult(key, "banner", label, "not_applicable")
    if not banner.pre_ticked:
        return CheckResult(key, "banner", label, "passed")
    return CheckResult(
        key,
        "banner",
        label,
        "failed",
        detail=names(banner.pre_ticked),
        fix=(
            f"These categories are ticked before the visitor chooses: "
            f"{names(banner.pre_ticked)}. A pre-ticked box isn't consent, so optional "
            "categories must start unticked. Check for a script on your site ticking them."
        ),
        evidence={"categories": banner.pre_ticked},
    )


def _scroll_check(banner: BannerObservation) -> CheckResult:
    key = "banner.scroll"
    label = LABELS[key]
    if banner.dismissed_by_scroll is None:
        return CheckResult(
            key, "banner", label, "inconclusive", detail="We couldn't scroll the page."
        )
    if not banner.dismissed_by_scroll:
        return CheckResult(key, "banner", label, "passed")
    return CheckResult(
        key,
        "banner",
        label,
        "failed",
        fix=(
            "The banner disappeared when the visitor scrolled. Scrolling isn't consent, so "
            "the banner must stay until the visitor chooses. Check for a script on your site "
            "hiding it."
        ),
    )


def _wall_check(reject: PhaseObservation, banner: BannerObservation) -> CheckResult:
    key = "banner.cookie_wall"
    label = LABELS[key]
    reason = not_observed(reject, needs_click=True)
    if reason:
        return CheckResult(key, "banner", label, "inconclusive", detail=reason)
    if banner.walled_after_reject is None:
        return CheckResult(
            key,
            "banner",
            label,
            "inconclusive",
            detail="We couldn't check the page after Reject.",
        )
    if not banner.walled_after_reject:
        return CheckResult(key, "banner", label, "passed")
    return CheckResult(
        key,
        "banner",
        label,
        "failed",
        fix=(
            "After the visitor rejected, something still covers the page, a cookie wall. "
            "Visitors who refuse must still be able to use the site. Check for an overlay "
            "or script on your site that stays until the visitor accepts."
        ),
    )


def dark_pattern_checks(
    before: PhaseObservation, reject: PhaseObservation, banner: BannerObservation
) -> list[CheckResult]:
    """The banner's dark-pattern checks, one per pattern."""
    reason = _not_measured(before, banner)
    if reason:
        # The cookie wall is checked on the Reject visit, so can still be judged.
        unmeasured = [
            CheckResult(
                key,
                "banner",
                LABELS[key],
                "inconclusive",
                detail=reason,
                severity="warning" if key == "banner.equal_buttons" else "error",
            )
            for key in LABELS
            if key != "banner.cookie_wall"
        ]
        return [*unmeasured, _wall_check(reject, banner)]
    return [
        _reject_check(banner),
        _equal_buttons_check(banner),
        _pre_ticked_check(banner),
        _scroll_check(banner),
        _wall_check(reject, banner),
    ]
