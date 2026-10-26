"""Test pages for the consent checks: one set up correctly, and broken ones.

Each page is a small stand-in for a site with a consent banner. A
``Setup`` describes how it behaves, and every broken page changes one
thing from the correct setup. The page also stands in for Google: it
sends a request carrying ``gcs`` whenever the consent state changes, as
Google tags do, so the Consent Mode checks see what Google would.

The GPP strings are built from the GPP specification's layouts: a header
listing usnat, then a usnat core with every opt-out set to the visitor's
choice and, where asked, the GPC sub-section.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

_BASE64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
HEADER_USNAT = "DBABLA"


def _encode(bits: str) -> str:
    bits += "0" * (-len(bits) % 6)
    return "".join(_BASE64URL[int(bits[i : i + 6], 2)] for i in range(0, len(bits), 6))


def usnat_string(opt_out: int, gpc: bool) -> str:
    """A GPP string with a usnat v1 section: notices given, opt-outs ``opt_out``."""
    core = _encode(f"{1:06b}" + "01" * 6 + f"{opt_out:02b}" * 3 + "00" * 18)
    return f"{HEADER_USNAT}~{core}.{_encode('01' + ('1' if gpc else '0'))}"


@dataclass(frozen=True)
class Setup:
    """How a test page behaves."""

    consent_default: str = "denied"  # denied, granted, none or after_tag
    wait_for_update: bool = True
    update_on_accept: bool = True
    reject_grants: bool = False
    tracker_before_consent: bool = False
    cookie_before_consent: bool = False
    gpp: str = "ok"  # ok, missing or stale
    gpc_recorded: bool = True
    reject_button: bool = True


@dataclass(frozen=True)
class Case:
    name: str
    setup: Setup
    # Checks that don't simply pass, and the result each should get.
    expected: dict[str, str] = field(default_factory=dict)


CORRECT = Setup()

CASES = [
    Case("correct", CORRECT),
    Case(
        "no-consent-default",
        replace(CORRECT, consent_default="none"),
        {
            "before_consent.gcm_default": "failed",
            "before_consent.gcm_denied": "inconclusive",
            "before_consent.gcm_wait": "inconclusive",
        },
    ),
    Case(
        "default-after-google-tag",
        replace(CORRECT, consent_default="after_tag"),
        {"before_consent.gcm_default": "failed"},
    ),
    Case(
        "default-granted",
        replace(CORRECT, consent_default="granted"),
        {"before_consent.gcm_denied": "failed", "gcm_received": "failed"},
    ),
    Case(
        "no-wait-for-update",
        replace(CORRECT, wait_for_update=False),
        {"before_consent.gcm_wait": "failed"},
    ),
    Case(
        "no-update-after-accept",
        replace(CORRECT, update_on_accept=False),
        {"after_accept.gcm_update": "failed", "gcm_received": "failed"},
    ),
    Case(
        "reject-still-grants",
        replace(CORRECT, reject_grants=True),
        {"after_reject.gcm_denied": "failed", "gcm_received": "failed"},
    ),
    Case(
        "tracker-before-consent",
        replace(CORRECT, tracker_before_consent=True),
        {"before_consent.trackers": "failed"},
    ),
    Case(
        "cookie-before-consent",
        replace(CORRECT, cookie_before_consent=True),
        # Nothing removes the cookie on Reject either.
        {"before_consent.cookies": "failed", "after_reject.cookies": "failed"},
    ),
    Case(
        "gpp-missing",
        replace(CORRECT, gpp="missing"),
        {
            "before_consent.gpp_api": "failed",
            "after_accept.gpp": "failed",
            "after_reject.gpp": "failed",
        },
    ),
    Case(
        "gpp-not-updated",
        replace(CORRECT, gpp="stale"),
        {"after_accept.gpp": "failed", "after_reject.gpp": "failed"},
    ),
    Case(
        "gpc-ignored",
        replace(CORRECT, gpc_recorded=False),
        {"with_gpc.recorded": "failed"},
    ),
    Case(
        "no-reject-button",
        replace(CORRECT, reject_button=False),
        {
            "after_reject.saved": "inconclusive",
            "after_reject.cookies": "inconclusive",
            "after_reject.trackers": "inconclusive",
            "after_reject.gcm_denied": "inconclusive",
            "after_reject.gpp": "inconclusive",
        },
    ),
]

# What the test site has switched on, for every page.
EXPECTED_COOKIES = [("session_id", "necessary"), ("_ga", "analytics")]

_DENIED = {
    "ad_storage": "denied",
    "ad_user_data": "denied",
    "ad_personalization": "denied",
    "analytics_storage": "denied",
}
_GRANTED = {key: "granted" for key in _DENIED}
# The Google tag snippet: the library, then its start-up commands.
_GTAG = """<script src="https://www.googletagmanager.com/gtag/js?id=G-TEST"></script>
<script>
window.dataLayer = window.dataLayer || [];
function gtag() { dataLayer.push(arguments); }
gtag('js', new Date());
gtag('config', 'G-TEST');
</script>"""


def render(setup: Setup) -> str:
    """The HTML for a page behaving as ``setup`` says."""
    default = _GRANTED if setup.consent_default == "granted" else dict(_DENIED)
    if setup.wait_for_update:
        default["wait_for_update"] = 500
    config = {
        "consentDefault": setup.consent_default,
        "default": default,
        "denied": _DENIED,
        "granted": _GRANTED,
        "updateOnAccept": setup.update_on_accept,
        "rejectGrants": setup.reject_grants,
        "trackerBeforeConsent": setup.tracker_before_consent,
        "cookieBeforeConsent": setup.cookie_before_consent,
        "gpp": setup.gpp,
        "gpcRecorded": setup.gpc_recorded,
        "rejectButton": setup.reject_button,
        "gppStrings": {
            "accept": usnat_string(2, gpc=False),
            "acceptGpc": usnat_string(2, gpc=True),
            "reject": usnat_string(1, gpc=False),
            "rejectGpc": usnat_string(1, gpc=True),
        },
    }
    tag_first = _GTAG if setup.consent_default == "after_tag" else ""
    tag_after = "" if setup.consent_default == "after_tag" else _GTAG
    return f"""<!doctype html>
<html><head>
{tag_first}
<script>
const SETUP = {json.dumps(config)};
window.dataLayer = window.dataLayer || [];
function gtag() {{ dataLayer.push(arguments); }}
let state = {{}};
const states = (s) => (s.ad_storage === 'granted' ? '1' : s.ad_storage ? '0' : '-')
  + (s.analytics_storage === 'granted' ? '1' : s.analytics_storage ? '0' : '-');
// Stands in for a Google tag reporting the consent state it received.
const ping = () => fetch('https://www.google-analytics.com/g/collect?v=2&gcs=G1' + states(state));
if (SETUP.consentDefault !== 'none') {{
  gtag('consent', 'default', SETUP.default);
  state = {{ ...SETUP.default }};
}}
if (SETUP.cookieBeforeConsent) document.cookie = '_ga=GA1.1.1; path=/';
if (SETUP.trackerBeforeConsent) fetch('https://connect.facebook.net/en_US/fbevents.js');
document.cookie = 'session_id=1; path=/';
let gppString = '';
if (SETUP.gpp !== 'missing') {{
  window.__gpp = (command, callback) => {{
    if (command !== 'ping') return;
    callback({{ cmpStatus: 'loaded', supportedAPIs: ['7:usnat'], gppString }}, true);
  }};
}}
</script>
{tag_after}
</head><body>
<div id="consentos-banner-host"></div>
<script>
const gpc = navigator.globalPrivacyControl === true;
if (SETUP.consentDefault !== 'none') ping();
const choose = (accepted) => {{
  const saved = {{ accepted }};
  if (gpc && SETUP.gpcRecorded) Object.assign(saved, {{ gpcDetected: true, gpcHonoured: true }});
  document.cookie = '_consentos_consent=' + encodeURIComponent(JSON.stringify(saved)) + '; path=/';
  const grant = accepted ? SETUP.updateOnAccept : SETUP.rejectGrants;
  const update = grant ? SETUP.granted : SETUP.denied;
  if (accepted ? SETUP.updateOnAccept : true) {{
    gtag('consent', 'update', update);
    state = {{ ...state, ...update }};
  }}
  if (accepted) document.cookie = '_ga=GA1.1.1; path=/';
  if (SETUP.gpp === 'ok') {{
    const key = (accepted ? 'accept' : 'reject') + (gpc && SETUP.gpcRecorded ? 'Gpc' : '');
    gppString = SETUP.gppStrings[key];
  }}
  ping();
}};
const root = document.getElementById('consentos-banner-host').attachShadow({{ mode: 'open' }});
root.innerHTML = '<button data-action="accept">Accept all</button>'
  + (SETUP.rejectButton ? '<button data-action="reject">Reject all</button>' : '');
root.querySelector('[data-action=accept]').onclick = () => choose(true);
if (SETUP.rejectButton) root.querySelector('[data-action=reject]').onclick = () => choose(false);
</script>
</body></html>
"""
