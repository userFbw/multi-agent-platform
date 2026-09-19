"""node_inputs.py —— 节点输入组装（**一条路**：按技能声明的输入逐个填）

## 为什么只有一条路

每个 Agent 都是一次技能调用，区别只在"绑了哪个技能"：

    内置 Agent    skill_id = pm-workflow / backend-executor / qa-workflow / …
    自定义 Agent  skill_id = generic-prompt-agent（用户的提示词是它的一个**输入值**）
                  ↑ 由 resolve_run() 换算，用户的提示词不再当正文

所以引擎只需要做一件事：读该技能 `SKILL.md` frontmatter 的 `input[]`，
按「输入名 → 来源」逐个填。整张来源表只有一行和自定义 Agent 有关（`system_prompt`）。

## 文本优先原则（写死在这里，别再改回去）

名字只是提示，**填不上不报错**：
    · 认得出的名字 → 按语义取（需求 / PRD / 任务书 / 代码 / 项目名 …）
    · 认不出的名字 → 给「全局上下文块」= 用户需求 + 上游全部输出
    · 全局上下文也空 → 给空串（**但 key 一定存在** —— ai_client.fill_placeholders 不认
                       `required: false`，缺 key 会直接 DshError，所以必须补齐每一个声明过的输入）

为什么要"用户需求 + 上游"两块都兜：画布链路里**第一个节点没有上游**（实测踩过），
只兜上游会让它拿到空串；而用户需求本来就是整条链的起点，必须一起给。
"""
from typing import Optional

from app.agents.ai_client import PLACEHOLDER_PATTERN, ai_client, parse_frontmatter

# 自定义提示词 Agent 统一绑这个技能：它没有人设，唯一职责是"扮演交给它的提示词"
GENERIC_SKILL = "generic-prompt-agent"


def resolve_run(agent) -> tuple:
    """agents 表一行 → (skill_id, mode, 该行自带的输入)。

    自定义提示词 Agent（mode='prompt' 且没显式绑技能）会被换算成
    `generic-prompt-agent` + `{"system_prompt": 用户提示词}`；
    其余原样（显式绑了技能的 prompt Agent 走 `build_prompt` 的"技能+强化"组合）。
    """
    if agent.mode == "prompt" and not agent.skill_id:
        return GENERIC_SKILL, "skill", {"system_prompt": agent.system_prompt or ""}
    return (agent.skill_id or ""), (agent.mode or "skill"), {}


def skill_inputs(skill_id: str) -> list:
    """读技能 frontmatter 声明的 input[]（**含 required: false 的**）。读不到就返回空表。"""
    if not skill_id:
        return []
    path = ai_client.skills_dir / skill_id / "SKILL.md"
    try:
        meta = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return []
    declared = meta.get("input")
    return [d for d in declared if isinstance(d, dict) and d.get("name")] if isinstance(declared, list) else []


def assemble(
    skill_id: str, *,
    extra=None, seeds=None, upstream: str = "",
    project_title: str = "", output_kind: str = "",
) -> dict:
    """按技能声明的输入逐个取值；**返回的 dict 保证含全部声明的 key**。"""
    extra = {k: v for k, v in (extra or {}).items() if v}
    seeds = seeds or {}
    context = _context_block(seeds, upstream)
    filled = {
        spec["name"]: _pick(spec["name"], extra, seeds, context, project_title, output_kind)
        for spec in skill_inputs(skill_id)
    }

    # 自定义 Agent 的提示词是**输入值**、不是正文，所以它里面的 {{...}} 不会被
    # ai_client.fill_placeholders 扫到，会以字面量留在最终 prompt 里（真机实测：生成的
    # 提示词里带着一个没替换的 {{raw_requirement}}）。这里按同一份映射补填一次：
    # 平台能给的填掉，给不了的原样保留（不报错 —— 文本优先）。
    if extra:
        # 映射要含 seeds（用户提示词里常见的 {{user_requirement}}/{{prd_content}} 在种子里，
        # 不是 generic-prompt-agent 声明的输入），再让技能声明的输入覆盖同名项。
        snapshot = {**seeds, **filled}
        for name in list(filled):
            if name in extra:
                filled[name] = PLACEHOLDER_PATTERN.sub(
                    lambda m: str(snapshot[m.group(1)]) if m.group(1) in snapshot else m.group(0),
                    str(filled[name]),
                )
    return passthrough_undeclared(skill_id, filled)


def passthrough_undeclared(skill_id: str, inputs: dict) -> dict:
    """把技能正文里出现、但**未在 frontmatter 声明**的占位符原样回填。

    为什么需要：`ai_client.fill_placeholders` 对**任何**缺失的 `{{...}}` 都抛 KeyError，
    可有些技能必须**举例说明**占位符写法 —— 例如 `agent-prompt-authoring`（教人写提示词的技能）
    正文里的 `{{input.xxx}}` 是文档，不是真输入。不回填的话它永远跑不起来（真机实测踩到）。

    回填成它自己的字面量即可：`re.sub` 不会重扫替换结果，正文里的示例原样保留。
    """
    if not skill_id:
        return inputs
    try:
        body = ai_client.load_skill_text(skill_id)
    except Exception:                       # noqa: BLE001 — 读不到就当没有，别影响主流程
        return inputs
    declared = {spec["name"] for spec in skill_inputs(skill_id)}
    extra = {name: f"{{{{{name}}}}}"
             for name in set(PLACEHOLDER_PATTERN.findall(body)) - declared - set(inputs)}
    return {**inputs, **extra} if extra else inputs


def _context_block(seeds: dict, upstream: str) -> str:
    """全局上下文：用户需求 + 上游输出。认不出的输入名一律给它。"""
    parts = []
    requirement = str(seeds.get("user_requirement") or "").strip()
    if requirement:
        parts.append(f"===== 用户需求 =====\n{requirement}")
    if upstream:
        parts.append(upstream)
    return "\n\n".join(parts)


# =====================================================================
# 「平台到底填得起哪些占位符」——**唯一真相**
# =====================================================================
# 写自定义提示词的 Agent（agent-prompt-authoring）会照着这份清单写占位符；
# 以前它只能靠猜（技能只说了"名字要和调用方一致"，却没说调用方有哪些名字），
# 于是编出了 {{project_context}} —— 平台填不了、又**不报错**、原样留在提示词里，
# 模型看到一个空占位符，用户完全不知道（实测踩到）。
#
# ⚠️ 这份清单是**实测出来的**，改动前先自己跑一遍（见 canvas_e2e_smoke 的占位符用例）：
#   · {{prd}} 别名、{{project_name}} 只对**技能声明的输入**生效，对自定义提示词不生效；
#   · 两种写法都行：`{{user_requirement}}` 与 `{{input.user_requirement}}`（PLACEHOLDER_PATTERN 会剥掉 input. 前缀）。
PROMPT_PLACEHOLDERS = (
    ("user_requirement", "用户原始需求（建项目时填的那句话）"),
    ("prd_content", "上游产出的 PRD 全文（节点上游有 PM 时）"),
    ("user_input", "上游节点的全部产出 + 用户需求（通用的整块兜底文本）"),
    ("output_contract", "平台声明的输出格式：text / json_object / json_array / file_blocks"),
)

PROMPT_PLACEHOLDER_HELP = (
    "\n\n===== 可用占位符（唯一清单，**不得发明新名字**）=====\n"
    "平台运行时只会替换下面这些名字，两种写法都认（`{{名字}}` 或 `{{input.名字}}`）：\n"
    + "\n".join(f"- `{{{{{name}}}}}` —— {desc}" for name, desc in PROMPT_PLACEHOLDERS)
    + "\n\n**写了清单外的名字不会报错，但会原样留在提示词里**（模型看到一串花括号，"
      "拿不到任何内容，而你在界面上完全看不出来）。需要清单外的信息（例如「项目背景」）时，"
      "请改成引用清单里最接近的（如 `{{user_requirement}}`），或写成对输入内容的要求，"
      "**不要造 `{{project_context}}` 这类平台填不了的名字**。"
)


def unfilled_placeholders(values) -> list:
    """挑出**平台填不了**的占位符：填完之后还留在文本里的那些。

    为什么要报出来：`assemble` 遵循文本优先 —— "给不了的原样保留、不报错"，
    于是提示词里写错名字时会**静默**留在发给模型的提示词里（实测：整段"项目背景"是空的）。
    调用方（引擎）把结果写进运行日志，让"这一步到底收到了什么"看得见。
    """
    if isinstance(values, dict):          # 传 dict 进来也认（省得调用方漏写 .values()）
        values = values.values()
    names: list = []
    for v in values or ():
        for m in PLACEHOLDER_PATTERN.findall(str(v)):
            if m not in names:
                names.append(m)
    return names


def missing_declared(skill_id: str, given: dict) -> list:
    """节点显式写了 inputs、但漏了技能声明里某个**可选**输入时，返回漏掉的名字。

    为什么需要：`required: false` 只影响提示词作者的期待，不影响 fill_placeholders ——
    少给一个 key 照样 DshError（实测 code-reviewer 漏 prd_content 就会炸）。
    """
    if not skill_id:
        return []
    return [s["name"] for s in skill_inputs(skill_id) if s["name"] not in given]


def _pick(name: str, extra: dict, seeds: dict, context: str,
          project_title: str, output_kind: str) -> str:
    """输入名 → 值。前几档是"认得出的名字"，最后一档是万能兜底。"""
    if extra.get(name):
        return extra[name]                       # 自定义 Agent 的提示词（system_prompt）
    if name == "output_contract":
        return output_kind                       # 期望输出格式说明（generic-prompt-agent 用）
    if seeds.get(name) not in (None, ""):
        return str(seeds[name])                  # 与种子同名的（user_requirement / prd_content …）
    if name in ("prd", "prd_content") and seeds.get("prd_content"):
        return str(seeds["prd_content"])         # PRD 的别名
    if name == "project_name":
        return project_title
    # 其余一律给全局上下文（文本优先：名字对不上也让 Agent 拿得到东西）
    return context
