"""Token 签发与验签（HMAC 签名，零外部依赖）。

    token   = base64url(payload) + "." + base64url(HMAC-SHA256(secret, payload_b64))
    payload = {"uid": <int>, "exp": <unix 时间戳>}

设计要点：
    · payload 是**明文**（base64 是编码不是加密）→ 绝不能放密码等敏感信息；
    · 不可伪造性来自签名 —— 没有 SECRET_KEY 就算不出正确签名；
    · **无状态**：验签不查库。代价是改密码/删用户不会让已签发的 token 立刻失效，
      只能等 TOKEN_TTL_SECONDS 到期（如需即时失效，得引入服务端存储）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Optional

from app.core.config import settings

_DIGEST = hashlib.sha256


def _b64e(raw: bytes) -> str:
    """bytes → base64url 字符串（去掉 = 填充，便于放 URL/Header）。"""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    """base64url 字符串 → bytes（自动补回 = 填充）。"""
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload_b64: str) -> str:
    """对 payload 的 base64 串做 HMAC-SHA256，返回 base64url 签名。"""
    mac = hmac.new(settings.SECRET_KEY.encode("utf-8"), payload_b64.encode("ascii"), _DIGEST)
    return _b64e(mac.digest())


def create_token(user_id: int, ttl_seconds: int = 0) -> str:
    """为用户签发 token。ttl_seconds 为 0 时用配置里的默认有效期。"""
    ttl = ttl_seconds or settings.TOKEN_TTL_SECONDS
    payload = {"uid": int(user_id), "exp": int(time.time()) + int(ttl)}
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64)}"


def verify_token(token: str) -> Optional[int]:
    """验签并解出 user_id；任何异常（格式错/签名错/过期）一律返回 None，绝不抛错。"""
    if not token or "." not in token:
        return None
    payload_b64, _, sig = token.partition(".")
    if not payload_b64 or not sig:
        return None

    # 先验签再解析 payload —— 未通过签名校验的内容一律不采信
    try:
        if not hmac.compare_digest(sig, _sign(payload_b64)):
            return None
    except Exception:  # noqa: BLE001 —— 非法字符等
        return None

    try:
        payload = json.loads(_b64d(payload_b64).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None

    if not isinstance(payload, dict):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    uid = payload.get("uid")
    return int(uid) if isinstance(uid, int) else None
