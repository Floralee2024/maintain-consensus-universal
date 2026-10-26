---
name: maintain-consensus-universal
description: Track whether an updated shared understanding changes a concrete action, then verify the outcome against an external state source. Use the bundled v0.3.1 runtime for auditable action records and action-purpose binding; do not use it as a substitute for ordinary goal alignment or as an approval gate.
metadata:
  short-description: Record and verify consensus-linked actions
---

# Maintain Consensus Universal

Use this skill when a task needs a traceable link from current context and commitments to a concrete action, and then to an externally observed result. It packages the runnable `maintain-consensus-universal` v0.3.1 runtime as an independent extension to the frozen `maintain-consensus` v0.4 core.

## Runtime location

The current bundled program is under `runtime/maintain-consensus-universal-v0.3.1-2026-10-07/`. It uses only the Python 3 standard library. Read its [README](runtime/maintain-consensus-universal-v0.3.1-2026-10-07/README.md) and [usage guide](runtime/maintain-consensus-universal-v0.3.1-2026-10-07/USAGE.md) for the current schema and commands. The v0.2 package is retained beside it for compatibility and version history.

Keep each task's ledger outside the shared Skill directory and name it `universal_ledger.json`. Initialize it with a task-specific session ID. For every write, pass `--expect-revision` using the ledger's current revision; after a successful write, use the returned revision for the next one. This protects against stale or concurrent writes; it is not human approval.

## Workflow

1. Record the task's goal, purpose, constraints, consensus version, and meaningful `action_delta` in a context JSON. Preserve the user's actual goal; do not turn an assumption into a confirmed commitment.
2. Initialize the ledger, then write the context and any scoped commitments with their source, prohibited bases, and required conditions.
3. Create a proposal for one concrete action. Include its scenario and action IDs, decision basis, evidence references, executable expected postcondition checks, and any legitimate alternate paths. Run `precheck` before the external action.
4. Treat `consistent`, `needs_restatement`, and `insufficient_basis` as recorded diagnostic statuses. `precheck` is non-blocking: it neither authorizes nor executes the action. Carry out any external action through the appropriate system and only within the authorization already given for that action.
5. For key decisions where it changes the record, bind the proposal to its purpose with `serves_purpose`, say how it responds to the latest `action_delta` with `action_delta_response`, and select an `actual_action_source` policy. Use `trigger_rules` only for the decision nodes that matter; the default `record_only` mode records checks without becoming an approval gate. `comparison` is computed by the program; do not populate it in the proposal.
6. Verify by pointing the ledger at the external result: a file/artifact, read-only SQLite query, JSON tool receipt, environment variable, directory, or JSON environment snapshot. Do not submit self-reported postconditions as evidence. Set source authority, independence, mutability, age, and coverage metadata where available.
7. Read both the legacy `divergence_type` and the newer action-binding `comparison` when present. Report `unknown` as unverified, never as success. Keep claims limited to the action and state paths actually checked.

For full command syntax, revision examples, source formats, postcondition quality settings, source-quality fields, action-binding policies, and trigger rules, follow the bundled [v0.3.1 usage guide](runtime/maintain-consensus-universal-v0.3.1-2026-10-07/USAGE.md). Use the bundled [introduction](runtime/maintain-consensus-universal-v0.3.1-2026-10-07/INTRODUCTION.md) for design rationale. Earlier v0.2 and v0.3 source documents are kept under `references/`.

## Result meanings

- `aligned`: the external postcondition passed and the action matched the proposal or followed a declared legal path.
- `justified_revision`: the action changed, the change has supporting evidence, and the external postcondition passed.
- `drift`: the external postcondition failed, or the changed action lacks a declared legal path or evidence-backed reason.
- `unknown`: the source/action could not be verified or the checks were not executable. This is not a pass.

`comparison` adds a separate action-binding result: `aligned`, `partial`, `drift`, `justified_revision`, or `unknown`. It records whether the proposed action is tied to the purpose and latest change and whether the source policy provides the actual action. It does not replace `divergence_type`; legacy proposals can retain an `aligned` divergence result while having a `partial` binding comparison.

These results describe a recorded action and its observed state. The bundled long-horizon runner is a deterministic protocol comparison, not evidence of general model capability, causal effect, or practical improvement.

## Version boundary

Use v0.3.1 for new work. It adds postcondition-quality checks, verification-source quality and coverage checks, a long-horizon process comparison, and optional action-purpose binding and key-node triggers. The bundled v0.2 runtime remains available for old ledgers and compatibility. Read [version history and evidence status](references/v0.3-documentation-status.md) before making claims across versions. The package includes tests, but do not report them as passing unless they have actually been run.
