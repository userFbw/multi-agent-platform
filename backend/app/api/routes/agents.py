"""agents 注册表接口 —— AI 角色增删改查

归属规则（与 agents_crud 一致）：
  - user_id 为空  = 平台内置，全局共享，只读（禁改禁删）
  - user_id 非空  = 该用户自定义，私有
  - 列表只返回「内置 + 自己的」，看不到别人的

接口清单：
  - GET    /api/agents              → 角色菜单（内置 + 自己的）
  - POST   /api/agents              → 创建自定义 agent
  - PUT    /api/agents/{agent_id}   → 更新（白名单字段；内置返回 403）
  - DELETE /api/agents/{agent_id}   → 删除（内置返回 403）
"""
import json
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import assert_owner, current_user_id
from app.db.engine import get_db
from app.agents.ai_client import OUTPUT_KINDS
from app.agents import skill_meta
from app.agents.agent_author import AuthorError, author_prompt
from app.db.crud import agents_crud, workflows_crud
from app.db.crud.projects_crud import get_project_by_id
from app.db.models.project_steps_model import ProjectStepDB
from app.api.schemas.agents_schemas import (
    AgentUsageResponse,
    AgentRegistryCreate,
    AgentRegistryUpdate,
    AgentRegistryResponse,
    AgentAuthorRequest,
    AgentAuthorResponse,
)

router = APIRouter(prefix="/api/agents", tags=["Agent 角色注册表"])

# 可绑定技能（mode='skill' 时 skill_id 必须在这里面）。
#
# 以前是手写死的 10 个字符串，注释却写着"与 .dsh/skills/ 一一对应" —— 实际目录里是 13 个，
# 目录一增技能这里必然漂移（用户在下拉里看得到、选了却被 422 拒）。现在改成**扫目录**，
# 只有 `skill_meta.NOT_BINDABLE` 里明确列出的内部技能不对外开放。
# 与 `GET /api/skills` 同一份数据源，前端下拉不会再和后端校验打架。
BUILTIN_SKILL_IDS = set(skill_meta.bindable_skill_ids())

# 提示词长度上限（原来是 4000，两处硬编码）：
#   · PROMPT_MAX 是**落库值** system_prompt 的上限 —— "基于出厂技能整合"出来的提示词会
#     明显变长（出厂技能正文本身就 609~4030 字），4000 会逼着模型压缩、丢掉硬约束；
#   · DRAFT_MAX 是**给写手的草稿**上限 —— 要把整份技能正文当草稿传进去，必须高于它。
PROMPT_MAX = 8000
DRAFT_MAX = 12000


def _to_response(agent: agents_crud.AgentRegistryDB) -> dict:
    """数据库行 → 响应字典（自动标记 builtin）

    补两个**展示用**字段（前端 Agent 页面/技能页要，见 §3.1）：

        description  —— 一句话说明：技能角色取 SKILL.md frontmatter；prompt 角色取提示词首句
        prompt_text  —— **这个 Agent 真正会用的提示词正文**：
                        skill 角色 = SKILL.md 正文（去 frontmatter）；prompt 角色 = system_prompt

    为什么要从技能文件里取：内置角色的提示词**不在 agents 表里**（表里只有 system_prompt 一列，
    skill 角色那是空的），所以以前接口回 null、页面上"System Prompt"永远空白。
    """
    is_skill = (agent.mode or "skill") == "skill" and bool(agent.skill_id)
    if is_skill:
        description = skill_meta.skill_description(agent.skill_id)
        prompt_text = skill_meta.skill_prompt_text(agent.skill_id)
    else:
        prompt_text = agent.system_prompt or ""
        description = skill_meta.prompt_mode_summary(prompt_text)

    return {
        "id": agent.id,
        "user_id": agent.user_id,
        "role_key": agent.role_key,
        "name": agent.name,
        "mode": agent.mode,
        "skill_id": agent.skill_id,
        "system_prompt": agent.system_prompt,
        "description": description or None,
        "prompt_text": prompt_text or None,
        "output_kind": agent.output_kind,
        "builtin": agent.user_id is None,
    }


def _validate_output_kind(kind: Optional[str]) -> None:
    """output_kind 只允许闭集里的值（闭集唯一真相在 ai_client.OUTPUT_KINDS）。"""
    if kind is None or kind == "":
        return
    if kind not in OUTPUT_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"output_kind '{kind}' 非法，允许：{'/'.join(OUTPUT_KINDS)}",
        )


def _validate_create(body: AgentRegistryCreate) -> None:
    """创建时字段校验"""
    name = (body.name or "").strip()
    if not name or len(name) > 30:
        raise HTTPException(status_code=400, detail="name 必须 1-30 字（去首尾空白后）")

    if body.mode not in ("prompt", "skill"):
        raise HTTPException(status_code=400, detail="mode 必须为 'prompt' 或 'skill'")

    _validate_output_kind(body.output_kind)

    if body.mode == "prompt":
        if body.skill_id:
            raise HTTPException(status_code=400, detail="prompt 模式下 skill_id 必须为空")
        if not body.system_prompt or len(body.system_prompt.strip()) < 10:
            raise HTTPException(status_code=400, detail="prompt 模式下 system_prompt 必填且不少于 10 字")
        if len(body.system_prompt) > PROMPT_MAX:
            raise HTTPException(status_code=400, detail=f"system_prompt 不能超过 {PROMPT_MAX} 字")
    elif body.mode == "skill":
        if not body.skill_id:
            raise HTTPException(status_code=400, detail="skill 模式下 skill_id 必填")
        if body.skill_id not in BUILTIN_SKILL_IDS:
            raise HTTPException(status_code=422, detail=f"skill_id '{body.skill_id}' 不在平台技能白名单中")


def _validate_update(body: AgentRegistryUpdate) -> None:
    """更新时字段校验"""
    if body.name is not None:
        name = (body.name or "").strip()
        if not name or len(name) > 30:
            raise HTTPException(status_code=400, detail="name 必须 1-30 字（去首尾空白后）")

    if body.mode is not None and body.mode not in ("prompt", "skill"):
        raise HTTPException(status_code=400, detail="mode 必须为 'prompt' 或 'skill'")

    # mode 切换时的互斥校验
    if body.mode == "prompt" and body.skill_id is not None:
        raise HTTPException(status_code=400, detail="切换到 prompt 模式时 skill_id 必须为空")
    if body.mode == "skill" and body.skill_id is None and body.system_prompt is None:
        # 纯切换 mode 到 skill 但没给 skill_id，需要校验
        pass  # 由调用方决定是否提供 skill_id

    if body.skill_id is not None and body.skill_id not in BUILTIN_SKILL_IDS:
        raise HTTPException(status_code=422, detail=f"skill_id '{body.skill_id}' 不在平台技能白名单中")

    if body.system_prompt is not None and len(body.system_prompt) > PROMPT_MAX:
        raise HTTPException(status_code=400, detail=f"system_prompt 不能超过 {PROMPT_MAX} 字")

    _validate_output_kind(body.output_kind)


# ==================== 接口 ====================

@router.get("", response_model=List[AgentRegistryResponse], summary="获取角色菜单（内置 + 自己的）")
def list_agents(
    uid: Optional[int] = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """返回：内置 + 自己的自定义 Agent，看不到别人的。"""
    if uid is None:
        raise HTTPException(status_code=422, detail="缺少身份信息（user_id 或 token）")
    agents = agents_crud.list_for_user(db, user_id=uid)
    return [_to_response(a) for a in agents]


@router.post("", response_model=AgentRegistryResponse, status_code=201, summary="创建自定义 Agent")
def create_agent(
    body: AgentRegistryCreate,
    uid: Optional[int] = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """创建自定义 Agent；role_key 由后端自动生成 custom_{uuid8}。只能创建属于自己的。"""
    if uid is not None and uid != body.user_id:
        raise HTTPException(status_code=404, detail="用户不存在")
    _validate_create(body)

    # 自动生成 role_key（避免用户可控导致冲突/冒充内置）
    custom_role_key = f"custom_{uuid.uuid4().hex[:8]}"

    try:
        agent = agents_crud.create_agent(
            db,
            name=body.name.strip(),
            role_key=custom_role_key,
            mode=body.mode,
            user_id=body.user_id,
            skill_id=body.skill_id,
            system_prompt=body.system_prompt.strip() if body.system_prompt else None,
            output_kind=body.output_kind or None,
        )
    except agents_crud.AgentConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return _to_response(agent)


@router.post(
    "/author",
    response_model=AgentAuthorResponse,
    summary="一句话描述 → 规范化 Agent 提示词（自定义 Agent 的第一步）",
)
async def author_agent(
    body: AgentAuthorRequest,
    uid: Optional[int] = Depends(current_user_id),
    db: Session = Depends(get_db),  # noqa: ARG001  与其它端点保持一致的依赖签名
):
    """把用户的一句描述交给 `agent-prompt-authoring` 技能，产出可直接落库的提示词。

    **只生成、不落库** —— 用户过目后再调 `POST /api/agents`（mode='prompt'）保存。
    完整闭环见 `app/agents/agent_author.py` 的模块说明。

    ⚠️ 会真起一次 DSH 会话（**烧模型额度**），耗时约 10–60 秒。
    """
    # 身份口径与 POST /api/agents（create_agent）保持一致：query/token 优先，其次 body.user_id。
    # 以前这里**只**认 query/token，而前端 author() 又没带，于是必 422；
    # 同一个模块里两个端点两种口径，谁调用谁踩（实测报障）。
    if uid is None:
        uid = body.user_id
    if uid is None:
        raise HTTPException(status_code=422,
                            detail="缺少身份信息：请在 query 里带 ?user_id=、或带上登录 token")
    if uid != body.user_id:
        raise HTTPException(status_code=404, detail="用户不存在")

    goal = (body.agent_goal or "").strip()
    if not (2 <= len(goal) <= 500):
        raise HTTPException(status_code=400, detail="agent_goal 必须 2-500 字（去首尾空白后）")
    draft = (body.user_draft or "").strip()
    if len(draft) > DRAFT_MAX:
        raise HTTPException(status_code=400, detail=f"user_draft 不能超过 {DRAFT_MAX} 字")

    # 「基于出厂技能整合」：技能必须是可绑定的（内部技能不给用），
    # 且**允许被正文撑长** —— 出厂技能正文最长 4030 字，所以草稿上限要高于它
    base_skill = (body.base_skill_id or "").strip()
    if base_skill and base_skill not in BUILTIN_SKILL_IDS:
        raise HTTPException(status_code=422,
                            detail=f"base_skill_id '{base_skill}' 不是可用的出厂技能")

    try:
        result = await author_prompt(agent_goal=goal, user_draft=draft,
                                     base_skill_id=base_skill)
    except AuthorError as e:
        raise HTTPException(status_code=502, detail=f"提示词生成失败: {e}")

    return {
        **result,
        "message": (
            (
                f"已基于出厂技能「{result['base_skill_id']}」整合完成"
                f"（输出契约跟随原技能：{result.get('suggested_output_kind') or '—'}）。"
                "确认无误后调 POST /api/agents（mode='prompt'）落库，画布左侧面板会立刻多出这个节点。"
                if result.get("base_skill_id")
                else "生成完成。确认无误后调 POST /api/agents（mode='prompt'）落库，"
                     "画布左侧面板会立刻多出这个节点。"
            )
            if result["usable"]
            else f"生成结果不足 {10} 字，落库会被拒，建议重试或手写草稿后再生成。"
        ),
    }


@router.put("/{agent_id}", response_model=AgentRegistryResponse, summary="更新 Agent（白名单字段）")
def update_agent(
    agent_id: int,
    body: AgentRegistryUpdate,
    uid: Optional[int] = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """更新 Agent；可改 name/mode/skill_id/system_prompt，禁改 user_id/role_key。"""
    agent = agents_crud.get_by_id(db, agent_id)
    if agent and agent.user_id is None:
        raise HTTPException(status_code=403, detail="内置 Agent 不允许修改")
    assert_owner(agent, uid, "Agent")        # 只能改自己的自定义 Agent

    _validate_update(body)

    try:
        updated = agents_crud.update_agent(
            db,
            agent,
            name=body.name.strip() if body.name else None,
            mode=body.mode,
            skill_id=body.skill_id,
            system_prompt=body.system_prompt.strip() if body.system_prompt else None,
            output_kind=body.output_kind or None,
        )
    except agents_crud.AgentConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return _to_response(updated)


@router.delete("/{agent_id}", status_code=204, summary="删除自定义 Agent")
@router.get("/{agent_id}/usage", response_model=AgentUsageResponse,
            summary="这个 Agent 被哪些工作流引用（删除前的影响面提示）")
def agent_usage(
    agent_id: int,
    uid: Optional[int] = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """**删除前先问一句"谁还在用它"**。

    删除是硬删（库里没有软删标记），而工作流里存的是 `agent.role_key`：
    Agent 一旦没了，图上那些节点的角色就解析不到 —— 画布会标红提示，运行到那一步直接失败。
    所以删除确认框要把"会影响哪几张图"说清楚，而不是一句干巴巴的"确认删除？"。

    另外报一下**历史项目**用过它的次数：那些 project_steps 的 agent_id 会变成悬空，
    步骤卡片显示不出 Agent 名字（已有产物不受影响，只是留痕不完整）。
    """
    agent = agents_crud.get_by_id(db, agent_id)
    if agent and agent.user_id is None:
        return {"agent_id": agent_id, "name": agent.name, "role_key": agent.role_key,
                "deletable": False, "reason": "内置 Agent 不允许删除（它是全平台共享的角色）",
                "workflows": [], "project_count": 0, "project_titles": []}
    assert_owner(agent, uid, "Agent")

    # 引用了这个 role_key 的工作流（图存在 nodes JSON 里，按 role_key 找）
    workflows = []
    for wf in workflows_crud.get_workflows_by_user(db, uid):
        try:
            nodes = json.loads(wf.nodes) if isinstance(wf.nodes, str) else (wf.nodes or [])
        except (TypeError, ValueError):
            continue
        hit = [n.get("name") or n.get("id") or "?" for n in nodes
               if ((n.get("agent") or {}).get("role_key") or "") == agent.role_key]
        if hit:
            workflows.append({"id": wf.id, "name": wf.name, "nodes": hit})

    # 用过它的历史项目（按 project_steps.agent_id 统计）
    used = (db.query(ProjectStepDB.project_id)
            .filter(ProjectStepDB.agent_id == agent_id).distinct().all())
    pids = [row[0] for row in used]
    titles = []
    for pid in pids[:3]:
        proj = get_project_by_id(db, pid)
        if proj:
            titles.append(proj.title)

    return {"agent_id": agent_id, "name": agent.name, "role_key": agent.role_key,
            "deletable": True, "reason": None,
            "workflows": workflows, "project_count": len(pids), "project_titles": titles}


@router.delete("/{agent_id}", status_code=204, summary="删除自定义 Agent")
def delete_agent(
    agent_id: int,
    uid: Optional[int] = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """删除自定义 Agent；内置行返回 403；不存在或不属于自己返回 404。"""
    agent = agents_crud.get_by_id(db, agent_id)
    if agent and agent.user_id is None:
        raise HTTPException(status_code=403, detail="内置 Agent 不允许删除")
    assert_owner(agent, uid, "Agent")        # 只能删自己的自定义 Agent

    try:
        agents_crud.delete_agent(db, agent_id)
    except agents_crud.BuiltinProtectError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return None
