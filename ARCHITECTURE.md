# ARCHITECTURE.md — 架构与接口契约

> Lead 维护。**实现跨模块代码前必读本文件。**接口变更需 Lead 在 [MESSAGES.md](MESSAGES.md) 发 DECISION。

---

## 1. 总体数据流

```
PDF (textbooks/*.pdf)
  │  T-01: PyMuPDF 解析
  ▼
RawDoc JSON (data/raw/{book}.json)
  │  T-02: 章节切分 + 清洗
  ▼
Chunk[] (data/chunks/{book}.json)
  │  T-04: LLM 实体/关系抽取
  ▼
Triple[] (data/triples/{book}.json)
  │  T-05: NetworkX 构图
  ▼
单本 KG (data/kg/{book}.graphml)
  │  T-06: 跨教材实体对齐
  ▼
合并 KG (data/kg/merged.graphml)
  │  T-07: 压缩到 ≤30%
  ▼
精华 KG + 报告 (data/report/*)
  │  T-08/T-11: 前端可视化  +  T-10: 多轮对话
  ▼
Demo
```

## 2. 数据 Schema（强约束）

### 2.1 `RawDoc`（T-01 输出 + T-N02b 增强）

```json
{
  "book_id": "03_生理学",
  "title": "生理学",
  "total_chars": 615000,
  "pages": [
    {"page_no": 1, "text": "...", "bbox_blocks": []}
  ],
  "toc": [
    {"level": 1, "title": "第一章 绪论", "page_no": 1}
  ],
  "chapters": [
    {
      "chapter_id": "03_生理学::ch01",
      "title": "第一章 绪论",
      "page_start": 1,
      "page_end": 12,
      "content": "...章节正文（markdown）...",
      "char_count": 18432
    }
  ]
}
```

### 2.2 `Chunk`（T-N02 输出，RAG 友好）

```json
{
  "chunk_id": "03_生理学::ch02::s003::00012",
  "book_id": "03_生理学",
  "chapter": "第二章 细胞的基本功能",
  "section": "第三节 细胞的电活动",
  "page": 35,
  "char_start": 12450,
  "char_end": 13180,
  "n_chars": 730,
  "text": "...保留 markdown 原样..."
}
```

约束：
- `n_chars` ∈ [500, 800]
- 相邻 chunk 重叠 ∈ [50, 100] 字符（sliding window）
- 不跨章；段落优先（遇 `\n\n` 优先切）；不切断 `$$...$$` 公式块

### 2.3 `KGNode`（T-N04 抽取输出，对齐官方 schema）

```json
{
  "id": "03_生理学::ch02::node_017",
  "name": "动作电位",
  "definition": "细胞受到刺激后膜电位发生的一次快速可逆的倒转...",
  "category": "核心概念",
  "chapter": "第二章 细胞的基本功能",
  "page": 35,
  "book_id": "03_生理学",
  "chunk_id": "03_生理学::ch02::s003::00012"
}
```

`category` 推荐枚举（不强制）：`核心概念 | 现象 | 过程 | 结构 | 物质 | 疾病 | 方法`

### 2.4 `KGEdge`（T-N04 抽取输出，4 关系枚举）

```json
{
  "source": "03_生理学::ch02::node_017",
  "target": "03_生理学::ch02::node_005",
  "relation_type": "prerequisite",
  "description": "理解动作电位需要先掌握静息电位"
}
```

`relation_type` 强约束枚举：

| 枚举值 | 说明 |
|---|---|
| `prerequisite` | B 学习需先掌握 A |
| `parallel` | 同层级平行概念 |
| `contains` | 上位概念包含下位 |
| `applies_to` | A 是 B 的应用场景 |

### 2.5 合并图节点/边（NetworkX 内部表示，T-N05/T-N06 后）

节点累积属性：
- `name, definition, category, book_ids:list[str], chapters:list[str], pages:list[int], n_mentions:int, alias_names:list[str]`

边累积属性：
- `relation_type, descriptions:list[str], book_ids:list[str], weight:int`

### 2.6 整合决策对象（T-N06 输出 → `data/report/decisions.json`）

```json
{
  "decision_id": "merge_001",
  "action": "merge",
  "affected_nodes": ["03_生理学::node_015", "05_病理学::node_032"],
  "result_node": "merged::node_001",
  "reason": "两本教材都讲解'炎症'，措辞不同但定义等价；保留病理学版本（描述更完整）",
  "confidence": 0.92
}
```

`action` ∈ `merge | keep | remove`

## 3. 技术选型

| 模块 | 选型 | 备注 |
|---|---|---|
| PDF 解析 | PyMuPDF (`fitz`) | 速度快，能拿 bbox |
| LLM | ModelScope 免费 API | 走 `src/llm/client.py` 统一封装 |
| 抽取模型 | Qwen2.5-72B-Instruct（首选）/ 备选 14B | JSON mode 输出 |
| 知识图谱 | NetworkX + graphml | **不上 Neo4j**，节省部署 |
| 可视化 | ECharts graph | 静态 html + 本地 json，无后端 |
| 对话 | 简单 CLI / FastAPI 二选一 | P2 阶段定 |

## 4. 范围收敛策略

**P0 阶段只跑 2 本：`03_生理学.pdf` + `07_病理生理学.pdf`**
- 理由：内容高度互补，最能演示"跨教材整合"价值
- P1 阶段验证有效后，横向扩展到全部 7 本

## 5. 压缩口径定义（按官方要求重订）

> 题目原文："整合后保留的内容总字数不超过原始总字数的 30%。系统需要在前端展示压缩比统计（原始总字数 → 整合后字数 → 压缩比百分比）"

**主口径（官方对齐）**：
```
original_chars  = sum(每本 RawDoc.total_chars)
merged_chars    = sum(merged_node.definition 长度) + sum(merged_edge.description 长度)
ratio           = merged_chars / original_chars   # 必须 ≤ 0.30
```

**副口径（参考）**：节点数压缩比 = 合并后节点数 / 7 本独立节点总和

两口径都写入 `data/report/compression.json`，并暴露给 `GET /api/compress/stats`。

### 教学完整性自检（T-X01，与压缩同步）

压缩取舍后必须扫一遍图：
- 找出所有 `prerequisite` 边 `(A → B)`，B 留下但 A 被删 → **断链告警**
- 自动把所有断链的 A 加回精华图（强制保留前置依赖）
- 报告写入 `data/report/integrity.json`：`{broken_prereqs:[...], auto_recovered:[...]}`

## 6. LLM Prompt 公共约定

- 一律要求 LLM 输出 **严格 JSON**，外层用 ```json fences
- 解析失败时 retry ≤ 2 次，仍失败记录到 `data/errors/` 跳过
- 单次 prompt token ≤ 4k，留足输出空间

## 7. Agent 架构（T-10 多轮对话）

### 7.1 选型决策：单 agent + 工具调用，**不上 multi-agent**

理由：
1. 题目要求是"产品形态"为 agent，不是内部架构必须是 multi
2. 5 小时窗口承担不起多 agent 框架接入成本
3. PDF→图谱→压缩 是确定性 pipeline，无协商场景
4. ModelScope 免费配额有限，多 agent 互相 prompt 烧 token

### 7.2 KnowledgeAgent 设计

```
KnowledgeAgent (单体)
├── llm        : LLMClient (复用 src/llm)
├── graph      : 当前合并图（merged.json + 用户改动 patch 累加）
├── diff_log   : list[GraphEdit]  # 全部用户操作可追溯
└── tools      : function-calling 工具集（见 7.3）
```

会话状态机（极简）：

```
USER 提问 → LLM 选 tool → 执行 tool → tool 结果回灌 LLM → 自然语言回答 → 等下一轮
                                                                   ↑
                              如果改图，diff_log 追加，graph 立即更新
```

### 7.3 工具集（function-calling JSON schema）

| tool 名 | 入参 | 行为 | 副作用 |
|---|---|---|---|
| `search_kg` | `query: str, k: int=10` | 在 graph 节点 name/aliases 模糊搜 | 只读 |
| `show_subgraph` | `node_id: str, hop: int=1` | 返回 1~2 跳邻居子图（节点+边） | 只读 |
| `compare_books` | `node_id: str` | 该实体在各教材中的 evidence 片段对比 | 只读 |
| `propose_merge` | `node_a: str, node_b: str, reason: str` | 把两个节点合并为同一规范节点 | 写图 + diff_log |
| `propose_split` | `node_id: str, by_book: bool=true` | 按教材或类型拆节点 | 写图 + diff_log |
| `update_relation` | `head: str, tail: str, new_relation: str` | 修改/新增/删除一条边 | 写图 + diff_log |
| `add_evidence` | `head: str, tail: str, evidence: str, book_id: str` | 给现有边追加证据片段 | 写图 + diff_log |
| `recompress` | `target_ratio: float=0.30` | 在改后的 graph 上重跑 T-07 压缩 | 写 compression.json |
| `export_report` | `path: str` | 重新生成 summary.md | 写报告 |

### 7.4 GraphEdit (diff_log 元素) schema

```json
{
  "edit_id": "uuid",
  "ts": "ISO8601",
  "op": "merge|split|update_relation|add_evidence|delete_node|delete_edge",
  "actor": "teacher|agent",
  "args": {...},
  "rationale": "老师/agent 给出的理由（自然语言）"
}
```

`diff_log` 落 `data/chat/diff_log.jsonl`，每轮 append。

### 7.7 前端决策回放（出彩点 ⭐）

所有写图工具（`propose_merge/propose_split/update_relation/add_evidence`）执行后向前端推送：
```
{"type":"diff_card", "edit":{...GraphEdit...}, "before_subgraph":{...}, "after_subgraph":{...}}
```
前端把每个 edit 渲染为卡片：左右对比子图 + "撤销"按钮 → 调 `POST /api/chat/undo {edit_id}`。

### 7.5 实现路径（T-10 子任务，时间盒 1 小时）

1. **T-10a (15min)**：`src/chat/tools.py` ── 工具实现，纯函数对 graph dict 操作
2. **T-10b (15min)**：`src/chat/agent.py` ── 包 LLM 的 function-calling loop（OpenAI tools 风格）
3. **T-10c (15min)**：`src/chat/loop.py` ── CLI REPL，读 stdin，调 agent，pprint 回答
4. **T-10d (15min)**：联调 + 演示脚本，准备 3 个老师典型提问

### 7.6 演示话术（评委友好）

- 老师："`心肌细胞`和`心肌`是同一个概念吗？" → agent 调 `compare_books` → 发现别名 → 调 `propose_merge`
- 老师："`高血压`这个节点在生理学和病理生理学里讲的是同一回事吗？" → agent 调 `compare_books` → 给出对比
- 老师："把整合后的内容压缩到 25%" → agent 调 `recompress(0.25)` → `export_report`

---

---

## 8. RAG Pipeline（T-R*，P0 必做）

### 8.1 分块策略

见 §2.2 `Chunk`。500–800 字符 + 50–100 overlap。**理由**（写入 `docs/需求分析.md`）：
- 中文医学语义密度高，500–800 比英文常用 200–500 更合适，能容纳 1–2 段完整论述
- overlap 50–100 防止跨块概念被切断（如 "动作电位\n\n是膜电位..."）
- 章节边界优先（不跨章）→ 引用回原文时章节定位天然准确

### 8.2 嵌入模型

- 默认：`BAAI/bge-small-zh-v1.5`（512 维，中文专用，本地 sentence-transformers）
- 备选：`paraphrase-multilingual-MiniLM-L12-v2`
- 索引存储：ChromaDB 持久化到 `data/vector/`（collection 名 = `book_id`，另有 `_all` 全集合）

### 8.3 检索

- vanilla：`top_k=5`，cosine
- 加分：BM25 关键词召回 + 向量召回 取并集 → cross-encoder rerank（P1，时间允许做）

### 8.4 GraphRAG（出彩点 ⭐ T-R04）

```
query → vector retrieval → top_5 chunks
                        → 每个 chunk 抽出实体（按图谱节点名 fuzzy 匹配） → 1 跳邻居子图
                        → 把 chunks + 子图三元组 一起进 LLM prompt
                        → 回答 + 引用 + "知识脉络" 字段（图谱节点链）
```

相对 vanilla 的优势：跨教材的隐性关联能被检索到（同一概念在多本书的不同章节），评测可量化（T-X03 benchmark）。

### 8.5 生成约束 prompt

硬约束：
- 只基于 context
- 每条事实附 `[教材, 第X章, 第X页]`
- 找不到 → "当前知识库中未找到相关信息"

返回结构（与官方 §3.1(5) 完全一致）：
```json
{
  "answer": "...",
  "citations": [{"textbook":..., "chapter":..., "page":..., "relevance_score":0.92}],
  "source_chunks": ["...原文..."],
  "graph_context": ["动作电位 -[prerequisite]-> 静息电位", ...]   // GraphRAG 独有
}
```

---

## 9. 后端 API 契约（T-B*）

FastAPI，全部 JSON。CORS `*`（开发期）。

### 9.1 教材管理

| Method | Path | 说明 |
|---|---|---|
| `POST` | `/api/upload` | multipart：file + 可选 title。返回 `{book_id, filename, size, format}` |
| `GET`  | `/api/books` | 列出已上传教材及其状态（uploaded/parsing/parsed/indexed） |
| `POST` | `/api/parse` | `{book_id}` → 异步触发 PDF→md→RawDoc→chunks→triples→KG。返回 `{task_id}` |
| `GET`  | `/api/parse/status/{book_id}` | 进度 |

### 9.2 RAG

| Method | Path | 说明 |
|---|---|---|
| `POST` | `/api/rag/index` | `{book_ids:[]}` → 建/重建向量索引 |
| `POST` | `/api/rag/query` | `{question, k:5, mode:"vanilla"\|"graph"}` → §8.5 结构 |
| `GET`  | `/api/rag/status` | `{n_books, n_chunks, model, ready:bool}` |

### 9.3 知识图谱

| Method | Path | 说明 |
|---|---|---|
| `GET`  | `/api/graph/{book_id}` | ECharts 友好 `{nodes, edges}` |
| `GET`  | `/api/graph/merged` | 合并图 |
| `POST` | `/api/merge/run` | `{book_ids:[]}` → 触发对齐 + 决策。返回 decisions list |
| `POST` | `/api/compress/run` | `{target_ratio:0.30}` → 压缩 + 完整性自检 |
| `GET`  | `/api/compress/stats` | `{original_chars, merged_chars, ratio, integrity:{...}}` |

### 9.4 多轮对话

| Method | Path | 说明 |
|---|---|---|
| `POST` | `/api/chat` | `{session_id, message}` → `{reply, tool_calls:[], diff_cards:[]}` |
| `POST` | `/api/chat/undo` | `{edit_id}` → 撤销该次写图 |
| `GET`  | `/api/chat/history/{session_id}` | 历史 |

### 9.5 健康/静态

- `GET /` → 静态托管 `src/web/index.html`
- `GET /healthz` → `{ok:true}`

---

**变更日志**：
- 2026-05-10 H-0  / Copilot：初版
- 2026-05-10 H+1:25 / Copilot：新增 §7 Agent 架构
- 2026-05-10 H+1:55 / Copilot：**Plan A 大重构**——schema 对齐官方（chapters/官方 node/edge/4 关系枚举）、压缩改字符数口径、新增 §8 RAG、§9 API 契约
