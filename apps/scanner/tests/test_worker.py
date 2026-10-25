"""Tests for the scanner HTTP service."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.worker import create_app


@pytest.fixture
def client():
    """Create a test client for the scanner app."""
    app = create_app()
    return TestClient(app)


def test_health_endpoint(client):
    """Health endpoint returns ok."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@patch("src.sitemap.discover_urls", new_callable=AsyncMock)
@patch("src.crawler.CookieCrawler.crawl_site", new_callable=AsyncMock)
def test_scan_endpoint_with_domain(mock_crawl, mock_discover, client):
    """POST /scan with just a domain discovers URLs and crawls."""
    from src.crawler import CrawlResult, DiscoveredCookie, SiteCrawlResult

    mock_discover.return_value = ["https://example.com/"]
    mock_crawl.return_value = SiteCrawlResult(
        domain="example.com",
        pages=[
            CrawlResult(
                url="https://example.com/",
                cookies=[
                    DiscoveredCookie(
                        name="_ga",
                        domain=".example.com",
                        storage_type="cookie",
                        page_url="https://example.com/",
                        value_length=30,
                    ),
                    DiscoveredCookie(
                        name="session_id",
                        domain="example.com",
                        storage_type="cookie",
                        page_url="https://example.com/",
                        value_length=36,
                        http_only=True,
                        secure=True,
                    ),
                ],
            ),
        ],
        total_cookies_found=2,
    )

    resp = client.post("/scan", json={"domain": "example.com", "max_pages": 5})
    assert resp.status_code == 200
    data = resp.json()

    assert data["domain"] == "example.com"
    assert data["pages_crawled"] == 1
    assert data["total_cookies"] == 2
    assert len(data["cookies"]) == 2
    assert data["cookies"][0]["name"] == "_ga"
    assert data["cookies"][1]["name"] == "session_id"
    assert data["cookies"][1]["secure"] is True


@patch("src.crawler.CookieCrawler.crawl_site", new_callable=AsyncMock)
def test_scan_endpoint_with_urls(mock_crawl, client):
    """POST /scan with explicit URLs skips URL discovery."""
    from src.crawler import CrawlResult, SiteCrawlResult

    mock_crawl.return_value = SiteCrawlResult(
        domain="example.com",
        pages=[CrawlResult(url="https://example.com/about", cookies=[])],
        total_cookies_found=0,
    )

    resp = client.post(
        "/scan",
        json={
            "domain": "example.com",
            "urls": ["https://example.com/about"],
            "max_pages": 1,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["pages_crawled"] == 1
    assert data["cookies"] == []


@patch("src.sitemap.discover_urls", new_callable=AsyncMock)
@patch("src.crawler.CookieCrawler.crawl_site", new_callable=AsyncMock)
def test_scan_endpoint_with_errors(mock_crawl, mock_discover, client):
    """Scan results include page errors."""
    from src.crawler import CrawlResult, SiteCrawlResult

    mock_discover.return_value = ["https://example.com/"]
    mock_crawl.return_value = SiteCrawlResult(
        domain="example.com",
        pages=[
            CrawlResult(url="https://example.com/", cookies=[], error="Timeout"),
        ],
        total_cookies_found=0,
    )

    resp = client.post("/scan", json={"domain": "example.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["errors"] == ["Timeout"]


def test_scan_request_validation(client):
    """Missing domain returns 422."""
    resp = client.post("/scan", json={})
    assert resp.status_code == 422


@pytest.mark.parametrize("domain", ["https://example.com", "example.com/x", "a@127.0.0.1", ""])
def test_scan_rejects_invalid_domain(client, domain):
    """Domains must be bare hostnames."""
    resp = client.post("/scan", json={"domain": domain})
    assert resp.status_code == 400


@patch("src.crawler.CookieCrawler.crawl_site", new_callable=AsyncMock)
def test_scan_only_crawls_urls_on_site_domains(mock_crawl, client):
    """Explicit URLs outside the site's domains are dropped."""
    from src.crawler import SiteCrawlResult

    mock_crawl.return_value = SiteCrawlResult(domain="example.com")

    resp = client.post(
        "/scan",
        json={
            "domain": "www.example.com",
            "additional_domains": ["example.org"],
            "urls": [
                "https://example.com/a",
                "https://shop.example.org/b",
                "http://169.254.169.254/",
                "https://other.net/",
            ],
        },
    )
    assert resp.status_code == 200
    assert mock_crawl.call_args.args[0] == ["https://example.com/a", "https://shop.example.org/b"]


def test_scan_rejects_port_by_default(client):
    """Ports are only accepted when private networks are allowed."""
    resp = client.post("/scan", json={"domain": "localhost:3000"})
    assert resp.status_code == 400


def test_scan_accepts_port_when_private_networks_allowed(monkeypatch):
    """A local development server can be scanned by host and port."""
    from src.crawler import SiteCrawlResult

    monkeypatch.setenv("SCANNER_ALLOW_PRIVATE_NETWORKS", "true")
    with patch("src.crawler.CookieCrawler.crawl_site", new_callable=AsyncMock) as mock_crawl:
        mock_crawl.return_value = SiteCrawlResult(domain="localhost:3000")
        resp = TestClient(create_app()).post(
            "/scan",
            json={"domain": "localhost:3000", "urls": ["http://localhost:3000/about"]},
        )

    assert resp.status_code == 200
    assert mock_crawl.call_args.args[0] == ["http://localhost:3000/about"]


def test_scan_with_only_foreign_urls_is_rejected(client):
    """If no explicit URL is on the site's domains there is nothing to scan."""
    resp = client.post("/scan", json={"domain": "example.com", "urls": ["http://localhost:8080/"]})
    assert resp.status_code == 400


@pytest.mark.parametrize("opt_out", [False, True])
def test_scan_passes_navigation_setting_to_crawler(monkeypatch, opt_out):
    """SCANNER_ALLOW_PRIVATE_NETWORKS reaches the crawler's policy."""
    from src.crawler import SiteCrawlResult

    monkeypatch.setenv("SCANNER_ALLOW_PRIVATE_NETWORKS", str(opt_out).lower())
    captured = {}

    class FakeCrawler:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def crawl_site(self, urls, *, max_pages):
            return SiteCrawlResult(domain="example.com")

    with patch("src.crawler.CookieCrawler", FakeCrawler):
        resp = TestClient(create_app()).post(
            "/scan", json={"domain": "example.com", "urls": ["https://example.com/"]}
        )

    assert resp.status_code == 200
    assert captured["navigation_policy"].allow_private_networks is opt_out


def test_validate_rejects_non_http_url(client):
    """Only http(s) URLs can be validated."""
    resp = client.post("/validate", json={"url": "file:///tmp/page.html"})
    assert resp.status_code == 400


def _mock_playwright(context):
    """Build an ``async_playwright()`` stand-in yielding *context*.

    Returns the stand-in and the mock browser that creates *context*.
    """
    from unittest.mock import MagicMock

    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    pw = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=browser)
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=pw)
    manager.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=manager), browser


def test_validate_rejects_private_hosts(client):
    resp = client.post("/validate", json={"url": "http://127.0.0.1/"})
    assert resp.status_code == 400


@patch("src.navigation.NavigationPolicy.is_allowed", new_callable=AsyncMock, return_value=True)
@patch("src.consent_validator.run_consent_checks", new_callable=AsyncMock)
def test_validate_passes_expectations_and_returns_checks(mock_run, _allowed, client):
    from src.consent_validator import CheckResult, ValidationResult

    mock_run.return_value = ValidationResult(
        url="https://example.com/",
        checks=[
            CheckResult("after_accept.saved", "after_accept", "Accepting is remembered", "passed")
        ],
    )

    resp = client.post(
        "/validate",
        json={
            "url": "https://example.com/",
            "expect": {"tcf_enabled": True, "cookies": [{"name": "_ga", "category": "analytics"}]},
        },
    )

    assert resp.status_code == 200
    assert resp.json()["checks"][0]["status"] == "passed"
    expect = mock_run.await_args.args[1]
    assert expect.tcf_enabled is True
    assert expect.cookies[0].name == "_ga"


@pytest.mark.parametrize("opt_out", [False, True])
async def test_consent_checks_guard_navigation_and_record_from_the_start(monkeypatch, opt_out):
    """Each visit gets the route guard unless opted out, and the recorder script."""
    from unittest.mock import MagicMock

    from src.consent_validator import Expectations, run_consent_checks
    from src.navigation import NavigationPolicy

    page = AsyncMock()
    page.on = MagicMock()
    page.goto = AsyncMock(return_value=MagicMock(status=200))
    page.evaluate = AsyncMock(return_value={"consent": [], "tcf": None})
    locator = MagicMock()
    locator.first.is_visible = AsyncMock(return_value=True)
    locator.first.click = AsyncMock()
    page.locator = MagicMock(return_value=locator)
    context = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    context.cookies = AsyncMock(return_value=[])
    playwright_factory, browser = _mock_playwright(context)

    with patch("playwright.async_api.async_playwright", playwright_factory):
        result = await run_consent_checks(
            "https://example.com/",
            Expectations(),
            headless=True,
            timeout_ms=1_000,
            policy=NavigationPolicy(allow_private_networks=opt_out),
            settle_ms=0,
        )

    assert result.error is None
    assert browser.new_context.await_count == 2
    assert context.route.called is not opt_out
    assert context.add_init_script.await_count == 2
    assert browser.new_context.await_args.kwargs["service_workers"] == "block"
    statuses = {c.key: c.status for c in result.checks}
    assert statuses["after_accept.saved"] == "failed"


# ── Streamed scan ────────────────────────────────────────────────────


def _stream_events(resp) -> list[dict]:
    import json

    return [json.loads(line) for line in resp.text.splitlines() if line]


@patch("src.crawler.CookieCrawler.crawl_site")
def test_scan_stream_reports_progress_then_result(mock_crawl, client):
    """POST /scan/stream emits one progress line per page, then the result."""
    from src.crawler import CrawlResult, DiscoveredCookie, SiteCrawlResult

    urls = ["https://example.com/", "https://example.com/about"]
    ga = DiscoveredCookie(name="_ga", domain=".example.com", page_url=urls[0])

    async def crawl(urls, *, max_pages, on_page):
        result = SiteCrawlResult(domain="example.com")
        for url in urls:
            result.pages.append(CrawlResult(url=url, cookies=[ga]))
            result.total_cookies_found += 1
            await on_page(result, len(urls))
        return result

    mock_crawl.side_effect = crawl

    resp = client.post("/scan/stream", json={"domain": "example.com", "urls": urls})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")

    events = _stream_events(resp)
    assert events[:2] == [
        {"type": "progress", "pages_crawled": 1, "pages_total": 2, "cookies_found": 1},
        {"type": "progress", "pages_crawled": 2, "pages_total": 2, "cookies_found": 1},
    ]
    final = events[2]
    assert final["type"] == "result"
    assert final["pages_crawled"] == 2
    assert [c["name"] for c in final["cookies"]] == ["_ga"]
    assert len(events) == 3


@patch("src.crawler.CookieCrawler.crawl_site")
def test_scan_stream_reports_crawl_failure(mock_crawl, client):
    """A crash during the crawl ends the stream with an error line."""
    mock_crawl.side_effect = RuntimeError("browser crashed")

    resp = client.post(
        "/scan/stream",
        json={"domain": "example.com", "urls": ["https://example.com/"]},
    )
    assert resp.status_code == 200
    assert _stream_events(resp) == [{"type": "error", "detail": "browser crashed"}]


def test_scan_stream_rejects_invalid_domain_before_streaming(client):
    """Request errors are plain HTTP errors, not stream lines."""
    resp = client.post("/scan/stream", json={"domain": "not a domain"})
    assert resp.status_code == 400
