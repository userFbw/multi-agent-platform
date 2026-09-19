from sqlalchemy import Column, Integer, String
from app.db.engine import Base

class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_name = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False) # 明文存储密码
    avatar = Column(String, nullable=True)     #头像允许为空