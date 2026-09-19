"""planner_runner.py —— 任务编排官：把「计划」翻成可执行的 Agent 图

从 `api/routes/planner.py` 搬来（那 336 行里约 252 行是编排逻辑，路由只该留接口）。
这里放三件事，全部是 **Agent 领域**关注点，不是 HTTP 关注点：

    ① 技能规格表   SKILL_ID_TO_ROLE_KEY / SKILL_OUTPUT_META —— 哪个技能对应哪个角色、产出什么
    ② 计划翻译     convert_plan_to_workflow() —— LLM 出的 plan JSON → workflow 节点图
    ③ 两阶段执行   compose_plan()（阶段一：编排决策）/ execute_plan()（阶段二：执行 + 收尾）

「任务编排官」是**注册在册的 Agent**（`agents.role_key='planner'`，绑定 `.dsh/skills/planner`），
不是一个路由函数。阶段一走 `WORKFLOW_PLAN` 单节点图，所以编排决策这一步同样有
project_steps 行 / 会话号 / 运行日志 / `Plan.json`，可追溯。
"""
import json

from sqlalchemy.orm import Session

from app.db.crud import agents_crud
from app.storage.file_helper import file_helper
from app.agents.builtin_workflows import WORKFLOW_PLAN
from app.agents.orchestrator import step_orchestrator
from app.agents.workflow_engine import WorkflowError, run_workflow

def build_available_agents(db: Session, user_id: int) -> str:
    """给编排官的「可用角色」清单 —— **内置 + 该用户自己的自定义 Agent**。

    每行是 `标识: 名称（来源）`，标识就是 `agents.role_key`，编排官必须逐字引用它。
    为什么用 role_key 而不是 skill_id：
        · 自定义 Agent（mode='prompt'）**没有 skill_id**，用 skill_id 表示不了；
        · role_key 是 agents 表的真实主键语义，能同时覆盖内置、绑定技能的自定义、
          以及纯提示词的自定义三种情况，于是"plan → 图"不再需要任何硬编码映射表。
    """
    lines = []
    for row in agents_crud.list_for_user(db, user_id=user_id):
        if row.skill_id:
            source = f"技能 {row.skill_id}"
        else:
            source = "自定义提示词"
        lines.append(f"{row.role_key}: {row.name}（{source}）")
    return "\n".join(lines)


def resolve_role_key(db: Session, user_id: int, ref: str) -> str:
    """把编排官给的标识解析成可用的 role_key；解析不了抛错（宁可明确失败）。

    兼容：编排官偶尔仍写 `skill_id`（历史提示词口径），这里按技能反查一次。
    """
    if agents_crud.get_by_role_key(db, ref, user_id=None) or \
            agents_crud.get_by_role_key(db, ref, user_id=user_id):
        return ref
    row = next((r for r in agents_crud.list_for_user(db, user_id=user_id) if r.skill_id == ref), None)
    if row:
        return row.role_key
    raise ValueError(f"编排官引用了不存在的角色 {ref!r}（只能从可用角色清单里逐字引用）")


def convert_plan_to_workflow(plan: dict, db: Session = None, user_id: int = None,
                             project_title: str = "") -> dict:
    """编排官的 plan JSON → 可执行的节点图。

    **只做三件事**：把步骤翻成节点、把 `depends_on` 翻成 `deps`、给需要切片的角色补显式输入。
    其余全交给引擎（见 node_inputs / _persist_by_shape），所以这里不再需要
    "skill_id → role_key" 和 "skill_id → output_kind/artifact_file/code_dir" 两张硬编码表：

        · 输出契约   → 技能自己的 SKILL.md 声明（新技能接入不必改本文件）
        · 产物落盘   → 引擎按输出形态决定（代码落 src/，文本落 <步骤名>.md）
        · 输入绑定   → 引擎按技能声明的 input[] 自动组装，名字对不上也有全局上下文兜底

    **PM 步骤会被剔掉**：PRD 已经由项目的第一段产出、并且用户已经审批过了，图里再来一个
    PM 就是让它跑第二遍（真机踩过：p25 里两个 pm-workflow 会话相隔 1 秒并发）。
    这是结构性保证 —— 不管 Planner 怎么写（它的提示词里就举了"生成 PRD"当例子），
    生成的图里都不会有第二个 PM。下游要用 PRD 就走 `seed.prd_content`（由调用方传入）。
    """
    nodes = []
    code_producer_ids = []
    roles = {}                                    # step_no → role_key（给切片找上游用）
    dropped = set()                               # 被剔掉的步骤号（PRD 步骤）

    for step in plan.get("steps", []):
        step_no = step["step_no"]
        ref = step.get("skill_id") or step.get("role_key") or ""
        role_key = resolve_role_key(db, user_id, ref) if db is not None else ref
        if role_key in PRD_ROLES or ref in PRD_ROLES:
            dropped.add(step_no)
            continue
        roles[step_no] = role_key

        node = {
            "id": f"s{step_no}",
            "name": step.get("name", f"s{step_no}"),
            "agent": {"role_key": role_key},
            # 上游若含被剔掉的 PRD 步骤，这条依赖一并去掉（PRD 改从 seed 拿）
            "deps": [f"s{d}" for d in step.get("depends_on", []) if d not in dropped],
        }
        if step.get("note"):
            node["task_note"] = step["note"]
        if role_key in CODE_ROLES:
            code_producer_ids.append(node["id"])
        nodes.append(node)

    if not nodes:
        raise WorkflowError("编排官的计划里没有任何可执行节点（只剩 PRD 步骤？）")

    # 显式输入只补"必须切片"的角色（见 _TASK_SLICE 的注释）
    for step, node in zip([s for s in plan.get("steps", []) if s["step_no"] not in dropped], nodes):
        node["inputs"] = _sliced_inputs(
            roles[step["step_no"]], [f"s{d}" for d in step.get("depends_on", []) if d not in dropped],
            {f"s{n}": r for n, r in roles.items()},
        )
        if not node["inputs"]:
            del node["inputs"]                   # 空字典会让引擎以为"显式声明了"，不如不给

    # QA / 审查 / 文档类必须能看到代码：没连代码节点就自动补一条依赖
    for node in nodes:
        role_key = node["agent"]["role_key"]
        if role_key in NEED_CODE_ROLES and code_producer_ids \
                and not any(d in code_producer_ids for d in node["deps"]):
            node["deps"].append(code_producer_ids[-1])

    # 图里没有 PRD 节点了 → 凡是吃 PRD 的角色显式绑定 seed.prd_content，
    # 不再依赖"名字对不上就给整段上下文"那个兜底（文本优先能把活干完，但不够明确）。
    # 同样两种写法都收：role_key 与 skill_id（没传 db 时是后者）。
    needs_prd = {"architect", "architect-planner",
                 "backend-executor", "frontend-executor", "simple-frontend",
                 "qa", "qa-workflow"}
    for node in nodes:
        if node["agent"]["role_key"] in needs_prd:
            node.setdefault("inputs", {})["prd_content"] = {"ref": "seed.prd_content"}

    for node in nodes:                           # docgen 的项目名来自实际标题，不是 seeds
        if node["agent"]["role_key"] == "docgen" and project_title:
            node.setdefault("inputs", {})["project_name"] = project_title

    return {
        "name": plan.get("goal", "planner_generated"),
        "description": f"Planner 自动生成：{plan.get('goal', '')}",
        "nodes": nodes,
    }


# 产出代码的角色（它们的输出会落进项目代码目录）
CODE_ROLES = {"backend-executor", "frontend-executor", "simple-frontend"}
# 必须先看到代码的角色（没连代码节点时自动补依赖）
NEED_CODE_ROLES = {"qa", "reviewer", "docgen"}
# PRD 的生产者：它在项目第一段就跑过了，Planner 生成的图里不该再出现（见 convert_plan_to_workflow）。
# 两种写法都要认：`role_key`（agents 表，如 pm）与 `skill_id`（如 pm-workflow）——
# 没传 db 时 resolve_role_key 是原样透传 skill_id 的，只认一种会漏。
PRD_ROLES = {"pm", "pm-workflow"}

# —— 需要「按标记切片」的角色 ——
# 架构师技能把前后端任务书写在**同一份说明书**里，且明确要求"后端看不到前端任务书、
# 前端也看不到后端任务书"。所以这两个角色的输入必须切干净，不能整段塞给它。
# 其余角色一律不写 inputs，交给引擎自动组装（拿到的就是上游全文）。
_TASK_SLICE = {
    "backend-executor": ("backend_task", "from_marker", "后端开发任务书"),
    "frontend-executor": ("frontend_task", "until_marker", "后端开发任务书"),
}


def _sliced_inputs(role_key: str, deps: list, node_roles: dict) -> dict:
    """给需要切片的角色生成显式输入；其余返回 {}（= 交给引擎自动组装）。"""
    spec = _TASK_SLICE.get(role_key)
    if not spec or not deps:
        return {}
    name, kind, marker = spec
    # 上游里找架构师那一步；找不到就退而取最近的一个上游
    src = next((d for d in deps if node_roles.get(d) == "architect"), deps[-1])
    return {name: {"ref": f"{src}.output", kind: marker}}


async def compose_plan(db, *, project, prd_content: str = "", user_requirement: str = "",
                       round_no: int, ai=None) -> tuple:
    """阶段一：把「编排决策」当成一次普通节点执行，返回 (plan, 节点执行结果)。

    走 run_workflow 而不是直接调技能，是为了让这一步也有 project_steps 行、
    会话号、运行日志和产物（Plan.json）—— 编排决策从此可追溯。

    ⚠️ **输入是 PRD，不是原始需求**：Agent 模式下 PM 已经在第一段跑完、用户也审批过了，
    编排官要做的是"读这份 PRD，决定用哪些开发节点"（`user_requirement` 只在拿不到 PRD 时兜底）。
    以前的顺序是"编排官读原始需求 → 自己排一个 PM 步骤"，结果 PM 跑两遍（真机踩过）。
    """
    seeds = {
        "available_skills": build_available_agents(db, project.user_id),
    }
    if prd_content:
        seeds["prd_content"] = prd_content
    if user_requirement:
        seeds["user_requirement"] = user_requirement

    result = await run_workflow(
        db,
        project=project,
        user_id=project.user_id,
        workflow=WORKFLOW_PLAN,
        seeds=seeds,
        round_no=round_no,
        ai=ai,
    )
    node = (result.get("node_results") or {}).get("planner") or {}
    if not node.get("success"):
        raise RuntimeError(node.get("error") or "编排决策节点执行失败")

    plan = node.get("parsed")
    if not isinstance(plan, dict):
        plan = json.loads(_read_plan_json(project, node))     # 契约没解析出来 → 读落盘的 Plan.json
    return plan, result


def _read_plan_json(project, node: dict) -> str:
    """兜底：契约解析没给出 dict 时，读回 stage 1 落盘的 Plan.json。"""
    path = node.get("artifact_path") or "Plan.json"
    return file_helper.read_file_by_db_path(project.user_id, project.id, path)


async def execute_plan(db, *, project, workflow: dict, seeds: dict, round_no: int, ai=None) -> dict:
    """阶段二 + 收尾：跑 Planner 生成的图，然后走与主链路完全相同的收尾。

    收尾用 **异步入口**：`finish_project` 里会建 venv + 装依赖 + 起应用（最坏 ~150 秒），
    同步调会把事件循环占死 —— 客户此时刷新页面就是"一直转圈没反应"（真机踩到）。
    """
    result = await run_workflow(
        db, project=project, user_id=project.user_id,
        workflow=workflow, seeds=seeds, round_no=round_no, ai=ai,
    )
    await step_orchestrator.finish_project_async(db, project, result)
    return result
