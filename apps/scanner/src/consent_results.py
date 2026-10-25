"""What the consent checks observe and report, shared by each set of checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CheckStatus = Literal["passed", "failed", "inconclusive", "not_applicable"]
Phase = Literal["before_consent", "after_accept", "after_reject"]


@dataclass
class ExpectedCookie:
    name: str
    category: str | None


@dataclass
class Expectations:
    """What the site has switched on, from its live configuration."""

    tcf_enabled: bool = False
    gcm_enabled: bool = False
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
    # Consent Mode commands sent through gtag, each with its page time ``t``.
    consent_commands: list[dict] = field(default_factory=list)
    # When the choice was made, on the same clock as ``consent_commands``.
    click_time: float | None = None
    # Google tag scripts that loaded, each with its start time ``t``.
    google_tag_loads: list[dict] = field(default_factory=list)
    # Google requests made in this phase that report a consent state.
    consent_state_requests: list[str] = field(default_factory=list)
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
    severity: Literal["error", "warning"] = "error"


def names(items: list[str]) -> str:
    shown = ", ".join(items[:5])
    return shown if len(items) <= 5 else f"{shown} and {len(items) - 5} more"


def not_observed(obs: PhaseObservation, needs_click: bool) -> str | None:
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
