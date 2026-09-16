# 《Embedding 召回层设计方案》审核意见

> 审核对象：`docs/embedding-recall-design.md`（设计稿，未开工）
> 审核范围：公开库 `augura-os/augura`（main）+ 本地 checkout 上的源码事实 + 外部官方文档/论文核验
> 审核时间：2026-09-16
> 审核方式：**只读源码与文档，未运行代码、未实测性能**。下文所有"实测"字样均指**别人公开的实测**，我本人未复现。

---

## 0. 结论先行

**方向同意，PR 拆分合理，代码事实核对扎实**（我逐条核过：`analysis.py:131`、`pipeline.py:330-337`、`clustering.py:155`、`assets.py:293`、`CLUSTER_THRESHOLD=0.85`、`TEXT_CLUSTER_THRESHOLD=0.34`、`CLUSTER_MARGIN=0.05`、三个 embedding 列均为 JSONB 可空——全部对得上）。

**但有 3 处按现方案实施会直接失效或造成静默错误（P0），4 处设计选择建议改（P1），3 类证据缺失需要补。**

| 级别 | 问题 | 后果 |
| --- | --- | --- |
| P0-1 | 模型缓存挂载点写错（`/root/.cache/huggingface`） | volume 缓存不生效，每次容器重建重下 ~90MB |
| P0-2 | "失败则设 `HF_ENDPOINT` 重试"在进程内无效 | 国内网络降级路径整条失效 |
| P0-3 | 降级为 `None` 会污染 `representative_embedding` 的均值口径 | 老库开启后代表向量静默漂移（既有 bug，被本方案放大） |
| P1-4 | 阈值/margin 跨模型迁移未验证，"用校准接入"低估工作量 | 0.85 未经验证即接管自动归族 |
| P1-5 | 代表向量用"族内均值" = 主动制造 hub | 大族引力，小族/散点更难召回 |
| P1-6 | 嵌入文本选 `summary + tags`（最泛化的文本） | 向量区分度下降，与 5/4 的效应叠加 |
| P1-7 | 默认 `local` 会改变存量行为，不是纯增强 | 26 条老素材静默切换判定通道 |
| 证据-8/9/10/11 | 附录 B 不存在、`AGENTS.md` 不存在、版本号自相矛盾、算力口径错 512 倍 | 可核验性问题 |

---

## 1. P0 硬伤（会直接失效，不是优化项）

### P0-1 模型缓存挂载点错了 —— volume 缓存等于没做

**事实（已核验源码）**：fastembed Python 版默认缓存目录来自

`fastembed/common/utils.py: define_cache_dir()`：

```python
default_cache_dir = os.path.join(tempfile.gettempdir(), "fastembed_cache")
cache_path = Path(os.getenv("FASTEMBED_CACHE_PATH", default_cache_dir))
```

即默认落在 **`/tmp/fastembed_cache`**（容器内向 `tempfile.gettempdir()`），不由 HuggingFace 的缓存目录决定；也支持显式传 `cache_dir=`（`TextEmbedding.__init__(model_name, cache_dir, threads, providers, cuda, device_ids, lazy_load, **kwargs)`，已核验 0.8.x 签名，注意参数名是 `cache_dir`，不是旧版的 `local_cache_dir`）。

**结论**：方案 3.4 第 2 点"模型缓存进 docker volume（`hf-cache`，挂 `/root/.cache/huggingface`）"**缓存不到任何东西** —— 模型会写进容器 `/tmp`，`docker compose up` 重建即丢，每次冷启动重下 ~90MB。

**改法**：显式 `TextEmbedding(cache_dir="/models/fastembed")` 或环境变量 `FASTEMBED_CACHE_PATH=/models/fastembed`，volume 挂 `/models/fastembed`。文档里把"HuggingFace 缓存"和"fastembed 缓存"分开写清楚（HF 的 `HF_HOME` 与 fastembed 的缓存是两回事，fastembed 的 hub 下载也会被引到它自己的 `cache_dir`）。

### P0-2 运行时改 `HF_ENDPOINT` 不生效

**事实（已核验上游源码）**：

- `huggingface_hub/constants.py:69` → `ENDPOINT = os.getenv("HF_ENDPOINT", _HF_DEFAULT_ENDPOINT).rstrip("/")`：**import 时求值一次**。
- `huggingface_hub/file_download.py:287` → `if endpoint is not None and url.startswith(constants.ENDPOINT)`：用的是模块常量 `constants.ENDPOINT`。

**结论**：方案 3.4 第 1 点"下载顺序：官方 HF → 失败则 `HF_ENDPOINT=https://hf-mirror.com` 重试"——如果在应用进程内 `os.environ["HF_ENDPOINT"] = ...` 再重试，**不会生效**（常量已绑定）。这条降级链路按现写法是空的。

**改法（二选一，推荐 a）**：

- a) 在 `docker-compose.yml` 的 `environment:` 或 entrypoint 里**进程启动前**设 `HF_ENDPOINT`（这是最干净的，也顺带解决"默认给国内用户走镜像"）。
- b) 真要运行时切换，必须 monkeypatch：`huggingface_hub.constants.ENDPOINT = "https://hf-mirror.com"`，并把这行收口在 embedding 抽象层里（同时要处理已缓存的 URL 前缀）。

**另一处被漏掉的细节**：`BAAI/bge-small-zh-v1.5` 在 fastembed 里同时配了**两个**源（已核验 `fastembed/text/onnx_embedding.py`）：

```python
sources=ModelSource(
    hf="Qdrant/bge-small-zh-v1.5",
    url="https://storage.googleapis.com/qdrant-fastembed/fast-bge-small-zh-v1.5.tar.gz",
    _deprecated_tar_struct=True,
),
```

`download_model()` 的顺序是：先 HF（含 `local_files_only` 缓存命中）→ 失败 → 回退 **Google Cloud Storage**。这个 GCS 回退在国内同样不可达，所以真实降级链是「hf-mirror → GCS（大概率也不通）→ 全失败」。方案里把 GCS 这一跳写成"官方 HF"，读者会误判兜底强度。

好消息：`download_model()` 里有 `specific_model_path` 逃生口（`specific_model_path = kwargs.pop(...)`，直接 return 该路径），比"挂卷"更可靠——**离线路径建议写成：宿主机预下载 → `specific_model_path=` 指过去**，而不是"手动挂卷"这种模糊说法。

### P0-3 降级为 `None` 会静默污染代表向量（既有 bug，本方案会放大）

**事实（已核验源码）**：`pipeline.py:245-248`，attach 到已有族时：

```python
creative.representative_embedding = running_mean(
    creative.representative_embedding,
    embedding,
    creative_repo.variant_count(creative.id),
)
```

而 `running_mean(old, new, count)` 的语义是"旧值 = `count` 个向量的均值"。**`variant_count` 数的是该 creative 的全部 variant，而不是"真正参与过均值的向量数"。**

两者不等的场景：

- 历史 variant 因 embedding 不可用（Kimi 现状 = 全部不可用）没有贡献向量 → `count` 偏大 → 旧均值被**过度加权**，新向量被稀释；
- 反之在部分失败/部分成功交错时会向另一侧漂。

**为什么这是本方案的问题**：方案 3.4 把"下载失败 → 返回 `None` → 文本兜底"明确设计为**常态**（国内首启、镜像抖动、模型切换、回填部分失败）。也就是说 **P0-3 的触发条件从"异常"变成"日常"**，而 4.4 的回填又会在一次巩固里批量触发它。

**另外两个相关缺口**：

1. 4.4 只写回填 `analysis_results.embedding` + 重算 `creatives.representative_embedding`，**漏了 `variants.embedding`**——而 `graph.py:230` 拆族时 `representative_embedding=variants[0].embedding` **直接拿它当新族的代表向量**。漏回填 → 拆出来的新族带着旧模型的毒向量（且是单条未归一化向量，与非 NULL 分支的"均值"语义也不一致）。
2. 回填"重算 `representative_embedding`（running_mean 语义，NULL 时直接赋值）"——模型切换时 `variant.embedding` 也是旧的，`running_mean` 增量叠加会叠在毒向量上。正确做法是**按成员向量重算均值**（均值与顺序无关），不是增量更新。

**改法**：

- 增加 `embedding_count`（或 `embedding_mean_count`）字段：语义 = "参与过均值的向量条数"，随写入维护；`running_mean` 用这个数而不是 `variant_count`。
- 回填 = 对每个 creative **重新求成员向量的均值**（同时覆盖 `analysis_results.embedding` / `variants.embedding` / `creatives.representative_embedding` 三处），不做增量。
- 拆族路径的 `variants[0].embedding` 改为走同一口径（均值 + 记 count），否则同一个字段在不同代码路径下含义不同。
- 补一条回归测试：模拟"部分 variant 无 embedding"的族，断言代表向量 = 有向量成员的真实均值。

---

## 2. P1 设计选择（建议改，不致命）

### P1-4 阈值/margin 跨模型迁移未验证，且"接入校准"低估了工作量

**已核验的官方口径**（BAAI BGE 模型卡原文要点）：v1.5 用温度 0.01 对比学习微调，**相似度分布大致落在 [0.6, 1.0]**；"大于 0.5 不代表两条句子相似"；并且明确写着 **"绝不要硬编码别的模型的阈值"**、"**能排序就不要阈值**"、"要阈值就必须自己校准，0.8 / 0.85 / 0.9 只是起点"。

**结论**：0.85 是从 OpenAI 类模型的经验里带过来的，落到 512 维的 bge-zh 上属于**未经验证的取值**；`CLUSTER_MARGIN=0.05` 在一个被压缩的分布里代表的意义也与原场景不同（同样的 0.05 在两个空间里的"双子并列"判定强度不等价）。方案 8 的风险表把这解释为"偏保守方向是漏并、安全侧"——**这个判断我没有证据支持它成立**，因为分布压缩可能让 0.85 反而变"松"（更多对过线），方向不确定，必须实测分布。

**"PR-E3 用 threshold_calibration 校准"这件事的工作量被低估了**：现有实现（已核验 `threshold_calibration.py`）是

- 网格搜索区间 `GRID_LOW, GRID_HIGH, GRID_STEP = 0.20, 0.50, 0.01`——这是**文本相似度的量纲**，跟余弦 0.85 完全不在一个区间；
- 打分口径 `score = max(text_score, analysis_score)`——只有两路特征。

要校准余弦阈值，不是"接入"，是**改口径**：网格区间、特征集、以及"并集召回下不同量纲的分数不可比"（`max()` 混不同量纲本来就是权宜）。而且样本量是硬约束：`MIN_SAMPLES = 4`、标注来自 `edit_logs` 的 merge + `split_rulings`，一个 26 条素材的库能产出多少对？**在这个样本量上网格搜一个标量阈值，置信度很低。**

**建议改法**（按性价比排序）：

1. **按名次而不是按分数截断**：候选生成用"每条的 top-k"（k 由 LLM 预算反推），而不是一个全局阈值。BAAI 也建议"rank, do not threshold"。这直接把 P1-4 的阈值问题降级成"只影响 k"。
2. 真要判定阈值：把"单标量阈值"升级成**四路特征的小分类器**（余弦 + 文本 Jaccard + 分析 Jaccard + 视觉重叠），L2 正则逻辑回归或线性探针，交叉验证报 PR 曲线。方案在 `threshold_calibration.py` 里拒绝逻辑回归的理由是"几十条样本上两特征权重学不稳"——但**一个 4 维、单调、强正则的线性模型，比"在一个错误量纲的网格上搜一个标量"风险更低**，而且能直接输出概率供 LLM 成本排序。
3. 无论如何都要在真机上先看分布：0.70/0.75/0.80/0.85/0.90 各自的候选数与被裁决命中率。

### P1-5 代表向量用"族内均值" = 主动制造 hub

**理论与实证（外部，已核验存在）**：hubness 是高维空间的固有现象——靠近数据分布中心的点会成为大量其他点的近邻（Radovanović 等的经典结论；Feldbauer & Flexer《A comprehensive empirical comparison of hubness reduction in high-dimensional spaces》, KAIS / PMID 32647403 的大规模实证比较）；Criteo AI Lab 还专门写过"cross-cluster hubness"在广告/推荐场景下的表现。更直接的一条：在跨模态检索的 hub 研究里，**"对全体输入平均相似度最大"的那个向量，其解析解正好就是归一化向量的平均值**（由 Cauchy–Schwarz 取等推出），并且实证表明单条 hub 就能显著破坏检索排序。

**结论**：`running_mean` 算出来的代表向量，**按定义就是全库里最"像所有东西"的那个点**。用它做近邻召回，会形成 rich-get-richer：大族的代表向量天然吸引新素材 → 变得更大。`decide_cluster` 的 margin 只能挡"双子并列"，**挡不住"被大族稳稳吸走"**。

**改法（任一即可，成本都不高）**：

- 代表向量改用 **medoid**（族内到其他成员平均相似度最高的那条，而不是均值）；
- 或保留**多条成员向量**做多向量召回（族内 top-k 向量各算一次相似度取 max），存储成本在 512 维下可忽略；
- 或对分数做 centering / CSLS 惩罚（`centered = cos - mean(cos(*, d))`）；
- 无论选哪个：加一个 hub 监控指标 —— **每个族被 attach 的次数分布**（复用现有 `intervention_density` 的思路），偏斜突然变大就是 hub 在作祟。

### P1-6 被嵌入的文本选错了：`summary + tags` 恰好是文本通道里刻意剔除噪声的那类

**事实**：文本通道走的是 `review._filtered_similarity` + `_signal_tokens`，会先用 `markets.resolve_generic_tokens` 把**通用词（题材/市场/游戏）剔掉**；漏合并的分析通道用的是 `hook + gameplay`（`missed_merge_scan.py: _analysis_texts`）。

而 embedding 用的是（`pipeline.py:330`）：

```python
embed_text = f"{payload.summary} {' '.join(payload.tags)}"
```

`summary` 是泛化描述，`tags` 多为通用属性（game / genre / market）——**把最泛化的文本喂给向量**，等于让向量随身携带一堆通用噪声：所有素材都被推向同一个"广告素材"中心，整体相似度抬高、区分度下降。这会和 P1-5（hub）、P1-4（bge 分布压缩）叠加放大。

**改法（性价比最高，建议直接进 PR-E1 做对照）**：**不改表、不改 prompt，只换被嵌入的字符串**——用 `hook + gameplay`（或再加规范化后的 `creative_name`）。然后拿现有 26 条素材在同一后端下对比"`summary+tags` vs `hook+gameplay`"的候选质量，用数据定夺。

### P1-7 默认 `local` 不是"只会更好"，它改变存量行为

**事实（已核验源码）**：`pipeline.py:202-210`

```python
if embedding is not None:
    candidates = top_creative_matches(embedding, creatives, limit=2)
    threshold = CLUSTER_THRESHOLD        # 0.85
else:
    candidates = top_creative_matches_by_text(...)
    threshold = TEXT_CLUSTER_THRESHOLD   # 0.34
```

即 **embedding 是否存在，直接决定走哪条判定通道**。

**结论**：回填 26 条老素材 = 把它们的归属判定**静默地从文本通道切到向量通道**，而接管的那条阈值（0.85）正是 P1-4 里"未经验证"的那个。方案 5.2 的验证点只写了"`GET /review/queue` 行为不变"，**覆盖不到"自动 attach 的判定口径变了"**。

**改法**：先提供 `shadow` 模式——embedding 只写入、只参与**收件箱候选并集**，**不接管 attach**；跑一轮周期巩固，观察 embedding 通道在收件箱里的命中准确率，再切 `local` 全量。这样 3.2 里"默认 `local` 不会让任何人变糟"才是真的成立。

---

## 3. 文档证据缺失（可核验性）

| # | 问题 | 具体 |
| --- | --- | --- |
| 8 | **附录 B 不存在** | 正文第 27、76 行引用"附录 B 的实测（B.5…最高相似对仅 0.21）""同一题材 13 种机制"，但本文件正文到 168 行结束，**没有任何附录**。两个关键数字无法核验 → 补附录或注明出处文件。 |
| 9 | **`AGENTS.md` 在公开库不存在** | 公开库 `docs/` 下只有 `AGENT_SPEC.md`、`design-principles.md`、`open-source-release-checklist.md`；`design-principles.md` 引用的 `AGENTS.md` 与 `docs/creative-boundary-rules.md` 同样不存在。第 70 行"AGENTS.md 的人工治理红线"对公开读者是断链 → 红线内容落到 `design-principles.md`（P04 Human in the Loop）并改成可核验引用。 |
| 10 | **版本号两套并存** | 仓库 tag `v0.7.0` / HEAD `1d63fdb`（与方案一致 ✅），但 `apps/api/app/version.py` 的 `APP_VERSION = "0.12.2"`。定位现状用 v0.7.0 没错，但升级/风险表述建议统一用 commit 或 git tag。 |
| 11 | **算力口径算错 512 倍** | 3.3 写"1 万条素材…暴力两两比对是 **5×10⁷ 次乘加**"。5×10⁷ 是**对数**（1e4²/2），乘上 512 维 ≈ **2.6×10¹⁰ 次乘加**（≈5×10¹⁰ FLOPs）。结论（NumPy 下 <1s）仍成立，但依据写错了一个量级。**另**：`X @ X.T` 全矩阵在 1 万条时是 1e8×4B = **400MB 峰值**，5 万条就是 **10GB** → "一次性 stack"不成立，需配分块 + `argpartition` 取 top-k，否则 5 万条的边界站不住。 |
| 12 | **场景 D 只算了余弦，漏了扫描器的大头** | `recall_pairs` 每对都要跑 `find_prior_ruling` + 两路 `_filtered_similarity`（纯 Python 分词/Jaccard），**且视觉通道要把每条 creative 的代表视频从 MinIO 下载下来逐帧算 pHash**（代码注释自己写了"每次扫描现算（不建缓存表）：百级 creative 分钟级跑完，量级到万再缓存"）。到 1000 条时瓶颈是 Python 级 O(N²) 循环 + 视频下载，不是余弦 → 场景 D 应把"向量通道成本"与"整轮 scan 成本"分开列，并把视觉签名缓存表排进路线。 |

---

## 4. 可直接借鉴的经过验证的做法

> 选取标准：**要么是头部大厂的生产实践，要么是近两年可核验的论文/官方文档**。每条我都标了"可核验来源"和"我们怎么用"。未核验的我另外列出。

### 4.1 语义去重（和本方案同构的问题，已经被大厂做过一遍）

**SemDeDup（Meta AI + Stanford，arXiv 2303.09540）** —— 用预训练 embedding + **k-means 分桶**，只在桶内做两两余弦，把 O(N²) 降到约 O(N²/k)；阈值取 `1-ε`，LAION 上删掉 50% 数据性能基本不掉、训练时间减半。

- **我们怎么用**：直接借"分桶再桶内两两"这个结构。到千/万级时先对 `representative_embedding` 做一次球面 k-means，只扫同桶 + 邻桶 —— 这一招同时解掉 P1-5（hub 被限制在桶内）和第 12 条的算力问题。注意 SemDeDup 的桶是"近似重复"桶，每桶较小，正好避免"大桶 = 大族"的引力。
- 同一族系还有 **D4（Meta，2023，去重后加多样化）**、**SoftDedup（2024，软重加权代替硬删除）**、**FineWeb（2024，15T token 级的生产报告，MinHash 112 perm / 0.8 Jaccard）**——共同结论：**去重之后还要管"冗余"与"多样性"，且生产上从保守阈值起步（0.95 起，按评测下调），并保留一组"已知硬案例"防止误删稀有样本**。你们的 `case-rulings.json` 就是天然的"已知硬案例"负样本集，可以直接拿来做回归。

### 4.2 阈值该怎么定（官方口径，直击 P1-4）

**BAAI BGE 模型卡的使用建议**（已核验）：v1.5 分布压缩在 [0.6, 1.0]；**"不要用别的模型硬编码阈值"**、**"能排序就别用阈值"**、**"要阈值就在自己的数据上校准，0.8/0.85/0.9 只是起点"**；指令前缀只对"短查询检索长文档"有用，**聚类/s2s 场景不需要**（这条对选型是减负：不用为前缀纠结）。

- **我们怎么用**：把 P1-4 的建议 1（top-k 候选预算）+ 建议 3（先看真机分布）直接写进文档，作为"阈值从哪来"的答案。

### 4.3 召回之后、LLM 之前，还该有一层（本方案最大的机会点）

**fastembed 自带 cross-encoder 重排**（已核验：`fastembed/rerank/cross_encoder/` 模块存在；上游模型清单含 `BAAI/bge-reranker-base`、`BAAI/bge-reranker-v2-m3`）。

- **我们怎么用**：在"embedding 召回 → LLM 3 票判定"之间插一层 reranker，把 0.80 召回出来的候选对**按 pair 精排一遍**，只把 top 部分交给 LLM/收件箱。**不引入新依赖**（同库同进程），能再砍一个数量级的 LLM 成本，而且这是 MTEB reranking 榜上被反复验证的路线。中文场景直接选 `bge-reranker-v2-m3`。

### 4.4 LLM 用在"该问哪些对"上，而不是"问所有对"（近两年最贴题的一支）

**Li et al.《On Leveraging Large Language Models for Enhancing Entity Resolution: A Cost-efficient Approach》（arXiv 2401.03426，2024，华南师大/港科大/微众）** —— 把实体消解重新定义为**不确定性削减**问题：用 Shannon 熵衡量当前分区的不确定性，用贪心算法挑"信息增益/token 成本"最高的匹配对去问 LLM；并对 LLM 的错误率做**贝叶斯修正**，不把 LLM 回答当真值。

- **我们怎么用**：与"召回 → LLM 3 票"的结构**完全同构**。候选对的排序不该只按相似度，而应按"期望不确定性下降"。这比"0.80 粗召回阈值"更有原则，也天然把 LLM 成本管起来。落地形态：候选池按 `entropy_gain / token_cost` 排序，收件箱按此顺序展示（而不是按相似度降序）。

**LLMEdgeRefine（EMNLP 2024, Feng et al., ACL Anthology `2024.emnlp-main.1025`，已核验存在）** —— 用 LLM 只精修聚类的**边界点**（edge points / 超点），而不是让 LLM 处理全部样本。

- **我们怎么用**：方案里"embedding 只召回、LLM 精判"与之一致，可以再进一步——**只把 margin 小的边界对送 LLM**。你们已经有 `CLUSTER_MARGIN`，把它从"防摇摆的判定开关"扩展成"LLM 预算的分配器"。

### 4.5 人工裁决应该沉淀成小模型（产品定位上的正解）

**TnT-LLM（KDD 2024, Microsoft, Bing Copilot，已核验存在）** —— 两阶段：① LLM 零样本多阶段推理，迭代生成并精修标签体系（taxonomy）；② LLM 当数据标注器产伪标签，训练**轻量监督分类器**，之后靠小模型规模化服务，LLM 只兜不确定的部分。

- **我们怎么用**：这正是 **Augura「DNA 家族」这条线的正确终局**。现状 `family_bootstrap` 每次都要重建族、LLM 成本不随库收敛；按 TnT-LLM 的做法：人工确认过的族（+ `keywords`）应该训练一个轻量分类器（哪怕就是 embedding + 线性探针），新素材先过分类器，LLM 只处理低置信的。**这与产品定位"把每次人工裁决变成复利的团队资产"是同一件事** —— 现在裁决只用来调一个标量阈值，兑现得太少。

### 4.6 高维几何的坑（直击 P1-5）

- **Feldbauer & Flexer《A comprehensive empirical comparison of hubness reduction in high-dimensional spaces》（Knowledge and Information Systems，PMID 32647403，已核验存在）**：对全部主流 hubness 削减方法做了大规模实证比较，结论是**缩放类与密度梯度拉平类方法能稳定提升 hubness 与最近邻分类精度**；居中类只在特定设置下有效。
- **Criteo AI Lab《On Nearest Neighbors and Hubs in High-Dimensional Data》**（工程侧解释，含"cross-cluster hubness"在广告推荐里的表现）、以及跨模态 hub 研究（**hub 的解析解 = 归一化向量的均值**）。
- **我们怎么用**：见 P1-5 的四个改法；用"每个族被 attach 的次数分布"做 hub 监控。

---

## 5. 未证实 / 需要你决策的点（我不替你拍板）

**5.1 Kimi 是否已有 embeddings 端点 —— 未证实，必须实测。**
我核过两处官方文档（`platform.moonshot.cn/docs/api/chat` 与 `platform.kimi.com/docs` 快速开始），**都只文档化了 chat completions，没有 embeddings**；但第三方 API 目录（apis.io 的 Kimi provider 条目）与社区 SDK（`Moonshot.Extensions.AI.Community`）都引用了 `/v1/embeddings` + `text-embedding-v1`。**冲突未解**，我不下结论。

- 最省事的实测：仓库自带"获取模型列表"（`services/settings.py: list_provider_models`）+ 设置页"测试连接"，先跑一次 `GET {base_url}/models` 看有没有 embedding 模型，再直接 `POST /v1/embeddings` 一次。
- **如果 Kimi 已有**：本方案的定位应从"补能力"改写成"离线/保底能力"，且 `provider` 后端应加入国内可直接用的预设（SiliconFlow / DashScope / 火山等，零新依赖、质量更高、无需下模型）。这会让 PR-E1 的价值从"唯一路径"变成"其中一条路径"，优先级和默认值都该重算。

**5.2 2.3 节的模型选型有实测数据支撑，我补上具体数字（建议写进文档）**
C-MTEB 平均分（含检索/STS/分类分项）：

| 模型 | 维度 | C-MTEB 平均 | 检索 | STS | 分类 |
| --- | --- | --- | --- | --- | --- |
| bge-small-zh-v1.5 | 512 | **57.82** | **61.77** | 49.11 | 63.96 |
| bge-base-zh-v1.5 | 768 | 63.13 | 69.49 | 53.72 | 65.39 |
| multilingual-e5-small | 384 | 55.38 | 59.95 | 45.27 | 65.85 |
| multilingual-e5-large | 1024 | 58.79 | 63.66 | 48.44 | 56.00 |
| text-embedding-ada-002（参考） | 1536 | 53.02 | 52.00 | 43.35 | 64.31 |

**结论**：中文场景 bge-small-zh-v1.5 优于 multilingual-e5-small（+2.4 平均 / +1.8 检索），且维度更小 → 原判断成立，可以拿这组数字替掉"中文最强小模型"的定性说法。
**但要注意**：这个优势是"中文文本"上的。你们是**中文团队做英文投放素材**（分析文本中英混合、素材名是英文 kebab-case）。**这个混合分布上的表现没有任何 benchmark 覆盖**，属于必须自己实测的部分（建议放进 E0）。
另注：fastembed 的模型清单对 bge-small-zh-v1.5 标注 `Prefixes for queries/documents: not so necessary`，聚类是 s2s 场景，**不需要指令前缀**（BGE 官方也只对 s2p 检索建议加）。这条可以省掉一个坑。

**5.3 fastembed 选型的维护性核验结果（支持原判断）**
fastembed Python 版：**0.8.0 发布于 2026-03-23，最近仓库提交 2026-09-09，3.1k star，Apache-2.0，月下载 ~860 万**，无已知漏洞。依赖 10 个：`huggingface-hub / loguru / mmh3 / numpy / onnxruntime / pillow / py-rust-stemmers / requests` 等（**纯 Python wheel，113KB**，重的是 onnxruntime）。
→ 方案 8 "Qdrant 出品、2026 年仍活跃（v0.8.x）"**成立**，但把 `v0.8.x` 改成"0.8.0（2026-03）"更准确。
→ **注意 CI**：onnxruntime 的 pin 与 Python 版本强相关（0.8.0 的约束里 py3.11/3.12/3.13 各不相同，例如 py3.13 要求 `!=1.24.0,!=1.24.1,>1.21.0`；py3.14 还要 ≥1.24.2）。**顺便发现**：CI 用 Python 3.11（`.github/workflows/ci.yml`），而 `AGENT_SPEC.md` 写 3.12 —— 顺手对齐。建议把本地后端做成**可选 extra**（`local-embed`），CI 装轻量版 + 一个 nightly job 跑 integration，而不是让每次 CI 都拉 onnxruntime。

---

## 6. 其他小问题（不阻塞，但会咬人）

1. **两套余弦实现的风险**：`clustering.cosine_similarity` 是纯 Python，新方案要 NumPy 矩阵化 → 会引入第二套实现。必须保证语义一致（长度不等/空/零向量返回 `0.0` 的行为），否则同一阈值在两条路径上含义不同。要么统一到 numpy，要么加一个交叉断言测试。
2. **懒加载单例要加锁**：`pipeline.py` 有并发闸门 `_PIPELINE_SLOTS`（`analysis_concurrency`），多个分析线程可能同时首触 → 同时下载/初始化。onnxruntime 推理是线程安全的，**初始化不是**。
3. **三处冗余存向量**：`analysis_results.embedding` / `variants.embedding` / `creatives.representative_embedding`。JSONB 存 float32 约 70MB/万条（方案写 60MB，接近），改 `REAL[]` 能降到 ~20MB 且解析更快，但要一次迁移 —— **我同意现在"不动表结构"，把它记为 next**，别塞进这一轮。
4. **流程归属冲突**：`AGENT_SPEC.md` 写着 `infra/docker/`（Dockerfile）和 `docker-compose.yml` 是"主代理所有，**禁止修改**"，而 PR-E1 恰恰要改这两个（加 volume、加 env）。要么解掉这个归属约定，要么这版 PR 落不了地。这是**流程上的阻塞项**，不是技术问题。
5. **视觉签名**：`_visual_signatures` 每次扫描重新下载所有代表视频 —— 它已经是全流程最贵的一步。向量召回进来后，候选对变多会不会让下游测量/裁决（`measure_pair_alignment`）成本同步上升？值得在 E2 前先量一次。

---

## 7. 修订建议：PR 拆分（在原方案上调整）

| PR | 变化 | 关键内容 |
| --- | --- | --- |
| **E0（新增，先做）** | 0.5 天，**决定 E1 的默认值与阈值** | ① 探测 Kimi 是否已有 embeddings（用现有 `/models` + 直连 `/embeddings`）；② 拿 26 条真实素材离线跑 bge-small-zh-v1.5，看**真实相似度分布** + 0.70/0.75/0.80/0.85 各档候选数与命中率；③ 对照 `summary+tags` vs `hook+gameplay` 哪个被嵌文本的候选质量更好；④ 核验中文模型在"英文素材名 + 中文分析文本"混合分布上的表现；⑤ 复现 P0-3 的 count 漂移 |
| **E1** | 原 E1 + P0 修正 | 缓存路径改 `cache_dir`/`FASTEMBED_CACHE_PATH`（不是 HF 目录）；`HF_ENDPOINT` 走 compose env 或 monkeypatch；`embedding_count` 口径修复；`shadow` 模式；可关闭的后端三态 |
| **E2** | 原 E2 + 补漏 | 召回并集；`family_bootstrap` 分桶（可直接借 SemDeDup 的分桶结构）；回填**覆盖 `variants.embedding`** 且**按成员重算**；单例加锁 |
| **E3** | 原 E3 升级 | 从"标量阈值校准"升级为"top-k 候选预算 + 四路特征小分类器"；`bge-reranker-v2-m3` 作为可选精排层 |
| **E4（新增）** | TnT-LLM 式沉淀 | 用已确认的族训练轻量分类器，让 `family_bootstrap` 的 LLM 成本随库收敛 |

---

## 8. 审核边界（我做了什么、没做什么）

**做了**：读本地 checkout（HEAD `1d63fdb` / tag `v0.7.0`）与 GitHub 公开库源码；逐条核对方案里的代码引用（行号、常量、列类型）；核验 fastembed 上游（模型清单、下载顺序、缓存路径、`HF_ENDPOINT` 语义、重排模块）；核验 BAAI 官方模型卡口径；核验引用论文（LLMEdgeRefine EMNLP 2024 / TnT-LLM KDD 2024 / SemDeDup arXiv 2303.09540 / arXiv 2401.03426 / hubness 综述）是否真实存在及其内容要点。

**没做**（所以这些结论的强度要打折）：

- **没有运行任何代码、没有跑 benchmark、没有实测下载耗时与推理延迟**。方案里"<1s""30s–2min""10–30ms"这类数字是推理估计，我也未复现，**不作为结论**。
- **没有实测 26 条素材的相似度分布** —— 所以 P1-4 的"0.85 偏松还是偏紧"我明确说不知道，只能给方法。
- **没有逐行核 `TextCrossEncoder` 在 Python 0.8.0 的入参签名**（只确认了模块存在与上游模型清单）。
- **Kimi embeddings 端点存在性未解**（见 5.1）。
- 引用中标注为"二手来源"的（NVIDIA NeMo Curator 的 0.85 默认阈值、FineWeb 的具体 MinHash 参数、D4/SoftDedup）来自二手技术文章，**我未回原文核**，采用前请自行核对。

---

## 附：修订记录

| 日期 | 版本 | 说明 |
| --- | --- | --- |
| 2026-09-16 | v1 | 首次审核。基于 `embedding-recall-design.md`（168 行，2026-09-16 10:29）、仓库 main `1d63fdb`（tag v0.7.0）。 |
| 2026-09-16 | v1 复核 | 完成代码级深审后（见同目录 `code-review-2026-09-16.md`），回头逐条核对了本文件对源码的引用：`assets.py:293`、`version.py`（`APP_VERSION="0.12.2"`）、`threshold_calibration.py` 的 `GRID_LOW/HIGH/STEP = 0.20,0.50,0.01` 与 `MIN_SAMPLES=4`、`pipeline.py:202-210/245-248/330-337`、`clustering.py:14/16/19/27/155`、`graph.py:228-231`、CI `python-version: "3.11"` vs `AGENT_SPEC.md` 的 3.12、`docs/AGENTS.md` 不存在——**全部属实，本文件无需修订**。唯一需要补充的是：深审发现 `assets.py:299` 还有**第四条**代表向量写入路径（`creative.representative_embedding = embedding` 直接覆盖整族均值），本文件 P0-3 只列了三条，已在 `code-review-2026-09-16.md` §1 P0-3 中作为独立证据补全，结论方向（须统一口径 + 记 count）不变但**更强**。 |
