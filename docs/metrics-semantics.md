# 指标口径（Metrics Semantics）

后端 KPI 聚合口径的唯一事实源是 `apps/api/app/services/metrics.py` 的模块
docstring（口径注册表）；本文是面向人读的镜像，改口径时两处同步。

**约定：新增 KPI 必须先在 metrics.py 注册口径，再实现。** 口径只许有一份
定义，recommendation（规则判定）、priority（货币化排序）、review /
creative_score 等消费方共用同一个 `aggregate()`，不允许各自重算。

## 口径表

| 指标 | 聚合语义 |
| --- | --- |
| `spend` / `installs` / `impressions` / `clicks` / `payers` | 行级求和（sum）。`payers` 不在 Performance 列上，从 raw JSONB 经 `metrics_from_raw` 解析后求和 |
| `cpp` | `spend / payers`（ratio of sums，先求和再相除）；无付费为 `None` |
| `cpi` | `spend / installs`（ratio of sums）；无安装为 `None` |
| `ctr` | `clicks / impressions`（ratio of sums）；`impressions = 0` 为 `None`。`CreativeMetrics` 的派生 property，不单独存储 |
| `roas`（d1_roas）/ `d3_roas` / `d1_retention` / `ipm` | 行级比值的**消耗加权平均**（spend-weighted row mean），**非** ratio of sums——有意为之：权重正比于行消耗，极端小行（几分钱消耗、比值失真）不会主导结果；缺该指标的行同时不进分子与分母。无任何带值行时为 `None` |
| `days_idle` | 最近数据行距**全库最大日期**的天数（不是距今天）；该 creative 无数据为 `None` |
| `recent_spend` / `recent_cpp` | 近 7 天窗口（`RECENT_WINDOW_DAYS = 7`），窗口同样相对**全库最大日期**（`date > max_date - 7d`）；`recent_cpp` 为窗口内 ratio of sums，窗口无付费为 `None` |
| `prev_spend` | 紧邻 recent 窗口之前的等长 7 天窗口（`max_date - 14d < date <= max_date - 7d`）消耗求和；`recent_spend < 0.5 × prev_spend` 近似消耗腰斩（频次/供给衰减信号） |
| `market_count` | 派生字段，非 `aggregate()` 计算：`build_report` 注入——投放行按市场码归一后的 distinct 市场数；无投放行为 `0` |
| `spend_share` / `payer_share` | 份额口径：单 creative 值 ÷ 全库合计。分发诊断（`distribution_hint`）基于每个 creative 最新 verdict 快照的 metrics JSONB 计算，不重聚合 Performance |
| `main_market` | 派生字段，非 `aggregate()` 计算：`build_report` 注入——消耗最高变体的市场标签，无投放数据回退变体文件名前缀，再无则 `""` |
| `optimization_type` | 派生字段，非 `aggregate()` 计算：`build_report` 注入——投放行按 `Performance.optimization_type`（`install`/`aeo`/`vo`，迁移 0020 从 raw「优化方式」列回填）分桶后**消耗最大桶**的键；无类型/无投放为 `""`。判定（`aggregate()` 与规则链）只喂主桶行；未知口径回落 aeo = 旧行为 |
| `mixed_spend` | 派生字段，非 `aggregate()` 计算：非主桶消耗合计（混用素材中未参与本次判定的部分）；`> 0` 时追加补充理由 `mixed_objectives` |

## 相关约定

- 判定阈值（红线 / 绿线 / 起量线等）不在本文件范围：默认值在
  `services/settings.py` 的 `DEFAULT_THRESHOLDS`，分市场覆盖由
  `resolve_thresholds` 合并；规则使用的判定常量（`SPEND_MIN_JUDGE` /
  `PAYERS_MIN_JUDGE` / `SPEND_SIGNIFICANT` / `IDLE_DAYS_ARCHIVE`）在
  `services/recommendation_rules.py`。
- `days_idle` / `recent_*` 都锚定"全库最大日期"而非当前时间：报表是对
  已入库数据的截面判定，重跑历史数据得到的是同一口径的可复现结果。
