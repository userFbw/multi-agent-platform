"""workflow_engine.py —— 通用图执行器（Agent 编排运行时）

职责：读一份 workflow 定义 → 按依赖波次调度节点 → 每个节点查 agents 注册表实例化一个
Agent（经 ai_client 拉起 DSH 会话）→ 产物落项目目录 → 每步写一行 project_steps。

能力：
    · 串行 / 并行：依赖已结束的节点同一波并发（asyncio.gather），如前后端并行；
    · 条件分支：node.when 引用上游的**决策字段**（如「某个上游节点」.parsed.level == "complex"），
      不满足的节点记 SKIPPED，**不实例化 agent**（跳过的节点零成本）；
      被引用的节点成为「决策源」，引擎**从图里推导出它必须给出的取值闭集**，执行时注入提示词，
      执行后校验 —— 取不到/不合法 → 该节点 FAILED（ROUTE_MISSING），**绝不静默跳过**；
    · 输入绑定：ref / from_marker / until_marker / code_combined 等，语法见 builtin_workflows 模块头；
    · 产物：artifact_file 落文本文件；code_dir 把代码类产物合并进项目目录；
    · 登记：project_steps 一行一步（PENDING → SUCCESS / FAILED / SKIPPED，含会话号/耗时/产物路径/错误码）。

外部契约：
    run_workflow(db, *, project, user_id, workflow, seeds=..., ai=..., round_no=..., log_dir=...)
    —— ai 可注入（测试用假实现），默认 ai_client 全局单例。
"""
import asyncio
import os
import re
import shutil
import tempfile
import traceback
from pathlib import Path
from typing import Optional

from app.db.crud import agents_crud
from app.db.crud import project_steps_crud as steps_crud
from app.db.crud import workflows_crud
from app.agents.builtin_workflows import (
    BUILTIN_WORKFLOWS, DEFAULT_WORKFLOW_NAME, LEGACY_WORKFLOW_NAMES,
)
from app.agents import node_inputs, runlog, run_registry
from app.agents.ai_client import ai_client as _default_ai
from app.storage.file_helper import file_helper


class WorkflowError(RuntimeError):
    """工作流定义/执行错误。"""


def resolve_workflow(db, *, user_id: int, workflow_id=None, workflow_name: str = "", default=None):
    """决定"这次跑哪张图"：自定义(by id) → 内置(by name) → 默认模板。

    主链路的 approve 用它，让 PM→DEV→QA 从"写死的常量"变成"可替换的默认模板"。
    找不到/没权限一律抛 WorkflowError（宁可明确报错，也别悄悄跑成别的图）。
    """
    if workflow_id is not None:
        row = workflows_crud.get_workflow_by_id(db, workflow_id)
        if not row or row.user_id != user_id:
            raise WorkflowError(f"工作流 {workflow_id} 不存在或不属于当前用户")
        return {"name": row.name, "description": row.description or "", "nodes": row.nodes or []}
    if workflow_name:
        wf = BUILTIN_WORKFLOWS.get(workflow_name)
        if not wf:
            # 旧名字（历史项目的 projects.workflow_name / 前端 localStorage）自动映射到现名，
            # 免得"项目选定的工作流已不可用"把老项目卡死
            mapped = LEGACY_WORKFLOW_NAMES.get(workflow_name)
            wf = BUILTIN_WORKFLOWS.get(mapped) if mapped else None
        if not wf:
            raise WorkflowError(
                f"内置工作流 {workflow_name!r} 不存在，可选：{'/'.join(BUILTIN_WORKFLOWS)}"
            )
        return wf
    return default if default is not None else BUILTIN_WORKFLOWS[DEFAULT_WORKFLOW_NAME]


# 软失败错误码：节点产出了东西、只是格式没对上技能声明的契约。
# 文本优先原则下这**不算失败**（下游是 LLM，给文本它就读得懂），但要在步骤里留痕。
SOFT_CONTRACT_CODE = "CONTRACT_SOFTFAIL"

# 决策缺失错误码：这个节点的产出是**别的节点要不要参与**的依据，而它没给出合法取值。
#
# 为什么它必须是**硬失败**（而格式问题只是软失败）：
#   内容格式不对 → 下游是 LLM，给文本它就读得懂 → 可以容忍；
#   决策给不出   → 引擎无法判断走哪条分支 → **链路根本无法继续** → 必须停下来说清楚。
#
# 修的是这个真 bug：以前 `ctx.bind()` 判不出时返回空串，于是
#   「我不懂（解析不出）」和「答案是别的（真的不匹配）」落到同一个 `!=` 分支
#   → 两条路全被静默跳过 → 零代码还报 COMPLETED。
ROUTE_MISSING = "ROUTE_MISSING"


def parse_when_ref(ref: str) -> tuple:
    """拆 `when.ref`：`'reviewer.parsed.level'` → `('reviewer', 'level')`。

    **只认 `<节点id>.parsed.<字段>` 这一种形式**（决策必须来自结构化输出的一个字段）。
    返回 `("", "")` 表示形式不合法，由 validate_workflow 报错。
    """
    parts = (ref or "").split(".")
    if len(parts) == 3 and parts[1] == "parsed" and all(parts):
        return parts[0], parts[2]
    return "", ""


def derive_decisions(wf: dict) -> dict:
    """扫图推导「决策源」：被 `when` 引用的节点，以及它**必须给出**的取值闭集。

    这是本次修复的核心 —— 闭集**从图里推导**，而不是靠"写图的人私下约定、技能自己猜"：
        图里写了 `eq: simple` / `eq: complex`  ⟹  分类器必须给出 simple 或 complex 之一。

    返回 `{节点id: {"field": 字段名, "closed": [取值…], "used_by": [下游步骤名…]}}`
    """
    out = {}
    for nd in wf["nodes"]:
        when = nd.get("when")
        if not when:
            continue
        src, field = parse_when_ref(when.get("ref", ""))
        if not src:
            continue                       # 形式不合法 → validate_workflow 已拦下
        d = out.setdefault(src, {"field": field, "closed": [], "used_by": []})
        value = str(when.get("eq"))
        if value not in d["closed"]:
            d["closed"].append(value)
        d["used_by"].append(nd.get("name") or nd["id"])
    return out


def decision_note(dec: dict) -> str:
    """给决策源节点追加的执行要求（走 task_note 这个平台通道，不改技能）。"""
    closed = "、".join(f"`{v}`" for v in dec["closed"])
    return (
        f"\n\n===== 本次必须给出决策 =====\n"
        f"你的输出必须能被解析为 JSON，且 `{dec['field']}` 字段只能取以下之一：{closed}。\n"
        f"平台据此决定后续派哪些 Agent 参与（{('、'.join(dec['used_by']))}），"
        f"**取值缺失或不合法会导致本次执行失败**。"
    )


def check_decision(res: dict, dec: dict) -> str:
    """决策源节点执行后校验；返回错误文案（空串 = 通过）。"""
    parsed = res.get("parsed")
    if not isinstance(parsed, dict):
        return (f"没有给出可解析的 JSON（需要 `{dec['field']}` ∈ "
                f"{'、'.join(dec['closed'])}），无法决定后续派哪些 Agent")
    if dec["field"] not in parsed:
        return (f"JSON 里缺少 `{dec['field']}` 字段（需要 "
                f"{'、'.join(dec['closed'])} 之一），无法决定后续派哪些 Agent")
    got = str(parsed[dec["field"]])
    if got not in dec["closed"]:
        return (f"`{dec['field']}` = {got!r} 不在允许取值内（只能是 "
                f"{'、'.join(dec['closed'])}），无法决定后续派哪些 Agent")
    return ""


# =====================================================================
# 1. 定义校验
# =====================================================================
def validate_workflow(workflow: dict) -> dict:
    """校验并归一 workflow 定义；非法直接抛 WorkflowError。"""
    if not isinstance(workflow, dict) or not workflow.get("nodes"):
        raise WorkflowError("workflow 必须是含 nodes 列表的对象")
    nodes = workflow["nodes"]

    # 第一遍：id 与 agent 的基本合法性。必须先把全量 id 收齐再判依赖 ——
    # 画布上新拖入的节点排在数组末尾，"新节点 → 已有节点"这种依赖天生是前向引用，
    # 边收边判会把它误报成"依赖不存在的节点"（列表顺序不该影响定义是否合法）。
    ids, code_dirs = set(), set()
    for nd in nodes:
        nid = nd.get("id")
        if not nid or nid in ids:
            raise WorkflowError(f"节点 id 缺失或重复: {nid!r}")
        ids.add(nid)
        agent = nd.get("agent") or {}
        aid = agent.get("id")
        if aid not in (None, "", 0, "0"):
            try:
                int(aid)
            except (TypeError, ValueError):
                raise WorkflowError(f"节点 {nid} 的 agent.id 不是数字: {aid!r}")
        elif not (agent.get("role_key") or "").strip():
            # id 为空就等于没给，这时必须给 role_key（画布保存的图走的就是 role_key 这条路）
            raise WorkflowError(f"节点 {nid} 缺少 agent(id/role_key)")
        if nd.get("code_dir"):
            code_dirs.add(nd["code_dir"])

    # 第二遍：依赖引用
    for nd in nodes:
        for dep in nd.get("deps", []):
            if dep not in ids:
                raise WorkflowError(f"节点 {nd['id']} 依赖不存在的节点 {dep!r}")
    if len(code_dirs) > 1:
        raise WorkflowError(f"v0 仅支持单一代码合并目录，当前: {code_dirs}")

    # —— 决策判据的形式校验（在保存/执行前就拦住，而不是运行时变成谜之跳过）——
    for nd in workflow["nodes"]:
        when = nd.get("when")
        if not when:
            continue
        if "ref" not in when or "eq" not in when:
            raise WorkflowError(f"节点 {nd['id']} 的 when 必须含 ref 与 eq")
        src, _field = parse_when_ref(when["ref"])
        if not src:
            raise WorkflowError(
                f"节点 {nd['id']} 的 when.ref 必须是 `<节点id>.parsed.<字段>` 形式"
                f"（决策只能来自结构化输出的一个字段），当前: {when['ref']!r}"
            )
        if src not in ids:
            raise WorkflowError(f"节点 {nd['id']} 的 when 引用了不存在的节点 {src!r}")
        if not str(when.get("eq") or ""):
            raise WorkflowError(f"节点 {nd['id']} 的 when.eq 不能为空")
    # 归一 name/description：自定义工作流常常只有 nodes（前端画布就是传 {"nodes": [...]}），
    # 缺 name 时后面取 wf["name"] 会直接 KeyError，把整轮跑挂掉。
    return {
        **workflow,
        "name": workflow.get("name") or "自定义工作流",
        "description": workflow.get("description") or "",
    }


# =====================================================================
# 2. 工具：引用解析 / 绑定 / src 拼接 / 代码落盘
# =====================================================================
def _get_path(obj, parts):
    cur = obj
    for p in parts:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and p.isdigit() and int(p) < len(cur):
            cur = cur[int(p)]
        else:
            return None
    return cur


class BindContext:
    def __init__(self, seeds: dict, outputs: dict, src_dir: str):
        self.seeds = seeds
        self.outputs = outputs      # node_id -> ai_client 结果 dict
        self.src_dir = src_dir

    def _resolve_ref(self, ref: str):
        parts = ref.split(".")
        if parts[0] == "seed":
            return self.seeds.get(".".join(parts[1:]))
        node_id, rest = parts[0], parts[1:]
        res = self.outputs.get(node_id)
        if res is None:
            raise WorkflowError(f"引用节点 {node_id!r} 尚无输出（依赖顺序错误）")
        return _get_path(res, rest) if rest else res

    def bind(self, value):
        """绑定值 → 字符串：dict 特殊语法 / 其它按字面量。"""
        if isinstance(value, dict):
            if "ref" in value:
                text = self._resolve_ref(value["ref"])
                text = "" if text is None else str(text)
                fm, um = value.get("from_marker"), value.get("until_marker")
                if fm and fm in text:
                    text = text[text.find(fm):]
                if um and um in text:
                    text = text[:text.find(um)]
                return text
            if value.get("code_combined"):
                return self._combine_src()
            raise WorkflowError(f"不支持的绑定语法: {value}")
        return "" if value is None else str(value)

    def _combine_src(self) -> str:
        if not self.src_dir or not os.path.isdir(self.src_dir):
            return ""
        combined = []
        for root, _, files in sorted(os.walk(self.src_dir)):
            for f in sorted(files):
                # .stdout/.stderr = 会话捕获文件（日志），绝不能当"源码"喂给下游 Agent
                if f.endswith((".log", ".stdout", ".stderr")):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, self.src_dir).replace("\\", "/")
                try:
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        combined.append(f"=== File: {rel} ===\n{fh.read()}\n\n")
                except OSError:
                    pass
        return "".join(combined)


def _safe_join(base: str, rel: str) -> str:
    rel = rel.replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return ""
    return os.path.join(base, *rel.split("/"))


# Agent 会话里可能顺手写出的运行期垃圾，不进项目目录
#   · .stdout/.stderr：平台的会话捕获文件（`ai_client.list_files` 已不列它们，
#     这里再兜一层 —— 老项目 `src/` 里已有的那几份也不会被再当成产物传播）
_SKIP_SUFFIX = (".log", ".pyc", ".db", ".sqlite3", ".stdout", ".stderr")
_SKIP_PARTS = ("__pycache__", "node_modules", ".venv", ".git")
_PLATFORM_FILES = (".selftest.sh", ".selftest_runs.md", ".openapi.json",
                   "run_check.log", "_dsh_task.md")   # _dsh_task.md = 超长 prompt 的文件交接

# 「这是代码产物」的两种输出契约（闭集见 ai_client.OUTPUT_KINDS）
CODE_KINDS = ("file_blocks", "json_array")

# 平台对"代码类节点"统一追加的真跑要求。
#
# 为什么放在引擎而不是写在每张图的 task_note 里：内置图、编排官生成的图、用户画布图
# 三种来源都要覆盖，写在图里就得每张图抄一遍 —— 实测就漏过（full_dev_chain 的 simple
# 节点 task_note 是空的，真机跑出来 Agent 根本没执行自检脚本）。
#
# 脚本自己会分流：目录里有 main.py 就起 uvicorn 打接口，没有就静态托管页面 +
# 探测 HTML 引用的资源 + node --check 解析 JS，所以这一段对前后端通用。
ABORTED_STEP_MSG = "用户手动终止（这一步没跑完，已完成的产物保留）"

# 增量修改时贴在 task_note 里的协议（**必须显式说**：上一版代码就在工作目录里，
# 让它做局部修改而不是"凭空重写一套"）
INCREMENTAL_NOTE = (
    "⚠️ 这是**增量修改**（不是从零重写）：当前工作目录里**已经有上一版代码**。"
    "请先读一遍现有文件，然后**在原有文件上做局部修改**，"
    "保留没有被要求改动的功能、样式与文件；只有明确要求改的部分才改。"
    "不要新建一批替代文件来绕过旧文件，也不要因为没让你改就删掉它们。"
)

RUN_NOTE = (
    "写完**必须真跑**，不许只读代码：在项目根执行 `bash .selftest.sh`"
    "（可带参数指定代码目录，如 `bash .selftest.sh backend`）。"
    "脚本会真起服务/真解析文件，失败时打出真实报错与行号；"
    "照报错改代码→再跑，最多 3 轮，直到输出 `[SELFTEST] runtime=PASS`。"
    "该结论行由脚本产出并自动记录，**不要自己手写、不要伪造**。"
)


# 产出 PRD 的角色标识：审批闸门、以及"多个 PM 时下游读哪一份"都以它为准
PRD_ROLE_KEY = "pm"

# 节点的上游还有一份 PRD 时追加的说明（"审批之后再让 PM 出一版"就是这么用的）。
REVISE_PRD_NOTE = (
    "你上游已经有**一版 PRD**（完整内容见下面的「上一版 PRD」）："
    "请在它基础上产出更新后的**完整** PRD（保留仍然适用的章节、落实新的要求），"
    "不要只写一段增量说明或差异清单。"
)


def _is_prd_node(nd: dict) -> bool:
    return ((nd.get("agent") or {}).get("role_key") or "") == PRD_ROLE_KEY


def _bfs_upstream(nodes: list, nd: dict, outputs: dict, want) -> str:
    """按 BFS（先近后远）找第一个满足 `want(节点, 产出文本)` 的上游节点 id；没有返回 ""。"""
    deps = {n["id"]: list(n.get("deps") or []) for n in nodes}
    by_id = {n["id"]: n for n in nodes}
    seen, queue = set(), list(deps.get(nd["id"]) or [])
    while queue:
        nid = queue.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        src = by_id.get(nid)
        text = ((outputs.get(nid) or {}).get("output") or "").strip()
        if text and src is not None and want(src, text):
            return nid
        queue.extend(deps.get(nid) or [])
    return ""


def nearest_prd_output(nodes: list, nd: dict, outputs: dict, is_code_node=None) -> str:
    """这个节点的 `prd_content` 该取哪一份上游产出？（BFS，先近后远）

    两档：
      ① 最近的上游**产出 PRD 的节点**（role_key = pm）—— 图上可以有多个
         （"审批通过后再让 PM 出一版"就是这么画的），取离它最近的那个，与画布上连的线一致；
      ② 没有 pm 节点时（图上中间那一段是**自定义 Agent**，实测 p34 就是这种：
         PM → 我的审PM(自定义) → 简单前端），退一步取最近的**非代码**上游产出 ——
         写代码的节点产出的是代码不是需求，拿它当 PRD 会让下游核对错对象，所以跳过。
      ③ 都没有 → 返回 ""，调用方回落到种子（= 审批通过的那份）。
    """
    found = _bfs_upstream(nodes, nd, outputs, lambda src, _t: _is_prd_node(src))
    if found:
        return found
    if is_code_node is None:
        return ""
    return _bfs_upstream(nodes, nd, outputs, lambda src, _t: not is_code_node(src))


def _is_junk(rel: str) -> bool:
    """平台自带文件与运行期垃圾，都不该进项目产物。"""
    name = rel.rsplit("/", 1)[-1]
    return (rel.endswith(_SKIP_SUFFIX)
            or any(p in rel.split("/") for p in _SKIP_PARTS)
            or name in _PLATFORM_FILES
            or name.startswith(".selftest")     # 平台脚本 + Agent 自己造的同类脚本
            or name.startswith("SELFTEST"))


# 平台放进 Agent 工作目录的一键真跑脚本（见 agent_selftest.sh 的头部说明）
_SELFTEST_SH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_selftest.sh")


def _install_selftest(tmp_dir: str) -> None:
    """把平台的 .selftest.sh 放进代码类节点的工作目录，供 Agent 真跑自检。

    Agent 沙箱里后台进程不跨 bash 调用存活，起服务+探测+杀服务必须在一次调用内完成；
    这个脚本把这套动作封好，Agent 只管 `bash .selftest.sh backend /api/xxx`。
    """
    try:
        shutil.copy2(_SELFTEST_SH, os.path.join(tmp_dir, ".selftest.sh"))
    except OSError:
        pass


def _contract_kind(skill_id: str, run_kind: str = "", agent_kind: str = "") -> str:
    """这次调用会按哪种契约解析输出 —— 复用 ai_client 的四层优先级链，不另写一套。"""
    from app.agents.ai_client import resolve_contract
    return resolve_contract(skill_id, run_kind=run_kind, agent_kind=agent_kind)


def _is_code_result(res: dict) -> bool:
    """这次产出是不是"代码"——看**契约种类**，不猜正文。

    文本优先下"格式不对"不再判失败（P0-3），但"是不是代码"必须判准：
    否则 Agent 写出来的源码会随 tmp 一起被删掉（实测踩过）。
    """
    return ((res.get("contract") or {}).get("kind") or "") in CODE_KINDS


def _safe_artifact_name(nd: dict) -> str:
    """节点名 → 安全的产物文件名（纯文本输出存成 <步骤名>.md）。"""
    name = str(nd.get("name") or nd.get("id") or "step")
    return "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "step"


def _read_runs(tmp_dir: str) -> str:
    """读平台真跑记录。必须在 tmp 被删之前读：它是结论的唯一可信来源。"""
    try:
        return Path(tmp_dir, ".selftest_runs.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def _strip_code_dir_prefix(rel: str, code_dir: str) -> str:
    """把产物路径里多余的 `<code_dir>/` 前缀剥掉。

    真机踩到（p47）：约定是"`# File:` 路径相对 `src/`"，但 Agent 有时写成
    `# File: src/backend/main.py` → 落盘后变成 `<项目>/src/src/backend/main.py`
    → runner 找不到 `src/backend/main.py`，应用根本起不来。
    所以这里统一归一：`src/backend/main.py` → `backend/main.py`。
    """
    rel = (rel or "").replace("\\", "/").lstrip("./")
    prefix = (code_dir or "src").strip("/") + "/"
    while rel.startswith(prefix):
        rel = rel[len(prefix):]
    return rel


def _persist_code(res: dict, tmp_dir: str, dest_dir: str, project_dir: str,
                  code_dir: str = "src") -> list:
    """把代码类 agent 产物汇入 dest_dir；返回**实际落地的项目相对路径**列表。

    返回真实路径（如 `src/backend/main.py`）而不是个数，是为了让运行日志能直接写出
    "这个 Agent 产出了哪些文件"，不用再让人拿工作目录相对路径和 `src/` 自己拼。

    Agent 自己写的 `运行日志/`、`项目BUG/` 放到项目根（产物目录之外），免得混进源码目录；
    这类不算产物，不进返回值。
    """
    os.makedirs(dest_dir, exist_ok=True)
    landed: list = []

    def _place(rel: str, text=None) -> None:
        """rel 是工作目录相对路径；text 为 None 时从工作目录拷文件。"""
        to_log_dir = runlog.is_agent_log_path(rel)
        if not to_log_dir:
            source_rel = rel                       # 读文件要用**原始**路径
            rel = _strip_code_dir_prefix(rel, code_dir)
        else:
            source_rel = rel
        dst = _safe_join(project_dir if to_log_dir else dest_dir, rel)
        if not dst:
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if text is None:
            shutil.copy2(os.path.join(tmp_dir, source_rel), dst)
        else:
            with open(dst, "w", encoding="utf-8") as f:
                f.write(text)
        if not to_log_dir:
            landed.append(Path(dst).relative_to(project_dir).as_posix())

    for rel in [f for f in res.get("files") or [] if not _is_junk(f)]:
        _place(rel)

    if not landed:                              # 工作目录里没有代码文件 → 用契约解析结果
        parsed = res.get("parsed")
        if isinstance(parsed, list):            # simple-frontend JSON 数组
            for item in parsed:
                _place((item or {}).get("file_name") or "index.html",
                       (item or {}).get("code_block") or "")
        elif isinstance(parsed, dict):          # executor # File: 块
            for rel, code in parsed.items():
                _place(rel, code)
    return landed


def _upstream_block(ctx, node: dict, node_name: dict, exclude=()) -> str:
    """把上游节点的输出拼成带标题的文本块。

    文本优先原则的落点：下游声明什么名字都无所谓 —— 认不出来就把这块整段给它。
    `exclude` 用来跳过"已经作为正式输入送下去"的那一个（如 prd_content），免得同一份文本出现两遍。
    """
    parts = []
    for dep in node.get("deps") or []:
        if dep in exclude:
            continue
        res = ctx.outputs.get(dep)
        text = ((res or {}).get("output") or "").strip()
        if text:
            parts.append(f"===== 上游「{node_name.get(dep, dep)}」的输出 =====\n{text}")
    return "\n\n".join(parts)


def _persist_by_shape(res: dict, tmp_dir: str, project_dir: str, src_abs: str,
                      code_dir_name: str, nd: dict) -> tuple:
    """节点没声明 `code_dir` / `artifact_file` 时的兜底落盘（看输出形态）：

        · 契约是 file_blocks / json_array（= 代码）→ 落进项目代码目录
        · 其它（纯文本）                          → 存成 <步骤名>.md
        · 判成代码却一个文件都没落出来（格式崩了） → 退回把正文存成 <步骤名>.md，绝不静默丢

    返回 (artifact_path, 产物相对路径列表)。
    """
    if _is_code_result(res):
        landed = _persist_code(res, tmp_dir, src_abs, project_dir, code_dir_name)
        if landed:
            return code_dir_name + "/", landed
        # 契约说是代码、但解析不出文件块 → 至少把原文留下来，别丢
    name = _safe_artifact_name(nd) + ".md"
    os.makedirs(project_dir, exist_ok=True)
    with open(os.path.join(project_dir, name), "w", encoding="utf-8") as f:
        f.write(res.get("output") or "")
    return name, [name]


def _log_node(project_dir, nd, agent, row_, round_no, res, status,
              artifact_path="", record="", artifacts=(), error_code=None, inputs=None,
              unfilled=()):
    """节点结束后补两类日志：运行日志（人看这一步） + 项目BUG（这次代码真跑的结果）。

    record 是平台 .selftest.sh 自己记的真跑记录，是结论的可信来源；
    Agent 最终回答里的 [SELFTEST] 行只在没有 record 时兜底。
    artifacts 是**实际落地的项目相对路径**，直接写进运行日志，不用人再拼 src/。
    """
    output = res.get("output") or ""
    runlog.write_step_log(
        project_dir,
        seq=row_.step_no, name=nd.get("name", nd["id"]),
        round_no=round_no, skill_id=agent.skill_id or "", agent_name=agent.name or "",
        session_id=res.get("session_id") or "",
        elapsed_ms=int(round((res.get("elapsed_seconds") or 0) * 1000)),
        status=status,
        error_code=(res.get("error_code") or "") if error_code is None else error_code,
        artifact_path=artifact_path, files=artifacts,
        output=output, record=record, inputs=inputs, unfilled=list(unfilled or []),
    )
    return runlog.write_bug_logs(
        project_dir, step_no=row_.step_no, agent_name=agent.name or nd["id"],
        round_no=round_no, output=output, record=record,
    )


# =====================================================================
# 3. 执行器
# =====================================================================
def ancestors_of(nodes: list, nid: str) -> set:
    """`nid` 的全部上游 + 自身。用于"只跑到闸门节点为止"（PRD 段就这么截出来的）。"""
    deps = {n["id"]: list(n.get("deps") or []) for n in nodes}
    seen, stack = {nid}, [nid]
    while stack:
        for d in deps.get(stack.pop(), []):
            if d in deps and d not in seen:
                seen.add(d)
                stack.append(d)
    return seen


def find_prd_node(wf: dict) -> str:
    """图里「产出 PRD 的那个节点」的 id —— 它就是人工审批的闸门位置。

    为什么按角色找而不是按节点 id 写死：图是数据，用户可以自己画；写死 `pm`
    会在用户自绘图上失效。找不到就返回 ""，由调用方决定是报错还是走兜底。
    """
    for nd in wf.get("nodes") or []:
        if _is_prd_node(nd):
            return nd.get("id") or ""
    return ""


def ensure_prd_node(wf: dict) -> str:
    """创建项目用的图必须含 PRD 节点：没有它就没有审批对象，项目生命周期不成立。"""
    nid = find_prd_node(wf)
    if not nid:
        raise WorkflowError(
            "工作流必须包含生成 PRD 的节点（角色 role_key = pm）："
            "平台要先产出需求文档、让用户审批，之后才继续开发"
        )
    return nid


def project_template_hint(wf: dict) -> str:
    """这张图能不能当**项目模版**？返回 ""（可以）或一句人话说明（不可以）。

    规则只有一条：必须含"产出 PRD 的节点"（审批闸门卡在它之后）。画布随时可以存这样的图、
    也能用「执行」立刻跑，但它不会出现在首页「指定工作流」下拉里 —— 以前这件事**没有任何提示**，
    用户只会觉得"我保存的图怎么不见了"（实测反馈）。
    """
    if find_prd_node(wf):
        return ""
    return ("这张图没有「生成 PRD」节点（产品经理），因此只能用于画布上的「执行」，"
            "不能作为项目模版（首页「指定工作流」里不会出现）——"
            "项目流程是 PM 出 PRD → 人工审批 → 再按图继续。")


# 产出后端代码的角色 / 只产出静态页的角色（判"这张图能不能交出前后端分离的应用"用）
BACKEND_ROLES = ("backend-executor",)
STATIC_ONLY_ROLES = ("simple-frontend",)


def shape_mismatch_warning(wf: dict, prd_text: str) -> str:
    """审批前的**形态预检**：PRD 判定的运行形态，与这张图实际能产出的东西，对不对得上。

    只返回一句人话（"" = 没问题），**不做阻断**：判断依据是 PRD 正文里的「运行形态」那一行，
    决定权仍在用户手上。真机踩到过：PRD 判「前后端分离」（备忘录要持久化数据），
    图却是 pm → 简单前端 → QA —— 跑完只有三个前端文件，QA 判 FAIL、白烧一轮额度。
    """
    if not prd_text:
        return ""
    m = re.search(r"运行形态\**[ \t]*[:：][ \t]*\**[ \t]*(纯前端|前后端分离)", prd_text)
    if not m:
        return ""
    shape = m.group(1)
    roles = {(nd.get("agent") or {}).get("role_key") or "" for nd in (wf or {}).get("nodes") or []}
    has_backend = any(r in roles for r in BACKEND_ROLES)
    has_static = any(r in roles for r in STATIC_ONLY_ROLES)

    if shape == "前后端分离" and not has_backend:
        tip = "建议改用内置「复杂项目」模板，或在画布上补：架构师 → 后端开发 ∥ 前端开发 → 测试。"
        if has_static:
            tip = ("图里现在只有「简单前端」（simple-frontend），它只产出 HTML/CSS/JS 静态页。"
                   "建议改用内置「复杂项目」模板，或把「简单前端」换成 架构师 + 后端开发 + 前端开发 + 测试。")
        return (f"这张图产不出后端：没有「后端开发」节点（backend-executor），"
                f"而 PRD 判定为「前后端分离」。这样跑下去 QA 大概率判不通过（交付物只有前端）。{tip}")
    if shape == "纯前端" and has_backend:
        return ("PRD 判定为「纯前端」，但图里有「后端开发」节点，会产出与需求不符的后端代码。"
                "建议改用内置「简单项目」模板。")
    return ""


async def run_workflow(
    db,
    *,
    project,
    user_id: int,
    workflow: dict,
    seeds: Optional[dict] = None,
    ai=None,
    round_no: int = 1,
    log_dir: str = "",
    stop_after: str = "",
    preset: Optional[dict] = None,
    incremental: bool = False,
) -> dict:
    """
    执行一张工作流图，返回 {workflow, round_no, steps:[...], node_results:{...}, paused_at}。
    失败策略：任一节点 FAILED 即中止后续（已并行节点等本波跑完）。

    「一张图分两段跑」（人工审批卡在图中间）就靠这两个参数：
        stop_after : 只跑 `stop_after` 节点及其全部上游（= PRD 段），跑完返回而不碰下游；
                     返回里带 `paused_at`，调用方据此把项目置为"待审批"。
        incremental: **增量修改**模式（真机需求）：代码节点的工作目录里预置上一版代码，
                     并在执行要求里明确"在现有文件上局部修改、保留未涉及的功能"。
                     src/ 是否清空由调用方（orchestrator）决定，这里只负责"给 Agent 看什么"。
        preset     : 这些节点【不执行、不建步骤行】，直接把给定输出注入下游 —— 审批通过后
                     重跑整张图时，用它把已经跑过的 PRD 段接上，从而 PM 全程只跑一次。
    """
    wf = validate_workflow(workflow)
    ai = ai or _default_ai
    seeds = {k: str(v) for k, v in (seeds or {}).items()}
    preset = dict(preset or {})

    project_dir = file_helper.get_project_dir(user_id, project.id)
    code_dir_name = next((n["code_dir"] for n in wf["nodes"] if n.get("code_dir")), "src")
    src_abs = os.path.join(project_dir, code_dir_name)
    log_dir = log_dir or file_helper.get_log_dir(user_id, project.id)
    ctx = BindContext(seeds, {}, src_abs)
    # 决策源与取值闭集：从图里推导（谁被 when 引用、必须给出哪些取值）
    decisions = derive_decisions(wf)

    nodes = list(wf["nodes"])
    node_name = {n["id"]: n.get("name", n["id"]) for n in nodes}
    node_ids = [n["id"] for n in nodes]
    states = {nid: "PENDING" for nid in node_ids}        # PENDING/RUNNING/SUCCESS/SKIPPED/FAILED
    step_rows = {nid: None for nid in node_ids}
    node_verdicts: dict = {}                             # node id → (运行验证结论, 真跑次数)
    node_skill: dict = {}                                # node id → 绑定的技能名（供总览显示）
    counter = steps_crud.next_step_no(db, project.id, round_no)

    # —— 分段与预置（人工审批卡在图中间；见 run_workflow 文档）——
    results: dict = {}
    for nid in preset:
        if nid not in node_ids:
            raise WorkflowError(f"preset 里的节点 {nid!r} 不在这张图里（图被改过？）")
    if stop_after and stop_after not in node_ids:
        raise WorkflowError(f"stop_after 节点 {stop_after!r} 不在这张图里")
    run_ids = ancestors_of(nodes, stop_after) if stop_after else set(node_ids)
    pending_ids = [nid for nid in node_ids if nid in run_ids and nid not in preset]
    paused_at = stop_after if (stop_after and len(run_ids) < len(node_ids)) else ""

    for nid in preset:
        # 预置节点 = 上一段已经跑过了（或有现成产物）：不建步骤行、不写日志、不执行，
        # 只把输出接进上下文，让下游照常引用它（如 {"ref": "pm.output"}）。
        states[nid] = "SUCCESS"
        ctx.outputs[nid] = preset[nid]
        results[nid] = {"result": preset[nid], "artifact_path": preset[nid].get("artifact_path"),
                        "preset": True}

    _code_node_cache: dict = {}

    def _node_is_code(nd_) -> bool:
        """这个节点是不是"写代码的"（按它自己声明的输出契约判，与 _run_node 里同一个函数）。

        用途：给下游挑 prd_content 时跳过代码类产出 —— 上游是写代码的，产出的是代码不是需求。
        判定要查 agents 表，按节点 id 缓存一次，别在 BFS 里反复查。
        """
        nid = nd_["id"]
        if nid not in _code_node_cache:
            try:
                ag = _agent_row(nd_)
                skill, _mode, _extra = node_inputs.resolve_run(ag)
                kind = _contract_kind(skill, nd_.get("output_kind", ""), ag.output_kind or "")
                _code_node_cache[nid] = kind in CODE_KINDS
            except Exception:            # 角色解析不了等异常交给 _run_node 正常报错，这里只当"不是代码节点"
                _code_node_cache[nid] = False
        return _code_node_cache[nid]

    def _agent_row(nd):
        """节点 → agents 表一行。三种引用方式，依次尝试。

        第三种是给画布用的：前端连线时只知道 `role_key`（拖进来的是"我的 Agent"，
        它拿不到 user_id），所以内置里找不到就再到**项目属主**的自定义里找。

        ⚠️ 判据是"id 的值有没有效"，不是"有没有 id 这个键"：画布保存的图里节点是
        `{"id": None, "role_key": "pm", "user_id": None}` —— 有 id 键但值是 null。
        以前只判 `"id" in spec`，于是 `int(None)` 抛 TypeError，项目一开跑就 FAILED，
        连一行步骤记录都留不下（p49 就是这么废掉的）。所以 id 为空时按 role_key 查。
        """
        spec = nd.get("agent") or {}
        aid = spec.get("id")
        if aid not in (None, "", 0, "0"):
            row = agents_crud.get_by_id(db, int(aid))
            if not row:
                raise WorkflowError(f"agents 注册表找不到节点 {nd['id']} 的 agent: {spec}")
            return row

        role_key = (spec.get("role_key") or "").strip()
        if not role_key:
            raise WorkflowError(f"节点 {nd['id']} 的 agent 既没有有效 id 也没有 role_key: {spec}")
        row = agents_crud.get_by_role_key(db, role_key, user_id=spec.get("user_id"))
        if not row and spec.get("user_id") is None:
            row = agents_crud.get_by_role_key(db, role_key, user_id=user_id)
        if not row:
            raise WorkflowError(f"agents 注册表找不到节点 {nd['id']} 的 agent: {spec}")
        return row

    async def _run_node(nd) -> dict:
        """执行一个节点（已建 RUNNING 行）。"""
        row_ = step_rows[nd["id"]]
        agent = _agent_row(nd)

        # —— 输入组装（一条路：按技能声明的 input[] 逐个填）——
        # 自定义提示词 Agent 会被换算成 generic-prompt-agent，它的提示词变成一个输入值。
        skill_id, mode, extra = node_inputs.resolve_run(agent)
        explicit = {k: ctx.bind(v) for k, v in nd.get("inputs", {}).items()}
        # prd_content 跟着**图**走：取离这个节点最近的上游 PRD 产出（没有才用种子 = 审批通过的那份）。
        # 这样"审批之后让 PM 再出一版"的图里，下游读到的是新版 PRD，而不是第一版。
        prd_from = nearest_prd_output(nodes, nd, ctx.outputs, is_code_node=_node_is_code)
        node_seeds = dict(ctx.seeds)
        if prd_from:
            node_seeds["prd_content"] = (ctx.outputs.get(prd_from) or {}).get("output") or ""
        auto = node_inputs.assemble(
            skill_id, extra=extra, seeds=node_seeds,
            upstream=_upstream_block(ctx, nd, node_name),
            project_title=getattr(project, "title", ""),
            output_kind=nd.get("output_kind", "") or (agent.output_kind or ""),
        )
        inputs = {**auto, **explicit}           # 节点显式声明优先；其余（含可选输入）自动补齐

        # 提示词里写了平台填不了的名字（实测踩过 {{project_context}}）：填完之后还留在文本里的
        # 就是"没填上"的。它不会报错、会静默进 prompt，所以必须写进运行日志让人看见。
        unfilled = node_inputs.unfilled_placeholders(inputs)

        # 「这个节点会不会产出代码」只判一次，三处共用：
        #   ① 要不要装 .selftest.sh  ② 要不要追加真跑要求
        # ⚠️ 这两件事必须同条件，否则会出现"要求 Agent 跑脚本、脚本却不在"（真机实测踩到：
        #    编排官生成的节点没声明 code_dir，脚本没装，Agent 只好自己写一个 .selftest.sh）
        kind = _contract_kind(skill_id, nd.get("output_kind", ""), agent.output_kind or "")
        is_code_node = kind in CODE_KINDS


        # 代码类节点统一追加真跑要求（节点自己写的 task_note 保留在前面）
        task_note = nd.get("task_note", "")
        if is_code_node:
            task_note = f"{task_note}\n{RUN_NOTE}".strip()
            if incremental:
                # 别依赖 PRD 里出现某个标记（那条路从来没生效过）：由引擎**显式**说明这是迭代
                task_note = f"{task_note}\n{INCREMENTAL_NOTE}".strip()
        # 上游产出**必须真的进 prompt**：技能正文里没写 {{input.x}} 占位符的输入会被
        # ai_client 丢掉（只有正文里引用了才注入），而"只声明 prd_content"的技能
        # （simple-frontend / qa-workflow 等）**拿不到别人的产出** ——
        # 实测 p34：图是 PM → 我的审PM(自定义) → 简单前端，那份审查意见压根没进前端节点的 prompt，
        # "审了等于没审"。这里把**不是**正式输入的那些上游产出补进执行要求（永远会被渲染）。
        upstream_note = _upstream_block(ctx, nd, node_name, exclude={prd_from} if prd_from else ())
        if upstream_note:
            task_note = (f"{task_note}\n\n===== 上游节点的产出（图上连给你的，必须一并考虑）=====\n"
                         f"{upstream_note}").strip()

        # 自己就是产出 PRD 的节点、且上游还有一份 PRD → 它是"改版"而不是"初版"。
        # 上一版正文必须**贴在 task_note 里**，不能只靠 `prd_content` 输入：
        # pm-workflow 这类技能只声明了 `user_requirement`，而输入取值里"种子命中就返回种子"，
        # 于是上游文本根本进不了它的 prompt（实测：第二版 PRD 就写成了跟第一版一样）。
        if prd_from and _is_prd_node(nd):
            prior = (ctx.outputs.get(prd_from) or {}).get("output") or ""
            task_note = (f"{task_note}\n{REVISE_PRD_NOTE}\n\n"
                         f"===== 上一版 PRD（来自「{node_name.get(prd_from, prd_from)}」）=====\n{prior}").strip()
        # 决策源：把"必须给出的取值闭集"告诉它（闭集是从图里推导的，不是技能自己猜的）
        dec = decisions.get(nd["id"])
        if dec:
            task_note = f"{task_note}{decision_note(dec)}".strip()
        # 节点的提示词已作为输入值时，别再当正文拼一遍
        body_prompt = "" if extra.get("system_prompt") else (agent.system_prompt or nd.get("system_prompt", ""))

        tmp = tempfile.mkdtemp(prefix=f"wf_{nd['id']}_")
        if is_code_node:
            _install_selftest(tmp)              # 代码节点才需要"真跑"脚本
            # ★ 增量修改：把上一版代码**预置进工作目录**，Agent 才能看到并改真实文件。
            #   持久化是按文件合并覆盖（`_persist_code`），所以"只改动了几个文件"也能正确落回 src/。
            if incremental and os.path.isdir(src_abs):
                for item in os.listdir(src_abs):
                    s, d = os.path.join(src_abs, item), os.path.join(tmp, item)
                    if os.path.isdir(s):
                        shutil.copytree(s, d, dirs_exist_ok=True)
                    else:
                        shutil.copy2(s, d)

        def _fail_node(error_code, error) -> None:
            """节点失败的**唯一出口**：落 FAILED 行 + 写日志 + 抛错。

            会话失败和决策缺失都走这里，保证两者的日志/留痕行为完全一致。
            """
            steps_crud.finish_step(
                db, row_, steps_crud.STATUS_FAILED,
                session_id=res.get("session_id"),
                elapsed_ms=int(round((res.get("elapsed_seconds") or 0) * 1000)),
                error_code=error_code, error=error,
            )
            record_ = _read_runs(tmp)
            shutil.rmtree(tmp, ignore_errors=True)
            n_ = _log_node(project_dir, nd, agent, row_, round_no, res,
                           steps_crud.STATUS_FAILED, record=record_, artifacts=[],
                           error_code=error_code or "", inputs=inputs, unfilled=unfilled)
            node_verdicts[nd["id"]] = (runlog.verdict_of(res.get("output") or "", record_), n_)
            raise WorkflowError(f"节点 {nd['id']} 执行失败: {error}")

        try:
            res = await ai.arun_agent(
                skill_id=skill_id,
                system_prompt=body_prompt,
                mode=mode,
                inputs=inputs,
                output_kind=nd.get("output_kind", ""),
                agent_output_kind=agent.output_kind or "",
                task_note=task_note,
                workdir=tmp,
                log_dir=log_dir,
                # ★ 登记正在跑的会话进程：客户在页面上点「终止运行」时按这个找到并杀掉
                run_key=project.id,
                run_step_no=row_.step_no,
                run_step_name=nd.get("name", ""),
                # 文本优先：格式不对 ≠ 没产出（ai_client 已不再因格式判失败，这里只留语义说明）。
            )
        finally:
            pass  # 产物文件在 tmp 中，待落盘后再删
        if not res.get("success"):
            _fail_node(res.get("error_code"), res.get("error"))

        # —— 决策校验（本次修复的核心）——
        # 这个节点的产出是"别的节点要不要参与"的依据。取不到合法取值 → **硬失败**：
        #   内容格式不对可以容忍（下游是 LLM），但决策给不出，引擎就不知道该跑哪些节点了。
        # 以前这里没有校验，`when` 判不出会静默跳过两条分支 → 零代码还报 COMPLETED。
        if dec:
            why = check_decision(res, dec)
            if why:
                _fail_node(ROUTE_MISSING, why)

        artifact_path = None
        record = ""
        artifacts: list = []
        try:
            if nd.get("code_dir"):
                artifacts = _persist_code(res, tmp, os.path.join(project_dir, nd["code_dir"]), project_dir)
                if not artifacts:
                    raise WorkflowError(f"节点 {nd['id']} 未产出代码文件")
                artifact_path = nd["code_dir"] + "/"
            elif nd.get("artifact_file"):
                target = os.path.join(project_dir, nd["artifact_file"])
                os.makedirs(os.path.dirname(target) or project_dir, exist_ok=True)
                with open(target, "w", encoding="utf-8") as f:
                    f.write(res.get("output") or "")
                artifact_path = nd["artifact_file"]
                artifacts = [nd["artifact_file"]]     # 文本类产物也进运行日志的产物清单
            else:
                # 节点没声明落盘方式（画布拖出来的节点就是这样）→ 按**输出形态**决定，别丢东西
                artifact_path, artifacts = _persist_by_shape(
                    res, tmp, project_dir, src_abs, code_dir_name, nd)
            record = _read_runs(tmp)            # 真跑记录：tmp 一删就没了，必须先读出来
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        elapsed_ms = int(round((res.get("elapsed_seconds") or 0) * 1000))
        # 契约没解析出来（该 JSON 给了散文之类）：**不阻断**，但要让"运行日志/项目BUG"里看得见。
        contract = res.get("contract") or {}
        soft = "" if contract.get("ok", True) else (contract.get("error") or "输出不符合技能声明的格式")
        steps_crud.finish_step(
            db, row_, steps_crud.STATUS_SUCCESS,
            session_id=res.get("session_id"), elapsed_ms=elapsed_ms,
            artifact_path=artifact_path,
            error_code=SOFT_CONTRACT_CODE if soft else None,
            error=soft or None,
        )
        n_runs = _log_node(project_dir, nd, agent, row_, round_no, res,
                           steps_crud.STATUS_SUCCESS, artifact_path, record, artifacts,
                           inputs=inputs, unfilled=unfilled)
        node_verdicts[nd["id"]] = (runlog.verdict_of(res.get("output") or "", record), n_runs)
        return {"result": res, "artifact_path": artifact_path}

    # —— 按依赖波次调度 ——
    # pending 只含"本段要跑的节点"：stop_after 之外的下游留给下一段，preset 的节点已经在上面接好了。
    pending = list(pending_ids)
    error: Optional[str] = None
    while pending and error is None:
        # ★ 终止检查点（每个波次开始前）：客户点了「终止运行」就**不再启动新节点**。
        #   只杀当前会话是不够的 —— 引擎会接着调度下一个节点、又起一个新会话，
        #   用户会看到"我按了终止，它又跑起来了"。
        if run_registry.is_aborted(project.id):
            for nid in list(pending):
                row_ = step_rows.get(nid)
                if row_ is not None:
                    steps_crud.finish_step(db, row_, steps_crud.STATUS_FAILED,
                                           error=ABORTED_STEP_MSG)
                states[nid] = "FAILED"
                pending.remove(nid)
            raise run_registry.RunAborted("用户手动终止")

        wave = [nid for nid in pending
                if all(states[d] in ("SUCCESS", "SKIPPED") for d in _deps(nodes, nid))]
        if not wave:
            raise WorkflowError(f"工作流存在循环依赖或无法推进: {pending}")

        runners = []
        for nid in wave:
            nd = next(n for n in nodes if n["id"] == nid)
            pending.remove(nid)
            states[nid] = "RUNNING"
            nd_agent = _agent_row(nd) if nd.get("agent") else None
            node_skill[nid] = (nd_agent.skill_id or "") if nd_agent else ""
            step_rows[nid] = steps_crud.create_step(
                db, project.id, (nd_agent.id if nd_agent else None),
                nd.get("name", nid), round_no=round_no, step_no=counter,
            )
            counter += 1

            # 条件跳过（不实例化 agent）
            when = nd.get("when")
            if when and str(ctx.bind({"ref": when["ref"]}) or "") != str(when["eq"]):
                steps_crud.finish_step(db, step_rows[nid], steps_crud.STATUS_SKIPPED)
                states[nid] = "SKIPPED"
                results[nid] = {"skipped": True}
                continue
            # 真正要跑了：先置 RUNNING，前端轮询才看得到"当前由谁在跑"
            steps_crud.start_step(db, step_rows[nid])
            runners.append((nid, _run_node(nd)))

        if runners:
            outcome = await asyncio.gather(*(c for _, c in runners), return_exceptions=True)
            for (nid, _), out in zip(runners, outcome):
                if isinstance(out, BaseException):
                    # 客户按了终止：这个节点的失败原因是"人按停的"，不是系统故障 —— 文案要能
                    # 让客户看懂（前端据此不显示成红色"失败"）。
                    aborted = run_registry.is_aborted(project.id)
                    if not aborted:
                        # 非预期异常（会话失败/决策缺失已由 _fail_node 落过错误码和原因了）。
                        # 兜底也别让原因空着：前端最怕的就是一个红色步骤却没有任何文案。
                        # 打一份栈：否则这类 bug 只剩一句 str(e)，排查全靠猜（本次就吃过一次）。
                        traceback.print_exception(type(out), out, out.__traceback__)
                    row_ = step_rows[nid]
                    states[nid] = "FAILED"
                    steps_crud.finish_step(db, row_, steps_crud.STATUS_FAILED,
                                           error=ABORTED_STEP_MSG if aborted
                                           else (row_.error or str(out)))
                    error = error or (ABORTED_STEP_MSG if aborted else str(out))
                else:
                    states[nid] = "SUCCESS"
                    ctx.outputs[nid] = out["result"]   # 供后续节点输入引用
                    results[nid] = out

    if error:
        raise WorkflowError(error)

    def _skill_of_step(step) -> str:
        """技能名从库里按 agent_id 反查。

        为什么不只用内存里的 node_skill：同一轮里可能有**多次** run_workflow（编排官就分两阶段），
        总览会把本轮的步骤行全列出来，而那些更早的步骤不在本次内存状态里 —— 不查库就会显示 `—`。
        """
        row = agents_crud.get_by_id(db, step.agent_id) if step.agent_id else None
        if row:
            return row.skill_id or ""
        return step_meta.get(step.step_no, (("", 0), ""))[1]

    # step_no → 运行验证结论，供调用方（orchestrator 的总览）直接取用
    step_meta = {
        step_rows[nid].step_no: (v, node_skill.get(nid, ""))
        for nid, v in node_verdicts.items() if step_rows.get(nid)
    }
    return {
        "incremental": bool(incremental),
        "workflow": wf["name"],
        "round_no": round_no,
        # 非空 = 本段在闸门处停下（人工审批点），下游节点留到下一段跑
        "paused_at": paused_at,
        "steps": [
            {"id": s.id, "step_no": s.step_no, "name": s.name, "status": s.status,
             "agent_id": s.agent_id, "session_id": s.session_id,
             "elapsed_ms": s.elapsed_ms, "artifact_path": s.artifact_path,
             "error_code": s.error_code, "error": s.error,
             "skill": _skill_of_step(s),
             "verdict": step_meta.get(s.step_no, (("", 0), ""))[0][0],
             "runs": step_meta.get(s.step_no, (("", 0), ""))[0][1]}
            for s in steps_crud.get_steps(db, project.id, round_no)
        ],
        "node_results": {
            nid: {
                "success": r.get("result", {}).get("success", False) if not r.get("skipped") else False,
                "skipped": r.get("skipped", False),
                "session_id": r.get("result", {}).get("session_id") if not r.get("skipped") else None,
                "elapsed_ms": int(round((r.get("result", {}).get("elapsed_seconds") or 0) * 1000))
                if not r.get("skipped") else 0,
                "artifact_path": r.get("artifact_path"),
                "parsed": r.get("result", {}).get("parsed") if not r.get("skipped") else None,
                "level": _parsed_level(r) if not r.get("skipped") else None,
                "error_code": r.get("result", {}).get("error_code") if not r.get("skipped") else None,
                "error": r.get("result", {}).get("error") if not r.get("skipped") else None,
            }
            for nid, r in results.items()
        },
    }


def _deps(nodes: list, nid: str) -> list:
    nd = next(n for n in nodes if n["id"] == nid)
    return nd.get("deps", [])


def _parsed_level(r: dict):
    """取节点契约解析结果里的 level（仅 dict 型 parsed 有该字段；数组/None 返回 None）。"""
    parsed = r.get("result", {}).get("parsed")
    return parsed.get("level") if isinstance(parsed, dict) else None
