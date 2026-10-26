---
name: maintain-consensus-universal
description: Track consequential or multi-step actions from current context through external verification. Use when an auditable action record matters; skip simple reversible tasks that need no traceability.
metadata:
  short-description: Record and verify consequential actions
  skill-version: "0.4"
---

# Maintain Consensus Universal — Skill v0.4

This is a workflow update over the bundled `maintain-consensus-universal` runtime v0.3.1. It does not change the runtime schema or the frozen `maintain-consensus` v0.4 core.

## Choose a path

Skip the ledger for simple, reversible tasks where no action trace or external postcondition is needed. Use the **fast path** for ordinary consequential actions. Use the **full path** only for ongoing commitments, multi-step/high-consequence work, or an explicit audit/evaluation request.

## Fast path

For a new tracked task, keep `universal_ledger.json` in the project/task data, outside this Skill folder. Initialize once, then write context once. For each tracked action:

`proposal + precheck → authorized external action → verify`

For the first action, the usual command sequence is `init → set-context → precheck → verify`. Add `commit` only when there is a real commitment that should constrain later actions. Reuse the same ledger and context for later actions; do not initialize or rewrite unchanged context. Read the latest revision and pass it as `--expect-revision` for each write. Consult the bundled [v0.3.1 usage guide](runtime/maintain-consensus-universal-v0.3.1-2026-10-07/USAGE.md) only when command or JSON details are needed.

## Full path

When the task warrants deeper tracking, record sourced commitments, purpose and `action_delta` response, key-node `trigger_rules`, postcondition quality, and source authority/independence/freshness/coverage. Keep triggers at `record_only` unless an explicitly chosen workflow requires external evidence. Run `check` or `metrics` at review/summary time, not after every write.

Run `scripts/long_horizon_compare.py` only when the user asks to compare the protocol flows or that comparison is the task's objective. It is not part of ordinary action tracking. The bundled [README](runtime/maintain-consensus-universal-v0.3.1-2026-10-07/README.md) gives the experiment scope; do not treat its deterministic scenario as a general model-capability result.

## Keep these guarantees

- `precheck` is diagnostic and non-blocking; it does not authorize or execute an action.
- Verify postconditions and actual actions from the declared external source. Do not treat self-report as external evidence.
- `unknown` is unverified, never success. Report legacy `divergence_type` separately from v0.3.1 `comparison` when both exist.
- Use `aligned`, `justified_revision`, `drift`, and `unknown` only for the specific action and state paths checked.

The runtime uses Python 3 standard library modules. Earlier Skill instructions and package history are preserved in `references/`.
