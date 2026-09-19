"""CRUD 共享小工具：减少各表模块的重复代码。"""
from typing import Any

from sqlalchemy.orm import Session


def commit_and_refresh(db: Session, obj) -> Any:
    """新增后提交并刷新，返回带自增 id 的对象。"""
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def delete_and_commit(db: Session, obj) -> bool:
    """删除并提交，返回 True。"""
    db.delete(obj)
    db.commit()
    return True


def paginate(query, page: int = 1, size: int = 20) -> tuple:
    """对已过滤的 Query/select 应用分页，返回 (items, total)。

    阶段二 project_steps 等高频列表查询可直接复用。
    """
    total = query.count()
    items = query.offset((page - 1) * size).limit(size).all()
    return items, total
