"""agents 表 CRUD（AI 角色注册表 / 菜单）—— 阶段一查表驱动的地基。

归属规则（隔离第④层）：
  - user_id 为空  = 平台内置，全局共享，且【不允许删除】(保护底座)
  - user_id 非空  = 该用户自定义，私有
  - list_for_user 只返回 "内置 + 自己的"，看不到别人的
唯一性：内置按 role_key 全局唯一；自定义按 (user_id, role_key) 唯一，由本层校验。
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models.agent_registry_model import AgentRegistryDB
from app.db.crud.base import commit_and_refresh


class AgentConflictError(ValueError):
    """role_key 重复（内置全局唯一 / 自定义同用户下唯一）"""


class BuiltinProtectError(ValueError):
    """内置 Agent 不允许删除"""


def create_agent(
    db: Session,
    name: str,
    role_key: str,
    mode: str = "skill",
    *,
    user_id: Optional[int] = None,
    skill_id: Optional[str] = None,
    system_prompt: Optional[str] = None,
    output_kind: Optional[str] = None,
) -> AgentRegistryDB:
    """注册一个 Agent；role_key 冲突抛 AgentConflictError"""
    if get_by_role_key(db, role_key, user_id=user_id):
        scope = "平台内置" if user_id is None else "该用户"
        raise AgentConflictError(f"角色标识 {role_key!r} 在{scope}下已存在")
    return commit_and_refresh(
        db,
        AgentRegistryDB(
            user_id=user_id, role_key=role_key, name=name,
            mode=mode, skill_id=skill_id, system_prompt=system_prompt,
            output_kind=output_kind,
        ),
    )


def get_by_id(db: Session, agent_id: int) -> Optional[AgentRegistryDB]:
    return db.get(AgentRegistryDB, agent_id)


def get_by_role_key(
    db: Session, role_key: str, *, user_id: Optional[int] = None
) -> Optional[AgentRegistryDB]:
    """按 role_key 查；user_id 不传/None 只查内置；传值查该用户自定义"""
    q = db.query(AgentRegistryDB).filter(AgentRegistryDB.role_key == role_key)
    if user_id is None:
        q = q.filter(AgentRegistryDB.user_id.is_(None))
    else:
        q = q.filter(AgentRegistryDB.user_id == user_id)
    return q.first()


def list_for_user(db: Session, user_id: int) -> list:
    """用户可见角色菜单 = 平台内置 + 该用户自定义"""
    return (
        db.query(AgentRegistryDB)
        .filter(
            (AgentRegistryDB.user_id.is_(None))
            | (AgentRegistryDB.user_id == user_id)
        )
        .order_by(AgentRegistryDB.id)
        .all()
    )


def list_builtin(db: Session) -> list:
    """全部平台内置角色"""
    return (
        db.query(AgentRegistryDB)
        .filter(AgentRegistryDB.user_id.is_(None))
        .order_by(AgentRegistryDB.id)
        .all()
    )


def update_agent(
    db: Session,
    agent: AgentRegistryDB,
    *,
    name: Optional[str] = None,
    mode: Optional[str] = None,
    skill_id: Optional[str] = None,
    system_prompt: Optional[str] = None,
    output_kind: Optional[str] = None,
) -> AgentRegistryDB:
    """白名单更新：禁止通过本方法改 user_id / role_key"""
    if name is not None:
        agent.name = name
    if mode is not None:
        agent.mode = mode
    if skill_id is not None:
        agent.skill_id = skill_id
    if system_prompt is not None:
        agent.system_prompt = system_prompt
    if output_kind is not None:
        agent.output_kind = output_kind
    db.commit()
    db.refresh(agent)
    return agent


def delete_agent(db: Session, agent_id: int) -> bool:
    """删除自定义 Agent；内置(user_id 为空)抛 BuiltinProtectError"""
    agent = get_by_id(db, agent_id)
    if not agent:
        return False
    if agent.user_id is None:
        raise BuiltinProtectError(f"内置 Agent {agent.name!r} 不允许删除")
    db.delete(agent)
    db.commit()
    return True


def purge_builtin_rows(db: Session, role_keys: list) -> int:
    """【仅供 seed 对账迁移】删除指定 role_key 的内置占位行，返回删除条数。

    说明：业务层删除内置角色必须走 delete_agent（抛 BuiltinProtectError 保护底座）；
    本方法是 seed.py 由“跳过式幂等”升级为“对账式 reconcile”时的唯一内部出口
    （用于清掉老版本遗留的 dev/reviewer/docgen 等占位行），不要在业务代码中调用。
    """
    rows = (
        db.query(AgentRegistryDB)
        .filter(
            AgentRegistryDB.user_id.is_(None),
            AgentRegistryDB.role_key.in_(role_keys),
        )
        .all()
    )
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)
