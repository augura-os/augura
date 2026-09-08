# telemetry/ — 使用数据收集模块

本目录是 Augura 唯一的数据上报通道（PRIVACY.md §1.1）。设计原则：**命名诚实、白名单可审计、失败静默**。

## 收集什么（allowlist.py 写死）

| 事件 | 字段 | 用途 |
|---|---|---|
| `session_start` | app_version, os_family, timestamp_bucket | 版本/环境分布 |
| `feature_click` | feature_name, timestamp_bucket | 功能迭代 |
| `error` | error_code, stack_signature, timestamp_bucket | 稳定性 |
| `correction` | entity_type, field, old_value, new_value, timestamp_bucket | ★ 监督修正对（模型训练核心资产） |

## 永不收集（代码层面不读取）

- 素材文件本体 / 本地文件路径与文件名
- 投放明细行（spend / ROAS / CPI 原始值）
- 用户身份信息 / 设备唯一标识

## 架构

```
allowlist.py   白名单：不在表内的字段直接丢弃
anonymize.py   实例 ID（90 天旋转）+ 时间戳 6 小时桶
client.py      SQLite 本地队列 → 批量 POST → 失败静默丢弃
export.py      从 edit_logs 导出修正对（行为数据已在落库，无需新埋点）
```

上报端点在设置页配置（`telemetry_endpoint`），未配置时一切为 no-op。
