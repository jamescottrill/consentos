"""Check that a site's consent setup works, as a first-time visitor sees it.

The page is loaded twice, each time in a fresh browser: once to look at
it before any choice and then accept, and once to reject. Cookies,
tracker requests and the consent signals the page exposes are recorded
for each phase, then judged against what the site has switched on.
Before the choice the banner is also checked for dark patterns.

Every check reports one of four results. A check that couldn't observe
what it needed is inconclusive, never passed, so a page that didn't load
or a button that couldn't be found can't read as a clean result.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlsplit

from src.consent_mode import consent_mode_checks
from src.consent_results import (
    BannerObservation,
    CheckResult,
    Expectations,
    ExpectedCookie,
    Phase,
    PhaseObservation,
    names,
    not_observed,
)
from src.dark_pattern_detector import dark_pattern_checks, measure_banner, measure_wall
from src.gpp_checks import gpp_checks

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page, Response

    from src.navigation import NavigationPolicy

logger = logging.getLogger(__name__)

BANNER_HOST_SELECTOR = "#consentos-banner-host"
CONSENT_COOKIE = "_consentos_consent"
# The banner's config request, answered with the site's live config.
CONFIG_PATH = "/geo-resolved"
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

# Installed before any page script runs, so Consent Mode commands, and
# Google tags starting up, are recorded in order even when the site
# replaces ``dataLayer`` or its ``push``, as Google Tag Manager does.
RECORDER_SCRIPT = """
(() => {
  const log = [];
  window.__consentosCheck = { consent: log };
  const record = (item) => {
    try {
      if (!item) return;
      if (item[0] === 'consent') {
        log.push({
          command: String(item[1]),
          state: JSON.parse(JSON.stringify(item[2] || {})),
          t: performance.now(),
        });
      } else if (item[0] === 'js' || item[0] === 'config' || item.event === 'gtm.js') {
        // A Google tag or Tag Manager starting up.
        log.push({ command: 'tag', name: item.event || String(item[0]), t: performance.now() });
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

# The browser setting a visitor turns on to send Global Privacy Control.
GPC_SCRIPT = """
Object.defineProperty(Navigator.prototype, 'globalPrivacyControl', { get: () => true });
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
  // In GPP 1.1, ping carries the GPP string and parsed sections.
  const gpp = typeof window.__gpp === 'function'
    ? { ping: await withTimeout(new Promise((resolve) => window.__gpp('ping', resolve))) }
    : null;
  return {
    consent: (window.__consentosCheck && window.__consentosCheck.consent) || [],
    tcf,
    gpp,
  };
}
"""


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

    With Consent Mode on, these are judged by the Consent Mode checks
    rather than as trackers, since Consent Mode lets Google tags send
    cookieless requests before consent.
    """
    return "gcs" in parse_qs(urlsplit(url).query)


def is_google_tag(url: str) -> bool:
    """Whether ``url`` loads the Google tag or Tag Manager itself."""
    parts = urlsplit(url)
    return (parts.hostname or "").endswith("googletagmanager.com") and parts.path in (
        "/gtag/js",
        "/gtm.js",
    )


def judged_by_consent_mode(url: str) -> bool:
    """Google requests the Consent Mode checks judge, rather than the tracker checks.

    Consent Mode lets the Google tag load before consent and send
    cookieless requests that report the consent state in ``gcs``.
    """
    return is_google_tag(url) or carries_consent_state(url)


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
    reason = not_observed(obs, needs_click)
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
            detail=names(optional),
            fix=(
                f"These optional cookies are set {when}: {names(optional)}. Check the "
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
                f"{names(unclassified)}. Review them on the Cookies tab so we can tell "
                "whether they're allowed."
            ),
            evidence={"cookies": unclassified[:_EVIDENCE_LIMIT]},
        )
    return CheckResult(key, obs.phase, label, "passed")


def _trackers_check(
    obs: PhaseObservation, expect: Expectations, *, needs_click: bool
) -> CheckResult:
    before = obs.phase == "before_consent"
    key = f"{obs.phase}.trackers"
    label = (
        "No trackers load before the visitor chooses" if before else "No trackers load after Reject"
    )
    reason = not_observed(obs, needs_click)
    if reason:
        return CheckResult(key, obs.phase, label, "inconclusive", detail=reason)

    hosts = sorted(
        {
            host
            for url in obs.tracker_requests
            if (host := tracker_host(url))
            and not (expect.gcm_enabled and judged_by_consent_mode(url))
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
        detail=names(hosts),
        fix=(
            f"These trackers loaded {when}: {names(hosts)}. Check the scripts that load "
            "them have a category on the Cookies tab, and that the ConsentOS script is the "
            "first in <head>."
        ),
        evidence={"hosts": hosts[:_EVIDENCE_LIMIT]},
    )


def _saved_check(obs: PhaseObservation) -> CheckResult:
    accept = obs.phase == "after_accept"
    key = f"{obs.phase}.saved"
    label = "Accepting is remembered" if accept else "Rejecting is remembered"
    reason = not_observed(obs, needs_click=True)
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
    reason = not_observed(obs, needs_click)
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
    gpc: PhaseObservation | None = None,
    banner: BannerObservation | None = None,
) -> list[CheckResult]:
    """Turn what each phase looked like into the site's consent checks.

    ``gpc`` is a visit with Global Privacy Control on, made only when the
    site has GPC switched on. ``banner`` is how the banner looked, for
    the dark-pattern checks.
    """
    consent_mode = consent_mode_checks(expect, before, accept, reject)
    gpp = gpp_checks(expect, before, accept, reject, gpc or PhaseObservation("with_gpc"))
    return [
        _cookies_check(before, expect.cookies, needs_click=False),
        _trackers_check(before, expect, needs_click=False),
        *consent_mode["before_consent"],
        _tcf_check(before, expect, needs_click=False),
        *gpp["before_consent"],
        *dark_pattern_checks(before, reject, banner or BannerObservation()),
        _saved_check(accept),
        *consent_mode["after_accept"],
        _tcf_check(accept, expect, needs_click=True),
        *gpp["after_accept"],
        _saved_check(reject),
        _cookies_check(reject, expect.cookies, needs_click=True),
        _trackers_check(reject, expect, needs_click=True),
        *consent_mode["after_reject"],
        _tcf_check(reject, expect, needs_click=True),
        *gpp["after_reject"],
        *consent_mode["received"],
        *gpp["with_gpc"],
    ]


# ── Observing ────────────────────────────────────────────────────────


class _Visit:
    """One fresh browser context, recording requests by phase."""

    def __init__(self, context: BrowserContext, page: Page) -> None:
        self.context = context
        self.page = page
        self.phase: Phase = "before_consent"
        self.requests: dict[Phase, list[str]] = defaultdict(list)
        self.consent_state_requests: dict[Phase, list[str]] = defaultdict(list)
        self.config_response: Response | None = None
        page.on("request", self._on_request)
        page.on("response", self._on_response)

    def _on_request(self, request) -> None:  # noqa: ANN001
        if tracker_host(request.url):
            self.requests[self.phase].append(request.url)
        if carries_consent_state(request.url):
            self.consent_state_requests[self.phase].append(request.url)

    def _on_response(self, response: Response) -> None:
        if CONFIG_PATH in response.url:
            self.config_response = response

    async def blocking_mode(self) -> str | None:
        """The blocking mode in the config the banner was served."""
        if self.config_response is None:
            return None
        try:
            return (await self.config_response.json()).get("blocking_mode")
        except Exception:
            return None

    async def choose(self, obs: PhaseObservation, selectors: tuple[str, ...]) -> None:
        """Make the visitor's choice, recording when on the page's clock."""
        self.phase = obs.phase
        try:
            obs.click_time = await self.page.evaluate("performance.now()")
        except Exception:
            obs.click_time = None
        obs.clicked = await _click(self.page, selectors)

    async def observe(self, obs: PhaseObservation) -> None:
        obs.observed = True
        cookies = await self.context.cookies()
        obs.cookies = [{"name": c["name"], "domain": c["domain"]} for c in cookies]
        obs.consent_state = _saved_choice(cookies)
        obs.tracker_requests = list(self.requests[obs.phase])
        obs.consent_state_requests = list(self.consent_state_requests[obs.phase])
        try:
            signals = await self.page.evaluate(_SIGNALS_SCRIPT)
        except Exception:
            logger.debug("Couldn't read consent signals", exc_info=True)
            return
        obs.consent_commands = signals.get("consent") or []
        obs.tcf = signals.get("tcf")
        obs.gpp = signals.get("gpp")


def _saved_choice(cookies: list[dict]) -> dict | None:
    """The banner's saved choice, from the value of its consent cookie."""
    for cookie in cookies:
        if cookie["name"] == CONSENT_COOKIE:
            try:
                return json.loads(unquote(cookie.get("value", "")))
            except ValueError:
                return None
    return None


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
    """Visit ``url`` as a new visitor and accept, again and reject, then with GPC on."""
    from playwright.async_api import async_playwright

    result = ValidationResult(url=url)
    before = PhaseObservation("before_consent")
    accept = PhaseObservation("after_accept")
    reject = PhaseObservation("after_reject")
    gpc = PhaseObservation("with_gpc")
    banner = BannerObservation()

    async def new_visit(browser, *, send_gpc: bool = False) -> _Visit:  # noqa: ANN001
        context = await browser.new_context(
            ignore_https_errors=True,
            service_workers="block",
            extra_http_headers={"Sec-GPC": "1"} if send_gpc else None,
        )
        await policy.apply(context)
        await context.add_init_script(RECORDER_SCRIPT)
        if send_gpc:
            await context.add_init_script(GPC_SCRIPT)
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
                    await measure_banner(
                        visit.page,
                        banner,
                        settle_ms=settle_ms,
                        blocking_mode=await visit.blocking_mode(),
                    )
                    await visit.choose(accept, _ACCEPT_SELECTORS)
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
                await visit.choose(reject, _REJECT_SELECTORS)
                if reject.clicked:
                    await visit.page.wait_for_timeout(settle_ms)
                    await visit.observe(reject)
                    await measure_wall(visit.page, banner)
            await visit.context.close()

            if expect.gpc_enabled:
                visit = await new_visit(browser, send_gpc=True)
                await _load(visit, url, gpc, timeout_ms, banner_wait_ms)
                if gpc.loaded and gpc.banner_found:
                    await visit.choose(gpc, _ACCEPT_SELECTORS)
                    if gpc.clicked:
                        await visit.page.wait_for_timeout(settle_ms)
                        await visit.observe(gpc)
                await visit.context.close()
        except Exception as exc:
            logger.warning("Consent checks failed for %s: %s", url, exc)
            result.error = str(exc).splitlines()[0]
            # Phases the browser didn't get to are reported with the error,
            # not as a missing button.
            for obs in (before, accept, reject, gpc):
                if not obs.observed:
                    obs.loaded, obs.error = False, result.error
        finally:
            await browser.close()

    result.checks = judge(expect, before, accept, reject, gpc, banner)
    return result
