"""Tool execution engine for declarative (Agent Studio) agents.

Two kinds, both governed:

* ``mock`` — returns the configured response sample. Deterministic, safe,
  and honest about being a demo (the response is authored, not fetched).
* ``http`` — performs a real outbound call at execution time, behind an
  SSRF guard: private/loopback/link-local targets are refused, the URL must
  match the tool's declared host, and responses are capped in size and time.

Every execution is expected to be audited by the caller (the declarative
executor reports ``tool.executed`` to the gateway's chain).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

MAX_RESPONSE_BYTES = 16 * 1024
TIMEOUT_SECONDS = 5.0


@dataclass
class ToolResult:
    ok: bool
    output: str
    tool: str
    ms: int = 0


def _resolve_allows(host: str) -> None:
    """Refuse names that resolve (or point) into private/loopback/reserved
    space — the SSRF guard for http tools."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve host {host!r}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(f"host {host!r} resolves to a non-public address ({ip})")


def execute_tool(tool: dict[str, Any], text: str = "") -> ToolResult:
    """Execute a registry tool definition. Never raises; failures are results."""
    import time

    name = tool.get("name", "?")
    kind = tool.get("kind", "mock")
    config = tool.get("config") or {}
    t0 = time.perf_counter()

    if kind == "mock":
        response = str(config.get("response", ""))
        # {input} template substitution, capped — mock outputs can echo intent
        out = response.replace("{input}", text[:200]) if "{input}" in response else response
        return ToolResult(ok=True, output=out, tool=name, ms=round((time.perf_counter() - t0) * 1000))

    if kind == "http":
        try:
            url = str(config.get("url", ""))
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                return ToolResult(ok=False, output="invalid tool url", tool=name)
            _resolve_allows(parsed.hostname)
            with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False) as http:
                r = http.request(config.get("method", "GET"), url)
            body = r.text[:MAX_RESPONSE_BYTES]
            return ToolResult(
                ok=r.status_code < 400,
                output=f"HTTP {r.status_code} · {body[:600]}",
                tool=name,
                ms=round((time.perf_counter() - t0) * 1000),
            )
        except ValueError as exc:
            return ToolResult(ok=False, output=f"refused: {exc}", tool=name)
        except Exception as exc:
            return ToolResult(ok=False, output=f"error: {exc}", tool=name)

    return ToolResult(ok=False, output=f"unknown kind {kind!r}", tool=name)
