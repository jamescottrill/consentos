"""Tests for the action check extension point (neutral fixtures)."""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from src.extensions.registry import get_registry
from src.services.action_checks import ActionRefusedError
from src.services.scanner import ScanInProgressError
from tests.conftest import create_test_site, requires_db

REASON = "Not allowed here"


@pytest.fixture
def checks():
    """Register action checks for one test and remove them afterwards."""
    registry = get_registry()
    added: list = []

    def register(fn):
        registry.action_checks.append(fn)
        added.append(fn)
        return fn

    yield register
    for fn in added:
        registry.action_checks.remove(fn)


def refuse(action: str, seen: list | None = None):
    async def check(db, ctx):
        if seen is not None:
            seen.append(ctx)
        return REASON if ctx.action == action else None

    return check


def assert_refused(resp) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": REASON, "code": "action_refused"}


@requires_db
class TestActionChecks:
    async def test_site_create_can_be_refused(self, db_client, auth_headers, checks):
        seen: list = []
        checks(refuse("site.create", seen))
        resp = await db_client.post(
            "/api/v1/sites/",
            headers=auth_headers,
            json={"domain": "refused.test", "display_name": "R"},
        )
        assert_refused(resp)
        assert {c.action for c in seen} == {"site.write", "site.create"}

    async def test_site_writes_are_checked_and_reads_are_not(self, db_client, auth_headers, checks):
        site_id = await create_test_site(db_client, auth_headers, start_scan=False)
        seen: list = []
        checks(refuse("site.write", seen))

        assert (await db_client.get(f"/api/v1/sites/{site_id}", headers=auth_headers)).is_success
        assert seen == []

        resp = await db_client.patch(
            f"/api/v1/sites/{site_id}/config", headers=auth_headers, json={"terms_url": "x"}
        )
        assert_refused(resp)
        assert seen[0].site_id == uuid.UUID(site_id)
        assert seen[0].method == "PATCH"

        resp = await db_client.post(
            f"/api/v1/sites/{site_id}/translations/",
            headers=auth_headers,
            json={"locale": "fr", "strings": {"title": "Bonjour"}},
        )
        assert_refused(resp)

    async def test_site_cookie_writes_are_checked(self, db_client, auth_headers, checks):
        site_id = await create_test_site(db_client, auth_headers, start_scan=False)
        checks(refuse("site.write"))
        base = f"/api/v1/cookies/sites/{site_id}"
        cookie = {"name": "_x", "domain": "x.test"}
        entry = {"name_pattern": "_x", "domain_pattern": "x.test", "category_id": str(uuid.uuid4())}
        assert_refused(await db_client.post(base, headers=auth_headers, json=cookie))
        assert_refused(await db_client.post(f"{base}/allow-list", headers=auth_headers, json=entry))
        assert_refused(await db_client.post(f"{base}/classify", headers=auth_headers))

    async def test_group_and_org_config_writes_are_checked(self, db_client, auth_headers, checks):
        checks(refuse("site_group.write"))
        checks(refuse("org_config.write"))
        resp = await db_client.post(
            "/api/v1/site-groups/", headers=auth_headers, json={"name": "G"}
        )
        assert_refused(resp)
        assert_refused(await db_client.put("/api/v1/org-config/", headers=auth_headers, json={}))

    async def test_a_scan_can_be_refused(self, db_client, auth_headers, checks):
        site_id = await create_test_site(db_client, auth_headers, start_scan=False)
        checks(refuse("scan.start"))
        with patch("src.tasks.scanner.run_scan.delay") as delay:
            resp = await db_client.post(
                "/api/v1/scanner/scans", headers=auth_headers, json={"site_id": site_id}
            )
        assert_refused(resp)
        delay.assert_not_called()

    async def test_a_refused_first_scan_still_creates_the_site(
        self, db_client, auth_headers, checks
    ):
        checks(refuse("scan.start"))
        with patch("src.tasks.scanner.run_scan.delay") as delay:
            await create_test_site(db_client, auth_headers, start_scan=True)
        delay.assert_not_called()

    async def test_concurrent_site_creates_are_checked_one_at_a_time(
        self, db_client, auth_headers, checks
    ):
        active = 0
        peak = 0

        async def slow(db, ctx):
            nonlocal active, peak
            if ctx.action == "site.create":
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.2)
                active -= 1
            return None

        checks(slow)
        await asyncio.gather(
            create_test_site(db_client, auth_headers, start_scan=False),
            create_test_site(db_client, auth_headers, start_scan=False),
        )
        assert peak == 1

    async def test_concurrent_scan_starts_are_checked_one_at_a_time(
        self, db_client, auth_headers, checks
    ):
        sites = [await create_test_site(db_client, auth_headers, start_scan=False) for _ in "ab"]
        active = 0
        peak = 0

        async def slow(db, ctx):
            nonlocal active, peak
            if ctx.action == "scan.start":
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.2)
                active -= 1
            return None

        checks(slow)
        with patch("src.tasks.scanner.run_scan.delay"):
            responses = await asyncio.gather(
                *(
                    db_client.post(
                        "/api/v1/scanner/scans", headers=auth_headers, json={"site_id": site}
                    )
                    for site in sites
                )
            )
        assert [r.status_code for r in responses] == [201, 201]
        assert peak == 1

    async def test_another_organisations_site_is_never_checked(
        self, db_client, auth_headers, other_org_headers, checks
    ):
        site_id = await create_test_site(db_client, other_org_headers, start_scan=False)
        seen: list = []
        checks(refuse("site.write", seen))
        resp = await db_client.patch(
            f"/api/v1/sites/{site_id}/config",
            headers=auth_headers,
            json={"terms_url": "https://x.test/t"},
        )
        assert resp.status_code == 404
        assert seen == []

    async def test_visitor_endpoints_are_never_checked(self, db_client, auth_headers, checks):
        site_id = await create_test_site(db_client, auth_headers, start_scan=False)
        seen: list = []
        checks(refuse("none", seen))
        await db_client.get(f"/api/v1/config/sites/{site_id}/geo-resolved")
        await db_client.get(f"/api/v1/translations/{site_id}/en")
        await db_client.get("/api/v1/cookies/categories")
        assert seen == []


@pytest.mark.parametrize("error", [ActionRefusedError(REASON), ScanInProgressError()])
async def test_skipped_scheduled_scans_release_their_lock(error):
    from src.tasks.scanner import check_scheduled_scans

    site = type("Site", (), {"id": uuid.uuid4()})()

    async def due(db):
        return [site]

    async def refused(*args, **kwargs):
        raise error

    rollback = AsyncMock()

    with (
        patch("src.services.scanner.get_sites_due_for_scan", due),
        patch("src.services.scanner.configured_max_pages", AsyncMock(return_value=50)),
        patch("src.services.scanner.start_scan", refused),
        patch("sqlalchemy.ext.asyncio.AsyncSession.rollback", rollback),
    ):
        # The task runs its own event loop, so keep it off this one.
        assert await asyncio.to_thread(check_scheduled_scans) == {
            "sites_checked": 1,
            "scans_triggered": 0,
        }
    rollback.assert_awaited_once()
