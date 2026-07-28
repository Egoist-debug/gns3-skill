# Tests

Unit tests for the skill library (goal workflows, lifecycle, console/SSH helpers).

```bash
# from gns3-skill/
PYTHONPATH=src python -m pytest \
  tests/test_workflow_core.py \
  tests/test_goal_authorization.py \
  tests/test_goal_completion.py \
  tests/test_server_lifecycle.py -q
```

Do **not** use these files as a lab-ops path. Lab work goes through `scripts/gns3`.
