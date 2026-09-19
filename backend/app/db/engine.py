from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 磁盘路径常量归 storage 层（本模块只关心数据库）。
# 导入它同时保证 backend/data 目录已就绪 —— SQLite 只建库文件、不建目录。
from app.storage.paths import DB_FILE_PATH

DATABASE_URL = f"sqlite:///{DB_FILE_PATH}"

# 创建引擎与 Session
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# SQLite 外键约束按"连接"生效：让连接池里每个新连接都自动开启，
# 保证 ON DELETE CASCADE 级联始终可靠（替代仅启动时手动 PRAGMA 一次的做法）
from sqlalchemy import event  # noqa: E402


@event.listens_for(engine, "connect")
def _sqlite_enable_foreign_keys(dbapi_conn, _record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    """依赖注入：获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()