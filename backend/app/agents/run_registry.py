"""run_registry —— 「谁在跑」的注册表 + 强制终止（客户按按钮停自己的项目）

为什么需要它：AI 会话是 `ai_client` 内部 spawn 的子进程，外面（API 层）根本拿不到 pid，
客户在页面上点「终止运行」时无从下手。所以起会话的地方登记一下，终止时按项目号找出来杀。

三条设计要点：

  ① **按项目号存一组**（不是单个）：架构步之后「后端 ∥ 前端」是并发的，同一项目会同时有两个
     会话在跑，终止要一起杀。

  ② **杀整个进程组**（`os.killpg`）：会话是 `start_new_session=True` 起的，Agent 自己跑的命令
     （`.selftest.sh`、它起的自测服务、pip）都在这个组里。只杀直接子进程 = 假终止：界面显示停了，
     自测服务还在后台跑。

  ③ **中止标志**：杀了当前会话不等于流程停住 —— 引擎会接着调度下一个节点、又起一个新会话。
     所以 `abort()` 记一个标志，引擎在**每个波次开始前**检查它，看到就收摊。
     ⚠️ 标志必须在下次开跑前清掉（`clear_abort`），否则客户点一次终止，**之后每次重跑都会被
     立刻再次终止**（这条有专门的回归测试锁着）。

不持久化：平台是单进程，注册表活在内存里就够；平台重启后本来就没有"在跑的会话"了
（生成的应用进程另有账本，见 app/runner/process.py）。
"""
import os
import signal
import threading
import time

# 终止时的宽限：先 SIGTERM 让子进程有机会收尾（落盘、关连接），再 SIGKILL
STOP_GRACE = 5.0


class RunAborted(Exception):
    """用户手动终止。**不是失败** —— 上层据此把项目置为「已终止」而不是「失败」。"""


_lock = threading.Lock()
_runs: dict = {}          # project_id -> [ {pid, pgid, step_no, name, started_at} ]
_aborted: set = set()     # project_id 集合：被用户终止过（等引擎看到后由上层清理）


# ---------------------------------------------------------------------------
# 登记 / 注销
# ---------------------------------------------------------------------------
def _key(project_id) -> int:
    return int(project_id)


def register(project_id, pid: int, *, step_no=None, name: str = "",
             started_at: float | None = None) -> dict:
    """登记一个正在跑的会话进程（`ai_client` 起完子进程就调）。"""
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = pid
    entry = {"pid": int(pid), "pgid": int(pgid), "step_no": step_no, "name": name,
             "started_at": started_at if started_at is not None else time.time()}
    with _lock:
        _runs.setdefault(_key(project_id), []).append(entry)
    return entry


def unregister(project_id, pid: int | None = None) -> None:
    """注销（会话结束：成功/失败/超时都要调，别漏）。pid 为空=清空该项目的全部。"""
    with _lock:
        k = _key(project_id)
        if pid is None:
            _runs.pop(k, None)
            return
        left = [e for e in _runs.get(k, []) if e.get("pid") != int(pid)]
        if left:
            _runs[k] = left
        else:
            _runs.pop(k, None)


def current(project_id=None) -> list:
    """正在跑的会话：给项目号就返回它的列表，不给就返回 {项目号: [...]} 全量。"""
    with _lock:
        if project_id is None:
            return {k: list(v) for k, v in _runs.items()}
        return list(_runs.get(_key(project_id), []))


def running_projects() -> list:
    with _lock:
        return sorted(_runs.keys())


# ---------------------------------------------------------------------------
# 终止
# ---------------------------------------------------------------------------
def _kill_group(pgid: int, pid: int, grace: float = STOP_GRACE) -> bool:
    """先 SIGTERM 整组，宽限后 SIGKILL。返回是否真的杀掉了。"""
    for sig in (signal.SIGTERM,):
        try:
            os.killpg(pgid, sig)
        except (OSError, ProcessLookupError):
            pass
    deadline = time.time() + grace
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    time.sleep(0.2)
    return not _alive(pid)


def _alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def abort(project_id, *, grace: float = STOP_GRACE) -> dict:
    """强制终止：杀掉这个项目**当前所有**在跑的会话进程组。

    幂等：没有在跑也返回（aborted=False + 人话说明），并**照样记中止标志** ——
    因为"刚好跑完"的瞬间点终止，也必须拦住下一步。
    """
    k = _key(project_id)
    with _lock:
        entries = list(_runs.get(k, []))
        _aborted.add(k)

    killed, names = [], []
    for e in entries:
        if _kill_group(e["pgid"], e["pid"], grace=grace):
            killed.append(e["pid"])
        names.append(e.get("name") or f"步骤{e.get('step_no')}")

    with _lock:
        _runs.pop(k, None)

    elapsed = None
    if entries:
        first = min(e.get("started_at") or time.time() for e in entries)
        elapsed = round(time.time() - first, 1)
    if killed:
        step = "、".join(dict.fromkeys(n for n in names if n))
        return {"aborted": True, "killed": killed, "step": step, "elapsed": elapsed,
                "message": f"已终止「{step}」" + (f"（已运行 {_fmt(elapsed)}）" if elapsed else "")}
    return {"aborted": False, "killed": [], "step": "", "elapsed": None,
            "message": "当前没有正在执行的步骤（已记下终止意图，后续步骤不会再启动）"}


def _fmt(sec: float | None) -> str:
    if not sec:
        return ""
    m, s = divmod(int(sec), 60)
    return f"{m}分{s}秒" if m else f"{s}秒"


# ---------------------------------------------------------------------------
# 中止标志（引擎的检查点用）
# ---------------------------------------------------------------------------
def is_aborted(project_id) -> bool:
    with _lock:
        return _key(project_id) in _aborted


def clear_abort(project_id) -> None:
    """清标志。**开跑前必须调**，否则上次的终止意图会把这次也一起掐掉。"""
    with _lock:
        _aborted.discard(_key(project_id))


def snapshot(project_id) -> dict:
    """给接口/日志用的一眼可读状态。"""
    cur = current(project_id)
    return {
        "running": bool(cur),
        "steps": [{"pid": e["pid"], "step_no": e.get("step_no"), "name": e.get("name"),
                   "elapsed": round(time.time() - (e.get("started_at") or time.time()), 1)}
                  for e in cur],
        "aborted_flag": is_aborted(project_id),
    }


def reset() -> None:
    """测试用：清空注册表（不动 abort 标志 → 用 clear_abort 单独清）。"""
    with _lock:
        _runs.clear()
        _aborted.clear()
