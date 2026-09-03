#!/usr/bin/env python3
"""Small requests-compatible HTTP shim built only on Python stdlib."""

import json as _json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


class RequestException(Exception):
    """Base HTTP request exception."""


class Timeout(RequestException):
    """Request timed out."""


class ConnectionError(RequestException):
    """Connection failed."""


class HTTPError(RequestException):
    """HTTP status error."""

    def __init__(self, message: str, response: Optional["Response"] = None):
        super().__init__(message)
        self.response = response


class _Exceptions:
    RequestException = RequestException
    Timeout = Timeout
    ConnectionError = ConnectionError
    HTTPError = HTTPError


exceptions = _Exceptions()

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


class Response:
    def __init__(self, status_code: int, headers: Any, content: bytes, url: str):
        self.status_code = status_code
        self.headers = dict(headers.items()) if hasattr(headers, "items") else dict(headers or {})
        self.content = content
        self.url = url

    @property
    def text(self) -> str:
        content_type = self.headers.get("Content-Type", "")
        charset = "utf-8"
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                charset = part.split("=", 1)[1].strip() or "utf-8"
        return self.content.decode(charset, errors="replace")

    def json(self) -> Any:
        return _json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(f"{self.status_code} Error", response=self)


def _with_params(url: str, params: dict[str, Any] | None) -> str:
    if not params:
        return url
    query = urllib.parse.urlencode(params, doseq=True)
    separator = "&" if urllib.parse.urlparse(url).query else "?"
    return f"{url}{separator}{query}"


def request(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: Any | None = None,
    timeout: int | None = None,
) -> Response:
    method = method.upper()
    headers = {**DEFAULT_HEADERS, **dict(headers or {})}
    data = None
    if json is not None:
        data = _json.dumps(json, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    full_url = _with_params(url, params)
    req = urllib.request.Request(full_url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    try:
        with opener.open(req, timeout=timeout) as res:
            return Response(res.getcode(), res.headers, res.read(), full_url)
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.headers, exc.read(), full_url)
    except TimeoutError as exc:
        raise Timeout(f"Request timed out after {timeout} seconds") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise Timeout(f"Request timed out after {timeout} seconds") from exc
        raise ConnectionError(str(reason)) from exc
    except OSError as exc:
        raise ConnectionError(str(exc)) from exc


def get(url: str, **kwargs: Any) -> Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs: Any) -> Response:
    return request("POST", url, **kwargs)
