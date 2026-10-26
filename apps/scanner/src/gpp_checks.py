"""GPP and GPC checks: is the visitor's choice signalled to the specification?

The GPP string is decoded here, independently of the banner, using the
field layouts of the IAB reference library (iabgpp-es). That way a
banner that encodes the string wrongly fails the check rather than
agreeing with itself.
"""

from __future__ import annotations

from src.consent_results import (
    CheckResult,
    Expectations,
    PhaseObservation,
    names,
    not_observed,
)

SECTION_IDS = {
    "usnat": 7,
    "usca": 8,
    "usva": 9,
    "usco": 10,
    "usut": 11,
    "usct": 12,
    "usfl": 13,
}
# Sections whose string has a GPC sub-section.
GPC_SECTIONS = frozenset({"usnat", "usca", "usco", "usct"})
OPT_OUT_FIELDS = ("SaleOptOut", "SharingOptOut", "TargetedAdvertisingOptOut")
# Opt-out field values: 1 opted out, 2 did not opt out.
OPTED_OUT, DID_NOT_OPT_OUT = 1, 2

_US_STATE = [
    "SharingNotice",
    "SaleOptOutNotice",
    "TargetedAdvertisingOptOutNotice",
    "SaleOptOut",
    "TargetedAdvertisingOptOut",
]
# Each section's 2-bit fields after its 6-bit version, up to its last
# opt-out field. Everything the checks read comes before the arrays.
_LEADING_FIELDS: dict[str, list[str]] = {
    "usnat": [
        "SharingNotice",
        "SaleOptOutNotice",
        "SharingOptOutNotice",
        "TargetedAdvertisingOptOutNotice",
        "SensitiveDataProcessingOptOutNotice",
        "SensitiveDataLimitUseNotice",
        "SaleOptOut",
        "SharingOptOut",
        "TargetedAdvertisingOptOut",
    ],
    "usca": [
        "SaleOptOutNotice",
        "SharingOptOutNotice",
        "SensitiveDataLimitUseNotice",
        "SaleOptOut",
        "SharingOptOut",
    ],
    "usva": _US_STATE,
    "usco": _US_STATE,
    "usct": _US_STATE,
    "usfl": ["ProcessingNotice", *_US_STATE[1:]],
}
_PREFIXES = {v: k for k, v in SECTION_IDS.items()}
_BASE64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


class GppDecodeError(ValueError):
    """The string isn't a valid GPP string."""


class _Bits:
    def __init__(self, encoded: str) -> None:
        try:
            self.bits = "".join(f"{_BASE64URL.index(ch):06b}" for ch in encoded)
        except ValueError as exc:
            raise GppDecodeError(f"Not base64url: {encoded!r}") from exc
        self.pos = 0

    def int(self, width: int) -> int:
        if self.pos + width > len(self.bits):
            raise GppDecodeError("The string ends early")
        value = int(self.bits[self.pos : self.pos + width], 2)
        self.pos += width
        return value

    def fibonacci(self) -> int:
        """A Fibonacci-coded integer: Zeckendorf bits, lowest first, ending 11."""
        fib = [1, 2]
        value, prev = 0, "0"
        index = 0
        while True:
            bit = self.int(1)
            if bit and prev == "1":
                return value
            if bit:
                while len(fib) <= index:
                    fib.append(fib[-1] + fib[-2])
                value += fib[index]
            prev = "1" if bit else "0"
            index += 1


def decode_header(encoded: str) -> list[int]:
    """The section IDs in a GPP header, per the GPP v1 specification."""
    bits = _Bits(encoded)
    if bits.int(6) != 3:
        raise GppDecodeError("The header doesn't start with type 3")
    if bits.int(6) != 1:
        raise GppDecodeError("The header isn't GPP version 1")
    ids: list[int] = []
    offset = 0
    for _ in range(bits.int(12)):
        is_range = bits.int(1)
        start = bits.fibonacci() + offset
        if is_range:
            end = bits.fibonacci() + start
            ids.extend(range(start, end + 1))
            offset = end
        else:
            ids.append(start)
            offset = start
    return ids


def decode_section(prefix: str, encoded: str) -> dict[str, int]:
    """The leading fields of a US section, and its GPC flag if it has one."""
    core, _, subsection = encoded.partition(".")
    bits = _Bits(core)
    fields = {"Version": bits.int(6)}
    for name in _LEADING_FIELDS[prefix]:
        fields[name] = bits.int(2)
    if subsection and prefix in GPC_SECTIONS:
        sub = _Bits(subsection)
        if sub.int(2) == 1:
            fields["Gpc"] = sub.int(1)
    return fields


def decode_gpp(gpp_string: str) -> dict[str, dict[str, int]]:
    """Each US section in a GPP string, by API prefix."""
    header, *sections = gpp_string.split("~")
    ids = decode_header(header)
    if len(ids) != len(sections):
        raise GppDecodeError(
            f"The header lists {len(ids)} sections but the string has {len(sections)}"
        )
    return {
        _PREFIXES[sid]: decode_section(_PREFIXES[sid], section)
        for sid, section in zip(ids, sections, strict=True)
        if _PREFIXES.get(sid) in _LEADING_FIELDS
    }


def expected_sections(expect: Expectations) -> list[str]:
    """The sections the banner should encode: usnat when none are listed."""
    listed = [p for p in expect.gpp_sections if p in _LEADING_FIELDS]
    return listed or (["usnat"] if not expect.gpp_sections else [])


def _gpp_string(obs: PhaseObservation) -> str:
    """The GPP string ``ping`` reported, as GPP 1.1 has it."""
    return ((obs.gpp or {}).get("ping") or {}).get("gppString") or ""


def _off(expect: Expectations, obs: PhaseObservation, key: str, label: str) -> CheckResult | None:
    if expect.gpp_enabled:
        return None
    return CheckResult(key, obs.phase, label, "not_applicable")


def _api_check(expect: Expectations, obs: PhaseObservation) -> CheckResult:
    key, label = "before_consent.gpp_api", "The GPP API responds"
    if off := _off(expect, obs, key, label):
        return off
    if reason := not_observed(obs, needs_click=False):
        return CheckResult(key, obs.phase, label, "inconclusive", detail=reason)
    ping = (obs.gpp or {}).get("ping")
    if obs.gpp is None:
        return CheckResult(
            key,
            obs.phase,
            label,
            "failed",
            fix=(
                "GPP is switched on, but the page has no GPP API (__gpp). Check the ConsentOS "
                "script loads on this page."
            ),
        )
    if not ping or ping.get("timedOut"):
        return CheckResult(
            key, obs.phase, label, "inconclusive", detail="The GPP API didn't answer a ping."
        )
    if ping.get("cmpStatus") != "loaded":
        return CheckResult(
            key,
            obs.phase,
            label,
            "failed",
            detail=f"cmpStatus is {ping.get('cmpStatus')!r}",
            fix="The GPP API reports it hasn't finished loading. Check the browser's console.",
        )
    listed = ping.get("supportedAPIs") or []
    supported = {api.split(":", 1)[-1] for api in listed}
    missing = [s for s in expected_sections(expect) if s not in supported]
    if missing:
        return CheckResult(
            key,
            obs.phase,
            label,
            "failed",
            detail=names(missing),
            fix=(
                f"The GPP API doesn't list these sections the site uses: {names(missing)}, so "
                "vendors won't ask for them."
            ),
            evidence={"missing": missing},
        )
    # GPP 1.1 lists each section as "<id>:<prefix>", e.g. "7:usnat".
    wanted = {s: f"{SECTION_IDS[s]}:{s}" for s in expected_sections(expect)}
    wrong = [api for api in listed if wanted.get(api.split(":", 1)[-1], api) != api]
    if wrong:
        return CheckResult(
            key,
            obs.phase,
            label,
            "failed",
            detail=names(wrong),
            fix=(
                "The GPP API lists sections without their GPP 1.1 section IDs, so vendors "
                f"written for 1.1 won't find them. Expected {names(list(wanted.values()))}."
            ),
            evidence={"wrong": wrong},
        )
    return CheckResult(key, obs.phase, label, "passed")


def _string_check(expect: Expectations, obs: PhaseObservation) -> CheckResult:
    accept = obs.phase == "after_accept"
    key = f"{obs.phase}.gpp"
    label = (
        "Accept is recorded in the GPP string" if accept else "Reject is recorded in the GPP string"
    )
    if off := _off(expect, obs, key, label):
        return off
    if reason := not_observed(obs, needs_click=True):
        return CheckResult(key, obs.phase, label, "inconclusive", detail=reason)
    gpp_string = _gpp_string(obs)
    when = "After Accept" if accept else "After Reject"
    if not gpp_string:
        return CheckResult(
            key,
            obs.phase,
            label,
            "failed",
            fix=f"{when}, the GPP API returned no GPP string, so vendors can't read the choice.",
        )
    try:
        sections = decode_gpp(gpp_string)
    except GppDecodeError as exc:
        return CheckResult(
            key,
            obs.phase,
            label,
            "failed",
            detail=str(exc),
            fix=(
                f"{when}, the GPP string doesn't follow the GPP specification, so vendors will "
                "misread it."
            ),
            evidence={"gpp_string": gpp_string},
        )

    expected = expected_sections(expect)
    missing = [s for s in expected if s not in sections]
    if missing:
        return CheckResult(
            key,
            obs.phase,
            label,
            "failed",
            detail=names(missing),
            fix=f"{when}, the GPP string has no section for {names(missing)}.",
            evidence={"gpp_string": gpp_string},
        )
    want = DID_NOT_OPT_OUT if accept else OPTED_OUT
    wrong = [
        f"{prefix} {name}"
        for prefix in expected
        for name in OPT_OUT_FIELDS
        if name in sections[prefix] and sections[prefix][name] != want
    ]
    if wrong:
        state = "opted out" if accept else "not opted out"
        return CheckResult(
            key,
            obs.phase,
            label,
            "failed",
            detail=names(wrong),
            fix=f"{when}, the GPP string still says the visitor {state}: {names(wrong)}.",
            evidence={"fields": wrong, "gpp_string": gpp_string},
        )
    return CheckResult(key, obs.phase, label, "passed")


def _gpc_check(expect: Expectations, obs: PhaseObservation) -> CheckResult:
    key, label = "with_gpc.recorded", "A Global Privacy Control signal is recorded"
    if not expect.gpc_enabled:
        return CheckResult(key, obs.phase, label, "not_applicable")
    if reason := not_observed(obs, needs_click=True):
        return CheckResult(key, obs.phase, label, "inconclusive", detail=reason)

    problems: list[str] = []
    state = obs.consent_state or {}
    if state.get("gpcDetected") is not True:
        problems.append("the saved choice doesn't note the GPC signal")
    elif expect.gpc_global_honour and state.get("gpcHonoured") is not True:
        problems.append("the saved choice doesn't show GPC as honoured")

    gpp_string = _gpp_string(obs)
    if expect.gpp_enabled and gpp_string:
        try:
            sections = decode_gpp(gpp_string)
        except GppDecodeError:
            sections = {}
        unset = [
            p
            for p in expected_sections(expect)
            if p in GPC_SECTIONS and sections.get(p, {}).get("Gpc") != 1
        ]
        if unset:
            problems.append(f"the GPP string's GPC flag isn't set for {names(unset)}")

    if not problems:
        return CheckResult(key, obs.phase, label, "passed")
    return CheckResult(
        key,
        obs.phase,
        label,
        "failed",
        detail="; ".join(problems),
        fix=(
            "A visitor's browser sent a Global Privacy Control signal, but "
            f"{'; '.join(problems)}. US privacy laws require the signal to be honoured."
        ),
    )


def gpp_checks(
    expect: Expectations,
    before: PhaseObservation,
    accept: PhaseObservation,
    reject: PhaseObservation,
    gpc: PhaseObservation,
) -> dict[str, list[CheckResult]]:
    """The GPP and GPC checks, grouped by the phase they belong with."""
    return {
        "before_consent": [_api_check(expect, before)],
        "after_accept": [_string_check(expect, accept)],
        "after_reject": [_string_check(expect, reject)],
        "with_gpc": [_gpc_check(expect, gpc)],
    }
