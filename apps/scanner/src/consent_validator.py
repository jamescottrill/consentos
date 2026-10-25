"""Check that a site's consent setup works, as a first-time visitor sees it.

The page is loaded twice, each time in a fresh browser: once to look at
it before any choice and then accept, and once to reject. Cookies,
tracker requests and the consent signals the page exposes are recorded
for each phase, then judged against what the site has switched on.

Every check reports one of four results. A check that couldn't observe
what it needed is inconclusive, never passed, so a page that didn't load
or a button that couldn't be found can't read as a clean result.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

    from src.navigation import NavigationPolicy

logger = logging.getLogger(__name__)

CheckStatus = Literal["passed", "failed", "inconclusive", "not_applicable"]
Phase = Literal["before_consent", "after_accept", "after_reject"]

BANNER_HOST_SELECTOR = "#consentos-banner-host"
CONSENT_COOKIE = "_consentos_consent"
# Cookies the banner itself writes to remember the choice.
OWN_COOKIES = frozenset({CONSENT_COOKIE, "euconsent-v2"})
_EVIDENCE_LIMIT = 10

KNOWN_TRACKER_DOMAINS = frozenset(
    {
        "google-analytics.com",
        "googletagmanager.com",
        "doubleclick.net",
        "facebook.net",
        "facebook.com",
        "analytics.tiktok.com",
        "snap.licdn.com",
        "bat.bing.com",
        "clarity.ms",
        "hotjar.com",
        "mouseflow.com",
        "cdn.segment.com",
        "cdn.mxpnl.com",
        "plausible.io",
        "px.ads.linkedin.com",
    }
)

_ACCEPT_SELECTORS = (
    f'{BANNER_HOST_SELECTOR} [data-action="accept"]',
    "button:has-text('Accept all')",
    "button:has-text('Accept')",
)
_REJECT_SELECTORS = (
    f'{BANNER_HOST_SELECTOR} [data-action="reject"]',
    "button:has-text('Reject all')",
    "button:has-text('Reject')",
)

# Installed before any page script runs, so Consent Mode commands are
# recorded even when the site replaces ``dataLayer`` or its ``push``, as
# Google Tag Manager does.
RECORDER_SCRIPT = """
(() => {
  const log = [];
  window.__consentosCheck = { consent: log };
  const record = (item) => {
    try {
      if (item && item[0] === 'consent') {
        log.push({
          command: String(item[1]),
          state: JSON.parse(JSON.stringify(item[2] || {})),
          t: performance.now(),
        });
      }
    } catch (e) {}
  };
  const watch = (arr) => {
    if (!Array.isArray(arr) || arr.__consentosWatched) return arr;
    Object.defineProperty(arr, '__consentosWatched', { value: true });
    arr.forEach(record);
    let push = arr.push;
    Object.defineProperty(arr, 'push', {
      configurable: true,
      get() {
        return function (...items) {
          items.forEach(record);
          return push.apply(this, items);
        };
      },
      set(fn) { push = fn; },
    });
    return arr;
  };
  let dataLayer = watch(window.dataLayer);
  Object.defineProperty(window, 'dataLayer', {
    configurable: true,
    get() { return dataLayer; },
    set(value) { dataLayer = watch(value); },
  });
})();
"""

# TCF's getTCData may never call back, so it is given a deadline.
_SIGNALS_SCRIPT = """
async () => {
  const withTimeout = (promise) => Promise.race([
    promise, new Promise((resolve) => setTimeout(() => resolve({ timedOut: true }), 2000)),
  ]);
  const tcf = typeof window.__tcfapi === 'function'
    ? await withTimeout(new Promise((resolve) => window.__tcfapi(
        'getTCData', 2, (data, success) => resolve({ success, data: data || null }))))
    : null;
  return {
    consent: (window.__consentosCheck && window.__consentosCheck.consent) || [],
    tcf,
  };
}
"""


@dataclass
class ExpectedCookie:
    name: str
    category: str | None


@dataclass
class Expectations:
    """What the site has switched on, from its live configuration."""

    tcf_enabled: bool = False
    cookies: list[ExpectedCookie] = field(default_factory=list)


@dataclass
class PhaseObservation:
    """What one phase of the visit looked like."""

    phase: Phase
    loaded: bool = False
    error: str | None = None
    banner_found: bool = False
    # Whether the visitor's choice for this phase was made.
    clicked: bool = False
    observed: bool = False
    cookies: list[dict] = field(default_factory=list)
    tracker_requests: list[str] = field(default_factory=list)
    consent_commands: list[dict] = field(default_factory=list)
    tcf: dict | None = None


@dataclass
class CheckResult:
    key: str
    phase: Phase
    label: str
    status: CheckStatus
    detail: str | None = None
    fix: str | None = None
    evidence: dict = field(default_factory=dict)


@dataclass
class ValidationResult:
    url: str
    checks: list[CheckResult] = field(default_factory=list)
    error: str | None = None


def tracker_host(url: str) -> str | None:
    """The known tracker domain ``url`` is on, if any."""
    host = (urlsplit(url).hostname or "").lower()
    for domain in KNOWN_TRACKER_DOMAINS:
        if host == domain or host.endswith(f".{domain}"):
            return domain
    return None


def carries_consent_state(url: str) -> bool:
    """Whether a Google request reports its consent state in ``gcs``.

    These are judged by the Consent Mode checks, since Consent Mode lets
    Google tags send cookieless requests before consent.
    """
    return "gcs" in parse_qs(urlsplit(url).query)


# ── Judging ──────────────────────────────────────────────────────────


def _cookie_category(name: str, expected: list[ExpectedCookie]) -> tuple[bool, str | None]:
    """Whether the cookie is known to the site, and its category."""
    for cookie in expected:
        if fnmatch.fnmatchcase(name, cookie.name):
            return True, cookie.category
    return False, None


def _sort_cookies(
    cookies: list[dict], expected: list[ExpectedCookie]
) -> tuple[list[str], list[str]]:
    """Split cookies into optional ones and ones the site hasn't classified."""
    optional: list[str] = []
    unclassified: list[str] = []
    for name in sorted({c["name"] for c in cookies} - OWN_COOKIES):
        known, category = _cookie_category(name, expected)
        if not known or category is None:
            unclassified.append(name)
        elif category != "necessary":
            optional.append(name)
    return optional, unclassified


def _names(items: list[str]) -> str:
    shown = ", ".join(items[:5])
    return shown if len(items) <= 5 else f"{shown} and {len(items) - 5} more"


def _not_observed(obs: PhaseObservation, needs_click: bool) -> str | None:
    """Why ``obs`` can't be judged, or None when it can."""
    if not obs.loaded:
        return obs.error or "The page didn't load."
    if needs_click and not obs.banner_found:
        return "The banner didn't appear."
    if needs_click and not obs.clicked:
        button = "Accept" if obs.phase == "after_accept" else "Reject"
        return f"We couldn't find the banner's {button} button."
    if not obs.observed:
        return obs.error or "The check stopped before this step."
    return None


def _cookies_check(
    obs: PhaseObservation, expected: list[ExpectedCookie], *, needs_click: bool
) -> CheckResult:
    before = obs.phase == "before_consent"
    key = f"{obs.phase}.cookies"
    label = (
        "No optional cookies are set before the visitor chooses"
        if before
        else "No optional cookies are set after Reject"
    )
    reason = _not_observed(obs, needs_click)
    if reason:
        return CheckResult(key, obs.phase, label, "inconclusive", detail=reason)

    optional, unclassified = _sort_cookies(obs.cookies, expected)
    when = "before the visitor chooses" if before else "after the visitor rejected"
    if optional:
        return CheckResult(
            key,
            obs.phase,
            label,
            "failed",
            detail=_names(optional),
            fix=(
                f"These optional cookies are set {when}: {_names(optional)}. Check the "
                "scripts that set them have the right category on the Cookies tab, and that "
                "the ConsentOS script is the first in <head>. The banner can't block cookies "
                "your server sets itself."
            ),
            evidence={"cookies": optional[:_EVIDENCE_LIMIT]},
        )
    if unclassified:
        return CheckResult(
            key,
            obs.phase,
            label,
            "inconclusive",
            detail=(
                f"These cookies are set {when} but aren't classified yet: "
                f"{_names(unclassified)}. Review them on the Cookies tab so we can tell "
                "whether they're allowed."
            ),
            evidence={"cookies": unclassified[:_EVIDENCE_LIMIT]},
        )
    return CheckResult(key, obs.phase, label, "passed")


def _trackers_check(obs: PhaseObservation, *, needs_click: bool) -> CheckResult:
    before = obs.phase == "before_consent"
    key = f"{obs.phase}.trackers"
    label = (
        "No trackers load before the visitor chooses" if before else "No trackers load after Reject"
    )
    reason = _not_observed(obs, needs_click)
    if reason:
        return CheckResult(key, obs.phase, label, "inconclusive", detail=reason)

    hosts = sorted(
        {
            host
            for url in obs.tracker_requests
            if (host := tracker_host(url)) and not carries_consent_state(url)
        }
    )
    if not hosts:
        return CheckResult(key, obs.phase, label, "passed")
    when = "before the visitor chose" if before else "after the visitor rejected"
    return CheckResult(
        key,
        obs.phase,
        label,
        "failed",
        detail=_names(hosts),
        fix=(
            f"These trackers loaded {when}: {_names(hosts)}. Check the scripts that load "
            "them have a category on the Cookies tab, and that the ConsentOS script is the "
            "first in <head>."
        ),
        evidence={"hosts": hosts[:_EVIDENCE_LIMIT]},
    )


def _saved_check(obs: PhaseObservation) -> CheckResult:
    accept = obs.phase == "after_accept"
    key = f"{obs.phase}.saved"
    label = "Accepting is remembered" if accept else "Rejecting is remembered"
    reason = _not_observed(obs, needs_click=True)
    if reason:
        return CheckResult(key, obs.phase, label, "inconclusive", detail=reason)
    if any(c["name"] == CONSENT_COOKIE for c in obs.cookies):
        return CheckResult(key, obs.phase, label, "passed")
    return CheckResult(
        key,
        obs.phase,
        label,
        "failed",
        fix=(
            "The banner didn't save the visitor's choice, so they'll be asked again on every "
            "page. Check your browser's console on the site for errors."
        ),
    )


def _granted_purposes(tcf: dict) -> list[str]:
    consents = ((tcf.get("data") or {}).get("purpose") or {}).get("consents") or {}
    return sorted(k for k, v in consents.items() if v)


def _tcf_check(obs: PhaseObservation, expect: Expectations, *, needs_click: bool) -> CheckResult:
    key = f"{obs.phase}.tcf"
    label = {
        "before_consent": "TCF reports no consent before the visitor chooses",
        "after_accept": "TCF reports consent after Accept",
        "after_reject": "TCF reports no consent after Reject",
    }[obs.phase]
    if not expect.tcf_enabled:
        return CheckResult(key, obs.phase, label, "not_applicable")
    reason = _not_observed(obs, needs_click)
    if reason:
        return CheckResult(key, obs.phase, label, "inconclusive", detail=reason)
    if obs.tcf is None:
        return CheckResult(
            key,
            obs.phase,
            label,
            "failed",
            fix=(
                "TCF is switched on, but the page has no TCF API (__tcfapi). Check the "
                "ConsentOS script loads on this page."
            ),
        )
    if obs.tcf.get("timedOut") or not obs.tcf.get("success"):
        return CheckResult(
            key, obs.phase, label, "inconclusive", detail="The TCF API didn't return its data."
        )

    granted = _granted_purposes(obs.tcf)
    if obs.phase == "after_accept":
        if granted:
            return CheckResult(key, obs.phase, label, "passed")
        return CheckResult(
            key,
            obs.phase,
            label,
            "failed",
            fix=(
                "After Accept, the TCF API still reports no purpose consents, so vendors "
                "will treat the visitor as not having consented."
            ),
        )
    if not granted:
        return CheckResult(key, obs.phase, label, "passed")
    when = "before the visitor chose" if obs.phase == "before_consent" else "after Reject"
    return CheckResult(
        key,
        obs.phase,
        label,
        "failed",
        detail=", ".join(granted),
        fix=(
            f"The TCF API reports consent for purposes {', '.join(granted)} {when}. Vendors "
            "will act on it, so it must report no consent until the visitor accepts."
        ),
        evidence={"purposes": granted},
    )


def judge(
    expect: Expectations,
    before: PhaseObservation,
    accept: PhaseObservation,
    reject: PhaseObservation,
) -> list[CheckResult]:
    """Turn what each phase looked like into the site's consent checks."""
    return [
        _cookies_check(before, expect.cookies, needs_click=False),
        _trackers_check(before, needs_click=False),
        _tcf_check(before, expect, needs_click=False),
        _saved_check(accept),
        _tcf_check(accept, expect, needs_click=True),
        _saved_check(reject),
        _cookies_check(reject, expect.cookies, needs_click=True),
        _trackers_check(reject, needs_click=True),
        _tcf_check(reject, expect, needs_click=True),
    ]


# ── Observing ────────────────────────────────────────────────────────


class _Visit:
    """One fresh browser context, recording requests by phase."""

    def __init__(self, context: BrowserContext, page: Page) -> None:
        self.context = context
        self.page = page
        self.phase: Phase = "before_consent"
        self.requests: dict[Phase, list[str]] = {
            "before_consent": [],
            "after_accept": [],
            "after_reject": [],
        }
        page.on("request", self._on_request)

    def _on_request(self, request) -> None:  # noqa: ANN001
        if tracker_host(request.url):
            self.requests[self.phase].append(request.url)

    async def observe(self, obs: PhaseObservation) -> None:
        obs.observed = True
        obs.cookies = [
            {"name": c["name"], "domain": c["domain"]} for c in await self.context.cookies()
        ]
        obs.tracker_requests = list(self.requests[obs.phase])
        try:
            signals = await self.page.evaluate(_SIGNALS_SCRIPT)
        except Exception:
            logger.debug("Couldn't read consent signals", exc_info=True)
            return
        obs.consent_commands = signals.get("consent") or []
        obs.tcf = signals.get("tcf")


async def _load(
    visit: _Visit, url: str, obs: PhaseObservation, timeout_ms: int, banner_wait_ms: int
) -> None:
    try:
        response = await visit.page.goto(url, wait_until="load", timeout=timeout_ms)
    except Exception as exc:
        obs.error = str(exc).splitlines()[0]
        return
    if response is None or response.status >= 400:
        obs.error = f"The page returned {response.status if response else 'no response'}"
        return
    obs.loaded = True
    try:
        await visit.page.wait_for_selector(
            BANNER_HOST_SELECTOR, state="attached", timeout=banner_wait_ms
        )
        obs.banner_found = True
    except Exception:
        obs.banner_found = False


async def _click(page: Page, selectors: tuple[str, ...]) -> bool:
    for selector in selectors:
        try:
            button = page.locator(selector).first
            if await button.is_visible(timeout=1_000):
                await button.click()
                return True
        except Exception:
            continue
    return False


async def run_consent_checks(
    url: str,
    expect: Expectations,
    *,
    headless: bool,
    timeout_ms: int,
    policy: NavigationPolicy,
    banner_wait_ms: int = 8_000,
    settle_ms: int = 2_000,
) -> ValidationResult:
    """Visit ``url`` as a new visitor, accept, then visit again and reject."""
    from playwright.async_api import async_playwright

    result = ValidationResult(url=url)
    before = PhaseObservation("before_consent")
    accept = PhaseObservation("after_accept")
    reject = PhaseObservation("after_reject")

    async def new_visit(browser) -> _Visit:  # noqa: ANN001
        context = await browser.new_context(ignore_https_errors=True, service_workers="block")
        await policy.apply(context)
        await context.add_init_script(RECORDER_SCRIPT)
        return _Visit(context, await context.new_page())

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        try:
            visit = await new_visit(browser)
            await _load(visit, url, before, timeout_ms, banner_wait_ms)
            if before.loaded:
                await visit.page.wait_for_timeout(settle_ms)
                await visit.observe(before)
                accept.loaded, accept.banner_found = True, before.banner_found
                if before.banner_found:
                    visit.phase = "after_accept"
                    accept.clicked = await _click(visit.page, _ACCEPT_SELECTORS)
                    if accept.clicked:
                        await visit.page.wait_for_timeout(settle_ms)
                        await visit.observe(accept)
            else:
                accept.error = before.error
            await visit.context.close()

            visit = await new_visit(browser)
            await _load(visit, url, reject, timeout_ms, banner_wait_ms)
            if reject.loaded and reject.banner_found:
                # Only requests made after the click count against Reject.
                visit.phase = "after_reject"
                reject.clicked = await _click(visit.page, _REJECT_SELECTORS)
                if reject.clicked:
                    await visit.page.wait_for_timeout(settle_ms)
                    await visit.observe(reject)
            await visit.context.close()
        except Exception as exc:
            logger.warning("Consent checks failed for %s: %s", url, exc)
            result.error = str(exc).splitlines()[0]
            # Phases the browser didn't get to are reported with the error,
            # not as a missing button.
            for obs in (before, accept, reject):
                if not obs.observed:
                    obs.loaded, obs.error = False, result.error
        finally:
            await browser.close()

    result.checks = judge(expect, before, accept, reject)
    return result
