"""后端配置：从 .env / 环境变量读取。

注意：数据库路径不在这里配置 —— engine.py 用绝对路径定位 backend/data/project.db。
"""
import os

from dotenv import load_dotenv

load_dotenv()

# 开发默认密钥：仅用于本地跑通，部署时必须用环境变量覆盖
_DEV_SECRET = "dev-insecure-secret-please-override-in-env"


class Settings:
    # DSH（DeepSeek Harness）—— 唯一 AI 调用通道
    DSH_CMD: str = os.getenv("DSH_CMD", "dsh")                 # dsh 可执行文件 / 绝对路径
    DSH_PROFILE: str = os.getenv("DSH_PROFILE", "headless")    # 会话 profile
    DSH_TIMEOUT_SECONDS: int = int(os.getenv("DSH_TIMEOUT_SECONDS", "900"))
    DSH_SKILLS_DIR: str = os.getenv("DSH_SKILLS_DIR", "")      # 空 = 自动探测仓库根 .dsh/skills

    # 身份凭证（token）
    SECRET_KEY: str = os.getenv("SECRET_KEY", _DEV_SECRET)     # 签名密钥，泄露 = token 可伪造
    TOKEN_TTL_SECONDS: int = int(os.getenv("TOKEN_TTL_SECONDS", str(7 * 24 * 3600)))  # 默认 7 天
    # 是否强制请求带身份：False = 兼容放行（前端尚未接入 token）
    AUTH_REQUIRE_IDENTITY: bool = os.getenv("AUTH_REQUIRE_IDENTITY", "false").lower() == "true"


settings = Settings()
