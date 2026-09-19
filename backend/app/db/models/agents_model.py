from sqlalchemy import Column, Integer, String, ForeignKey, Text
from app.db.engine import Base

class ProjectAgentDB(Base):
    """AI 角色成果表（表名 project_agents）：某角色在某项目上产出了什么。

    注意与 agent_registry_model.AgentRegistryDB（表名 agents，角色菜单）区分。
    """
    __tablename__ = "project_agents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    role = Column(String, nullable=False)     
    agent_name = Column(String, nullable=False)   
    elapsed_time = Column(Integer, default=0)  
    final_output = Column(Text, nullable=True)      
    path = Column(String, nullable=True)
    session_id = Column(String, nullable=True)          # DSH 会话号（migrate 对老库补列）