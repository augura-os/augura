# Embedding 召回层 E0 前置实测报告

> 日期：2026-09-16 ｜ 执行：scratch 目录 `scripts/embedding-e0/`（未入库）｜ 数据基线：main `1d63fdb`（v0.7.0），库内 25 creatives / 26 variants / 26 analyses，**全部未归族**（`creative_dnas` 为空）

## 0. TL;DR

- **Kimi `/embeddings` 端点存在且可用**：`POST https://api.kimi.com/coding/v1/embeddings` 返回 200，OpenAI 风格响应，实际模型 `bge_m3_embed`，维度 **1024**；请求体里的模型名被忽略（传 `text-embedding-3-small` 也回显 `bge_m3_embed`）。
- **同一 key 对 `api.moonshot.cn/v1/embeddings` 返回 401 Invalid Authentication** —— 仅说明这把 coding key 不适用于 moonshot 开放平台，不代表该平台没有 embeddings 端点。
- 方案定位维持评审结论：Kimi 端点是**离线保底**（1024 维与本地 512 维向量空间不互通，只能整条通道切换，不能混用），主力是本地 fastembed 模型。
- 本地拉取 `BAAI/bge-small-zh-v1.5`（fastembed 0.8.0）**直连 huggingface.co 成功**，下载 ~22s，无需镜像。**踩到一个真坑**：fastembed 的构造参数是 `model_name=`，传 `model=` 会被 `**kwargs` 静默吞掉、**无报错地回退到默认的 bge-small-en-v1.5**——E1 集成时必须用 `model_name=`。
- **相似度分布整体偏高**（同游戏品类素材扎堆）：跨 creative 余弦均值 0.72–0.76，≥0.70 档占全部候选对的 76–86%——**全局阈值在这份数据上不可用，坐实 top-k 预算设计**。有效区分度在尾巴上：p95≈0.80（hook+gameplay）/ 0.85（summary+tags）。
- **阳性对照偏弱**：唯一一组同 creative 的 variant 对仅 0.846（summary+tags）/ 0.787（hook+gameplay），处于跨 creative 分布的 ~p95 水平——纯向量无法可靠区分"同族"与"同品类"，**shadow-only + LLM 裁决必须维持**。
- **P0-3 running_mean 漂移复现**：5 variant 中 3 个有向量的场景下，族中心与真实均值的余弦降到 **0.8496**（角度偏移 31.83°），有效权重 15:5:4 vs 正确 8:8:8，系统性偏袒最早进族的向量。
- **推荐 E1 默认值**：嵌入文本 `summary+tags`；`k=5`（命名常量，上限 10）；不开 attach 开关（shadow 默认）。依据见 §5。

## 1. E0-1 Kimi embeddings 端点探测

证据文件（均已截断、无 key）：`scripts/embedding-e0/data/kimi_embeddings_response_head.json`、`kimi_models_response_head.json`、`moonshot_embeddings_response.json`。

| 探测 | 结果 |
|---|---|
| `POST /coding/v1/embeddings`（api.kimi.com） | **HTTP 200**，OpenAI 风格 `data[0].embedding`，维度 **1024** |
| 响应 `model` 字段 | `bge_m3_embed`（请求传 `text-embedding-3-small` 被忽略，服务端固定映射） |
| `usage` 字段 | `null`（无计费信息回传） |
| `GET /coding/v1/models` | 200，列表 `['kimi-for-coding','kimi-for-coding-highspeed','k3','k3-256k']`，**无独立 embedding 模型条目** |
| `POST api.moonshot.cn/v1/embeddings`（同一把 key） | **401 Invalid Authentication**（key 不通用；不代表该平台无端点） |

含义：

1. 端点真实存在，可作为 embedding 来源的**离线保底**（例如本地模型装不上时应急）。
2. 1024 维 bge_m3 与本地 bge-small-zh（512 维）**维度不同，向量空间不互通**——保底通道生成的向量不能和本地模型的向量混在同一余弦空间召回，只能整条通道切换。与设计稿 3.3 的 `embedding_model_active` 一致性机制吻合：切模型 = 旧向量失效 + 回填。
3. 模型名不可选、`usage` 为空，调用成本不透明，不宜作为主通道。

## 2. E0-3 本地模型拉取体验（fastembed 0.8.0 + bge-small-zh-v1.5）

环境：`python:3.11` 容器（后固化为本地镜像 `augura-e0`）+ 命名卷 `augura-e0-cache`（`FASTEMBED_CACHE_PATH=/cache`），本机直连。

| 阶段 | 耗时 | 备注 |
|---|---|---|
| `pip install fastembed numpy` | 3m31s | 主要是 onnxruntime 轮子；构建进镜像后一次性成本 |
| zh 模型下载（直连 huggingface.co，无 token） | ~22s（5 文件） | 有 "unauthenticated requests" 警告，不影响 |
| `TextEmbedding(model_name=...)` 含下载 | 24.93s | 命中缓存后 0.58s |
| 首次 embed 单句 | 0.008s | CPU |
| 向量维度 / 模长 | **512** / 1.0（已归一化） | 与设计稿 3.1 口径一致 |
| 模型体积 | zh ~90MB（缓存卷合计 155MB，含误拉的 en 65MB） | |

**关键坑（E0 最有价值的发现之一）**：fastembed 的构造参数名是 `model_name=`。初版脚本误传 `model=`，被 `TextEmbedding.__init__` 的 `**kwargs` 静默吞掉，**无任何报错/警告**地加载了默认模型 `BAAI/bge-small-en-v1.5`（384 维）。若不是核对缓存目录名（`models--qdrant--bge-small-en-v1.5-onnx-q`）和维度，这个错误会一路带进 E1。集成建议：加载后断言 `model.model.model_name == 预期模型 id`，把这类静默回退变成显式失败。

**下载链验证**：直连 HF 可用，未走 hf-mirror。若其他环境直连失败，重跑时加 `-e HF_ENDPOINT=https://hf-mirror.com`（注意 `HF_ENDPOINT` 在 `huggingface_hub` import 时绑定，必须进程启动前设置——已核实）。设计稿 3.4 的降级链判断成立：zh 模型在 fastembed 注册表里是 HF（`Qdrant/bge-small-zh-v1.5`）+ GCS tar.gz 双源，GCS 国内大概率不通，别把它算成有效兜底。

日志：`scripts/embedding-e0/data/pull_direct.log`（首次误拉 en）、`pull_zh_direct.log`（修正后 zh 实拉）。

## 3. E0-4a 相似度分布实验

脚本 `scripts/embedding-e0/similarity_distribution.py`（容器内运行，模型 bge-small-zh-v1.5 / 512 维 / 无 query 前缀）；结果 `data/similarity_results.json`、候选清单 `data/top_pairs.md`。26 个 variant 两两比对（排除同 creative 对后 324 对）；creative 级向量 = 该 creative 下各 variant 向量均值后归一化（与 `representative_embedding` 语义一致；本样本仅 1 个 creative 有 2 个 variant）。

### 3.1 分布表（variant 级，跨 creative 对，n=324）

| 文本 | mean | median | p75 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| summary+tags | 0.7626 | 0.7708 | 0.8084 | 0.8315 | 0.8455 | 0.8910 | 0.9153 |
| hook+gameplay | 0.7242 | 0.7333 | 0.7623 | 0.7890 | 0.8010 | 0.8266 | 0.8527 |

候选对数（≥阈值）：

| 阈值 | 0.70 | 0.75 | 0.80 | 0.85 | 0.90 |
|---|---|---|---|---|---|
| summary+tags | 279 (86%) | 214 (66%) | 103 (32%) | **15 (4.6%)** | 2 |
| hook+gameplay | 245 (76%) | 121 (37%) | **18 (5.6%)** | 1 | 0 |

creative 级分布与 variant 级几乎一致（只有 1 个 creative 有双 variant），数据见 `similarity_results.json`，不重复列。

### 3.2 阳性对照（样本极小，如实记录）

同 creative 内 variant 对只有 1 组（某 creative 的 2 个 variant，且两者 hook 其实不同：海滩小品 vs 街头采访；素材名已按公开仓规则隐去）：

- summary+tags：**0.846** ≈ 跨 creative 分布的 p95
- hook+gameplay：**0.787** ≈ 跨 creative 分布的 ~p90

即"同一 creative"在向量空间里并不比"同品类随机两条"显著更近。**纯向量无法区分同族与同品类，候选必须过 LLM 裁决**——这正是设计稿 shadow 默认的前提，E0 数据支持。

### 3.3 top 候选对人工过目

用 variant 命名里的 campaign 码（示意：`数值对比V1`、`失败重试A版`、`剧情反转B版`——真实码已按公开仓规则替换为虚构码，人名同理）当弱 ground truth 验证：

- **summary+tags top-15**：#1/#2（葡语街头采访 hook 两两相配）、#4（制作人甲 `失败重试A版` 两条，0.8919）、#9（制作人乙 `剧情反转B版` 两条、同创作者素材，0.8746）等为真换皮/同 brief 对；中部（0.85–0.86）混有"同 hook 类型但不同创意"的对（如 #11 吃醋女友 vs VIP 俱乐部），属召回噪声，需 LLM 过滤。
- **hook+gameplay top-15**：#3（同创作者对，0.8304）、#4（制作人丙 `数值对比V1` 对，0.8271）、#7（制作人甲 `失败重试A版` 对，0.8189）为真对；整体分布更紧、绝对分值更低，高置信区（≥0.85）只有 1 对，**召回能力偏弱**。
- 结论：`summary+tags` 尾巴更肥、真对召回更多（它本来就涵盖 hook/gameplay 内容描述，tags 再补品类/市场词），作为主嵌入文本；`hook+gameplay` 可作为候选补充但不是必需。

## 4. E0-4b P0-3 running_mean 漂移复现

脚本：`scripts/embedding-e0/drift_repro.py`（逐字复制 `clustering.py:155-162` 的 `running_mean` 与 `pipeline.py:246-249` 调用处）。结果：`scripts/embedding-e0/data/drift_repro_result.json`。

场景：族内 5 个 variant，仅 v1/v3/v5 有向量，`count` 却按全部 variant 计。

| 指标 | 漂移实现 | 正确实现 |
|---|---|---|
| 有效权重 v1:v3:v5 | **15:5:4**（/24） | 8:8:8（/24） |
| 与真实均值的余弦 | **0.8496** | 1.0 |
| 角度偏移 | **31.83°** | 0° |
| 输出模长 | 0.6796（未 renorm） | 1.0 |

结论：漂移是**系统性**的——`running_mean` 把缺失向量也计入分母，等价于给最早进族的向量加倍权重；在余弦召回下方向偏移直接改变近邻排序，P0-3 必须修（分母只数有向量的 variant，或改为存累加和）。

## 5. 推荐 E1 默认值

| 决策点 | 推荐值 | E0 依据 |
|---|---|---|
| 嵌入文本构造 | **`summary + " " + tags 展开`** | §3.1/3.3：尾巴更肥（≥0.85 档 15 对 vs 1 对），top 候选里经 campaign 码验证的真对更多；与 `pipeline.py:330` 现状一致，改动最小 |
| top-k 的 k | **`k=5`**（命名常量，上限 10） | §3.1：summary+tags 的 p95≈0.85，25 creatives 下高置信尾约 15 对 ≈ 0.6 对/creative；k=5 在捕捉 p90–p95 区间真对（如 0.82–0.83 的同 brief 对）与 LLM 预算间取平衡——每新 creative ≤5 次裁决调用，全量扫描去重后 ~60 对 |
| attach 开关 | **不开，维持 shadow-only** | §3.2：阳性对照仅落在跨 creative 分布 p90–p95，纯向量分不清同族与同品类，必须 LLM 裁决 |
| （备用）若未来开阈值 | 从 **0.85**（summary+tags，≈p95）起步 | §3.1；低于此值候选爆炸（0.80 档已占 32%） |
| 模型加载 | `TextEmbedding(model_name="BAAI/bge-small-zh-v1.5")` + 加载后断言 `model.model.model_name` | §2：`model=` kwarg 静默回退 en 模型的坑 |
| 模型下载兜底 | HF 直连 → `HF_ENDPOINT=https://hf-mirror.com`；GCS 源不算有效兜底 | §2；`HF_ENDPOINT` 须进程启动前设置 |
| Kimi 保底通道 | 仅整条通道切换使用（1024 维 bge_m3_embed），不与本地 512 维向量混存 | §1 |

## 附：环境坑备忘

- Git Bash 下 `MSYS_NO_PATHCONV=1` 会让 mingw curl 写不了 `-o` 输出文件（报 `(23) client returned ERROR on write`）；curl 前须 `unset MSYS_NO_PATHCONV`，只有 docker `-v` 命令才 export 它。
- 每次 Bash 调用需重新 `export PATH="$PATH:/c/Program Files/Docker/Docker/resources/bin"`。
- 本机 python 3.14 无 pytest/numpy，实验全在容器内跑（镜像 `augura-e0` = python:3.11 + fastembed + numpy）。
- 库内素材未归族（`creative_dnas` 空），候选对质量无 ground truth，靠人工过目 + variant 命名 campaign 码弱验证；阳性对照仅 1 组，样本极小。
- 全程未改 `apps/` 产品代码、未动 git；`scripts/embedding-e0/` 为 scratch 目录，不入库。
