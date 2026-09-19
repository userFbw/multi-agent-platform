from sqlalchemy import Column, Integer, String, Text, ForeignKey
from app.db.engine import Base


class AgentRegistryDB(Base):
    """AI 角色注册表（agents）：平台内置（user_id 为空，全局共享）+ 用户自定义（user_id 非空，私有）"""
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    role_key = Column(String, nullable=False)                # 内置: pm/dev/qa/reviewer/docgen; 自定义: custom_xxx
    name = Column(String, nullable=False)                    # 显示名: 产品经理…
    mode = Column(String, nullable=False, default="skill")   # skill=绑定DSH技能 / prompt=用户填提示词
    skill_id = Column(String, nullable=True)                 # 绑定的 DSH 技能目录名, 如 pm-workflow
    system_prompt = Column(Text, nullable=True)              # prompt 模式的角色提示词正文
    output_kind = Column(String, nullable=True)              # 输出契约覆盖：json_object/json_array/file_blocks/text
                                                             # 空 = 用所绑技能的 frontmatter 声明
