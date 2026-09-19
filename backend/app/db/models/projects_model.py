from sqlalchemy import JSON, Column, Integer, String, ForeignKey, Text
from app.db.engine import Base

class ProjectDB(Base):
    """项目主表模型"""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="INITIAL")  # INITIAL, RUNNING, COMPLETED, FAILED
    zip_path = Column(String, nullable=True)
    pass_first_try = Column(Integer, nullable=True)  # 首次编译复验是否通过 0/1（migrate 对老库补列）
    # 这个项目选定的工作流：**创建项目时就定下来**，之后审批 / 迭代都沿用同一张图。
    # 自定义图存 id（改名也不影响解析），内置模版存 name（内置没有 id）。都为空 = 用默认模版。
    workflow_id = Column(Integer, nullable=True)
    workflow_name = Column(String, nullable=True)
    # 编排模式：workflow = 用户先画好/选好图（图在执行前就存在）；agent = 先跑 PM，审批通过后
    # 由 Planner 读 PRD 现场出图（图在审批之后才产生）。两种模式的审批闸门**都在 PM 之后**。
    mode = Column(String, nullable=False, default="workflow")

    # ★ agent 模式：编排官（Planner）当场出的那张图。存下来是为了**重跑/迭代时原样复用** ——
    #   否则会退回模版图，编排官插的自定义节点会被悄悄丢掉（真机踩到，p46）。
    #   用户显式换图时（审批/迭代带 workflow_*）会被清空，让显式选择说了算。
    planned_workflow = Column(JSON, nullable=True)