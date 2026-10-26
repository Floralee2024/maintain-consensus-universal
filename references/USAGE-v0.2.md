# maintain-consensus-universal v0.2：使用说明

## 1. 准备

运行时只需要 Python 3 标准库。进入本目录后设置两个路径：

```bash
ROOT=/path/to/maintain-consensus-universal-v0.2-2026-10-06
STATE=/tmp/universal-demo/universal_ledger.json
```

账本必须命名为 `universal_ledger.json`。所有写操作都带 `--expect-revision`，用于防止基于旧状态写入；它是并发保护，不是人工审批。

## 2. 初始化账本

```bash
python3 "$ROOT/scripts/universal_agent.py" \
  --state "$STATE" init --session-id demo-001
```

初始化后账本从 `revision: 1` 开始。账本包括 `context`、`commitments`、`actions`、`action_delta`、`events` 和完整性哈希。

## 3. 写入上下文

创建 `context.json`：

```json
{
  "consensus_version": 4,
  "goal": "验证部署产物是否达到目标状态",
  "purpose": "把行动与外部真实状态对应起来",
  "constraints": ["不修改冻结核心"],
  "action_delta": {
    "recent_changes": ["新增外部状态核对"],
    "do_not_reuse": ["只凭模型自报完成"],
    "superseded_bases": ["只看字段是否齐全"]
  }
}
```

写入：

```bash
python3 "$ROOT/scripts/universal_agent.py" \
  --state "$STATE" set-context --expect-revision 1 \
  --input context.json
```

旧版本使用的 `current_goal` 也可以输入，系统会归一化为 `goal` 并保留原字段。

## 4. 写入承诺

创建 `commitment.json`：

```json
{
  "commitment_id": "reality-first",
  "statement": "行动结果必须以可读取的外部状态为依据",
  "source": {"kind": "artifact", "ref": "protocol_universal_v0_2.json"},
  "scope": {
    "targets": ["action verification"],
    "forbidden": ["model self-report"]
  },
  "prohibits": ["只凭模型自报完成"],
  "requires": ["external source", "postcondition check"],
  "status": "active"
}
```

写入：

```bash
python3 "$ROOT/scripts/universal_agent.py" \
  --state "$STATE" commit --expect-revision 2 \
  --input commitment.json
```

## 5. 提交行动提案并执行前检查

行动提案必须包含：

- `action_id`：行动标识；
- `scenario_id`：用于分组统计；
- `proposed_action`：具体行动描述，可附 `target`；
- `decision_basis`：决策依据；
- `evidence_refs`：证据引用；
- `expected_postcondition`：执行后要检查的状态；
- `legal_paths`：允许的替代路径；
- `context_version`：提案基于的共识版本。

示例：

```json
{
  "action_id": "deploy-001",
  "scenario_id": "deploy",
  "proposed_action": {
    "description": "直接写入部署产物",
    "target": "demo-artifact"
  },
  "decision_basis": ["goal", "reality-first"],
  "evidence_refs": ["ticket:123"],
  "expected_postcondition": {
    "description": "部署成功",
    "checks": [
      {"path": "status", "op": "eq", "value": "completed"},
      {"path": "version", "op": "gte", "value": 2}
    ]
  },
  "legal_paths": [
    {"path_id": "direct", "description": "直接写入"},
    {"path_id": "cache", "description": "通过缓存后写入"}
  ],
  "context_version": 4
}
```

执行检查：

```bash
python3 "$ROOT/scripts/universal_agent.py" \
  --state "$STATE" precheck --expect-revision 3 \
  --input proposal.json
```

检查返回 `consistent`、`needs_restatement` 或 `insufficient_basis`。这一步不会执行行动，也不会自动阻断行动。

## 6. 执行外部行动并验证真实状态

外部系统执行行动后，创建观测文件：

```json
{
  "verification_source": {
    "kind": "artifact",
    "ref": "result.json"
  }
}
```

`result.json` 可以包含工具写出的真实状态：

```json
{
  "status": "completed",
  "version": 2,
  "path_id": "cache",
  "action": {"description": "通过缓存后写入", "target": "demo-artifact"},
  "effects": ["artifact-written"]
}
```

验证：

```bash
python3 "$ROOT/scripts/universal_agent.py" \
  --state "$STATE" verify --expect-revision 4 \
  --id deploy-001 --input observation.json
```

系统会从外部来源读取状态，而不是相信观测文件中的自报结果。`observed_postcondition` 字段会被拒绝。

## 7. 外部来源格式

### 文件或产物

```json
{"kind": "file", "ref": "result.json"}
```

JSON 文件会作为状态对象读取；普通文本会记录文本、字节数和 SHA-256。

### SQLite 数据库

```json
{
  "kind": "database",
  "ref": "sqlite:///tmp/app.sqlite",
  "query": "SELECT status, version FROM jobs WHERE id = 1"
}
```

只允许 `SELECT`、`PRAGMA` 和 `EXPLAIN`，数据库以只读模式打开。查询结果统一为 `rows`、`columns` 和 `row_count`，因此后置条件可以使用 `rows[0].status`。

### 工具回执

```json
{"kind": "tool", "ref": "tool_receipt.json"}
```

工具回执必须是 JSON 对象。回执中的 `action` 或 `path_id` 可用来识别实际路径，但状态仍要经过后置条件检查。

### 环境变量、目录和环境快照

```json
{"kind": "environment", "ref": "env:DEPLOYMENT_ID"}
```

也可以把目录或 JSON 环境快照作为 `ref`。目录会读取存在性和条目列表；JSON 快照会按普通状态对象检查。

## 8. 后置条件检查语言

检查格式是 `path`、`op` 和 `value`。支持：

| 操作符 | 作用 |
|---|---|
| `eq` / `neq` | 等于 / 不等于 |
| `contains` / `not_contains` | 包含 / 不包含 |
| `exists` | 字段存在，不需要 `value` |
| `in` | 实际值属于给定列表 |
| `gte` / `lte` | 大于等于 / 小于等于 |

路径支持点号和数组下标，例如 `deployment.status`、`rows[0].status`。

旧式只有文字描述、没有 `checks` 的后置条件可以被记录，但会返回 `insufficient_basis`，验证时归为 `unknown`，不会被判为成功。

## 9. 结果解释

| 结果 | 解释 | 后续动作 |
|---|---|---|
| `aligned` | 行动相同或走合法路径，且后置条件通过 | 可作为成功记录 |
| `justified_revision` | 行动变化，有证据支持修订，且后置条件通过 | 保留修订依据 |
| `drift` | 状态失败，或行动变化没有合法路径/依据 | 复查行动和承诺 |
| `unknown` | 来源不可读、行动未知或缺少可执行检查 | 不算成功，补充验证来源 |

## 10. 查看上下文、指标和账本状态

```bash
python3 "$ROOT/scripts/universal_agent.py" \
  --state "$STATE" context --json

python3 "$ROOT/scripts/universal_agent.py" \
  --state "$STATE" metrics --scenario-id deploy --k 1

python3 "$ROOT/scripts/universal_agent.py" \
  --state "$STATE" check --json
```

`context` 显示当前上下文、有效承诺、最近行动变化和未验证行动。`metrics` 返回 `pass_at_1`、`pass_power_k`、成功数、`unknown` 数和 `drift` 数。`check` 验证账本完整性并列出尚未验证的行动。

## 11. 并发和错误处理

- `--expect-revision` 过期时，写入会失败，需要重新读取当前账本后再决定是否重试；
- 账本完整性摘要不匹配时，系统拒绝继续写入；
- 同一账本同时写入时，锁文件会阻止并发破坏；
- 外部来源暂时不可读时，验证结果为 `unknown`，不会静默降级为 `aligned`；
- `precheck` 发现冲突不会替用户批准或拒绝行动，它只保留冲突记录。

## 12. 运行自带测试

```bash
cd "$ROOT"
python3 -m unittest discover -s tests -v
```

当前测试覆盖文件、SQLite、工具回执、环境快照、合法替代路径、证据支持修订、漂移、来源缺失、旧上下文兼容、自报后置状态拒绝和账本完整性。
