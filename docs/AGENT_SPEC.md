# Augura MVP — 共享契约（AGENT SPEC）

本文件是前后端实现的**唯一权威契约**。任何一方不得擅自更改；如有冲突，保持现状并在报告中指出。

## 0. 项目目标（唯一目标）

本地可运行的 MVP，验证链路：
`上传视频/图片/Facebook Excel → AI 自动分析 → 自动标签 → 自动聚类为 Creative → Graph 可视化 → 人工修改 → 保存`。
除此之外不开发任何功能。

## 1. 目录结构（固定）

```
augura/
  apps/web/          # React + TS + Vite 前端（前端 worker 所有）
  apps/api/          # FastAPI 后端（后端 worker 所有）
  packages/shared/   # TS 共享类型（前端 worker 所有，纯 TS 源码包，无构建）
  packages/graph/    # Python Neo4j 仓库包（后端 worker 所有，pip 可安装）
  infra/docker/      # Dockerfile / nginx（主代理所有，禁止修改）
  docs/              # 文档
  uploads/           # 本地临时文件（docker 挂载到 api /app/uploads）
  docker-compose.yml # 主代理所有，禁止修改
```

## 2. 技术栈（固定）

- 前端：React 18+ / TypeScript **strict** / Vite / TailwindCSS / shadcn 风格组件 / React Router v6+ / TanStack React Query / Zustand / React Flow / axios。
- 后端：Python 3.12 / FastAPI / SQLAlchemy 2.x / Alembic / Pydantic v2。**禁止 Any，类型标注完整。**
- DB：PostgreSQL 16；Graph：Neo4j 5；对象存储：MinIO（SDK 用 `minio` 包）；AI：OpenAI API（`openai` 官方 SDK，vision 模型 + `text-embedding-3-small`）。

## 3. API 契约（后端实现，前端按此消费）

- 无路径前缀，根路径挂载。统一返回信封：

```json
{ "success": true, "data": {}, "message": "" }
```

- 错误时 `success=false`，`message` 为可读错误信息，`data=null`，HTTP 状态码语义化（400/404/500）。
- 前端通过**相对路径**调用（dev 由 vite proxy、docker 由 nginx 代理到 api:8000），前端代码中**禁止硬编码 host**。Vite dev proxy 与 nginx 均按路径前缀 `^(upload|assets|graph|analysis|settings)` 代理。

### 端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/upload` | multipart/form-data，字段名 `files`（可多文件）。支持 mp4/mov/png/jpg/xlsx/xls。返回已创建的 asset 列表。视频/图片自动触发后台 AI 分析；Excel 解析为 Performance 数据。 |
| GET | `/assets?search=` | 列表项：`{id, filename, file_type("video"\|"image"\|"excel"), thumbnail_url, created_at, analysis_status("pending"\|"processing"\|"completed"\|"failed"\|"none"), creative_name}` |
| GET | `/assets/{id}` | `{asset, analysis, tags, creative, performance}`；asset 内含 `media_url`、`thumbnail_url` |
| GET | `/assets/{id}/media` | 从 MinIO 流式返回原始文件（视频播放器用） |
| GET | `/assets/{id}/media?variant=thumb` | 返回缩略图（视频取首帧，图片为自身，excel 404） |
| PUT | `/assets/{id}` | body 为可编辑分析字段（见 §4 全部字段 + `creative_name`），保存 DB 并同步 Neo4j/聚类 |
| DELETE | `/assets/{id}` | 删除 DB 记录、MinIO 对象、Neo4j 节点 |
| GET | `/graph` | `{nodes: [{id, type: "creative"\|"variant"\|"asset"\|"tag"\|"dna", label, ref_id}], edges: [{id, source, target, type: "HAS_VARIANT"\|"HAS_ASSET"\|"HAS_TAG"\|"SIMILAR_TO"\|"HAS_CREATIVE"}]}`，供 React Flow 渲染（SIMILAR_TO = Creative 间观察对，虚线展示；dna = Pattern 层家族节点） |
| POST | `/analysis` | body `{asset_id}`，同步执行/重跑 AI 分析，返回分析 JSON |
| POST | `/graph/merge` | body `{source_creative_id, target_creative_id}`，合并两个 Creative |
| POST | `/graph/split` | body `{creative_id, variant_ids: string[]}`，把指定 Variant 拆出为新 Creative |
| GET | `/settings` | `{api_key_set, api_key_masked, base_url, vision_model, embedding_model}` |
| PUT | `/settings` | body 全可选：`{api_key?, base_url?, vision_model?, embedding_model?}`；`embedding_model` 显式传空串 = 禁用 embedding，聚类走本地文本相似度兜底 |

> 契约说明：用户需求中要求 Settings 页配置 OpenAI Key，但 API 清单未列 settings 端点，这是一个需求冲突点。按最小必要原则增加上述两个端点，已在最终报告中向用户说明。

## 4. AI 分析输出 Schema（严格 JSON，禁止自由输出）

```json
{
  "summary": "",
  "hook": "",
  "conflict": "",
  "gameplay": "",
  "reward": "",
  "characters": [],
  "environment": [],
  "emotion": [],
  "tags": [],
  "creative_name": "",
  "confidence": 0.0
}
```

- 视频：ffmpeg 抽 3 帧（首/中/尾）→ base64 → vision 模型（如 `gpt-4o`），`response_format` 用 strict JSON schema。
- 图片：直接送 vision 模型。
- Excel：走 pandas/openpyxl 解析，不调用 AI。
- OpenAI Key 读取顺序：DB settings 表 → 环境变量 `OPENAI_API_KEY`。未配置时分析状态置 `failed`，message 提示配置 Key。

## 5. Creative 聚类规则（MVP，无机器学习）

1. 分析完成后，对 `summary + tags` 文本调用 `text-embedding-3-small` 得 embedding（存 AnalysisResult 或 CreativeVariant，JSON 数组）。
2. 与全部已有 Creative 的代表 embedding 计算余弦相似度。
3. 最大值 ≥ **0.85** → 归入该 Creative；否则新建 Creative，名称为 AI 返回的 `creative_name`。
4. 每个素材上传后包一层 Variant（Variant 名默认取文件名去扩展名），关系：`Creative -HAS_VARIANT→ Variant -HAS_ASSET→ Asset -HAS_TAG→ Tag`。
5. 人工支持 merge / split / rename（rename 通过 PUT `/assets/{id}` 的 `creative_name` 或 merge/split 端点完成）。

## 6. PostgreSQL 表（10 + 1）

Project、Creative、CreativeVariant、CreativeAsset、Performance、Tag、TagAssignment、AnalysisResult、GraphNode、GraphEdge，另加 `settings`（key/value 表，用于 OpenAI Key，属契约说明中的冲突解决方案）。

- 主键统一 UUID 字符串。所有表含 `created_at` / `updated_at`。
- 所有字段用 JSON/JSONB 存储数组类数据，保证后续可扩展。
- GraphNode / GraphEdge 为 Neo4j 的 SQL 镜像（Neo4j 为关系事实源；`/graph` 优先读 Neo4j，Neo4j 不可用时回退 SQL 镜像）。
- Performance 字段建议：`asset_id`（可空）、`creative_name`、日期、impressions、clicks、spend、installs、原始行 JSON（Excel 列名不固定，宽松解析：列名包含 name/impression/click/spend/install 即识别）。
- Alembic 初始迁移包含全部表；api 容器启动命令已固定为 `alembic upgrade head && uvicorn ...`（见 infra/docker/api.Dockerfile，禁止改动 Dockerfile，alembic 配置必须适配该工作目录 `/app/apps/api`）。

## 7. Neo4j

节点标签：`Creative` / `Variant` / `Asset` / `Tag`（属性含 `ref_id` 对应 SQL 主键、`label`）。
关系：`(CreativeDNA)-[:HAS_CREATIVE]->(Creative)-[:HAS_VARIANT]->(Variant)-[:HAS_ASSET]->(Asset)-[:HAS_TAG]->(Tag)`；`(Creative)-[:SIMILAR_TO]->(Creative)` 表达观察对（边界规则 §4.1），仅存单方向（ref_id 小者为源），带 `reason` 属性。
`packages/graph` 提供 `GraphRepository`（同步 driver 即可），封装 upsert/delete/merge/split/read_graph，供 FastAPI 调用。包名 `creative-graph`，模块名 `graph`，`src/` 布局，含 `pyproject.toml`。

## 8. 环境变量（compose 已注入，后端读取）

`DATABASE_URL`、`NEO4J_URI`、`NEO4J_USER`、`NEO4J_PASSWORD`、`MINIO_ENDPOINT`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_BUCKET`、`OPENAI_API_KEY`、`UPLOAD_DIR`（默认 /app/uploads，用于 ffmpeg 临时帧）。

## 9. 前端页面（仅 5 个，React Router）

| 路由 | 页面 | 说明 |
| --- | --- | --- |
| `/` | Creative Graph | **系统首页**。React Flow 渲染 `/graph`。点击节点，右侧面板显示 Performance / Tags / Composition / Variant。支持选中两个 Creative 后 merge；选中 Variant 后 split。 |
| `/upload` | Upload | 拖拽上传（mp4/mov/png/jpg/xlsx/xls），POST `/upload`，显示上传进度与结果。 |
| `/assets` | Asset List | 表格：缩略图、文件名、上传时间、AI 分析状态、Creative 名称；搜索框（`?search=`）。行点击进入详情。 |
| `/assets/:id` | Asset Detail | 左侧视频播放器（`/assets/{id}/media`）；右侧 AI 分析结果表单：Hook/Conflict/Gameplay/Reward/Character/Environment/Tags/Summary，可编辑，点击保存 PUT `/assets/{id}`。 |
| `/settings` | Settings | 配置 OpenAI Key（GET/PUT `/settings`）。 |

### UI 规范

- 风格：Linear / Notion / Vercel 极简。大量留白，白底，细灰边框（`#e5e5e5` 级别），中性色文字，无渐变、无玻璃拟态、无炫酷动画。
- 左侧窄边栏导航（Graph / Upload / Assets / Settings）。
- 组件拆分：`components/`（ui 基础件 + 业务组件）、`hooks/`、`services/`（axios 封装）、`stores/`（Zustand）、`pages/`、`lib/`。TypeScript strict，禁止 `any`（确有必要时用 `unknown` 收窄）。
- 共享类型放 `packages/shared/src/index.ts`（API 信封、Asset、AnalysisResult、Graph DTO 等），web 通过 tsconfig/vite alias `@shared` 引用（相对路径 `../../packages/shared/src`）。
- `vite.config.ts` 必须：`server.host = true`，proxy 前缀 `^(upload|assets|graph|analysis|settings)` → `http://localhost:8000`；`npm run dev` 为 `vite`（CLI 的 --host/--port 参数可透传）。

## 10. 验证命令

- 后端：`python -m compileall apps/api/app packages/graph/src`（本机无依赖，仅语法检查；依赖在 Docker 内安装）。
- 前端：`cd apps/web && npm install && npm run build`（本机 npm 需 `npm.cmd`，或 PATH 加 `/c/Users/Administrator/AppData/Local/Programs/kimi-desktop/resources/resources/runtime/node`）。
- 整体：`docker compose up --build`（本机无 Docker，由用户执行）。

## 11. 非目标（禁止开发）

Dashboard、用户系统/登录、机器学习训练、标签体系管理页、批量操作、导出、通知、主题切换、任何超出 §9 的页面。

## 12. AI 提供方泛化（2026-07-17 更新）

应用户要求，AI 提供方从"仅 OpenAI"泛化为"任意 OpenAI 兼容接口"（OpenAI / Kimi / DeepSeek / OpenRouter 等）：

- Settings 页配置 `api_key` + `base_url` + `vision_model` + `embedding_model`，优先级：DB → 环境变量。
- Kimi 直连：`base_url=https://api.moonshot.cn/v1`，`vision_model=kimi-k2.5`（支持 base64 图片输入）。Kimi 不支持 strict json_schema → 自动降级 JSON Mode（输出仍经 Pydantic 严格校验）。
- **Kimi/DeepSeek 均无 embeddings 接口** → `embedding_model` 置空时，聚类自动使用本地 token-Jaccard 文本相似度（含中文单字+二元组），阈值 0.34。精度低于 embedding，但链路完整可用，人工 merge/split 可修正。
- `creatives.representative_text`（迁移 0002）存储文本签名用于兜底匹配。
