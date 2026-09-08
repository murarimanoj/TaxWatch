from dataclasses import dataclass
from time import monotonic
from urllib.parse import urljoin

from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright
from playwright.sync_api import Response as PlaywrightResponse
from playwright_stealth import Stealth


@dataclass(frozen=True)
class BrowserResponse:
    content: bytes
    headers: dict[str, str]
    status_code: int
    url: str

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class BrowserClient:
    """Lazy, reusable Chromium client for JavaScript/WAF-sensitive sources."""

    def __init__(
        self,
        timeout_seconds: float,
        user_agent: str,
        headless: bool = False,
        channel: str = "chrome",
        popup_timeout_seconds: float = 90,
    ) -> None:
        self._timeout_ms = timeout_seconds * 1000
        self._popup_timeout_ms = popup_timeout_seconds * 1000
        self._user_agent = user_agent
        self._headless = headless
        self._channel = channel
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    def _start(self) -> BrowserContext:
        if self._context is not None:
            return self._context
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            channel=self._channel,
            headless=self._headless,
        )
        self._context = self._browser.new_context(
            user_agent=self._user_agent,
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            viewport={"width": 1440, "height": 900},
            java_script_enabled=True,
        )
        Stealth().apply_stealth_sync(self._context)
        self._context.set_default_timeout(self._timeout_ms)
        self._context.set_default_navigation_timeout(self._timeout_ms)
        return self._context

    def get(self, url: str) -> BrowserResponse:
        page = self._start().new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded")
            if response is None:
                raise RuntimeError(f"Browser navigation returned no response for {url}")
            page.wait_for_timeout(1500)
            headers = {key.lower(): value for key, value in response.headers.items()}
            content_type = headers.get("content-type", "")
            if response.status >= 400:
                raise RuntimeError(f"Browser request returned HTTP {response.status} for {url}")
            if "application/pdf" in content_type:
                # Chrome renders PDFs inside its extension, so the navigation body is
                # viewer HTML. Reuse the browser context (and its cookies) for raw bytes.
                download = page.context.request.get(url)
                if not download.ok:
                    raise RuntimeError(
                        f"Browser download returned HTTP {download.status} for {url}"
                    )
                headers = {key.lower(): value for key, value in download.headers.items()}
                body = download.body()
            else:
                body = page.content().encode()
            return BrowserResponse(body, headers, response.status, page.url)
        finally:
            page.close()

    def download(self, url: str, referer: str) -> BrowserResponse:
        """Download bytes using the active browser session and source referrer."""
        response = self._start().request.get(
            url,
            headers={"Referer": referer},
            timeout=self._timeout_ms,
        )
        if not response.ok:
            raise RuntimeError(
                f"Browser download returned HTTP {response.status} for {url}"
            )
        return BrowserResponse(
            content=response.body(),
            headers={
                key.lower(): value for key, value in response.headers.items()
            },
            status_code=response.status,
            url=response.url,
        )

    def get_with_popup_links(
        self, url: str, button_selector: str, limit: int
    ) -> BrowserResponse:
        """Render a listing and attach popup destinations to link-like buttons."""
        page = self._start().new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded")
            if response is None:
                raise RuntimeError(f"Browser navigation returned no response for {url}")
            if response.status >= 400:
                raise RuntimeError(f"Browser request returned HTTP {response.status} for {url}")
            page.wait_for_timeout(1500)
            buttons = page.locator(button_selector)
            for index in range(min(buttons.count(), limit)):
                button = buttons.nth(index)
                try:
                    with page.context.expect_page(
                        timeout=self._popup_timeout_ms
                    ) as popup_info:
                        button.click()
                    popup = popup_info.value
                    popup.wait_for_load_state("domcontentloaded")
                    destination = popup.url
                    popup.close()
                    button.evaluate(
                        "(element, href) => element.setAttribute('data-document-url', href)",
                        destination,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"Could not resolve document link for listing item {index + 1}"
                    ) from exc
            headers = {key.lower(): value for key, value in response.headers.items()}
            return BrowserResponse(page.content().encode(), headers, response.status, page.url)
        finally:
            page.close()

    def capture_response(
        self,
        page_url: str,
        response_url_contains: str,
    ) -> BrowserResponse:
        """Capture a dynamic response before its page redirects or redraws."""
        page = self._start().new_page()
        captured: list[BrowserResponse] = []

        def capture(response: PlaywrightResponse) -> None:
            if response_url_contains not in response.url or captured:
                return
            captured.append(
                BrowserResponse(
                    content=response.body(),
                    headers={
                        key.lower(): value
                        for key, value in response.headers.items()
                    },
                    status_code=response.status,
                    url=response.url,
                )
            )

        page.on("response", capture)
        try:
            response = page.goto(page_url, wait_until="domcontentloaded")
            if response is None:
                raise RuntimeError(
                    f"Browser navigation returned no response for {page_url}"
                )
            deadline = monotonic() + (self._timeout_ms / 1000)
            while not captured and monotonic() < deadline:
                page.wait_for_timeout(250)
            if not captured:
                raise RuntimeError(
                    f"Did not receive a response containing "
                    f"{response_url_contains!r} from {page_url}"
                )
            return captured[0]
        finally:
            page.close()

    def get_page_resource(
        self,
        page_url: str,
        resource_path: str,
    ) -> BrowserResponse:
        """Fetch a same-origin resource after opening its configured page."""
        page = self._start().new_page()
        resource_url = urljoin(page_url, resource_path)
        try:
            response = page.goto(page_url, wait_until="domcontentloaded")
            if response is None:
                raise RuntimeError(
                    f"Browser navigation returned no response for {page_url}"
                )
            result = page.evaluate(
                """
                async (url) => {
                    const response = await fetch(url, {credentials: "include"});
                    return {
                        body: await response.text(),
                        headers: Object.fromEntries(response.headers.entries()),
                        status: response.status,
                        url: response.url,
                    };
                }
                """,
                resource_url,
            )
            if not isinstance(result, dict):
                raise TypeError(
                    f"Browser returned an invalid response for {resource_url}"
                )
            status = int(result.get("status", 0))
            if status >= 400:
                raise RuntimeError(
                    f"Browser request returned HTTP {status} for {resource_url}"
                )
            headers = {
                str(key).lower(): str(value)
                for key, value in dict(result.get("headers", {})).items()
            }
            return BrowserResponse(
                content=str(result.get("body", "")).encode(),
                headers=headers,
                status_code=status,
                url=str(result.get("url", resource_url)),
            )
        finally:
            page.close()

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._context = None
        self._browser = None
        self._playwright = None
