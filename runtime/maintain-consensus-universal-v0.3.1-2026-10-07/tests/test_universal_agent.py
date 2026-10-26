import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "universal_agent.py"
FIXTURES = ROOT / "fixtures"


class UniversalAgentCLITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.state = self.work / "universal_ledger.json"
        self.cli("init", "--session-id", "test-session")
        self.cli("set-context", "--expect-revision", "1", "--input", str(FIXTURES / "context.json"))
        self.cli("commit", "--expect-revision", "2", "--input", str(FIXTURES / "commitment.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args, expect=0):
        command = [sys.executable, str(SCRIPT), "--state", str(self.state), *map(str, args)]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        if completed.returncode != expect:
            self.fail(f"command failed: {command}\nstdout={completed.stdout}\nstderr={completed.stderr}")
        if not completed.stdout.strip():
            return {}
        return json.loads(completed.stdout)

    def write_json(self, name, value):
        path = self.work / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def add_action(self, proposal_name, revision):
        return self.cli("precheck", "--expect-revision", revision, "--input", str(FIXTURES / proposal_name))

    def test_file_legal_alternate_path_is_aligned(self):
        proposal = self.add_action("proposal_aligned.json", 3)
        self.assertEqual(proposal["precheck"]["status"], "consistent")
        result = self.cli("verify", "--expect-revision", "4", "--id", "action-aligned",
                          "--input", str(FIXTURES / "observation_file.json"))
        self.assertEqual(result["divergence_type"], "aligned")
        self.assertEqual(result["observed"]["classification"]["path_match"], "declared_legal_path")

    def test_precheck_conflict_is_recorded_without_blocking(self):
        proposal = self.write_json("proposal_conflict.json", {
            "action_id": "action-conflict",
            "scenario_id": "precheck-demo",
            "proposed_action": "只凭模型自报完成",
            "decision_basis": ["goal"],
            "evidence_refs": ["fixture:conflict"],
            "expected_postcondition": {
                "description": "状态存在",
                "checks": [{"path": "status", "op": "exists"}]
            },
            "legal_paths": [],
            "context_version": 4
        })
        result = self.cli("precheck", "--expect-revision", "3", "--input", str(proposal))
        self.assertEqual(result["precheck"]["status"], "needs_restatement")
        self.assertIn("proposed_action_matches_do_not_reuse", result["precheck"]["reasons"])
        ledger = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(ledger["actions"]["action-conflict"]["precheck"]["status"], "needs_restatement")

    def test_sqlite_is_read_only_and_verifies_real_rows(self):
        db = self.work / "app.sqlite"
        with sqlite3.connect(db) as connection:
            connection.execute("CREATE TABLE jobs (id INTEGER, status TEXT)")
            connection.execute("INSERT INTO jobs VALUES (1, 'completed')")
            connection.commit()
        self.add_action("proposal_sqlite.json", 3)
        observation = self.write_json("observation_db.json", {
            "verification_source": {
                "kind": "database", "ref": f"sqlite:///{db}",
                "query": "SELECT status FROM jobs WHERE id = 1",
                "authority": "database",
                "independence": "independent",
                "mutable_by_executor": False,
                "max_age_seconds": 3600,
                "coverage": ["rows[0].status", "row_count"]
            },
            "observed_action": "从只读数据库读取完成状态"
        })
        result = self.cli("verify", "--expect-revision", "4", "--id", "action-sqlite", "--input", observation)
        self.assertEqual(result["divergence_type"], "aligned")
        with sqlite3.connect(db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)

    def test_tool_receipt_and_environment_snapshot_are_external_sources(self):
        proposal = self.write_json("proposal_tool.json", {
            "action_id": "action-tool",
            "scenario_id": "tool-demo",
            "proposed_action": "执行直接写入",
            "decision_basis": ["goal"],
            "evidence_refs": ["fixture:tool"],
            "expected_postcondition": {
                "description": "工具回执成功",
                "checks": [
                    {"path": "status", "op": "eq", "value": "completed"},
                    {"path": "effects", "op": "contains", "value": "artifact-written"}
                ]
            },
            "legal_paths": [{"path_id": "direct", "description": "直接写入"}],
            "context_version": 4
        })
        self.cli("precheck", "--expect-revision", "3", "--input", proposal)
        result = self.cli("verify", "--expect-revision", "4", "--id", "action-tool",
                          "--input", str(FIXTURES / "observation_tool.json"))
        self.assertEqual(result["divergence_type"], "aligned")
        environment_proposal = self.write_json("proposal_environment.json", {
            "action_id": "action-environment",
            "scenario_id": "environment-demo",
            "proposed_action": "执行直接写入",
            "decision_basis": ["goal"],
            "evidence_refs": ["fixture:environment"],
            "expected_postcondition": {
                "description": "环境快照满足部署条件",
                "checks": [
                    {"path": "deployment.status", "op": "eq", "value": "completed"},
                    {"path": "version", "op": "eq", "value": 2}
                ]
            },
            "legal_paths": [{"path_id": "direct", "description": "直接写入"}],
            "context_version": 4
        })
        self.cli("precheck", "--expect-revision", "5", "--input", environment_proposal)
        result = self.cli("verify", "--expect-revision", "6", "--id", "action-environment",
                          "--input", str(FIXTURES / "observation_environment.json"))
        self.assertEqual(result["divergence_type"], "aligned")

    def test_drift_when_real_postcondition_fails(self):
        proposal = self.add_action("proposal_drift.json", 3)
        self.assertEqual(proposal["action_id"], "action-drift")
        result_file = self.write_json("failed_result.json", {
            "status": "failed",
            "action": {"description": "执行直接写入"}
        })
        observation = self.write_json("observation_drift.json", {
            "verification_source": {
                "kind": "artifact", "ref": str(result_file),
                "authority": "artifact_store", "independence": "independent",
                "mutable_by_executor": False, "max_age_seconds": 3600,
                "coverage": ["status", "effects"]
            }
        })
        result = self.cli("verify", "--expect-revision", "4", "--id", "action-drift", "--input", observation)
        self.assertEqual(result["divergence_type"], "drift")
        self.assertFalse(result["observed"]["postcondition"]["passed"])

    def test_evidence_backed_action_change_is_justified_revision(self):
        self.add_action("proposal_revision.json", 3)
        result_file = self.write_json("revision_result.json", {
            "status": "completed",
            "revision": 2,
            "action": {"description": "执行通过备用路径的写入"}
        })
        observation = self.write_json("observation_revision.json", {
            "verification_source": {
                "kind": "artifact", "ref": str(result_file),
                "authority": "artifact_store", "independence": "independent",
                "mutable_by_executor": False, "max_age_seconds": 3600,
                "coverage": ["status", "revision"]
            },
            "revision_basis": {
                "reason": "外部限制要求切换到备用路径",
                "evidence_refs": ["ticket:123"]
            }
        })
        result = self.cli("verify", "--expect-revision", "4", "--id", "action-revision", "--input", observation)
        self.assertEqual(result["divergence_type"], "justified_revision")

    def test_missing_source_is_unknown_and_not_success(self):
        self.add_action("proposal_aligned.json", 3)
        result = self.cli("verify", "--expect-revision", "4", "--id", "action-aligned",
                          "--input", str(FIXTURES / "observation_missing.json"))
        self.assertEqual(result["divergence_type"], "unknown")
        metrics = self.cli("metrics", "--scenario-id", "deploy-demo")
        self.assertEqual(metrics["successful_attempts"], 0)
        self.assertEqual(metrics["unknown_attempts"], 1)

    def test_self_reported_postcondition_is_rejected(self):
        self.add_action("proposal_aligned.json", 3)
        self.cli("verify", "--expect-revision", "4", "--id", "action-aligned",
                 "--input", str(FIXTURES / "observation_self_report.json"), expect=2)
        ledger = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertIsNone(ledger["actions"]["action-aligned"]["observed"])
        self.assertIsNone(ledger["actions"]["action-aligned"]["divergence_type"])

    def test_weak_postcondition_is_not_allowed_to_align(self):
        proposal = self.write_json("weak_proposal.json", {
            "action_id": "action-weak",
            "scenario_id": "quality-demo",
            "proposed_action": "执行弱检查行动",
            "decision_basis": ["goal"],
            "evidence_refs": ["fixture:weak"],
            "expected_postcondition": {
                "description": "状态存在",
                "checks": [{"path": "status", "op": "eq", "value": "completed"}]
            },
            "legal_paths": [],
            "context_version": 4
        })
        precheck = self.cli("precheck", "--expect-revision", "3", "--input", proposal)
        self.assertEqual(precheck["precheck"]["status"], "insufficient_basis")
        result_file = self.write_json("weak_result.json", {
            "status": "completed", "action": {"description": "执行弱检查行动"}
        })
        observation = self.write_json("weak_observation.json", {
            "verification_source": {
                "kind": "artifact", "ref": str(result_file),
                "authority": "artifact_store", "independence": "independent",
                "mutable_by_executor": False, "max_age_seconds": 3600,
                "coverage": ["status"]
            }
        })
        result = self.cli("verify", "--expect-revision", "4", "--id", "action-weak", "--input", observation)
        self.assertEqual(result["divergence_type"], "unknown")
        self.assertEqual(result["observed"]["postcondition_quality"]["grade"], "weak")

    def test_action_binding_fields_and_key_node_trigger_are_recorded(self):
        proposal = self.write_json("proposal_binding.json", {
            "action_id": "action-binding",
            "scenario_id": "binding-demo",
            "proposed_action": {
                "description": "读取外部产物并核对预期后置状态",
                "target": "demo-artifact"
            },
            "serves_purpose": "检验框架是否把研究目的落实到选案行动",
            "action_delta_response": "不再把资料容易获得作为主案例标准",
            "actual_action_source": "external_only",
            "trigger_rules": {
                "node_type": "case_selection",
                "precheck": True,
                "postcheck": True,
                "mode": "external_required"
            },
            "decision_basis": ["goal", "action_delta"],
            "evidence_refs": ["fixture:binding"],
            "expected_postcondition": {
                "description": "外部产物完成",
                "checks": [
                    {"path": "status", "op": "eq", "value": "completed"},
                    {"path": "result.version", "op": "gte", "value": 2}
                ]
            },
            "legal_paths": [],
            "context_version": 4
        })
        precheck = self.cli("precheck", "--expect-revision", "3", "--input", proposal)
        self.assertEqual(precheck["precheck"]["status"], "consistent")
        binding = precheck["precheck"]["action_binding"]
        self.assertEqual(binding["precheck_nodes"], ["case_selection"])
        self.assertEqual(binding["postcheck_nodes"], ["case_selection"])
        result = self.cli("verify", "--expect-revision", "4", "--id", "action-binding",
                          "--input", str(FIXTURES / "observation_file.json"))
        self.assertEqual(result["divergence_type"], "aligned")
        self.assertEqual(result["comparison"], "aligned")
        self.assertEqual(result["observed"]["actual_action_source"], "external_source")

    def test_external_only_does_not_accept_declared_action_fallback(self):
        proposal = self.write_json("proposal_external_only.json", {
            "action_id": "action-external-only",
            "scenario_id": "binding-demo",
            "proposed_action": "执行外部可验证行动",
            "serves_purpose": "验证外部行动绑定",
            "action_delta_response": "响应最近的行动变化",
            "actual_action_source": "external_only",
            "decision_basis": ["goal"],
            "evidence_refs": ["fixture:external-only"],
            "expected_postcondition": {
                "description": "状态完成且有影响",
                "checks": [
                    {"path": "status", "op": "eq", "value": "completed"},
                    {"path": "effects", "op": "contains", "value": "artifact-written"}
                ]
            },
            "legal_paths": [],
            "context_version": 4
        })
        self.cli("precheck", "--expect-revision", "3", "--input", proposal)
        result_file = self.write_json("external_without_action.json", {
            "status": "completed", "effects": ["artifact-written"]
        })
        observation = self.write_json("observation_declared_fallback.json", {
            "verification_source": {
                "kind": "artifact", "ref": str(result_file),
                "authority": "artifact_store", "independence": "independent",
                "mutable_by_executor": False, "max_age_seconds": 3600,
                "coverage": ["status", "effects"]
            },
            "observed_action": "执行外部可验证行动"
        })
        result = self.cli("verify", "--expect-revision", "4", "--id", "action-external-only", "--input", observation)
        self.assertEqual(result["divergence_type"], "unknown")
        self.assertEqual(result["comparison"], "unknown")
        self.assertIn("external_only", result["reason"])

    def test_legacy_aligned_action_gets_partial_binding_comparison(self):
        self.add_action("proposal_aligned.json", 3)
        result = self.cli("verify", "--expect-revision", "4", "--id", "action-aligned",
                          "--input", str(FIXTURES / "observation_file.json"))
        self.assertEqual(result["divergence_type"], "aligned")
        self.assertEqual(result["comparison"], "partial")

    def test_source_quality_records_independence_freshness_and_coverage_gaps(self):
        proposal = self.write_json("source_quality_proposal.json", {
            "action_id": "action-source-quality",
            "scenario_id": "quality-demo",
            "proposed_action": "执行来源质量测试",
            "decision_basis": ["goal"],
            "evidence_refs": ["fixture:source-quality"],
            "expected_postcondition": {
                "description": "状态和版本正确",
                "checks": [
                    {"path": "status", "op": "eq", "value": "completed"},
                    {"path": "version", "op": "eq", "value": 2}
                ]
            },
            "legal_paths": [],
            "context_version": 4
        })
        self.cli("precheck", "--expect-revision", "3", "--input", proposal)
        result_file = self.write_json("source_quality_result.json", {
            "status": "completed", "version": 2,
            "action": {"description": "执行来源质量测试"}
        })
        observation = self.write_json("source_quality_observation.json", {
            "verification_source": {
                "kind": "artifact", "ref": str(result_file),
                "authority": "executor_receipt", "independence": "same_executor",
                "mutable_by_executor": True, "max_age_seconds": 0,
                "coverage": ["status"]
            }
        })
        result = self.cli("verify", "--expect-revision", "4", "--id", "action-source-quality", "--input", observation)
        self.assertEqual(result["divergence_type"], "unknown")
        quality = result["observed"]["source_quality"]
        self.assertIn("source_not_independent", quality["weaknesses"])
        self.assertIn("source_mutability_unknown_or_allowed", quality["weaknesses"])
        self.assertIn("postcondition_paths_outside_source_coverage", quality["weaknesses"])

    def test_legacy_context_and_description_only_postcondition_are_safe(self):
        legacy_context = self.write_json("legacy_context.json", {
            "consensus_version": 4,
            "current_goal": "兼容旧上下文",
            "action_delta": {
                "recent_changes": ["legacy"],
                "do_not_reuse": [],
                "superseded_bases": []
            }
        })
        self.cli("set-context", "--expect-revision", "3", "--input", legacy_context)
        view = self.cli("context", "--json")
        self.assertEqual(view["context"]["goal"], "兼容旧上下文")
        self.assertEqual(view["context"]["current_goal"], "兼容旧上下文")
        proposal = self.write_json("legacy_proposal.json", {
            "action_id": "action-legacy",
            "proposed_action": "执行旧式行动",
            "decision_basis": ["goal"],
            "evidence_refs": ["legacy:fixture"],
            "expected_postcondition": {"description": "旧式文字后置状态"},
            "context_version": 4
        })
        result = self.cli("precheck", "--expect-revision", "4", "--input", proposal)
        self.assertEqual(result["precheck"]["status"], "insufficient_basis")
        result_file = self.write_json("legacy_result.json", {
            "status": "completed", "action": {"description": "执行旧式行动"}
        })
        observation = self.write_json("legacy_observation.json", {
            "verification_source": {
                "kind": "artifact", "ref": str(result_file),
                "authority": "artifact_store", "independence": "independent",
                "mutable_by_executor": False, "max_age_seconds": 3600,
                "coverage": ["full_document"]
            }
        })
        result = self.cli("verify", "--expect-revision", "5", "--id", "action-legacy", "--input", observation)
        self.assertEqual(result["divergence_type"], "unknown")
        self.assertIn("postcondition quality is insufficient", result["reason"])

    def test_metrics_and_integrity_are_reproducible(self):
        self.add_action("proposal_aligned.json", 3)
        self.cli("verify", "--expect-revision", "4", "--id", "action-aligned",
                 "--input", str(FIXTURES / "observation_file.json"))
        self.add_action("proposal_sqlite.json", 5)
        db = self.work / "metrics.sqlite"
        with sqlite3.connect(db) as connection:
            connection.execute("CREATE TABLE jobs (status TEXT)")
            connection.execute("INSERT INTO jobs VALUES ('completed')")
            connection.commit()
        observation = self.write_json("metrics_observation.json", {
            "verification_source": {
                "kind": "database", "ref": f"sqlite:///{db}",
                "query": "SELECT status FROM jobs",
                "authority": "database", "independence": "independent",
                "mutable_by_executor": False, "max_age_seconds": 3600,
                "coverage": ["rows[0].status", "row_count"]
            },
            "observed_action": "从只读数据库读取完成状态"
        })
        self.cli("verify", "--expect-revision", "6", "--id", "action-sqlite", "--input", observation)
        metrics = self.cli("metrics", "--scenario-id", "deploy-demo", "--k", "2")
        self.assertEqual(metrics["attempts"], 2)
        self.assertEqual(metrics["successful_attempts"], 2)
        self.assertEqual(metrics["pass_power_k"], 1.0)
        check = self.cli("check", "--json")
        self.assertEqual(check["integrity"], "valid")
        self.assertEqual(check["unverified_actions"], [])


if __name__ == "__main__":
    unittest.main()
