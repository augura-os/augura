# 开源发布 Checklist（公私分离）

> 目标：公开仓库 = 通用骨架（功能完整可跑）；私有仓库 = 骨架 + 行业方法论。
> **发布动作永远手动执行**，本清单是执行前的核对表。

## 0. 最重要的警告

**当前私有仓库的 git 历史包含全部 IP**（docs 业务知识、真版 prompts、阈值口径、任务卡）。
**禁止**直接把本仓库转为 public —— 历史记录会全部暴露。开源必须**新起仓库推快照**（见 §3）。

## 1. 文件分级

### 公开（进入开源仓库）

- 全部代码骨架：`apps/`、`packages/`、`infra/`、`docker-compose.yml`、`install.bat`
- `apps/api/app/services/ip_pack.py` —— **必须替换为 `ip_pack/oss_ip_pack.py` 占位版**
- 文档：`README.md`（英文，GitHub 默认展示）、`README.zh-CN.md`（中文）、`PRIVACY.md`、`CONTRIBUTING.md`、`LICENSE`、`docs/AGENT_SPEC.md`、`docs/design-principles.md`、本文件
- `.env.example`（不含任何真实密钥——发布前再核对一遍）

### 私有（**永不**进入开源仓库）

私有侧另有一套方法论与数据文件（行业 know-how、在投数据、内部流程文档），
**具体清单、敏感点说明与注入步骤见私有侧 `docs/release-runbook.md`**——
清单本身也是敏感信息（等于告诉读者该去找什么），故不列入本公开文件。

公开版功能完整可跑：骨架、占位 IP pack、缺失私有文件时的降级路径均经过验证。

> 另注：PR/Issue/review/commit 历史属平台层数据——新仓快照天然不含；
> 但**私有仓永不转 public**（历史里的 IP 不会因改可见性而泄漏）。

## 2. 私有部署注入（日常使用）

1. 真版 `ip_pack.py` 放到 `ip_pack/ip_pack.py`（已 gitignore）
2. 取消 `docker-compose.yml` api 服务里 `# - ./ip_pack/ip_pack.py:...` 的注释
3. `docker compose up -d --force-recreate api`
4. docs 私有文件保持 `./docs:/app/docs:ro` 挂载（开源用户无这些文件时 merge_guard 自动降级为空裁决，已在 v0.10 验证）

## 2.5 代码级泄漏点（已于彩排 Phase A 处理完毕）

骨架代码本身也可能带产品标识，README 脱敏只是表面——以下已全部配置化/清除
（2026-08 彩排，PR #41）：

- ~~市场前缀硬编码~~ → `services/markets.py`（settings 键 `market_prefixes`，中性默认 KS_EN,KS_KR；私有部署 DB 覆盖真实值）
- ~~测试与注释中的真实素材名/文件名~~ → 全部替换为 KS_EN/KS_KR + 虚构名（制作人甲-庚）
- ~~一次性数据脚本~~ → 已删除（assign_dnas/backfill_tag_layers，私有仓历史保留）
- README/公开文档：禁止出现真实产品名（统一用 KS_EN / KS_KR 等占位示例）
- 遥测/分析 prompt 中的市场标签示例：通用市场标签（'brazil-pt'）可保留

**每次发布新快照前必跑 §3 的扫描**，0 命中才算过。

> **扫描词表本身也是敏感信息**——它等于告诉读者"该去找什么"。具体词只保存在私有侧
> `docs/release-runbook.md`，本文件（公开）不列任何具体词。

### 2.6 本地工作区不得与公开仓同目录（真实事故面）

**骨架无关的本地产物会随 `git add -A` 一起进公开仓。** 已发生的实例：
`scripts/embedding-e0/`（E0 探测工作区）含真实素材名、真人姓名与项目前缀，
且**不在 `.gitignore` 里**——一次手滑即可公开。已加忽略规则，但根因是位置问题：

**规则**：探测脚本、真实数据导出、临时报告一律放在**公开仓目录之外**
（如 `../augura-local/`），不要图省事放在 `scripts/` 下。放在仓内的东西，
迟早会被 `git add -A`、被 IDE 的"add all"、或被 CI 的打包步骤带走。

## 3. 发布流程（手动）

```bash
# 1) 全新空目录做快照（不带任何历史）
mkdir Augura-public && cd Augura-public && git init
# 2) 从私有仓复制公开级文件（按 §1 清单；用 ip_pack/oss_ip_pack.py 覆盖真版）
# 3) 敏感词与密钥扫描
#    词表从私有侧读（路径见私有侧 release-runbook），不要写在本文件里
#   （本文件是公开的）：
PATTERNS=<私有侧词表路径>
#    grep 必须加 -E（或 -f 读词表）：不加 -E 时 "a|b" 被当成字面量，
#    扫描会永远 0 命中——那等于没有这道关。
grep -rniE -f "$PATTERNS" --include="*.py" --include="*.ts" --include="*.md" .
#    数据文件同样要扫（真实素材名往往先出现在 csv/json 里）
grep -rniE -f "$PATTERNS" --include="*.csv" --include="*.json" --include="*.log" .
#    历史也要扫（只扫工作区扫不到已提交内容）：
git log --all -p | grep -niE -f "$PATTERNS"
git log --oneline | head   # 应只有一条 initial commit
# 4) initial commit → 推到新建的 public 仓库
```

后续同步：只从私有仓向公开仓**单向**同步骨架文件（建议每次发版打 tag 后手工同步，
或用 `git sparse-checkout` 维护一个"公开子集"工作区）。

## 4. 合规确认

- [ ] `LICENSE`（Modified Apache 2.0 + SaaS 限制）已就位
- [ ] `PRIVACY.md` 遥测披露完整（白名单字段、opt-out 路径、匿名实例 ID）
- [ ] README 数据主权章节与遥测实际行为一致（`apps/api/telemetry/` 可审计）
- [ ] 公开版首次启动可跑通：install.bat → Settings 配 key → 上传素材 → 分析 → 图谱
- [ ] `scripts/install.sh` 在干净 Mac 实机跑通（curl 直装 + brew 装 Docker 路径 + `install.command` 双击）
- [ ] 公开版不含任何真实素材名/投放数据/客户信息（测试数据也用虚构名）

## 5. 防复刻的现实预期

- 骨架（FastAPI+React+图谱+队列）本身可被复刻——这是开源的代价，也是换社区与数据积累的筹码
- 真正的护城河不在代码：方法论体系与数据积累全部留在私有侧，
  fork 者拿到的是没有灵魂的躯壳
