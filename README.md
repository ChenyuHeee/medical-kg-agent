# 医学教材整合知识图谱 AI Agent

> 第一届 AI 全栈黑客松参赛项目  
> 5 小时内交付：7 本医学教材 → 跨教材知识整合 → ≤30% 压缩 → RAG 问答 → 多轮对话迭代 → Web 可视化

## 1. 一图看懂

详见 [docs/Agent架构说明.md](docs/Agent架构说明.md)（含 Mermaid 架构图、对话循环时序图、整合流水线图）。

## 2. 启动

### 2.1 一键 Docker（推荐，5 分钟可复现）

```bash
cp .env.example .env          # 然后把 MODELSCOPE_API_KEY 填进去
docker-compose up -d --build  # 构建并后台启动
open http://localhost:8000/
```

容器内会挂载 `./data` 与 `./textbooks`，所有图谱、向量、日志均落盘到本地，重启不丢。

### 2.2 本地 Python（开发模式）

```bash
# 1) 装依赖
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2) LLM key（DeepSeek 或 ModelScope）
export MODELSCOPE_API_KEY='sk-xxx'
export MODELSCOPE_BASE_URL='https://api.deepseek.com/v1'   # DeepSeek
export MODELSCOPE_MODEL='deepseek-chat'

# 3) 拉起后端（同时挂载前端）
uvicorn src.api.app:app --reload --port 8000

# 4) 浏览器打开
open http://localhost:8000/
```

## 3. 全量数据流水线

```bash
# 7 本教材已置于 textbooks/ 下
# A. 解析 + 切分
python -m src.ingest.pdf_parse --all          # PDF → md → RawDoc
python -m src.ingest.enrich_chapters --all    # 章节切分
python -m src.ingest.chunker --all            # 500-800 字 chunks

# B. KG 抽取（LLM；耗时最长）
python -m src.kg.extract --all --workers 12   # 支持 resume

# C. 单本图 build
python -m src.kg.build --all

# D. 跨教材整合
curl -X POST http://localhost:8000/api/merge/run -H 'Content-Type: application/json' -d '{"use_embedding": true}'

# E. 压缩到 ≤30%
curl -X POST http://localhost:8000/api/compress/run -H 'Content-Type: application/json' -d '{"target_ratio": 0.30}'

# F. RAG 索引
curl -X POST http://localhost:8000/api/rag/index -H 'Content-Type: application/json' -d '{}'

# G. 问答（GraphRAG 默认）
curl -X POST http://localhost:8000/api/rag/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"什么是动作电位？","k":5,"mode":"graph"}'
```

## 4. 与赛题对齐

| 赛题硬约束 | 实现 |
|---|---|
| 4 关系枚举 | `prerequisite/parallel/contains/applies_to`，`extract.py` 强制校验 |
| 7 类别枚举 | `核心概念/现象/过程/结构/物质/疾病/方法` |
| ≤ 30% 字符 | `merge/compress.py` 按字符严格控制 |
| 教学完整性 | fixpoint rescue：保证保留节点的前置不丢 |
| 多格式上传 | `/api/upload` 支持 PDF/MD/TXT/DOCX |
| RAG 带引用 | 每个回答含 `[教材, 章节, 页码]` |
| Web SPA | 前端挂载在 `/`，组件库 Vue3 + ECharts |
| 多轮对话 | KnowledgeAgent + 10 工具 + diff_log + undo |

## 5. 创新点

1. **GraphRAG**：在向量检索的基础上，注入相关知识点的 1-hop 子图三元组到 LLM prompt，输出 `knowledge_paths`，让回答能解释概念依赖（不仅仅"是什么"，还能解释"为什么"）。
2. **教学完整性自检**：压缩后 fixpoint loop 检查 `prerequisite` 链不断裂，自动 rescue。
3. **diff_card UI 协议**：Agent 每次写图都返回 before/after 子图，前端可逐步审计。
4. **可撤销**：`diff_log.jsonl` + `/api/chat/undo` 反向重放。

## 6. 项目结构

```
.
├── src/                  # 源码（详见 docs/系统设计.md）
├── docs/                 # 设计文档
│   ├── 赛题.md
│   ├── 需求分析.md
│   ├── 系统设计.md
│   └── Agent架构说明.md  # ⭐ Mermaid 架构图
├── textbooks/            # 7 本教材原始 PDF
├── data/                 # 中间产物
│   ├── md/ raw/ chunks/ triples/ kg/ vector/ report/
├── report/               # 整合报告
├── ARCHITECTURE.md       # 架构契约
├── TASKS.md              # 任务看板
├── MESSAGES.md           # 双 AI 异步消息板
└── COLLAB.md             # 协作规则
```

## 7. 双 AI 协作

本项目由 **GitHub Copilot (Lead)** + **Claude Code (Executor)** 两个 AI 共同交付：

- **Copilot**：架构设计、KG 抽取/合并/压缩、RAG/GraphRAG、Agent + Tools、FastAPI 后端、文档体系
- **Claude**：PDF2MD、chunker、Vue3 + ECharts 前端
- **协作协议**：[COLLAB.md](COLLAB.md) + [MESSAGES.md](MESSAGES.md) 异步消息板

## 8. License

MIT
