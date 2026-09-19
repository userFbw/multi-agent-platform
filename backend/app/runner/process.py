"""runner.process —— 生成项目后端进程的起停与观测（单实例、固定端口）

设计要点：
    · **单实例**：已定"只分一个端口（8100）给客户"，所以同一时刻只允许一个应用在跑；
    · **状态不写数据库**：运行状态随平台进程生灭（写在 `backend/data/app_runtime.json`），
      用 `os.kill(pid, 0)` 实时校验存活 —— 避免"DB 说有、进程其实没了"的错位，也免迁移；
    · **账本按端口记账**（S2-1）：一个端口一行，停 A 项目不会把 B 项目的账抹掉；
      老格式（单槽）读进来会自动迁移；
    · **进程组**：`start_new_session=True` 起独立进程组，停止时杀整组（uvicorn 可能有子进程）；
    · **随平台退出**（S2-1）：子进程里 `PR_SET_PDEATHSIG=SIGTERM`，平台一死应用跟着死，
      结构上不再产生"孤儿应用占着端口"（真机踩到过：账本被清 + 进程逃逸 → 8100 永久卡死）；
    · **认领丢失账本的进程**（S2-1）：端口被占但账本为空/对不上时，扫 `/proc` 找
      "cmdline 里有 exports/u*/p*/.venv/bin/python" 的自有进程，能认领就认领，认不出来就
      明确让人去释放端口（而不是报一句自相矛盾的错）；
    · **环境隔离**：一律 `env.build_env()`（绝不继承平台的 PYTHONPATH / venv）；
    · **资源上限**：RLIMIT_CPU / AS / FSIZE，防止生成代码把整机拖死；
    · **日志**：stdout/stderr 直接写 `<项目>/应用日志/app.log`，UI 看得到真实报错。
"""
import ctypes
import json
import os
import re
import resource
import signal
import subprocess
import threading
import time
from pathlib import Path

from app.runner import env as renv
from app.runner import venv as rvenv

APP_LOG = "应用日志/app.log"
READY_TIMEOUT = 30
STOP_GRACE = 5

# 自有应用的"指纹"：cmdline 里的 `<...>/p<id>/.venv/bin/python -m uvicorn main:app`
# —— 这个组合是平台自己造出来的（项目 venv 的解释器 + 平台固定的启动命令），别的程序不会长这样。
#    不写死 exports 路径是为了让测试用临时目录也能覆盖到这条逻辑。
_APP_CMDLINE_RE = re.compile(r"(\S*/p(\d+))/\.venv/bin/python\s+-m\s+uvicorn\s+main:app")
_USER_IN_PATH_RE = re.compile(r"/u(\d+)/p\d+$")

# PR_SET_PDEATHSIG（linux/prctl.h）
_PR_SET_PDEATHSIG = 1


# ---------------------------------------------------------------------------
# 账本（按端口记一行；放平台数据区，不放项目目录）
# ---------------------------------------------------------------------------
# 测试可以把账本指向临时文件（见 set_runtime_file）：否则测试写/清的是**平台真实账本**，
# 会把平台正在跑的应用"弄丢"（实测踩到：跑测试期间平台的应用变成没人认领的孤儿占着 8100）。
_runtime_override: Path | None = None


def set_runtime_file(path: str | Path | None) -> None:
    """把账本文件指到别处（测试用；传 None 恢复默认）。也可用环境变量 DSH_APP_RUNTIME。"""
    global _runtime_override
    _runtime_override = Path(path) if path else None


def _runtime_file() -> Path:
    if _runtime_override is not None:
        return _runtime_override
    env = os.environ.get("DSH_APP_RUNTIME")
    if env:
        return Path(env)
    from app.storage.paths import DATA_DIR

    return Path(DATA_DIR) / "app_runtime.json"


def read_runtime() -> dict:
    """返回 {'apps': {port: entry}}；老格式（单槽）自动迁移成新格式。"""
    try:
        raw = json.loads(_runtime_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"apps": {}}
    if not isinstance(raw, dict):
        return {"apps": {}}
    if "apps" in raw and isinstance(raw["apps"], dict):
        return raw
    if raw.get("pid"):                       # 老格式：{"project_id":..,"pid":..,"port":..}
        port = str(raw.get("port") or renv.APP_PORT)
        return {"apps": {port: raw}}
    return {"apps": {}}


def write_runtime(state: dict | None) -> None:
    f = _runtime_file()
    apps = ((state or {}).get("apps") or {}) if state else {}
    if not apps:
        try:
            f.unlink()                       # 一条都没有 → 文件也删掉，不留假状态
        except OSError:
            pass
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"apps": apps}, ensure_ascii=False), encoding="utf-8")


def _entries() -> dict:
    return read_runtime()["apps"]


def _put(port: int | str, entry: dict) -> None:
    apps = _entries()
    apps[str(port)] = entry
    write_runtime({"apps": apps})


def _drop(port: int | str) -> None:
    apps = _entries()
    apps.pop(str(port), None)
    write_runtime({"apps": apps})


def _entry(project_id: int | None = None, port: int | None = None) -> dict | None:
    """取账本里的一行：给了 project_id 就按项目找，否则按端口找，都没有则返回唯一那行。"""
    apps = _entries()
    if port is not None and str(port) in apps:
        return apps[str(port)]
    if project_id is not None:
        return next((e for e in apps.values() if e.get("project_id") == project_id), None)
    return next(iter(apps.values()), None) if len(apps) == 1 else None


def _alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _alive_app(pid: int) -> bool:
    """账本里的 pid 是不是**我们那个应用**，而不只是"有个进程占着这个号"。

    为什么不能只看 `os.kill(pid, 0)`（真机踩到）：pid 会被复用 —— 平台重启后账本里的
    7 号进程早没了，而系统里可能又有个 7 号进程（完全无关）。只看存活就会：
      · 状态假报"运行中"（用户以为能打开，其实什么都没有）；
      · `stop()` 去 kill 一个**无辜的进程**（严重）。
    所以额外核一遍 cmdline 指纹（项目 venv 的 python + `-m uvicorn main:app`）。
    读不到 /proc（权限或命名空间差异）时按"存活"算 —— 宁可漏杀，不可误杀。
    """
    if not _alive(pid):
        return False
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
    except OSError:
        return True
    cmdline = raw.replace("\0", " ").strip()
    if not cmdline:                 # 僵尸进程：cmdline 为空
        return False
    return bool(_APP_CMDLINE_RE.search(cmdline))


def port_in_use(port: int = renv.APP_PORT, host: str = renv.APP_HOST) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


# ---------------------------------------------------------------------------
# 认领：扫 /proc 找回"我们自己的、但账本丢了的"应用进程（S2-1）
# ---------------------------------------------------------------------------
def scan_app_processes() -> list:
    """扫 /proc，找出所有"本平台起的生成应用"进程。

    判据是 cmdline 里的 `<...>/p<id>/.venv/bin/python -m uvicorn main:app`（平台自己造的
    项目 venv + 固定启动命令）。返回 [{pid, project_id, user_id, cmdline}]。

    注意：只能看见**同一 PID 命名空间**里的进程。跨实例逃逸的孤儿由 `_preexec` 的
    PDEATHSIG 从源头杜绝（见该函数注释）。
    """
    found = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return found
    for name in names:
        if not name.isdigit():
            continue
        try:
            cmdline = Path(f"/proc/{name}/cmdline").read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        cmdline = cmdline.replace("\0", " ").strip()
        m = _APP_CMDLINE_RE.search(cmdline)
        if not m:
            continue
        um = _USER_IN_PATH_RE.search(m.group(1))
        found.append({"pid": int(name), "project_id": int(m.group(2)),
                      "user_id": int(um.group(1)) if um else None,
                      "cmdline": cmdline})
    return found


def _kill_pid(pid: int, *, hard_after: float = STOP_GRACE) -> bool:
    """SIGTERM 进程组 → 宽限后 SIGKILL。返回是否真的杀掉了。"""
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    deadline = time.time() + hard_after
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    time.sleep(0.2)
    return not _alive(pid)


def _adopt(entry: dict) -> None:
    """把找回的进程写进账本（账本丢了但进程还活着时用）。"""
    _put(entry["port"], {"project_id": entry.get("project_id"), "pid": entry["pid"],
                         "port": entry["port"], "started_at": entry.get("started_at"),
                         "ready_seconds": entry.get("ready_seconds"),
                         "adopted": True})


# ---------------------------------------------------------------------------
# 观测
# ---------------------------------------------------------------------------
def app_log_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / APP_LOG


def logs(project_dir: str | Path, tail: int = 200) -> list:
    p = app_log_path(project_dir)
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-tail:]


def log_tail(project_dir: str | Path, n: int = 12) -> str:
    return "\n".join(logs(project_dir, tail=n))


def status(project_dir: str | Path | None = None, *, project_id: int | None = None,
           port: int = renv.APP_PORT) -> dict:
    """当前应用状态。

    `project_dir` 只用于取日志尾部（可选）；`project_id` 可显式指定（否则从目录名 p<id> 推）。
    账本里那一行如果进程已经没了 → 顺手清掉，不留假状态。

    ⚠️ 没传 `project_id` 时，以**账本里那一行**为准（单实例下最多一行）；从目录名 `p<id>`
    推出来的 id 只用来判断 `running_this_project`。顺序反了会出事：调用方若没传 id、
    而目录名又和账本对不上（测试用的临时目录就是这样），就会把正确的账目"认领"成错的。
    """
    want = project_id
    if want is None and project_dir is not None:
        m = re.search(r"\bp(\d+)$", str(project_dir).rstrip("/"))
        want = int(m.group(1)) if m else None

    entry = _entry(project_id=project_id) if project_id is not None else _entry()
    if entry:
        pid = int(entry.get("pid") or 0)
        if _alive_app(pid):          # 必须核身份：只看存活会被 pid 复用骗到（真机踩到）
            out = {
                "status": "running",
                "port": entry.get("port", port),
                "pid": pid,
                "project_id": entry.get("project_id"),
                "started_at": entry.get("started_at"),
                "ready_seconds": entry.get("ready_seconds"),
                "running_this_project": want is not None and entry.get("project_id") == want,
            }
            if project_dir is not None:
                out["log_tail"] = log_tail(project_dir)
            return out
        _drop(entry.get("port", port))        # 进程没了 → 清账

    # 账本没有（或已清）：端口被占的话，看看是不是我们自己的进程逃逸了 → 认领
    if want is not None and port_in_use(port):
        mine = [p for p in scan_app_processes() if _alive(p["pid"])]
        same = next((p for p in mine if p["project_id"] == want), None)
        if same:
            _adopt({"project_id": want, "pid": same["pid"], "port": port})
            out = {"status": "running", "port": port, "pid": same["pid"],
                   "project_id": want, "running_this_project": True,
                   "note": "账本丢失，已按进程认领"}
            if project_dir is not None:
                out["log_tail"] = log_tail(project_dir)
            return out
        other = next((p for p in mine if p["project_id"] != want), None)
        if other:
            return {"status": "occupied", "port": port, "pid": other["pid"],
                    "project_id": other["project_id"], "running_this_project": False,
                    "note": "端口被另一个项目占用"}
        return {"status": "occupied", "port": port, "project_id": None,
                "running_this_project": False,
                "note": f"端口 {port} 被非本平台进程占用"}

    if project_dir is not None:
        return {"status": "not_started", "port": port, "project_id": None,
                "has_backend": (Path(project_dir) / "src" / "backend" / "main.py").exists(),
                "log_tail": log_tail(project_dir)}
    return {"status": "not_started", "port": port, "project_id": None}


def app_status(project_dir: str | Path, *, project_id: int, port: int = renv.APP_PORT) -> dict:
    """给接口用的状态：在 status() 基础上补 `has_backend` / `needs_install`。"""
    out = status(project_dir, project_id=project_id, port=port)
    out["has_backend"] = (Path(project_dir) / "src" / "backend" / "main.py").exists()
    out["needs_install"] = rvenv.needs_install(project_dir) if out["has_backend"] else False
    return out


# ---------------------------------------------------------------------------
# 起 / 停
# ---------------------------------------------------------------------------
def _rlimits():  # pragma: no cover - 只在子进程里执行
    """子进程资源上限：CPU 300s / 地址空间 1G / 单文件 50M。"""
    resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
    resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (50 << 20, 50 << 20))


def _preexec():  # pragma: no cover - 只在子进程里执行
    """资源上限 + **随平台退出**（`PR_SET_PDEATHSIG`）。

    平台一死就给应用发 SIGTERM —— 这样"平台被 kill -9"也不会留下占着 8100 的孤儿（真机踩到过）。

    ⚠️ 它绑定的是**调用 Popen 的那个线程**：线程一退出，信号就发过来了。所以**只有从主线程
       启动时才用它**（见 `start()` 里的选择）：从线程池的工作线程启动的话，线程干完活就退了，
       会把应用一起带走（anyio 的线程池会不会回收空闲线程不保证，不能赌）。
       非主线程那条路靠"账本 + 启动时对账 + /proc 认领"兜底 —— 见本文件顶部设计说明。
    """
    _rlimits()
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
    except Exception:                         # noqa: BLE001 —— 设不上也不能影响启动
        pass


def stop(*, project_id: int | None = None, port: int | None = None, wait: bool = True) -> dict:
    """停应用（幂等）。默认停账本里那一个；给了 project_id/port 就只停对应的那个。"""
    entry = _entry(project_id=project_id, port=port)
    if not entry:
        return {"stopped": False, "note": "没有在跑的应用"}
    pid = int(entry.get("pid") or 0)
    _drop(entry.get("port", port))
    if not pid or not _alive_app(pid):
        return {"stopped": False, "pid": pid, "project_id": entry.get("project_id"),
                "note": "进程已不在（账本已清理）"}
    killed = _kill_pid(pid) if wait else True
    if not wait:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    return {"stopped": True, "pid": pid, "project_id": entry.get("project_id"),
            "killed": killed}


def stop_project(project_dir: str | Path, *, project_id: int | None = None) -> dict:
    """按项目停（删除项目 / 迭代前调用）。账本里没有但进程还在 → 也扫出来杀掉。"""
    if project_id is None:
        m = re.search(r"\bp(\d+)$", str(project_dir).rstrip("/"))
        project_id = int(m.group(1)) if m else None

    out = stop(project_id=project_id)
    # 兜底：账本丢了但进程还在（真机踩到过）→ 按 cmdline 指纹认出来杀掉
    strays = [p for p in scan_app_processes()
              if p["project_id"] == project_id and _alive(p["pid"])]
    for p in strays:
        _kill_pid(p["pid"])
    if strays:
        out = {"stopped": True, "pid": strays[0]["pid"], "project_id": project_id,
               "note": f"账本里没有，按进程指纹回收了 {len(strays)} 个"}
    return out


def start(project_dir: str | Path, *, project_id: int, port: int = renv.APP_PORT,
          force: bool = False, install: bool = True) -> dict:
    """启动生成项目：确保 venv → 装依赖 → 起 uvicorn → 等就绪。

    返回 {ok, status, port, pid, ready_seconds, message, log_tail}。任何一步失败都返回
    ok=False + **真实原因**（不抛异常，调用方好把它显示给用户）。
    """
    project_dir = Path(project_dir)
    backend_dir = project_dir / "src" / "backend"
    entry = backend_dir / "main.py"
    if not entry.exists():
        return {"ok": False, "status": "failed",
                "message": f"找不到后端入口 {entry}（生成的后端应把 main.py 放在 src/backend/）"}

    # ① 账本里已有应用在跑
    cur = status(project_dir, project_id=project_id, port=port)
    if cur["status"] == "running":
        if cur.get("project_id") == project_id:
            return {"ok": True, "status": "running", "port": cur["port"], "pid": cur["pid"],
                    "message": "应用已在运行"}
        if not force:
            return {"ok": False, "status": "occupied", "port": cur["port"],
                    "project_id": cur.get("project_id"),
                    "message": f"端口 {cur['port']} 已被项目 {cur.get('project_id')} 占用（应用单实例）。"
                               f"要切换请带 force=true，平台会先停掉它。"}
        stop(project_id=cur.get("project_id"), port=cur.get("port"))

    # ② 端口被占但没有（或对不上的）账本行 → 先试着认领/回收自有进程
    if port_in_use(port):
        strays = [p for p in scan_app_processes() if _alive(p["pid"])]
        mine = next((p for p in strays if p["project_id"] == project_id), None)
        if mine:                                    # 就是本项目逃逸的进程 → 认领，直接用
            _adopt({"project_id": project_id, "pid": mine["pid"], "port": port})
            return {"ok": True, "status": "running", "port": port, "pid": mine["pid"],
                    "message": "应用已在运行（账本丢失，已按进程认领）"}
        other = next((p for p in strays if p["project_id"] != project_id), None)
        if other and not force:
            return {"ok": False, "status": "occupied", "port": port,
                    "project_id": other["project_id"],
                    "message": f"端口 {port} 已被项目 {other['project_id']} 占用（应用单实例）。"
                               f"要切换请带 force=true，平台会先停掉它。"}
        if other and force:
            stop_project(project_dir, project_id=other["project_id"])
        elif port_in_use(port):
            return {"ok": False, "status": "failed", "port": port,
                    "message": f"端口 {port} 被**非本平台**的进程占用，请在服务器上执行 "
                               f"`fuser -k -n tcp {port}` 释放它后重试。"}

    # ③ venv + 依赖
    ok, why = rvenv.ensure_venv(project_dir)
    if not ok:
        return {"ok": False, "status": "failed", "message": why, "log_tail": rvenv._last_lines(why, 12)}
    if install and rvenv.needs_install(project_dir):
        ok, tail = rvenv.install_requirements(project_dir)
        if not ok:
            return {"ok": False, "status": "failed", "message": "依赖安装失败（详见应用日志）",
                    "log_tail": tail}

    # ④ 起进程（独立进程组；日志直接落文件；子进程随平台退出）
    log = app_log_path(project_dir)
    log.parent.mkdir(parents=True, exist_ok=True)
    # 运行期数据目录：平台先建好（应用只要按 APP_DATA_DIR 写就行，不必自己 mkdir）
    renv.data_dir(project_dir).mkdir(parents=True, exist_ok=True)
    f = open(log, "a", encoding="utf-8")
    f.write(f"\n=== 启动应用（{time.strftime('%Y-%m-%d %H:%M:%S')}，端口 {port}）===\n")
    f.flush()
    cmd = [str(renv.venv_python(project_dir)), "-m", "uvicorn", "main:app",
           "--host", renv.APP_HOST, "--port", str(port)]
    env = renv.build_env(project_dir, port=port)
    # ★ 主线程启动 → 带上 PDEATHSIG（平台死了应用跟着死）；工作线程启动 → 只设资源上限。
    #   为什么不在 preexec 里判断：preexec 跑在 fork 之后的子进程里，"当前线程是不是主线程"
    #   在那里已经失去意义（子进程只剩 forking 的那个线程），必须在**父进程**这边先判断好。
    preexec = _preexec if threading.current_thread() is threading.main_thread() else _rlimits
    started = time.time()
    try:
        proc = subprocess.Popen(cmd, cwd=str(backend_dir), env=env,
                                stdout=f, stderr=subprocess.STDOUT,
                                start_new_session=True, preexec_fn=preexec)
    except OSError as e:
        f.close()
        return {"ok": False, "status": "failed", "message": f"启动失败：{e}"}

    # ⑤ 等就绪（先 /health 再 /）
    ready = _wait_ready(port, READY_TIMEOUT)
    if not ready:
        stop_pid = proc.pid
        try:
            os.killpg(os.getpgid(stop_pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        f.write(f"[平台] {READY_TIMEOUT}s 内未就绪，已终止。\n")
        f.close()
        return {"ok": False, "status": "failed", "port": port,
                "message": f"应用启动后 {READY_TIMEOUT}s 内未就绪（已自动终止），请看日志",
                "log_tail": log_tail(project_dir, 15)}
    f.close()

    ready_seconds = round(time.time() - started, 1)
    _put(port, {"project_id": project_id, "pid": proc.pid, "port": port,
                "started_at": int(time.time()), "ready_seconds": ready_seconds})
    return {"ok": True, "status": "running", "port": port, "pid": proc.pid,
            "ready_seconds": ready_seconds, "message": f"应用已启动（{ready_seconds}s）"}


def autostart(project_dir: str | Path, *, project_id: int, port: int = renv.APP_PORT) -> dict:
    """开发链跑完自动起一次（S2-5）。

    **绝不抛异常、也不改项目状态** —— 自动启动失败只该记一笔，不该让"代码已生成好"这件事失败。
    没有后端入口（纯前端项目）就什么都不做，返回 skipped。
    """
    if not (Path(project_dir) / "src" / "backend" / "main.py").exists():
        return {"ok": False, "status": "skipped", "message": "纯前端项目，无需启动应用"}
    try:
        return start(project_dir, project_id=project_id, port=port)
    except Exception as e:                        # noqa: BLE001 —— 自动启动永不拖垮流程
        return {"ok": False, "status": "failed", "message": f"自动启动异常：{e}"}


def _wait_ready(port: int, timeout: int) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        for path in ("/health", "/"):
            try:
                with urllib.request.urlopen(f"http://{renv.APP_HOST}:{port}{path}", timeout=1) as r:
                    if r.status < 500:
                        return True
            except urllib.error.HTTPError as e:
                if e.code < 500:          # 404 也算"服务起来了"
                    return True
            except Exception:             # noqa: BLE001 —— 连不上就继续等
                pass
        time.sleep(0.4)
    return False


def cleanup_orphans() -> list:
    """平台启动时对账：账本里还活着的应用一律收掉（避免重启后残留）。

    返回被回收的 pid 列表。注意：**只能看见自己 PID 命名空间里的进程** —— 跨实例逃逸的
    孤儿靠 `_preexec` 的 PDEATHSIG 从源头上杜绝。
    """
    killed = []
    for port, entry in list(_entries().items()):
        pid = int(entry.get("pid") or 0)
        if pid and _alive_app(pid):
            _kill_pid(pid, hard_after=0.3)
            killed.append(pid)
        _drop(port)
    return killed
