# maintain-consensus-universal v0.2

这是一个独立的通用运行层，用来把已有的 `context` / `action_delta`、承诺与依据、行动前一致性检查、外部真实状态读取、后置条件验证、多条合法路径和漂移分类合并到同一个可审计账本中。

它不改写冻结的 maintain-consensus v0.4，也不把人工授权放进默认链路。`precheck` 只记录行动是否需要重述或补充依据；真正的行动由外部系统执行，`verify` 再从文件、产物、SQLite 数据库、工具回执或环境快照读取状态并做后置条件判断。

## 目录

```text
maintain-consensus-universal-v0.2-2026-10-06/
├── README.md
├── protocol_universal_v0_2.json
├── scripts/universal_agent.py
├── fixtures/
└── tests/test_universal_agent.py
```

## 统一记录模型

账本文件固定命名为 `universal_ledger.json`，包含：

| 区域 | 作用 |
|---|---|
| `context` | 当前目标、用途、约束和 `action_delta` |
| `commitments` | 带来源、作用域、禁止项和要求项的承诺 |
| `actions` | 提案、决策依据、证据、预期后置条件、合法路径和验证结果 |
| `events` | 每次变更的事件记录 |
| `integrity_sha256` | 对账本主体做的完整性摘要 |

验证结果只有四类：

- `aligned`：后置条件通过，且行动相同或走了声明的合法替代路径。
- `justified_revision`：行动有变化，但有证据支持的修订依据，且后置条件通过。
- `drift`：后置条件失败，或行动变化没有合法路径或证据支持。
- `unknown`：外部来源不可读，或无法得到真实行动；不会被算作成功。

外部来源中的 `action` / `path_id` 可以作为观测信号，但系统不会接受观测输入里的 `observed_postcondition` 作为事实。后置条件始终对读取到的真实状态执行检查。

输入兼容旧的 `current_goal` 上下文字段，并在账本中归一化为 `goal`。旧式只提供文字描述、没有可执行 `checks` 的后置条件可以被记录和复查，但会被标记为 `insufficient_basis` / `unknown`，不会因为文字相似而误报成功。

## 最小使用流程

```bash
ROOT=/path/to/maintain-consensus-universal-v0.2-2026-10-06
STATE=/tmp/universal-demo/universal_ledger.json

python3 "$ROOT/scripts/universal_agent.py" --state "$STATE" \
  init --session-id demo-001

python3 "$ROOT/scripts/universal_agent.py" --state "$STATE" \
  set-context --expect-revision 1 --input "$ROOT/fixtures/context.json"

python3 "$ROOT/scripts/universal_agent.py" --state "$STATE" \
  commit --expect-revision 2 --input "$ROOT/fixtures/commitment.json"

python3 "$ROOT/scripts/universal_agent.py" --state "$STATE" \
  precheck --expect-revision 3 --input "$ROOT/fixtures/proposal_aligned.json"

# 外部系统在这里执行行动；系统不代替外部工具执行动作。
python3 "$ROOT/scripts/universal_agent.py" --state "$STATE" \
  verify --expect-revision 4 --id action-aligned \
  --input "$ROOT/fixtures/observation_file.json"

python3 "$ROOT/scripts/universal_agent.py" --state "$STATE" check --json
python3 "$ROOT/scripts/universal_agent.py" --state "$STATE" metrics --k 1
```

`--expect-revision` 是并发保护和旧状态检测，不是人工批准。写操作采用临时文件替换和锁文件，账本损坏或版本落后会直接报错。

## 可接入的来源

### 文件 / 产物

`{"kind":"file", "ref":"result.json"}` 或 `{"kind":"artifact", "ref":"build/output.json"}`。JSON 会作为状态对象；非 JSON 文件会变成带文本、字节数和 SHA-256 的状态。

### SQLite

`{"kind":"database", "ref":"sqlite:///tmp/app.sqlite", "query":"SELECT status FROM jobs"}`。只允许 `SELECT`、`PRAGMA`、`EXPLAIN`，连接使用只读模式，结果统一为 `rows`、`columns`、`row_count`。

### 工具回执

`{"kind":"tool", "ref":"receipt.json"}`。工具必须写出 JSON 回执；其中的 `action` 或 `path_id` 可用于区分实际路径，状态仍由后置条件读取和核对。

### 环境

`{"kind":"environment", "ref":"env:DEPLOYMENT_ID"}` 读取环境变量；目录引用会读取目录是否存在和条目列表；JSON 文件会按环境快照读取。

## 后置条件 DSL

每个检查包含 `path`、`op` 和（除 `exists` 外）`value`。支持 `eq`、`neq`、`contains`、`not_contains`、`exists`、`in`、`gte`、`lte`。路径支持点号和列表下标，例如 `rows[0].status`。

## 回归测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖：文件、SQLite、工具回执、环境快照、合法替代路径、证据支持的修订、漂移、来源缺失后的 `unknown`、自报后置条件拒绝、完整性和重复运行指标。
