from sqlalchemy import JSON, Column, ForeignKey, Integer, String
from app.db.engine import Base


class WorkflowDB(Base):
    """自定义工作流表模型"""
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    nodes = Column(JSON, nullable=False)  # 工作流节点定义
    builtin = Column(Integer, nullable=False, default=0)  # 是否为内置工作流 0/1
