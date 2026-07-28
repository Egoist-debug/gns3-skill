#!/usr/bin/env python3
"""
Telnet Client for GNS3 Console Access
Provides robust telnet connection to network device consoles.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_READY_TIMEOUT = 30.0
DEFAULT_MAX_RESPONSE_BYTES = 512 * 1024
_PAGER_RE = re.compile(r"(?i)--\s*more\s*--")
_ANSI_RE = re.compile(
    # Order matters: multi-byte CSI/OSC/DCS before single-byte Fe.
    # Fe alone would match ESC] and leave residues like "0;title".
    r"(?:"
    r"\x1B\["          # CSI
    r"[0-?]*"          # parameter bytes
    r"[ -/]*"          # intermediate bytes
    r"[@-~]"           # final byte
    r"|"
    r"\x1B\]"          # OSC
    r"[^\x07\x1B]*"    # payload
    r"(?:\x07|\x1B\\)" # BEL or ST
    r"|"
    r"\x1B[PX^_]"      # DCS / SOS / PM / APC
    r".*?"
    r"(?:\x1B\\|\x07)"
    r"|"
    r"\x1B[()][0-2AB]" # SCS character-set designate
    r"|"
    r"\x1B[=>]"        # DECKPAM / DECKPNM (keypad)
    r"|"
    r"\x1B[@-Z\\-_]"   # remaining 7-bit C1 Fe
    r"|"
    r"\x9B"            # 8-bit CSI
    r"[0-?]*[ -/]*[@-~]"
    r")"
    ,
    re.DOTALL,
)
_LOGIN_MARKERS = ("Username:", "username:", "login:", "Login:", "Password:", "password:")
_USER_MARKERS = ("Username:", "username:", "login:", "Login:")
_PASS_MARKERS = ("Password:", "password:")
_SHELL_ENDINGS = (">", "#", "$", "%")
_IAC = 255


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def default_ready_timeout() -> float:
    return _env_float("GNS3_CONSOLE_READY_TIMEOUT", DEFAULT_READY_TIMEOUT)


def default_max_response_bytes() -> int:
    return max(1024, _env_int("GNS3_CONSOLE_MAX_RESPONSE_BYTES", DEFAULT_MAX_RESPONSE_BYTES))


def strip_ansi(text: str) -> str:
    """Remove ANSI/VT escape sequences (CSI/OSC/DCS/Fe)."""
    if not text:
        return ""
    return _ANSI_RE.sub("", text)


def clean_console_text(text: str) -> str:
    """Normalize console text for agent consumption.

    Strips Telnet IAC noise, full ANSI/VT escape sequences (not just ESC),
    pager chrome, and remaining C0 controls while preserving printable text.
    """
    if not text:
        return ""
    # Drop basic Telnet IAC command sequences (IAC + cmd [+ option]).
    raw = text.encode("utf-8", errors="ignore")
    out = bytearray()
    i = 0
    while i < len(raw):
        b = raw[i]
        if b == _IAC and i + 1 < len(raw):
            cmd = raw[i + 1]
            if cmd == _IAC:
                out.append(_IAC)
                i += 2
                continue
            # WILL/WONT/DO/DONT + option
            if cmd in (251, 252, 253, 254) and i + 2 < len(raw):
                i += 3
                continue
            # SB ... IAC SE (best-effort skip)
            if cmd == 250:
                i += 2
                while i < len(raw):
                    if raw[i] == _IAC and i + 1 < len(raw) and raw[i + 1] == 240:
                        i += 2
                        break
                    i += 1
                continue
            i += 2
            continue
        out.append(b)
        i += 1
    text = out.decode("utf-8", errors="ignore")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Remove full ANSI/VT sequences while ESC is still present.
    # Dropping only ESC (later C0 filter) leaves residues like "[01;36m".
    text = strip_ansi(text)
    # Strip pager chrome lines/fragments.
    text = _PAGER_RE.sub("", text)
    cleaned_chars: List[str] = []
    for ch in text:
        o = ord(ch)
        if ch in ("\n", "\t") or 32 <= o <= 126 or o >= 160:
            cleaned_chars.append(ch)
        # drop other C0 controls (including leftover lone ESC)
    text = "".join(cleaned_chars)
    # Collapse runs of spaces before newlines lightly.
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text


def shape_command_response(text: str, cmd: Optional[str] = None) -> str:
    """Trim leading command echo and trailing completion prompt.

    Devices echo the typed command before emitting output and a completion
    prompt. Agents already have ``command`` in the result entry. Public
    ``response`` is command body only; completion is reported via meta
    ``completed``, not by echoing the prompt string or username.
    """
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Drop leading empty / prompt-only / command-echo lines.
    cmd_norm = (cmd or "").strip()
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if looks_like_shell_prompt(s):
            i += 1
            continue
        if cmd_norm and s == cmd_norm:
            i += 1
            break
        # Some consoles emit the local echo immediately after the prompt
        # (for example, ``R1#show version``) rather than on its own line.
        # Require an actual recognized prompt prefix so body lines that merely
        # contain the command remain intact.
        if cmd_norm and s.endswith(cmd_norm):
            prompt = s[: -len(cmd_norm)]
            if prompt and looks_like_shell_prompt(prompt):
                i += 1
                break
        # Local-echo can re-print the command with trailing spaces.
        if cmd_norm and s.rstrip() == cmd_norm:
            i += 1
            break
        break
    body = lines[i:]
    # Trim trailing empties, then strip the completion prompt line.
    while body and not body[-1].strip():
        body.pop()
    if body and looks_like_shell_prompt(body[-1]):
        body.pop()
        while body and not body[-1].strip():
            body.pop()
    return "\n".join(body)


def _last_nonempty_line(text: str) -> str:
    if not text:
        return ""
    for line in reversed(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        if line.strip():
            return line.strip()
    return ""


def is_pager_line(line: str) -> bool:
    return bool(_PAGER_RE.search(line or ""))


def is_login_line(line: str) -> bool:
    low = (line or "").lower()
    return any(m.lower() in low for m in _LOGIN_MARKERS) or (line or "").rstrip().endswith(":")


def looks_like_shell_prompt(line: str, extra_markers: Optional[List[str]] = None) -> bool:
    """True if a single line looks like a CLI shell/config prompt.

    Real prompts are short tokens without spaces (``R1#``, ``R1(config-if)#``,
    ``user@host$``). Free text that merely ends with ``#``/``>`` is not a prompt.

    ANSI color on the prompt (common on SONiC/Linux) is stripped before matching
    so framing still completes on the first prompt line.
    """
    line = strip_ansi(line or "").strip()
    if not line or len(line) > 80:
        return False
    if is_pager_line(line):
        return False
    if any(m.lower() in line.lower() for m in _LOGIN_MARKERS):
        return False
    if line.startswith("!"):
        return False
    # Password/username prompts end with ':' — not shell.
    if line.lower().endswith(("password:", "username:", "login:")):
        return False

    # Multi-character markers (e.g. "(config)#") may match on the last line.
    if extra_markers:
        for m in extra_markers:
            if not m or len(m) <= 1:
                continue
            if m in line and (line.endswith(m) or line.endswith(tuple(_SHELL_ENDINGS))):
                # Still reject prose containing the marker mid-sentence with spaces
                # unless the whole line is a compact prompt token.
                if " " not in line and "\t" not in line:
                    return True

    if " " in line or "\t" in line:
        return False

    if line.endswith(_SHELL_ENDINGS):
        return True

    return False


def prompt_complete(buf: str, wait_for: Optional[List[str]] = None) -> bool:
    """True when buffer contains at least one shell/config prompt line."""
    return split_at_first_prompt(buf, wait_for=wait_for) is not None


def split_at_first_prompt(
    buf: str, wait_for: Optional[List[str]] = None
) -> Optional[Tuple[str, str]]:
    """Split buffer at the first shell/config prompt line.

    Returns ``(through_prompt, remainder)`` or ``None`` if no prompt yet.
    Completion is first-prompt (not last-line) so multi-command bleed cannot
    be returned as a single response. Prompt detection strips ANSI so colored
    SONiC/Linux prompts still frame correctly.
    """
    if not buf:
        return None

    norm = buf.replace("\r\n", "\n").replace("\r", "\n")
    lines = norm.split("\n")
    acc: List[str] = []
    for i, line in enumerate(lines):
        acc.append(line)
        # Detect on ANSI-stripped content; keep original bytes in the split.
        if line.strip() and looks_like_shell_prompt(line, extra_markers=wait_for):
            complete = "\n".join(acc)
            if i < len(lines) - 1:
                complete += "\n"
            remainder = "\n".join(lines[i + 1 :])
            return complete, remainder
    return None


def apply_response_cap(text: str, max_bytes: Optional[int] = None) -> Tuple[str, Dict[str, Any]]:
    """Cap cleaned text by UTF-8 byte length. Returns (text, meta)."""
    cap = max_bytes if max_bytes is not None else default_max_response_bytes()
    raw = text.encode("utf-8", errors="ignore")
    meta: Dict[str, Any] = {
        "truncated": False,
        "response_bytes": len(raw),
        "response_bytes_raw": len(raw),
    }
    if len(raw) <= cap:
        return text, meta
    head = raw[:cap]
    # Avoid splitting multi-byte char at end.
    while head and (head[-1] & 0xC0) == 0x80:
        head = head[:-1]
    clipped = head.decode("utf-8", errors="ignore")
    meta["truncated"] = True
    meta["response_bytes"] = len(head)
    meta["response_bytes_raw"] = len(raw)
    return clipped, meta


class TelnetClient:
    """Enhanced Telnet client for GNS3 console interaction."""

    def __init__(self, host: str, port: int, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.connected = False
        self.max_response_bytes = default_max_response_bytes()
        # Unconsumed console bytes after first-prompt framing. Never invent data:
        # only socket reads and split remainders land here.
        self._rx_buf: str = ""

    def connect(self) -> bool:
        """Connect to the Telnet server."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect((self.host, self.port))
            self.connected = True
            self._rx_buf = ""
            logger.info(f"Telnet connected to {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Telnet connection failed to {self.host}:{self.port}: {e}")
            self.connected = False
            self._rx_buf = ""
            return False

    def close(self):
        """Close the connection."""
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
            self.connected = False
            self._rx_buf = ""
            logger.info(f"Telnet connection closed to {self.host}:{self.port}")

    def _recv_chunk(self, size: int = 4096) -> str:
        if not self.sock or not self.connected:
            return ""
        try:
            data = self.sock.recv(size)
            if not data:
                return ""
            return data.decode("utf-8", errors="ignore")
        except socket.timeout:
            return ""
        except Exception as e:
            logger.error(f"Read error: {e}")
            return ""

    def _push_rx(self, data: str) -> None:
        if data:
            self._rx_buf += data
            # Bound residual growth similarly to read_until hard stop.
            cap = max(self.max_response_bytes * 2, 1024)
            raw = self._rx_buf.encode("utf-8", errors="ignore")
            if len(raw) > cap:
                self._rx_buf = raw[-cap:].decode("utf-8", errors="ignore")

    def _take_rx(self) -> str:
        data = self._rx_buf
        self._rx_buf = ""
        return data

    def _rx_is_prompt_only(self, wait_for: Optional[List[str]] = None) -> bool:
        """True if residual is empty/whitespace or only shell prompt line(s)."""
        text = self._rx_buf
        if not text or not text.strip():
            return True
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            s = line.strip()
            if not s:
                continue
            if not looks_like_shell_prompt(s, extra_markers=wait_for):
                return False
        return True

    def _strip_leading_prompts(self, wait_for: Optional[List[str]] = None) -> None:
        """Drop leading shell-prompt lines from residual; keep body lines.

        Leftover ``R1#`` after boot/login must not early-complete the next
        command. Body that already arrived (rare / scripted) is preserved.
        """
        if not self._rx_buf:
            return
        norm = self._rx_buf.replace("\r\n", "\n").replace("\r", "\n")
        lines = norm.split("\n")
        i = 0
        while i < len(lines):
            s = lines[i].strip()
            if not s:
                i += 1
                continue
            if looks_like_shell_prompt(s, extra_markers=wait_for):
                i += 1
                continue
            break
        self._rx_buf = "\n".join(lines[i:])

    def _drain_idle_prompts(
        self,
        wait_for: Optional[List[str]] = None,
        budget: float = 0.5,
        idle_gap: float = 0.05,
    ) -> None:
        """Absorb brief leftover prompt noise; strip leading residual prompts.

        Stops after the first non-empty absorption (prompt-only clear or
        non-prompt body) so a following command's scripted/queued output is
        not pulled before send. Idle gap only applies while still empty.
        """
        if not self.sock or not self.connected:
            return

        # Residual may already hold a leftover prompt from boot/login.
        if self._rx_buf:
            if self._rx_is_prompt_only(wait_for):
                self._rx_buf = ""
                return
            self._strip_leading_prompts(wait_for)
            return

        deadline = time.time() + max(0.05, budget)
        last_data = time.time()
        try:
            while time.time() < deadline:
                self.sock.settimeout(min(idle_gap, max(0.02, deadline - time.time())))
                chunk = self._recv_chunk()
                if chunk:
                    self._push_rx(chunk)
                    if self._rx_is_prompt_only(wait_for):
                        self._rx_buf = ""
                    else:
                        self._strip_leading_prompts(wait_for)
                    return
                if (time.time() - last_data) >= idle_gap:
                    break
        except Exception as e:
            logger.error(f"Pre-command drain error: {e}")
        finally:
            try:
                self.sock.settimeout(self.timeout)
            except Exception:
                pass

    def read_until(
        self,
        valid_end_chars: Optional[List[str]] = None,
        timeout: Optional[float] = None,
        *,
        line_oriented: bool = True,
        handle_pager: bool = True,
        max_bytes: Optional[int] = None,
        return_framed: bool = False,
    ):
        """Read until first shell/config prompt (line-oriented) or timeout.

        Line-oriented mode completes on the **first** prompt line and stashes
        any remainder in the residual RX buffer for subsequent reads. This
        keeps multi-command output paired with the correct command.

        When ``return_framed=True``, returns ``(text, framed)`` where ``framed``
        is True only if line-oriented ``split_at_first_prompt`` succeeded
        (not timeout / partial buffer).
        """
        text, framed = self._read_until_result(
            valid_end_chars,
            timeout,
            line_oriented=line_oriented,
            handle_pager=handle_pager,
            max_bytes=max_bytes,
        )
        return (text, framed) if return_framed else text

    def _read_until_result(
        self,
        valid_end_chars: Optional[List[str]] = None,
        timeout: Optional[float] = None,
        *,
        line_oriented: bool = True,
        handle_pager: bool = True,
        max_bytes: Optional[int] = None,
    ) -> Tuple[str, bool]:
        """Read until first prompt; ``framed`` is True only on a real split."""
        if not self.sock or not self.connected:
            return "", False

        timeout = timeout if timeout is not None else self.timeout
        cap = max_bytes if max_bytes is not None else self.max_response_bytes
        start_time = time.time()
        buf = self._take_rx()
        markers = list(valid_end_chars or list(_SHELL_ENDINGS))
        hard_cap = cap * 2

        def _trim(buf_in: str) -> str:
            raw = buf_in.encode("utf-8", errors="ignore")
            if len(raw) <= hard_cap:
                return buf_in
            return raw[:hard_cap].decode("utf-8", errors="ignore")

        # Residual alone may already contain a complete framed response.
        if line_oriented:
            split = split_at_first_prompt(buf, wait_for=markers)
            if split is not None:
                complete, remainder = split
                self._push_rx(remainder)
                return complete, True
        else:
            for end_char in markers:
                if end_char and end_char in buf:
                    # Marker match without first-prompt split is not framed.
                    return buf, False

        while time.time() - start_time < timeout:
            try:
                self.sock.settimeout(min(0.5, max(0.05, timeout)))
                chunk = self._recv_chunk()
                if chunk:
                    buf += chunk
                    buf = _trim(buf)

                last = _last_nonempty_line(buf)
                if handle_pager and is_pager_line(last):
                    try:
                        self.sock.send(b" ")
                    except Exception as e:
                        logger.error(f"Pager advance failed: {e}")
                        break
                    time.sleep(0.05)
                    continue

                if line_oriented:
                    split = split_at_first_prompt(buf, wait_for=markers)
                    if split is not None:
                        complete, remainder = split
                        self._push_rx(remainder)
                        return complete, True
                else:
                    for end_char in markers:
                        if end_char and end_char in buf:
                            return buf, False

                if not chunk:
                    time.sleep(0.05)
            except Exception as e:
                logger.error(f"Read error: {e}")
                break

        # Timeout / partial: no first-prompt split.
        return buf, False

    def read_available(self, wait_time: float = 0.5, idle_gap: float = 0.2) -> str:
        """Read available data until idle gap after initial wait."""
        if not self.sock or not self.connected:
            return ""

        time.sleep(wait_time)
        buf = self._take_rx()
        deadline = time.time() + max(wait_time * 4, 2.0)
        last_data = time.time()
        try:
            while time.time() < deadline:
                self.sock.settimeout(idle_gap)
                chunk = self._recv_chunk()
                if chunk:
                    buf += chunk
                    last_data = time.time()
                    if is_pager_line(_last_nonempty_line(buf)):
                        try:
                            self.sock.send(b" ")
                        except Exception:
                            pass
                    continue
                if buf and (time.time() - last_data) >= idle_gap:
                    break
                if not buf and (time.time() - last_data) >= wait_time:
                    break
        except Exception as e:
            logger.error(f"Read error: {e}")
        finally:
            try:
                self.sock.settimeout(self.timeout)
            except Exception:
                pass
        return buf

    def _finalize_output(
        self,
        raw: str,
        cmd: Optional[str] = None,
        *,
        shape: bool = True,
        framed: bool = False,
    ) -> Tuple[str, Dict[str, Any]]:
        cleaned = clean_console_text(raw)
        # ``framed`` means first-prompt split succeeded (not merely that
        # read_until was called). Idle reads and timeouts stay completed=False
        # even if cleaned body happens to end with a prompt-looking line.
        completed = bool(framed)
        if shape:
            cleaned = shape_command_response(cleaned, cmd=cmd)
        text, meta = apply_response_cap(cleaned, self.max_response_bytes)
        meta["completed"] = completed
        return text, meta

    def send_cmd(
        self,
        cmd: str,
        wait_for: Optional[List[str]] = None,
        wait_time: float = 0.5,
        timeout: Optional[float] = None,
        return_meta: bool = False,
        shape_response: bool = True,
    ):
        """Send a command and wait for response.

        By default the returned text is shaped to remove the command echo and
        trailing completion prompt. Meta ``completed`` is True only when
        ``wait_for`` was set **and** first-prompt framing split succeeded.
        Internal control paths may set ``shape_response=False`` when they
        inspect raw prompt markers.
        Returns cleaned text, or (text, meta) when return_meta=True.
        """
        if not self.sock or not self.connected:
            logger.error("Not connected")
            empty_meta = {
                "truncated": False,
                "response_bytes": 0,
                "response_bytes_raw": 0,
                "completed": False,
            }
            return ("", empty_meta) if return_meta else ""

        try:
            markers = list(wait_for) if wait_for is not None else list(_SHELL_ENDINGS)
            self._drain_idle_prompts(wait_for=markers)

            full_cmd = f"{cmd}\r"
            self.sock.send(full_cmd.encode())
            logger.debug(f"Sent command: {cmd}")

            if wait_for is not None:
                raw, framed = self.read_until(
                    wait_for,
                    timeout=timeout or self.timeout,
                    return_framed=True,
                )
            else:
                raw = self.read_available(wait_time)
                framed = False

            text, meta = self._finalize_output(
                raw, cmd=cmd, shape=shape_response, framed=framed
            )
            return (text, meta) if return_meta else text
        except Exception as e:
            logger.error(f"Send error: {e}")
            empty_meta = {
                "truncated": False,
                "response_bytes": 0,
                "response_bytes_raw": 0,
                "completed": False,
            }
            return ("", empty_meta) if return_meta else ""

    def wait_for_boot(
        self,
        timeout: int = 120,
        additional_prompts: Optional[List[str]] = None,
        accept_login_prompts: bool = True,
    ) -> bool:
        """Wait for device to boot and show a prompt or login screen."""
        if not self.sock or not self.connected:
            return False

        start_time = time.time()
        prompts = [
            ">",
            "#",
            "PC1>",
            "PC2>",
            "PC3>",
            "PC4>",
            "Laptop1>",
            "Laptop2>",
            "Router>",
            "Router#",
            "Switch>",
            "Switch#",
            "$",
            "%",
            "[yes/no]:",
            "[confirm]",
        ]
        if accept_login_prompts:
            prompts = prompts + list(_LOGIN_MARKERS)
        if additional_prompts:
            prompts.extend(additional_prompts)

        while time.time() - start_time < timeout:
            try:
                self.sock.send(b"\r")
                res = self.read_until(
                    prompts,
                    timeout=2,
                    line_oriented=False,
                    handle_pager=False,
                )
                lower = res.lower()

                if "[yes/no]:" in res or "yes/no" in lower:
                    logger.info("Initial Config Dialog detected. Sending 'no'.")
                    self.sock.send(b"no\r")
                    time.sleep(5)
                    continue

                if "[confirm]" in res:
                    logger.info("Confirm prompt detected. Sending enter.")
                    self.sock.send(b"\r")
                    time.sleep(2)
                    continue

                if accept_login_prompts and any(
                    m.lower() in lower for m in ("username:", "login:", "password:")
                ):
                    logger.info("Login prompt detected during boot wait")
                    return True

                last = _last_nonempty_line(res)
                if looks_like_shell_prompt(last) or any(
                    p in res for p in prompts if p not in ("[yes/no]:", "[confirm]")
                ):
                    # Prefer line-oriented shell detection when available.
                    if looks_like_shell_prompt(last) or any(
                        m.lower() in lower for m in ("username:", "login:", "password:")
                    ):
                        logger.info("Device prompt detected, boot complete")
                        return True
                    if last and any(last.endswith(p) for p in _SHELL_ENDINGS):
                        logger.info("Device prompt detected, boot complete")
                        return True

                time.sleep(1)
            except Exception as e:
                logger.error(f"Error waiting for boot: {e}")
                time.sleep(1)
                continue

        logger.error(f"Timeout waiting for device boot after {timeout}s")
        return False

    def login(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: Optional[float] = None,
        ready_timeout: Optional[float] = None,
        settle: float = 1.0,
    ) -> bool:
        """Best-effort console login with readiness retries.

        Retries when no stable login/shell markers appear yet.
        Hard-fails when credentials are rejected (still at login after password).
        Never logs credentials.
        """
        if not self.sock or not self.connected:
            return False

        budget = (
            ready_timeout
            if ready_timeout is not None
            else (timeout if timeout is not None else default_ready_timeout())
        )
        deadline = time.time() + max(0.5, float(budget))
        if settle > 0:
            time.sleep(min(settle, max(0.0, deadline - time.time())))

        shell_markers = list(_SHELL_ENDINGS)
        user_markers = list(_USER_MARKERS)
        pass_markers = list(_PASS_MARKERS)
        any_markers = user_markers + pass_markers + shell_markers

        def _has_shell(text: str) -> bool:
            last = _last_nonempty_line(text)
            return looks_like_shell_prompt(last)

        def _at_login(text: str) -> bool:
            return any(m in (text or "") for m in user_markers + pass_markers)

        attempt = 0
        backoff = 0.5
        try:
            while time.time() < deadline:
                attempt += 1
                remaining = max(0.2, deadline - time.time())
                try:
                    self.sock.send(b"\r")
                except Exception as e:
                    logger.error("Console login nudge failed: %s", e)
                    return False

                buf = self.read_until(
                    any_markers,
                    timeout=min(5.0, remaining),
                    line_oriented=False,
                    handle_pager=False,
                )

                if _has_shell(buf) and not _at_login(buf):
                    logger.info("Console already at shell prompt; login not required")
                    return True

                # No markers yet → readiness wait
                if not _at_login(buf) and not _has_shell(buf):
                    sleep_for = min(backoff, max(0.0, deadline - time.time()))
                    if sleep_for <= 0:
                        break
                    time.sleep(sleep_for)
                    backoff = min(backoff * 2, 4.0)
                    continue

                if any(m in buf for m in user_markers):
                    if not username:
                        logger.error("Login username required but not provided")
                        return False
                    logger.debug("Sending console username")
                    self.sock.send(f"{username}\r".encode())
                    buf = self.read_until(
                        pass_markers + shell_markers,
                        timeout=min(5.0, max(0.2, deadline - time.time())),
                        line_oriented=False,
                        handle_pager=False,
                    )

                if any(m in buf for m in pass_markers):
                    if password is None:
                        logger.error("Login password required but not provided")
                        return False
                    logger.debug("Sending console password")
                    self.sock.send(f"{password}\r".encode())
                    buf = self.read_until(
                        shell_markers + user_markers + pass_markers,
                        timeout=min(5.0, max(0.2, deadline - time.time())),
                        line_oriented=False,
                        handle_pager=False,
                    )

                    # Hard auth failure: still at login after password.
                    if _at_login(buf) and not _has_shell(buf):
                        logger.error("Console authentication failed")
                        return False

                if _has_shell(buf):
                    logger.info("Console login completed")
                    return True

                # Some devices need a second enter after auth
                self.sock.send(b"\r")
                buf = self.read_until(
                    shell_markers + user_markers + pass_markers,
                    timeout=min(3.0, max(0.2, deadline - time.time())),
                    line_oriented=False,
                    handle_pager=False,
                )

                if _at_login(buf) and not _has_shell(buf):
                    logger.error("Console authentication failed")
                    return False

                if _has_shell(buf):
                    logger.info("Console login completed")
                    return True

                if not _at_login(buf):
                    # Soft success: no login markers remaining.
                    logger.info("Console login completed (no login prompt remaining)")
                    return True

                sleep_for = min(backoff, max(0.0, deadline - time.time()))
                if sleep_for <= 0:
                    break
                time.sleep(sleep_for)
                backoff = min(backoff * 2, 4.0)

            logger.error("Console login timed out or failed after %s attempts", attempt)
            return False
        except Exception as e:
            logger.error("Console login error: %s", e)
            return False

    def enter_config_mode(self, enable_password: Optional[str] = None) -> bool:
        """Enter privileged and configuration mode (Cisco-style)."""
        if not self.connected:
            return False

        try:
            output = self.send_cmd(
                "enable",
                wait_for=[">", "#", "Password:"],
                wait_time=1.0,
                shape_response=False,
            )

            if "Password:" in output:
                if not enable_password:
                    logger.error("Enable password required but not provided")
                    return False
                self.send_cmd(
                    enable_password,
                    wait_for=["#"],
                    wait_time=1.0,
                    shape_response=False,
                )

            output = self.send_cmd(
                "configure terminal",
                wait_for=["(config)#"],
                wait_time=1.0,
                shape_response=False,
            )

            if "(config)" in output:
                return True

            return False
        except Exception as e:
            logger.error(f"Failed to enter config mode: {e}")
            return False

    def exit_config_mode(self) -> bool:
        """Exit configuration mode."""
        if not self.connected:
            return False

        try:
            self.send_cmd("end", wait_for=["#"], wait_time=1.0, shape_response=False)
            return True
        except Exception as e:
            logger.error(f"Failed to exit config mode: {e}")
            return False

    def save_config(self, confirm: bool = True) -> str:
        """Save running configuration (Cisco-style)."""
        if not self.connected:
            return ""

        try:
            output = self.send_cmd(
                "write memory",
                wait_for=["#", "[OK]", "[confirm]"],
                wait_time=2.0,
                shape_response=False,
            )

            if "[confirm]" in output and confirm:
                output += self.send_cmd(
                    "", wait_for=["#", "[OK]"], wait_time=2.0, shape_response=False
                )

            return output
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return ""

    def send_config_commands(
        self,
        commands: List[str],
        enter_config: bool = True,
        save_config: bool = False,
        enable_password: Optional[str] = None,
        return_meta: bool = False,
    ):
        """Send multiple configuration commands in sequence."""
        if not self.connected:
            return []

        outputs = []
        metas: List[Dict[str, Any]] = []

        try:
            if enter_config:
                if not self.enter_config_mode(enable_password):
                    logger.error("Failed to enter config mode")
                    return [] if not return_meta else ([], [])

            prompts = [
                "#",
                "(config)#",
                "(config-if)#",
                "(config-router)#",
                "(config-line)#",
            ]

            for cmd in commands:
                text, meta = self.send_cmd(
                    cmd, wait_for=prompts, wait_time=1.0, return_meta=True
                )
                outputs.append(text)
                metas.append(meta)

            if enter_config:
                self.exit_config_mode()

            if save_config:
                save_output = self.save_config()
                outputs.append(save_output)
                metas.append(
                    {
                        "truncated": False,
                        "response_bytes": len(save_output.encode("utf-8")),
                        "response_bytes_raw": len(save_output.encode("utf-8")),
                    }
                )

            return (outputs, metas) if return_meta else outputs
        except Exception as e:
            logger.error(f"Failed to send config commands: {e}")
            return (outputs, metas) if return_meta else outputs

    def get_running_config(self) -> str:
        """Get the running configuration."""
        if not self.connected:
            return ""

        try:
            # Line-oriented wait: interior '#' in comments must not complete early.
            output = self.send_cmd(
                "show running-config",
                wait_for=["#"],
                wait_time=5.0,
                timeout=max(self.timeout, 60.0),
            )
            return output
        except Exception as e:
            logger.error(f"Failed to get running config: {e}")
            return ""
