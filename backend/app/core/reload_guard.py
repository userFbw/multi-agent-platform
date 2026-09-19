"""启动方式守卫：**不能带 `--reload` 跑**（S1 运维说明的第 1 条，真机又踩了一次）。

为什么：生成项目会往 `backend/exports/` 里写几百个文件（每个项目还带一个 738 个 .py 的 venv），
uvicorn 的 `--reload` 监视工作目录 → 每次写文件都触发重载 → 重载会**杀掉承载任务的进程**，
结果就是：任务写到一半终止、界面点了没反应（请求落在重载窗口里）、服务器短暂不响应。

怎么修：
    · 去掉 `--reload`（推荐，演示/正式都该这样）；或
    · 保留 `--reload` 但排除产物目录：`--reload-exclude "exports" --reload-exclude "*.pyc"`
"""
import os
from pathlib import Path

# 探测结果（启动时设置一次）
RELOAD_DETECTED = False


def _argv_of(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
    except OSError:
        return ""
    return raw.replace("\0", " ").strip()


def detect() -> bool:
    """当前进程（或其父进程）的启动参数里有没有 `--reload`。

    `uvicorn --reload` 会 fork 一个**子进程**跑应用，命令行参数留在父进程上；
    所以既要看自己，也要看父进程。
    """
    global RELOAD_DETECTED
    hits = []
    import sys

    hits.append(" ".join(sys.argv))
    try:
        hits.append(_argv_of(os.getppid()))
        # 也看看父进程的父进程（有些启动方式会再套一层）
        ppid = int(Path(f"/proc/{os.getppid()}/stat").read_text().split()[3])
        if ppid > 1:
            hits.append(_argv_of(ppid))
    except (OSError, ValueError, IndexError):
        pass
    RELOAD_DETECTED = any("--reload" in h for h in hits if h)
    return RELOAD_DETECTED


def banner() -> None:
    if not RELOAD_DETECTED:
        return
    line = "=" * 78
    print(f"\n{line}\n"
          "⚠️  检测到平台是以 `--reload` 启动的 —— 这会让**长任务被重载打断**！\n"
          "    生成项目会往 backend/exports/ 写几百个文件（还带一个 738 个 .py 的 venv），\n"
          "    每次写文件都会触发 watchfiles 重载 → 重载会杀掉正在跑任务的进程，\n"
          "    表现为：项目写到一半终止 / 点了没反应 / 服务器短期不响应。\n\n"
          "    修复：去掉 --reload；确实需要热重载就排除产物目录：\n"
          "      uvicorn main:app --host 0.0.0.0 --port 8000 \\\n"
          "        --reload --reload-exclude \"exports\" --reload-exclude \"*.pyc\"\n"
          f"{line}\n", flush=True)


def assert_reload_safe() -> None:
    """在"启动新任务"的入口调用：reload 模式下直接拒绝（免得白烧额度、留下半成品项目）。"""
    if not RELOAD_DETECTED:
        return
    if os.getenv("DSH_ALLOW_RELOAD", "").lower() in ("1", "true", "yes"):
        return
    from fastapi import HTTPException

    raise HTTPException(
        status_code=400,
        detail="平台当前以 --reload 启动，长任务会被热重载打断（项目会跑到一半终止）。"
               "请去掉 --reload 重启后端；确实需要热重载就先加 `--reload-exclude exports`。"
               "（坚持要在 reload 下跑，可设环境变量 DSH_ALLOW_RELOAD=true）")
