"""Consent Mode checks: does Google receive the visitor's choice?

Two kinds of evidence are used. The ``consent`` commands the page sends
through ``gtag`` show what the site told Google. The ``gcs`` parameter on
Google's own requests shows what Google actually received, which catches
anything else on the page overriding the banner.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from src.consent_results import (
    CheckResult,
    Expectations,
    Phase,
    PhaseObservation,
    names,
    not_observed,
)

# The types Google uses for advertising and analytics storage.
REQUIRED_TYPES = ("ad_storage", "ad_user_data", "ad_personalization", "analytics_storage")

# ``gcs`` is "G1" followed by the ad_storage and analytics_storage states:
# 1 granted, 0 denied, - not set.
_EXPECTED_GCS: dict[Phase, str] = {
    "before_consent": "00",
    "after_accept": "11",
    "after_reject": "00",
}
_PHASE_NAMES: dict[Phase, str] = {
    "before_consent": "before the visitor chose",
    "after_accept": "after Accept",
    "after_reject": "after Reject",
}


def gcs_value(url: str) -> str | None:
    """The ``gcs`` parameter on a Google request, if it has one."""
    values = parse_qs(urlsplit(url).query).get("gcs")
    return values[0] if values else None


def gcs_states(value: str) -> str | None:
    """The ad_storage and analytics_storage states in a ``gcs`` value."""
    if len(value) == 4 and value.startswith("G1"):
        return value[2:]
    return None


def _default(commands: list[dict]) -> dict | None:
    return next((c for c in commands if c.get("command") == "default"), None)


def _state(commands: list[dict]) -> dict:
    """The consent state after every command, defaults first then updates."""
    state: dict = {}
    for command in commands:
        if command.get("command") in ("default", "update"):
            state.update(command.get("state") or {})
    return state


def _off(expect: Expectations, key: str, phase: Phase, label: str) -> CheckResult | None:
    if expect.gcm_enabled:
        return None
    return CheckResult(key, phase, label, "not_applicable")


def _default_check(expect: Expectations, obs: PhaseObservation) -> CheckResult:
    key, label = "before_consent.gcm_default", "Consent Mode defaults are set before Google tags"
    if off := _off(expect, key, obs.phase, label):
        return off
    if reason := not_observed(obs, needs_click=False):
        return CheckResult(key, obs.phase, label, "inconclusive", detail=reason)
    default = _default(obs.consent_commands)
    if default is None:
        return CheckResult(
            key,
            obs.phase,
            label,
            "failed",
            fix=(
                "The page didn't set Consent Mode defaults, so Google tags run as if the "
                "visitor had consented. Check the ConsentOS script is on the page and is the "
                "first in <head>."
            ),
        )
    early = [tag["url"] for tag in obs.google_tag_loads if tag["t"] < default["t"]]
    if early:
        return CheckResult(
            key,
            obs.phase,
            label,
            "failed",
            detail=names(early),
            fix=(
                "A Google tag loaded before Consent Mode defaults were set, so it ran without "
                "them. Move the ConsentOS script above your Google tag or Tag Manager snippet "
                "in <head>."
            ),
            evidence={"tags": early},
        )
    return CheckResult(key, obs.phase, label, "passed")


def _denied_by_default_check(expect: Expectations, obs: PhaseObservation) -> CheckResult:
    key, label = "before_consent.gcm_denied", "Consent Mode defaults to denied"
    if off := _off(expect, key, obs.phase, label):
        return off
    if reason := not_observed(obs, needs_click=False):
        return CheckResult(key, obs.phase, label, "inconclusive", detail=reason)
    default = _default(obs.consent_commands)
    if default is None:
        return CheckResult(
            key, obs.phase, label, "inconclusive", detail="No Consent Mode defaults were set."
        )
    state = default.get("state") or {}
    not_denied = [t for t in REQUIRED_TYPES if state.get(t) != "denied"]
    if not not_denied:
        return CheckResult(key, obs.phase, label, "passed")
    return CheckResult(
        key,
        obs.phase,
        label,
        "failed",
        detail=names(not_denied),
        fix=(
            f"These Consent Mode types don't default to denied: {names(not_denied)}. They "
            "must be denied until the visitor chooses."
        ),
        evidence={"types": not_denied},
    )


def _waits_check(expect: Expectations, obs: PhaseObservation) -> CheckResult:
    key, label = "before_consent.gcm_wait", "Google tags wait for the visitor's choice"
    if off := _off(expect, key, obs.phase, label):
        return off
    if reason := not_observed(obs, needs_click=False):
        return CheckResult(key, obs.phase, label, "inconclusive", detail=reason)
    default = _default(obs.consent_commands)
    if default is None:
        return CheckResult(
            key, obs.phase, label, "inconclusive", detail="No Consent Mode defaults were set."
        )
    wait = (default.get("state") or {}).get("wait_for_update")
    if isinstance(wait, (int, float)) and wait > 0:
        return CheckResult(key, obs.phase, label, "passed")
    return CheckResult(
        key,
        obs.phase,
        label,
        "failed",
        severity="warning",
        fix=(
            "The Consent Mode default has no wait_for_update, so Google tags that load "
            "before the banner don't wait for the visitor's choice."
        ),
    )


def _accept_check(expect: Expectations, obs: PhaseObservation) -> CheckResult:
    key, label = "after_accept.gcm_update", "Accept grants Consent Mode consent"
    if off := _off(expect, key, obs.phase, label):
        return off
    if reason := not_observed(obs, needs_click=True):
        return CheckResult(key, obs.phase, label, "inconclusive", detail=reason)
    since = obs.click_time if obs.click_time is not None else float("-inf")
    updates = [c for c in obs.consent_commands if c.get("command") == "update" and c["t"] >= since]
    if not updates:
        return CheckResult(
            key,
            obs.phase,
            label,
            "failed",
            fix=(
                "After Accept, the page didn't send a Consent Mode update, so Google still "
                "treats the visitor as not having consented."
            ),
        )
    state = _state(obs.consent_commands)
    still_denied = [t for t in REQUIRED_TYPES if state.get(t) != "granted"]
    if not still_denied:
        return CheckResult(key, obs.phase, label, "passed")
    return CheckResult(
        key,
        obs.phase,
        label,
        "failed",
        detail=names(still_denied),
        fix=f"After Accept, these Consent Mode types aren't granted: {names(still_denied)}.",
        evidence={"types": still_denied},
    )


def _reject_check(expect: Expectations, obs: PhaseObservation) -> CheckResult:
    key, label = "after_reject.gcm_denied", "Reject keeps Consent Mode denied"
    if off := _off(expect, key, obs.phase, label):
        return off
    if reason := not_observed(obs, needs_click=True):
        return CheckResult(key, obs.phase, label, "inconclusive", detail=reason)
    if not obs.consent_commands:
        return CheckResult(
            key, obs.phase, label, "inconclusive", detail="No Consent Mode commands were seen."
        )
    state = _state(obs.consent_commands)
    granted = [t for t in REQUIRED_TYPES if state.get(t) == "granted"]
    if not granted:
        return CheckResult(key, obs.phase, label, "passed")
    return CheckResult(
        key,
        obs.phase,
        label,
        "failed",
        detail=names(granted),
        fix=(
            f"After Reject, these Consent Mode types are granted: {names(granted)}. Check "
            "nothing else on the page, such as a Tag Manager consent setting, grants them."
        ),
        evidence={"types": granted},
    )


def _received_check(expect: Expectations, phases: list[PhaseObservation]) -> CheckResult:
    key, label = "gcm_received", "Google receives the visitor's choice"
    if off := _off(expect, key, "after_reject", label):
        return off
    seen: list[str] = []
    wrong: list[str] = []
    for obs in phases:
        if not_observed(obs, needs_click=obs.phase != "before_consent"):
            continue
        values = [v for url in obs.consent_state_requests if (v := gcs_value(url))]
        if not values:
            continue
        seen.append(_PHASE_NAMES[obs.phase])
        bad = sorted({v for v in values if gcs_states(v) != _EXPECTED_GCS[obs.phase]})
        if bad:
            wrong.append(f"{_PHASE_NAMES[obs.phase]}: {', '.join(bad)}")
    if wrong:
        return CheckResult(
            key,
            "after_reject",
            label,
            "failed",
            detail="; ".join(wrong),
            fix=(
                "Google received a consent state that doesn't match the visitor's choice "
                f"({'; '.join(wrong)}). Check nothing else on the page, such as a Tag Manager "
                "consent setting, overrides ConsentOS."
            ),
            evidence={"gcs": wrong},
        )
    if not seen:
        return CheckResult(
            key,
            "after_reject",
            label,
            "inconclusive",
            detail=(
                "No Google requests reported a consent state, so we couldn't confirm what "
                "Google received."
            ),
        )
    return CheckResult(key, "after_reject", label, "passed", detail=f"Checked {', '.join(seen)}.")


def consent_mode_checks(
    expect: Expectations,
    before: PhaseObservation,
    accept: PhaseObservation,
    reject: PhaseObservation,
) -> dict[Phase | str, list[CheckResult]]:
    """The Consent Mode checks, grouped by the phase they belong with."""
    return {
        "before_consent": [
            _default_check(expect, before),
            _denied_by_default_check(expect, before),
            _waits_check(expect, before),
        ],
        "after_accept": [_accept_check(expect, accept)],
        "after_reject": [_reject_check(expect, reject)],
        "received": [_received_check(expect, [before, accept, reject])],
    }
