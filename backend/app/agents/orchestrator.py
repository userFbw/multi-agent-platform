"""orchestrator.py —— Agent 调度中心（兼容外壳：老接口 + 新引擎）

对外保持 5 个方法名/签名不变（projects.py 路由依赖）：step_1_run_pm / step_2_handle_approval /
step_3_run_dev / step_3_revise_dev / run_qa_stage。它自己不做编排，只做四件事：

1. 查表：_resolve_skill_id 从 agents 表把 role_key 解析成 skill_id；
2. 状态流转：INITIAL → RUNNING → PENDING_APPROVAL → RUNNING → COMPLETED / FAILED；
3. 跑图：图是**建项目时选定**的（projects.workflow_id / workflow_name），整条链分两段执行 ——
   ① 创建项目跑 PM 段（run_workflow 的 `stop_after`，停在人工审批闸门）；
   ② 审批通过跑其余段（`preset` 把已跑好的 PRD 接上，PM 不重跑）。
   再把结果折叠登记回 project_agents（_map_steps_to_agents）—— 老接口靠这张表取产物文件；
4. 收尾：_finalize 打包 ZIP 并置 COMPLETED。
"""

import asyncio
import json
import os
import shutil
import tempfile
import time
import traceback
from pathlib import Path

from app.core.config import settings  # noqa: F401
from app.db.engine import SessionLocal
from app.db.crud import agents_crud, project_steps_crud
from app.db.crud.projects_crud import get_project_by_id, update_project_status_or_zip
from app.db.crud.project_agents_crud import get_project_agents, upsert_by_role
from app.db.models.projects_model import ProjectDB
from app.agents import runlog
from app.agents import workflow_engine
from app.agents.ai_client import ai_client
from app.agents.workflow_engine import (run_workflow, resolve_workflow,
                                         validate_workflow, WorkflowError)
from app.agents import run_registry
from app.runner import process as app_runner
from app.storage.file_helper import file_helper

ROLE_PM = "PM"
ROLE_ARCHITECT = "ARCHITECT"
ROLE_BACKEND = "BACKEND"
ROLE_FRONTEND = "FRONTEND"
ROLE_DEV = "DEV"
ROLE_QA = "QA"


class AgentRunError(RuntimeError):
    """单步 agent 契约/运行失败。"""


def prd_with_feedback(prd_text: str, feedback: str = "") -> str:
    """把「审批时提的补充要求」并进 PRD 正文。

    为什么不只写进 `user_requirement`：只声明 `prd_content` 的技能（simple-frontend、
    code-reviewer 等）拿不到 user_requirement —— 意见放在需求描述里它们**根本看不见**。
    实测用户会在"同意"时补一句要求，以前这句话被直接丢掉（只有驳回才用）。
    """
    feedback = (feedback or "").strip()
    if not feedback:
        return prd_text
    return f"{prd_text}\n\n## 审批时的补充要求（必须一并落实）\n{feedback}"


def read_project_prd(db, project) -> str:
    """读项目里已产出的 PRD 正文（PM 行登记的路径）；没有则返回 ""。

    两种模式都要用它：workflow 模式是"审批通过后接着开发"，agent 模式是"编排官读它出图"。
    """
    pm_row = next((a for a in get_project_agents(db, project.id) if a.role == ROLE_PM), None)
    if not pm_row or not pm_row.path:
        return ""
    try:
        return file_helper.read_file_by_db_path(project.user_id, project.id, pm_row.path)
    except Exception:  # noqa: BLE001 —— 物理文件丢了就当没有，由调用方决定怎么报
        return ""


class StepOrchestrator:

    # ---------------- 基础工具 ----------------
    @staticmethod
    def _log(msg: str) -> None:
        print(f"[orchestrator] {msg}")

    def _fail(self, db, project_id: int, msg: str) -> None:
        """失败的**唯一收口**。

        ⚠️ 客户在页面上按了「终止运行」时**不能算失败** —— 那是他自己按停的，界面该显示
        「已终止」（STOPPED）而不是红色「失败」；否则答辩时看起来像系统崩了。
        所以这里先看注册表的中止标志，分流到 `_aborted`。
        """
        if run_registry.is_aborted(project_id):
            self._aborted(db, project_id, msg)
            return
        try:
            update_project_status_or_zip(db, project_id, status="FAILED")
            self._log(f"项目 {project_id} 置为 FAILED: {msg}")
        except Exception as e:  # noqa: BLE001
            self._log(f"置 FAILED 失败: {e}")

    def _aborted(self, db, project_id: int, msg: str) -> None:
        """客户手动终止的收尾：项目置 STOPPED + 在运行日志里留一份说明 + **清掉中止标志**。

        清标志这一步是必须的：不清的话，客户点一次终止，**之后每次重跑都会被立刻再次终止**
        （`run_registry.clear_abort` 有专门的回归测试锁着）。
        已完成的产物（PRD、跑完的步骤日志、代码）一律保留，客户可以从项目页「重新跑开发链」
        接着跑（复用 revise，PRD 不重跑）。
        """
        try:
            project = get_project_by_id(db, project_id)
            pdir = self._project_dir(project.user_id, project_id) if project else ""
            if pdir:
                logdir = Path(pdir) / "运行日志"
                logdir.mkdir(parents=True, exist_ok=True)
                steps = project_steps_crud.get_steps(
                    db, project_id, project_steps_crud.get_latest_round(db, project_id)) or []
                done = [s for s in steps if s.status == "SUCCESS"]
                unfinished = [s for s in steps if s.status == "FAILED"]
                lines = [
                    "# 本次运行被用户手动终止",
                    "",
                    "- 终止方式：客户在项目页点「终止运行」（不是系统故障，也不是超时）",
                    f"- 项目状态：已终止（STOPPED）",
                    f"- 已跑完的步骤：{len(done)} 个（产物全部保留）",
                    # 去重：同一张图重跑过几次就有几行 FAILED，重复列名字很难看（真机见过
                    # "架构拆解任务书、架构拆解任务书"）
                    f"- 被终止时未跑完的步骤："
                    + ("、".join(dict.fromkeys(s.name or f"步骤{s.step_no}" for s in unfinished)) or "无"),
                    "",
                    "> 想接着跑：在项目页点「重新跑开发链」——PRD 不会重跑，从架构拆解那一步往后继续，",
                    "> 已完成的产物和代码目录会被重新生成覆盖。",
                ]
                (logdir / "99-本次运行被终止.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            update_project_status_or_zip(db, project_id, status="STOPPED")
            self._log(f"项目 {project_id} 已被用户手动终止，置为 STOPPED")
        except Exception as e:  # noqa: BLE001
            self._log(f"置 STOPPED 失败: {e}")
        finally:
            run_registry.clear_abort(project_id)

    @staticmethod
    def _resolve_skill_id(db, role_key: str) -> str:
        row = agents_crud.get_by_role_key(db, role_key, user_id=None)
        if not row or not row.skill_id:
            raise AgentRunError(
                f"agents 注册表缺少内置角色 {role_key!r} 的 skill 绑定，"
                f"请先在 backend/ 目录下跑 python3 app/db/init_db.py"
            )
        return row.skill_id

    async def _call_agent(
        self, db, role_key: str, inputs: dict,
        output_kind: str = "", task_note: str = "", log_dir: str = "",
    ) -> dict:
        skill_id = self._resolve_skill_id(db, role_key)
        workdir = tempfile.mkdtemp(prefix=f"dsh_{role_key}_")
        try:
            self._log(f"拉起 {role_key}（skill={skill_id}）...")
            res = await ai_client.arun_agent(
                skill_id=skill_id,
                inputs=inputs,
                workdir=workdir,
                output_kind=output_kind,
                task_note=task_note,
                log_dir=log_dir,
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        if not res.get("success"):
            raise AgentRunError(f"{role_key} 执行失败: {res.get('error')}")
        return res

    @staticmethod
    def _project_dir(user_id: int, project_id: int) -> str:
        return file_helper.get_project_dir(user_id, project_id)

    @staticmethod
    def _log_dir(user_id: int, project_id: int) -> str:
        """DSH 会话原始日志目录（backend/data/logs/u*/p*/，在 exports 之外）。"""
        return file_helper.get_log_dir(user_id, project_id)

    # ---------------- 图从哪来 / 闸门怎么接 ----------------
    def _project_workflow(self, db, project, override: dict = None) -> dict:
        """这次跑哪张图：override（显式换图）> 项目选定的那张 > 默认模版。

        图在建项目时就写进 projects.workflow_id / workflow_name，所以审批与迭代**自动沿用**，
        不需要前端每次把选择带回来 —— 这也是"迭代修改悄悄换回默认图"那个 bug 的根因修法。
        """
        if override:
            return override
        # ★ agent 模式：编排官当场出的图优先 —— 重跑/迭代要原样复用它，
        #   否则会退回模版图、把编排官排的自定义节点悄悄丢掉（真机踩到，p46）
        planned = getattr(project, "planned_workflow", None)
        if isinstance(planned, dict) and planned.get("nodes"):
            try:
                return validate_workflow(planned)
            except WorkflowError as e:
                self._log(f"编排官出的图已失效（{e}），退回项目选定的模版")
        try:
            return resolve_workflow(
                db, user_id=project.user_id,
                workflow_id=project.workflow_id, workflow_name=project.workflow_name or "",
            )
        except WorkflowError as e:
            raise AgentRunError(f"项目选定的工作流已不可用: {e}")

    @staticmethod
    def _prd_preset(gate: str, prd_text: str, pm_row) -> dict:
        """把「已经跑过的 PRD 段」接回图里：PM 节点不执行，下游照常引用 `pm.output`。

        形状就是一次 ai_client 调用的结果，这样引擎侧不需要为它开特例。
        """
        if not gate:
            return {}
        return {gate: {
            "success": True, "output": prd_text, "parsed": None,
            "session_id": getattr(pm_row, "session_id", None),
            "elapsed_seconds": 0.0, "files": [], "contract": {},
            "artifact_path": getattr(pm_row, "path", "") or "",
            "error": None, "error_code": None, "attempts": 0,
        }}

    def _segment2_preset(self, db, project, wf: dict, gate: str,
                         prd_text: str, pm_row) -> dict:
        """第二段的预置集合 = 闸门节点 + **第一段里跟它一起跑过的上游节点**。

        一次生成被切成两段（PRD 段 / 审批之后的其余段）。以前只把闸门节点（PM）预置进第二段，
        于是画在 PM **前面**的节点（比如"用户理解人员"）在第一段跑一次、审批之后又跑一次
        —— 真机就是同一个自定义角色跑了两次（p53 的步骤 #1 与 #4），白费额度、步骤列表也重复。

        这里把闸门的上游一并预置掉，输出从**已经落盘的产物**读回来（按节点名找最近一次成功的
        步骤行），这样下游引用（如 PM 的输入指向它）照常拿得到内容。

        产物找不到或读不出来就跳过预置 —— 宁可贵一点重跑一次，也不能少给下游输入。
        """
        preset = self._prd_preset(gate, prd_text, pm_row)
        nodes = (wf or {}).get("nodes") or []
        if not gate or not nodes:
            return preset
        ups = workflow_engine.ancestors_of(nodes, gate) - {gate}
        if not ups:
            return preset

        by_id = {n.get("id"): n for n in nodes}
        # 节点名 → 最近一次成功的步骤行。同一张图里节点名唯一（重命名功能就是为区分同名节点加的）。
        latest: dict = {}
        try:
            for rnd in range(project_steps_crud.get_latest_round(db, project.id), 0, -1):
                for row in project_steps_crud.get_steps(db, project.id, rnd):
                    if row.status == "SUCCESS" and row.artifact_path and row.name not in latest:
                        latest[row.name] = row
        except Exception as e:  # noqa: BLE001 —— 读不到就退回旧行为
            self._log(f"读已有步骤失败（{e}），PRD 段上游按旧行为重跑")
            return preset

        for nid in sorted(ups):
            nd = by_id.get(nid) or {}
            name = nd.get("name") or nid
            row = latest.get(name)
            if not row:
                self._log(f"PRD 段上游节点「{name}」没有可复用的产物，第二段会重跑它")
                continue
            try:
                text = file_helper.read_file_by_db_path(project.user_id, project.id, row.artifact_path)
            except Exception as e:  # noqa: BLE001
                self._log(f"PRD 段上游节点「{name}」的产物读不出来（{e}），第二段会重跑它")
                continue
            parsed = None
            if (nd.get("output_kind") or "") == "json_array":
                try:
                    parsed = json.loads(text)
                except Exception:  # noqa: BLE001
                    parsed = None
            preset[nid] = {
                "success": True, "output": text, "parsed": parsed,
                "session_id": row.session_id,
                "elapsed_seconds": (row.elapsed_ms or 0) / 1000.0,
                "files": [], "contract": {}, "artifact_path": row.artifact_path,
                "error": None, "error_code": None, "attempts": 0,
            }
            self._log(f"PRD 段上游节点「{name}」第一段已跑过，第二段不再重跑")
        return preset

    @staticmethod
    def _prd_text_of(project, result: dict, gate: str) -> str:
        """从第一段结果里取 PRD 正文：先看闸门节点的产物路径，再兜底找 PRD.md。"""
        node_results = result.get("node_results") or {}
        path = (node_results.get(gate) or {}).get("artifact_path") or ""
        if not path:
            path = next((v.get("artifact_path") or "" for v in node_results.values()
                         if (v.get("artifact_path") or "").endswith("PRD.md")), "")
        if not path:
            return ""
        try:
            return file_helper.read_file_by_db_path(project.user_id, project.id, path)
        except Exception:  # noqa: BLE001 —— 取不到就当空串，下面的映射会据此判断"没有 PRD"
            return ""

    # ---------------- 核心：一次"开发链"（workflow engine 驱动） ----------------
    async def _dev_chain(self, db, project, prd_text: str, workflow: dict = None,
                         preset: dict = None, incremental: bool = False) -> dict:
        """
        PRD 在手后：调用 workflow engine 执行图的**审批闸门之后那一段**。

        workflow 为空 → 用项目选定的图（再退到默认模版）；调用方可以传一张图显式换掉。
        preset 非空 → 图里的 PRD 节点视为已完成，**不重跑 PM**（PRD 已经由第一段产出、
        并且用户已经审批过了）。

        返回 workflow 执行结果（含 steps，供 _finalize 写总览）。
        """
        project_dir = self._project_dir(project.user_id, project.id)
        src_abs = os.path.join(project_dir, "src")
        log_dir = self._log_dir(project.user_id, project.id)

        # 1) 代码目录：**重新生成**才清空；**增量修改**保留上一版（Agent 会在其基础上改）
        if incremental:
            os.makedirs(src_abs, exist_ok=True)
            self._log("增量修改模式：保留 src/ 上一版代码，Agent 将在其基础上局部修改")
        else:
            if os.path.exists(src_abs):
                shutil.rmtree(src_abs)         # 幂等清空
            os.makedirs(src_abs, exist_ok=True)

        # 2) 获取当前轮次
        round_no = project_steps_crud.get_latest_round(db, project.id)

        # 3) 跑图（第二段）
        chosen = self._project_workflow(db, project, workflow)
        self._log(f"启动 workflow engine（round_no={round_no}，workflow={chosen.get('name', '?')}）...")
        snap_before = self._snapshot_tree(src_abs) if incremental else {}
        # prd_content 给"直接吃种子"的图留个后路；user_requirement 给含 PM 的自定义图留个后路。
        seeds = {"prd_content": prd_text, "user_requirement": project.description}

        try:
            result = await run_workflow(
                db,
                project=project,
                user_id=project.user_id,
                workflow=chosen,
                seeds=seeds,
                round_no=round_no,
                log_dir=log_dir,
                preset=preset,
                incremental=incremental,
            )
        except WorkflowError as e:
            raise AgentRunError(f"workflow 执行失败: {e}")

        # 4) 从 workflow 结果中提取 level
        level = ""
        for nid, node_result in result.get("node_results", {}).items():
            if node_result.get("level"):
                level = node_result["level"]
                break

        # 5) 老行映射器：把 project_steps 结果折叠登记回 project_agents
        self._map_steps_to_agents(db, project.id, result, prd_text)

        self._log(f"workflow 完成，level={level}，{len(result.get('steps', []))} 步已登记")
        return result

    def _map_steps_to_agents(self, db, project_id: int, result: dict, prd_text: str):
        """
        老行映射器：把 workflow engine 产生的 project_steps 结果
        折叠登记回 project_agents（router 依赖 PM/DEV/QA 行取文件）。
        """
        node_results = result.get("node_results", {})

        def ran(nid: str) -> bool:
            """这个节点真的跑过吗（SKIPPED 的节点不该在 project_agents 里留空行）。"""
            r = node_results.get(nid) or {}
            return bool(r) and not r.get("skipped")


        # 映射 pm 节点
        if ran("pm") and node_results["pm"].get("artifact_path"):
            upsert_by_role(
                db, project_id, ROLE_PM, "Product Manager",
                final_output=prd_text[:200] + "..." if len(prd_text) > 200 else prd_text,
                path=node_results["pm"]["artifact_path"],
                session_id=node_results["pm"].get("session_id"),
                additional_time=node_results["pm"].get("elapsed_ms", 0),
            )

        # 映射 architect 节点
        if ran("architect") and node_results["architect"].get("artifact_path"):
            upsert_by_role(
                db, project_id, ROLE_ARCHITECT, "系统架构师",
                final_output="任务书已生成",
                path=node_results["architect"]["artifact_path"],
                session_id=node_results["architect"].get("session_id"),
                additional_time=node_results["architect"].get("elapsed_ms", 0),
            )

        # 映射 backend 节点
        if ran("backend"):
            upsert_by_role(
                db, project_id, ROLE_BACKEND, "后端开发工程师",
                final_output="后端代码已写入 src/",
                session_id=node_results["backend"].get("session_id"),
                additional_time=node_results["backend"].get("elapsed_ms", 0),
            )

        # 映射 frontend 节点
        if ran("frontend"):
            upsert_by_role(
                db, project_id, ROLE_FRONTEND, "前端开发工程师",
                final_output="前端代码已写入 src/",
                session_id=node_results["frontend"].get("session_id"),
                additional_time=node_results["frontend"].get("elapsed_ms", 0),
            )

        # 映射 simple 节点（简单前端分支）
        if ran("simple"):
            upsert_by_role(
                db, project_id, ROLE_DEV, "Software Developer",
                final_output="简单前端代码已写入 src/",
                path="src/",
                session_id=node_results["simple"].get("session_id"),
                additional_time=node_results["simple"].get("elapsed_ms", 0),
            )
        elif ran("backend") or ran("frontend"):
            # complex 分支：DEV 汇总行（router 靠 role='DEV' 提供代码下载）
            upsert_by_role(
                db, project_id, ROLE_DEV, "Software Developer",
                final_output="代码已写入 src/（complex 链路）",
                path="src/",
            )

        # 映射 qa 节点
        if ran("qa") and node_results["qa"].get("artifact_path"):
            upsert_by_role(
                db, project_id, ROLE_QA, "QA Engineer",
                final_output="测试报告已生成",
                path=node_results["qa"]["artifact_path"],
                session_id=node_results["qa"].get("session_id"),
                additional_time=node_results["qa"].get("elapsed_ms", 0),
            )

    # ---------------- 公共收尾（三条执行路径共用） ----------------
    def finish_project(self, db, project, result: dict = None, prd_text: str = "") -> str:
        """跑完一张图之后的统一收尾 —— orchestrator 主链路 / planner / workflow.execute 共用。

        做四件事：① 折叠登记 project_agents（下游 /prd、/qa-report、/preview-url 靠它取文件）
        ② 写「运行日志/00-总览」 ③ 打包 ZIP ④ 项目置 COMPLETED。
        不做收尾的后果（实测过）：项目停在 RUNNING、前端拿不到任何产物。
        """
        result = result or {}
        self._map_steps_to_agents(db, project.id, result, prd_text)
        self._map_roles_generic(db, project.id, result)
        return self._finalize(db, project, result)

    def _map_roles_generic(self, db, project_id: int, result: dict) -> None:
        """按「这一步实际用了哪个角色」通用登记，兜住 planner / 用户自定义图。

        缘由：`_map_steps_to_agents` 是按**节点 id**（pm / backend / qa…）认的，
        只对内置图有效；planner 生成的图节点 id 是 s1/s2…，一个都对不上，
        于是下游三个接口全 404。这里改成看 project_steps 里每步绑定的 agents 行。

        下游只认三件事，所以这三条必须保证：role='PM' 的 path、role='QA' 的 path、
        role='DEV' 的 path（代码目录，/preview-url 用）。
        """
        for s in result.get("steps") or []:
            if s.get("status") != "SUCCESS" or not s.get("agent_id"):
                continue
            agent = agents_crud.get_by_id(db, s["agent_id"])
            artifact = s.get("artifact_path") or ""
            if not agent or not artifact:
                continue
            role_key = (agent.role_key or "").upper()
            if role_key in ("PM", "QA"):
                upsert_by_role(
                    db, project_id, role_key, agent.name or role_key,
                    path=artifact, session_id=s.get("session_id"),
                )

        code_dir = next((s["artifact_path"] for s in result.get("steps") or []
                         if (s.get("artifact_path") or "").endswith("/")), "")
        if code_dir:
            upsert_by_role(db, project_id, ROLE_DEV, "Software Developer", path=code_dir)

    # QA 报告里"结论"的几种写法（模板是固定的，但也留了兜底，避免格式漂移就认不出）
    _QA_VERDICT_PATTERNS = (
        r"核心测试状态\**[^\n]*?\[?\s*(PASS|FAIL)\s*\]?",
        r"测试状态\**[^\n]*?\[?\s*(PASS|FAIL)\s*\]?",
        r"测试结论\**[^\n]*?\[?\s*(PASS|FAIL)\s*\]?",
        r"测试结果\**[^\n]*?\[?\s*(PASS|FAIL)\s*\]?",
        r"\[QA\]\s*verdict\s*=\s*(PASS|FAIL)",
        r"\[通过\]",          # 中文写法：命中即视为 PASS（下面单独处理）
        r"\[不通过\]",
        r"\[(PASS|FAIL)\]",
    )

    def _qa_verdict(self, db, project) -> tuple:
        """读 QA 报告的结论。返回 (verdict, 依据说明)；认不出时 verdict 为空串。"""
        import re as _re

        row = next((a for a in get_project_agents(db, project.id) if a.role == ROLE_QA), None)
        if not row:
            # 图里**根本没有 QA 节点**（用户自定义图）→ 不是"没通过"，是"没安排审查"：
            # 交不交付由用户自己定，平台不拦，但要记一笔
            return "SKIP", "这张图里没有自动化测试节点（用户自定义图）"
        if not row.path:
            return "", "有测试节点但没有产出报告路径"
        try:
            text = file_helper.read_file_by_db_path(project.user_id, project.id, row.path)
        except FileNotFoundError:
            return "", f"QA 报告文件不在（{row.path}）"
        for pat in self._QA_VERDICT_PATTERNS:
            m = _re.search(pat, text, _re.I)
            if m:
                if "[不通过]" in m.group(0):
                    return "FAIL", f"报告里的 {m.group(0)[:40]!r}"
                if "[通过]" in m.group(0):
                    return "PASS", f"报告里的 {m.group(0)[:40]!r}"
                if m.groups():
                    return m.group(1).upper(), f"报告里的 {m.group(0)[:40]!r}"
        return "", "报告里没有可识别的结论（模板可能变了）"

    def _write_delivery_blocked(self, project, verdict_why: str) -> None:
        """写一份《交付未通过》，让客户一眼看到"为什么没算完成"。"""
        try:
            pdir = self._project_dir(project.user_id, project.id)
            note = Path(pdir) / "运行日志" / "98-交付未通过.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(
                "# 交付未通过（QA 闸门）\n\n"
                "- 判定来源：自动化测试报告里的「核心测试状态」\n"
                f"- 判定依据：{verdict_why}\n"
                "- 项目状态：**未交付**（FAILED）——因为测试报告判定为不通过\n\n"
                "> 平台规则：测试报告的结论是**交付闸门**。判 FAIL 时不会算作已完成，也不会自动启动应用；\n"
                "> 请查看《测试报告》里的缺陷清单，修完（或点「重新跑开发链」让程序员按缺陷清单返工）后再交付。\n",
                encoding="utf-8")
        except Exception as e:  # noqa: BLE001 —— 写说明失败不影响主流程
            self._log(f"写《交付未通过》说明失败（忽略）：{e}")

    # ---------------- 增量修改：改动清单 ----------------
    @staticmethod
    def _snapshot_tree(root: str) -> dict:
        """给代码目录拍快照：{相对路径: 大小}。用于算"这一轮到底改了什么"。"""
        import hashlib

        out = {}
        if not os.path.isdir(root):
            return out
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", "node_modules", ".venv", ".git")]
            for fn in filenames:
                if fn.endswith((".pyc", ".log")):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                try:
                    with open(full, "rb") as f:
                        out[rel] = hashlib.sha256(f.read()).hexdigest()[:12]
                except OSError:
                    continue
        return out

    def _write_change_manifest(self, project, snap_before: dict, round_no: int,
                               src_abs: str) -> dict:
        """写《本轮改动》清单：新增 / 修改 / 删除。增量模式下客户最关心这个。"""
        after = self._snapshot_tree(src_abs)
        added = sorted(set(after) - set(snap_before))
        removed = sorted(set(snap_before) - set(after))
        changed = sorted(k for k in set(after) & set(snap_before) if after[k] != snap_before[k])
        try:
            logdir = Path(self._project_dir(project.user_id, project.id)) / "运行日志"
            logdir.mkdir(parents=True, exist_ok=True)
            lines = [
                f"# 本轮改动清单（第 {round_no} 轮 · 增量修改）", "",
                f"- 新增 {len(added)} 个 / 修改 {len(changed)} 个 / 删除 {len(removed)} 个"
                f"（对比本轮开始前的 `src/`）", "",
            ]
            for title, items in (("新增", added), ("修改", changed), ("删除", removed)):
                lines.append(f"## {title}（{len(items)}）")
                lines += [f"- `{p}`" for p in items] or ["- （无）"]
                lines.append("")
            lines.append("> 增量修改模式下，没有被改到的文件会原样保留 —— 上面的清单就是这轮的真实改动。")
            (logdir / "97-本轮改动.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            self._log(f"写改动清单失败（忽略）：{e}")
        self._log(f"本轮改动：新增 {len(added)} / 修改 {len(changed)} / 删除 {len(removed)}")
        return {"added": added, "changed": changed, "removed": removed}

    def _finalize(self, db, project, result: dict = None) -> str:
        """开发链（含 QA）跑完后的收尾：写总览 + 打包 + **过 QA 交付闸门** + （通过才）自动起应用。

        ⚠️ 闸门是必须的：真机踩到过"测试报告写 [FAIL]、项目却是 COMPLETED 并打了 ZIP"，
        客户点开是白屏 —— 那等于把没通过验收的东西交付了。

        ⚠️ **本函数是同步的，里面会建 venv + 装依赖 + 起应用（最坏 ~150 秒）**。
        async 流程一律用 `await self._finalize_async(...)`：直接在这里调会把事件循环占死，
        真机现象就是"项目跑着跑着，客户刷新页面/点任何按钮都一直转圈没反应"。
        同步版保留给测试与离线脚本（它们不在事件循环里跑）。
        """
        t_all = time.perf_counter()
        t0 = time.perf_counter()
        self._write_overview(db, project, result or {})
        zip_path = file_helper.zip_project(project.user_id, project.id)
        self._cost("写总览 + 打包 ZIP", t0)

        t1 = time.perf_counter()
        verdict, why = self._qa_verdict(db, project)
        self._cost("QA 交付闸门判定", t1)
        if verdict == "FAIL":
            update_project_status_or_zip(db, project.id, status="FAILED", zip_path=zip_path)
            self._write_delivery_blocked(project, why)
            self._log(f"项目 {project.id} 未交付：QA 判定不通过（{why}）→ 置 FAILED，不自动启动应用")
            return zip_path
        if verdict == "":
            # 有测试节点、却拿不到结论（报告缺失/被截断/模板变了）→ **不许静默当成通过**：
            # 那等于"没验收就交付"。宁可明确失败，让客户看到原因并重跑。
            update_project_status_or_zip(db, project.id, status="FAILED", zip_path=zip_path)
            self._write_delivery_blocked(project, f"拿不到测试结论：{why}")
            self._log(f"项目 {project.id} 未交付：拿不到 QA 结论（{why}）→ 置 FAILED")
            return zip_path
        if verdict == "SKIP":
            self._log(f"项目 {project.id} 的图里没有测试节点（{why}）—— 按通过处理（用户自定义图）")

        update_project_status_or_zip(db, project.id, status="COMPLETED", zip_path=zip_path)
        t2 = time.perf_counter()
        self._autostart_app(project)
        self._cost("自动起应用（建 venv/装依赖/等就绪）", t2)
        self._cost("收尾总计", t_all)
        return zip_path

    async def _finalize_async(self, db, project, result: dict = None) -> str:
        """`_finalize` 的异步入口：整段丢到工作线程，**事件循环在此期间照常服务前端请求**。

        为什么必须走这个入口：`_finalize` 里的"自动起应用"会同步跑 `python -m venv`、
        `pip install`（各 120 秒超时）和 30 秒等就绪。这些跑在事件循环上时，uvicorn 连 accept
        都做不了 —— 客户此时刷新页面/点按钮就是"一直转圈没反应"（真机踩到）。

        代价（已权衡）：从工作线程起的应用**不带 PDEATHSIG**（`process.py` 里由父进程按
        "是否主线程"决定，见 `_preexec/_rlimits`），平台重启后残留的应用不再自动跟着死，
        改由平台启动时的 `cleanup_orphans()` 回收。换来的是"收尾那一刻全站不再假死"。
        """
        return await asyncio.to_thread(self._finalize, db, project, result)

    async def finish_project_async(self, db, project, result: dict = None, prd_text: str = "") -> str:
        """`finish_project` 的异步入口（async 调用方一律用这个，理由同 `_finalize_async`）。"""
        return await asyncio.to_thread(self.finish_project, db, project, result, prd_text)

    @staticmethod
    def _cost(label: str, t0: float) -> None:
        """收尾分段耗时（保险丝）：哪一段慢，日志里直接看得见，不用再猜。"""
        print(f"[orchestrator] 收尾耗时 · {label} {time.perf_counter() - t0:.2f}s")

    def _autostart_app(self, project) -> None:
        """前后端分离项目跑完**自动启动一次**（S2-5）：用户跑完直接点开就能看，不必先找启动按钮。

        三条边界（都很重要）：
          · 纯前端项目没有 `src/backend/main.py` → 什么都不做（`autostart` 返回 skipped）；
          · 失败（依赖装不上 / 端口被别的项目占着 / 超时未就绪）**只记一笔，不让项目状态变成 FAILED**
            —— "代码已经生成好了"这件事不该因为起服务失败而失败；
          · **会被 `_finalize_async` 丢到工作线程里执行**：`start()` 自己按"是否主线程"决定要不要设
            PR_SET_PDEATHSIG（`process.py` 的 `_preexec/_rlimits`），线程里起就只设资源上限，
            不会出现"线程一退出就把应用带走"。残留应用由启动时的 `cleanup_orphans()` 回收。
        """
        pdir = self._project_dir(project.user_id, project.id)
        try:
            res = app_runner.autostart(pdir, project_id=project.id)
        except Exception as e:                    # noqa: BLE001 —— 兜底，绝不外抛
            self._log(f"自动启动应用异常（忽略）：{e}")
            return
        self._log(f"自动启动应用：{res.get('status')} · {res.get('message')}")
        if not res.get("ok") and res.get("status") not in ("skipped", "running"):
            print(f"[runner] p{project.id} 自动启动未成功：{res.get('message')}")

    def _write_overview(self, db, project, result: dict) -> None:
        """把 PM（不在 project_steps 里）+ workflow 各步骤汇总成「运行日志/00-总览」。

        主链路跑的是 dev_only_chain（不含 pm 节点），PM 只在 project_agents 里，所以要单独补一行；
        但 full_dev_chain 这种**图里就有 pm 节点**的情况若再加一行就重复了 —— 这里判一下。
        """
        pm = next((a for a in get_project_agents(db, project.id) if a.role == ROLE_PM), None)
        pm_ids = {a.id for a in agents_crud.list_builtin(db) if a.role_key == "pm"}
        already = any(s.get("agent_id") in pm_ids for s in result.get("steps", []))
        if already:
            pm = None
        rows = []
        if pm:
            pm_agent = agents_crud.get_by_role_key(db, "pm", user_id=None)
            rows.append({
                "seq": "", "name": "PM 生成 PRD", "status": "SUCCESS",
                "skill": pm_agent.skill_id if pm_agent else "",
                "elapsed_ms": pm.elapsed_time or 0, "session_id": pm.session_id or "",
                "verdict": "—",
            })
        for s in result.get("steps", []):
            rows.append({
                "seq": f"{s['step_no']:02d}", "name": s["name"], "status": s["status"],
                "skill": s.get("skill") or "", "elapsed_ms": s.get("elapsed_ms") or 0,
                "session_id": s.get("session_id") or "", "verdict": s.get("verdict") or "—",
                "runs": s.get("runs") or 0,
            })
        runlog.write_overview(
            self._project_dir(project.user_id, project.id),
            title=project.title, status="COMPLETED",
            round_no=project_steps_crud.get_latest_round(db, project.id), rows=rows,
        )

    # ---------------- 对外步骤（方法名/签名与 router 保持兼容） ----------------
    async def step_1_run_pm(self, project_id: int):
        """项目第一段：跑「项目选定那张图的 PM 段」（到产出 PRD 为止），停下等人工审批。

        为什么不再直接调 PM Agent 而是跑图：图是**建项目时就选定**的，PM 只是图上的第一个节点。
        这样"整条链"是一份数据，用户看到的就是它；而审批闸门卡在图中间（`stop_after`），
        审批通过后从闸门之后接着跑（`preset` 把已跑好的 PRD 接上），**PM 全程只跑一次**。
        """
        db = SessionLocal()
        try:
            project = get_project_by_id(db, project_id)
            if not project:
                return
            update_project_status_or_zip(db, project_id, status="RUNNING")

            wf = self._project_workflow(db, project)
            gate = workflow_engine.ensure_prd_node(wf)
            result = await workflow_engine.run_workflow(
                db, project=project, user_id=project.user_id, workflow=wf,
                seeds={"user_requirement": project.description},
                round_no=project_steps_crud.get_latest_round(db, project_id),
                log_dir=self._log_dir(project.user_id, project_id),
                stop_after=gate,
            )
            self._map_steps_to_agents(db, project_id, result, self._prd_text_of(project, result, gate))

            update_project_status_or_zip(db, project_id, status="PENDING_APPROVAL")
            self._log(f"PRD 段完成（{result.get('workflow', '?')}），等待用户审批")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._fail(db, project_id, str(e))
        finally:
            db.close()

    async def step_2_handle_approval(self, db, project_id: int, approved: bool, feedback: str,
                                     workflow: dict = None):
        """人工审批：同意 → 按项目的编排模式继续；驳回 → 合并意见重跑 PRD 段。

        两种模式的**闸门都在 PM 之后**，所以这里只按 `project.mode` 分叉"通过之后干什么"：
            workflow → step_3_run_dev（跑选定的那张图的其余节点）
            agent    → step_3_run_agent_plan（编排官读 PRD 出图 → 跑它）
        """
        try:
            project = get_project_by_id(db, project_id)
            if not project:
                return
            feedback = (feedback or "").strip()
            if approved:
                if feedback:
                    # 「同意 + 补充要求」：并进需求描述，后续轮次也还带着它（与驳回同款留痕）。
                    # 并进这一轮 PRD 正文的活由 _dev_chain / agent 计划那条路做。
                    db_project = db.query(ProjectDB).filter(ProjectDB.id == project_id).first()
                    if db_project:
                        db_project.description = f"{db_project.description}\n【审批意见】: {feedback}"
                        db.commit()
                    self._log(f"审批意见已并入需求（{len(feedback)} 字），会一并传给后续节点")
                project = get_project_by_id(db, project_id)     # 取回带审批意见的描述
                update_project_status_or_zip(db, project_id, status="RUNNING")
                if (project.mode or "workflow") == "agent":
                    asyncio.create_task(self.step_3_run_agent_plan(project_id, feedback=feedback))
                    self._log("用户同意开发，已异步调起「编排官出图 → 执行」")
                else:
                    asyncio.create_task(self.step_3_run_dev(project_id, workflow, feedback=feedback))
                    self._log("用户同意开发，已异步调起开发链")
            else:
                db_project = db.query(ProjectDB).filter(ProjectDB.id == project_id).first()
                if db_project:
                    db_project.description = f"【上次需求】: {db_project.description}\n【修改意见】: {feedback}"
                    db_project.status = "RUNNING"
                    db.commit()
                asyncio.create_task(self.step_1_run_pm(project_id))
                self._log("用户驳回，已合并修改意见并异步重跑 PM")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._fail(db, project_id, str(e))

    async def step_3_run_dev(self, project_id: int, workflow: dict = None, feedback: str = ""):
        """审批通过后的第二段：读已批准的 PRD -> 跑图的其余节点（含 QA）-> 打包 COMPLETED。

        PRD 节点**不重跑**：它已由第一段产出、且用户已经审批过（`preset` 把它接回图里）。
        `feedback` 是"同意"时填的补充要求：并进 PRD 正文一起送下去（图里若还接了一个 PM 节点，
        它会拿这份 PRD 当底稿再出一版，下游读到的就是新版）。
        """
        db = SessionLocal()
        try:
            project = get_project_by_id(db, project_id)
            if not project:
                return
            pm_row = next((a for a in get_project_agents(db, project_id) if a.role == ROLE_PM), None)
            if not pm_row or not pm_row.path:
                raise AgentRunError("未找到 PM 登记的 PRD，无法开发")
            prd_text = read_project_prd(db, project)
            update_project_status_or_zip(db, project_id, status="RUNNING")
            chosen = self._project_workflow(db, project, workflow)
            gate = workflow_engine.find_prd_node(chosen)
            prd_text = prd_with_feedback(prd_text, feedback)
            result = await self._dev_chain(db, project, prd_text, chosen,
                                           preset=self._segment2_preset(db, project, chosen, gate, prd_text, pm_row))
            zip_path = await self._finalize_async(db, project, result)
            self._log(f"开发链完成（{result.get('workflow', '?')}），已打包 {zip_path}，项目 COMPLETED")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._fail(db, project_id, str(e))
        finally:
            db.close()

    async def step_3_run_agent_plan(self, project_id: int, feedback: str = ""):
        """**Agent 模式**审批通过后的第二段：编排官读 PRD 出图 → 跑那张图 → 收尾。

        与工作流模式的差别只有"图从哪来"：
            workflow 模式：图在建项目时就选好了，这里跑它的其余节点；
            agent    模式：图在**现在**才产生（Planner 读已批准的 PRD 现场决定用哪些节点）。
        两种模式的审批闸门都在 PM 之后、PM 都只跑一次 —— 图里不会再出现第二个 PM
        （见 planner_runner.convert_plan_to_workflow 里对 PM 步骤的剔除）。
        """
        db = SessionLocal()
        try:
            # 局部 import：planner_runner 顶层 import 了本模块的 step_orchestrator（互相依赖），
            # 在这里 import 才不会形成循环。
            from app.agents import planner_runner

            project = get_project_by_id(db, project_id)
            if not project:
                return
            prd_text = prd_with_feedback(read_project_prd(db, project), feedback)
            if not prd_text:
                raise AgentRunError("未找到 PM 登记的 PRD，无法编排")
            update_project_status_or_zip(db, project_id, status="RUNNING")
            round_no = project_steps_crud.get_latest_round(db, project_id)

            # ① 编排决策：读 PRD → 出计划（这一步本身也是一次节点执行，有步骤行/会话号/Plan.json）
            self._log("Agent 模式：编排官正在读 PRD 出图…")
            plan, _ = await planner_runner.compose_plan(
                db, project=project, prd_content=prd_text,
                user_requirement=project.description, round_no=round_no,
            )
            # ② 计划 → 图（PM 步骤会被剔掉；PRD 走 seed.prd_content）
            workflow = planner_runner.convert_plan_to_workflow(
                plan, db, project.user_id, project_title=project.title,
            )
            # ★ 把编排官出的图**落到项目上**：否则重跑/迭代会退回模版图，编排官插的自定义
            #   节点会被悄悄丢掉（真机踩到，p46）。写失败不影响本次执行，只记一笔。
            try:
                update_project_fields(db, project.id,
                                      planned_workflow={"name": workflow.get("name"),
                                                        "nodes": workflow.get("nodes")})
            except Exception as e:  # noqa: BLE001
                self._log(f"编排官出图落盘失败（不影响本次执行）：{e}")
            self._log(f"编排官出图完成：{len(workflow['nodes'])} 个节点，开始执行")

            # ③ 跑图 + 收尾（与主链路完全相同的收尾）
            result = await planner_runner.execute_plan(
                db, project=project, workflow=workflow,
                seeds={"prd_content": prd_text, "user_requirement": project.description},
                round_no=round_no,
            )
            self._log(f"Agent 编排链完成（{result.get('workflow', '?')}），项目 COMPLETED")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._fail(db, project_id, str(e))
        finally:
            db.close()

    async def step_3_revise_dev(self, project_id: int, feedback: str, workflow: dict = None,
                                incremental: bool = True):
        """用户提修改意见后的重构：合成重构 PRD -> 重新 workflow 执行（含 QA）。"""
        db = SessionLocal()
        try:
            project = get_project_by_id(db, project_id)
            if not project:
                return
            rows = get_project_agents(db, project_id)
            pm_row = next((a for a in rows if a.role == ROLE_PM), None)
            if not pm_row or not pm_row.path:
                raise AgentRunError("未找到原 PRD 路径，无法重构")
            original_prd = file_helper.read_file_by_db_path(project.user_id, project_id, pm_row.path)
            qa_row = next((a for a in rows if a.role == ROLE_QA), None)
            qa_report = ""
            if qa_row and qa_row.path:
                try:
                    qa_report = file_helper.read_file_by_db_path(project.user_id, project_id, qa_row.path)
                except FileNotFoundError:
                    pass

            synthesized = f"# 原始产品需求文档 (Original PRD)\n\n{original_prd}\n\n"
            synthesized += (f"## 核心修改指令 (User Feedback)\n"
                            f"请根据以下修改建议对代码进行重构与修复：\n> {feedback}\n\n")
            if qa_report:
                synthesized += f"## 现有测试缺陷报告参考 (Test Report)\n{qa_report}"

            update_project_status_or_zip(db, project_id, status="RUNNING")
            # 沿用项目选定的那张图（图存在项目上，所以"迭代一次就悄悄换回默认图"这个 bug 不存在了）；
            # PRD 节点仍不重跑 —— 送下去的是"原 PRD + 修改指令 + 现有测试报告"。
            chosen = self._project_workflow(db, project, workflow)
            gate = workflow_engine.find_prd_node(chosen)
            round_no = project_steps_crud.get_latest_round(db, project.id)
            src_abs = os.path.join(self._project_dir(project.user_id, project.id), "src")
            snap_before = self._snapshot_tree(src_abs) if incremental else {}
            result = await self._dev_chain(db, project, synthesized, chosen,
                                           preset=self._segment2_preset(db, project, chosen, gate, synthesized, pm_row),
                                           incremental=incremental)
            if incremental:
                self._write_change_manifest(project, snap_before, round_no, src_abs)
            zip_path = await self._finalize_async(db, project, result)
            self._log(f"重构完成（{result.get('workflow', '?')}），已打包 {zip_path}，项目 COMPLETED")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._fail(db, project_id, str(e))
        finally:
            db.close()

    async def run_qa_stage(self, project_id: int):
        """QA：读 PRD + 拼接源码 -> qa-workflow -> 测试报告落盘 -> 打包 ZIP -> COMPLETED。"""
        db = SessionLocal()
        try:
            project = get_project_by_id(db, project_id)
            if not project:
                return
            rows = get_project_agents(db, project_id)
            pm_row = next((a for a in rows if a.role == ROLE_PM), None)
            dev_row = next((a for a in rows if a.role == ROLE_DEV), None)
            if not pm_row or not dev_row or not pm_row.path or not dev_row.path:
                raise AgentRunError("缺少 PM 或 DEV 产物，无法 QA")
            prd_text = file_helper.read_file_by_db_path(project.user_id, project_id, pm_row.path)
            combined = file_helper.read_src_codes_combined_by_db_path(project.user_id, project_id, dev_row.path)
            res = await self._call_agent(
                db, "qa",
                {"prd_content": prd_text, "code_text": combined or "(无代码产物可审查)"},
                task_note="严格按 qa-workflow 技能输出《自动化测试报告》全文。",
                log_dir=self._log_dir(project.user_id, project_id),
            )
            report = (res.get("output") or "").strip()
            if not report:
                raise AgentRunError("QA 未产出测试报告")
            path = file_helper.save_qa_report(project.user_id, project_id, report)
            upsert_by_role(
                db, project_id, ROLE_QA, "QA Engineer",
                final_output=report, path=path,
                session_id=res.get("session_id"),
                additional_time=int(round(res.get("elapsed_seconds", 0) * 1000)),
            )
            zip_path = await self._finalize_async(db, project)
            self._log(f"QA 完成，已打包 {zip_path}，项目 COMPLETED")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._fail(db, project_id, str(e))
        finally:
            db.close()


# 实例化全局单例（router: from app.agents.orchestrator import step_orchestrator）
step_orchestrator = StepOrchestrator()
