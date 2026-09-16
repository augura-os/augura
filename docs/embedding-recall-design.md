# Embedding 召回层设计方案（含执行预演）

> 状态：设计稿（评审修订版），未开工。本文只做设计与预演，不含任何代码改动。
> 代码事实均于 2026-09-16 在 main（git tag v0.7.0，commit 1d63fdb）上核实。版本口径统一用 git tag（`apps/api/app/version.py` 的 APP_VERSION 是独立双轨，不作定位依据）。
> 2026-09-16 经独立评审（`docs/embedding-recall-design-review.md`）后修订，修订要点见 §0。

---

## 0. 评审修订（2026-09-16）

设计稿经一轮独立评审：三个 P0 硬伤全部属实并已改正；P1 两条全采纳、两条降级采纳；采纳评审"实测先行"的思路，PR 拆分改为 E0→E1→E2（E3 可选、E4 推迟）。要点：

- **P0-1 缓存挂载点写错**：fastembed 默认缓存在 `/tmp/fastembed_cache`，原方案挂 `/root/.cache/huggingface` 缓存不到任何东西。改为代码内显式 `cache_dir=/app/uploads/.cache/fastembed`——docker-compose 已挂 `./uploads:/app/uploads` 持久卷，Dockerfile/compose 零改动（也不碰 `docs/AGENT_SPEC.md` 的归属红线），见 3.4。
- **P0-2 HF_ENDPOINT 运行时设置无效**：`huggingface_hub.constants.ENDPOINT` 在 import 时绑定一次，进程内 `os.environ` 设置不生效。镜像回退改为"初始化失败时 monkeypatch 常量切 hf-mirror 重试一次"，收口在 embedding 抽象层，见 3.4。
- **P0-3 代表向量均值口径是存量 bug**：`pipeline.py:245-248` 的 `running_mean` 用 `variant_count`（全部 variant 数）而非实际参与均值的向量数；`graph.py:230` 拆族直接拿 `variants[0].embedding` 当代表向量。本方案会把触发条件从"异常"变"日常"，E1 必须修（`creatives` 加 `embedding_count` 迁移 + 回归测试），见 3.3、4.4。
- **P1-7 默认 local 改变存量行为**：`pipeline.py:202-210` 按"embedding 是否存在"直接切换判定通道，回填老素材等于静默接管 attach 判定。改为 **shadow 模式为默认**：embedding 只写入、只进收件箱候选并集，不接管 attach；通道切换做成显式设置项（默认关），见 3.2、3.5。
- **P1-4 阈值跨模型未验证**：召回从"全局 0.80/0.85 阈值"改为 **top-k 预算**（k 为命名常量）；具体阈值等 E0 实测分布再定。评审建议的"四路特征小分类器"在 26 条样本上训不动，归入 backlog（E4），见 4.1、6。
- **P1-5 hub 风险降级采纳**：`representative_embedding` 是 creative 级（1–5 个 variant 均值）不是 DNA 族级，暴露面有限，且 shadow 下 hub 引力没有作用对象。采纳监控（attach 次数分布偏斜指标），medoid/多向量召回列为后续可选，见 3.5、6。
- **P1-6 嵌入文本待定**：`summary+tags` vs `hook+gameplay` 由 E0 对照实验数据拍板，设计不预设，见 4.1。
- 证据类修正：附录 B 注明出处（《批量建族方案-大库版》）、AGENTS.md 断链改指、版本口径统一 git tag、算力口径修正（原稿差 512 倍）、场景 D 补视觉签名成本。详见对应各节。

## 1. 一句话目标

给 Kimi 用户补上**本地 embedding 能力**，让仓库里已经铺好但一直在休眠的 embedding 链路真正亮起来，并把召回从"词汇重叠"升级为"语义相似"——LLM 保持精判，embedding 只做召回与粗排。

> 注：Kimi 是否已有 embeddings 端点**未证实**（官方文档未见，第三方目录有引用，冲突未解）。E0 用真 key 实测 `POST {base_url}/embeddings` 一锤定音；若端点存在，本方案定位从"补能力"改为"离线保底"，provider 后端加国内可直连预设，优先级与默认值据此重算（见 6 PR-E0）。

## 2. 现状盘点：轮子已经造好一半

这个项目不是"从零加 embedding"，而是"给已有空槽位装引擎"：

| 已有设施 | 位置 | 状态 |
| --- | --- | --- |
| embedding 客户端（OpenAI 兼容端点） | `analysis.py:132` `AnalysisService.embed()` | 工作正常，但 `embedding_model=""` 时抛错 → 调用方落文本兜底 |
| 存储列 | `analysis_results.embedding`、`creatives.representative_embedding`、variant embedding，均 JSONB 可空 | 已就绪（E1 需补一次 `creatives.embedding_count` 迁移，见 3.3） |
| 分析管线嵌入点 | `pipeline.py:330-337`：分析后 embed `summary+tags`，异常→ None 落文本兜底 | 失败隔离已是现成模式 |
| 增量代表向量 | `clustering.py:155 running_mean`（族内均值） | 函数已就绪，但调用处 count 口径有存量 bug（P0-3，E1 修，见 3.3） |
| 余弦聚类主路径 | `clustering.py`：`cosine_similarity` + `CLUSTER_THRESHOLD=0.85` + `CLUSTER_MARGIN=0.05` | 已就绪，等真向量 |
| 文本兜底 | `token_similarity`（Jaccard，CJK bigram）+ `TEXT_CLUSTER_THRESHOLD=0.34` | 现行默认 |
| 人工编辑后重嵌 | `routes/assets.py:293` | 已有先例 |
| 设置项 | `ai_embedding_model`（留空=不用 embedding） | 已有 |

**空缺的就一件事：embedding 只能从"服务商端点"来。** Kimi 没有这个端点（待 E0 证实，见 §1 注）→ 目标用户群全部走文本兜底。内部文档《批量建族方案-大库版》附录 B 的实测（B.5：小库上词汇重叠聚不出族、最高相似对仅 0.21）说明文本兜底的天花板就在这儿——embedding 恰好治这个病（语义相似不需要词汇重叠）。

## 3. 核心设计决策

### 3.1 模型：fastembed 跑 `BAAI/bge-small-zh-v1.5`

| 候选 | 体积/维度 | 判断 |
| --- | --- | --- |
| **bge-small-zh-v1.5（推荐）** | 24M 参数，512d，ONNX 约 95MB | C-MTEB 平均 57.82 / 检索 61.77，中文小模型里最强（multilingual-e5-small 为 55.38 / 59.95，且体积 4 倍）；CPU 上单条约 10–30ms；fastembed 官方支持 |
| multilingual-e5-small | 118M，384d | 多语言更广但中文不如 bge-zh；备选 |
| potion-multilingual-128M（Model2Vec） | 128M 静态 | 快但质量略低，生态新；备选 |
| bge-m3 | 568M，~2.2GB | 几百条广告短文本用不上，不上 |

选型：**fastembed**（Qdrant 出品，ONNX 运行时）+ `BAAI/bge-small-zh-v1.5`。理由：不装 torch（镜像 +约 150MB 而非 +2GB）、fastembed 自带模型下载/缓存管理、与 FastAPI 部署形态匹配。维护性已核验：0.8.0（2026-03 发布，Apache-2.0，月下载百万级，纯 Python wheel，重的是 onnxruntime）。

两点保留：① C-MTEB 的优势是"中文文本"上的，我们是**中文团队做英文投放素材**（分析文本中英混合），这个混合分布没有任何 benchmark 覆盖——E0 实测兜底；② 聚类是 s2s 场景，BGE 官方只对 s2p 检索建议指令前缀，**不需要为前缀纠结**。

### 3.2 后端抽象：三态开关 + shadow 默认，不做隐式魔法

新增设置项 `embedding_backend`（settings 表，设置页可配）：

- `off`：不用 embedding，纯文本兜底（现状行为）
- `provider`：走服务商端点（现有 `AnalysisService.embed` 路径，OpenAI 用户）
- `local`：fastembed 本地模型（默认，Kimi 用户）

**默认 `local`，但 shadow 模式为默认行为**：embedding 只写入存储、只进收件箱候选并集（4.1），**不接管 attach 判定**。存量行为是 `pipeline.py:202-210` 按"embedding 是否存在"直接切换判定通道（向量 0.85 / 文本 0.34）——回填老素材等于把它们的归属判定静默从文本通道切到向量通道（评审 P1-7）。因此 attach 通道切换做成独立的显式设置项 `embedding_attach_enabled`（默认关），等 E0 分布数据 + 阈值校准有把握再开。模型没下载成功时自动降级文本兜底（见 3.4），shadow 下默认开不会让任何人变糟。

`AnalysisService.embed()` 改为按 backend 分发：provider 走现有代码，local 走 fastembed 单例（懒加载，首次调用才下载/加载模型）。调用方（pipeline、assets 路由）完全不用改——抽象在 service 层收口。

### 3.3 存储：不上 pgvector，仅补一次小迁移

- 向量继续存现有 JSONB 列，Python 侧暴力余弦（`cosine_similarity` 现成）。量级预演（评审修正后口径）：1 万条素材 × 512 维 × JSONB ≈ 60–70MB；两两比对是 5×10⁷ **对** × 512 维 ≈ 2.6×10¹⁰ 次乘加（约 5×10¹⁰ FLOPs），NumPy 化后 < 1 秒的结论不变。但 `X @ X.T` 一次性全矩阵在 1 万条约 400MB 峰值、5 万条约 10GB——不可行，改**分块矩阵化 + argpartition 取 top-k**（见场景 D）。**十万条以内不需要 pgvector**：5 万条门槛的正确表述是"换分块矩阵化即可，仍不需要 pgvector"。明确不引入新扩展（安装脚本、备份、迁移全都不用动）。
- **E1 有一次迁移（修 P0-3）**：`creatives` 加 `embedding_count` 列，语义 = "实际参与过均值的向量条数"。现状 `pipeline.py:245-248` 的 `running_mean(old, new, variant_count)` 错用全部 variant 数——历史 variant 无向量时 count 偏大、旧均值被过度加权、新向量被稀释；本方案把"embedding 不可用"从异常变成常态，这个漂移会变成日常。修法：`running_mean` 改用 `embedding_count`；拆族路径 `graph.py:230`（现在直接拿 `variants[0].embedding` 当新族代表向量）改走同一口径（成员向量均值 + 记 count）。配回归测试：构造"部分 variant 无 embedding"的族，断言代表向量 = 有向量成员的真实均值。
- **模型一致性**：不同模型维度/语义空间不同（bge-zh 512d vs OpenAI 1536d），混存会让旧向量变毒。做法：settings 记 `embedding_model_active`（当前生效的模型 id）；`embed()` 发现配置模型与记录不一致 → 自动把存量向量视为失效并触发回填（见 4.4），而不是混着用。
- 记为 next（不进本轮）：JSONB → `REAL[]` 迁移（约 70MB → 20MB/万条，解析更快），要动表结构与备份链路，单独立项。

### 3.4 网络与降级（中国网络是一等公民）

fastembed 首次运行从 HuggingFace 下载模型——HF 在国内不可达，这是本方案最大的实际风险。评审核实上游源码后，真实链路比原稿写的更复杂，修订如下：

1. **下载链**：fastembed 对 bge-small-zh-v1.5 配了双源——HF（`Qdrant/bge-small-zh-v1.5`）失败后回退 GCS tar.gz，而 GCS 在国内同样不可达。真实降级链是「官方 HF → hf-mirror → GCS（大概率不通）→ 全失败」，别把 GCS 那一跳误算成有效兜底。
2. **HF 镜像回退**：`HF_ENDPOINT` 环境变量在 `huggingface_hub.constants` **import 时绑定一次**，进程内设置无效（P0-2）。做法：首次初始化失败时 monkeypatch `huggingface_hub.constants.ENDPOINT = "https://hf-mirror.com"` 重试一次，**收口在 embedding 抽象层**，调用方无感。
3. **模型缓存**：代码内显式 `TextEmbedding(cache_dir="/app/uploads/.cache/fastembed")`——docker-compose 已挂 `./uploads:/app/uploads` 持久卷，**Dockerfile/compose 零改动**，容器重建不丢模型（P0-1）。注意 fastembed 缓存与 HuggingFace 缓存（`HF_HOME`）是两回事；`FASTEMBED_CACHE_PATH` 环境变量是等效替代。
4. 全部失败 → 记 warning，该次 embed 返回 None → 文本兜底（与 pipeline 现有的 try/except 模式完全一致）。**分析主流程永远不被 embedding 拖死**。
5. 极端离线场景：fastembed 原生逃生口 `specific_model_path=`——宿主机预下载模型目录，直接指过去，比"手动挂卷"可靠，文档化为手动路径。

### 3.5 只召回，不判定

embedding 的职责严格限定为**召回与粗排**（把 LLM 的候选从 O(N²) 缩到 O(N·k)）。所有"是否同创意/是否同族"的最终判定仍走 LLM 投票 + 人工确认。这与 6 月调研的学界共识（LLMEdgeRefine、TnT-LLM）和 `docs/design-principles.md` 的人工治理红线（P04 Human in the Loop；多代理协作约定另见 `docs/AGENT_SPEC.md`）一致。

**shadow 模式让这条更彻底**：默认配置下 embedding 连 attach 判定都不接管，只影响收件箱候选并集（见 3.2）。

关于评审提出的 hub 风险（均值代表向量"像所有东西"、大族引力）：`representative_embedding` 是 **creative 级**（一个 creative 的 1–5 个几乎相同的 variant 的均值），不是 DNA 族级——均值只平均几条近乎相同的变体，"像所有东西"的程度有限；且 shadow 模式下 embedding 不接管 attach，hub 引力没有作用对象。采纳的是**监控**：E2 加"每个族被 attach 的次数分布"偏斜指标（复用 `intervention_density` 思路，接 review queue 一处展示），偏斜突然变大就是 hub 在作祟；medoid / 多向量召回 / CSLS 惩罚等真有数据支持再做（backlog）。

## 4. 召回层四条改造链路

### 4.1 漏合并召回（missed_merge_scan）——收益最大

现状 `recall_pairs` 用 text_score（名字 Jaccard）+ analysis_score（分析文本）+ 视觉签名。两条语义相同但措辞不同的素材（《批量建族方案-大库版》附录 B 实测：同一题材 13 种机制、词汇零重叠）文本通道必然漏召。

改法：候选生成时加 embedding 通道——对每个 creative 取 representative_embedding 余弦 **top-k**（k 为命名常量，由 LLM 预算反推）进候选池，与现有通道取**并集**。后续测量/裁决流程不变。

**不用全局阈值**（原稿的 0.80 粗召回阈值作废）：BAAI 模型卡明确 bge v1.5 的相似度分布压缩在 [0.6, 1.0]，"不要用别的模型硬编码阈值""能排序就别用阈值"；0.80/0.85 是 OpenAI 类模型的经验值，落到 512 维 bge-zh 上偏松偏紧都不知道（评审 P1-4）。top-k 预算把阈值问题降级成"只影响 k"；若未来确需阈值，取值由 E0 实测分布定（见 6 PR-E0）。

**嵌入文本待定**：现状 `pipeline.py:330` 嵌 `summary+tags`（最泛化的文本，tags 多为通用属性）；漏合并的分析通道用的是 `hook+gameplay`（`missed_merge_scan.py`）。哪个候选质量更好，**E0 对照实验数据拍板**，设计不预设（评审 P1-6）。

### 4.2 聚类自动归入（clustering 主路径）

`CLUSTER_THRESHOLD=0.85` + margin 决策这条路径本来就是为真向量设计的——但 **shadow 默认下它不接管**：attach 仍走文本通道（`TEXT_CLUSTER_THRESHOLD=0.34`），向量通道由显式设置项 `embedding_attach_enabled`（默认关）控制（见 3.2）。

0.85 阈值是按 OpenAI 类模型定的，bge-zh 的最优阈值未经验证；现有 `threshold_calibration` 的网格区间（0.20–0.50）是文本相似度量纲、`MIN_SAMPLES=4` 的标注规模在 26 条库上也撑不住可信校准。做法：**E0 先看真实分布**；E3（可选）用校准机制学 embedding 通道阈值（网格按 E0 分布定、单路余弦最简版）；有把握后再开 attach 开关。评审建议的"四路特征小分类器"（余弦 + 两路 Jaccard + 视觉重叠）在这个样本量上训不动，归入 backlog（E4，与四级信任阶梯同属"等数据"）。

### 4.3 智能建族的分批优化（family_bootstrap）

现状 8 个/批是按名字排序切分的，语义相近的素材被切散，LLM 跨批合并提案的负担重。
改法：分批前先按 embedding 做一轮贪心分组（按代表向量相似度把最像的排进同批），让每批都是语义连贯的候选族胚子。提案质量和"互斥体检"的负担都会改善。库涨到千级以上换 SemDeDup 式分桶（先球面 k-means 分桶、只扫同桶 + 邻桶），当前规模贪心即可。**没有 embedding 时保持现状**（名字排序），互不影响。

### 4.4 存量回填（backfill）

升级后老素材的 embedding 列是 NULL，要补。回填**覆盖三处**：`analysis_results.embedding` / `variants.embedding` / `creatives.representative_embedding`（原稿漏了 `variants.embedding`，而 `graph.py:230` 拆族直接拿它当新族代表向量——漏回填会让拆出来的新族带旧模型的毒向量）。`creatives.representative_embedding` **按成员向量重算均值**（均值与顺序无关），不做 running_mean 增量——否则模型切换时会叠在旧模型的毒向量上；重算同时维护 `embedding_count`（3.3）。两种方式都做：

- **随行回填**：`maybe_consolidate` 周期巩固时顺带补一批（每次 ≤ 50 条，命名常量），几天内自然补完，无需用户操作
- **手动全量**：管理端点 `POST /admin/backfill-embeddings`（或复用设置页一个按钮），同步回填全部，给急性子

回填失败单条跳过，不阻塞。

## 5. 执行预演（五个场景走查）

### 场景 A：全新安装，国内网络（主要用户路径）

`docker compose up` → api 启动（不加载模型，启动时间不变）→ 用户上传第一条视频 → 分析完成 → pipeline 调 `embed()` → fastembed 懒加载：HF 直连失败（约 5–10s 超时）→ 抽象层 monkeypatch 切 hf-mirror.com 重试，下载 95MB（国内约 30s–2min）→ 缓存进 uploads 持久卷 → 嵌入成功，后续素材毫秒级。
**失败分支**：镜像站也不可达（GCS 回退大概率也不通）→ warning 日志 → 该素材走文本兜底，行为和 v0.7.0 完全一致。用户无感知，下次触发自动重试。
**这条链路最差的结局 = 现状，不会更差。**

### 场景 B：v0.7.0 老用户升级

`docker compose pull && up -d` → 新镜像多 ~150MB（onnxruntime 等；本地后端是可选 extra，不装的部署不受影响）→ 一次小迁移（`creatives.embedding_count` 列）→ 默认 `local` + **shadow** 生效 → 首次巩固触发时随行回填（26 条库存一次就补完）→ **attach 判定行为不变**（仍走文本通道），只有漏合并召回的收件箱候选并集多了 embedding 通道。
**验证点**：升级前后 `GET /review/queue` 与自动 attach 行为都不变（shadow 语义保证）；回填后 missed_merge_scan 候选数 ≥ 升级前（并集语义保证只多不少）。

### 场景 C：CI

CI 不下载模型（95MB × 每次跑 = 不可接受）。fastembed 依赖做成**可选 extra**（如 `local-embed`），CI 默认不装 onnxruntime，nightly integration job 单跑真模型；顺手对齐 CI Python 版本（`ci.yml` 用 3.11，`docs/AGENT_SPEC.md` 写 3.12）。embedder 做成接口注入：测试用确定性假 embedder（如按 token 哈希造向量），fastembed 实现只留一个标记为 `slow`/`integration` 的测试且默认 skip。`ruff` + 全量 pytest 不受影响。这条必须在 PR 描述里写清楚，否则 OSS CI 会随机挂。

### 场景 D：库涨到几百/几千条

- embed 计算量：每素材一次（分析时），摊销可忽略
- **向量通道成本**：漏合并扫描两两余弦，1000 条 = 50 万对 × 512 维，NumPy 化后约 1–2 秒，周期巩固里跑完全可接受。但**不一次性 stack 全矩阵**（`X @ X.T` 在 1 万条约 400MB 峰值、5 万条约 10GB）——用**分块矩阵化 + `argpartition` 取 top-k**
- **整轮 scan 的大头不在余弦**（评审指正）：`_visual_signatures` 每次扫描现算（从 MinIO 下载代表视频逐帧算 pHash，代码注释自己写了"百级 creative 分钟级、量级到万再缓存"）+ 两路纯 Python 分词/Jaccard 的 O(N²) 循环。视觉签名缓存表记入后续路线
- 模型缓存不随库增长
- **不需要任何架构变更**；pgvector 的引入门槛改述为"5 万条用分块矩阵化仍足够"（写进文档，到那天再说）

### 场景 E：模型/配置变更事故

用户在 OpenAI 与 Kimi 之间切换、或手动改了 embedding 模型 → `embedding_model_active` 不一致 → 旧向量失效 → 自动触发随行回填重建 → 期间召回通道暂时少一个信号源（文本兜底仍在，attach 判定本就不受影响——shadow）。不会出现"拿 bge 向量和 OpenAI 向量算余弦"的静默错误。

## 6. PR 拆分（评审修订版）

执行顺序：**E0（实测报告）→ 拍板默认值 → E1 → E2 →（可选 E3）**；E4 推迟进 backlog。E0 不走 PR 评审节奏，是本地/仓库内脚本 + 报告；E1/E2 走既定 PR 流程。

### PR-E0 · 实测先行（0.5–1 天，产出数据，不改产品代码）

1. 用真实 Kimi key 实测 `POST {base_url}/embeddings`（一锤定音 Kimi 有无端点；有则方案定位改"离线保底"，provider 后端加国内可直连预设）
2. 本机（或 Docker 内）装 fastembed 拉 bge-small-zh-v1.5，对 26 条真实素材出**相似度分布报告**：0.70/0.75/0.80/0.85/0.90 各档候选对数
3. 对照实验：`summary+tags` vs `hook+gameplay` 两种嵌入文本的候选质量（人工快速标注候选对好坏）
4. 复现 P0-3：构造"部分 variant 无 embedding"的族，确认 count 漂移量
5. 产出：实测报告 `docs/embedding-recall-e0-report.md`（已完成，2026-09-16，关键数据见附录 A）；E1 的默认值（嵌入文本、top-k 的 k、是否开 attach 开关）由它拍板

### PR-E1 · 本地 embedder 基座（约 1–1.5 天）

- fastembed 依赖做成**可选 extra**（CI 默认不装 onnxruntime；nightly integration job 单跑）；顺手对齐 CI Python 版本
- `EmbeddingBackend` 抽象（off/provider/local 三态分发）；`cache_dir=/app/uploads/.cache/fastembed`（零 compose/Dockerfile 改动）；HF 失败 monkeypatch `huggingface_hub.constants.ENDPOINT` 切 hf-mirror 重试一次，收口在抽象层；离线 `specific_model_path` 文档化
- 懒加载单例 + **初始化锁**（`pipeline.py:283 _PIPELINE_SLOTS` 是多线程——onnxruntime 推理线程安全，初始化不是）
- **修 P0-3**：`creatives` 加 `embedding_count` 迁移；`running_mean` 改用它；拆族路径（`graph.py:230`）同口径；回归测试（部分无向量族）
- **shadow 为默认**：embedding 只写入、只进收件箱候选并集，不接管 attach；`embedding_attach_enabled` 显式设置项默认关
- settings：`embedding_backend` 键 + 设置页三态选择 + i18n；`embedding_model_active` 一致性检查（切模型 → 旧向量失效 → 触发回填）
- NumPy 矩阵化余弦与 `clustering.cosine_similarity` 语义交叉断言（长度不等/空/零向量返回 0.0 的行为一致）
- 测试：假 embedder 注入；三态分发；下载失败降级；CI 不触网

### PR-E2 · 召回链路接线（约 1–1.5 天）

- missed_merge_scan embedding 召回通道：**top-k 预算**（k=命名常量），与文本通道并集
- 回填：覆盖 `analysis_results.embedding` / `variants.embedding` / `creatives.representative_embedding` 三处，按成员向量**重算均值**（非增量）；随行分批 + `POST /admin/backfill-embeddings`
- family_bootstrap 按 embedding 预分组分批（千级以上换 SemDeDup 式分桶）
- hub 监控：attach 次数分布偏斜指标（接 review queue 一处展示）
- 视觉签名成本基线测量一次，数据写进 PR 描述
- 测试：召回并集语义、回填幂等、无 embedding 时行为与现状一致

### PR-E3（可选）· 阈值校准（约 0.5–1 天）

- 只做最简版：threshold_calibration 支持 embedding 通道阈值学习，**网格区间按 E0 实测分布定**（不再套 0.20–0.50 的文本量纲），特征保持单路余弦
- 设置页显示 embedding 后端状态（可用/降级中/模型未下载）
- 四路特征分类器、reranker（bge-reranker-v2-m3）写入 backlog，不进本轮

### E4（推迟，backlog）

TnT-LLM 式轻量分类器沉淀（人工确认的族 + keywords 训练 embedding + 线性探针，新素材先过分类器，LLM 只兜低置信）——与四级信任阶梯同属"等数据"backlog，本轮不动。

## 7. 明确不做

- **不上 pgvector / 向量索引**：5 万条以内分块矩阵化足够，避免动部署与备份链路
- **不动任何判定逻辑**：LLM 投票、人工确认、merge_guard 一律不碰；shadow 默认下 embedding 连 attach 通道都不接管，只影响"谁进候选池"
- **不训练分类器、不接 reranker**：26 条样本训不动，归入 backlog（E4）
- **不做视频/图像 embedding**（CLIP 类）：文本 embedding 已覆盖当前痛点，多模态是另一个量级的工程
- **不做自动合并/自动归族的新通道**：召回变好不等于闸门放松，所有自动执行条件维持现状
- **不在 CI 下载模型**
- **不动 REAL[] 存储优化**：记为 next，单独立项

## 8. 风险清单

| 风险 | 预演结论 | 缓解 |
| --- | --- | --- |
| HF 下载失败（国内） | 场景 A 已走查，最差结局=现状 | monkeypatch 镜像回退 + 失败降级 + `specific_model_path` 离线路径 |
| 镜像体积 +150MB | 可接受（对比 torch 方案 +2GB） | fastembed/ONNX 选型 + 可选 extra（不装的部署不受影响） |
| bge-zh 阈值未经验证 | 0.85 是 OpenAI 类模型经验值；bge 分布压缩在 [0.6,1.0]，偏松偏紧未知 | shadow 默认（不接管 attach）+ top-k 预算召回 + E0 实测分布后再定阈值 |
| 代表向量均值口径漂移（存量 bug） | embedding 常态化后触发条件从异常变日常 | E1 修：`embedding_count` 迁移 + 拆族同口径 + 回归测试 |
| hub 引力（均值代表向量） | creative 级均值暴露面有限，shadow 下无作用对象 | E2 attach 次数分布偏斜监控；medoid/多向量召回 backlog |
| 向量模型混存 | 场景 E 已走查 | `embedding_model_active` 一致性检查 + 自动回填 |
| 老库回填期间召回信号波动 | 回填只增不改，文本兜底全程在线，attach 不接管 | 随行分批 + 单条失败跳过 |
| fastembed 依赖维护性 | Qdrant 出品，活跃（0.8.0，2026-03，Apache-2.0，月下载百万级） | 抽象层隔离，换掉它只动一个文件 |
| Kimi 端点若已存在 | 方案定位从"补能力"变"离线保底" | E0 第一件事实测，优先级与默认值据此重算 |

## 9. 开放问题（E0 前需要拍板 / E0 后由数据拍板）

1. **默认模型确认**：bge-small-zh-v1.5（中文优先，C-MTEB 57.82）还是 multilingual-e5-small（多语言优先，55.38）？混合分布无 benchmark 覆盖——先 bge-zh，E0 实测兜底；切换成本就是 3.3 的自动回填。
2. **`embedding_attach_enabled` 何时开**：默认关（shadow），开启时点 = E0 分布数据 + （可选 E3）校准有把握之后。这是修订后唯一真正影响存量行为的开关。
3. **嵌入文本、top-k 的 k**：不再是开放讨论项，**E0 数据拍板**。
4. **回填触发**：随行回填（≤ 50 条/次）和手动端点是否都要？建议都做，成本都不高。

## 附录 A · E0 实测关键数据（2026-09-16，摘自 `docs/embedding-recall-e0-report.md`）

数据基线：main `1d63fdb`（v0.7.0），25 creatives / 26 variants，全部未归族。

- **Kimi `/embeddings` 端点存在**：`POST {base_url}/embeddings` → 200，实际模型 `bge_m3_embed`、1024 维（模型名被服务端忽略，`usage` 为空）。与本地 512 维空间不互通，只能整条通道切换 → 定位"离线保底"，主力仍是本地 fastembed。
- **本地模型直连可用**：fastembed 0.8.0 + bge-small-zh-v1.5 直连 HF 下载 ~22s，缓存后加载 0.58s，单句 8ms，512 维已归一化。**坑**：构造参数是 `model_name=`，误传 `model=` 被 `**kwargs` 静默吞掉回退 en 模型——E1 必须断言加载后的实际模型 id。
- **相似度分布**（variant 级跨 creative 对，n=324）：summary+tags mean 0.7626 / p95 0.8455；hook+gameplay mean 0.7242 / p95 0.8010。≥0.70 档占 76–86% → **全局阈值不可用，top-k 坐实**。≥0.85 档：summary+tags 15 对（4.6%）vs hook+gameplay 1 对。
- **对照结论**：`summary+tags` 尾巴更肥、经 campaign 码弱验证的真换皮对召回更多 → 选 `summary+tags`（与 `pipeline.py:330` 现状一致）。
- **阳性对照偏弱**：唯一同 creative variant 对仅 0.846/0.787 ≈ 跨 creative 分布 p95/p90 → 纯向量分不清"同族"与"同品类"，**shadow-only + LLM 裁决必须维持**。
- **P0-3 漂移复现**：5 variant 仅 3 个有向量时，族中心与真实均值余弦 0.8496（偏 31.83°），有效权重 15:5:4 vs 正确 8:8:8，系统性偏袒最早进族向量。
- **E0 推荐默认值**：嵌入文本 `summary+tags`；`k=5`（命名常量，上限 10）；不开 attach 开关（shadow 默认）；若未来开阈值从 0.85（≈p95）起步。

> 本文引用的"附录 B"（B.5 实测：小库最高相似对 0.21、同一题材 13 种机制词汇零重叠）出自内部文档《批量建族方案-大库版》，非本文件附录。
