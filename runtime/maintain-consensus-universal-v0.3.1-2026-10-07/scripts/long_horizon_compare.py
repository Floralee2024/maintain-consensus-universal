#!/usr/bin/env python3
"""Run a deterministic long-horizon comparison.

The baseline consumes context but does not run precheck or external-state
verification. The enhanced path uses the v0.3.1 ledger for every key action,
including optional action-binding fields and key-node triggers.
This is a protocol experiment with an explicit policy, not a claim about a
particular LLM's general ability.
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "scripts" / "universal_agent.py"
SCENARIO = ROOT / "experiments" / "long_horizon_scenario.json"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def call(state, *args):
    completed = subprocess.run([sys.executable, str(AGENT), "--state", str(state), *map(str, args)],
                               text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed: {args}: {completed.stderr.strip()}")
    return json.loads(completed.stdout)


def source_for(path, coverage):
    return {"kind": "artifact", "ref": str(path), "authority": "artifact_store",
            "independence": "independent", "mutable_by_executor": False,
            "max_age_seconds": 3600, "coverage": coverage}


def standard_checks(final=False):
    if final:
        return [
            {"path": "status", "op": "eq", "value": "completed"},
            {"path": "target_state", "op": "eq", "value": "ready"},
            {"path": "effects", "op": "contains", "value": "finalized"}
        ]
    return [
        {"path": "status", "op": "eq", "value": "completed"},
        {"path": "route", "op": "eq", "value": "current"},
        {"path": "effects", "op": "contains", "value": "artifact-written"}
    ]


def proposal(action_id, scenario_id, description, context_version, checks, legal_paths=None, node_type=None):
    result = {"action_id": action_id, "scenario_id": scenario_id,
            "proposed_action": {"description": description, "target": "demo-artifact"},
            "serves_purpose": "让部署行动持续服务当前目标",
            "action_delta_response": "根据当前 action_delta 选择可验证的行动路径",
            "actual_action_source": "external_only",
            "decision_basis": ["goal", "long-horizon-experiment"],
            "evidence_refs": ["experiment:long-horizon-v1"],
            "expected_postcondition": {"description": "外部状态满足部署目标", "checks": checks},
            "legal_paths": legal_paths or [], "context_version": context_version}
    if node_type:
        result["trigger_rules"] = {"node_type": node_type, "precheck": True,
                                    "postcheck": True, "mode": "record_only"}
    return result


def observation(path, coverage, revision_basis=None):
    value = {"verification_source": source_for(path, coverage)}
    if revision_basis:
        value["revision_basis"] = revision_basis
    return value


def run_baseline(scenario):
    records = []
    contexts = {item["step"]: item for item in scenario["contexts"]}
    for item in scenario["steps"]:
        step = item["step"]
        action = item["baseline_action"]
        if step == 1:
            result = item["result"]
        elif step == 2:
            result = item["initial_result"]
        elif step == 3:
            result = {"status": "completed", "route": "legacy", "effects": ["artifact-written"]}
        elif step == 4:
            result = item["result"]
        elif step == 5:
            result = {"status": "completed", "route": "legacy", "effects": ["artifact-written"], "repaired": False}
        else:
            result = {"status": "completed", "route": "legacy", "target_state": "stale", "effects": ["finalized"]}
        claimed_success = result.get("status") == "completed"
        if step == 1:
            ground_truth = result.get("status") == "completed" and result.get("route") == "current"
        elif step in {2, 3}:
            ground_truth = result.get("status") == "completed" and result.get("route") == "current"
        elif step == 4:
            ground_truth = result.get("status") == "completed"
        elif step == 5:
            ground_truth = result.get("status") == "completed" and result.get("repaired") is True
        else:
            ground_truth = result.get("status") == "completed" and result.get("target_state") == "ready"
        records.append({"step": step, "context_version": contexts[1 if step == 1 else 2]["consensus_version"],
                        "selected_action": action, "claimed_success": claimed_success,
                        "ground_truth_success": ground_truth, "result": result})
    return records


def run_enhanced(scenario, work):
    state = work / "enhanced" / "universal_ledger.json"
    context1 = work / "context1.json"
    context2 = work / "context2.json"
    commitment = work / "commitment.json"
    write_json(context1, scenario["contexts"][0])
    write_json(context2, scenario["contexts"][1])
    write_json(commitment, {
        "commitment_id": "external-reality",
        "statement": "关键行动必须以外部真实状态验证",
        "source": {"kind": "artifact", "ref": str(SCENARIO)},
        "scope": {"targets": ["long-horizon experiment"], "forbidden": []},
        "prohibits": [], "requires": ["external source", "postcondition checks"], "status": "active"
    })
    call(state, "init", "--session-id", "long-horizon-enhanced")
    rev = call(state, "set-context", "--expect-revision", 1, "--input", context1)["revision"]
    rev = call(state, "commit", "--expect-revision", rev, "--input", commitment)["revision"]
    records = []

    def execute_action(action_id, step, description, result_value, context_version, checks,
                       legal_paths=None, revision_basis=None, observed_action=None, node_type=None):
        nonlocal rev
        proposal_path = work / f"proposal-{action_id}.json"
        result_path = work / f"result-{action_id}.json"
        observation_path = work / f"observation-{action_id}.json"
        write_json(proposal_path, proposal(action_id, scenario["scenario_id"], description,
                                           context_version, checks, legal_paths, node_type))
        precheck_result = call(state, "precheck", "--expect-revision", rev, "--input", proposal_path)
        rev = precheck_result["revision"]
        result_payload = dict(result_value)
        result_payload.setdefault("action", {"description": observed_action or description, "target": "demo-artifact"})
        write_json(result_path, result_payload)
        coverage = [item["path"] for item in checks]
        write_json(observation_path, observation(result_path, coverage, revision_basis))
        verify_result = call(state, "verify", "--expect-revision", rev, "--id", action_id, "--input", observation_path)
        rev = verify_result["revision"]
        records.append({"step": step, "action_id": action_id, "selected_action": description,
                        "observed_action": verify_result["observed"].get("action"),
                        "precheck": precheck_result["precheck"],
                        "divergence_type": verify_result["divergence_type"],
                        "comparison": verify_result["comparison"],
                        "reason": verify_result["reason"]})
        return precheck_result, verify_result

    first = scenario["steps"][0]
    execute_action("enhanced-1", 1, first["enhanced_action"], first["result"], 1, standard_checks())
    rev = call(state, "set-context", "--expect-revision", rev, "--input", context2)["revision"]
    second = scenario["steps"][1]
    execute_action("enhanced-2-legacy", 2, second["enhanced_initial_action"], second["initial_result"], 2,
                   standard_checks(), [{"path_id": "legacy", "description": "legacy 路径"}], node_type="plan_change")
    execute_action("enhanced-2-current", 2, second["enhanced_revision_action"], second["revision_result"], 2,
                   standard_checks(), [{"path_id": "current", "description": "current 路径"}],
                   {"reason": "precheck 发现 legacy 路径已经被 action_delta 淘汰", "evidence_refs": ["context:step-2"]},
                   node_type="plan_change")
    third = scenario["steps"][2]
    execute_action("enhanced-3", 3, third["enhanced_action"], third["result"], 2, standard_checks())
    fourth = scenario["steps"][3]
    execute_action("enhanced-4", 4, fourth["enhanced_action"], fourth["result"], 2, standard_checks())
    fifth = scenario["steps"][4]
    execute_action("enhanced-5", 5, fifth["enhanced_action"], fifth["result"], 2, standard_checks(),
                   revision_basis={"reason": "step 4 外部状态显示直接修复失败，切换备用修复", "evidence_refs": ["action:enhanced-4"]},
                   observed_action=fifth["enhanced_actual_action"])
    sixth = scenario["steps"][5]
    execute_action("enhanced-6", 6, sixth["enhanced_action"], sixth["result"], 2,
                   standard_checks(final=True), node_type="completion_claim")
    metrics = call(state, "metrics", "--scenario-id", scenario["scenario_id"], "--k", 1)
    check = call(state, "check", "--json")
    final_state = scenario["steps"][-1]["result"]
    return {"records": records, "metrics": metrics, "check": check,
            "final_ground_truth_success": final_state.get("target_state") == "ready"}


def compare(scenario):
    with tempfile.TemporaryDirectory(prefix="universal-long-horizon-") as temp:
        work = Path(temp)
        baseline_records = run_baseline(scenario)
        enhanced = run_enhanced(scenario, work)
    baseline_claimed = sum(item["claimed_success"] for item in baseline_records)
    baseline_true = sum(item["ground_truth_success"] for item in baseline_records)
    baseline_false_positive = sum(item["claimed_success"] and not item["ground_truth_success"] for item in baseline_records)
    enhanced_success = sum(item["divergence_type"] in {"aligned", "justified_revision"} for item in enhanced["records"])
    enhanced_drift = sum(item["divergence_type"] == "drift" for item in enhanced["records"])
    revisions = sum(item["divergence_type"] == "justified_revision" for item in enhanced["records"])
    return {"scenario_id": scenario["scenario_id"], "scenario_description": scenario["description"],
            "baseline_context_only": {
                "steps": len(baseline_records), "claimed_successes": baseline_claimed,
                "ground_truth_successes": baseline_true, "false_positives": baseline_false_positive,
                "revisions": 0, "final_ground_truth_success": baseline_records[-1]["ground_truth_success"],
                "records": baseline_records
            },
            "enhanced_context_precheck_external": {
                "action_records": len(enhanced["records"]), "successful_records": enhanced_success,
                "drift_records": enhanced_drift, "justified_revisions": revisions,
                "final_ground_truth_success": enhanced["final_ground_truth_success"],
                "metrics": enhanced["metrics"], "ledger_check": enhanced["check"],
                "records": enhanced["records"]
            },
            "comparison": {
                "stale_action_caught_by_precheck_or_verification": any(
                    item["action_id"] == "enhanced-2-legacy" and item["divergence_type"] == "drift"
                    for item in enhanced["records"]),
                "failed_action_caught": any(
                    item["action_id"] == "enhanced-4" and item["divergence_type"] == "drift"
                    for item in enhanced["records"]),
                "revision_recorded": revisions == 1,
                "final_outcome_changed": not baseline_records[-1]["ground_truth_success"] and enhanced["final_ground_truth_success"]
            }}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default=str(SCENARIO))
    args = parser.parse_args()
    scenario = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
    print(json.dumps(compare(scenario), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
