# 0002. 订阅管理控制台的发布基线权威与可回滚切换

- **Status**: accepted
- **Date**: 2026-09-30
- **Deciders**: 项目所有者、Server Network Assist 维护者

## Context

C201-4090 的 9182 端口由 systemd **用户级**单元 `sna-commercial.service` 提供，同时服务多个入口（校园直连 `10.20.32.13:9182`、ZeroTier `10.85.246.82:9182`、WG 反代 `10.201.250.1:9180/subscription-admin/`、公网反代 `https://whm12.art/subscription-admin/`）。

这套系统的演进方式在历史中形成了一条**统一发布线**：每次变更不修改既有目录，而是新建一个快照目录，再通过 systemd drop-in 把 `WorkingDirectory` 与 `PYTHONPATH` 指向新目录，然后 `daemon-reload` + `restart`。

由此积累出三个必须正视的事实：

1. **发布目录是真正的运行代码，仓库不是。** 线上服务端代码**领先于仓库** —— 它包含 `/client/v1/subscription-preview`、probe-lease、`client_relay_probe.py`、`client_app_routing.py` 等仓库中根本不存在的模块。若按常规思路"把仓库部署上去"，会**静默回退这些端点**。
2. **drop-in 目录只有一个文件生效。** `sna-commercial.service.d/` 下累积了 10 个 `zz*` 前缀的 drop-in，systemd 按**文件名字典序**依次应用，**同名字段后者覆盖前者**，因此只有字典序最后的那个才真正生效。仅凭目录列表无法判断线上跑的是哪一版 —— 必须查 `systemctl show ... -p WorkingDirectory -p Environment`。
3. **数据库只有一个。** 所有发布版本共享 `--data /home/a/.local/share/server-network-assist-commercial/data`（含 5.1 GB 的 `commercial-service.sqlite3`）。发布回滚不会同时回滚数据，因此**任何 schema 变更都必须是回滚安全的**。

## Decision

### 1. 服务端以线上部署树为基线，仓库侧改动向上移植

新的服务端改动以**当前线上发布目录**为基线，把仓库中对应模块的改动移植上去，而不是用仓库整体替换部署树。发布前必须逐项比对基线独有的模块与端点，确认没有被回退。

### 2. 发布 = 新快照 + 新 drop-in；回滚 = 移走 drop-in

- 新发布先 `cp -a` 现有生效目录得到新快照目录，再覆盖改动文件；
- 新 drop-in 命名必须**字典序排在所有既有 drop-in 之后**（当前约定：`zzzzzzzzzzz-<主题>-<日期>.conf`）；
- 不改动 systemd 单元文件本身，不叠加第二个指向同一版本的 drop-in；
- 回滚操作固定为：移走该 drop-in → `daemon-reload` → `restart`。旧发布目录**永不删除、永不原地修改**。

### 3. 切换前必须完成的准备（三项缺一不可）

| 准备项 | 要求 |
|---|---|
| 隔离预检 | 以新发布目录 + **独立数据目录**（`/tmp/...`）在非生产端口起服务，跑完整生命周期验证脚本 |
| 数据备份 | 生产库用 SQLite 在线备份（`.backup`），并对副本做 `PRAGMA quick_check`；**必须用备份 API 而非文件拷贝**，因为库可能处于 WAL 状态 |
| 配置备份 | 整个 `sna-commercial.service.d/` 目录与单元文件复制到带日期的备份目录 |

### 4. 双向回滚演练是上线门（gate），不是可选项

上线后必须**实际执行一次往返**：切回旧 drop-in → 验证服务返回的页面内容与旧文件**字节一致**（`sha256` 比对，比对的是 HTTP 响应而非磁盘文件）→ 再切回新版本并同样验证。未完成往返演练的发布，一律视为未验收。

### 5. schema 变更必须纯增量

新增表、索引、列一律使用 `IF NOT EXISTS` 语义或等价的可重入写法；不做破坏性 DDL。已存在的表**不得**依赖 `CREATE TABLE IF NOT EXISTS` 去修正其结构 —— 那条语句对已存在的表是空操作，必须显式核对线上表结构与新代码的读写假设是否一致（列数、列序、外键目标），否则会写坏数据。

## Consequences

- Positive: 发布与回滚都是**目录切换**，不涉及代码回退编译，回滚窗口在秒级。
- Positive: 旧发布目录完整保留，事故现场可原地复现。
- Positive: 隔离预检把大部分缺陷挡在切换之前。
- Positive: 纯增量 schema 让"回滚应用但不回滚数据"成为安全操作。
- Negative: 发布目录会持续累积，需要定期清理（当前刻意保留全部，未清理）。
- Negative: drop-in 的"字典序最后者生效"是一条**隐式规则**，新成员容易误判生效版本；缓解措施是发布记录中始终写明生效文件名，并以上线探针为准。
- Negative: 数据库共享意味着无法通过回滚发布来回滚一次错误的数据写入；此类事故必须走数据库备份恢复。

## Alternatives considered

### 用仓库整体覆盖部署树

拒绝。会静默回退部署线独有的端点（preview、probe-lease 等），属于功能性倒退，且故障现象隐蔽（客户端调用 404 而非报错）。

### 原地修改生效发布目录

拒绝。失去回滚点，且正在运行的进程与新写入的 `.py` 文件可能不一致（部分模块已被导入缓存）。

### 新增第二个指向新版本的 drop-in 而不移除旧的

拒绝（对同一字段而言）。两个 drop-in 都设置 `WorkingDirectory` 时，实际生效者取决于文件名排序，会让"线上到底跑哪一版"变得不可判定。

### 用文件拷贝备份 5.1 GB 生产库

拒绝。库可能处于 WAL 模式，裸拷贝存在撕裂风险，且 `-wal` 文件容易遗漏。必须用 SQLite 在线备份 API。

### 每次发布前清理历史 drop-in

本次拒绝（保留观察期）。清理会扩大变更面并削弱回滚链条；列为后续独立事项。

## References

- `借网管理面板/handoffs/2026-09-30-订阅管理控制台v2上线.md`
- `借网管理面板/requirements_traceability.md` REQ-NET-057、REQ-NET-062
- `借网管理面板/test_reference.md` TEST-NET-069
- ADR-0003（本次发布引入的访问日志与地址生命周期）
