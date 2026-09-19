"""内置 agents 种子 —— 对账式 reconcile（可重复执行）。

一行 agents = 一个可直接实例化的技能执行单元（role_key 对应 .dsh/skills 目录）。
多步/分支链路（architect → 前后端执行器）不进表，由编排图表达。

reconcile 语义：缺 → 新增；字段变 → 同步；多余的内置行 → 清理。
"""
from sqlalchemy.orm import Session

# 显式导入全部模型：插入 agents（FK→users）前 users 模型须已注册进 metadata，
# 否则报 NoReferencedTableError（init_db.py 同样这么做）
from app.db.models import agents_model, auth_model, projects_model  # noqa: F401
from app.db.models.agent_registry_model import AgentRegistryDB  # noqa: F401

from app.db.crud import agents_crud
from app.db.engine import SessionLocal

# 平台内置 10 个执行单元（role_key 与 .dsh/skills 目录一一对应）
BUILTIN_AGENTS = [
    {"role_key": "planner", "name": "任务编排官", "mode": "skill", "skill_id": "planner"},
    {"role_key": "pm", "name": "产品经理", "mode": "skill", "skill_id": "pm-workflow"},
    {"role_key": "architect", "name": "系统架构师", "mode": "skill", "skill_id": "architect-planner"},
    {"role_key": "backend-executor", "name": "后端开发工程师", "mode": "skill", "skill_id": "backend-executor"},
    {"role_key": "frontend-executor", "name": "前端开发工程师", "mode": "skill", "skill_id": "frontend-executor"},
    {"role_key": "simple-frontend", "name": "简单前端工程师", "mode": "skill", "skill_id": "simple-frontend"},
    {"role_key": "qa", "name": "测试工程师", "mode": "skill", "skill_id": "qa-workflow"},
    {"role_key": "reviewer", "name": "代码审查员", "mode": "skill", "skill_id": "code-reviewer"},
    {"role_key": "docgen", "name": "文档生成器", "mode": "skill", "skill_id": "doc-writer"},
]


def seed_builtin_agents(db: Session) -> dict:
    """对账式种子：返回 {'added': int, 'updated': int, 'purged': int}"""
    stats = {"added": 0, "updated": 0, "purged": 0}
    existing = {row.role_key: row for row in agents_crud.list_builtin(db)}
    desired_keys = {item["role_key"] for item in BUILTIN_AGENTS}

    # 1) 缺 -> 新增；变 -> 白名单更新
    for item in BUILTIN_AGENTS:
        rk = item["role_key"]
        row = existing.get(rk)
        if not row:
            agents_crud.create_agent(db, user_id=None, **item)
            stats["added"] += 1
            print(f"[ADD] 内置 agents[{rk}] 已插入")
            continue
        changed = (
            row.name != item["name"]
            or row.mode != item["mode"]
            or row.skill_id != item["skill_id"]
        )
        if changed:
            agents_crud.update_agent(
                db, row,
                name=item["name"], mode=item["mode"], skill_id=item["skill_id"],
            )
            stats["updated"] += 1
            print(f"[UPDATE] 内置 agents[{rk}] 绑定已同步（skill_id={item['skill_id']}）")

    # 2) 多余内置占位行（不在 desired 中）-> 清理（seed 专用出口）
    stale = [rk for rk in existing if rk not in desired_keys]
    if stale:
        stats["purged"] = agents_crud.purge_builtin_rows(db, stale)
        print(f"[DELETE] 清理旧内置占位行: {stale}（删除 {stats['purged']} 条）")

    print(f"[DONE] 内置 agents 对账完成：新增 {stats['added']} / 同步 {stats['updated']} / 清理 {stats['purged']}")
    return stats


def main() -> None:
    db = SessionLocal()
    try:
        seed_builtin_agents(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
