#!/usr/bin/env python3
"""建库脚本（幂等，可重复执行；对老库零破坏，动库前自动备份）。

流程：create_all 建表 → 幂等加列/索引 → 灌内置 agents 种子

用法（在 backend/ 目录下执行）：
    python3 app/db/init_db.py
等价写法：python3 -m app.db.init_db
脚本自带 sys.path 引导，因此在任意 cwd 下都能跑。
"""
import sys
from pathlib import Path

# 把 backend/ 加入 sys.path，使 `python3 app/db/init_db.py` 也能导入 app.*
_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.db.engine import Base, SessionLocal, engine  # noqa: E402
from app.db.migrate import run_migrations  # noqa: E402
from app.db.seed import seed_builtin_agents  # noqa: E402

# 显式导入全部模型，保证 Base.metadata 覆盖所有表（含老表，避免遗漏）
from app.db.models import agents_model, auth_model, projects_model  # noqa: E402,F401
from app.db.models.agent_registry_model import AgentRegistryDB  # noqa: E402,F401
from app.db.models.project_steps_model import ProjectStepDB  # noqa: E402,F401
from app.db.models.workflows_model import WorkflowDB  # noqa: E402,F401


def main() -> None:
    print("== [1/3] create_all：创建/确认全部表 ==")
    Base.metadata.create_all(bind=engine)
    print("[OK] 已存在表是 no-op")

    print("\n== [2/3] 幂等迁移：老表加可空列 + 补索引 ==")
    run_migrations(backup=True)

    print("\n== [3/3] 内置 agents 种子 ==")
    db = SessionLocal()
    try:
        seed_builtin_agents(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
