"""Unit tests for TelnetClient.login heuristic."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from gns3_skill.telnet_client import TelnetClient


class FakeSock:
    def __init__(self, scripted_reads):
        self.scripted = list(scripted_reads)
        self.sent = []

    def send(self, data: bytes):
        self.sent.append(data)
        return len(data)

    def recv(self, _n: int) -> bytes:
        if not self.scripted:
            raise TimeoutError("no more data")
        item = self.scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, bytes) else item.encode()

    def settimeout(self, _t):
        pass

    def close(self):
        pass


class TelnetLoginTests(unittest.TestCase):
    def _client_with(self, reads):
        c = TelnetClient("127.0.0.1", 5000, timeout=1.0)
        c.sock = FakeSock(reads)
        c.connected = True
        return c

    def test_already_at_prompt(self):
        # After nudge, device shows enable prompt
        c = self._client_with([b"R1#"])
        # read_until will use recv loop; provide enough data
        with patch.object(c, "read_until", side_effect=["R1#"]):
            self.assertTrue(c.login("admin", "secret", settle=0.0))

    def test_username_password_flow(self):
        c = self._client_with([])
        with patch.object(
            c,
            "read_until",
            side_effect=["User Access Verification\nUsername:", "Password:", "R1>"],
        ):
            ok = c.login("admin", "secret", settle=0.0)
            self.assertTrue(ok)
            # username then password sent
            sent = b"".join(c.sock.sent)
            self.assertIn(b"admin\r", sent)
            self.assertIn(b"secret\r", sent)

    def test_missing_password_fails(self):
        c = self._client_with([])
        with patch.object(c, "read_until", side_effect=["Password:"]):
            self.assertFalse(c.login("admin", None, settle=0.0))

    def test_auth_failure_stays_at_login(self):
        c = self._client_with([])
        with patch.object(
            c,
            "read_until",
            side_effect=["Username:", "Password:", "Username:", "Username:"],
        ):
            # After password still Username: and no shell
            self.assertFalse(c.login("admin", "bad", settle=0.0, ready_timeout=2.0))


if __name__ == "__main__":
    unittest.main()
