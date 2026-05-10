# 协作规则（COLLAB.md）

> 本文件由 **Copilot（Lead）** 制定，约束 Copilot 与 CLAUDE 两个 AI 开发者在本工作区的协作方式。
> 人类用户是最终决策人，本文件由 Lead 维护，CLAUDE 可在 [MESSAGES.md](MESSAGES.md) 提议修改。

---

## 1. 角色与分工

| 角色 | 身份 | 职责 |
|---|---|---|
| **Copilot** | First Leader / 架构师 | 拆解赛题、定义模块边界、分配任务、评审产出、合并冲突、对外汇总 |
| **CLAUDE** | 执行者 / 全栈开发 | 按任务卡完成实现，主动报告进度与阻塞，对实现细节有自主权 |
| **人类** | 决策人 / 通信桥 | 在两个 Agent 之间传话、最终拍板、负责 git commit & 提交 |

**冲突解决**：技术细节 CLAUDE 自决；模块接口 / 范围 / 优先级以 Lead 为准；人类一票否决。

---

## 2. 通信协议

### 2.1 异步消息板：[MESSAGES.md](MESSAGES.md)

唯一的双向通信文件。**追加写入**，不要删历史。格式：

```md
## [YYYY-MM-DD HH:MM] FROM <Copilot|CLAUDE> -> <对方>
TYPE: <TASK | REPORT | QUESTION | BLOCKER | DECISION | FYI>
RE: <可选，引用上一条时间戳>

<正文，简洁>
---
```

- **TASK**：Lead 派活，必须包含 `验收标准` 和 `截止节点`（如"评审前"/"H+2"）
- **REPORT**：完成 / 进度更新，必须附产出文件路径
- **QUESTION**：需要对方回答才能继续
- **BLOCKER**：阻塞，需 Lead 或人类介入
- **DECISION**：Lead 的技术决策，CLAUDE 需遵守
- **FYI**：知会，不需回复

### 2.2 任务看板：[TASKS.md](TASKS.md)

Lead 维护的单一事实来源（SoT）。看板格式见该文件。CLAUDE 只能改自己负责任务的 `状态` 与 `产出` 字段，不能新增 / 删除任务。

### 2.3 实时同步原则

- **每次开工前**：先读 [MESSAGES.md](MESSAGES.md) 末尾、[TASKS.md](TASKS.md)、[ARCHITECTURE.md](ARCHITECTURE.md)
- **每次收工前**：在 [MESSAGES.md](MESSAGES.md) 追加 REPORT，更新 [TASKS.md](TASKS.md) 状态
- **遇到阻塞 ≥ 10 分钟**：立刻发 BLOCKER，不要硬磕

---

## 3. 代码协作规则

### 3.1 目录约定（Lead 拟定，可议）

```
510/
├── textbooks/              # 7 本 PDF（只读，禁止改）
├── docs/                   # 赛题、设计文档（CLAUDE 当前在转 PDF→md）
├── src/
│   ├── ingest/             # PDF 解析、文本切分（CLAUDE 主导）
│   ├── kg/                 # 知识图谱构建（Copilot 主导）
│   ├── merge/              # 跨教材整合 / 压缩（Copilot 主导）
│   ├── chat/               # 多轮对话 (二人协同)
│   └── viz/                # 前端可视化 (CLAUDE 主导)
├── data/                   # 中间产物：*.json / *.pkl / *.graphml
├── COLLAB.md               # 本文件
├── MESSAGES.md             # 双向消息板
├── TASKS.md                # 任务看板
└── ARCHITECTURE.md         # 架构 & 接口契约（Lead 维护）
```

### 3.2 文件所有权

- 一个文件**同一时段只能由一个 Agent 写**，由 [TASKS.md](TASKS.md) 任务的"负责人"字段决定
- 跨模块改动必须先在 [MESSAGES.md](MESSAGES.md) 发 QUESTION 或 DECISION
- 公共文件（COLLAB / TASKS / ARCHITECTURE / MESSAGES）只有 Lead 可改正文，CLAUDE 走提议

### 3.3 接口契约

模块之间的数据格式（JSON Schema）写在 [ARCHITECTURE.md](ARCHITECTURE.md)。**接口未定义前不准动手实现跨模块代码**。

### 3.4 提交策略

- 不自动 `git commit`，由人类负责
- 每完成一个 TASK 在 REPORT 里给一句 commit message 建议（中文，conventional 格式）

---

## 4. 比赛节奏（5 小时，仅作目标，不是死线）

| 阶段 | 时长 | 目标 | 主要负责人 |
|---|---|---|---|
| H+0 ~ H+0:30 | 30min | 架构定稿、ModelScope 联通、PDF→文本最小可用 | Lead 设计 + CLAUDE 跑通 |
| H+0:30 ~ H+2 | 1.5h | 单本教材知识图谱 + 基础可视化（端到端 demo） | 双线并行 |
| **H+2 评审节点** | — | 提交 GitHub 链接拿反馈 | 人类操作 |
| H+2 ~ H+4 | 2h | 跨教材整合 + 压缩到 30% | Lead 主导 |
| H+4 ~ H+5 | 1h | 多轮对话 + 报告 + 可视化打磨 + 提交 | 双线并行 |

策略：**收敛范围**——优先打通 2 本教材（建议生理学 + 病理生理学）的端到端，再横向扩展。

---

## 5. 给 CLAUDE 的初始指令

> **请人类把以下内容粘贴给 CLAUDE：**

1. 读完 [COLLAB.md](COLLAB.md) 全文，确认接受协作规则
2. 读 [MESSAGES.md](MESSAGES.md)，按格式回一条 `TYPE: FYI` 的签到消息，包含：
   - 当前 PDF→md 转换的进度（已完成几本 / 用什么工具）
   - 转换产物的存放路径
   - 你预计完成时间
3. 读 [TASKS.md](TASKS.md) 看自己被分到的任务
4. **不要**主动改 COLLAB.md / TASKS.md / ARCHITECTURE.md，有意见走 MESSAGES.md
5. 每完成一个 TASK 或遇到 BLOCKER 立刻在 MESSAGES.md 追加消息

之后 CLAUDE 的行动以 [MESSAGES.md](MESSAGES.md) 和 [TASKS.md](TASKS.md) 为准。
