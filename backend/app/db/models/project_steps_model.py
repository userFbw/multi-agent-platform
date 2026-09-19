"""project_steps —— 步骤执行明细表（步骤卡片 / 会话号 / 耗时 / 错误码）

agent_id 定为【可空 + ON DELETE SET NULL】：删掉自定义 agent 时历史步骤行应保留，
而不是报外键错或被级联删光。建表走 init_db 的 create_all，索引在 migrate.py 补。
"""

from sqlalchemy import Column, ForeignKey, Integer, String, Text

from app.db.engine import Base


class ProjectStepDB(Base):
    __tablename__ = "project_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True)
    round_no = Column(Integer, nullable=False, default=1)          # 初始=1，修改重跑=2…
    step_no = Column(Integer, nullable=False)                      # 本轮内 1..N（卡片排序）
    name = Column(String, nullable=False)                          # 步骤标题（生成 PRD / 写代码 / 测试…）
    status = Column(String, nullable=False, default="PENDING")     # PENDING/RUNNING/SUCCESS/FAILED/SKIPPED
    artifact_path = Column(String, nullable=True)                  # 产物相对路径（PRD.md / src/ / Task_Plan.md…）
    session_id = Column(String, nullable=True)                     # 该步 DSH 会话号 → data/logs/u<uid>/p<pid>/<会话号>.json
    elapsed_ms = Column(Integer, nullable=True, default=0)         # 该步真实耗时（耗时看板数据源）
    # 该步**开跑**的时刻（epoch 毫秒）。RUNNING 期间 elapsed_ms 还是 0，前端靠它显示
    # "已执行 X 分 Y 秒"（否则长节点只能显示"进行中"，用户没法判断是不是卡住了）。
    started_at_ms = Column(Integer, nullable=True)
    error_code = Column(String, nullable=True)                     # 稳定机器错误码（见 接口文档/前端接口文档.md §七）
    error = Column(Text, nullable=True)                            # 可读错误文案（给人看）
