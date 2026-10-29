"""A session wraps a transport with the MCP handshake and helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .client import (
    DEFAULT_PROTOCOL_VERSION,
    ProtocolError,
    Response,
    TransportError,
    make_transport,
)


class SessionError(Exception):
    """The handshake failed, so nothing downstream can be trusted."""


SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)


class Session:
    def __init__(
        self,
        command: Optional[str] = None,
        url: Optional[str] = None,
        timeout: float = 15.0,
        headers: Optional[Dict[str, str]] = None,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
    ) -> None:
        self.transport = make_transport(command=command, url=url, timeout=timeout, headers=headers)
        self.timeout = timeout
        self.requested_version = protocol_version
        self.init_result: Dict[str, Any] = {}
        self.capabilities: Dict[str, Any] = {}
        self.server_info: Dict[str, Any] = {}
        self.protocol_version: Optional[str] = None
        self.stray_after_notification: List[str] = []
        self._tools: Optional[List[Dict[str, Any]]] = None

    def open(self) -> "Session":
        try:
            self.transport.start()
            response = self.transport.request(
                "initialize",
                {
                    "protocolVersion": self.requested_version,
                    "capabilities": {},
                    "clientInfo": {"name": "mcpcheck", "version": "0.1.0"},
                },
            )
        except (TransportError, ProtocolError) as error:
            self.transport.close()
            raise SessionError(str(error))
        if response.is_error:
            self.transport.close()
            raise SessionError("initialize returned an error: %s" % response.error)
        self.init_result = response.result or {}
        capabilities = self.init_result.get("capabilities")
        self.capabilities = capabilities if isinstance(capabilities, dict) else {}
        info = self.init_result.get("serverInfo")
        self.server_info = info if isinstance(info, dict) else {}
        version = self.init_result.get("protocolVersion")
        self.protocol_version = version if isinstance(version, str) else None
        try:
            self.notify("notifications/initialized")
        except (TransportError, ProtocolError):
            pass
        return self

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Response:
        return self.transport.request(method, params)

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Response:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    @property
    def tools(self) -> List[Dict[str, Any]]:
        if self._tools is not None:
            return self._tools
        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        for _ in range(20):
            params: Dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            response = self.request("tools/list", params)
            if response.is_error:
                break
            result = response.result or {}
            page = result.get("tools")
            if isinstance(page, list):
                tools.extend(item for item in page if isinstance(item, dict))
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        self._tools = tools
        return tools

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a notification and record any reply the server should not send."""
        self.transport.notify(method, params)
        stray = self.transport.drain(0.05)
        if stray:
            self.stray_after_notification.extend(stray)

    def ping(self) -> bool:
        """Send a ping and wait for its reply."""
        if not self.transport.alive():
            return False
        try:
            response = self.transport.request("ping")
        except (TransportError, ProtocolError):
            return False
        return not response.is_error

    def alive(self) -> bool:
        return self.transport.alive()

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> "Session":
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()
