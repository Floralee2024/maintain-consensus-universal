# maintain-consensus-universal v0.3.1：使用说明

## 1. 准备

运行时只需要 Python 3 标准库。进入本目录后设置两个路径：

```bash
ROOT=/path/to/maintain-consensus-universal-v0.3.1-2026-10-07
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
  "source": {"kind": "artifact", "ref": "protocol_universal_v0_3_1.json"},
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
      {"path": "version", "op": "gte", "value": 2},
      {"path": "effects", "op": "contains", "value": "artifact-written"}
    ],
    "quality": {
      "min_checks": 2,
      "required_paths": ["status", "version"],
      "require_terminal": true,
      "require_effect": true
    }
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
    "ref": "result.json",
    "authority": "artifact_store",
    "independence": "independent",
    "mutable_by_executor": false,
    "max_age_seconds": 3600,
    "coverage": ["status", "version", "effects"]
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

### 后置条件质量

系统默认要求至少两个互不重复的检查，且不能全部是 `exists`。可以在 `expected_postcondition.quality` 中进一步声明：

```json
{
  "quality": {
    "min_checks": 3,
    "required_paths": ["status", "version", "effects"],
    "require_terminal": true,
    "require_effect": true
  }
}
```

质量评估会返回 `grade`、`score`、缺失路径和弱点。质量不足时，`precheck` 通常返回 `insufficient_basis`；即使外部状态恰好满足弱检查，验证也只会是 `unknown`。

旧式只有文字描述、没有 `checks` 的后置条件可以被记录，但会返回 `insufficient_basis`，验证时归为 `unknown`，不会被判为成功。

## 9. 来源可信度和覆盖率

验证来源应尽量声明以下字段：

| 字段 | 含义 |
|---|---|
| `authority` | 产生状态的系统或权威主体 |
| `independence` | `independent`、`same_executor`、`self_reported` 或 `unknown` |
| `mutable_by_executor` | 执行者是否能直接修改该来源 |
| `max_age_seconds` | 允许的最大来源年龄 |
| `coverage` | 来源明确覆盖的状态路径，或 `full_document` |

系统还会记录 `read_at`、`modified_at`、`age_seconds`、`freshness_status`、摘要和覆盖缺口。出现以下情况时，系统返回 `unknown`：

- 来源不是独立来源；
- 执行者可以修改来源；
- 来源已经过期，或没有可用的新鲜度界限；
- 后置条件检查的路径不在来源覆盖范围内。

这层检查的是来源条件，不是来源内容的领域语义。来源本身仍需要由接入方选择可信的系统。

## 10. 结果解释

| 结果 | 解释 | 后续动作 |
|---|---|---|
| `aligned` | 行动相同或走合法路径，且后置条件通过 | 可作为成功记录 |
| `justified_revision` | 行动变化，有证据支持修订，且后置条件通过 | 保留修订依据 |
| `drift` | 状态失败，或行动变化没有合法路径/依据 | 复查行动和承诺 |
| `unknown` | 来源不可读、行动未知或缺少可执行检查 | 不算成功，补充验证来源 |

## 11. 查看上下文、指标和账本状态

```bash
python3 "$ROOT/scripts/universal_agent.py" \
  --state "$STATE" context --json

python3 "$ROOT/scripts/universal_agent.py" \
  --state "$STATE" metrics --scenario-id deploy --k 1

python3 "$ROOT/scripts/universal_agent.py" \
  --state "$STATE" check --json
```

`context` 显示当前上下文、有效承诺、最近行动变化和未验证行动。`metrics` 返回 `pass_at_1`、`pass_power_k`、成功数、`unknown` 数和 `drift` 数。`check` 验证账本完整性并列出尚未验证的行动。

## 12. 长程对照测试

运行：

```bash
python3 "$ROOT/scripts/long_horizon_compare.py"
```

实验场景位于 `experiments/long_horizon_scenario.json`，包含六个步骤、一次共识更新、一次过时路径、一次真实失败和一次证据支持的行动修订。输出比较：

- 只有 `context` 时选择了什么行动；
- 基线把多少结果错误地当成成功；
- 增强流程捕获了多少 `drift`；
- 是否记录了 `justified_revision`；
- 最终目标状态是否实现。

该实验是确定性协议测试，不是某个具体模型的泛化能力测试。

## 13. 并发和错误处理

- `--expect-revision` 过期时，写入会失败，需要重新读取当前账本后再决定是否重试；
- 账本完整性摘要不匹配时，系统拒绝继续写入；
- 同一账本同时写入时，锁文件会阻止并发破坏；
- 外部来源暂时不可读时，验证结果为 `unknown`，不会静默降级为 `aligned`；
- `precheck` 发现冲突不会替用户批准或拒绝行动，它只保留冲突记录。

## 14. 运行自带测试

```bash
cd "$ROOT"
python3 -m unittest discover -s tests -v
```

当前测试覆盖文件、SQLite、工具回执、环境快照、合法替代路径、证据支持修订、漂移、来源缺失、后置条件质量不足、来源可信度和覆盖率缺口、旧上下文兼容、自报后置状态拒绝、账本完整性和长程对照实验。

## 5A. 声明行动目的、变化响应和触发规则

在选案、改指标、改变方案、扩大投入或宣布完成时，可以在提案中启用行动绑定字段：

```json
{
  "action_id": "case-001",
  "scenario_id": "case-selection",
  "proposed_action": {"description": "选择能呈现跨阶段变化的企业案例"},
  "serves_purpose": "检验框架能否解释需求、回应和后续行动的连接",
  "action_delta_response": "不再把资料容易获得作为主案例标准",
  "actual_action_source": "external_only",
  "trigger_rules": {
    "node_type": "case_selection",
    "precheck": true,
    "postcheck": true,
    "mode": "record_only"
  },
  "decision_basis": ["goal", "action_delta"],
  "evidence_refs": ["research:case-selection"],
  "expected_postcondition": {
    "description": "外部记录显示已选择目标案例",
    "checks": [
      {"path": "status", "op": "eq", "value": "completed"},
      {"path": "selected_case", "op": "exists"}
    ]
  },
  "legal_paths": [],
  "context_version": 4
}
```

字段规则：

- `serves_purpose` 和 `action_delta_response` 是可选文本。普通旧提案可以省略；启用 `trigger_rules` 后，缺失会记录为 `needs_restatement`，但默认不会硬阻断。
- `actual_action_source` 可取 `external_only`、`external_preferred` 或 `declared_allowed`。默认值是 `external_preferred`，用于兼容 v0.3。
- `comparison` 不应写入提案，系统会在 `verify` 后计算。它与 `divergence_type` 并存：前者看行动绑定完整度，后者看实际行动和后置状态是否符合。
- `trigger_rules` 也可以写成数组。`node_type` 可取 `case_selection`、`metric_change`、`plan_change`、`resource_scale`、`completion_claim`；`mode=external_required` 时，验证必须从外部来源得到实际行动。

`verify` 返回示例：

```json
{
  "divergence_type": "aligned",
  "comparison": "aligned",
  "observed": {
    "actual_action_source": "external_source",
    "action_source_policy": "external_only"
  }
}
```

如果外部状态满足后置条件，但只完成了声明字段的一部分，`comparison` 会是 `partial`；如果外部来源不可读或没有实际行动记录，`comparison` 会是 `unknown`，不会算作成功。
