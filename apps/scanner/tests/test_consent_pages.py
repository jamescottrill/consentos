"""Run the consent checks in real Chromium against pages that pass and fail on purpose.

Each page in ``consent_pages.CASES`` is served locally. Requests to any
other host, such as the stand-in tracker and Google requests, are
answered by the browser without leaving the machine, so the tests need
no network.
"""

from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.consent_validator import Expectations, ExpectedCookie, run_consent_checks
from tests.consent_pages import CASES, EXPECTED_COOKIES, render

EXPECT = Expectations(
    gcm_enabled=True,
    gpp_enabled=True,
    gpp_sections=["usnat"],
    gpc_enabled=True,
    gpc_global_honour=True,
    cookies=[ExpectedCookie(name, category) for name, category in EXPECTED_COOKIES],
)


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: ANN002
        pass


class LocalOnly:
    """A navigation policy that serves anything off the test server as empty."""

    def __init__(self, origin: str) -> None:
        self.origin = origin

    async def apply(self, context) -> None:  # noqa: ANN001
        async def handle(route) -> None:  # noqa: ANN001
            if route.request.url.startswith(self.origin):
                await route.continue_()
            else:
                await route.fulfill(status=200, body="", content_type="text/javascript")

        await context.route("**/*", handle)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("consent-pages")
    for case in CASES:
        (root / f"{case.name}.html").write_text(render(case.setup))
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
async def test_each_page_gets_the_results_it_should(site, case):
    result = await run_consent_checks(
        f"{site}/{case.name}.html",
        EXPECT,
        headless=True,
        timeout_ms=15_000,
        policy=LocalOnly(site),
        banner_wait_ms=5_000,
        # Long enough for each choice's requests on a slow CI runner.
        settle_ms=750,
    )

    assert result.error is None
    got = {c.key: c.status for c in result.checks}
    # TCF is off for the test site, so its checks never apply.
    want = {key: "not_applicable" if key.endswith(".tcf") else "passed" for key in got}
    want.update(case.expected)
    assert got == want
