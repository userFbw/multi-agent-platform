"""runner.env —— 生成项目的子进程环境（**隔离的唯一出口**）

⚠️ 这个文件存在的唯一理由：**保证生成项目的运行环境绝不污染服务器平台自己的运行环境**。

平台自己跑在 `/opt/multi agent/.venv`（或 new_venv）。生成的项目必须在**自己的 venv** 里
装依赖、跑服务。风险点有几个，全都在这一个文件里挡掉：

  ① 裸 `pip` / `python -m pip` 会落到系统或平台环境 → 本模块只产出"指向项目 venv"的环境，
     并且所有调用方都必须用 `<项目>/.venv/bin/python -m pip`（见 venv.py）。
  ② 平台运行时会带 `PYTHONPATH=backend`，子进程继承后可能 import 到平台的 `app.*` 包 → 这里清空。
  ③ `~/.local/lib`（user site）里的包会串进生成项目 → `PYTHONNOUSERSITE=1`。
  ④ pip 默认把缓存写 `~/.cache/pip`（实测已 24MB，且删项目不会清）→ `PIP_CACHE_DIR` 指到项目内。
  ⑤ 万一哪天有人手滑在非 venv 环境里跑 pip → `PIP_REQUIRE_VIRTUALENV=1` 直接拒绝（机制兜底）。
  ⑥ 平台的 pip 镜像配置在 `~/.pip/pip.conf`（user 级、取决于 HOME）。本模块故意把 HOME 指到
     项目内，于是 pip 读不到那份配置 → 回落到公网 PyPI → 这台机器公网不通 → **pip 一直重试到超时**
     （实测踩到：第一次跑 runner_smoke 卡满 120s）。所以这里把镜像配置**抄一份到项目内**并用
     `PIP_CONFIG_FILE` 显式指过去 —— 生成项目的 pip 永远走可达的镜像，且不读 /root 下的任何东西。

所以：**任何要起子进程的地方都必须用 `build_env()` 造环境，不许自己拼 os.environ。**
"""
import os
import sys
from pathlib import Path

# 生成的应用固定用的端口（已定：只分一个端口给客户，应用单实例）
APP_PORT = 8100

# ★ 生成应用真正监听的地址：必须 0.0.0.0，不能只绑 127.0.0.1。
#   原因（真机踩到）：平台自己的服务都用 `--host 0.0.0.0` 起，用户是通过服务器地址/代理
#   访问的；只绑回环的话，**本机 curl 一切正常、用户浏览器却打不开**（实测：页面一直空白加载）。
#   客户在浏览器里要能打开生成的应用，这里就必须对外监听。要锁回内网可设 DSH_APP_HOST=127.0.0.1。
APP_HOST = os.environ.get("DSH_APP_HOST", "0.0.0.0")

# ★ 客户该用什么**主机名**打开生成的应用（部署方声明，留空=由前端按浏览器地址自己判断）。
#
#   为什么需要它（真机踩到）：生成的应用跑在独立端口（8100），而用户可能用**隧道**访问平台
#   （浏览器地址是 `localhost:3000`，服务器侧的连接来自 127.0.0.1）。这时前端按
#   `location.hostname` 拼出来是 `http://localhost:8100/` —— 那是用户自己电脑的 8100，
#   直接"拒绝连接"。部署方（也就是我们）最清楚这台机器的对外地址，所以由它声明：
#       APP_PUBLIC_HOST=8.134.74.75
#   前端优先用这个；没配就退回 `location.hostname`（本机演示/隧道都配了端口时也对）。
def public_host() -> str:
    """每次现读环境变量（不缓存）—— 避免和 `load_dotenv()` 的加载顺序耦合。"""
    return (os.environ.get("APP_PUBLIC_HOST") or "").strip()

# 只保留这些"无害且必要"的宿主环境变量；其余一律不继承
_PASSTHROUGH = ("LANG", "LC_ALL", "TZ", "TERM")

# 系统 PATH 兜底（venv 的 bin 会放在最前）
_SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def venv_dir(project_dir: str | Path) -> Path:
    """项目的 venv 目录（唯一来源：平台算出来，生成代码无权改变）。"""
    return Path(project_dir) / ".venv"


def venv_python(project_dir: str | Path) -> Path:
    return venv_dir(project_dir) / "bin" / "python"


def data_dir(project_dir: str | Path) -> Path:
    """项目的**运行期数据目录**：项目根的 `data/`（与 src/ 同级，重跑不清）。

    ★ 为什么必须由平台规定、而不是让生成的代码自己猜路径：
      平台每轮重跑都会 `rmtree(<项目>/src)`（orchestrator）。生成的后端 main.py 在
      `src/backend/` 下，如果它按「main.py 的上级目录的 ../data」去算，算出来是
      `src/data` —— **正好在每轮都会被删的那棵树里**，用户数据下一轮迭代就没了。
      平台最清楚项目根在哪，所以直接把绝对路径用环境变量告诉应用（见 build_env 的
      `APP_DATA_DIR`），生成代码只管用；技能文本里同时写了"取不到环境变量就回退
      `parents[2]/data`"的兜底算法，双保险。
    """
    return Path(project_dir) / "data"


def venv_pip_argv(project_dir: str | Path, *args: str) -> list:
    """用**项目自己的 venv 解释器**跑 pip —— 绝不调用裸 pip / pip3。"""
    return [str(venv_python(project_dir)), "-m", "pip", *args]


# pip 自己的配置优先级（user 级那份就是平台在这台机器上配的镜像）
_PIP_CONFIG_CANDIDATES = (
    "/etc/pip.conf",
    "/etc/xdg/pip/pip.conf",
    "~/.pip/pip.conf",
    "~/.config/pip/pip.conf",
)

PROJECT_PIP_CONF = ".pip/pip.conf"


def _read_mirror() -> dict:
    """取平台当前生效的 pip 镜像（env 优先，其次配置文件）。"""
    import os.path as _p

    out = {}
    if os.environ.get("PIP_INDEX_URL"):
        out["index-url"] = os.environ["PIP_INDEX_URL"]
    if os.environ.get("PIP_TRUSTED_HOST"):
        out["trusted-host"] = os.environ["PIP_TRUSTED_HOST"]
    if out.get("index-url") and out.get("trusted-host"):
        return out
    for cand in _PIP_CONFIG_CANDIDATES:
        path = _p.expanduser(cand)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" not in line:
                continue
            k, v = (x.strip() for x in line.split("=", 1))
            if k in ("index-url", "trusted-host") and v and k not in out:
                out[k] = v
        if out.get("index-url"):
            break
    return out


def write_project_pip_conf(project_dir: str | Path) -> Path | None:
    """把镜像配置抄到 `<项目>/.pip/pip.conf`（幂等）。取不到镜像就返回 None。"""
    project_dir = Path(project_dir)
    mirror = _read_mirror()
    if not mirror.get("index-url"):
        return None
    conf = project_dir / PROJECT_PIP_CONF
    text = "[global]\nindex-url = {}\n".format(mirror["index-url"])
    if mirror.get("trusted-host"):
        text += "\n[install]\ntrusted-host = {}\n".format(mirror["trusted-host"])
    try:
        conf.parent.mkdir(parents=True, exist_ok=True)
        if not conf.exists() or conf.read_text(encoding="utf-8") != text:
            conf.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return conf


def build_env(project_dir: str | Path, *, port: int = APP_PORT, extra: dict | None = None) -> dict:
    """构造生成项目子进程的环境（建 venv、装依赖、跑服务**全都用这一份**）。

    返回的 dict 是**完整环境**（不是增量），调用方直接 `subprocess.run(..., env=build_env(...))`。
    """
    project_dir = Path(project_dir)
    vdir = venv_dir(project_dir)
    env = {
        # 只放项目 venv 的 bin + 系统目录：即使有裸 `pip`/`python` 调用也不会命中平台 venv
        "PATH": f"{vdir / 'bin'}:{_SYSTEM_PATH}",
        "HOME": str(project_dir / ".home"),          # 让任何 ~ 相关写入都落在项目内
        # ★ 清空 PYTHONPATH：平台自己带着 PYTHONPATH=backend，继承下去生成项目可能 import 到平台包
        "PYTHONPATH": "",
        "PYTHONNOUSERSITE": "1",                     # 不读 ~/.local/lib
        "VIRTUAL_ENV": str(vdir),
        # pip：缓存落项目内；不在 venv 里直接拒绝；不检查版本（省一次网络往返）
        "PIP_CACHE_DIR": str(project_dir / ".pip-cache"),
        "PIP_REQUIRE_VIRTUALENV": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        # 生成的后端按约定从 PORT 读端口（默认 8000 只是兜底）
        "PORT": str(port),
        "APP_HOST": APP_HOST,
        # ★ 运行期数据目录：绝对路径，指向项目根 data/（不在每轮被清空的 src/ 里）
        "APP_DATA_DIR": str(data_dir(project_dir)),
    }
    conf = write_project_pip_conf(project_dir)
    if conf is not None:
        env["PIP_CONFIG_FILE"] = str(conf)      # ★ 显式指向项目内的镜像配置（不依赖 HOME）
    for k in _PASSTHROUGH:
        if os.environ.get(k):
            env[k] = os.environ[k]
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


def env_report(project_dir: str | Path, *, port: int = APP_PORT) -> dict:
    """给测试/排查用：把隔离相关的关键项列出来（不含值里的路径细节）。"""
    env = build_env(project_dir, port=port)
    return {
        "path_first": env["PATH"].split(":")[0],
        "pythonpath_empty": env["PYTHONPATH"] == "",
        "no_user_site": env["PYTHONNOUSERSITE"] == "1",
        "pip_require_venv": env["PIP_REQUIRE_VIRTUALENV"] == "1",
        "pip_cache_inside_project": str(Path(project_dir)) in env["PIP_CACHE_DIR"],
        "pip_conf_inside_project": env.get("PIP_CONFIG_FILE", "").startswith(str(Path(project_dir))),
        "virtual_env": env["VIRTUAL_ENV"],
        "port": env["PORT"],
        "data_dir": env["APP_DATA_DIR"],
        "data_dir_at_project_root": env["APP_DATA_DIR"] == str(data_dir(project_dir)),
        "data_dir_outside_src": not env["APP_DATA_DIR"].startswith(str(Path(project_dir) / "src")),
    }


def assert_platform_untouched(before: dict, after: dict, what: str = "生成项目") -> None:
    """装依赖前后对比平台 venv 快照，不一致就直接抛错（宁可炸也不要悄悄污染）。

    before/after 由 `snapshot_site_packages()` 产出。
    """
    if before != after:
        raise RuntimeError(
            f"检测到{what}的依赖安装污染了平台运行环境！\n"
            f"  安装前：{before}\n  安装后：{after}\n"
            f"（这属于严重问题：生成项目必须只写自己的 .venv，见 app/runner/env.py）"
        )


def snapshot_site_packages(python_exe: str | Path | None = None) -> dict:
    """平台环境的快照：site-packages 下的文件数/总大小 + 顶层包名，用于"零污染"断言。"""
    exe = str(python_exe or sys.executable)
    import subprocess

    code = (
        "import json, site, sys, os;"
        "paths = list(site.getsitepackages()) + [site.getusersitepackages()];"
        "out = {};"
        "[(out.__setitem__(p, [sum(len(f) for _, _, f in os.walk(p)) if os.path.isdir(p) else 0,"
        " os.path.getsize(p) if os.path.isfile(p) else 0]) ) for p in paths];"
        "print(json.dumps({'exe': sys.executable, 'paths': out}, sort_keys=True))"
    )
    r = subprocess.run([exe, "-c", code], capture_output=True, text=True)
    return {"raw": (r.stdout or r.stderr).strip()}
