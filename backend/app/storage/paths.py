"""paths.py —— 全后端唯一的磁盘路径真相（storage 层的职责）

为什么单独放这里：这些是**文件/磁盘**关注点，不是数据库关注点。
之前它们写在 `app/db/engine.py` 里，导致 `api/routes/auth.py` 为了拿一个头像目录常量，
不得不 `from app.db.engine import IMAGES_DIR` —— 上层为了个路径反向依赖 DB 模块。

现在三个层的取用方式都干净了：
    db       → 只取 DB_FILE_PATH
    api      → 只取 IMAGES_DIR
    入口/工具 → 取 EXPORTS_DIR
"""
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]        # backend/
APP_DIR = BACKEND_DIR / "app"                            # backend/app
DATA_DIR = BACKEND_DIR / "data"                          # backend/data
IMAGES_DIR = DATA_DIR / "images"                         # backend/data/images
EXPORTS_DIR = BACKEND_DIR / "exports"                    # backend/exports
DB_FILE_PATH = DATA_DIR / "project.db"                   # backend/data/project.db

# 磁盘目录在 import 时就绪：SQLite 只会建库文件、不会建目录；
# `/avatars`、`/previews` 两个静态挂载点也要求目录存在，否则应用起不来。
for _d in (DATA_DIR, IMAGES_DIR, EXPORTS_DIR):
    os.makedirs(_d, exist_ok=True)
