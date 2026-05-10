"""Domain schema loader.

Pluggable schema for the integration framework — categories, relations, and
prompt are loaded from `domains/<name>.yaml` (or fall back to hardcoded
medical defaults so existing behaviour is preserved when DOMAIN env is unset).

Usage:
    from src.config.domain import get_domain
    d = get_domain()           # respects $DOMAIN, defaults to "medical"
    d.categories               # set[str]
    d.relations                # set[str]
    d.system_prompt            # str
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---- hardcoded medical defaults (identical to legacy values) -----------------
_MEDICAL_RELATIONS = {"prerequisite", "parallel", "contains", "applies_to"}
_MEDICAL_CATEGORIES = {"核心概念", "现象", "过程", "结构", "物质", "疾病", "方法"}
_MEDICAL_DEFAULT_CATEGORY = "核心概念"
_MEDICAL_SYSTEM_PROMPT = """你是医学知识图谱构建助手。任务：从教材片段中提取知识点（节点）与知识点间关系（边）。

【输出格式】严格 JSON 对象（不要 markdown 包裹），结构：
{
  "nodes": [
    {"name": "动作电位", "definition": "细胞受到刺激后膜电位发生的一次快速可逆的倒转", "category": "核心概念"}
  ],
  "edges": [
    {"source": "动作电位", "target": "静息电位", "relation_type": "prerequisite", "description": "理解动作电位需先掌握静息电位"}
  ]
}

【硬约束】
1. 只抽取该片段中明确出现的知识点；name 必须在原文里出现或为原文同义术语。
2. definition 控制在 30~120 字，必须基于原文，不要发挥。
3. category ∈ {核心概念, 现象, 过程, 结构, 物质, 疾病, 方法}（必须从这7个里选；拿不准选"核心概念"）
4. relation_type 必须是这 4 种之一：
   - prerequisite：B 学习需先掌握 A（A 是 B 的前置）
   - parallel：同层级平行概念
   - contains：A 包含 B（上位包含下位）
   - applies_to：A 是 B 的应用场景
5. edges 中的 source / target 必须是 nodes 数组里出现过的 name。
6. description 30 字以内，说明关系成立的依据。
7. 只抽事实，不推测；同片段内同名实体只出现一次。
8. 单次输出 nodes ≤ 12 条，edges ≤ 15 条；优先信息量大的核心概念。
9. 若片段是目录页/版权页/纯图注，输出 {"nodes":[],"edges":[]}。"""


@dataclass(frozen=True)
class Domain:
    name: str
    categories: frozenset[str]
    relations: frozenset[str]
    default_category: str
    system_prompt: str
    display_name: str = ""
    description: str = ""


_MEDICAL = Domain(
    name="medical",
    categories=frozenset(_MEDICAL_CATEGORIES),
    relations=frozenset(_MEDICAL_RELATIONS),
    default_category=_MEDICAL_DEFAULT_CATEGORY,
    system_prompt=_MEDICAL_SYSTEM_PROMPT,
    display_name="医学",
    description="医学教材整合（默认 schema）",
)

_REGISTRY: dict[str, Domain] = {"medical": _MEDICAL}


def _load_yaml_domain(name: str) -> Domain | None:
    """Load `domains/<name>.yaml` if present. Returns None if missing/invalid."""
    root = Path(__file__).resolve().parents[2]
    fp = root / "domains" / f"{name}.yaml"
    if not fp.exists():
        return None
    try:
        import yaml  # type: ignore
    except Exception:
        return None
    try:
        data = yaml.safe_load(fp.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    cats = data.get("categories")
    rels = data.get("relations")
    prompt = data.get("system_prompt")
    if not (isinstance(cats, list) and isinstance(rels, list) and isinstance(prompt, str)):
        return None
    return Domain(
        name=name,
        categories=frozenset(str(x) for x in cats),
        relations=frozenset(str(x) for x in rels),
        default_category=str(data.get("default_category") or (cats[0] if cats else "概念")),
        system_prompt=prompt,
        display_name=str(data.get("display_name") or name),
        description=str(data.get("description") or ""),
    )


def get_domain(name: str | None = None) -> Domain:
    """Resolve domain. Order: explicit arg > $DOMAIN env > 'medical'."""
    n = (name or os.getenv("DOMAIN") or "medical").strip().lower()
    if n in _REGISTRY:
        return _REGISTRY[n]
    d = _load_yaml_domain(n)
    if d is None:
        # unknown domain -> safe fallback to medical (preserves legacy behaviour)
        return _MEDICAL
    _REGISTRY[n] = d
    return d


def list_domains() -> list[str]:
    names = set(_REGISTRY.keys())
    root = Path(__file__).resolve().parents[2] / "domains"
    if root.exists():
        for fp in root.glob("*.yaml"):
            names.add(fp.stem)
    return sorted(names)
