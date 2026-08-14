"""FakeDistiller：离线测试执行器（不调用任何 LLM/API）。

按状态机 5 阶段产出**结构对齐 cangjie 规范**的假产物：
BOOK_OVERVIEW.md / candidates/（5 类）/ verified.md / rejected/
2 个 SKILL.md（六段齐全 + frontmatter）/ test-prompts.json（含诱饵与跨 skill 混淆）
/ test-results.md / INDEX.md / GLOSSARY.md / DIGEST.md

用途：pytest 全流程离线可跑；真实验收用 executor_llm.LLMDistiller。
"""
from __future__ import annotations

import json
from pathlib import Path

SKILLS = [
    {
        "slug": "inversion-thinking",
        "name": "inversion-thinking",
        "title": "反转思维：从失败倒推决策",
        "description": (
            "用户在纠结一个决策、列了一堆正面理由却理不出头绪时；"
            "或问'怎么做 X 才能成功'时。应反问'什么会导致 X 失败'再反向规避。"
            "不适用于：纯信息查询、日常琐碎选择、已有明确答案的问题。"
        ),
        "trigger_words": ["要不要", "纠结", "拿不准", "怎么才能成功"],
        "sibling": "second-order-effects",
    },
    {
        "slug": "second-order-effects",
        "name": "second-order-effects",
        "title": "二阶效应：追问'然后呢'",
        "description": (
            "用户在评估行动后果、只想到直接结果时；或问'如果我做 X 会怎样'时。"
            "应追问第二、第三层后果再下结论。不适用于：需要立即执行的明确操作、"
            "纯事实查询。"
        ),
        "trigger_words": ["会怎样", "后果", "连锁反应", "然后呢"],
        "sibling": "inversion-thinking",
    },
]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _skill_md(ctx, skill: dict, stage3_related: bool = False) -> str:
    related = ""
    if stage3_related:
        related = (
            "\n## 相关 skills\n\n"
            f"- contrasts-with: {skill['sibling']}\n"
        )
    return f"""---
name: {skill['name']}
description: |
  {skill['description']}
source_book: 《{ctx.title}》 {ctx.author}
source_chapter: 第 3 章
tags: [决策, 思维模型]
related_skills: [{skill['sibling']}]
---

# {skill['title']}

## R — 原文 (Reading)

> 思考问题的最优方式是反向思考：与其问'如何成功'，不如问'什么必然导致失败'。
>
> — {ctx.author or '作者'}, 第 3 章

---

## I — 方法论骨架 (Interpretation)

把问题倒过来看：先枚举'最坏可能'，再逐条设计防线。核心步骤是
1) 明确目标；2) 列出会导致失败的路径；3) 对每条路径设检查点；
4) 定期回顾是否滑向失败路径。适用于决策前做风险自检。

---

## A1 — 书中的应用 (Past Application)

### 案例 1: 项目取舍
- **问题**: 作者在两个方案间犹豫
- **方法论的使用**: 先问'哪个方案失败更致命'
- **结论**: 选择失败代价更低的方案
- **结果**: 避免了不可逆损失

---

## A2 — 触发场景 (Future Trigger) ★

### 用户会在什么情境下需要这个 skill?

1. 决策纠结、正面理由充足却仍然不安
2. 面对高风险不可逆选择
3. 问'怎么才能成功'类问题

### 语言信号 (用户的话里出现这些就应激活)

- "{skill['trigger_words'][0]}"
- "{skill['trigger_words'][1]}"
- "{skill['trigger_words'][2]}"

### 与相邻 skill 的区分

- 与 `{skill['sibling']}` 的区别: 反转思维管'决策前自检'，二阶效应管'行动后果推演'

---

## E — 可执行步骤 (Execution)

当 skill 被激活后, agent 应按以下步骤执行:

1. **复述目标**
   - 完成标准: 用一句话确认用户要决策什么

2. **反向枚举失败路径**
   - 完成标准: 列出 ≥3 条最坏可能
   - 判停条件: 若用户只是纯信息查询则跳到步骤 5

3. **逐条设计防线**
   - 完成标准: 每条失败路径有应对

4. **输出决策建议**
   - 完成标准: 给出推荐 + 最坏情况预案

5. **判停收尾**
   - 完成标准: 判断本 skill 是否适用，不适用则直接回答

---

## B — 边界 (Boundary) ★

### 不要在以下情况使用此 skill

- 纯信息查询 / 简单事实问题
- 用户已有明确答案只需确认

### 作者在书中警告的失败模式

- 过度反想导致决策瘫痪

### 作者的盲点 / 时代局限

- 对低风险日常选择过于繁琐

### 容易混淆的邻近方法论

- {skill['sibling']}
{related}
---

## 审计信息

- **验证通过**: V1 ✓ / V2 ✓ / V3 ✓
- **测试通过率**: 100% (详见 test-prompts.json)
- **蒸馏时间**: 2026-08-14
"""


def _test_prompts(ctx, skill: dict) -> str:
    cases = [
        {
            "id": "should-trigger-01", "type": "should_trigger",
            "prompt": f"我要决定要不要接这个新项目，列了一堆好处但还是没底，{skill['trigger_words'][0]}",
            "expected_behavior": f"调用 {skill['slug']}，反问'最不希望发生什么'",
            "notes": "正面场景: 决策纠结",
        },
        {
            "id": "should-trigger-02", "type": "should_trigger",
            "prompt": f"{skill['trigger_words'][2]}？我总觉得自己方法不对",
            "expected_behavior": f"调用 {skill['slug']}",
            "notes": "正面场景: 求成功方法",
        },
        {
            "id": "should-trigger-03", "type": "should_trigger",
            "prompt": f"这两个方案选哪个，{skill['trigger_words'][1]}",
            "expected_behavior": f"调用 {skill['slug']} 做反向自检",
            "notes": "正面场景: 方案取舍",
        },
        {
            "id": "should-not-trigger-01", "type": "should_not_trigger",
            "prompt": "帮我查一下这个 API 的参数",
            "expected_behavior": "纯信息查询, 不应调用任何决策 skill",
            "notes": "诱饵: 非决策场景",
        },
        {
            "id": "should-not-trigger-02", "type": "should_not_trigger",
            "prompt": f"如果我推了这个功能，{skill['sibling']}会怎样？",
            "expected_behavior": f"不应激活本 skill, 应激活 {skill['sibling']}",
            "notes": "跨 skill 混淆诱饵 (同书兄弟 skill)",
        },
        {
            "id": "edge-01", "type": "edge_case",
            "prompt": "我在想晚饭吃什么",
            "expected_behavior": "日常琐事, 不应调用 (虽然字面是'决策')",
            "notes": "边界: 区分严肃决策和日常选择",
        },
    ]
    return json.dumps({
        "skill": skill["slug"],
        "version": "0.1.0",
        "source_book": f"{ctx.title} — {ctx.author}",
        "darwin_compatible": True,
        "test_cases": cases,
        "minimum_pass_rate": 0.8,
    }, ensure_ascii=False, indent=2)


class FakeDistiller:
    """确定性伪执行器：产出结构对齐 cangjie 规范的假产物。

    fail_stage: 指定在哪个阶段抛异常（测失败分支）。
    """

    def __init__(self, fail_stage: str | None = None, n_skills: int = 2):
        self.fail_stage = fail_stage
        self.n_skills = n_skills

    # ---------- 阶段实现 ----------

    def stage0(self, ctx) -> None:
        self._maybe_fail("stage0_reading", ctx)
        _write(ctx.out_root / "BOOK_OVERVIEW.md", f"""# {ctx.title} · 整书理解

## 主旨
一本演示用方法论书，核心是决策思维。

## 骨架
1. 反转思维
2. 二阶效应

## 术语
- 反转思维: 反向思考
- 二阶效应: 后果推演
""")

    def stage1(self, ctx) -> None:
        self._maybe_fail("stage1_extracting", ctx)
        cand = ctx.out_root / "candidates"
        for fname, content in [
            ("framework.md", "# 框架候选\n- 反转思维框架\n- 二阶效应框架\n"),
            ("principle.md", "# 原则候选\n- 先问最坏可能\n- 追问第二层后果\n"),
            ("case.md", "# 案例候选\n- 项目取舍案例\n"),
            ("counter-example.md", "# 反例候选\n- 过度反想瘫痪\n"),
            ("glossary.md", "# 术语候选\n- 反转思维\n- 二阶效应\n"),
        ]:
            _write(cand / fname, content)

    def stage1_5(self, ctx) -> None:
        self._maybe_fail("stage1_5_verifying", ctx)
        _write(ctx.out_root / "verified.md", """# 通过三重验证的单元

## 反转思维
- V1 跨域: 书中 ≥2 章出现 ✓
- V2 预测力: 能回答书外新问题 ✓
- V3 独特性: 非常识 ✓

## 二阶效应
- V1 ✓ / V2 ✓ / V3 ✓
""")
        _write(ctx.out_root / "rejected" / "常见决策三步法.md", "V3 独特性不通过: 任何聪明人都会说\n")

    def stage2(self, ctx) -> None:
        self._maybe_fail("stage2_building", ctx)
        for skill in SKILLS[: self.n_skills]:
            _write(ctx.out_root / skill["slug"] / "SKILL.md", _skill_md(ctx, skill))

    def stage3(self, ctx) -> None:
        self._maybe_fail("stage3_linking", ctx)
        _write(ctx.out_root / "INDEX.md", """# 技能索引

```mermaid
graph LR
  inversion-thinking --> second-order-effects
```
""")
        _write(ctx.out_root / "GLOSSARY.md", "# 术语词典\n- 反转思维: 反向思考\n")

    def stage4(self, ctx) -> None:
        self._maybe_fail("stage4_testing", ctx)
        for skill in SKILLS[: self.n_skills]:
            d = ctx.out_root / skill["slug"]
            _write(d / "test-prompts.json", _test_prompts(ctx, skill))
            _write(d / "test-results.md", """# 压力测试结果

- 应触发 3/3 通过
- 诱饵 2/2 通过
- 边界 1/1 通过
- 通过率: 100% (fallback: 主流程自测，无独立 sub-agent)
""")

    def stage5(self, ctx) -> None:
        self._maybe_fail("stage5_delivering", ctx)
        _write(ctx.out_root / "DIGEST.md", f"""# {ctx.title} · 精华

不读全书也能用的决策框架：反转思维 + 二阶效应。详见各 SKILL.md。
""")

    def _maybe_fail(self, stage: str, ctx) -> None:
        if self.fail_stage == stage:
            raise RuntimeError(f"FakeDistiller 注入失败: {stage}")
