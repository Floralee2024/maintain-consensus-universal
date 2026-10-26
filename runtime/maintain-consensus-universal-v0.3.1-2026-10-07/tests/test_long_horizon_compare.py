import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "long_horizon_compare.py"


class LongHorizonComparisonTest(unittest.TestCase):
    def test_context_only_and_enhanced_paths_diverge_in_expected_ways(self):
        completed = subprocess.run([sys.executable, str(RUNNER)], cwd=ROOT,
                                   text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        baseline = report["baseline_context_only"]
        enhanced = report["enhanced_context_precheck_external"]
        comparison = report["comparison"]
        self.assertGreaterEqual(baseline["false_positives"], 3)
        self.assertFalse(baseline["final_ground_truth_success"])
        self.assertTrue(enhanced["final_ground_truth_success"])
        self.assertEqual(enhanced["justified_revisions"], 1)
        self.assertEqual(enhanced["drift_records"], 2)
        self.assertEqual(enhanced["ledger_check"]["integrity"], "valid")
        self.assertEqual(enhanced["ledger_check"]["unverified_actions"], [])
        self.assertTrue(comparison["stale_action_caught_by_precheck_or_verification"])
        self.assertTrue(comparison["failed_action_caught"])
        self.assertTrue(comparison["revision_recorded"])
        self.assertTrue(comparison["final_outcome_changed"])


if __name__ == "__main__":
    unittest.main()
