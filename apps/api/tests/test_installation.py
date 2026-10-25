"""Detecting the banner on a site's pages, and checking an install."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.services.installation import (
    consent_check_items,
    consent_expectations,
    diagnose,
    installation_state,
    page_without_query,
)

try:
    from tests.conftest import create_test_site, publish_site, requires_db
except ImportError:
    from conftest import create_test_site, publish_site, requires_db


async def _site(client, headers, *, publish: bool = True) -> dict:
    site_id = await create_test_site(client, headers, domain_prefix="install", start_scan=False)
    if publish:
        await publish_site(client, headers, site_id)
    return (await client.get(f"/api/v1/sites/{site_id}", headers=headers)).json()


async def _installation(client, headers, site_id: str) -> dict:
    resp = await client.get(f"/api/v1/sites/{site_id}/installation", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _load_banner(client, site_id: str, **headers: str):
    return await client.get(f"/api/v1/config/sites/{site_id}/geo-resolved", headers=headers)


@requires_db
class TestDetection:
    async def test_a_banner_load_on_the_site_marks_it_installed(self, db_client, auth_headers):
        site = await _site(db_client, auth_headers)
        resp = await _load_banner(
            db_client,
            site["id"],
            origin=f"https://{site['domain']}",
            referer=f"https://{site['domain']}/pricing?email=someone@example.com",
        )
        assert resp.status_code == 200

        installation = await _installation(db_client, auth_headers, site["id"])
        assert installation["state"] == "installed"
        assert installation["first_seen_at"] is not None
        # The query string is never stored.
        assert installation["last_seen_url"] == f"https://{site['domain']}/pricing"

    async def test_loads_from_other_domains_are_ignored(self, db_client, auth_headers):
        site = await _site(db_client, auth_headers)
        await _load_banner(db_client, site["id"], origin="https://copied-snippet.example.org")

        assert (await _installation(db_client, auth_headers, site["id"]))["state"] == (
            "not_detected"
        )

    async def test_requests_without_an_origin_are_ignored(self, db_client, auth_headers):
        site = await _site(db_client, auth_headers)
        await _load_banner(db_client, site["id"])

        assert (await _installation(db_client, auth_headers, site["id"]))["state"] == (
            "not_detected"
        )

    async def test_an_unpublished_site_is_still_detected(self, db_client, auth_headers):
        site = await _site(db_client, auth_headers, publish=False)
        resp = await _load_banner(db_client, site["id"], origin=f"https://www.{site['domain']}")
        assert resp.status_code == 404

        installation = await _installation(db_client, auth_headers, site["id"])
        assert installation["state"] == "installed"
        assert installation["last_seen_url"] == f"https://www.{site['domain']}"

    async def test_recording_is_throttled(self, db_client, auth_headers):
        site = await _site(db_client, auth_headers)
        origin = f"https://{site['domain']}"
        await _load_banner(db_client, site["id"], origin=origin, referer=f"{origin}/first")
        await _load_banner(db_client, site["id"], origin=origin, referer=f"{origin}/second")

        installation = await _installation(db_client, auth_headers, site["id"])
        assert installation["last_seen_url"] == f"{origin}/first"

    async def test_redis_being_down_does_not_affect_the_banner(self, db_client, auth_headers):
        site = await _site(db_client, auth_headers)
        with patch(
            "src.services.installation._first_sighting_this_minute",
            new=AsyncMock(side_effect=ConnectionError),
        ):
            resp = await _load_banner(db_client, site["id"], origin=f"https://{site['domain']}")

        assert resp.status_code == 200
        assert (await _installation(db_client, auth_headers, site["id"]))["state"] == (
            "not_detected"
        )

    async def test_the_sites_list_shows_each_sites_state(self, db_client, auth_headers):
        seen = await _site(db_client, auth_headers)
        unseen = await _site(db_client, auth_headers)
        await _load_banner(db_client, seen["id"], origin=f"https://{seen['domain']}")

        resp = await db_client.get("/api/v1/sites/installations", headers=auth_headers)
        assert resp.status_code == 200
        states = {row["site_id"]: row["state"] for row in resp.json()}
        assert states[seen["id"]] == "installed"
        assert states[unseen["id"]] == "not_detected"


# ── Check now ────────────────────────────────────────────────────────


def _loader(site_id: str, **overrides) -> dict:
    return {
        "src": "https://cmp.example.net/consent-loader.js",
        "site_id": site_id,
        "api_base": "https://cmp.example.net",
        "cdn_base": None,
        "is_async": False,
        "is_defer": False,
        "in_head": True,
        "first_script": True,
        **overrides,
    }


def _found(site_id: str, domain: str, **overrides) -> dict:
    return {
        "url": f"https://{domain}/",
        "final_url": f"https://{domain}/",
        "reachable": True,
        "error": None,
        "loader": _loader(site_id),
        "config_requests": [{"url": "https://cmp.example.net/geo-resolved", "status": 200}],
        "banner_rendered": True,
        **overrides,
    }


CONSENT_PASSED = {
    "url": "https://example.com/",
    "checks": [
        {
            "key": "before_consent.cookies",
            "phase": "before_consent",
            "label": "No optional cookies are set before the visitor chooses",
            "status": "passed",
        },
        {
            "key": "before_consent.tcf",
            "phase": "before_consent",
            "label": "TCF reports no consent before the visitor chooses",
            "status": "not_applicable",
        },
    ],
}


def _scanner(
    found: dict | None = None,
    *,
    status_code: int = 200,
    down: bool = False,
    consent: dict | None = None,
    consent_down: bool = False,
    validate_requests: list | None = None,
):
    """Patch the router's HTTP client to answer like the scanner."""
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        if down:
            raise httpx.ConnectError("refused")
        if request.url.path == "/validate":
            if validate_requests is not None:
                validate_requests.append(json.loads(request.content))
            if consent_down:
                raise httpx.ConnectError("refused")
            return httpx.Response(200, json=consent or CONSENT_PASSED)
        assert request.url.path == "/check-install"
        return httpx.Response(status_code, json=found or {})

    return patch(
        "src.routers.installation.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )


@requires_db
class TestCheckNow:
    async def test_a_clean_install_passes(self, db_client, auth_headers):
        site = await _site(db_client, auth_headers)
        with _scanner(_found(site["id"], site["domain"])):
            resp = await db_client.post(
                f"/api/v1/sites/{site['id']}/installation/check", headers=auth_headers
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["state"] == "installed"
        assert data["last_check"]["ok"] is True
        assert data["last_check"]["url"] == f"https://{site['domain']}/"
        assert {c["status"] for c in data["last_check"]["checks"]} <= {"passed", "skipped"}

        # Stored, so it's still there on the next visit.
        assert (await _installation(db_client, auth_headers, site["id"]))["last_check"]["ok"]

    async def test_a_problem_is_reported_with_its_fix(self, db_client, auth_headers):
        site = await _site(db_client, auth_headers)
        found = _found(site["id"], site["domain"], loader=_loader(site["id"], is_async=True))
        with _scanner(found):
            resp = await db_client.post(
                f"/api/v1/sites/{site['id']}/installation/check", headers=auth_headers
            )

        data = resp.json()
        assert data["state"] == "problem"
        [failed] = [c for c in data["last_check"]["checks"] if c["status"] == "failed"]
        assert failed["key"] == "synchronous"
        assert "Remove async and defer" in failed["fix"]

    async def test_checks_a_given_page_on_the_site(self, db_client, auth_headers):
        site = await _site(db_client, auth_headers)
        page = f"https://shop.{site['domain']}/basket"
        with _scanner(_found(site["id"], site["domain"], url=page)):
            resp = await db_client.post(
                f"/api/v1/sites/{site['id']}/installation/check",
                json={"url": page},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["last_check"]["url"] == page

    async def test_refuses_pages_on_other_domains(self, db_client, auth_headers):
        site = await _site(db_client, auth_headers)
        resp = await db_client.post(
            f"/api/v1/sites/{site['id']}/installation/check",
            json={"url": "https://elsewhere.example.org/"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_consent_checks_are_added_without_those_that_do_not_apply(
        self, db_client, auth_headers
    ):
        site = await _site(db_client, auth_headers)
        sent: list = []
        with _scanner(_found(site["id"], site["domain"]), validate_requests=sent):
            resp = await db_client.post(
                f"/api/v1/sites/{site['id']}/installation/check", headers=auth_headers
            )

        consent = [c for c in resp.json()["last_check"]["checks"] if c["group"] == "consent"]
        assert [c["key"] for c in consent] == ["before_consent.cookies"]
        assert sent[0]["url"] == f"https://{site['domain']}/"
        assert sent[0]["expect"] == {
            "tcf_enabled": False,
            "gcm_enabled": True,
            "gpp_enabled": True,
            "gpp_sections": ["usnat"],
            "gpc_enabled": True,
            "gpc_global_honour": False,
            "cookies": [],
        }

    async def test_a_failed_consent_check_is_a_problem(self, db_client, auth_headers):
        site = await _site(db_client, auth_headers)
        consent = {
            "url": "https://example.com/",
            "checks": [
                {
                    "key": "after_reject.trackers",
                    "phase": "after_reject",
                    "label": "No trackers load after Reject",
                    "status": "failed",
                    "fix": "These trackers loaded after the visitor rejected: facebook.net.",
                }
            ],
        }
        with _scanner(_found(site["id"], site["domain"]), consent=consent):
            resp = await db_client.post(
                f"/api/v1/sites/{site['id']}/installation/check", headers=auth_headers
            )

        data = resp.json()
        assert data["state"] == "problem"
        assert data["last_check"]["ok"] is False

    async def test_consent_checks_are_skipped_when_the_banner_did_not_appear(
        self, db_client, auth_headers
    ):
        site = await _site(db_client, auth_headers)
        sent: list = []
        found = _found(site["id"], site["domain"], banner_rendered=False)
        with _scanner(found, validate_requests=sent):
            resp = await db_client.post(
                f"/api/v1/sites/{site['id']}/installation/check", headers=auth_headers
            )

        assert sent == []
        [consent] = [c for c in resp.json()["last_check"]["checks"] if c["group"] == "consent"]
        assert consent["status"] == "skipped"

    async def test_consent_checks_failing_to_run_are_inconclusive(self, db_client, auth_headers):
        site = await _site(db_client, auth_headers)
        with _scanner(_found(site["id"], site["domain"]), consent_down=True):
            resp = await db_client.post(
                f"/api/v1/sites/{site['id']}/installation/check", headers=auth_headers
            )

        assert resp.status_code == 200
        data = resp.json()
        [consent] = [c for c in data["last_check"]["checks"] if c["group"] == "consent"]
        assert consent["status"] == "inconclusive"
        assert data["last_check"]["ok"] is True

    async def test_scanner_unavailable(self, db_client, auth_headers):
        site = await _site(db_client, auth_headers)
        with _scanner(down=True):
            resp = await db_client.post(
                f"/api/v1/sites/{site['id']}/installation/check", headers=auth_headers
            )
        assert resp.status_code == 503
        assert "scanner" in resp.json()["detail"]

    async def test_other_organisations_cannot_check(
        self, db_client, auth_headers, other_org_headers
    ):
        site = await _site(db_client, auth_headers)
        resp = await db_client.post(
            f"/api/v1/sites/{site['id']}/installation/check", headers=other_org_headers
        )
        assert resp.status_code == 404


# ── Diagnosis ────────────────────────────────────────────────────────

SITE_ID = uuid.uuid4()
NOW = datetime(2026, 10, 18, tzinfo=UTC)


def _fake_site(*, published: bool = True):
    return SimpleNamespace(
        id=SITE_ID,
        domain="example.com",
        additional_domains=["example.org"],
        live_revision_id=uuid.uuid4() if published else None,
    )


def _diagnose(
    found: dict,
    *,
    published: bool = True,
    api_base: str | None = None,
    consent: list[dict] | None = None,
) -> dict:
    return diagnose(
        _fake_site(published=published),
        found,
        expected_api_base=api_base,
        checked_at=NOW,
        consent=consent,
    )


def _statuses(result: dict) -> dict[str, str]:
    return {c["key"]: c["status"] for c in result["checks"]}


def _fix(result: dict, key: str) -> str:
    return next(c["fix"] for c in result["checks"] if c["key"] == key)


class TestDiagnose:
    def test_everything_passes(self):
        result = _diagnose(
            _found(str(SITE_ID), "example.com"),
            api_base="https://cmp.example.net",
            consent=consent_check_items(CONSENT_PASSED),
        )
        assert result["ok"] is True
        assert set(_statuses(result).values()) == {"passed"}

    def test_reports_every_item(self):
        keys = list(_statuses(_diagnose(_found(str(SITE_ID), "example.com"))))
        assert keys == [
            "reachable",
            "registered_domain",
            "snippet_present",
            "site_id",
            "api_base",
            "synchronous",
            "first_in_head",
            "config_loaded",
            "banner_shown",
            "consent",
            "published",
        ]

    def test_an_unreachable_page_skips_the_rest(self):
        result = _diagnose(
            _found(str(SITE_ID), "example.com", reachable=False, error="net::ERR_NAME_NOT_RESOLVED")
        )
        statuses = _statuses(result)
        assert statuses["reachable"] == "failed"
        assert {statuses[k] for k in statuses if k not in ("reachable", "published")} == {"skipped"}
        assert result["ok"] is False

    def test_missing_snippet(self):
        result = _diagnose(
            _found(
                str(SITE_ID), "example.com", loader=None, config_requests=[], banner_rendered=False
            )
        )
        assert _statuses(result)["snippet_present"] == "failed"
        assert "Add the install snippet" in _fix(result, "snippet_present")
        assert _statuses(result)["config_loaded"] == "skipped"

    def test_a_loader_added_by_a_tag_manager_counts(self):
        result = _diagnose(_found(str(SITE_ID), "example.com", loader=None))
        assert _statuses(result)["snippet_present"] == "passed"
        assert result["ok"] is True

    def test_wrong_site_id(self):
        result = _diagnose(
            _found(str(SITE_ID), "example.com", loader=_loader("someone-elses-site"))
        )
        assert _statuses(result)["site_id"] == "failed"
        assert "someone-elses-site" in _fix(result, "site_id")

    def test_wrong_api_address_when_it_is_configured(self):
        result = _diagnose(_found(str(SITE_ID), "example.com"), api_base="https://api.example.net")
        assert _statuses(result)["api_base"] == "failed"
        assert "https://api.example.net" in _fix(result, "api_base")

    def test_api_address_is_not_judged_when_it_is_not_configured(self):
        assert _statuses(_diagnose(_found(str(SITE_ID), "example.com")))["api_base"] == "skipped"

    @pytest.mark.parametrize("attribute", ["is_async", "is_defer"])
    def test_loaded_asynchronously(self, attribute):
        result = _diagnose(
            _found(str(SITE_ID), "example.com", loader=_loader(str(SITE_ID), **{attribute: True}))
        )
        assert _statuses(result)["synchronous"] == "failed"

    def test_not_first_in_head(self):
        result = _diagnose(
            _found(str(SITE_ID), "example.com", loader=_loader(str(SITE_ID), first_script=False))
        )
        assert _statuses(result)["first_in_head"] == "failed"
        assert "above the other scripts" in _fix(result, "first_in_head")

    def test_not_in_head(self):
        result = _diagnose(
            _found(str(SITE_ID), "example.com", loader=_loader(str(SITE_ID), in_head=False))
        )
        assert "into the page's <head>" in _fix(result, "first_in_head")

    def test_unregistered_domain(self):
        result = _diagnose(
            _found(str(SITE_ID), "example.com", final_url="https://example-shop.myshopify.com/")
        )
        assert _statuses(result)["registered_domain"] == "failed"
        assert "example-shop.myshopify.com" in _fix(result, "registered_domain")

    def test_subdomains_and_additional_domains_are_registered(self):
        for url in ("https://www.example.com/", "https://shop.example.org/"):
            result = _diagnose(_found(str(SITE_ID), "example.com", final_url=url))
            assert _statuses(result)["registered_domain"] == "passed"

    def test_config_request_failed(self):
        result = _diagnose(
            _found(
                str(SITE_ID),
                "example.com",
                config_requests=[{"url": "https://cmp.example.net/geo-resolved", "status": None}],
                banner_rendered=False,
            )
        )
        assert _statuses(result)["config_loaded"] == "failed"
        assert "content security policy" in _fix(result, "config_loaded")

    def test_banner_not_shown(self):
        result = _diagnose(_found(str(SITE_ID), "example.com", banner_rendered=False))
        assert _statuses(result)["banner_shown"] == "failed"

    def test_unpublished_is_a_warning_not_a_failure(self):
        found = _found(
            str(SITE_ID),
            "example.com",
            config_requests=[{"url": "https://cmp.example.net/geo-resolved", "status": 404}],
        )
        result = _diagnose(found, published=False)
        assert _statuses(result)["published"] == "failed"
        assert _statuses(result)["config_loaded"] == "passed"
        assert result["ok"] is True


class TestInstallationState:
    def _installation(self, **values):
        defaults = {
            "last_seen_at": None,
            "last_check_at": None,
            "last_check": None,
        }
        return SimpleNamespace(**{**defaults, **values})

    def test_nothing_known(self):
        assert installation_state(None) == "not_detected"
        assert installation_state(self._installation()) == "not_detected"

    def test_seen(self):
        assert installation_state(self._installation(last_seen_at=NOW)) == "installed"

    def test_a_passing_check(self):
        installation = self._installation(last_check_at=NOW, last_check={"ok": True})
        assert installation_state(installation) == "installed"

    def test_a_failing_check(self):
        installation = self._installation(
            last_seen_at=NOW - timedelta(hours=1), last_check_at=NOW, last_check={"ok": False}
        )
        assert installation_state(installation) == "problem"

    def test_seen_again_after_a_failing_check(self):
        installation = self._installation(
            last_seen_at=NOW + timedelta(minutes=5), last_check_at=NOW, last_check={"ok": False}
        )
        assert installation_state(installation) == "installed"


def test_page_without_query():
    assert page_without_query("https://example.com/a/b?x=1#top") == "https://example.com/a/b"


class TestConsentChecks:
    def test_expectations_come_from_the_live_revision(self):
        snapshot = {
            "config": {
                "tcf_enabled": True,
                "gcm_enabled": False,
                "gpp_enabled": True,
                "gpp_supported_apis": ["usnat", "usca"],
                "gpc_enabled": True,
                "gpc_global_honour": True,
            },
            "cookies": [{"name": "_ga", "domain": ".example.com", "category": "analytics"}],
        }
        assert consent_expectations(snapshot) == {
            "tcf_enabled": True,
            "gcm_enabled": False,
            "gpp_enabled": True,
            "gpp_sections": ["usnat", "usca"],
            "gpc_enabled": True,
            "gpc_global_honour": True,
            "cookies": [{"name": "_ga", "category": "analytics"}],
        }

    def test_an_unpublished_site_expects_nothing(self):
        assert consent_expectations(None) == {
            "tcf_enabled": False,
            "gcm_enabled": False,
            "gpp_enabled": False,
            "gpp_sections": [],
            "gpc_enabled": False,
            "gpc_global_honour": False,
            "cookies": [],
        }

    def test_checks_that_do_not_apply_are_dropped(self):
        items = consent_check_items(CONSENT_PASSED)
        assert [i["key"] for i in items] == ["before_consent.cookies"]
        assert items[0]["group"] == "consent"

    def test_an_inconclusive_check_keeps_its_reason_but_no_fix(self):
        [item] = consent_check_items(
            {
                "checks": [
                    {
                        "key": "after_reject.saved",
                        "label": "Rejecting is remembered",
                        "status": "inconclusive",
                        "detail": "We couldn't find the banner's Reject button.",
                        "fix": "ignored",
                    }
                ]
            }
        )
        assert item["detail"] == "We couldn't find the banner's Reject button."
        assert item["fix"] is None

    def test_diagnosis_places_consent_checks_before_the_publish_warning(self):
        consent = consent_check_items(CONSENT_PASSED)
        result = diagnose(
            _fake_site(),
            _found(str(SITE_ID), "example.com"),
            expected_api_base=None,
            checked_at=NOW,
            consent=consent,
        )
        keys = [c["key"] for c in result["checks"]]
        assert keys[-2:] == ["before_consent.cookies", "published"]

    def test_without_consent_results_the_checks_show_as_skipped(self):
        result = _diagnose(_found(str(SITE_ID), "example.com"))
        [consent] = [c for c in result["checks"] if c["group"] == "consent"]
        assert consent["status"] == "skipped"

    def test_a_consent_warning_keeps_its_severity_and_does_not_fail_the_check(self):
        consent = consent_check_items(
            {
                "checks": [
                    {
                        "key": "before_consent.gcm_wait",
                        "label": "Google tags wait for the visitor's choice",
                        "status": "failed",
                        "severity": "warning",
                        "fix": "The Consent Mode default has no wait_for_update.",
                    }
                ]
            }
        )
        assert consent[0]["severity"] == "warning"
        result = _diagnose(
            _found(str(SITE_ID), "example.com"), api_base="https://cmp.example.net", consent=consent
        )
        assert result["ok"] is True
