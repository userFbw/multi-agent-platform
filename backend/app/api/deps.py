"""FastAPI 依赖：识别当前用户 + 归属校验。

识别顺序（三级回落）：
    ① `Authorization: Bearer <token>` → 验签得 user_id（前端接入后自动生效）
    ② `?user_id=<n>`                  → 直接采用（兼容前端现状）
    ③ 都没有                           → None；AUTH_REQUIRE_IDENTITY=True 时直接 401

实现说明：这里**不用 `Query(...)` 声明**，而是直接读 `request.query_params`。
因为 `?user_id=` 与部分端点的路径参数 `/{user_id}` 同名，用 Query 声明会让 FastAPI
误判成路径参数并报 `Cannot use Query for path param`。

把"身份从哪来"收敛在这一个函数里：将来改鉴权方式只动这里，各端点不用改。
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request

from app.core.config import settings
from app.core.security import verify_token

_BEARER = "Bearer "


def _uid_from_query(request: Request) -> Optional[int]:
    """从 ?user_id= 取（过渡期兼容）。非法值直接 422，不静默忽略。"""
    raw = request.query_params.get("user_id")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail="user_id 必须是整数")


def current_user_id(request: Request) -> Optional[int]:
    """从请求中解析当前用户 ID；无法识别时返回 None（除非强制要求身份）。"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith(_BEARER):
        uid = verify_token(auth[len(_BEARER):].strip())
        if uid is not None:
            return uid
        if settings.AUTH_REQUIRE_IDENTITY:
            raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录")

    uid = _uid_from_query(request)
    if uid is not None:
        return uid

    if settings.AUTH_REQUIRE_IDENTITY:
        raise HTTPException(status_code=401, detail="未登录")
    return None


def assert_owner(row, uid: Optional[int], what: str = "资源") -> None:
    """归属校验。

    uid 为 None（未识别身份且未强制）= 兼容放行，不做归属判断；
    否则：行不存在、或不属于该用户 → 404（用 404 而非 403，避免泄露资源是否存在）。
    """
    if uid is None:
        return
    if row is None or getattr(row, "user_id", None) != uid:
        raise HTTPException(status_code=404, detail=f"{what}不存在")
