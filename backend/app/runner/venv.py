"""runner.venv —— 项目专属 venv 与依赖安装

硬规则（见 env.py 的说明）：**只用项目自己的 venv 与 pip**，绝不碰平台环境。
   · 建 venv：用平台解释器 `-m venv`（只创建，不安装），venv 自带 pip；
   · 装依赖：`<项目>/.venv/bin/python -m pip install -r requirements.txt`；
   · 缓存：`PIP_CACHE_DIR=<项目>/.pip-cache`（删项目即消失）；
   · 失败原因（真实 pip 输出）写 `应用日志/install.log`，供 UI 显示。
"""
import hashlib
import os
import subprocess
import time
from pathlib import Path

from app.runner import env as renv

INSTALL_LOG = "应用日志/install.log"
REQ_REL = "src/backend/requirements.txt"
# 依赖装好后的标记（内容 = requirements 的哈希）：变了才重装，避免每次启动都等 pip
MARKER = ".venv/.deps-installed"
INSTALL_TIMEOUT = 120


def log_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / INSTALL_LOG


def ensure_venv(project_dir: str | Path) -> tuple[bool, str]:
    """确保 venv 存在。返回 (ok, 说明)。"""
    project_dir = Path(project_dir)
    vdir = renv.venv_dir(project_dir)
    if renv.venv_python(project_dir).exists():
        return True, "venv 已存在"
    log = log_path(project_dir)
    log.parent.mkdir(parents=True, exist_ok=True)
    # 用**平台解释器**建 venv：这一步只创建目录结构，不会往平台环境里装任何东西
    import sys

    r = subprocess.run([sys.executable, "-m", "venv", str(vdir)],
                       capture_output=True, text=True, timeout=120)
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n=== 建 venv（{time.strftime('%Y-%m-%d %H:%M:%S')}）===\n{r.stdout}{r.stderr}")
    if not renv.venv_python(project_dir).exists():
        return False, f"建 venv 失败：{(r.stderr or r.stdout or '').strip()[-500:]}"
    return True, "venv 已创建"


def _req_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def requirements_file(project_dir: str | Path) -> Path:
    return Path(project_dir) / REQ_REL


def needs_install(project_dir: str | Path) -> bool:
    """依赖需要装吗：没装过 或 requirements.txt 变了。"""
    project_dir = Path(project_dir)
    req = requirements_file(project_dir)
    if not req.exists():
        return False        # 没声明依赖 → 用 venv 自带的即可
    marker = project_dir / MARKER
    return not marker.exists() or marker.read_text(encoding="utf-8").strip() != _req_hash(req)


def install_requirements(project_dir: str | Path, *, timeout: int = INSTALL_TIMEOUT) -> tuple[bool, str]:
    """装依赖。返回 (ok, 日志尾部)。**只用项目自己的 venv pip**。"""
    project_dir = Path(project_dir)
    req = requirements_file(project_dir)
    if not req.exists():
        return True, "没有 requirements.txt（跳过装依赖）"

    log = log_path(project_dir)
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = renv.venv_pip_argv(project_dir, "install", "-r", str(req))
    started = time.time()
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n=== 安装依赖（{time.strftime('%Y-%m-%d %H:%M:%S')}）\n$ {' '.join(cmd)}\n")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=renv.build_env(project_dir), cwd=str(project_dir))
        out = f"{r.stdout}\n{r.stderr}".strip()
        ok = r.returncode == 0
    except subprocess.TimeoutExpired as e:
        out = f"安装超时（>{timeout}s）：{e}"
        ok = False
    with open(log, "a", encoding="utf-8") as f:
        f.write(out[-8000:] + "\n")
    if ok:
        (project_dir / MARKER).write_text(_req_hash(req), encoding="utf-8")
        tail = f"依赖安装完成（{time.time() - started:.1f}s）"
    else:
        tail = _last_lines(out, 12)
    return ok, tail


def _last_lines(text: str, n: int = 12) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(lines[-n:])
