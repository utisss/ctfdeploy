import httpx


class CtfdError(Exception):
    pass


class Ctfd:
    """CTFd's admin REST API, over a unix socket (`unix:/run/ctfd/ctfd.sock`) or HTTP."""

    def __init__(self, url: str, token: str):
        if url.startswith("unix:"):
            transport, base = httpx.HTTPTransport(uds=url.removeprefix("unix:")), "http://ctfd"
        else:
            transport, base = httpx.HTTPTransport(), url.rstrip("/")
        self.http = httpx.Client(
            base_url=f"{base}/api/v1",
            transport=transport,
            headers={"Authorization": f"Token {token}"},
            timeout=60,
        )

    def get(self, path: str):
        return self._call("GET", path)

    def post(self, path: str, json: dict | None = None, **kwargs):
        return self._call("POST", path, json=json, **kwargs)

    def patch(self, path: str, json: dict):
        return self._call("PATCH", path, json=json)

    def delete(self, path: str) -> None:
        self._call("DELETE", path)

    def _call(self, method: str, path: str, **kwargs):
        # CTFd only accepts tokens on JSON requests; multipart sets its own content type.
        headers = {} if "files" in kwargs else {"Content-Type": "application/json"}
        try:
            response = self.http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as e:
            raise CtfdError(f"{method} {path}: {e}") from e
        body = response.json() if "json" in response.headers.get("content-type", "") else {}
        if response.is_error or body.get("success") is False:
            detail = body.get("errors") or body.get("message") or response.reason_phrase
            raise CtfdError(f"{method} {path}: {response.status_code} {detail}")
        return body.get("data")
