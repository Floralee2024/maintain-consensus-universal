#!/usr/bin/env python3
"""Unified consensus, commitment, action, and external-state verification ledger.

This is an independent v0.3 layer. It composes the existing context/action_delta
state with commitment provenance, non-blocking action prechecks, postcondition
quality gates, source credibility and coverage records, pluggable external
readers, legal alternative paths, drift classification, and repeated-run
metrics. It does not implement approval or hard action blocking.
"""

import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import uuid


SCHEMA_VERSION = 2
PRECHECK_STATUSES = {"consistent", "needs_restatement", "insufficient_basis"}
DIVERGENCE_TYPES = {"aligned", "justified_revision", "drift", "unknown"}
COMPARISON_TYPES = {"aligned", "partial", "drift", "justified_revision", "unknown"}
SOURCE_KINDS = {"file", "artifact", "database", "tool", "environment"}
SOURCE_INDEPENDENCE = {"independent", "same_executor", "self_reported", "unknown"}
ACTION_SOURCE_POLICIES = {"external_only", "external_preferred", "declared_allowed"}
TRIGGER_NODE_TYPES = {"case_selection", "metric_change", "plan_change", "resource_scale", "completion_claim"}
TRIGGER_MODES = {"record_only", "external_required"}
POSTCONDITION_OPERATORS = {"eq", "neq", "contains", "not_contains", "exists", "in", "gte", "lte"}


class UniversalAgentError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise UniversalAgentError(f"{name} must be non-empty text")
    return value.strip()


def text_list(value, name, required=False):
    if not isinstance(value, list) or (required and not value):
        raise UniversalAgentError(f"{name} must be a {'non-empty ' if required else ''}list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise UniversalAgentError(f"{name} contains empty or non-text entries")
    return [item.strip() for item in value]


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise UniversalAgentError(f"cannot read JSON {path}: {exc}") from exc


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_context(value):
    if not isinstance(value, dict):
        raise UniversalAgentError("context must be an object")
    value = copy.deepcopy(value)
    # v0.1 and the frozen core used current_goal; the universal ledger keeps
    # goal as its canonical name while retaining current_goal for round-trip
    # context views.
    if "goal" not in value and "current_goal" in value:
        value["goal"] = value["current_goal"]
    if "purpose" not in value:
        value["purpose"] = "inherited consensus context"
    version = value.get("consensus_version")
    if type(version) is not int or version < 0:
        raise UniversalAgentError("context.consensus_version must be a non-negative integer")
    text(value.get("goal"), "context.goal")
    text(value.get("purpose"), "context.purpose")
    text_list(value.get("constraints", []), "context.constraints")
    delta = value.get("action_delta", {})
    if not isinstance(delta, dict):
        raise UniversalAgentError("context.action_delta must be an object")
    for key in ("recent_changes", "do_not_reuse", "superseded_bases"):
        text_list(delta.get(key, []), f"context.action_delta.{key}")
    return value


def validate_source(value):
    if not isinstance(value, dict):
        raise UniversalAgentError("source must be an object")
    kind = value.get("kind")
    if kind not in SOURCE_KINDS:
        raise UniversalAgentError(f"source.kind must be one of {sorted(SOURCE_KINDS)}")
    result = copy.deepcopy(value)
    result["ref"] = text(result.get("ref"), "source.ref")
    result["source_id"] = text(result.get("source_id", result["ref"]), "source.source_id")
    result["authority"] = text(result.get("authority", "unknown"), "source.authority")
    independence = result.get("independence", "unknown")
    if independence not in SOURCE_INDEPENDENCE:
        raise UniversalAgentError(f"source.independence must be one of {sorted(SOURCE_INDEPENDENCE)}")
    result["independence"] = independence
    mutable = result.get("mutable_by_executor", None)
    if mutable is not None and type(mutable) is not bool:
        raise UniversalAgentError("source.mutable_by_executor must be boolean or null")
    result["mutable_by_executor"] = mutable
    max_age = result.get("max_age_seconds")
    if max_age is not None and (type(max_age) is not int or max_age < 0):
        raise UniversalAgentError("source.max_age_seconds must be a non-negative integer or null")
    result["max_age_seconds"] = max_age
    coverage = result.get("coverage", [])
    if coverage == "full_document":
        coverage = ["full_document"]
    result["coverage"] = text_list(coverage, "source.coverage")
    if "expected_sha256" in result:
        expected = text(result["expected_sha256"], "source.expected_sha256")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise UniversalAgentError("source.expected_sha256 must be a 64-character hexadecimal digest")
        result["expected_sha256"] = expected.lower()
    if kind == "database":
        result["query"] = text(result.get("query"), "source.query")
        first = result["query"].lstrip().split(None, 1)[0].upper()
        if first not in {"SELECT", "PRAGMA", "EXPLAIN"}:
            raise UniversalAgentError("database source only permits read-only SELECT/PRAGMA/EXPLAIN queries")
    return result


def validate_commitment(value):
    if not isinstance(value, dict):
        raise UniversalAgentError("commitment must be an object")
    allowed = {"commitment_id", "statement", "source", "scope", "prohibits", "requires", "status"}
    unknown = set(value) - allowed
    if unknown:
        raise UniversalAgentError(f"unknown commitment fields: {sorted(unknown)}")
    result = copy.deepcopy(value)
    if "commitment_id" in value:
        result["commitment_id"] = text(value["commitment_id"], "commitment_id")
    result["statement"] = text(value.get("statement"), "statement")
    result["source"] = validate_source(value.get("source"))
    scope = value.get("scope", {})
    if not isinstance(scope, dict):
        raise UniversalAgentError("commitment.scope must be an object")
    result["scope"] = {
        "targets": text_list(scope.get("targets", []), "commitment.scope.targets"),
        "forbidden": text_list(scope.get("forbidden", []), "commitment.scope.forbidden"),
    }
    result["prohibits"] = text_list(value.get("prohibits", []), "commitment.prohibits")
    result["requires"] = text_list(value.get("requires", []), "commitment.requires")
    result["status"] = value.get("status", "active")
    if result["status"] not in {"active", "superseded", "retracted"}:
        raise UniversalAgentError("commitment.status is invalid")
    return result


def validate_postcondition(value):
    if not isinstance(value, dict):
        raise UniversalAgentError("expected_postcondition must be an object")
    result = copy.deepcopy(value)
    result["description"] = text(result.get("description"), "expected_postcondition.description")
    checks = result.get("checks", [])
    if not isinstance(checks, list):
        raise UniversalAgentError("expected_postcondition.checks must be a list")
    normalized = []
    for check in checks:
        if not isinstance(check, dict):
            raise UniversalAgentError("postcondition checks must be objects")
        op = check.get("op")
        if op not in POSTCONDITION_OPERATORS:
            raise UniversalAgentError(f"unsupported postcondition operator: {op}")
        item = copy.deepcopy(check)
        item["path"] = text(item.get("path"), "postcondition.path")
        if op not in {"exists"} and "value" not in item:
            raise UniversalAgentError(f"postcondition {op} requires value")
        normalized.append(item)
    result["checks"] = normalized
    quality = result.get("quality", {})
    if not isinstance(quality, dict):
        raise UniversalAgentError("expected_postcondition.quality must be an object")
    quality_result = copy.deepcopy(quality)
    min_checks = quality_result.get("min_checks", 2)
    if type(min_checks) is not int or min_checks < 1:
        raise UniversalAgentError("expected_postcondition.quality.min_checks must be a positive integer")
    quality_result["min_checks"] = min_checks
    required_paths = quality_result.get("required_paths", [])
    quality_result["required_paths"] = text_list(required_paths, "expected_postcondition.quality.required_paths")
    for key in ("require_terminal", "require_effect"):
        flag = quality_result.get(key, False)
        if type(flag) is not bool:
            raise UniversalAgentError(f"expected_postcondition.quality.{key} must be boolean")
        quality_result[key] = flag
    if "exception_reason" in quality_result:
        quality_result["exception_reason"] = text(quality_result["exception_reason"],
                                                    "expected_postcondition.quality.exception_reason")
    result["quality"] = quality_result
    return result


def validate_proposal(value):
    if not isinstance(value, dict):
        raise UniversalAgentError("proposal must be an object")
    allowed = {"action_id", "scenario_id", "proposed_action", "decision_basis",
               "evidence_refs", "expected_postcondition", "legal_paths", "context_version",
               "serves_purpose", "action_delta_response", "actual_action_source", "trigger_rules"}
    unknown = set(value) - allowed
    if unknown:
        raise UniversalAgentError(f"unknown proposal fields: {sorted(unknown)}")
    result = copy.deepcopy(value)
    if "action_id" in value:
        result["action_id"] = text(value["action_id"], "action_id")
    result["scenario_id"] = text(value.get("scenario_id", "default"), "scenario_id")
    action = value.get("proposed_action")
    if isinstance(action, str):
        action = {"description": action}
    if not isinstance(action, dict):
        raise UniversalAgentError("proposed_action must be text or an object")
    action["description"] = text(action.get("description"), "proposed_action.description")
    result["proposed_action"] = action
    result["serves_purpose"] = text(value["serves_purpose"], "serves_purpose") if "serves_purpose" in value else None
    result["action_delta_response"] = text(value["action_delta_response"], "action_delta_response") if "action_delta_response" in value else None
    source_policy = value.get("actual_action_source", "external_preferred")
    if source_policy not in ACTION_SOURCE_POLICIES:
        raise UniversalAgentError(f"actual_action_source must be one of {sorted(ACTION_SOURCE_POLICIES)}")
    result["actual_action_source"] = source_policy
    result["trigger_rules"] = validate_trigger_rules(value.get("trigger_rules"))
    result["decision_basis"] = text_list(value.get("decision_basis", []), "decision_basis")
    result["evidence_refs"] = text_list(value.get("evidence_refs", []), "evidence_refs")
    if type(value.get("context_version")) is not int or value["context_version"] < 0:
        raise UniversalAgentError("context_version must be a non-negative integer")
    result["expected_postcondition"] = validate_postcondition(value.get("expected_postcondition"))
    paths = value.get("legal_paths", [])
    if not isinstance(paths, list):
        raise UniversalAgentError("legal_paths must be a list")
    result["legal_paths"] = []
    for path in paths:
        if not isinstance(path, dict):
            raise UniversalAgentError("legal_paths entries must be objects")
        result["legal_paths"].append({
            "path_id": text(path.get("path_id"), "legal_path.path_id"),
            "description": text(path.get("description"), "legal_path.description"),
        })
    return result


def validate_revision_basis(value):
    if not isinstance(value, dict):
        raise UniversalAgentError("revision_basis must be an object")
    return {"reason": text(value.get("reason"), "revision_basis.reason"),
            "evidence_refs": text_list(value.get("evidence_refs"), "revision_basis.evidence_refs", required=True)}


def validate_trigger_rules(value):
    """Normalize optional key-node rules without affecting ordinary actions."""
    if value is None:
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise UniversalAgentError("trigger_rules must be an object or list")
    result = []
    allowed = {"node_type", "precheck", "postcheck", "mode"}
    for rule in value:
        if not isinstance(rule, dict):
            raise UniversalAgentError("trigger_rules entries must be objects")
        unknown = set(rule) - allowed
        if unknown:
            raise UniversalAgentError(f"unknown trigger rule fields: {sorted(unknown)}")
        node_type = rule.get("node_type")
        if node_type not in TRIGGER_NODE_TYPES:
            raise UniversalAgentError(f"trigger_rules.node_type must be one of {sorted(TRIGGER_NODE_TYPES)}")
        mode = rule.get("mode", "record_only")
        if mode not in TRIGGER_MODES:
            raise UniversalAgentError(f"trigger_rules.mode must be one of {sorted(TRIGGER_MODES)}")
        precheck_enabled = rule.get("precheck", True)
        postcheck_enabled = rule.get("postcheck", True)
        if type(precheck_enabled) is not bool or type(postcheck_enabled) is not bool:
            raise UniversalAgentError("trigger_rules.precheck and postcheck must be boolean")
        result.append({"node_type": node_type, "precheck": precheck_enabled,
                       "postcheck": postcheck_enabled, "mode": mode})
    return result


def validate_observation(value):
    if not isinstance(value, dict):
        raise UniversalAgentError("observation must be an object")
    allowed = {"verification_source", "observed_action", "path_id", "revision_basis"}
    unknown = set(value) - allowed
    if unknown:
        raise UniversalAgentError(f"unknown observation fields: {sorted(unknown)}")
    result = copy.deepcopy(value)
    result["verification_source"] = validate_source(value.get("verification_source"))
    if "observed_action" in value:
        action = value["observed_action"]
        if isinstance(action, str):
            action = {"description": action}
        if not isinstance(action, dict):
            raise UniversalAgentError("observed_action must be text or an object")
        action["description"] = text(action.get("description"), "observed_action.description")
        result["observed_action"] = action
    if "path_id" in value:
        result["path_id"] = text(value["path_id"], "path_id")
    if "revision_basis" in value:
        result["revision_basis"] = validate_revision_basis(value["revision_basis"])
    return result


def nested_get(value, path):
    current = value
    for part in path.split("."):
        match = re.fullmatch(r"(.+)\[(\d+)\]", part)
        if match:
            part, index = match.group(1), int(match.group(2))
            if not isinstance(current, dict) or part not in current:
                return False, None
            current = current[part]
            if not isinstance(current, list) or index >= len(current):
                return False, None
            current = current[index]
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def evaluate_check(state, check):
    exists, actual = nested_get(state, check["path"])
    op = check["op"]
    expected = check.get("value")
    if op == "exists":
        return exists, {"path": check["path"], "op": op, "actual": actual, "passed": exists}
    if not exists:
        return False, {"path": check["path"], "op": op, "actual": None, "expected": expected, "passed": False}
    if op == "eq":
        passed = actual == expected
    elif op == "neq":
        passed = actual != expected
    elif op == "contains":
        passed = expected in actual
    elif op == "not_contains":
        passed = expected not in actual
    elif op == "in":
        passed = actual in expected
    elif op == "gte":
        passed = actual >= expected
    elif op == "lte":
        passed = actual <= expected
    else:
        passed = False
    return passed, {"path": check["path"], "op": op, "actual": actual, "expected": expected, "passed": passed}


def evaluate_postcondition(state, postcondition):
    if not postcondition["checks"]:
        return {"passed": False, "checks": [], "error": "no executable postcondition checks"}
    checks = []
    for check in postcondition["checks"]:
        try:
            passed, detail = evaluate_check(state, check)
        except (TypeError, ValueError) as exc:
            passed, detail = False, {"path": check["path"], "op": check["op"], "passed": False, "error": str(exc)}
        checks.append(detail)
    return {"passed": all(item["passed"] for item in checks), "checks": checks}


def assess_postcondition_quality(postcondition):
    """Assess whether the declared checks are strong enough to support alignment.

    This is a structural gate, not a semantic proof. It rejects common weak
    forms such as one existence check, duplicate paths, or a check set that
    does not cover paths explicitly required by the proposal.
    """
    checks = postcondition.get("checks", [])
    quality = postcondition.get("quality", {})
    paths = [item["path"] for item in checks]
    unique_paths = set(paths)
    value_checks = [item for item in checks if item["op"] != "exists"]
    weaknesses = []
    if len(checks) < quality.get("min_checks", 2):
        weaknesses.append("too_few_checks")
    if len(unique_paths) < len(paths):
        weaknesses.append("duplicate_check_paths")
    if checks and not value_checks:
        weaknesses.append("existence_only_checks")
    missing_required = [path for path in quality.get("required_paths", []) if path not in unique_paths]
    if missing_required:
        weaknesses.append("required_paths_missing")
    terminal_paths = {"status", "state", "phase", "result.status", "deployment.status"}
    has_terminal = any(path in terminal_paths or path.endswith(".status") or path.endswith(".state") for path in unique_paths)
    effect_paths = {"effects", "result", "version", "revision", "hash", "id", "row_count"}
    has_effect = any(path in effect_paths or any(token in path.lower() for token in ("effect", "version", "revision", "hash", "count")) for path in unique_paths)
    if quality.get("require_terminal") and not has_terminal:
        weaknesses.append("terminal_state_missing")
    if quality.get("require_effect") and not has_effect:
        weaknesses.append("effect_or_identity_missing")
    if not checks:
        weaknesses.append("no_executable_checks")
    if quality.get("exception_reason"):
        weaknesses.append("quality_exception_declared")
    severe = {"too_few_checks", "existence_only_checks", "required_paths_missing", "no_executable_checks"}
    sufficient = not any(item in severe for item in weaknesses) and not quality.get("exception_reason")
    score = 0.0
    if checks:
        score += min(0.5, len(checks) / max(quality.get("min_checks", 2), 1) * 0.5)
    if len(unique_paths) >= 2:
        score += 0.2
    if value_checks:
        score += 0.2
    if not missing_required:
        score += 0.1
    score = round(min(score, 1.0), 3)
    grade = "strong" if sufficient and score >= 0.9 else "adequate" if sufficient else "weak"
    return {"grade": grade, "score": score, "sufficient": sufficient,
            "checks_count": len(checks), "unique_paths": sorted(unique_paths),
            "missing_required_paths": missing_required, "weaknesses": weaknesses,
            "has_terminal_state": has_terminal, "has_effect_or_identity": has_effect}


def action_equal(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return left == right
    return {key: left[key] for key in sorted(left)} == {key: right[key] for key in sorted(right)}


def source_path(source, base_dir):
    candidate = Path(source["ref"])
    if not candidate.is_absolute():
        candidate = Path(base_dir).resolve() / candidate
    return candidate


def source_metadata(source, base, path=None):
    metadata = dict(base)
    metadata.update({
        "source_id": source["source_id"],
        "authority": source["authority"],
        "independence": source["independence"],
        "mutable_by_executor": source["mutable_by_executor"],
        "coverage": source["coverage"],
        "read_at": now(),
    })
    if path is not None:
        stat = path.stat()
        age_seconds = max(0, round(time.time() - stat.st_mtime, 3))
        metadata["modified_at"] = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        metadata["age_seconds"] = age_seconds
        metadata["max_age_seconds"] = source["max_age_seconds"]
        if source["max_age_seconds"] is None:
            metadata["freshness_status"] = "unbounded"
        else:
            metadata["freshness_status"] = "fresh" if age_seconds <= source["max_age_seconds"] else "stale"
    else:
        metadata["age_seconds"] = 0
        metadata["max_age_seconds"] = source["max_age_seconds"]
        metadata["freshness_status"] = "fresh_by_read"
    expected = source.get("expected_sha256")
    if expected and metadata.get("sha256") != expected:
        raise UniversalAgentError("external source digest did not match source.expected_sha256")
    return metadata


def read_file_source(source, base_dir):
    path = source_path(source, base_dir)
    raw = path.read_bytes()
    metadata = source_metadata(source, {
        "kind": source["kind"], "ref": str(path), "bytes": len(raw), "sha256": sha256_bytes(raw)
    }, path)
    try:
        parsed = json.loads(raw.decode("utf-8"))
        return {"value": parsed, "metadata": metadata}
    except (UnicodeDecodeError, ValueError):
        return {"value": {"text": raw.decode("utf-8", errors="replace"), "sha256": metadata["sha256"], "bytes": len(raw)},
                "metadata": metadata}


def read_database_source(source, base_dir):
    ref = source["ref"]
    if ref.startswith("sqlite:///"):
        db_path = Path("/") / ref[len("sqlite:///"):]
    else:
        db_path = source_path({"ref": ref}, base_dir)
    query = source["query"]
    uri = f"file:{db_path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(query)
        rows = [dict(row) for row in cursor.fetchall()]
        columns = [column[0] for column in cursor.description] if cursor.description else []
    return {"value": {"rows": rows, "columns": columns, "row_count": len(rows)},
            "metadata": source_metadata(source, {
                "kind": "database", "ref": str(db_path.resolve()), "query": query,
                "read_mode": "read_only"
            }, db_path)}


def read_tool_source(source, base_dir):
    result = read_file_source(source, base_dir)
    if not isinstance(result["value"], dict):
        raise UniversalAgentError("tool receipt must be a JSON object")
    result["metadata"]["receipt"] = True
    return result


def read_environment_source(source, base_dir):
    ref = source["ref"]
    if ref.startswith("env:"):
        name = ref[len("env:"):]
        return {"value": {"name": name, "present": name in os.environ, "value": os.environ.get(name)},
                "metadata": source_metadata(source, {"kind": "environment", "ref": ref})}
    path = source_path(source, base_dir)
    if path.is_dir():
        return {"value": {"path": str(path), "exists": True, "is_dir": True,
                          "entries": sorted(item.name for item in path.iterdir())},
                "metadata": source_metadata(source, {"kind": "environment", "ref": str(path)}, path)}
    normalized = copy.deepcopy(source)
    normalized["ref"] = str(path)
    return read_file_source(normalized, base_dir)


def read_external_source(source, base_dir):
    try:
        if source["kind"] in {"file", "artifact"}:
            return read_file_source(source, base_dir)
        if source["kind"] == "database":
            return read_database_source(source, base_dir)
        if source["kind"] == "tool":
            return read_tool_source(source, base_dir)
        if source["kind"] == "environment":
            return read_environment_source(source, base_dir)
        raise UniversalAgentError(f"no reader for source kind {source['kind']}")
    except (OSError, sqlite3.Error, UnicodeError, ValueError) as exc:
        raise UniversalAgentError(f"external source could not be read: {exc}") from exc


def source_action(observation, external):
    value = external["value"]
    if isinstance(value, dict):
        for key in ("observed_action", "action"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return {"description": candidate}, "external_source"
            if isinstance(candidate, dict) and candidate.get("description"):
                return copy.deepcopy(candidate), "external_source"
    if "observed_action" in observation:
        return copy.deepcopy(observation["observed_action"]), "declared_observation"
    return None, "missing"


def assess_action_binding(context, proposal):
    """Assess declaration completeness; key-node rules make this visible in precheck."""
    issues = []
    if not proposal.get("serves_purpose"):
        issues.append("missing_served_purpose")
    if not proposal.get("action_delta_response"):
        issues.append("missing_action_delta_response")
    response = (proposal.get("action_delta_response") or "").lower()
    if any(term.lower() in response for term in context["action_delta"].get("do_not_reuse", [])):
        issues.append("action_delta_response_matches_do_not_reuse")
    rules = proposal.get("trigger_rules", [])
    precheck_nodes = [rule["node_type"] for rule in rules if rule.get("precheck")]
    postcheck_nodes = [rule["node_type"] for rule in rules if rule.get("postcheck")]
    external_required_nodes = [rule["node_type"] for rule in rules if rule.get("mode") == "external_required"]
    return {
        "complete": not issues,
        "issues": issues,
        "triggered": bool(rules),
        "precheck_required": bool(precheck_nodes),
        "postcheck_required": bool(postcheck_nodes),
        "precheck_nodes": precheck_nodes,
        "postcheck_nodes": postcheck_nodes,
        "external_required_nodes": external_required_nodes,
        "rules": rules,
    }


def path_covered(path, coverage):
    if "full_document" in coverage or "*" in coverage:
        return True
    return any(path == item or path.startswith(item + ".") or path.startswith(item + "[") for item in coverage)


def assess_source_quality(source, metadata, postcondition):
    required_paths = [item["path"] for item in postcondition.get("checks", [])]
    coverage = metadata.get("coverage", source.get("coverage", []))
    missing_coverage = [path for path in required_paths if not path_covered(path, coverage)]
    weaknesses = []
    if metadata.get("independence") != "independent":
        weaknesses.append("source_not_independent")
    if metadata.get("mutable_by_executor") is not False:
        weaknesses.append("source_mutability_unknown_or_allowed")
    if metadata.get("freshness_status") in {"stale", "unbounded", "unknown"}:
        weaknesses.append("source_freshness_unbounded_or_stale")
    if missing_coverage:
        weaknesses.append("postcondition_paths_outside_source_coverage")
    score = 1.0
    score -= 0.25 if "source_not_independent" in weaknesses else 0
    score -= 0.25 if "source_mutability_unknown_or_allowed" in weaknesses else 0
    score -= 0.25 if "source_freshness_unbounded_or_stale" in weaknesses else 0
    score -= 0.25 if "postcondition_paths_outside_source_coverage" in weaknesses else 0
    score = round(max(score, 0.0), 3)
    sufficient = not weaknesses
    grade = "strong" if sufficient else "weak" if score < 0.5 else "adequate"
    return {"grade": grade, "score": score, "sufficient": sufficient,
            "source_id": metadata.get("source_id"),
            "independence": metadata.get("independence"),
            "mutable_by_executor": metadata.get("mutable_by_executor"),
            "freshness_status": metadata.get("freshness_status"),
            "age_seconds": metadata.get("age_seconds"),
            "max_age_seconds": metadata.get("max_age_seconds"),
            "coverage": coverage, "required_paths": required_paths,
            "missing_coverage": missing_coverage, "weaknesses": weaknesses}


def precheck(context, commitments, proposal):
    reasons = []
    conflicts = []
    delta = context["action_delta"]
    description = proposal["proposed_action"]["description"].lower()
    if proposal["context_version"] != context["consensus_version"]:
        reasons.append("stale_context_version")
    if any(term.lower() in description for term in delta.get("do_not_reuse", [])):
        reasons.append("proposed_action_matches_do_not_reuse")
    quality = assess_postcondition_quality(proposal["expected_postcondition"])
    binding = assess_action_binding(context, proposal)
    if not proposal["decision_basis"] or not proposal["expected_postcondition"]["checks"]:
        reasons.append("missing_action_basis_or_postcondition")
    if not quality["sufficient"]:
        reasons.append("weak_postcondition_quality")
    if proposal.get("trigger_rules"):
        reasons.extend(binding["issues"])
    target = str(proposal["proposed_action"].get("target", "")).lower()
    for commitment in commitments.values():
        if commitment.get("status") != "active":
            continue
        prohibited = [item.lower() for item in commitment.get("prohibits", [])]
        forbidden = [item.lower() for item in commitment.get("scope", {}).get("forbidden", [])]
        if any(item and (item in description or item in target) for item in prohibited + forbidden):
            conflicts.append(commitment["id"])
    if conflicts:
        reasons.append("active_commitment_conflict")
    basis_reasons = {"missing_action_basis_or_postcondition", "weak_postcondition_quality"}
    status = "insufficient_basis" if reasons and all(reason in basis_reasons for reason in reasons) else "needs_restatement" if reasons else "consistent"
    return {"status": status, "reasons": reasons, "conflicts": conflicts,
            "postcondition_quality": quality, "action_binding": binding, "checked_at": now()}


def classify(record, observation, external, postcondition_result, actual_action, action_source,
             postcondition_quality, source_quality):
    if external is None or postcondition_result is None:
        return "unknown", "external observation was unavailable", {"path_match": "unknown"}
    if not postcondition_quality["sufficient"]:
        return "unknown", "postcondition quality is insufficient", {"path_match": "unknown", "postcondition_quality": postcondition_quality}
    if not source_quality["sufficient"]:
        return "unknown", "verification source quality is insufficient", {"path_match": "unknown", "source_quality": source_quality}
    source_policy = record.get("actual_action_source", "external_preferred")
    if source_policy == "external_only" and action_source != "external_source":
        return "unknown", "external_only action source policy was not satisfied", {
            "path_match": "unknown", "action_source": action_source, "required_action_source": source_policy}
    if not postcondition_result["passed"]:
        return "drift", "one or more expected postcondition checks failed", {"path_match": "not_evaluated"}
    if actual_action is None:
        return "unknown", "external state was read but the observed action is unavailable", {"path_match": "unknown"}
    legal_ids = {item["path_id"] for item in record.get("legal_paths", [])}
    observed_path = observation.get("path_id")
    source_value = external["value"]
    if isinstance(source_value, dict) and source_value.get("path_id"):
        observed_path = source_value["path_id"]
    if observed_path and observed_path in legal_ids:
        return "aligned", "postcondition passed on a declared legal path", {"path_match": "declared_legal_path", "action_source": action_source}
    if action_equal(record["proposed_action"], actual_action):
        return "aligned", "proposed action and externally verified postcondition match", {"path_match": "same_action", "action_source": action_source}
    revision = observation.get("revision_basis")
    if revision and revision.get("evidence_refs"):
        return "justified_revision", "action changed with an evidence-backed revision basis and a passing postcondition", {"path_match": "evidence_backed_revision", "action_source": action_source}
    return "drift", "action changed without a legal path or evidence-backed revision basis", {"path_match": "unexplained_change", "action_source": action_source}


def compare_action_binding(record, divergence, actual_action, action_source):
    """Compute the action-binding comparison separately from legacy divergence metrics."""
    if divergence == "unknown":
        return "unknown", "action or verification evidence was not sufficient"
    if divergence == "drift":
        return "drift", "the actual action or postcondition diverged"
    if divergence == "justified_revision":
        return "justified_revision", "the changed action has an evidence-backed revision"
    precheck_record = record.get("precheck", {})
    binding = precheck_record.get("action_binding", {})
    issues = list(binding.get("issues", []))
    if "action_binding" not in precheck_record:
        issues = ["missing_served_purpose", "missing_action_delta_response"]
    if actual_action is None or action_source == "missing":
        return "unknown", "actual action source was unavailable"
    if issues:
        return "partial", "action passed, but the purpose/action_delta declaration is incomplete or conflicting"
    return "aligned", "declared purpose and action_delta response accompany the verified action"


def new_state(session_id):
    return {"schema_version": SCHEMA_VERSION, "session_id": session_id, "revision": 1,
            "context": None, "commitments": {}, "actions": {}, "action_delta": [],
            "events": [], "integrity_sha256": ""}


def seal(state):
    state["integrity_sha256"] = digest({key: value for key, value in state.items() if key != "integrity_sha256"})


def validate_state(state):
    required = {"schema_version", "session_id", "revision", "context", "commitments", "actions", "action_delta", "events", "integrity_sha256"}
    if not isinstance(state, dict) or set(state) != required:
        raise UniversalAgentError("ledger fields differ from schema")
    if state["schema_version"] != SCHEMA_VERSION or not isinstance(state["session_id"], str):
        raise UniversalAgentError("unsupported ledger schema")
    if type(state["revision"]) is not int or state["revision"] < 1:
        raise UniversalAgentError("invalid ledger revision")
    if state["integrity_sha256"] != digest({key: value for key, value in state.items() if key != "integrity_sha256"}):
        raise UniversalAgentError("ledger integrity mismatch")
    if state["context"] is not None:
        validate_context(state["context"])
    if not isinstance(state["commitments"], dict) or not isinstance(state["actions"], dict):
        raise UniversalAgentError("invalid ledger collections")
    for key, item in state["commitments"].items():
        if key != item.get("id"):
            raise UniversalAgentError("invalid commitment id")
    for key, item in state["actions"].items():
        if key != item.get("id") or item.get("precheck", {}).get("status") not in PRECHECK_STATUSES:
            raise UniversalAgentError("invalid action record")
        if item.get("divergence_type") not in ({None} | DIVERGENCE_TYPES):
            raise UniversalAgentError("invalid divergence type")
        if item.get("comparison") not in ({None} | COMPARISON_TYPES):
            raise UniversalAgentError("invalid action comparison")
        if item.get("actual_action_source", "external_preferred") not in ACTION_SOURCE_POLICIES:
            raise UniversalAgentError("invalid actual action source policy")
    return state


def load(path):
    try:
        return validate_state(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise UniversalAgentError(f"cannot read ledger {path}: {exc}") from exc


def atomic_write(path, state):
    seal(state)
    validate_state(state)
    fd, temporary = tempfile.mkstemp(prefix=".universal-agent-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(path):
    lock = path.with_suffix(path.suffix + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise UniversalAgentError(f"ledger is locked: {lock}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "created_at": now()}))
        yield
    finally:
        lock.unlink()


def context_view(state):
    return {"session_id": state["session_id"], "revision": state["revision"],
            "context": state["context"],
            "active_commitments": [item for item in state["commitments"].values() if item.get("status") == "active"],
            "recent_action_delta": state["action_delta"][-10:],
            "unverified_actions": [item["id"] for item in state["actions"].values() if item.get("divergence_type") is None]}


def mutate(state, args):
    if args.command == "set-context":
        context = validate_context(read_json(args.input))
        state["context"] = context
        state["action_delta"].append({"at": now(), "type": "context_updated", "consensus_version": context["consensus_version"], "summary": "context/action_delta snapshot updated"})
        return {"consensus_version": context["consensus_version"]}
    if args.command == "commit":
        commitment = validate_commitment(read_json(args.input))
        identifier = commitment.get("commitment_id") or f"c{len(state['commitments']) + 1:04d}"
        if identifier in state["commitments"]:
            raise UniversalAgentError(f"commitment already exists: {identifier}")
        commitment["id"] = identifier
        commitment["created_at"] = now()
        state["commitments"][identifier] = commitment
        state["action_delta"].append({"at": now(), "type": "commitment_added", "commitment_id": identifier, "summary": commitment["statement"]})
        return {"commitment_id": identifier}
    if args.command == "precheck":
        if state["context"] is None:
            raise UniversalAgentError("set context before precheck")
        proposal = validate_proposal(read_json(args.input))
        identifier = proposal.get("action_id") or f"a{len(state['actions']) + 1:04d}"
        if identifier in state["actions"]:
            raise UniversalAgentError(f"action already exists: {identifier}")
        result = precheck(state["context"], state["commitments"], proposal)
        record = {"id": identifier, **proposal, "precheck": result,
                  "postcondition_quality": result["postcondition_quality"],
                  "observed": None, "divergence_type": None, "divergence_reason": None,
                  "comparison": None, "comparison_reason": None,
                  "created_at": now(), "verified_at": None, "history": []}
        state["actions"][identifier] = record
        state["action_delta"].append({"at": now(), "type": "action_prechecked", "action_id": identifier,
                                      "precheck": result["status"], "summary": proposal["proposed_action"]["description"]})
        return {"action_id": identifier, "precheck": result}
    if args.command == "verify":
        if args.id not in state["actions"]:
            raise UniversalAgentError(f"unknown action: {args.id}")
        observation = validate_observation(read_json(args.input))
        record = state["actions"][args.id]
        try:
            external = read_external_source(observation["verification_source"], Path(args.input).resolve().parent)
            postcondition = evaluate_postcondition(external["value"], record["expected_postcondition"])
            postcondition_quality = record.get("postcondition_quality") or assess_postcondition_quality(record["expected_postcondition"])
            source_quality = assess_source_quality(observation["verification_source"], external["metadata"], record["expected_postcondition"])
            actual_action, action_source = source_action(observation, external)
            divergence, reason, classification = classify(record, observation, external, postcondition, actual_action, action_source,
                                                          postcondition_quality, source_quality)
            comparison, comparison_reason = compare_action_binding(record, divergence, actual_action, action_source)
            observed = {"source": external["metadata"], "state": external["value"], "action": actual_action,
                        "action_source": action_source, "actual_action_source": action_source,
                        "action_source_policy": record.get("actual_action_source", "external_preferred"),
                        "postcondition": postcondition,
                        "postcondition_quality": postcondition_quality, "source_quality": source_quality,
                        "classification": classification, "comparison": comparison,
                        "comparison_reason": comparison_reason}
        except (UniversalAgentError, OSError, sqlite3.Error, ValueError) as exc:
            divergence, reason = "unknown", str(exc)
            comparison, comparison_reason = compare_action_binding(record, divergence, None, "missing")
            observed = {"source": observation.get("verification_source"), "error": str(exc)}
        record["observed"] = observed
        record["divergence_type"] = divergence
        record["divergence_reason"] = reason
        record["comparison"] = comparison
        record["comparison_reason"] = comparison_reason
        record["verified_at"] = now()
        record["history"].append({"at": now(), "operation": "verify", "divergence_type": divergence,
                                   "comparison": comparison, "reason": reason})
        state["action_delta"].append({"at": now(), "type": "action_verified", "action_id": args.id,
                                      "divergence_type": divergence, "comparison": comparison, "summary": reason})
        return {"action_id": args.id, "divergence_type": divergence, "comparison": comparison,
                "comparison_reason": comparison_reason, "reason": reason, "observed": observed}
    raise UniversalAgentError(f"unsupported mutation: {args.command}")


def metrics(state, scenario_id=None, k=1):
    records = [item for item in state["actions"].values() if scenario_id is None or item.get("scenario_id") == scenario_id]
    successful = [item for item in records if item.get("divergence_type") in {"aligned", "justified_revision"}]
    result = {"scenario_id": scenario_id, "attempts": len(records), "successful_attempts": len(successful),
              "pass_at_1": (len(successful) / len(records)) if records else None,
              "unknown_attempts": sum(item.get("divergence_type") == "unknown" for item in records),
              "drift_attempts": sum(item.get("divergence_type") == "drift" for item in records)}
    groups = {}
    for item in records:
        groups.setdefault(item.get("scenario_id", "default"), []).append(item)
    eligible = 0
    passed = 0
    for items in groups.values():
        if len(items) < k:
            continue
        eligible += 1
        if all(item.get("divergence_type") in {"aligned", "justified_revision"} for item in items[:k]):
            passed += 1
    result["pass_power_k"] = (passed / eligible) if eligible else None
    result["pass_power_k_k"] = k
    return result


def parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, help="canonical universal_ledger.json path")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--session-id", required=True)
    for command in ("set-context", "commit", "precheck"):
        item = sub.add_parser(command)
        item.add_argument("--expect-revision", type=int, required=True)
        item.add_argument("--input", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--expect-revision", type=int, required=True)
    verify.add_argument("--id", required=True)
    verify.add_argument("--input", required=True)
    view = sub.add_parser("context")
    view.add_argument("--json", action="store_true")
    stat = sub.add_parser("metrics")
    stat.add_argument("--scenario-id")
    stat.add_argument("--k", type=int, default=1)
    check = sub.add_parser("check")
    check.add_argument("--json", action="store_true")
    return parser


def execute(args):
    path = Path(args.state).resolve()
    if path.name != "universal_ledger.json":
        raise UniversalAgentError("canonical file must be named universal_ledger.json")
    if args.command == "init":
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked(path):
            if path.exists():
                raise UniversalAgentError("universal ledger already exists")
            state = new_state(text(args.session_id, "session_id"))
            atomic_write(path, state)
        return {"state": str(path), "session_id": state["session_id"], "revision": 1}, 0
    state = load(path)
    if args.command == "context":
        value = context_view(state)
        return value if args.json else json.dumps(value, ensure_ascii=False, indent=2), 0
    if args.command == "metrics":
        if args.k < 1:
            raise UniversalAgentError("k must be at least 1")
        return metrics(state, args.scenario_id, args.k), 0
    if args.command == "check":
        return {"state": str(path), "revision": state["revision"], "integrity": "valid",
                "commitments": len(state["commitments"]), "actions": len(state["actions"]),
                "unverified_actions": context_view(state)["unverified_actions"]}, 0
    with locked(path):
        state = load(path)
        if state["revision"] != args.expect_revision:
            raise UniversalAgentError(f"stale revision: expected {args.expect_revision}, current {state['revision']}")
        result = mutate(state, args)
        state["revision"] += 1
        state["events"].append({"at": now(), "operation": args.command, "revision": state["revision"], **result})
        atomic_write(path, state)
        result.update({"state": str(path), "revision": state["revision"]})
        return result, 0


def main(argv=None):
    try:
        result, code = execute(parser().parse_args(argv))
        print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
        return code
    except (UniversalAgentError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
