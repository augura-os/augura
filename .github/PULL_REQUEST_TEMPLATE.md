## 动机

<!-- 为什么做这个改动？关联的问题/待办/文档条目是什么？ -->

## 改动点

<!-- 改了哪些文件/模块，各自做了什么。列出数据库迁移（如有）。 -->

## 验证

<!-- 如何确认改动有效：测试结果（pytest / vitest / build / ruff）、手动验证步骤、截图。 -->

## 回滚方式

<!-- 出问题怎么回退：git revert 本 PR / alembic downgrade 到哪个版本 / 是否需要恢复数据备份。 -->

## 检查清单

- [ ] `pytest`、`vitest`、`ruff check`、`npm run build` 全部通过
- [ ] 涉及判定树 / DNA / 标签本体的改动已同步更新 `docs/` 对应文档
- [ ] 人工 Merge/Split/Rename 或标签治理已留 `edit_logs`
- [ ] 破坏性数据库操作前已运行 `scripts/backup_db.ps1`
- [ ] 改动未违背 `AGENTS.md §9` 已定产品决策
