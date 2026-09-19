"""
ai_client.py —— 统一 AI 调用封装（Harness / AI 组负责，阶段一主改造点）

设计定位（与《项目架构.md》的分工一致）：
    · orchestrator（后端组）只编排流程，**所有"调 AI、解析输出"都经由本文件**；
    · 本文件 = 把已在 pipeline_runner.py / harness_test.py 验证过的 DSH 模式收进后端：
          skill 正文(SKILL.md)  +  {{input.xxx}} 占位符填充  +  可选的 system_prompt / 执行要求
          → 在产物工作目录里拉起 `dsh --profile headless "<prompt>"`
          → 捕获 stdout → 过滤 <think> → 按技能契约校验/解析 → 返回 会话号 + 耗时 + 产物
    · 每次调用 = 实例化一个“专职子 Agent”（DSH 会话天然无状态，按需拉起）。

技能输出契约（决定如何解析/校验返回文本）—— 四层优先级链，详见《backend/W6-输出契约数据化方案.md》§二：
    · ① run 级   ：调用方/编排节点显式 output_kind（最高）
    · ② agent 级 ：agents 落库值（用户自定义 agent 的旋钮；通道就绪后由编排层传入）
    · ③ skill 级 ：各 SKILL.md frontmatter 的 output[].contract（技能自己声明天然产出什么）
    · ④ 兜底     ：text
    闭集：json_object / json_array / file_blocks / text（见 OUTPUT_KINDS，唯一真相）。
    ⇒ 新增技能只要在自己的 SKILL.md 里声明 contract，**不必改本文件**；
      `SKILL_CONTRACTS` 仅为未补 frontmatter 的技能保留旧行为，属兼容兜底、非真相源。

对外只导出 `ai_client`（DshClient 全局单例），编排层统一调用它。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.core.config import settings
from app.agents import run_registry

# =====================================================================
# 1. 文本工具（与 pipeline_runner.py 同源，独立实现、不进后端依赖）
# =====================================================================
def strip_frontmatter(text: str) -> str:
    """去掉 SKILL.md 顶部 --- frontmatter --- 段，只留正文。"""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].lstrip("\n")
    return text


def _scalar(v: str):
    """frontmatter 标量取值：去包裹引号；true/false → bool。

    注意：**不剥离行内注释**。description 里合法地会出现 " # "（如 "… / # File: 代码块"），
    一律剥离会截断正文；需要纯净值的调用方（如 contract / required）自行取首词元即可。
    """
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    return v


def parse_frontmatter(text: str) -> dict:
    """极简 frontmatter 解析（**手写实现，不引 yaml 依赖** —— 与 strip_frontmatter 同源约定）。

    只支持本仓库 SKILL.md 实际用到的最小子集：
        key: 标量                    → {"key": "标量"}
        key:                         → {"key": [ {…}, … ]}
          - name: x
            type: string
    规则与容错：
      - 只解析文件顶部 --- … --- 段；无 frontmatter / 未闭合 / 文件缺失 → {}；
      - 用缩进区分「列表项的子键」与「新的顶层键」；
      - 任何异常片段静默跳过，**绝不抛错**：契约解析不出来只该降级为 text，
        不该让整个 agent 跑不起来（真正必须报错的场景由 parse_output 负责）。
    """
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].strip().lstrip("\ufeff") != "---":
        return {}
    body, closed = [], False
    for ln in lines[1:]:
        if ln.strip() == "---":
            closed = True
            break
        body.append(ln)
    if not closed:
        return {}

    def _indent(s: str) -> int:
        return len(s) - len(s.lstrip(" "))

    data: dict = {}
    cur_key, cur_item, cur_item_indent = None, None, -1
    for raw in body:
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent, s = _indent(raw), raw.strip()

        # 列表项：起一个新的 dict（如 output: 下的 "- name: xxx"）
        if s.startswith("-"):
            rest = s[1:].strip()
            if cur_key is None:
                continue                                  # 无归属列表项，跳过
            if not isinstance(data.get(cur_key), list):
                data[cur_key] = []
            cur_item, cur_item_indent = {}, indent
            data[cur_key].append(cur_item)
            if rest:
                k, sep, v = rest.partition(":")
                if sep:
                    cur_item[k.strip()] = _scalar(v)
            continue

        k, sep, v = s.partition(":")
        if not sep:
            continue
        k, v = k.strip(), v.strip()

        # 比列表项缩进更深 → 归属该列表项；否则是新的顶层键（列表到此结束）
        if cur_item is not None and indent > cur_item_indent:
            cur_item[k] = _scalar(v) if v else None
            continue
        cur_item, cur_item_indent, cur_key = None, -1, (k if not v else None)
        data[k] = _scalar(v) if v else None
    return data


# 输出契约闭集（W6 §二）：ai_client 只认这四种 kind，其余一律降级为 text。
# 后端组做 agents 级白名单校验时**应直接导入本常量**，避免出现第五份字面量副本。
OUTPUT_KINDS = ("json_object", "json_array", "file_blocks", "text")


# 占位符语法：{{input.xxx}} / {{output.xxx}} / {{xxx}}
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(?:(?:input|output)\.)?([\w-]+)\s*\}\}")


def fill_placeholders(text: str, mapping: dict) -> str:
    """填充 {{input.xxx}} / {{output.xxx}} / {{xxx}} 占位符。缺 key 直接报错，避免静默漏填。"""
    def _rep(m: re.Match) -> str:
        key = m.group(1)
        if key not in mapping:
            raise KeyError(f"占位符 {{{{ {key} }}}} 缺少对应输入值（可选填: {list(mapping)}）")
        return str(mapping[key])

    return PLACEHOLDER_PATTERN.sub(lambda m: _rep(m), text)


def render_inputs_block(mapping: dict) -> str:
    """把 inputs 渲染成「本次任务输入」文本块（供 build_prompt 在占位符缺位时兜底，见 B5）。"""
    chunks = []
    for k, v in mapping.items():
        chunks.append(f"### {k}\n{'' if v is None else str(v)}")
    return "\n\n".join(chunks)


def clean_think(text: str) -> str:
    """滤除模型可能输出的 <think>...</think> 思考段。"""
    return re.sub(r"<think>.*?</think>\s*", "", text or "", flags=re.DOTALL)


def extract_json(text: str) -> Any:
    """提取文本中的 JSON 对象（容忍 ```json 包裹或前后废话）。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("输出中未找到 JSON 对象")
    return json.loads(t[start:end + 1])


def parse_json_array(text: str) -> Optional[list]:
    """解析 simple-frontend 要求的 JSON 数组 → list。解析失败返回 None。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        arr = json.loads(t[start:end + 1])
    except json.JSONDecodeError:
        return None
    return arr if isinstance(arr, list) else None


def extract_file_blocks(text: str) -> dict:
    """
    解析 “# File: 路径” + ```语言\n代码\n``` → {路径: 源码}
    （backend/frontend-executor 技能规定的输出格式）
    """
    blocks: dict = {}
    lines = (text or "").splitlines()
    i, n = 0, len(lines)
    while i < n:
        m = re.match(r"^#\s*File:\s*(.+?)\s*$", lines[i])
        if m:
            path = m.group(1).strip().strip("`").strip()
            j = i + 1
            while j < n and not lines[j].startswith("```"):
                j += 1
            if j < n:
                content, j = [], j + 1
                while j < n and not lines[j].startswith("```"):
                    content.append(lines[j])
                    j += 1
                if content:
                    blocks[path] = "\n".join(content) + "\n"
                i = j + 1
                continue
        i += 1
    return blocks


def list_files(root: Path) -> list:
    """列出目录下真实文件（排除缓存/依赖目录），用于统计 agent 落盘产物。

    ⚠️ **必须排掉会话捕获文件**（`<session>.stdout` / `<session>.stderr`）：它们只是子进程管道的
    落地文件，不是这一步的产物。真机踩到过：它们被当成产物复制进 `src/`，于是
    ① 交付 ZIP 里混进几万行会话日志（p47 实测 4 个文件 480KB），客户解压看到的是日志当源码；
    ② `_combine_src` 把它们当"源码"喂给下游 Agent → 上下文膨胀、烧额度、干扰判断。
    工作目录用完即 `rmtree`，所以这里不列它们就够了；诊断能力不丢（stderr 已并入返回的 log 字段）。
    """
    if not root.exists():
        return []
    skip = ("__pycache__", ".git", "node_modules")
    capture = (".stdout", ".stderr")          # 会话捕获文件 → 不算产物
    return [
        str(p.relative_to(root))
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and not p.name.endswith(capture)
        and not any(s in p.relative_to(root).parts for s in skip)
    ]


# =====================================================================
# 2. DSH 配置解析（env > 默认值）
# =====================================================================
def _resolve_skills_dir() -> Path:
    """技能目录解析优先级：settings.DSH_SKILLS_DIR → 仓库根 .dsh/skills → cwd/.dsh/skills"""
    if getattr(settings, "DSH_SKILLS_DIR", ""):
        p = Path(settings.DSH_SKILLS_DIR)
        if p.is_dir():
            return p
    here = Path(__file__).resolve()
    repo_root = here.parents[3]                       # backend/app/agents → 仓库根
    for cand in (repo_root / ".dsh" / "skills", Path.cwd() / ".dsh" / "skills"):
        if cand.is_dir():
            return cand
    return repo_root / ".dsh" / "skills"


def read_skill_frontmatter(skill_id: str, skills_dir: Optional[Path] = None) -> dict:
    """读取技能 SKILL.md 的 frontmatter；技能目录/文件不存在 → {}（不抛错）。"""
    if not skill_id:
        return {}
    p = (skills_dir or _resolve_skills_dir()) / skill_id / "SKILL.md"
    try:
        return parse_frontmatter(p.read_text(encoding="utf-8"))
    except OSError:
        return {}


def read_skill_contract(skill_id: str, skills_dir: Optional[Path] = None) -> str:
    """读取技能声明的输出契约（frontmatter `output[].contract`）；未声明/非法 → ""。

    只认 OUTPUT_KINDS 闭集，且只取首词元 —— 因此行内注释
    （如 `contract: json_array  # 说明`）与多余空格都不影响解析。
    """
    outputs = read_skill_frontmatter(skill_id, skills_dir).get("output")
    if not isinstance(outputs, list):
        return ""
    for item in outputs:
        if not isinstance(item, dict):
            continue
        raw = item.get("contract")
        if raw is None:
            continue
        tokens = str(raw).split()
        if tokens and tokens[0] in OUTPUT_KINDS:
            return tokens[0]
    return ""


# =====================================================================
# 3. DSH Client —— 统一 AI 调用封装（新主路径）
# =====================================================================
class DshError(RuntimeError):
    """DSH 调用/契约层错误。

    code 非空 = 可上报的配置类错误（取值见 ErrorCode），`run_agent` 会把它转成正常的失败返回，
    好让步骤行上留下 error_code/error；因此这类错误不会抛给调用方。
    code 为空 = 需要调用方自己处理的异常。
    """

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


# =====================================================================
# 统一错误码（实施方案 §五-7「错误反馈格式与重试策略」定稿）
#   约定：ai_client 返回里 error = 可读文案（给人看），error_code = 稳定机器码（给程序看），
#         二者同时出现；编排层/前端据 error_code 分支、据 error 展示。
# =====================================================================
class ErrorCode:
    DSH_NOT_FOUND = "DSH_NOT_FOUND"            # dsh 命令不存在（环境未就绪，不可重试）
    DSH_LAUNCH = "DSH_LAUNCH"                  # 子进程启动异常（配置/环境，不可重试）
    DSH_TIMEOUT = "DSH_TIMEOUT"                # 会话超时（可重试 1 次）
    DSH_EXIT_NONZERO = "DSH_EXIT_NONZERO"      # 会话非零退出码（可重试 1 次）
    DSH_ABORTED = "DSH_ABORTED"                # 客户在页面上按了「终止运行」（**绝不重试**）
    DSH_STALLED = "DSH_STALLED"                # 会话长时间无输出且进程不退出（可重试 1 次）
    PLACEHOLDER_MISSING = "PLACEHOLDER_MISSING"  # 占位符缺值（配置错误，不可重试）
    SKILL_NOT_FOUND = "SKILL_NOT_FOUND"        # 技能不存在（agents 表绑定错误，不可重试）


ERROR_MESSAGES = {
    ErrorCode.DSH_NOT_FOUND: "找不到 dsh 可执行命令（headless 环境未就绪）",
    ErrorCode.DSH_LAUNCH: "DSH 会话启动异常",
    ErrorCode.DSH_TIMEOUT: "DSH 会话超时",
    ErrorCode.DSH_EXIT_NONZERO: "DSH 会话非零退出码",
    ErrorCode.DSH_ABORTED: "会话已被用户手动终止",
    ErrorCode.DSH_STALLED: "会话长时间无输出（已按静默判据收尾）",
    ErrorCode.PLACEHOLDER_MISSING: "技能占位符缺少对应输入",
    ErrorCode.SKILL_NOT_FOUND: "技能不存在（agents 表 skill_id 绑定错误）",
}


# 注意：`DSH_ABORTED`（客户按了终止）**绝不能进这个集合** —— 重试等于把客户刚按停的会话
# 重新跑起来，客户会觉得按钮没用。
#
# 可自动重试 1 次的失败类别：只保留“会话本身没跑成”的两类（超时 / 非零退出）。
# 环境/配置类错误（未找到命令、技能、占位符缺失、启动异常）重试无意义，不重试。
# 输出格式不合格既不判失败、也不重试（文本优先）：agent 会话是无状态子进程，重试等于把整场
# 推理从头再跑一遍（真机上单步可达数百秒），而下游是 LLM，给文本它就读得懂。格式要不要紧交由
# 编排层按语义判：该容忍的记 CONTRACT_SOFTFAIL（步骤仍 SUCCESS），真要紧的用 ROUTE_MISSING 硬失败。
RETRYABLE_ERROR_CODES = frozenset({
    ErrorCode.DSH_TIMEOUT,
    ErrorCode.DSH_EXIT_NONZERO,
    ErrorCode.DSH_STALLED,
})


# 命令行参数长度上限：Linux MAX_ARG_STRLEN ≈ 131072 字节，中文 UTF-8 每字 3 字节，
# 因此按“字节数”保守取 60KB；超过则把 prompt 落到工作目录 _dsh_task.md，命令行只传短指令。
# （真实踩坑：QA 步把 PRD+全部源码拼进 prompt，触发 OSError [Errno 7] Argument list too long）
ARGV_BYTE_LIMIT = 60_000
TASK_FILE_NAME = "_dsh_task.md"
TASK_FILE_INSTRUCTION = (
    "请先读取当前工作目录下的 _dsh_task.md 文件，并严格按照其中的全部要求完成任务；"
    "所有输入、格式约束与产物要求都以该文件为准。"
)


# ⚠️ 兼容兜底，**非真相源**（W6 §二）：技能的输出契约由各自 SKILL.md frontmatter 的
#    output[].contract 声明（读取入口 read_skill_contract）。本表只为尚未补 frontmatter 的
#    技能保留旧行为；待 contract_consistency.py 全绿 + agent 级通道就绪后可整体删除。
SKILL_CONTRACTS = {
    "simple-frontend": "json_array",
    "architect-planner": "text",
    "backend-executor": "file_blocks",
    "frontend-executor": "file_blocks",
    "pm-workflow": "text",
    "qa-workflow": "text",
}


def resolve_contract(skill_id: str = "", run_kind: str = "", agent_kind: str = "") -> str:
    """输出契约四层优先级链（W6 §二）：

        ① run_kind    调用方/编排节点显式指定（最高 —— 同一 agent 在不同节点可能要不同格式）
        ② agent_kind  agent 级落库值（用户自定义 agent 的旋钮；后端通道就绪后由编排层传入）
        ③ skill 声明  SKILL.md frontmatter `output[].contract`（这个技能天然产出什么）
        ④ text        兜底

    为什么 ③ 必须存在：`mode='skill'` 的技能应当自己声明产出格式，新增技能时**不必改本文件**。
    为什么 ① 在 ② 之前：run 级是编排决策，不该回写 agent 定义。
    非法值一律忽略并降级，不抛错 —— 解析阶段的错误由 parse_output 统一报可读文案。
    """
    for kind in (run_kind, agent_kind):
        k = (kind or "").strip()
        if k in OUTPUT_KINDS:
            return k
    declared = read_skill_contract(skill_id) if skill_id else ""
    if declared:
        return declared
    return SKILL_CONTRACTS.get(skill_id, "text") or "text"


class DshClient:
    """
    实例化 = 准备好拉起 DSH headless 会话的参数；run_agent() = 跑一个专职子 Agent。
    """

    def __init__(
        self,
        cmd: Optional[list] = None,
        profile: str = "",
        timeout: Optional[int] = None,
        skills_dir: Optional[Path] = None,
    ):
        self.cmd = cmd or [
            os.getenv("DSH_CMD", getattr(settings, "DSH_CMD", "") or "dsh"),
            "--profile",
            profile or os.getenv("DSH_PROFILE", getattr(settings, "DSH_PROFILE", "") or "headless"),
        ]
        # 会话时长上限（秒）。**0 = 不设上限**（已定：不同项目用时就该不一样，靠客户在页面上
        # 自己按「终止运行」，不靠一刀切的时间）。环境变量 DSH_TIMEOUT_SECONDS 覆盖。
        if timeout is None:
            raw_timeout = os.getenv("DSH_TIMEOUT_SECONDS",
                                    str(getattr(settings, "DSH_TIMEOUT_SECONDS", 900)))
            try:
                self.timeout = int(raw_timeout)
            except (TypeError, ValueError):
                self.timeout = 900
        else:
            self.timeout = int(timeout)
        self.skills_dir = skills_dir or _resolve_skills_dir()
        # ★ 静默看门狗阈值（秒）。**默认 0 = 关闭** —— 这是实测后的结论，别再改回非 0：
        #   我们试过两个"活跃信号"，**都不成立**：
        #     ① stdout 文件增长：dsh 只在 **turn 结束时**才把最终答案写 stdout →
        #        拿它当信号，会把正在干活的长会话当成"卡住"杀掉（真机误杀过 3 个会话，
        #        还把 71~134 字的开场白当成"交付"，直接造成一次残缺交付）；
        #     ② DSH 事件流（sessions/**.jsonl.zstd）增长：实测**同样只在 turn 结束时才落盘**
        #        （3 秒的会话里，前 2 秒都是 0 字节）。
        #   ⇒ 目前**没有可靠的"会话还活着"信号**，所以基于静默的自动杀必定误杀。
        #   卡住时由客户在页面上点「终止运行」（killpg 收整组，已有）；平台另有启动对账兜底。
        #   真要启用（例如给极长任务兜底），把 DSH_IDLE_TIMEOUT_SECONDS 设成正数，
        #   并且记住：**只有事件流里出现 turn/end 才会采用其产出**，否则一律报 DSH_STALLED。
        try:
            self.idle_timeout = int(os.getenv("DSH_IDLE_TIMEOUT_SECONDS", "0"))
        except ValueError:
            self.idle_timeout = 0

    @staticmethod
    def _kill_tree(pid: int, grace: float = 5.0) -> bool:
        """杀掉整个进程组（可选兜底：会话超上限时用）。客户手动终止走 run_registry。"""
        import signal as _signal

        try:
            pgid = os.getpgid(pid)
        except OSError:
            return False
        try:
            os.killpg(pgid, _signal.SIGTERM)
        except (OSError, ProcessLookupError):
            return False
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            time.sleep(0.1)
        try:
            os.killpg(pgid, _signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        return True

    # ---------------- DSH 事件流：可靠的"会话还在干活"信号 ----------------
    @staticmethod
    def _dsh_session_dir(workdir) -> "Path | None":
        """尽力定位这次会话的 DSH 事件流目录（按工作目录名匹配 + 取最新）。

        为什么需要它：dsh 只在 turn 结束才把最终答案写 stdout，**turn 进行中只有事件流在动**。
        所以判断"会话是否还活着"必须看事件流。找不到就返回 None —— 调用方据此**放弃静默判据**，
        宁可一直等（老行为），也不误杀正在干活的会话。
        """
        import os as _os
        from pathlib import Path as _P

        home = _os.environ.get("DSH_HOME") or str(_P.home() / ".dsh")
        sessions = _P(home) / "sessions"
        if not sessions.is_dir():
            return None
        key = _P(workdir).name                       # 例如 wf_s3_31fo0ab3
        cands = [d for d in sessions.iterdir() if d.is_dir() and key in d.name]
        if not cands:
            return None
        newest = max(cands, key=lambda d: d.stat().st_mtime)
        subs = [d for d in newest.iterdir() if d.is_dir()]
        return max(subs, key=lambda d: d.stat().st_mtime) if subs else None

    @staticmethod
    def _stream_activity(sdir) -> tuple:
        """事件流的 (最近改动时间, 总字节)。持续追加 → 是可靠的活跃信号。"""
        if sdir is None:
            return 0.0, 0
        newest, total = 0.0, 0
        try:
            for f in sdir.glob("*"):
                try:
                    st = f.stat()
                except OSError:
                    continue
                total += st.st_size
                newest = max(newest, st.st_mtime)
        except OSError:
            return 0.0, 0
        return newest, total

    @staticmethod
    def _stream_turn_done(sdir) -> "bool | None":
        """事件流里是否已经出现 `turn/end`（= 这个 turn 真的结束了）。

        返回 None = 读不到/解不开（调用方按"未确认"处理，即不把输出当成功）。
        """
        if sdir is None:
            return None
        try:
            from compression import zstd      # Python 3.14 自带
        except Exception:                     # noqa: BLE001
            return None
        for f in sorted(sdir.glob("*.zstd"), key=lambda x: x.stat().st_size, reverse=True):
            try:
                raw = zstd.decompress(f.read_bytes()).decode("utf-8", "replace")
            except Exception:                 # noqa: BLE001
                continue
            if '"type": "turn/end"' in raw or '"type":"turn/end"' in raw:
                return True
            return False                      # 只有这一个文件，读了就算数
        return None

    # ---------------- 技能读取 ----------------
    def load_skill_text(self, skill_id: str) -> str:
        """读取 skill 正文（去 frontmatter）。skill_id 不存在直接报错，快速暴露配置错误。"""
        p = self.skills_dir / skill_id / "SKILL.md"
        if not p.exists():
            raise DshError(f"技能不存在: {p}（请在 agents 表绑定正确的 skill_id）",
                           ErrorCode.SKILL_NOT_FOUND)
        return strip_frontmatter(p.read_text(encoding="utf-8"))

    # ---------------- Prompt 构建 ----------------
    def build_prompt(
        self,
        *,
        skill_id: str = "",
        system_prompt: str = "",
        mode: str = "skill",
        inputs: Optional[dict] = None,
        task_note: str = "",
    ) -> str:
        """
        拼装一次 agent 会话的完整 prompt：
            [skill 正文 | system_prompt]（按 mode 选择）→ 占位符填充 →（输入兜底）→ 附加执行要求
        mode: 'skill' 用 skill_id 读 SKILL.md；'prompt' 用 system_prompt（用户自定义 agent）；
              两者同时给时都拼上（skill 为角色说明书 + system_prompt 为系统级强化）。

        输入兜底（B5）：inputs 默认只通过 {{input.xxx}} 占位符注入。若调用方给了 inputs，
        但正文里一个占位符都没有（用户自定义提示词很常见），输入会被静默丢弃、agent 空转
        却仍报 SUCCESS。此时自动把 inputs 追加成「===== 本次任务输入 =====」块。
        """
        mapping = dict(inputs or {})
        parts = []
        if mode == "skill" and skill_id:
            parts.append(self.load_skill_text(skill_id))
        if system_prompt:
            parts.append(system_prompt)
        if not parts:
            # 连 prompt 都拼不出来 → 会话不可能启动，归到"启动异常"（配置类，不可重试）
            raise DshError("build_prompt 缺少内容：skill_id 与 system_prompt 至少提供一个",
                           ErrorCode.DSH_LAUNCH)

        text = "\n".join(parts)
        placeholder_hits = len(PLACEHOLDER_PATTERN.findall(text))

        try:
            text = fill_placeholders(text, mapping)
        except KeyError as e:
            raise DshError(f"技能[{skill_id or mode}]占位符填充失败: {e}",
                           ErrorCode.PLACEHOLDER_MISSING)

        # B5 兜底：给了输入却没用上任何占位符 → 输入会静默丢失，这里显式附上
        if mapping and placeholder_hits == 0:
            text = f"{text}\n\n===== 本次任务输入 =====\n{render_inputs_block(mapping)}"

        if task_note:
            text = f"{text}\n\n===== 执行要求 =====\n{task_note}"
        return text

    # ---------------- 契约解析 ----------------
    def parse_output(self, raw: str, skill_id: str = "", output_kind: str = "",
                     agent_kind: str = "") -> dict:
        """
        按契约解析 agent 原始输出。
        返回 {"kind":..., "parsed":..., "ok":bool, "error":str|None}
        kind: json_object | json_array | file_blocks | text
        契约来源优先级见 resolve_contract（run > agent > skill 声明 > text）。
        """
        kind = resolve_contract(skill_id, run_kind=output_kind, agent_kind=agent_kind)
        parsed, err = None, None
        if kind == "json_object":
            try:
                parsed = extract_json(raw)
            except Exception as e:
                err = f"JSON 对象解析失败: {e}"
        elif kind == "json_array":
            parsed = parse_json_array(raw)
            if parsed is None:
                err = "JSON 数组解析失败（期望 [{file_name, code_block}, ...]）"
        elif kind == "file_blocks":
            parsed = extract_file_blocks(raw)
            if not parsed:
                err = "未解析到 # File: 代码块（期望 “# File: 路径” + 围栏代码块）"
        elif kind == "text":
            parsed = None  # 文本类不做结构解析，由调用方/编排层消费原文
        else:
            # 经 resolve_contract 后不可达，保留以防御未来新增 kind 时漏配解析分支
            err = f"未知 output_kind: {kind}"
        return {"kind": kind, "parsed": parsed, "ok": not err, "error": err}

    # ---------------- 核心：跑一个 agent ----------------
    def run_agent(
        self,
        *,
        skill_id: str = "",
        system_prompt: str = "",
        mode: str = "skill",
        inputs: Optional[dict] = None,
        workdir: str | Path = "",
        task_note: str = "",
        output_kind: str = "",
        agent_output_kind: str = "",
        collect_files: bool = True,
        log_dir: str | Path = "",
        dry_run: bool = False,
        retries: int = 1,
        run_key=None,
        run_step_no=None,
        run_step_name: str = "",
    ) -> dict:
        """
        拉起一次 DSH headless 会话 = 实例化一个专职子 Agent。

        入参：
            skill_id / system_prompt / mode : 决定“这个 agent 是谁”（对应 agents 表绑定）
            inputs                           : {{input.xxx}} 填充值（如 user_requirement/prd_content）
            workdir                          : 产物工作目录（账号/项目隔离目录；agent 可直接落盘）
            task_note                        : 附加执行要求（如“输出后把结果保存为 PRD.md”）
            output_kind                      : run 级契约（节点/调用方显式指定）；空 = 继续向下回落
            agent_output_kind                : agent 级契约（用户自定义 agent 的落库值）；空 = 用技能声明推断。
                                               后端提供该通道后由编排层传入（详见 W6 §4.4）；当前默认空 = 老行为
            log_dir                          : 非空 = 会话日志写结构化 JSON（<session_id>.json，含
                                               meta/prompt/原始输出）到此目录；空 = 退回在 workdir 写
                                               <session_id>.log（仅原始输出）
            dry_run                          : True 只构建 prompt 不执行（联调/预览，不耗额度）
            retries                          : 失败自动重试次数（默认 1 = 最多 2 次尝试）；
                                                仅对 RETRYABLE_ERROR_CODES（超时/非零退出）生效。
                                                输出格式不合格不判失败、也不重试，只体现在返回的
                                                contract.ok / contract.error 里（文本优先）。

        返回（供 orchestrator 登记 project_agents.session_id / elapsed_time / final_output）：
            {success, session_id, elapsed_seconds, output, parsed, contract,
             files, workdir, log_file, dry_run, error, error_code, attempts}
        """
        workdir = Path(workdir) if workdir else Path.cwd()
        session_id = uuid.uuid4().hex[:12]
        # 配置类错误（技能不存在 / 占位符缺值 / 既没 skill 也没 system_prompt）也走"返回结果"这条路，
        # 而不是抛异常 —— 抛出去调用方只能拿到一句 traceback，步骤行上留不下 error_code/error。
        # 这类错误重试无意义（不在 RETRYABLE_ERROR_CODES 里），attempts 记 0 = 未执行。
        try:
            prompt = self.build_prompt(
                skill_id=skill_id, system_prompt=system_prompt, mode=mode,
                inputs=inputs, task_note=task_note,
            )
        except DshError as e:
            return {
                "success": False, "session_id": session_id, "dry_run": dry_run,
                "prompt": "", "output": "", "parsed": None, "contract": {},
                "files": [], "workdir": str(workdir), "log_file": "",
                "elapsed_seconds": 0.0, "error": str(e),
                "error_code": e.code or ErrorCode.DSH_LAUNCH, "attempts": 0,
            }

        # —— dry-run：只返回构建好的 prompt ——
        if dry_run:
            return {
                "success": True, "session_id": session_id, "dry_run": True,
                "prompt": prompt, "output": "", "parsed": None,
                "contract": {"kind": resolve_contract(skill_id, output_kind, agent_output_kind),
                             "ok": True, "error": None},
                "files": [], "workdir": str(workdir), "log_file": "",
                "elapsed_seconds": 0.0, "error": None, "error_code": None, "attempts": 0,
            }

        if not shutil.which(self.cmd[0]):
            return {
                "success": False, "session_id": session_id, "dry_run": False,
                "prompt": prompt, "output": "", "parsed": None, "contract": {},
                "files": [], "workdir": str(workdir), "log_file": "",
                "elapsed_seconds": 0.0,
                "error": ERROR_MESSAGES[ErrorCode.DSH_NOT_FOUND],
                "error_code": ErrorCode.DSH_NOT_FOUND, "attempts": 0,
            }

        workdir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()

        def _run_once(_prompt: str):
            """拉起一次 dsh 并等它结束。返回 (returncode, raw, log, via_file, killed_by_user)。

            为什么不用 `subprocess.run(capture_output=True, timeout=...)`：
              ① 拿不到子进程 pid —— 客户点「终止运行」时无从下手（本模块要把它登记进注册表）；
              ② 它是独立进程组才杀得干净：Agent 自己跑的命令（`.selftest.sh`、自测起的服务、pip）
                 都是它的**孙进程**，只杀直接子进程 = 假终止；
              ③ 输出走**文件**而不是管道：不依赖管道 EOF，也不会有"孙进程持着写端"的隐患。
            stdout / stderr 分开两个文件，保证 `raw` 仍然只含 stdout（下游契约解析不吃 stderr 噪声）。

            返回 meta = {kill_reason: "" | "timeout" | "idle", silent_seconds}：
              ""      = 进程自己正常退出（绝大多数情况）
              timeout = 撞到可选硬上限（DSH_TIMEOUT_SECONDS）
              idle    = ★ 静默判据：输出很久没增长但进程不退 → 已收掉整组，用已有输出继续
            """
            argv_prompt, via_file = _prompt, False
            if len(_prompt.encode("utf-8")) > ARGV_BYTE_LIMIT:
                (workdir / TASK_FILE_NAME).write_text(_prompt, encoding="utf-8")
                argv_prompt, via_file = TASK_FILE_INSTRUCTION, True

            out_p = workdir / f"{session_id}.stdout"
            err_p = workdir / f"{session_id}.stderr"
            started_at = time.monotonic()
            with open(out_p, "w", encoding="utf-8", errors="replace") as fo, \
                 open(err_p, "w", encoding="utf-8", errors="replace") as fe:
                proc = subprocess.Popen(
                    self.cmd + [argv_prompt], cwd=str(workdir),
                    stdout=fo, stderr=fe, text=True, encoding="utf-8", errors="replace",
                    start_new_session=True,          # ★ 独立进程组：终止时能连孙进程一起杀
                )
                if run_key is not None:
                    run_registry.register(run_key, proc.pid, step_no=run_step_no,
                                          name=run_step_name, started_at=time.time())
                kill_reason, silent_seconds, last_size, last_progress = "", 0.0, 0, started_at
                sdir = self._dsh_session_dir(workdir)
                _act_size, _act_mtime = last_size, 0.0
                try:
                    while True:
                        try:
                            proc.wait(timeout=0.5 if self.timeout else 2)
                            break
                        except subprocess.TimeoutExpired:
                            now = time.monotonic()
                            # ★ 活跃信号优先看 **DSH 事件流**（turn 进行中持续追加）——
                            #   stdout 只在 turn 结束时才写，用它当信号会误杀正在干活的会话。
                            mtime, size = self._stream_activity(sdir)
                            if sdir is not None and (size != _act_size or mtime != _act_mtime):
                                _act_size, _act_mtime = size, mtime
                                last_progress = now
                            else:
                                # 兜底/补充：stdout 有增长也算活着（假 dsh 测试、或事件流定位不到时）
                                try:
                                    osize = out_p.stat().st_size
                                except OSError:
                                    osize = 0
                                if osize != last_size:
                                    last_size, last_progress = osize, now
                            # ① 硬上限（可选，默认关：DSH_TIMEOUT_SECONDS=0）
                            if self.timeout and now - started_at > self.timeout:
                                kill_reason = "timeout"
                                break
                            # ② ★ 静默判据：turn 早就结束了、进程却赖着不退（真机踩到的那种）
                            if self.idle_timeout and now - last_progress > self.idle_timeout:
                                kill_reason = "idle"
                                silent_seconds = round(now - last_progress, 1)
                                break
                finally:
                    if kill_reason:
                        # 连**整个进程组**一起收掉：泄漏的后台子进程（自测服务、npm/node 残留）都在这组里
                        self._kill_tree(proc.pid)
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            pass
                        print(f"[ai_client] 会话 {session_id} 被收尾：{kill_reason}"
                              f"（静默 {silent_seconds}s，pid={proc.pid}）")
                    if run_key is not None:
                        run_registry.unregister(run_key, proc.pid)

            raw = out_p.read_text(encoding="utf-8", errors="replace")
            err = err_p.read_text(encoding="utf-8", errors="replace")
            log = raw + ("\n[stderr]\n" + err if err.strip() else "")
            turn_done = self._stream_turn_done(sdir) if kill_reason else None
            meta = {"kill_reason": kill_reason, "silent_seconds": silent_seconds,
                    "turn_done": turn_done, "stream_found": sdir is not None}
            return proc.returncode, raw, log, via_file, meta

        def _spawn(_prompt: str):
            """单次拉起 dsh；超长自动文件交接 + Errno 7 兜底（返回 rc/raw/log/via_file/meta）。"""
            try:
                rc, raw, log, via_file, meta = _run_once(_prompt)
            except OSError as e:
                # 兜底：真触发 Argument list too long 时自动降级为文件交接再试一次
                if getattr(e, "errno", None) == 7 and len(_prompt.encode("utf-8")) <= ARGV_BYTE_LIMIT:
                    (workdir / TASK_FILE_NAME).write_text(_prompt, encoding="utf-8")
                    rc, raw, log, via_file, meta = _run_once(TASK_FILE_INSTRUCTION)
                else:
                    raise
            return rc, raw, log, via_file, meta

        max_attempts = 1 + max(0, int(retries))
        attempt = 0
        error_code, error = None, None
        output, contract = "", {}
        rc, log, prompt_via_file = None, "", False

        watchdog: dict = {}
        for attempt in range(1, max_attempts + 1):
            try:
                rc, raw, log, prompt_via_file, meta = _spawn(prompt)
                if meta.get("kill_reason"):
                    watchdog = meta
            except subprocess.TimeoutExpired:
                error_code = ErrorCode.DSH_TIMEOUT
                error = f"{ERROR_MESSAGES[error_code]}（>{self.timeout}s），已终止"
                break
            except Exception as e:  # noqa: BLE001 —— 启动异常，重试无意义
                error_code = ErrorCode.DSH_LAUNCH
                error = f"{ERROR_MESSAGES[error_code]}: {e}"
                break

            output = clean_think(raw)
            contract = self.parse_output(output, skill_id=skill_id, output_kind=output_kind,
                                         agent_kind=agent_output_kind)

            # ★ 客户在页面上按了「终止运行」→ 明确报"被手动终止"，**不重试**（重试等于把他刚按停
            #   的东西又跑起来）。放在 returncode 判断之前：被杀掉的会话退出码必然是负的，
            #   不先判就会显示成含糊的"非零退出码"。
            if run_key is not None and run_registry.is_aborted(run_key):
                error_code = ErrorCode.DSH_ABORTED
                error = ERROR_MESSAGES[error_code]
                break

            # ★ 静默判据收的尾：**只要抓到了产出就当作成功**（真机踩到：dsh 的 turn 早就完成、
            #   76KB 代码就在 stdout 文件里，却因为进程不退而全丢）。产出为空才判失败并允许重试。
            if watchdog.get("kill_reason") == "idle":
                # ★ 只有**确认 turn 已经结束**（事件流里出现 turn/end）才把已抓到的输出当成功。
                #   否则一律报 DSH_STALLED —— 绝不把"半截产出"伪装成交付（第一版就是这么出错的：
                #   把一个正在干活的前端会话杀了，还把 134 字开场白判成成功）。
                if watchdog.get("turn_done") is True and raw.strip():
                    error_code, error = None, None
                    print(f"[ai_client] 会话 {session_id} 的 turn 已结束（事件流确认），"
                          f"按静默判据收尾并采用其产出")
                    break
                error_code = ErrorCode.DSH_STALLED
                error = (f"{ERROR_MESSAGES[error_code]}"
                         f"（事件流静默 {watchdog.get('silent_seconds')}s，"
                         f"turn 完成={watchdog.get('turn_done')}，事件流定位="
                         f"{watchdog.get('stream_found')}，已收掉会话进程组）")
                break
            if watchdog.get("kill_reason") == "timeout":
                error_code = ErrorCode.DSH_TIMEOUT
                error = f"{ERROR_MESSAGES[error_code]}（>{self.timeout}s），已终止"
                break

            # 文本优先：输出格式不合格不判失败（只落在返回的 contract.ok / contract.error 上），
            # 所以本次调用唯一的失败判据是会话自身非零退出。
            if rc == 0:
                error_code, error = None, None
                break

            error_code = ErrorCode.DSH_EXIT_NONZERO
            error = f"DSH 退出码 {rc}"
            if contract.get("error"):
                error = f"{error}; {contract['error']}"

            if attempt < max_attempts and error_code in RETRYABLE_ERROR_CODES:
                # 把失败原因回灌进 prompt，驱动 agent 在下一次尝试中修正执行
                prompt = (
                    prompt
                    + f"\n\n===== 自动重试反馈（第 {attempt} 次尝试失败）=====\n"
                    + f"你上一次的执行未能正常结束，失败原因：{error}\n"
                    + "请针对上述原因修正后重新完成任务。"
                )

        elapsed = time.monotonic() - started

        # —— 审计：运行时日志落盘（前端“会话日志 / 步骤执行可视化”数据源）——
        # log_dir 非空 → 结构化 JSON（meta + prompt + 原始输出 + 清洗后输出）；
        # log_dir 空 → 退回旧行为：workdir 下 <session_id>.log。dry-run/未执行不写。
        log_file = ""
        try:
            if log_dir:
                log_path = Path(log_dir)
                log_path.mkdir(parents=True, exist_ok=True)
                log_file = str(log_path / f"{session_id}.json")
                (log_path / f"{session_id}.json").write_text(
                    json.dumps({
                        "session_id": session_id,
                        "skill_id": skill_id,
                        "mode": mode,
                        "output_kind": resolve_contract(skill_id, output_kind, agent_output_kind),
                        "started_at": datetime.now().isoformat(timespec="seconds"),
                        "elapsed_seconds": round(elapsed, 3),
                        "exit_code": rc,
                        "watchdog": watchdog or None,
                        "success": error is None,
                        "error": error,
                        "error_code": error_code,
                        "attempts": attempt,
                        "inputs": {
                            k: (v[:2000] + "…(已截断)" if isinstance(v, str) and len(v) > 2000 else v)
                            for k, v in (inputs or {}).items()
                        },
                        "prompt": prompt,
                        "prompt_via_file": prompt_via_file,
                        "output_clean": output,
                        "raw_log": log,
                    }, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            else:
                (workdir / f"{session_id}.log").write_text(log, encoding="utf-8", errors="replace")
        except OSError:
            log_file = ""  # 日志落盘失败不阻断主流程

        return {
            "success": error is None,
            "session_id": session_id,
            "dry_run": False,
            "prompt": prompt,
            "output": output,
            "parsed": contract.get("parsed") if contract else None,
            "contract": contract,
            "files": list_files(workdir) if collect_files else [],
            "workdir": str(workdir),
            "log_file": log_file,
            "prompt_via_file": prompt_via_file,
            "elapsed_seconds": round(elapsed, 3),
            "watchdog": watchdog or None,     # 非空 = 被静默判据/硬上限收的尾
            "error": error,
            "error_code": error_code,
            "attempts": attempt,
        }

    # ---------------- 异步包装（FastAPI 事件循环友好） ----------------
    async def arun_agent(self, **kwargs) -> dict:
        """run_agent 的异步版：子进程阻塞放到线程池，不卡事件循环。"""
        return await asyncio.to_thread(self.run_agent, **kwargs)


# 全局单例：orchestrator / 各路由统一用 ai_client
ai_client = DshClient()
