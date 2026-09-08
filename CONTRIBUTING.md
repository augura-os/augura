# CONTRIBUTING · Augura 开发流程

> 本文件是开发流程的唯一权威说明。`AGENTS.md` 是项目上下文，本文件是怎么改代码的规矩。

---

## 1. 分支模型（Trunk-based）

- `main` 是唯一长期分支，始终保持可发布状态（CI 绿）。
- 所有改动走 `feature/<scope>-<desc>` 短分支（如 `feature/graph-similar-to`），生命周期不超过几天。
- 合并用 **Squash merge**，合并后删除分支。
- 不直接向 `main` 推送（GitHub 免费版私有仓库无 branch protection，靠纪律；升级 Pro 后开启 required PR + status checks）。

## 2. Commit 规范

```
type(scope): subject

feat(graph): SIMILAR_TO observation-pair edges
fix(api): coerce string list fields from JSON Mode providers
```

- type：`feat` / `fix` / `perf` / `docs` / `chore` / `test` / `refactor`
- AI 代理的提交使用身份 `Kimi Work <kimi-work@local>`
- 一个 commit 做一件事；文档与代码同步的改动可以同 commit

## 3. PR 流程（Review：AI 自检 + 人工确认）

1. **AI 自检**（由 Kimi 执行，全部通过才开 PR）：
   - `pytest`、`vitest run`、`ruff check`、`npm run build` 全绿
   - diff 自审：无无关改动、无调试残留、注释与行为一致
   - 文档同步检查：改了判定树 / DNA / 标签本体 / 流程 → 同步 `docs/` 或本文件
2. **PR 描述**按模板四段式：动机 / 改动点 / 验证 / 回滚方式。
3. **人工确认后合并**。AI 不自行合并 PR。

### 硬性 Review 规则

- **Human > AI**：标签、聚类、合并的一切人工修正优先，且必须留 `edit_logs`。
- 涉及 Creative 边界（判定树 Q1–Q4、DNA 归族）的改动，必须同步更新
  `docs/creative-boundary-rules.md` / `docs/creative-dna-registry.md`，
  需要时在边界规则 §4 追加案例裁决录，**并在 `docs/case-rulings.json` 同步一行**
  （合并守卫的机器可读依据，CI 有一致性测试）。
- 合并守卫：推翻 `case-rulings.json` 中"维持拆分"裁决的合并必须填 `force_reason`，
  edit_logs 标 forced——便捷不等于免判断（案例 13）。
- 不违背 `AGENTS.md §9` 已定产品决策（要改先讨论）。

## 4. 测试

- 后端：`apps/api/tests/`（pytest）。纯逻辑单测 + Postgres 集成测试（独立测试库
  `augura_test` / `augura_migration_test`，不碰生产库；DB 不可达自动 skip）。
- 前端：`apps/web/src/**/*.test.ts`（vitest）。
- 要求：**关键路径覆盖**，不追求覆盖率数字。新功能带关键路径测试；修 bug 先写复现测试。
- 数据库迁移必须可 downgrade，`test_migrations.py` 的 upgrade→downgrade→upgrade 往返是 CI 门禁。

## 5. 版本与回滚

### 版本

- 语义化 tag：`v0.x.y`，每个里程碑打一个，推送远程。
- 历史：`v0.1.0` = 流程建设前的基线；`v0.2.0` = 开发流程落地。

### 回滚手段（按场景选）

| 场景 | 手段 |
|---|---|
| 代码改错 | `git revert` 生成反向 PR，走正常 PR 流程合并（不用 reset --hard 推远程） |
| schema 改错 | `alembic downgrade` 回退迁移（CI 已验证往返） |
| 数据损坏 | 恢复 `backups/` 下最近的 pg_dump（见 §6） |
| 整版回退 | 检出对应 tag 的代码 + 恢复当时备份 |

### 紧急回滚顺序

```
revert 代码（PR） → alembic downgrade → 必要时恢复 pg_dump
```

## 6. 数据备份（破坏性操作前置条件）

```powershell
powershell -ExecutionPolicy Bypass -File scripts/backup_db.ps1
```

以下操作**之前**必须先用上面的命令备份：

- 任何 alembic 迁移（upgrade/downgrade）
- 批量数据回填脚本（如 backfill_*）
- 人工 Merge/Split 批量操作

备份内容：

- Postgres `augura` 库全量 pg_dump
- Neo4j `SIMILAR_TO` 边导出（**这些边只存在于 Neo4j，Postgres 无法重建**）

备份输出到 `backups/`（已 gitignore）。

## 7. CI（GitHub Actions 标准档）

`.github/workflows/ci.yml`，PR + push main 触发：

- backend：ruff → Postgres 16 service → alembic upgrade → pytest
- frontend：npm ci → tsc -b → vitest → vite build

CI 红 = 不允许合并。本地复现 CI 失败的命令与 CI 完全一致（见各 job 的 run 步骤）。
