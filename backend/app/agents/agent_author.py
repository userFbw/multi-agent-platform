"""agent_author.py —— 把「一句描述」变成「可直接落库的自定义 Agent 提示词」

这是「用户描述 → 提示词 → 画布节点」三步闭环的**第一步**（后两步已存在）：

    ① 用户一句话描述
       → POST /api/agents/author       （本模块：调 agent-prompt-authoring 技能）
    ② 用户看一眼、确认
       → POST /api/agents              （已有：mode='prompt' 落库，role_key 自动 custom_xxx）
    ③ 画布左侧面板立刻多一个可拖的节点
       → GET  /api/agents              （已有：返回内置 + 本人自定义）

为什么放在 agents 层而不是路由里：和 planner_runner 同理 —— 它是一次**技能调用 + 提示词后处理**，
属于 Agent 领域逻辑，不是 HTTP 领域。路由只负责收请求、校验长度、映射错误码。

为什么"生成"与"落库"分成两个接口：`agent-prompt-authoring` 技能的产出说明里写着
"可直接粘贴到 /api/agents 的 system_prompt，**必要时附体检说明**" —— 说明它是给人过目用的。
一次调用顺手落库会让用户失去过目的机会，也让接口不再幂等（重试就多一个 Agent）。
"""
import re
import shutil
import tempfile

from app.agents import node_inputs, skill_meta
from app.agents.ai_client import ai_client, read_skill_contract

# Harness 组提供的「自定义 Agent 提示词工程师」技能（.dsh/skills/agent-prompt-authoring）
AUTHOR_SKILL = "agent-prompt-authoring"

# POST /api/agents 对 prompt 模式下 system_prompt 的下限（见 routes/agents.py._validate_create）
MIN_PROMPT_CHARS = 10

# 平台侧的附加要求：技能本身允许"必要时附体检说明"，但落库要的是纯净正文。
#
# ⚠️ 后半段（可用占位符清单）是**必须**的：技能只规定了"占位符名字要和调用方一致"，
# 却没法告诉写手"调用方到底有哪些名字"——写手只能猜，于是编出了 {{project_context}}
# 这种平台填不了的名字，而且**不报错**、原样留在提示词里（实测 p34 踩到）。
# 清单的唯一真相在 node_inputs.PROMPT_PLACEHOLDERS，这里拼进去而不是另写一份。
_TASK_NOTE = (
    "只输出**可直接使用的提示词正文**：不要任何解释、不要体检说明、"
    "不要自我评价、不要用 Markdown 代码围栏包裹整段。"
    + node_inputs.PROMPT_PLACEHOLDER_HELP
)

# 「基于出厂技能整合」时的额外执行要求（走 task_note 通道，不需要改 Harness 的技能）。
#
# 为什么必须显式交代：写手技能默认是"依据一句话目标从零写一份提示词"，它并不知道
# 手上那份草稿是**一份完整的出厂技能**。不交代的话它会压缩、丢约束，甚至把原技能的
# 输出格式（该 JSON 的、该 # File: 的）写没 —— 那新角色的产出就解析不出来了。
_INTEGRATE_NOTE = (
    "\n\n===== 这次是【整合】而不是从零撰写 =====\n"
    "`user_draft` 里是一份**已经能跑的出厂技能**全文，`agent_goal` 是用户要在此基础上改/加的要求。\n"
    "请输出一份**融合后的完整提示词**，要求：\n"
    "1. **保留**原技能的角色定位、专业口径、输入占位符（`{{input.xxx}}` 原样保留、名字不要改；"
    "若原技能用了清单外的名字，改成清单里最接近的那个）、"
    "输出格式与全部硬约束；\n"
    "2. 把用户的新要求**合并进相应段落**（该改规则就改规则、该加字段就加字段），"
    "不要只是把要求追加到末尾当附注；\n"
    "3. 若用户要求与原技能冲突，**以用户要求为准**，但**不得丢掉原技能的输出契约**"
    "（原来是 JSON 就仍是 JSON、原来是 `# File:` 代码块就仍是代码块）；\n"
    "4. 段落结构按你自己的规范重排，读起来要像一份**原生就是这个用途**的提示词，"
    "不要出现「原技能说…/现改为…」这类对照说明。\n"
    "5. 只输出提示词正文，不要解释、不要体检说明、不要用代码围栏包整段。"
)

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n\s*```\s*$", re.S)


class AuthorError(RuntimeError):
    """提示词生成失败（技能跑挂 / 产出为空）。"""


def clean_prompt(text: str) -> str:
    """剥掉整段包裹的代码围栏与多余空白 —— 模型偶尔仍会包一层。"""
    text = (text or "").strip()
    m = _FENCE.match(text)
    if m:
        text = m.group(1).strip()
    return text.strip()


async def author_prompt(*, agent_goal: str, user_draft: str = "", base_skill_id: str = "",
                        ai=None) -> dict:
    """调 `agent-prompt-authoring` 技能，返回可直接落库的提示词。**不写数据库。**

    两条用法：
        · 从零撰写：只给 `agent_goal`（一句话目标）；
        · **基于出厂技能整合**：给 `base_skill_id`，服务端把该技能的 `SKILL.md` 全文当草稿，
          并要求写手把用户要求**合并进去**（而不是追加在末尾）。原技能只读、绝不修改。

    返回里带 `base_skill_id` 与 `suggested_output_kind`（= 原技能的契约），
    前端据此把新角色的输出契约设对 —— 否则整合后的 JSON/代码块会被当纯文本，
    代码不落 `src/`、条件分支也读不到字段。

    ⚠️ 会真起一次 DSH 会话（烧模型额度）。
    """
    client = ai or ai_client

    task_note = _TASK_NOTE
    base_body = ""
    if base_skill_id:
        base_body = skill_meta.skill_prompt_text(base_skill_id)
        if not base_body:
            raise AuthorError(f"技能 {base_skill_id} 不存在或读不到正文")
        # 原技能全文当草稿（写手会"在其基础上审校改写"）
        user_draft = base_body
        task_note = _TASK_NOTE + _INTEGRATE_NOTE
    # 走「一条路」的输入组装：它保证技能声明过的输入**全部键都存在**
    # （agent-prompt-authoring 的 user_draft 是 required:false，但 ai_client.fill_placeholders
    #  不认可选 —— 少给一个 key 直接 DshError，实测过）
    inputs = node_inputs.assemble(
        AUTHOR_SKILL, extra={"agent_goal": agent_goal, "user_draft": user_draft},
    )

    workdir = tempfile.mkdtemp(prefix="author_")
    try:
        res = await client.arun_agent(
            skill_id=AUTHOR_SKILL,
            inputs=inputs,
            output_kind="text",
            task_note=task_note,
            workdir=workdir,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if not res.get("success"):
        raise AuthorError(res.get("error") or res.get("error_code") or "提示词生成失败")

    prompt = clean_prompt(res.get("output") or "")
    if not prompt:
        raise AuthorError("技能没有产出提示词正文")
    return {
        "system_prompt": prompt,
        "char_count": len(prompt),
        "usable": len(prompt) >= MIN_PROMPT_CHARS,
        "skill_id": AUTHOR_SKILL,          # 写手技能（谁生成的）
        "base_skill_id": base_skill_id or None,   # 基于哪个出厂技能整合的（从零撰写时为空）
        # 新角色该用的输出契约：整合时跟随原技能，从零撰写时留空（= 纯文本）
        "suggested_output_kind": (
            read_skill_contract(base_skill_id) if base_skill_id else ""
        ) or ("text" if base_skill_id else ""),
        "session_id": res.get("session_id") or "",
        "elapsed_seconds": res.get("elapsed_seconds") or 0,
    }
