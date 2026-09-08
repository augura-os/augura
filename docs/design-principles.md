# Augura 设计原则（Design Principles）

**版本**：v1.0（2026-07-22）
**来源**：产品与 GPT 的创意智能系统系列讨论（产品宪法对话），经与现有实现比对蒸馏
**定位**：产品宪法。功能会迭代，本文件的原则不变。与 `AGENTS.md`（工程上下文）、
`docs/creative-boundary-rules.md`（Creative 判定操作手册）配合使用。

---

## 1. 产品使命

> **让每一条广告素材，不再只是一次性的投放资产，而成为团队可积累、可复用、可推理、可持续进化的创意知识。**

Augura 不是素材管理工具，是 **Creative Intelligence System**。它回答的不是
"素材放在哪里"，而是：为什么这个素材能跑？为什么它不能裂变？下一条应该做什么？

## 2. 不可协商的设计原则

| # | 原则 | 含义 | 现状落点 |
|---|---|---|---|
| P01 | **Creative First** | 素材(Video/Asset)只是 Creative 的一次实验，不是知识的最小单位 | ✅ 已实现（Creative→Variant→Asset 四层） |
| P02 | **Knowledge First** | 一切功能最终沉淀知识，不是数据 | 🟡 部分（edit_logs、DNA 文档；Insight/Knowledge 未建） |
| P03 | **AI Native** | AI 是产品的运行核心，用户不应感知"哪里用了 AI" | ✅ 方向一致（上传即自动分析，人工只做确认） |
| P04 | **Human in the Loop** | AI 永远只有建议权，一切人工修正留痕并反馈 AI | ✅ edit_logs + confidence 复核徽章 |
| P05 | **Explainable AI** | AI 必须解释"为什么"，不允许"AI 觉得" | 🟡 部分（analysis 有 hook/conflict 解释；Recommendation 未建） |
| P06 | **Immutable Data** | 原始数据不可改，一切修改有审计 | ✅ performances.raw 原样保存 + edit_logs |
| P07 | **Graph Driven** | 数据组织方式是图，不是文件夹/树 | ✅ Neo4j + React Flow 首页 |
| P08 | **Evolution over Management** | 产品推动创意演化，不是管理素材 | 🔲 待建（裂变关系 DERIVED_FROM 未建） |

## 3. 领域模型：对话愿景 ↔ 现状映射

对话最终领域模型（V1 收敛版）：

```
Audience ← Hypothesis → Pattern → Creative → Variant → Video → Experiment → Insight → Knowledge
```

现状实现（26 素材 / 21 Creative 的 MVP）：

```
(DNA 文档级) → Creative → Variant → Asset(Video) → Performance(事实表)
     ↑              ↑            ↑           ↑              ↑
  ≈Pattern 层    ✅ 已建      ✅ 已建      ✅ 已建      ✅ 已建（raw+匹配）
```

| 对话概念 | 现状 | 差距与 V1 决策 |
|---|---|---|
| **Hypothesis**（创意假设） | 无 | 暂缓。素材量不足以验证假设，先靠人工创意方向（见 §5） |
| **Pattern**（创意模式） | DNA 登记表（文档级，12 家族） | **最接近**。DNA = 钩子原型×机制×叙事，本质就是 Pattern。V1 决策：素材/DNA 增长到触发条件后建表（见 AGENTS.md §8） |
| **Audience**（受众） | 仅 market 标签（latam-pt/es） | 暂缓。当前只投巴西/拉美两市场，Audience 维度启用时机=多市场扩量 |
| **Experiment**（受控实验） | 无（只有素材级投放数据） | 暂缓→轻量。V1 不做对照实验结构，用 Creative 内 Variant 间数据对比近似（前贴效果对比已在用） |
| **Insight**（数据洞察） | 无（沉淀在 docs 案例裁决录） | 轻量落地候选：docs 已有人工 Insight（如"V2 竖版 Roas 更优"），可先保持文档级 |
| **Knowledge** | 边界规则 + DNA 文档本身就是 | ✅ 文档级已存在（判定树、钩子原型库、换皮因子、案例裁决录） |
| **Design Intent**（设计意图） | analysis.hook/conflict 隐含 | 暂缓。素材量不足，推理可信度低 |
| **Causal Engine**（因果引擎） | 无 | 明确 V2+。需要 Experiment 结构先行 |
| **RFC 文档体系** | docs/ 三份核心文档 | 暂缓重构。现有文档已承担"宪法"职能，体系稳定后再改名 |

## 4. 与现状的关键冲突点（需人工裁决）

以下 4 处对话主张与现有实现存在实质分歧，见本节末尾的裁决记录。

- **C1 标签自由度**：对话要求封闭 Ontology 字典（禁止自由 tag）；现状是"AI 自由生成 +
  五层本体治理 + 人工确认"的半开放模式（90 标签已分层）。
- **C2 判定树情绪维度**：对话把"情绪目标变化"列为新 Creative 的判定条件；
  现判定树 Q1–Q4 未显式包含 Emotion（钩子原型隐含了部分情绪）。
- **C3 Composition 八要素**：对话要求 Hook/Gameplay/Goal/Emotion/Reward/Presentation/
  Narrative/Commercial 八组件；现分析 schema 缺 **Goal / Narrative / Commercial** 三个显式字段。
- **C4 评分 → 推荐**：对话否定 Score，要求 KEEP / ITERATE / PAUSE / ARCHIVE 四态推荐；
  现状无任何推荐层。

### 裁决记录（2026-07-22 已裁决）

- C1：**维持半开放**。AI 自由生成 + 五层本体治理 + 人工确认；封闭字典在素材量小的阶段会漏掉新概念。
- C2：**暂不加情绪维度**。钩子原型（H01–H17）已是冲突+情绪的复合体，26 素材无误判案例，素材量上来再评估。
- C3：**暂不拆八要素**。现有 hook/conflict/gameplay/reward 已覆盖分析需求，goal/narrative/commercial 待素材量增长后再拆。
- C4：**首个开发任务 = DNA（Pattern 层）建表迁移**，推荐引擎（KEEP/ITERATE/PAUSE/ARCHIVE）排在其后。

## 5. 已确认的方向性结论（无需裁决）

1. **"创意树"概念废弃**，统一称 Creative Graph（已在用）。
2. **角色/颜色/字幕/奖励/画幅/前贴变化 ≠ 新 Creative**——与边界规则换皮因子完全一致，已落地。
3. **AI 不打分，给建议**——对话四态推荐与现有"人工判定树裁决"工作流兼容，落地时直接采用。
4. **AI 输出必须带 confidence**，人工修正即训练数据——已落地。
5. **本地私有化部署 + 20 人以内团队**为 V1 边界；RBAC/多 workspace 暂缓。
6. **Metric Engine / 向量库 / Opportunity Agent / Generation 层** 均为 V2+，当前不建。

## 6. 开发任务排序（C4 裁决后）

1. **DNA（Pattern 层）建表迁移**（已定为首任务）：文档级 12 家族升级为系统层——
   Postgres `creative_dnas` 表 + `creatives.dna_id` + Neo4j `(:CreativeDNA)-[:HAS_CREATIVE]`，
   SQL 草案见 `creative-dna-registry.md` §4。
2. ~~**Creative 级 Recommendation 引擎（规则版）**~~（✅ 2026-07-22 完成：R1–R9 规则引擎 +
   `GET /creatives/recommendations` + Graph 首页"今日建议"面板）——基于 performance + DNA + 观察对，
   对每个 Creative 输出 KEEP / ITERATE / PAUSE / ARCHIVE 建议及理由（P05 可解释）。
3. Composition 八要素补齐（C3 已裁决暂缓，触发条件：素材量显著增长后重议）。
4. ~~**D13 + 纯建造加注裂变方案**~~（✅ 2026-07-23 完成：`docs/creative-directions-加注裂变-20260723.md`，
   6 个方向含判定标准；按 D13-A/PB-A 语言裂变 → D13-B/PB-C 前贴 → 二期道具/时长推进，
   回流闭环走 derivations 连边 + 收件箱判定）
