"""幂等迁移工具：老表只加可空列 + 补索引；新表由 create_all 负责，此处不管。"""
import os
import shutil
import time

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.db.engine import engine
from app.storage.paths import DATA_DIR

# 老表可空新增列（与 数据库设计方案.md 一致，全部可空，老行零影响）
ADD_COLUMNS = [
    # (表名, 列名, DDL)
    ("project_agents", "session_id", "ALTER TABLE project_agents ADD COLUMN session_id VARCHAR"),
    ("projects", "pass_first_try", "ALTER TABLE projects ADD COLUMN pass_first_try INTEGER"),
    ("project_steps", "error_code", "ALTER TABLE project_steps ADD COLUMN error_code VARCHAR"),
    ("project_steps", "error", "ALTER TABLE project_steps ADD COLUMN error TEXT"),
    # agent 级输出契约：自定义 Agent（尤其 prompt 模式）用它声明"我要输出 JSON / 代码块"，
    # 空则沿用所绑技能 frontmatter 的声明（见 W6 §4.1）
    ("agents", "output_kind", "ALTER TABLE agents ADD COLUMN output_kind VARCHAR"),
    # 项目选定的工作流：创建项目时定下来，之后审批 / 迭代沿用同一张图
    ("projects", "workflow_id", "ALTER TABLE projects ADD COLUMN workflow_id INTEGER"),
    ("projects", "workflow_name", "ALTER TABLE projects ADD COLUMN workflow_name VARCHAR"),
    # 编排模式：workflow（先有图再执行）/ agent（先 PM，审批后 Planner 出图）
    ("projects", "mode", "ALTER TABLE projects ADD COLUMN mode VARCHAR DEFAULT 'workflow'"),
    # agent 模式：编排官当场出的图（重跑/迭代要原样复用，别退回模版图）
    ("projects", "planned_workflow", "ALTER TABLE projects ADD COLUMN planned_workflow JSON"),
    # 步骤开跑时刻（epoch 毫秒）：RUNNING 期间前端据此显示实时耗时
    ("project_steps", "started_at_ms", "ALTER TABLE project_steps ADD COLUMN started_at_ms INTEGER"),
]

# 老表外键列补索引（SQLite 外键不自动建索引）+ project_steps 常用查询索引（设计稿 ⑤）
ADD_INDEXES = [
    ("idx_projects_user_id", "CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id)"),
    ("idx_project_agents_project_id", "CREATE INDEX IF NOT EXISTS idx_project_agents_project_id ON project_agents(project_id)"),
    ("idx_project_steps_project_id", "CREATE INDEX IF NOT EXISTS idx_project_steps_project_id ON project_steps(project_id)"),
    ("idx_project_steps_status", "CREATE INDEX IF NOT EXISTS idx_project_steps_status ON project_steps(status)"),
]


def _existing_columns(table: str) -> set:
    with engine.connect() as con:
        rows = con.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


def backup_db() -> str:
    """ALTER 前自动备份 project.db，保证可回滚。"""
    src = os.path.join(DATA_DIR, "project.db")
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(DATA_DIR, f"project.db.bak_{ts}")
    shutil.copy2(src, dst)
    print(f"📦 已自动备份数据库 -> {dst}")
    return dst


def run_migrations(backup: bool = True) -> None:
    """幂等执行：先探测需要加哪些列，需要动库前先备份，然后逐条 ALTER。"""
    pending = [(t, c, ddl) for (t, c, ddl) in ADD_COLUMNS if c not in _existing_columns(t)]

    if pending and backup:
        backup_db()

    for table, col, ddl in pending:
        with engine.connect() as con:
            con.execute(text(ddl))
            con.commit()
        print(f"➕ {table} 增加列 {col}")

    for name, ddl in ADD_INDEXES:
        with engine.connect() as con:
            try:
                con.execute(text(ddl))
                con.commit()
                print(f"[OK] 索引就绪 {name}")
            except OperationalError as e:
                if "no such table" not in str(e):
                    raise
                # 新表索引：若表尚未由 create_all 创建则跳过（下次 init_db 会补上）
                print(f"[SKIP] 跳过索引 {name}（表尚未创建，待 create_all 后重跑补上）")
                con.rollback()

    if not pending:
        print("[OK] 老表无需加列（列均已存在），仅检查/补索引")


if __name__ == "__main__":
    run_migrations()
