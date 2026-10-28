# 离线归族实验与团队协作测试

本目录提供**离线实验工具**，用于比较素材家族判定、复核和回测协议。它不是生产自动归族功能，也不会自动合并、自动判异或自动结案。

## 隐私边界

- 公开仓库只包含可公开的分类法、通用提示词、代码和虚构示例。
- 不得提交真实素材名、人员姓名、项目标识、UUID/storage key、投放数据、客户信息、真实 gold、媒体文件或模型原始返回。
- 实验输出（gold、缓存、请求账本、结果和报告）必须放在仓库之外。默认 `/tmp/merge_family_vnext`；仓库也忽略 `merge_family_vnext*/`。
- `run` 会把证据发送给当前配置的模型服务商。团队测试必须使用虚构或明确获授权的内容，不要外发真实业务素材。
- CI 和单元测试使用 mock，不需要真实 API key，也不调用模型。

## 配置目标素材

`prepare` 需要 `--creatives` 指定恰好两组 creative，合计 13 个变体。配置格式：

```json
[
  {"name": "demo-survival-alpha", "expected_variants": 7},
  {"name": "demo-survival-beta", "expected_variants": 6}
]
```

`apps/api/examples/creatives.demo.json` 仅演示 schema，不包含真实项目数据。实际使用时请在自己的私有目录编写配置，名称必须与本地数据库中的 `creatives.name` 一致，不要把该私有配置提交到仓库。

## 运行流程

在 `apps/api` 目录、已经配置好本地数据库和模型服务的实例中运行：

```bash
python -m scripts.run_merge_family_experiment prepare \
  --creatives examples/creatives.demo.json \
  --workdir /tmp/merge_family_vnext
```

然后：

1. 人工复核 `review_universe.json`，只填写确认后的 `confirmed_family_id`、`confirmed_ending`、`confirmation_status` 和 `rationale`。清单不包含 AI 预判，避免锚定。
2. 冻结 gold：`python -m scripts.run_merge_family_experiment freeze --workdir /tmp/merge_family_vnext`
3. 固定抽样：`python -m scripts.run_merge_family_experiment sample --workdir /tmp/merge_family_vnext`
4. 运行实验：`python -m scripts.run_merge_family_experiment run --workdir /tmp/merge_family_vnext`
5. 生成报告：`python -m scripts.run_merge_family_experiment report --workdir /tmp/merge_family_vnext`

`run` 是唯一能发起真实模型请求的子命令，硬上限为每个实验目录 100 次请求，包含重试。语义灰区不重复掷骰；真实错误与 `not_run` 分开记录。生产数据库和 Neo4j 只读，实验结果只写 `--workdir`。

## 零调用验证

```bash
cd apps/api
python -m pytest tests/test_merge_family_experiment.py -q
python -m pytest tests/test_run_merge_family_experiment.py -q
```

这些测试覆盖配置校验、完整 gold 划分、固定分层抽样、版本化缓存、跨进程预算、排他锁、灰区路由、结果复用和指标分母，不需要真实素材或模型服务。

## 当前限制

- 当前协议只支持单产品实例、两组共 13 项的离线评估，不能直接外推到其他产品或素材类型。
- 公开分类法不代表判定效果已经通过验收。此前小样本实验同时暴露过错拆、漏并、证据漏帧和 token 截断，不能据此开放生产自动化。
- `complete` 只表示执行完成，不表示归族质量达标。必须同时查看 over-split、over-merge、灰区、错误、覆盖率和人工复核成本。
- 公开示例和单元测试用于协作验证工程行为，不提供真实效果基准。
