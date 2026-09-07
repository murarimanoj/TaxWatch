import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class HttpClient:
    def __init__(self, timeout: float, user_agent: str) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept": "text/html,application/pdf,*/*"},
        )

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def get(self, url: str) -> httpx.Response:
        response = self._client.get(url)
        response.raise_for_status()
        return response

    def close(self) -> None:
        self._client.close()

