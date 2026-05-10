# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI全栈黑客松比赛项目：开发一个 AI 智能体，对 7 本医学教材进行知识整合，构建可视化知识图谱，跨教材去重提纯，将内容压缩到原体量的 30% 以内，并支持多轮对话迭代优化。

## Tech Stack & Environment

- **Python 3.10+** and **Node.js 18+** required
- LLM API: 魔搭 ModelScope 免费推理服务（无需自备 API key）
- PDF 教材位于 `./textbooks/`，共 7 本医学教材（解剖学、组织学与胚胎学、生理学、微生物学、病理学、传染病学、病理生理学）
- 推荐使用 Claude Code 辅助开发，GitHub CLI + Lark CLI 提交

## Project Phases

1. PDF 解析与文本提取 — 从 7 本教材 PDF 中提取结构化文本
2. 知识图谱构建 — 为每本教材独立构建知识图谱（实体识别、关系抽取）
3. 跨教材知识整合 — 识别知识点重叠、互补与缺失，去重提纯
4. 内容压缩 — 整合后的内容不超过原体量的 30%
5. 多轮对话 — 支持与学科老师交互，迭代优化整合方案
6. 可视化输出 — 知识图谱的可视化展示

## Architecture Notes

- 知识图谱推荐使用 NetworkX 或 Neo4j 存储，前端可视化可用 D3.js 或 ECharts
- PDF 解析可选用 PyMuPDF（fitz）或 pdfplumber
- LLM 调用统一走 ModelScope SDK，集中封装便于切换模型
- 多轮对话需维护会话状态和知识图谱的增量更新能力
