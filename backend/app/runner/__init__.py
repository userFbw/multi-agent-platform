"""runner —— 生成项目的运行环境与进程管理（平台侧）

分工（与「确定性的交给平台，判断性的交给 Agent」一致）：
    env.py      子进程环境的唯一出口（**隔离**：绝不污染平台自己的 venv）
    venv.py     建项目 venv + 装 requirements（只用项目自己的 pip）
    process.py  起停 uvicorn、就绪探测、状态、日志（单实例、固定端口 8100）

不在这里做的事：
    · 不让 Agent 去起/维护常驻进程（无状态会话做不到可靠回收）；
    · Agent 只负责"生成可被启动的代码"（端口读 PORT、DB 写 $APP_DATA_DIR、输出 requirements.txt）
      以及写完自己 `bash .selftest.sh` 真跑一遍。
"""
