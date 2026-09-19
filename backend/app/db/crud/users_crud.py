"""users 表 CRUD —— 纯数据库操作。

注意：密码为历史明文存储（哈希改造另行立项），本层不做加解密。
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models.auth_model import UserDB
from app.db.crud.base import delete_and_commit


def get_user_by_id(db: Session, user_id: int):
    """根据 ID 获取用户"""
    return db.get(UserDB, user_id)


def get_user_by_name(db: Session, user_name: str):
    """根据用户名获取用户（防重校验/登录用）"""
    return db.query(UserDB).filter(UserDB.user_name == user_name).first()


def exists_name(db: Session, user_name: str) -> bool:
    """用户名是否已被占用"""
    return get_user_by_name(db, user_name) is not None


def create_user(db: Session, user_name: str, password: str) -> UserDB:
    """创建新用户（唯一性校验由路由层负责，本层幂等写入）"""
    db_user = UserDB(user_name=user_name, password=password, avatar=None)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def update_user(
    db: Session, db_user: UserDB, *,
    user_name: Optional[str] = None,
    password: Optional[str] = None,
    avatar: Optional[str] = None,
) -> UserDB:
    """更新这三个可改字段；传 None 表示不改。

    白名单由**函数签名**表达（而不是靠上层 DTO 限制），db 层因此不依赖 api 层。
    """
    if user_name is not None:
        db_user.user_name = user_name
    if password is not None:
        db_user.password = password
    if avatar is not None:
        db_user.avatar = avatar
    db.commit()
    db.refresh(db_user)
    return db_user


def delete_user(db: Session, db_user: UserDB) -> bool:
    """删除用户（其项目/成果由外键级联删除）"""
    return delete_and_commit(db, db_user)


def authenticate_user(db: Session, user_name: str, password: str):
    """明文比对登录校验"""
    return (
        db.query(UserDB)
        .filter(UserDB.user_name == user_name, UserDB.password == password)
        .first()
    )
