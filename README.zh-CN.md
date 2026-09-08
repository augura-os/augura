<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
  <img alt="Augura" src="docs/assets/logo-light.svg" width="72">
</picture>

# Augura

**素材会过时，做素材的经验不该过时。**

面向海外手游 UA 团队的本地创意智能系统。<br/>
把每条素材拆成结构、连成图谱，让每一次人工判断都沉淀为团队资产。

[![CI](https://github.com/augura-os/augura/actions/workflows/ci.yml/badge.svg)](https://github.com/augura-os/augura/actions/workflows/ci.yml)

**[English](README.md) | 简体中文**

</div>

## Augura 是什么？

每个 UA 团队都有同一个隐痛：爆款为什么爆，只有设计出它的那个创意策划知道；他离开，经验就跟着走。素材散在网盘里，数据躺在 Excel 里，"这条和上个月那条是不是同一个创意"这种判断，每周都要靠人脑重新做一遍。

Augura 把这件事系统化：上传素材，AI 把它拆成钩子、冲突、玩法、奖励；拆完的素材自动聚成 Creative，连成一张 DNA → Creative → Variant → Asset 的图谱；此后每一次"合并还是拆分"、"这条裂变有没有效"的人工判断，都会被记住——下次同类情况，系统先给建议，你只需要点确认。

可以理解为本地优先的 Creative Intelligence 基础设施：数据默认存在你的机器上（AI 分析时抽帧发给你自己配置的模型服务），判断留给你，重复劳动交给系统。

## 功能特性

- **AI 素材解析** — 视频抽帧后由视觉模型输出结构化 JSON（钩子/冲突/玩法/奖励/角色/情绪/标签）。分析没把握的自动进人工复核，不让 AI 硬猜。
- **创意图谱** — 五层知识图谱（Neo4j）可视化整个素材世界。KS_EN 和 KS_KR 的同名文件？系统认出那是同一条素材的两个市场版本，等你一句话合并。
- **合并守卫** — 你裁过"这俩不是同一个创意"的组合，系统永久记住。谁想再合并，必须写明理由。判断只做一次，不做第二次。
- **裂变演化** — 每条 Variant 记录从谁裂变而来、改了什么因子（换语言/换画幅/换前贴...）。投放数据回流后判定有效无效，方向耗尽的方向系统会劝你停。
- **审核收件箱** — 疑似合并、未归族、观察对结案，每类都先由 LLM 自洽投票给出建议和理由。AI 判错的代价有刹车：改判率超限，自动降级为仅建议模式。
- **指标可配** — 付费成本红线、ROAS 绿线、D3/次留参不参与判定，Settings 页自己调，不用改代码。
- **数据主权** — 素材、投放明细、分析结果全部存在你自己的电脑 / 服务器 / NAS（本地数据卷）。创意基因共建计划默认开启但只回传白名单字段（素材名哈希脱敏），可一键关闭，代码可审计。

## 快速安装

**前置要求**：[Docker Desktop](https://www.docker.com/products/docker-desktop/)。

**macOS / Linux**——一行命令，无需下载仓库：

```bash
curl -fsSL https://raw.githubusercontent.com/augura-os/augura/main/scripts/install.sh | bash
```

macOS 也可以下载仓库后双击 `install.command`；**Windows** 双击 `install.bat`。三者都幂等——重复运行即更新重启。

或手动（在仓库目录下）：

```bash
cp .env.example .env
docker compose up -d          # 发布版：直接拉取 ghcr 预构建镜像（约 2-3 分钟）
docker compose up --build -d  # 开发/私有部署：本地构建（首次约 20 分钟）
```

| 服务 | 地址 |
| --- | --- |
| Web（首页 = 创意图谱） | http://localhost:3000 |
| API | http://localhost:8000/docs |
| Neo4j Browser | http://localhost:7474（首次安装向导会生成随机密码，见 .env） |
| MinIO Console | http://localhost:9001（同上） |

> **部署边界**：Augura 是**单用户本地工具**——没有登录认证/多租户，所有端口默认只绑本机（127.0.0.1）。**不要直接部署到公网服务器**；团队/服务器部署需自行加反向代理 + 认证层。

## 快速上手

### 1. 配置 AI Key

打开 **Settings**，填入任一 OpenAI 兼容接口的 Key（OpenAI、Kimi 都行）。不上传素材不产生任何调用。

### 2. 上传一批素材

**Upload** 页拖入 mp4 / png / xlsx（Facebook 报表）。视频分析每条需要几分钟——去喝杯水，图谱会慢慢长出来。

### 3. 看图谱，处理收件箱

首页图谱上，系统已经把疑似同创意的素材连成候选。打开**收件箱**：每条候选都带 LLM 的建议和理由，合并、拆分、归族，都是一键。

### 4. 回流投放数据

每周把新的投放 Excel 传上来，消耗、付费、ROAS 自动匹配回素材。**今日建议**面板会告诉你：哪些加注、哪些迭代、哪些该停——每条建议附带背后的数字。

到此，你的创意资产开始积累了。🎉

## 多个项目？

一个 Augura 实例 = 一个项目：图谱、素材库、DNA、收件箱都是实例级全局。要管多个游戏/项目，就**每项目跑一套独立实例**（数据完全隔离）：

```bash
git clone <repo> augura-game-a
git clone <repo> augura-game-b
```

在 `augura-game-b/` 里新建 `docker-compose.override.yml`，把宿主端口错开（容器间通信走内部网络，不受影响；`!override` 为整体替换端口，需要 Docker Compose v2.24+）：

```yaml
services:
  postgres:
    ports: !override
      - "127.0.0.1:5434:5432"
  neo4j:
    ports: !override
      - "127.0.0.1:7475:7474"
      - "127.0.0.1:7688:7687"
  minio:
    ports: !override
      - "127.0.0.1:9002:9000"
      - "127.0.0.1:9003:9001"
  api:
    ports: !override
      - "127.0.0.1:8001:8000"
  web:
    ports: !override
      - "127.0.0.1:3001:80"
```

两套各自 `docker compose up -d`，B 项目访问 http://localhost:3001。AI Key、市场前缀等在各自实例的 Settings 页独立配置。注意：实例间无法交叉分析，且每套独占一份 Postgres/Neo4j/MinIO 内存开销。单实例多项目（项目切换器）在路线图中，schema 已预留 `project_id`。

## 架构

```
┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
│  React Web   │────>│  FastAPI     │────>│   PostgreSQL     │
│  (图谱/收件箱)│<────│  (规则引擎)   │<────│   (结构化数据)    │
└──────────────┘     └──────┬───────┘     └──────────────────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
        ┌─────┴─────┐ ┌─────┴─────┐ ┌─────┴─────┐
        │   Neo4j   │ │   MinIO   │ │ LLM 提供方 │
        │ (创意图谱) │ │ (素材文件) │ │(分析/判定) │
        └───────────┘ └───────────┘ └───────────┘
```

| 层级 | 技术栈 |
|------|--------|
| 前端 | React + TypeScript + Vite + React Flow |
| 后端 | FastAPI + SQLAlchemy + Alembic（Python 3.12） |
| 存储 | PostgreSQL 16 · Neo4j 5 · MinIO |
| AI | 任意 OpenAI 兼容接口（OpenAI / Kimi 等），视觉模型分析 + 自洽投票判定 |

## 开发

参与贡献请参阅 [贡献指南](CONTRIBUTING.md)（trunk-based + PR + CI）。

```bash
cd apps/web && npm install && npm run dev   # 前端热更新，代理到 :8000
cd apps/api && pytest                        # 后端测试
```

## 数据与隐私

详见 [PRIVACY.md](PRIVACY.md)。准确版：**原始素材与投放明细默认保存在你的机器上；AI 分析时，素材抽帧与结构化信息会发送至你在 Settings 配置的模型服务**（OpenAI 兼容接口，数据去向取决于你选的提供方）。我们只需要你的"判断"，不需要你的"资产"。

「创意基因共建计划」回传字段全清单（默认开启，Settings 一键关闭）：

| 回传内容 | 字段 | 用途 |
| --- | --- | --- |
| 功能点击 | 功能名、时间桶 | 功能使用率 |
| 修正行为 | 字段、改动方向（素材名哈希脱敏） | 模型训练核心资产 |
| 聚合分布 | 品类、指标桶、计数 | 行业基准研究 |

运行保障数据（始终开启，仅报错与版本信息，用于兼容性支持）：

| 回传内容 | 字段 | 用途 |
| --- | --- | --- |
| 启动事件 | 版本、操作系统、时间桶 | 兼容性支持 |
| 报错 | 错误码、堆栈签名、时间桶 | 稳定性修复 |

## 为什么叫 "Augura"？

设计的生产有 Figma，代码的生产有 Git，投放的执行有广告后台——但"创意是怎么一步步迭代出来的"这件事，从来没有过自己的系统。

Augura 想做创意生产的操作系统：素材是进程，图谱是文件系统，而每一次人工裁决都是写进内核的经验。单个优化师的判断力是有限的，但一个会积累的团队不是。

## 开源协议

[Modified Apache 2.0](LICENSE)（Apache 2.0 全文并入 + 附加条件），署名信息见 [NOTICE](NOTICE)：

- 正常使用、二次开发、企业内部部署，完全自由。
- 拿本软件向第三方提供托管服务 / SaaS，需取得商业授权。
- 不得移除界面中的 LOGO 与版权信息；不使用界面的用法（只跑后端）不受此限。
