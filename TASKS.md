# TASKS.md — 任务看板

> 由 Lead 维护。CLAUDE 只能改"状态"和"产出"两列。
> 状态：`TODO` / `DOING` / `BLOCKED` / `REVIEW` / `DONE`
>
> **2026-05-10 H+1:55 大重构**：发现 `docs/赛题.md` 完整版有 P0 必做项 RAG/Web 前端/后端 API/多格式上传/官方 schema，原计划严重不达标。Lead（Copilot）切 Plan A 全面扩张。

---

## 已完成（保留作历史，不再动）

| ID | 任务 | 负责 | 状态 | 产出 |
|---|---|---|---|---|
| T-00 | 赛题 PDF→md | CLAUDE | DONE | `docs/赛题.md` |
| T-01 | PDF→RawDoc（PDF2MD 重做） | CLAUDE | DONE（2 本） | `data/raw/{03,07}.json`、`data/md/{03,07}.md` |
| T-03 | LLM 客户端 | Copilot | DONE | `src/llm/client.py` |
| T-05 | 单本 KG 构建（NetworkX） | Copilot | DONE | `src/kg/build.py` |
| T-06 | 跨教材实体对齐 v1 | Copilot | DONE | `src/merge/align.py` |
| T-09 | 整合报告 v1 | Copilot | DONE | `src/merge/report.py` |

> T-04（extract.py）和 T-07（compress.py）作废 v1，按新 schema 重写：T-N04/T-N07。

---

## P0 — 评审必须项（截止 H+4）

### 数据层

| ID | 任务 | 负责 | 状态 | 验收 | 产出 |
|---|---|---|---|---|---|
| T-N01 | PDF2MD 跑剩余 5 本（01/02/04/05/06） | CLAUDE | DONE | `data/md/*.md` 5 个 + `data/raw/*.json` 5 个 | 7 本全完成，见 REPORT |
| T-N02 | Chunker（500–800 字 + 50–100 overlap，含元数据） | CLAUDE | DONE | `Chunk[]`，每块带 `book_id/chapter/section/page/char_start/char_end`，n_chars∈[500,800]，overlap∈[50,100] | `data/chunks/*.json` 7 本 4284 chunks |
| T-N02b | 章节结构化 `chapters[]`（对齐官方 schema） | Copilot | TODO | 从 md 标题树提，写入 RawDoc | `data/raw/*.json` 增加 `chapters[]` |

### 知识图谱层（按官方 schema 重写）

| ID | 任务 | 负责 | 状态 | 验收 | 产出 |
|---|---|---|---|---|---|
| T-N04 | LLM 抽取（官方 node/edge schema + 4 关系枚举） | Copilot | TODO | 节点 `{id,name,definition,category,chapter,page}`；边 `{source,target,relation_type∈{prerequisite,parallel,contains,applies_to},description}` | `src/kg/extract.py` |
| T-N05 | 图构建升级（消费新 schema） | Copilot | TODO | 双写 graphml + json | `src/kg/build.py` |
| T-N06 | 跨教材对齐升级（embedding + 名称归一双策略 + 决策对象） | Copilot | TODO | `{action:merge/keep/remove, affected_nodes, result_node, reason, confidence}` | `src/merge/align.py` + `data/report/decisions.json` |
| T-N07 | 压缩到 ≤30% **字符数口径** + 教学完整性自检 | Copilot | TODO | `original_chars/merged_chars/ratio`；前置依赖断链检测 | `src/merge/compress.py` + `data/report/compression.json` |

### RAG 层（全新 P0）

| ID | 任务 | 负责 | 状态 | 验收 | 产出 |
|---|---|---|---|---|---|
| T-R01 | 向量嵌入（BGE-small-zh） | Copilot | TODO | 离线 embed | `src/rag/embed.py` |
| T-R02 | 向量库（Chroma 持久化） | Copilot | TODO | `index/query` | `src/rag/store.py` |
| T-R03 | 带引用问答 | Copilot | TODO | `{answer, citations, source_chunks}` | `src/rag/qa.py` |
| T-R04 | **GraphRAG**（出彩点 ⭐） | Copilot | TODO | 检索 chunk + 子图同时喂 LLM | `src/rag/graph_rag.py` |

### 后端层（全新 P0）

| ID | 任务 | 负责 | 状态 | 验收 | 产出 |
|---|---|---|---|---|---|
| T-B01 | FastAPI 骨架 + CORS + 静态托管前端 | Copilot | TODO | `uvicorn src.api.app:app` | `src/api/app.py` |
| T-B02 | `POST /api/upload`（PDF/MD/TXT/DOCX） | Copilot | TODO | 落 `data/uploads/`，返回 `book_id` | `src/api/routes/upload.py` |
| T-B03 | `POST /api/parse`（异步 PDF→md→RawDoc→chunks→triples→KG） | Copilot | TODO | 进度查询 + 幂等 | `src/api/routes/parse.py` |
| T-B04 | `/api/rag/index` `/api/rag/query` `/api/rag/status` | Copilot | TODO | 字段同官方示例 | `src/api/routes/rag.py` |
| T-B05 | `/api/graph/{book_id}` `/api/graph/merged` `/api/merge` `/api/compress` | Copilot | TODO | ECharts 友好 json | `src/api/routes/graph.py` |
| T-B06 | `/api/chat` 多轮对话 | Copilot | TODO | function-calling agent | `src/api/routes/chat.py` |

### 前端层（全新 P0）

| ID | 任务 | 负责 | 状态 | 验收 | 产出 |
|---|---|---|---|---|---|
| T-F01 | SPA 三栏布局 | CLAUDE | DONE | 单 HTML（Vue3 CDN + ECharts CDN） | `src/web/index.html` |
| T-F02 | 教材上传 + 列表 + 进度 | CLAUDE | DONE | 调 `/api/upload` + `/api/parse` + 拖拽 + 轮询状态 | 同上 |
| T-F03 | 知识图谱 ECharts（点击/频次/教材色/缩放/拖拽/搜索） | CLAUDE | DONE | 满足官方 §3.1(3) 全部 + mock 数据兜底 | 同上 |
| T-F04 | RAG 问答面板（带引用 + 展开 chunk） | CLAUDE | DONE | 满足官方 §3.1(5) + GraphRAG/vanilla 切换 | 同上 |
| T-F05 | 多轮对话 + 决策回放卡片 + 一键反悔（出彩点 ⭐） | CLAUDE | DONE | diff_log 卡片化 + undo API | 同上 |
| T-F06 | 整合报告 Tab + 压缩比统计可视化 | CLAUDE | DONE | 原始/整合/百分比 + 压缩进度条 + 完整性指标 | 同上 |

### Agent 层

| ID | 任务 | 负责 | 状态 | 验收 | 产出 |
|---|---|---|---|---|---|
| T-A01 | Tools（9 个，见 ARCHITECTURE §7.3） | Copilot | TODO | 自测 | `src/chat/tools.py` |
| T-A02 | KnowledgeAgent（function-calling loop） | Copilot | TODO | 3 个典型提问跑通 | `src/chat/agent.py` |

### 文档层（D 维 20 分核心）

| ID | 任务 | 负责 | 状态 | 验收 | 产出 |
|---|---|---|---|---|---|
| T-D01 | `docs/Agent架构说明.md`（Mermaid + 决策论证 + RAG pipeline + 取舍） | Copilot | TODO | 含创新章节 | 同左 |
| T-D02 | `docs/系统设计.md`（架构图+数据流+选型+API 表） | Copilot | TODO | 含请求/响应示例 | 同左 |
| T-D03 | `docs/需求分析.md` | Copilot | TODO | 粒度/重复判定/压缩比/RAG 分块依据 | 同左 |
| T-D04 | `report/整合报告.md` | Copilot | TODO | 五项齐全+教学完整性 | 同左 |
| T-D05 | `README.md` 重写 | Copilot | TODO | 依赖+步骤+配置+运行+部署 | 同左 |

---

## P1 — 出彩点（截止 H+5）

| ID | 任务 | 负责 | 状态 | 描述 |
|---|---|---|---|---|
| T-X01 | 教学完整性自检（前置依赖断链） | Copilot | TODO | 与 T-N07 合并 |
| T-X02 | 决策回放 + 一键反悔 | Copilot+CLAUDE | TODO | 后端 diff_log API + 前端卡片 |
| T-X03 | RAG 自建 benchmark（20 题 + 评测脚本） | Copilot | TODO | `bench/qa_set.json` + `scripts/eval_rag.py` |
| T-X04 | GraphRAG vs vanilla RAG 量化对比 | Copilot | TODO | 写入 `docs/Agent架构说明.md` |
| T-X05 | 跨教材矛盾检测 | Copilot | TODO | `src/merge/contradict.py` |
| T-X06 | Docker compose 一键部署 | Copilot | TODO | `docker-compose.yml` |
| T-X07 | 部署到魔搭创空间 | Copilot | TODO | 评审硬要求 |

---

## P2 — 论文（可选，截止后 24h）

| ID | 任务 | 负责 | 状态 | 描述 |
|---|---|---|---|---|
| T-P01 | 技术报告：GraphRAG vs vanilla RAG | Copilot | TODO | 实验设计+数据+图表 |

---

**硬规矩**：
1. P0 不全 DONE 不准摸 P1（T-X01/X04 例外，伴随 T-N07/T-R04 同时做）
2. RAG / 后端 / 前端三线并行；CLAUDE 主前端，Copilot 主后端
3. 冲突由 Lead 在 [MESSAGES.md](MESSAGES.md) 发 DECISION
