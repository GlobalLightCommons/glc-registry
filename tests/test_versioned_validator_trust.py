import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from fetch_registry import schema_lifecycle, validation_summary
from validate_datasets_pr import dataset_entries_requiring_verification
from validation_artifacts import (
    TRUSTED_VALIDATOR_POLICIES,
    policies_for_report,
    validate_report_content,
    verify_attestation,
)


class VersionedValidatorTrustTests(unittest.TestCase):
    def policy(self, policy_id):
        return next(
            policy for policy in TRUSTED_VALIDATOR_POLICIES if policy["id"] == policy_id
        )

    def test_current_image_selects_current_glc_policy(self):
        report = {
            "validator_image": "ghcr.io/tscnlab/glc-validator@sha256:abc"
        }

        self.assertEqual(
            [policy["id"] for policy in policies_for_report(report)],
            ["current_glc"],
        )

    def test_legacy_image_selects_legacy_glee_policy(self):
        report = {
            "validator_image": "ghcr.io/tscnlab/glee-validator@sha256:def"
        }

        self.assertEqual(
            [policy["id"] for policy in policies_for_report(report)],
            ["legacy_glee"],
        )

    def test_image_must_match_the_attestation_policy(self):
        report = {
            "repo": "tscnlab/example",
            "commit_sha": "a" * 40,
            "status": "pass",
            "validator_image": "ghcr.io/tscnlab/glee-validator@sha256:def",
        }

        errors = validate_report_content(
            report,
            "tscnlab/example",
            "a" * 40,
            self.policy("current_glc"),
        )

        self.assertTrue(any("does not start with" in error for error in errors))

    @patch("validation_artifacts.subprocess.run")
    @patch("validation_artifacts.shutil.which", return_value="/usr/bin/gh")
    def test_legacy_policy_verifies_with_legacy_signer_workflow(
        self, _which, run
    ):
        run.return_value = SimpleNamespace(returncode=0, stdout="[]", stderr="")

        verify_attestation(
            Path("validation.json"),
            "tscnlab/example",
            "a" * 40,
            self.policy("legacy_glee"),
        )

        command = run.call_args.args[0]
        signer_index = command.index("--signer-workflow") + 1
        self.assertEqual(
            command[signer_index],
            "tscnlab/glee-metadata-validator/.github/workflows/validate.yml",
        )
        self.assertIn("--deny-self-hosted-runners", command)

    def test_schema_lifecycle_distinguishes_current_and_legacy(self):
        self.assertEqual(schema_lifecycle("3.0.0"), "current")
        self.assertEqual(schema_lifecycle("2.0.0"), "legacy")
        self.assertEqual(schema_lifecycle("3.0.1"), "unrecognized")
        self.assertEqual(schema_lifecycle("4.0.0"), "unrecognized")
        self.assertEqual(schema_lifecycle(None), "unrecognized")

    def test_schema_lifecycle_works_when_a_future_version_is_current(self):
        self.assertEqual(schema_lifecycle("3.0.0", "3.0.1"), "legacy")
        self.assertEqual(schema_lifecycle("3.0.1", "3.0.1"), "current")
        self.assertEqual(schema_lifecycle("4.0.0", "3.0.1"), "unrecognized")

    def test_validation_summary_exposes_schema_and_trust_policy(self):
        result = {
            "validation": {
                "status": "pass",
                "repo": "tscnlab/example",
                "commit_sha": "a" * 40,
                "schema_version": "2.0.0",
                "validator_version": "0.4.3",
                "validator_image": "ghcr.io/tscnlab/glee-validator@sha256:def",
            },
            "manifest": {"files": [{}, {}]},
            "trust_policy": "legacy_glee",
        }

        summary = validation_summary(result)

        self.assertEqual(summary["schema_version"], "2.0.0")
        self.assertEqual(summary["schema_lifecycle"], "legacy")
        self.assertTrue(summary["upgrade_recommended"])
        self.assertEqual(summary["validator_trust_policy"], "legacy_glee")
        self.assertEqual(summary["manifest_file_count"], 2)

    def test_trust_logic_changes_force_all_datasets_to_be_verified(self):
        datasets = [
            {"repo": "tscnlab/legacy", "branch": "main"},
            {"repo": "tscnlab/current", "branch": "main"},
        ]

        selected = dataset_entries_requiring_verification(
            datasets,
            {"datasets": datasets},
            force_all=True,
        )

        self.assertEqual(
            selected,
            [
                (datasets[0], "registry trust logic changed"),
                (datasets[1], "registry trust logic changed"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
