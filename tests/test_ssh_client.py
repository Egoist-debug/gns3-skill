"""Unit tests for SSH guest exec helper."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import asyncssh

from gns3_skill import ssh_client


class ExtractIpTests(unittest.TestCase):
    def test_from_properties(self):
        node = {
            "name": "vm1",
            "properties": {"ip_address": "10.1.1.50", "adapters": 1},
            "console_host": "127.0.0.1",
        }
        ips = ssh_client.extract_ips_from_node(node)
        self.assertIn("10.1.1.50", ips)
        self.assertNotIn("127.0.0.1", ips)


class HostKeyPolicyTests(unittest.TestCase):
    def test_strict_uses_default_known_hosts(self):
        # strict: no known_hosts override → asyncssh loads default files
        kwargs = ssh_client._connect_kwargs_for_policy("strict")
        self.assertEqual(kwargs, {})

    def test_accept_new_skips_known_hosts(self):
        # known_hosts=None disables verification and skips client validator
        kwargs = ssh_client._connect_kwargs_for_policy("accept_new")
        self.assertEqual(kwargs, {"known_hosts": None})

    def test_warn_uses_empty_known_hosts_and_logs(self):
        kwargs = ssh_client._connect_kwargs_for_policy("warn")
        self.assertEqual(kwargs.get("known_hosts"), b"")
        self.assertIs(kwargs.get("client_factory"), ssh_client._WarnHostKeyClient)
        client = ssh_client._WarnHostKeyClient()
        key = MagicMock()
        key.get_fingerprint.return_value = "SHA256:test"
        with self.assertLogs("gns3_skill.ssh_client", level="WARNING") as cm:
            self.assertTrue(client.validate_host_public_key("h", "1.2.3.4", 22, key))
        self.assertTrue(any("fingerprint=SHA256:test" in m for m in cm.output))


class CredentialResolverTests(unittest.TestCase):
    def test_console_args_override_env(self):
        with patch.dict(
            "os.environ",
            {"GNS3_CONSOLE_USER": "envu", "GNS3_CONSOLE_PASSWORD": "envp"},
            clear=False,
        ):
            u, p = ssh_client.resolve_console_credentials("argu", "argp")
            self.assertEqual((u, p), ("argu", "argp"))
            u, p = ssh_client.resolve_console_credentials(None, None)
            self.assertEqual((u, p), ("envu", "envp"))

    def test_ssh_args_override_env(self):
        with patch.dict(
            "os.environ",
            {"GNS3_SSH_USER": "envu", "GNS3_SSH_PASSWORD": "envp"},
            clear=False,
        ):
            u, p = ssh_client.resolve_ssh_credentials("argu", "argp")
            self.assertEqual((u, p), ("argu", "argp"))


class ExecCommandsTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_host_and_creds(self):
        r = await ssh_client.exec_commands("", ["id"], username="u", password="p")
        self.assertEqual(r["status"], "error")
        r = await ssh_client.exec_commands("10.0.0.1", ["id"], username=None, password="p")
        self.assertEqual(r["status"], "error")

    async def test_runs_commands_stop_on_error(self):
        conn = MagicMock()
        result_ok = MagicMock(stdout="ok\n", stderr="", exit_status=0)
        result_bad = MagicMock(stdout="", stderr="fail", exit_status=1)
        conn.run = AsyncMock(side_effect=[result_ok, result_bad])
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)

        with patch("gns3_skill.ssh_client.asyncssh.connect", return_value=conn) as connect:
            out = await ssh_client.exec_commands(
                "10.0.0.1",
                ["echo ok", "false", "echo never"],
                username="user",
                password="pass",
                stop_on_error=True,
                host_key_policy="accept_new",
            )
            kwargs = connect.call_args.kwargs
            self.assertIsNone(kwargs.get("known_hosts"))
        self.assertEqual(out["status"], "error")
        self.assertTrue(out["authenticated"])
        self.assertEqual(len(out["results"]), 2)
        # password must not appear in payload
        self.assertNotIn("pass", str(out))

    async def test_success_all_commands(self):
        conn = MagicMock()
        conn.run = AsyncMock(
            side_effect=[
                MagicMock(stdout="a", stderr="", exit_status=0),
                MagicMock(stdout="b", stderr="", exit_status=0),
            ]
        )
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)

        with patch("gns3_skill.ssh_client.asyncssh.connect", return_value=conn):
            out = await ssh_client.exec_commands(
                "10.0.0.1",
                ["a", "b"],
                username="user",
                password="pass",
            )
        self.assertEqual(out["status"], "success")
        self.assertEqual(len(out["results"]), 2)
        self.assertEqual(out["username"], "user")

    async def test_retries_transient_then_succeeds(self):
        conn = MagicMock()
        conn.run = AsyncMock(
            return_value=MagicMock(stdout="ok", stderr="", exit_status=0)
        )
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)

        refused = ConnectionRefusedError("connection refused")
        with patch(
            "gns3_skill.ssh_client.asyncssh.connect",
            side_effect=[refused, conn],
        ) as connect, patch(
            "gns3_skill.ssh_client.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            out = await ssh_client.exec_commands(
                "10.0.0.1",
                ["id"],
                username="user",
                password="pass",
                connect_timeout=5.0,
            )
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["connect_attempts"], 2)
        self.assertEqual(connect.call_count, 2)
        sleep.assert_awaited()

    async def test_permission_denied_not_retried(self):
        with patch(
            "gns3_skill.ssh_client.asyncssh.connect",
            side_effect=asyncssh.PermissionDenied("nope"),
        ) as connect, patch(
            "gns3_skill.ssh_client.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            out = await ssh_client.exec_commands(
                "10.0.0.1",
                ["id"],
                username="user",
                password="bad",
                connect_timeout=10.0,
            )
        self.assertEqual(out["status"], "error")
        self.assertFalse(out["authenticated"])
        self.assertEqual(out["connect_attempts"], 1)
        self.assertEqual(connect.call_count, 1)
        sleep.assert_not_awaited()
        self.assertNotIn("bad", str(out))


if __name__ == "__main__":
    unittest.main()
