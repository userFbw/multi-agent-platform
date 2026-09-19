"""用户接口 —— 账号 CRUD、登录、头像上传

接口清单：
  - POST   /api/users                          → 创建用户（返回 token）
  - GET    /api/users/{user_id}                → 按 ID 查询
  - PUT    /api/users/{user_id}                → 修改用户名 / 密码 / 头像
  - DELETE /api/users/{user_id}                → 删除用户（其项目级联删除）
  - POST   /api/users/login                    → 登录校验（返回 token）
  - POST   /api/users/{user_id}/upload-avatar  → 上传头像

身份凭证：登录与注册都会签发 token（`app/core/security.py`），前端后续请求以
`Authorization: Bearer <token>` 携带即可；未携带时按 `?user_id=` 回落（见 `app/api/deps.py`）。
"""
import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import assert_owner, current_user_id
from app.api.schemas.auth_schemas import (
    LoginResponse,
    RegisterResponse,
    UserCreate,
    UserLogin,
    UserResponse,
    UserUpdate,
)
from app.core.security import create_token
from app.db.crud import users_crud
from app.db.engine import get_db
from app.storage.paths import IMAGES_DIR

router = APIRouter(prefix="/api/users", tags=["用户认证与管理"])


def convert_to_accessible_user(user, base_url: str = ""):
    """把库里存的头像文件名拼成**站内路径**（如 `/avatars/user_9_avatar.png`）。

    为什么不拼主机名：平台可能经隧道/反代访问，后端**看不到浏览器真正用的地址**；
    写死 `http://127.0.0.1:8000` 会让远端浏览器里头像变成破图（真机踩到过）。
    相对路径交给浏览器按当前站点解析即可 —— 前端 `stores/auth.ts` 的 `normalizeAvatar`
    正是按"站内路径"统一收纳的。`base_url` 参数保留，需要显式前缀时可传。
    """
    if user and user.avatar:
        user.avatar = f"{base_url}/avatars/{user.avatar}"
    return user


@router.post("", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED, summary="创建用户")
def create_user(user_in: UserCreate, db: Session = Depends(get_db)):
    """注册。成功后直接签发 token —— 前端注册完即视为已登录。"""
    if users_crud.get_user_by_name(db, user_name=user_in.user_name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已被占用")

    db_user = users_crud.create_user(db, user_in.user_name, user_in.password)
    return {
        "id": db_user.id,
        "user_name": db_user.user_name,
        "password": db_user.password,
        "avatar": db_user.avatar,
        "token": create_token(db_user.id),
    }


@router.get("/{user_id}", response_model=UserResponse, summary="根据ID获取指定用户")
def get_user_by_id(user_id: int, db: Session = Depends(get_db)):
    user = users_crud.get_user_by_id(db, user_id=user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return convert_to_accessible_user(user)


@router.put("/{user_id}", response_model=UserResponse, summary="修改用户信息")
def update_user(
    user_id: int,
    user_in: UserUpdate,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    user = users_crud.get_user_by_id(db, user_id=user_id)
    assert_owner(user, uid, "用户")          # 只能改自己的账号（含密码）

    if user_in.user_name and user_in.user_name != user.user_name:
        if users_crud.get_user_by_name(db, user_name=user_in.user_name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该用户名已被其他人占用，请更换",
            )

    updated_user = users_crud.update_user(
        db, user,
        user_name=user_in.user_name, password=user_in.password, avatar=user_in.avatar,
    )
    return convert_to_accessible_user(updated_user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除用户")
def delete_user(
    user_id: int,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    user = users_crud.get_user_by_id(db, user_id=user_id)
    assert_owner(user, uid, "用户")
    users_crud.delete_user(db, db_user=user)
    return None


@router.post("/login", response_model=LoginResponse, summary="用户登录校验")
def login_user(user_in: UserLogin, db: Session = Depends(get_db)):
    user = users_crud.authenticate_user(db, user_name=user_in.user_name, password=user_in.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    accessible_user = convert_to_accessible_user(user)
    return {
        "status": "success",
        "user_id": accessible_user.id,
        "user_name": accessible_user.user_name,
        "avatar": accessible_user.avatar,
        "token": create_token(accessible_user.id),
    }


@router.post("/{user_id}/upload-avatar", summary="上传/更换用户头像")
async def upload_avatar(
    user_id: int,
    file: UploadFile = File(...),
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    user = users_crud.get_user_by_id(db, user_id=user_id)
    assert_owner(user, uid, "用户")

    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只能上传图片文件")

    ext = os.path.splitext(file.filename)[1]
    avatar_filename = f"user_{user_id}_avatar{ext}"      # 防重名覆盖 + 防中文乱码
    file_save_path = os.path.join(IMAGES_DIR, avatar_filename)

    with open(file_save_path, "wb") as f:
        f.write(await file.read())

    users_crud.update_user(db, user, avatar=avatar_filename)

    return {
        "status": "success",
        "message": "头像上传成功",
        # 站内路径（不造主机名）：前端 normalizeAvatar 收纳后按当前站点解析
        "avatar_url": f"/avatars/{avatar_filename}",
    }
