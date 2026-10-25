"""Scanner HTTP service.

Exposes an HTTP endpoint that accepts scan requests, runs the Playwright
cookie crawler, and returns discovered cookies. Called by the API's Celery
worker to execute scan jobs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from src.crawler import SiteCrawlResult

logger = logging.getLogger(__name__)


# ── Settings ─────────────────────────────────────────────────────────


class ScannerSettings(BaseSettings):
    """Scanner service settings from environment."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    host: str = "0.0.0.0"
    port: int = 8001
    log_level: str = "INFO"
    crawler_timeout_ms: int = 30_000
    crawler_headless: bool = True
    max_pages_per_scan: int = 50
    scanner_allow_private_networks: bool = False


# ── Request / Response schemas ───────────────────────────────────────


class ProxyRequest(BaseModel):
    """Proxy configuration for geo-located scanning."""

    server: str
    username: str | None = None
    password: str | None = None


class ScanRequest(BaseModel):
    """Incoming scan request from the API worker."""

    domain: str
    additional_domains: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    max_pages: int = 50
    proxy: ProxyRequest | None = None


class DiscoveredCookieResponse(BaseModel):
    """A single cookie found during crawling."""

    name: str
    domain: str
    storage_type: str = "cookie"
    path: str | None = None
    expires: float | None = None
    http_only: bool | None = None
    secure: bool | None = None
    same_site: str | None = None
    value_length: int = 0
    script_source: str | None = None
    page_url: str = ""
    initiator_chain: list[str] = Field(default_factory=list)


class ScanResponse(BaseModel):
    """Result of a scan."""

    domain: str
    pages_crawled: int
    total_cookies: int
    cookies: list[DiscoveredCookieResponse]
    errors: list[str] = Field(default_factory=list)


class ExpectedCookieRequest(BaseModel):
    name: str
    category: str | None = None


class ValidationExpectations(BaseModel):
    """What the site has switched on, from its live configuration."""

    tcf_enabled: bool = False
    cookies: list[ExpectedCookieRequest] = Field(default_factory=list)


class ValidationRequest(BaseModel):
    """Request to check a site's consent setup."""

    url: str
    expect: ValidationExpectations = Field(default_factory=ValidationExpectations)


class ConsentCheckResponse(BaseModel):
    key: str
    phase: Literal["before_consent", "after_accept", "after_reject"]
    label: str
    status: Literal["passed", "failed", "inconclusive", "not_applicable"]
    detail: str | None = None
    fix: str | None = None
    evidence: dict = Field(default_factory=dict)


class ValidationResponse(BaseModel):
    """The site's consent checks."""

    url: str
    checks: list[ConsentCheckResponse] = Field(default_factory=list)
    error: str | None = None


def build_scan_response(result: SiteCrawlResult) -> ScanResponse:
    """Turn a crawl result into the ``/scan`` response body."""
    cookies = [
        DiscoveredCookieResponse(
            name=c.name,
            domain=c.domain,
            storage_type=c.storage_type,
            path=c.path,
            expires=c.expires,
            http_only=c.http_only,
            secure=c.secure,
            same_site=c.same_site,
            value_length=c.value_length,
            script_source=c.script_source,
            page_url=c.page_url,
            initiator_chain=c.initiator_chain,
        )
        for c in result.unique_cookies
    ]
    return ScanResponse(
        domain=result.domain,
        pages_crawled=len(result.pages),
        total_cookies=result.total_cookies_found,
        cookies=cookies,
        errors=[p.error for p in result.pages if p.error],
    )


class CheckInstallRequest(BaseModel):
    """Load a page and report how the banner is installed on it."""

    url: str


class LoaderTagResponse(BaseModel):
    src: str
    site_id: str | None
    api_base: str | None
    cdn_base: str | None
    is_async: bool
    is_defer: bool
    in_head: bool
    first_script: bool


class ConfigRequestResponse(BaseModel):
    url: str
    status: int | None


class CheckInstallResponse(BaseModel):
    url: str
    final_url: str | None = None
    reachable: bool
    error: str | None = None
    loader: LoaderTagResponse | None = None
    config_requests: list[ConfigRequestResponse] = Field(default_factory=list)
    banner_rendered: bool


# ── Application ──────────────────────────────────────────────────────


def create_app():  # noqa: ANN201
    """Create the scanner FastAPI application."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import StreamingResponse

    from src.crawler import CookieCrawler
    from src.navigation import (
        ALLOWED_SCHEMES,
        NavigationPolicy,
        filter_site_urls,
        is_valid_hostname,
        normalise_domain,
        site_domains,
    )
    from src.sitemap import discover_urls

    app = FastAPI(title="CMP Scanner Service", version="0.1.0")
    settings = ScannerSettings()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async def _prepare_scan(body: ScanRequest) -> tuple[list[str], CookieCrawler, int]:
        """Validate a scan request and work out what to crawl and how."""
        if not is_valid_hostname(body.domain, allow_port=settings.scanner_allow_private_networks):
            raise HTTPException(status_code=400, detail="Invalid domain")
        domain = normalise_domain(body.domain)
        domains = site_domains(domain, body.additional_domains)
        policy = NavigationPolicy(allow_private_networks=settings.scanner_allow_private_networks)
        max_pages = min(body.max_pages, settings.max_pages_per_scan)

        # Discover URLs if none provided
        urls = filter_site_urls(body.urls, domains)
        if not body.urls:
            try:
                urls = await discover_urls(
                    domain,
                    max_urls=max_pages,
                    additional_domains=body.additional_domains,
                    policy=policy,
                )
            except Exception as exc:
                logger.warning("URL discovery failed for %s: %s", domain, exc)
                urls = [f"https://{domain}/"]

        if not urls:
            raise HTTPException(status_code=400, detail="No URLs to scan")

        from src.crawler import ProxyConfig

        proxy_config = None
        if body.proxy:
            proxy_config = ProxyConfig(
                server=body.proxy.server,
                username=body.proxy.username,
                password=body.proxy.password,
            )

        crawler = CookieCrawler(
            headless=settings.crawler_headless,
            timeout_ms=settings.crawler_timeout_ms,
            proxy=proxy_config,
            navigation_policy=policy,
        )
        return urls, crawler, max_pages

    @app.post("/scan", response_model=ScanResponse)
    async def run_scan(body: ScanRequest) -> ScanResponse:
        """Execute a Playwright crawl and return discovered cookies."""
        urls, crawler, max_pages = await _prepare_scan(body)
        result = await crawler.crawl_site(urls, max_pages=max_pages)
        return build_scan_response(result)

    @app.post("/scan/stream")
    async def run_scan_stream(body: ScanRequest) -> StreamingResponse:
        """Execute a crawl, reporting progress as newline-delimited JSON.

        Each crawled page produces a ``progress`` line. The last line is
        either ``result``, carrying the same body as ``/scan``, or
        ``error``. Request errors are still returned as HTTP errors,
        because they are found before streaming starts.
        """
        urls, crawler, max_pages = await _prepare_scan(body)
        events: asyncio.Queue[dict | None] = asyncio.Queue()

        async def on_page(partial: SiteCrawlResult, pages_total: int) -> None:
            await events.put(
                {
                    "type": "progress",
                    "pages_crawled": len(partial.pages),
                    "pages_total": pages_total,
                    "cookies_found": len(partial.unique_cookies),
                }
            )

        async def crawl() -> None:
            try:
                result = await crawler.crawl_site(urls, max_pages=max_pages, on_page=on_page)
                await events.put(
                    {"type": "result", **build_scan_response(result).model_dump(mode="json")}
                )
            except Exception as exc:
                logger.exception("Streamed scan failed for %s", body.domain)
                await events.put({"type": "error", "detail": str(exc)})
            finally:
                await events.put(None)

        async def lines() -> AsyncIterator[str]:
            task = asyncio.create_task(crawl())
            try:
                while (event := await events.get()) is not None:
                    yield json.dumps(event) + "\n"
            finally:
                # The client went away mid-scan: stop crawling for it.
                if not task.done():
                    task.cancel()

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @app.post("/check-install", response_model=CheckInstallResponse)
    async def run_check_install(body: CheckInstallRequest) -> CheckInstallResponse:
        """Load a page as a first-time visitor and report on the banner."""
        from dataclasses import asdict

        from src.install_check import check_install

        if urlparse(body.url).scheme not in ALLOWED_SCHEMES:
            raise HTTPException(status_code=400, detail="URL must use http or https")
        policy = NavigationPolicy(allow_private_networks=settings.scanner_allow_private_networks)
        if not await policy.is_allowed(body.url):
            raise HTTPException(status_code=400, detail="URL is not permitted")

        result = await check_install(
            body.url,
            headless=settings.crawler_headless,
            timeout_ms=settings.crawler_timeout_ms,
            policy=policy,
        )
        return CheckInstallResponse.model_validate(asdict(result))

    @app.post("/validate", response_model=ValidationResponse)
    async def run_validation(body: ValidationRequest) -> ValidationResponse:
        """Visit a page as a new visitor, accept and reject, and check what happens."""
        from dataclasses import asdict

        from src.consent_validator import Expectations, ExpectedCookie, run_consent_checks

        if urlparse(body.url).scheme not in ALLOWED_SCHEMES:
            raise HTTPException(status_code=400, detail="URL must use http or https")
        policy = NavigationPolicy(allow_private_networks=settings.scanner_allow_private_networks)
        if not await policy.is_allowed(body.url):
            raise HTTPException(status_code=400, detail="URL is not permitted")

        expect = Expectations(
            tcf_enabled=body.expect.tcf_enabled,
            cookies=[ExpectedCookie(c.name, c.category) for c in body.expect.cookies],
        )
        result = await run_consent_checks(
            body.url,
            expect,
            headless=settings.crawler_headless,
            timeout_ms=settings.crawler_timeout_ms,
            policy=policy,
        )
        return ValidationResponse.model_validate(asdict(result))

    return app


# ── Entrypoint ───────────────────────────────────────────────────────


def main() -> None:
    """Run the scanner service with uvicorn."""
    import uvicorn

    settings = ScannerSettings()
    logging.basicConfig(level=settings.log_level)

    uvicorn.run(
        "src.worker:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        workers=1,  # Single worker — Playwright manages its own concurrency
        access_log=True,
    )


if __name__ == "__main__":
    main()
