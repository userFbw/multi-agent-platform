from typing import Optional

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    """创建用户请求体"""
    user_name: str = Field(..., min_length=2, max_length=50, description="用户名")
    password: str = Field(..., min_length=1, description="密码")


class UserUpdate(BaseModel):
    """修改用户信息请求体（字段全部可选，只提交要改的）"""
    user_name: Optional[str] = Field(None, min_length=2, max_length=50, description="新用户名")
    password: Optional[str] = Field(None, min_length=1, description="新密码")
    avatar: Optional[str] = Field(None, description="头像图片文件名或路径")


class UserResponse(BaseModel):
    """用户数据（含明文密码，仅用于开发联调）"""
    id: int
    user_name: str
    password: str
    avatar: Optional[str] = None

    class Config:
        from_attributes = True


class RegisterResponse(UserResponse):
    """注册响应：用户字段与 UserResponse 一致，只多加一个 token。

    前端注册成功即视为已登录，故必须一并签发 token，否则注册后没有身份凭证。
    """
    token: Optional[str] = None


class LoginResponse(BaseModel):
    """登录响应（含身份凭证 token）"""
    status: str
    user_id: int
    user_name: str
    avatar: Optional[str] = None
    token: Optional[str] = None


class UserLogin(BaseModel):
    """用户登录请求体"""
    user_name: str
    password: str
