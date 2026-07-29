"""Unit tests for GNS3 server lifecycle (probe/auto-start/stop)."""

from __future__ import annotations

import os
import signal
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from gns3_skill.server_lifecycle import (
    _parse_ss_pids,
    build_start_command,
    clear_healthy_cache,
    ensure_gns3_server,
    is_local_server_url,
    normalize_server_url,
    stop_gns3_server,
)


class LocalUrlTests(unittest.TestCase):
    def test_local_hosts(self):
        self.assertTrue(is_local_server_url("http://localhost:3080"))
        self.assertTrue(is_local_server_url("http://127.0.0.1:3080"))
        self.assertTrue(is_local_server_url("http://[::1]:3080"))
        self.assertFalse(is_local_server_url("http://192.168.1.10:3080"))
        self.assertFalse(is_local_server_url("http://gns3.lab.example:3080"))

    def test_normalize(self):
        self.assertEqual(normalize_server_url("http://localhost:3080/"), "http://localhost:3080")


class BuildStartCommandTests(unittest.TestCase):
    def test_default_injects_host_port(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GNS3_SERVER_START_CMD", None)
            argv = build_start_command("http://127.0.0.1:3081")
            self.assertEqual(argv[:1], ["gns3server"])
            self.assertIn("--host", argv)
            self.assertIn("127.0.0.1", argv)
            self.assertIn("--port", argv)
            self.assertIn("3081", argv)

    def test_custom_cmd_not_rewritten(self):
        with patch.dict(os.environ, {"GNS3_SERVER_START_CMD": "mywrapper --foo"}):
            argv = build_start_command("http://127.0.0.1:3081")
            self.assertEqual(argv, ["mywrapper", "--foo"])


class EnsureServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_healthy_cache()
        os.environ.pop("GNS3_SERVER_START_CMD", None)

    async def test_already_running_no_start(self):
        with patch(
            "gns3_skill.server_lifecycle.probe_server",
            new=AsyncMock(return_value=(True, {"version": "2.2"})),
        ) as probe, patch(
            "gns3_skill.server_lifecycle._spawn_server",
            new=AsyncMock(),
        ) as spawn:
            result = await ensure_gns3_server("http://127.0.0.1:3080", force=True)
            self.assertEqual(result["status"], "success")
            self.assertTrue(result["already_running"])
            self.assertFalse(result["started"])
            spawn.assert_not_called()
            self.assertGreaterEqual(probe.await_count, 1)

    async def test_cache_skips_probe(self):
        with patch(
            "gns3_skill.server_lifecycle.probe_server",
            new=AsyncMock(return_value=(True, {"version": "2.2"})),
        ) as probe:
            r1 = await ensure_gns3_server("http://127.0.0.1:3080", force=True)
            self.assertEqual(r1["status"], "success")
            r2 = await ensure_gns3_server("http://127.0.0.1:3080", force=False)
            self.assertEqual(r2["status"], "success")
            self.assertTrue(r2["already_running"])
            # second call uses cache → no extra probe
            self.assertEqual(probe.await_count, 1)

    async def test_remote_unreachable_no_spawn(self):
        with patch(
            "gns3_skill.server_lifecycle.probe_server",
            new=AsyncMock(return_value=(False, "connection refused")),
        ), patch(
            "gns3_skill.server_lifecycle._spawn_server",
            new=AsyncMock(),
        ) as spawn:
            result = await ensure_gns3_server("http://10.0.0.5:3080", force=True)
            self.assertEqual(result["status"], "error")
            self.assertIn("Remote", result["error"])
            spawn.assert_not_called()

    async def test_local_down_starts_and_waits(self):
        probes = [
            (False, "down"),
            (False, "down"),
            (True, {"version": "2.2"}),
        ]

        async def probe_side_effect(*_a, **_k):
            return probes.pop(0) if probes else (True, {"version": "2.2"})

        with patch(
            "gns3_skill.server_lifecycle.probe_server",
            new=AsyncMock(side_effect=probe_side_effect),
        ), patch(
            "gns3_skill.server_lifecycle._spawn_server",
            new=AsyncMock(return_value=(12345, "")),
        ) as spawn, patch(
            "gns3_skill.server_lifecycle._start_timeout",
            return_value=5.0,
        ), patch(
            "gns3_skill.server_lifecycle.asyncio.sleep",
            new=AsyncMock(),
        ):
            result = await ensure_gns3_server("http://127.0.0.1:3080", force=True)
            self.assertEqual(result["status"], "success")
            self.assertTrue(result["started"])
            self.assertFalse(result["already_running"])
            spawn.assert_awaited_once()

    async def test_http_401_is_auth_error_not_healthy(self):
        # Pre-2.1 behavior: a reachable server returning 401 was cached as
        # "success" with a 401 body, causing agents to loop retries with no
        # password hint. The new contract: 401/403 from probe_server is an
        # auth error (ok=False), and ensure_gns3_server surfaces a credential
        # hint instead of starting / caching. See references/setup.md
        # "Finding local server credentials".
        with patch(
            "gns3_skill.server_lifecycle.probe_server",
            new=AsyncMock(
                return_value=(False, {
                    "reachable": True,
                    "http_status": 401,
                    "detail": "",
                    "error": (
                        "GNS3 API authentication required (HTTP 401). Set "
                        "GNS3_USERNAME / GNS3_PASSWORD from the local server "
                        "config before retrying — see references/setup.md "
                        "\"Finding local server credentials\" (Linux: "
                        "~/.config/GNS3/2.2/gns3_server.conf under [Server]). "
                        "Do not guess passwords."
                    ),
                })
            ),
        ), patch(
            "gns3_skill.server_lifecycle._spawn_server",
            new=AsyncMock(),
        ) as spawn:
            result = await ensure_gns3_server("http://127.0.0.1:3080", force=True)
            self.assertEqual(result["status"], "error")
            self.assertTrue(result["already_running"])
            self.assertEqual(result.get("http_status"), 401)
            self.assertIn("GNS3_USERNAME", result["error"])
            self.assertIn("GNS3_PASSWORD", result["error"])
            self.assertIn("gns3_server.conf", result["error"])
            spawn.assert_not_called()


class ParseSsPidsTests(unittest.TestCase):
    def test_extracts_unique_pids(self):
        out = (
            "State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
            'LISTEN 0 100 127.0.0.1:3080 0.0.0.0:* users:(("gns3server",pid=4242,fd=6))\n'
        )
        self.assertEqual(_parse_ss_pids(out), [4242])

    def test_empty(self):
        self.assertEqual(_parse_ss_pids(""), [])


class StopServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_healthy_cache()

    async def test_remote_refused(self):
        with patch(
            "gns3_skill.server_lifecycle._pids_listening_on_port",
            return_value=[1],
        ) as pids:
            result = await stop_gns3_server("http://10.0.0.5:3080")
            self.assertEqual(result["status"], "error")
            self.assertIn("remote", result["error"].lower())
            pids.assert_not_called()

    async def test_already_stopped_no_pids(self):
        with patch(
            "gns3_skill.server_lifecycle._pids_listening_on_port",
            return_value=[],
        ):
            # seed cache then ensure clear
            from gns3_skill import server_lifecycle as sl

            sl._cache_set("http://127.0.0.1:3080", {"version": "x"})
            result = await stop_gns3_server("http://127.0.0.1:3080")
            self.assertEqual(result["status"], "success")
            self.assertTrue(result["already_stopped"])
            self.assertFalse(result["stopped"])
            self.assertIsNone(sl._cache_get("http://127.0.0.1:3080"))

    async def test_term_then_exit(self):
        alive = {99: True}

        def pid_alive(pid):
            return alive.get(pid, False)

        def signal_pid(pid, sig):
            if sig == signal.SIGTERM:
                alive[pid] = False
                return "signaled"
            return "gone"

        with patch(
            "gns3_skill.server_lifecycle._pids_listening_on_port",
            side_effect=[[99], []],
        ), patch(
            "gns3_skill.server_lifecycle._pid_alive",
            side_effect=pid_alive,
        ), patch(
            "gns3_skill.server_lifecycle._signal_pid",
            side_effect=signal_pid,
        ), patch(
            "gns3_skill.server_lifecycle.asyncio.sleep",
            new=AsyncMock(),
        ):
            result = await stop_gns3_server(
                "http://127.0.0.1:3080", stop_timeout=1.0
            )
            self.assertEqual(result["status"], "success")
            self.assertTrue(result["stopped"])
            self.assertEqual(result["pids"], [99])
            self.assertTrue(
                any(s.get("signal") == "SIGTERM" for s in result["signal_steps"])
            )
            self.assertFalse(
                any(s.get("signal") == "SIGKILL" for s in result["signal_steps"])
            )

    async def test_term_then_kill(self):
        # Stays alive until SIGKILL
        state = {"term_sent": False, "kill_sent": False}

        def pid_alive(pid):
            if state["kill_sent"]:
                return False
            return True

        def signal_pid(pid, sig):
            if sig == signal.SIGTERM:
                state["term_sent"] = True
                return "signaled"
            if sig == signal.SIGKILL:
                state["kill_sent"] = True
                return "signaled"
            return "error"

        listen_calls = {"n": 0}

        def listen(_port):
            listen_calls["n"] += 1
            # first discovery has pid; post-kill listen empty
            if state["kill_sent"]:
                return []
            return [77]

        with patch(
            "gns3_skill.server_lifecycle._pids_listening_on_port",
            side_effect=listen,
        ), patch(
            "gns3_skill.server_lifecycle._pid_alive",
            side_effect=pid_alive,
        ), patch(
            "gns3_skill.server_lifecycle._signal_pid",
            side_effect=signal_pid,
        ), patch(
            "gns3_skill.server_lifecycle.asyncio.sleep",
            new=AsyncMock(),
        ):
            result = await stop_gns3_server(
                "http://127.0.0.1:3080", stop_timeout=0.0
            )
            self.assertEqual(result["status"], "success")
            self.assertTrue(result["stopped"])
            self.assertTrue(state["term_sent"])
            self.assertTrue(state["kill_sent"])
            self.assertTrue(
                any(s.get("signal") == "SIGKILL" for s in result["signal_steps"])
            )


class CleanupSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_flags_false_success(self):
        from gns3_skill.server import gns3_cleanup_session

        result = await gns3_cleanup_session()
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["steps"]), 3)
        self.assertTrue(all(s["status"] == "skipped" for s in result["steps"]))

    async def test_missing_project_id_skipped(self):
        from gns3_skill.server import gns3_cleanup_session

        with patch(
            "gns3_skill.server.stop_gns3_server",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "stopped": True,
                    "already_stopped": False,
                    "pids": [1],
                    "signal_steps": [],
                    "server_url": "http://localhost:3080",
                    "wait_seconds": 0.1,
                }
            ),
        ) as stop:
            result = await gns3_cleanup_session(
                stop_nodes=True, close_project=True, stop_server=True
            )
            self.assertEqual(result["status"], "success")
            by_step = {s["step"]: s for s in result["steps"]}
            self.assertEqual(by_step["stop_nodes"]["status"], "skipped")
            self.assertEqual(by_step["close_project"]["status"], "skipped")
            self.assertEqual(by_step["stop_server"]["status"], "success")
            stop.assert_awaited_once()

    async def test_close_fail_still_stops_server(self):
        from gns3_skill.server import gns3_cleanup_session

        mock_client = MagicMock()
        mock_client.close_project = AsyncMock(side_effect=RuntimeError("close boom"))

        with patch(
            "gns3_skill.server.create_client_ready",
            new=AsyncMock(return_value=mock_client),
        ), patch(
            "gns3_skill.server.stop_gns3_server",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "stopped": True,
                    "already_stopped": False,
                    "pids": [5],
                    "signal_steps": [],
                    "server_url": "http://localhost:3080",
                    "wait_seconds": 0.2,
                }
            ),
        ) as stop:
            result = await gns3_cleanup_session(
                project_id="proj-1",
                close_project=True,
                stop_server=True,
            )
            self.assertEqual(result["status"], "partial")
            by_step = {s["step"]: s for s in result["steps"]}
            self.assertEqual(by_step["close_project"]["status"], "error")
            self.assertEqual(by_step["stop_server"]["status"], "success")
            stop.assert_awaited_once()

    async def test_stop_only_does_not_ensure(self):
        from gns3_skill.server import gns3_cleanup_session

        with patch(
            "gns3_skill.server.create_client_ready",
            new=AsyncMock(),
        ) as ready, patch(
            "gns3_skill.server.stop_gns3_server",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "stopped": False,
                    "already_stopped": True,
                    "pids": [],
                    "signal_steps": [],
                    "server_url": "http://localhost:3080",
                    "wait_seconds": 0,
                }
            ),
        ):
            result = await gns3_cleanup_session(stop_server=True)
            self.assertEqual(result["status"], "success")
            ready.assert_not_called()


if __name__ == "__main__":
    unittest.main()
