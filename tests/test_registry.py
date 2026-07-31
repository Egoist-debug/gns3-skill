from __future__ import annotations

import inspect
import json
import re
import unittest
from collections import Counter

from gns3_skill.contracts import OperationTier
from gns3_skill.registry import (
    OPERATIONS,
    OPERATION_BY_ID,
    describe_operation,
    get_operation,
    list_operations,
)


class RegistryContractTests(unittest.TestCase):
    def test_identifiers_are_unique_canonical_snake_case(self):
        identifiers = [spec.identifier for spec in OPERATIONS]

        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertEqual(set(identifiers), set(OPERATION_BY_ID))
        for identifier in identifiers:
            self.assertRegex(identifier, re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"))
            self.assertFalse(identifier.startswith("gns3_"))

    def test_every_registered_callable_is_async(self):
        for spec in OPERATIONS:
            with self.subTest(operation=spec.identifier):
                self.assertTrue(inspect.iscoroutinefunction(spec.callable))

    def test_tier_counts_are_exact(self):
        counts = Counter(spec.tier for spec in OPERATIONS)

        self.assertEqual(counts[OperationTier.GOAL], 8)
        self.assertEqual(counts[OperationTier.EXPERT], 50)
        self.assertEqual(len(OPERATIONS), 58)
        self.assertEqual(len(list_operations()), 8)
        self.assertEqual(len(list_operations("goal")), 8)
        self.assertEqual(len(list_operations("expert")), 50)
        self.assertEqual(len(list_operations("all")), 58)

    def test_lookup_and_describe_use_the_same_registry_entry(self):
        spec = get_operation("prepare_lab")
        description = describe_operation("prepare_lab")

        self.assertIsNotNone(spec)
        self.assertIsNotNone(description)
        assert spec is not None and description is not None
        self.assertEqual(description, spec.to_dict())
        self.assertIsNone(get_operation("gns3_prepare_lab"))
        self.assertIsNone(describe_operation("gns3_prepare_lab"))

    def test_schemas_and_descriptions_are_json_serializable(self):
        for spec in OPERATIONS:
            with self.subTest(operation=spec.identifier):
                schema = spec.parameter_schema()
                encoded = json.dumps(spec.to_dict())
                self.assertIsInstance(schema, dict)
                self.assertTrue(encoded)

    def test_summaries_are_non_empty_and_operation_specific(self):
        for spec in OPERATIONS:
            with self.subTest(operation=spec.identifier):
                self.assertTrue(spec.summary.strip())
                self.assertNotEqual(spec.summary.strip(), spec.identifier)

    def test_schema_reports_required_fields_and_defaults(self):
        get_project = describe_operation("get_project")
        ensure_server = describe_operation("ensure_server")

        assert get_project is not None and ensure_server is not None
        self.assertIn("project_id", get_project["schema"]["required"])
        self.assertEqual(
            ensure_server["schema"]["properties"]["force"]["default"], False
        )

    def test_sensitive_parameters_are_marked_without_values(self):
        descriptions = [spec.to_dict() for spec in OPERATIONS]
        sensitive_fields = []

        for description in descriptions:
            properties = description["schema"]["properties"]
            for name, metadata in properties.items():
                if metadata.get("sensitive"):
                    sensitive_fields.append(name)
                    self.assertNotIn("value", metadata)
                    self.assertNotIn("example", metadata)

        self.assertIn("password", sensitive_fields)
        console = describe_operation("send_console_commands")
        ssh = describe_operation("ssh_exec")
        snapshot = describe_operation("manage_snapshot")
        assert console is not None and ssh is not None and snapshot is not None
        self.assertTrue(
            console["schema"]["properties"]["login_password"]["sensitive"]
        )
        self.assertTrue(ssh["schema"]["properties"]["ssh_password"]["sensitive"])
        self.assertTrue(
            snapshot["schema"]["properties"]["confirmation_token"]["sensitive"]
        )
        configure = describe_operation("configure_devices")
        diagnose = describe_operation("diagnose_connectivity")
        template = describe_operation("apply_config_template")
        assert configure is not None and diagnose is not None and template is not None
        self.assertTrue(configure["schema"]["properties"]["targets"]["sensitive"])
        self.assertTrue(
            diagnose["schema"]["properties"]["suspect_nodes"]["sensitive"]
        )
        self.assertTrue(
            template["schema"]["properties"]["template_params"]["sensitive"]
        )


if __name__ == "__main__":
    unittest.main()
