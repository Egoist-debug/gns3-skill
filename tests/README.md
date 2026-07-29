# Tests

Unit tests for the skill library (goal workflows, lifecycle, console/SSH helpers).

```bash
# from gns3-skill/
PYTHONPATH=src python -m pytest tests/ -q
```

| File | Scope |
|------|-------|
| `test_goal_authorization.py` | Confirmation token gates for destructive goal ops |
| `test_goal_completion.py` | Goal tool completion / step trace |
| `test_goal_tools.py` | Goal tool unit tests (prepare_lab, manage_snapshot, finish_lab, prepare_image, run_guest_commands) |
| `test_goal_topology.py` | Build topology goal convergence |
| `test_server_lifecycle.py` | Server probe / auto-start / stop |
| `test_ssh_client.py` | SSH credential resolution, guest exec |
| `test_telnet_login.py` | Console login flow |
| `test_telnet_output.py` | Console output parsing, ANSI stripping, pager handling |
| `test_workflow_core.py` | Runner, envelopes, resolve helpers |

Do **not** use these files as a lab-ops path. Lab work goes through `scripts/gns3`.
