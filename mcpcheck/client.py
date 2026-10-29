"""A minimal MCP client, used to drive conformance and fuzz checks.

Two transports are implemented with the standard library only:

- stdio: newline-delimited JSON-RPC over a child process
- streamable HTTP: one POST per message, JSON or event-stream responses

The client is deliberately strict. When a server sends something that is not
valid JSON-RPC, the client records the raw line and raises :class:`ProtocolError`
instead of guessing, because a conformance tool that silently repairs broken
messages is not measuring anything.
"""

from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib import error as urlerror
from urllib import request as urlrequest

DEFAULT_PROTOCOL_VERSION = "2025-06-18"
DEFAULT_TIMEOUT = 15.0


class ProtocolError(Exception):
    """The server sent something that is not valid JSON-RPC 2.0."""


class TransportError(Exception):
    """The server could not be reached, or died."""


@dataclass
class Response:
    raw: Dict[str, Any]
    id: Any = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    elapsed_ms: float = 0.0

    @property
    def is_error(self) -> bool:
        return self.error is not None

    @property
    def error_code(self) -> Optional[int]:
        if not isinstance(self.error, dict):
            return None
        code = self.error.get("code")
        return code if isinstance(code, int) else None


def _split_command(command: str) -> List[str]:
    """Split a command string, keeping quoted paths intact on every platform."""
    parts = shlex.split(command, posix=False)
    cleaned = []
    for part in parts:
        if len(part) >= 2 and part[0] == part[-1] and part[0] in ("'", '"'):
            part = part[1:-1]
        cleaned.append(part)
    return cleaned


class StdioTransport:
    """A child process speaking newline-delimited JSON-RPC."""

    def __init__(self, command: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.command = command
        self.timeout = timeout
        self.process: Optional[subprocess.Popen] = None
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stderr: List[str] = []
        self._next_id = 0
        self.last_request_id: Any = None
        self.raw_lines: List[str] = []

    def start(self) -> None:
        argv = _split_command(self.command)
        if not argv:
            raise TransportError("empty server command")
        try:
            self.process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as error:
            raise TransportError("could not start %r: %s" % (self.command, error))
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self._queue.put(line)
        except Exception:
            pass
        finally:
            self._queue.put(None)

    def _pump_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        try:
            for line in self.process.stderr:
                line = line.rstrip()
                if line:
                    self._stderr.append(line)
                    if len(self._stderr) > 200:
                        del self._stderr[:100]
        except Exception:
            pass

    def write_raw(self, text: str) -> None:
        if self.process is None or self.process.stdin is None:
            raise TransportError("server is not running")
        try:
            self.process.stdin.write(text + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise TransportError("server closed its input: %s" % error)

    def read_raw(self, timeout: Optional[float] = None) -> str:
        try:
            line = self._queue.get(timeout=timeout or self.timeout)
        except queue.Empty:
            raise TransportError("no response within %.1fs" % (timeout or self.timeout))
        if line is None:
            tail = "\n".join(self._stderr[-5:])
            raise TransportError(
                "server exited%s" % ((": " + tail) if tail else "")
            )
        self.raw_lines.append(line.rstrip("\n"))
        return line

    def send(self, payload: Dict[str, Any]) -> Response:
        started = time.monotonic()
        self.write_raw(json.dumps(payload, ensure_ascii=False))
        line = self.read_raw()
        elapsed = (time.monotonic() - started) * 1000
        return parse_response(line, payload.get("id"), elapsed)

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self.write_raw(json.dumps(payload, ensure_ascii=False))

    def drain(self, timeout: float = 0.05) -> List[str]:
        """Collect any lines already buffered, without waiting long."""
        lines: List[str] = []
        while True:
            try:
                lines.append(self.read_raw(timeout=timeout))
            except TransportError:
                break
        return lines

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Response:
        self._next_id += 1
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.last_request_id = self._next_id
        return self.send(payload)

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def close(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.stdin:
                self.process.stdin.close()
        except Exception:
            pass
        try:
            self.process.terminate()
            self.process.wait(timeout=3)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        for stream in (self.process.stdout, self.process.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass
        self.process = None

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr[-10:])


class HTTPTransport:
    """Streamable HTTP: one POST per message."""

    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None, timeout: float = DEFAULT_TIMEOUT):
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self._next_id = 0
        self.last_request_id: Any = None
        self.raw_lines: List[str] = []

    def start(self) -> None:
        return None

    def _post(self, payload: Dict[str, Any]) -> str:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "mcpcheck",
        }
        headers.update(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urlrequest.Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urlrequest.urlopen(request, timeout=self.timeout) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self.session_id = session
                return response.read().decode("utf-8", "replace")
        except urlerror.HTTPError as error:
            body = error.read().decode("utf-8", "replace")
            # A JSON-RPC error may legitimately arrive with a 4xx status.
            if body.strip().startswith("{"):
                return body
            raise TransportError("HTTP %s: %s" % (error.code, body[:200]))
        except (urlerror.URLError, OSError) as error:
            raise TransportError("unreachable: %s" % error)

    def send(self, payload: Dict[str, Any]) -> Response:
        started = time.monotonic()
        body = self._post(payload)
        elapsed = (time.monotonic() - started) * 1000
        message = _message_from_body(body)
        self.raw_lines.append(body)
        return parse_message(message, payload.get("id"), elapsed)

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            self._post(payload)
        except TransportError:
            pass

    def drain(self, timeout: float = 0.05) -> List[str]:
        return []

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Response:
        self._next_id += 1
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.last_request_id = self._next_id
        return self.send(payload)

    def alive(self) -> bool:
        try:
            self.request("ping")
            return True
        except (TransportError, ProtocolError):
            return False

    def close(self) -> None:
        return None

    @property
    def stderr_tail(self) -> str:
        return ""


def _message_from_body(body: str) -> Dict[str, Any]:
    text = body.strip()
    if not text:
        raise ProtocolError("empty response body")
    if text.startswith("{"):
        try:
            message = json.loads(text)
        except ValueError as error:
            raise ProtocolError("invalid JSON: %s" % error)
        if not isinstance(message, dict):
            raise ProtocolError("response is not a JSON object")
        return message
    # event-stream
    for line in text.splitlines():
        if line.startswith("data:"):
            raw = line[5:].strip()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if isinstance(message, dict) and ("result" in message or "error" in message):
                return message
    raise ProtocolError("no JSON-RPC message in the event stream")


def parse_response(line: str, request_id: Any, elapsed_ms: float = 0.0) -> Response:
    text = line.strip()
    if not text:
        raise ProtocolError("empty response line")
    try:
        message = json.loads(text)
    except ValueError as error:
        raise ProtocolError("invalid JSON: %s (raw: %r)" % (error, text[:200]))
    if not isinstance(message, dict):
        raise ProtocolError("response is not a JSON object")
    return parse_message(message, request_id, elapsed_ms)


def parse_message(message: Dict[str, Any], request_id: Any, elapsed_ms: float = 0.0) -> Response:
    response = Response(raw=message, id=message.get("id"), elapsed_ms=elapsed_ms)
    if message.get("jsonrpc") != "2.0":
        raise ProtocolError("missing or wrong jsonrpc version: %r" % message.get("jsonrpc"))
    if "result" in message and "error" in message:
        raise ProtocolError("response has both result and error")
    if "result" in message:
        result = message["result"]
        if not isinstance(result, dict):
            raise ProtocolError("result must be an object, got %r" % type(result).__name__)
        response.result = result
    elif "error" in message:
        error = message["error"]
        if not isinstance(error, dict):
            raise ProtocolError("error must be an object, got %r" % type(error).__name__)
        response.error = error
    else:
        raise ProtocolError("response has neither result nor error")
    return response


def make_transport(
    command: Optional[str] = None,
    url: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    headers: Optional[Dict[str, str]] = None,
):
    if command and url:
        raise TransportError("choose either --stdio or --url, not both")
    if command:
        return StdioTransport(command, timeout=timeout)
    if url:
        return HTTPTransport(url, headers=headers, timeout=timeout)
    raise TransportError("one of --stdio or --url is required")
