"""file_helper —— 产物文件落盘（账号物理隔离）

目录约定（**只用不可变主键定位**，改用户名/改项目标题/删号重建都不影响）：
    backend/exports/u<user_id>/p<project_id>/
        ├── PRD.md / Test_Report.md / …   文本类产物
        ├── src/                          代码产物
        ├── 运行日志/                      每个 Agent 节点的运行记录（人看）
        └── 项目BUG/                       每个可运行代码的第 N 次运行记录（人看）
    backend/exports/u<user_id>/p<project_id>.zip   打包结果（不含下面两项）
    backend/data/logs/u<user_id>/p<project_id>/     DSH 会话原始 JSON（机器看，不进 ZIP）

职责边界：本模块只碰磁盘，不做任何数据库操作（库操作归 db/crud）。
"""
import os
import shutil
import zipfile

# 「运行日志 / 项目BUG」两个目录名的**唯一真相** —— 写入方在 agents/runlog.py，
# 读取方（本模块 + api 层）从这里取，免得同一个中文目录名散落三处。
RUNLOG_DIR = "运行日志"
BUG_DIR = "项目BUG"


class FileHelper:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.exports_dir = os.path.join(self.base_dir, 'exports')
        os.makedirs(self.exports_dir, exist_ok=True)

    def get_project_dir(self, user_id: int, project_id: int) -> str:
        """项目产物目录（绝对路径）。"""
        return os.path.join(self.exports_dir, f"u{user_id}", f"p{project_id}")

    def get_log_dir(self, user_id: int, project_id: int) -> str:
        """会话原始日志目录（纯算路径，不建夹）。放 exports 之外：不进 ZIP，也不会被 /previews 暴露。

        目录由 ai_client 真正写日志时自行创建，避免只读调用留下空目录。
        """
        return os.path.join(self.base_dir, "data", "logs", f"u{user_id}", f"p{project_id}")

    def ensure_project_folder(self, user_id: int, project_id: int) -> str:
        """创建项目目录（幂等），返回绝对路径。建项目时调用。"""
        project_dir = self.get_project_dir(user_id, project_id)
        os.makedirs(project_dir, exist_ok=True)
        return project_dir

    def save_prd(self, user_id: int, project_id: int, prd_content: str) -> str:
        """保存 PRD，返回相对 backend/ 的路径（写进 project_agents.path）。"""
        project_dir = self.ensure_project_folder(user_id, project_id)
        with open(os.path.join(project_dir, "PRD.md"), 'w', encoding='utf-8') as f:
            f.write(prd_content)
        return "PRD.md"

    def save_qa_report(self, user_id: int, project_id: int, qa_report: str) -> str:
        """保存测试报告，返回相对 backend/ 的路径。"""
        project_dir = self.ensure_project_folder(user_id, project_id)
        with open(os.path.join(project_dir, "Test_Report.md"), 'w', encoding='utf-8') as f:
            f.write(qa_report)
        return "Test_Report.md"

    def read_file_by_db_path(self, user_id: int, project_id: int, db_path: str) -> str:
        """按数据库登记的相对路径读取产物文本（path 字段是唯一索引）。"""
        full_path = os.path.join(self.get_project_dir(user_id, project_id), db_path)
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"未找到物理文件: {full_path}")
        with open(full_path, 'r', encoding='utf-8') as f:
            return f.read()

    def read_src_codes_combined_by_db_path(self, user_id: int, project_id: int, db_path: str) -> str:
        """按数据库登记的源码目录（如 'src/'）读取并拼接全部代码文件。"""
        src_dir = os.path.join(self.get_project_dir(user_id, project_id), db_path)
        if not os.path.exists(src_dir):
            return ""

        combined = ""
        for root, _, files in os.walk(src_dir):
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, src_dir)
                with open(file_path, 'r', encoding='utf-8') as f:
                    combined += f"=== File: {rel_path} ===\n{f.read()}\n\n"
        return combined

    # 打包时忽略的目录/文件名：运行期产物与临时环境，用户下载源码时不需要
    # 「.开头的目录」一律跳过（.venv/.pip-cache/.home/.pip/.git …）——和文件树的规则保持一致，
    # 否则实测会把 16MB 的 pip 下载缓存打进交付 ZIP。
    ZIP_SKIP_DIRS = {".venv", "venv", "__pycache__", "node_modules", ".git",
                     RUNLOG_DIR, BUG_DIR, "_logs"}
    ZIP_SKIP_NAMES = {".DS_Store"}

    @classmethod
    def _zip_skip_dirs(cls, dirs: list) -> list:
        """ZIP 与文件树共用的目录过滤规则。"""
        return [d for d in dirs if d not in cls.ZIP_SKIP_DIRS and not d.startswith(".")]

    def zip_project(self, user_id: int, project_id: int) -> str:
        """打包项目目录，返回相对 backend/ 的路径（写进 projects.zip_path）。"""
        project_dir = self.get_project_dir(user_id, project_id)
        zip_path = os.path.join(os.path.dirname(project_dir), f"p{project_id}.zip")

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(project_dir):
                dirs[:] = self._zip_skip_dirs(dirs)
                for file in files:
                    # .stdout/.stderr = 会话捕获文件（平台临时物，不是产物）：老项目 src/ 里
                    # 残留的那几份也要挡在 ZIP 外，不然客户解压看到的是会话日志当源码。
                    if (file in self.ZIP_SKIP_NAMES
                            or file.endswith((".pyc", ".log", ".db", ".stdout", ".stderr"))):
                        continue
                    file_full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_full_path, project_dir)
                    zipf.write(file_full_path, rel_path)
        return f"exports/u{user_id}/p{project_id}.zip"

    # ---------------- 运行日志 / 项目BUG 的读取 ----------------
    # 两类日志的**文件路径不在数据库里**，但完全可以从库里的字段推出来：
    #     运行日志/<step_no:02d>-<步骤名>_r<轮次>.md
    #     项目BUG/<step_no:02d>-<Agent名>-第<i>次运行_r<轮次>.md
    # 所以不需要加表加列 —— 这里只负责"按目录读出来"，给 api 层用。
    def _log_subdir(self, user_id: int, project_id: int, kind: str) -> str:
        name = BUG_DIR if kind == BUG_DIR else RUNLOG_DIR
        return os.path.join(self.get_project_dir(user_id, project_id), name)

    def list_logs(self, user_id: int, project_id: int) -> list:
        """列出项目目录下两类日志（不含正文，只给清单）。

        返回每项：{kind, name, size, modified}，按"先总览、再按文件名"排序（文件名带序号，天然有序）。
        """
        items = []
        for kind in (RUNLOG_DIR, BUG_DIR):
            d = self._log_subdir(user_id, project_id, kind)
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                if not name.endswith(".md"):
                    continue
                full = os.path.join(d, name)
                if not os.path.isfile(full):
                    continue
                items.append({
                    "kind": kind, "name": name,
                    "size": os.path.getsize(full),
                    "modified": int(os.path.getmtime(full)),
                })
        return sorted(items, key=lambda x: (x["kind"] != RUNLOG_DIR, x["name"]))

    def find_log(self, user_id: int, project_id: int, name: str):
        """在清单里按文件名找一条（白名单匹配）；找不到返回 None。"""
        return next((i for i in self.list_logs(user_id, project_id) if i["name"] == name), None)

    def read_log(self, user_id: int, project_id: int, kind: str, name: str) -> str:
        """读一份日志正文（`kind` 必须来自 list_logs 的结果）。

        安全性：先在真实文件清单里做**白名单匹配**再读 —— 不做路径拼接，
        因此 `../` 之类的穿越构造根本进不来（比事后校验更稳）。
        """
        if not any(i["name"] == name and i["kind"] == kind
                   for i in self.list_logs(user_id, project_id)):
            raise FileNotFoundError(name)
        with open(os.path.join(self._log_subdir(user_id, project_id, kind), name),
                  encoding="utf-8") as f:
            return f.read()

    # ---------------- 产物正文的读取（PRD / 测试报告之外的每一步产出） ----------------
    # 路径的唯一来源是 `project_steps.artifact_path`（引擎落盘时登记的相对路径），
    # 不是前端传来的任意路径；这里再兜一层"必须落在本项目目录内"，越界一律 None。
    # 为什么需要它：画布拖出来的自定义 Agent 产出的是 `<步骤名>.md`（如「审查计算器代码.md」），
    # 以前只有 PRD / 测试报告两个专用接口能读，这些产物在项目里根本看不到。
    def _artifact_path(self, user_id: int, project_id: int, rel: str):
        """登记路径 → 项目内绝对路径；空 / 绝对 / 含 `..` / 越界 → None。"""
        rel = (rel or "").strip().replace("\\", "/")
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            return None
        project_dir = os.path.realpath(self.get_project_dir(user_id, project_id))
        full = os.path.realpath(os.path.join(project_dir, rel))
        if full != project_dir and not full.startswith(project_dir + os.sep):
            return None
        return full

    def describe_artifact(self, user_id: int, project_id: int, rel: str) -> dict:
        """登记路径 → {exists, is_dir, size, files}（只描述，不读正文）。"""
        full = self._artifact_path(user_id, project_id, rel)
        if full is None:
            return {"exists": False, "is_dir": False, "size": 0, "files": 0}
        if os.path.isdir(full):
            files = sum(len(f) for _, _, f in os.walk(full))
            return {"exists": True, "is_dir": True, "size": 0, "files": files}
        if not os.path.isfile(full):
            return {"exists": False, "is_dir": False, "size": 0, "files": 0}
        return {"exists": True, "is_dir": False, "size": os.path.getsize(full), "files": 0}

    def read_artifact(self, user_id: int, project_id: int, rel: str, limit: int = 400_000) -> str:
        """读产物正文：文件直读（超长截断），目录按 `=== File: x ===` 合并全部代码。

        二进制 / 读不了的文件**跳过而不是整份失败** —— 否则一个 `__pycache__` 就能让
        "看代码"整个报 500。
        """
        full = self._artifact_path(user_id, project_id, rel)
        if full is None or not os.path.exists(full):
            raise FileNotFoundError(rel)
        if os.path.isfile(full):
            with open(full, encoding="utf-8", errors="replace") as f:
                return f.read(limit)
        parts = []
        for root, dirs, files in os.walk(full):
            dirs[:] = self._zip_skip_dirs(dirs)
            for name in sorted(files):
                p = os.path.join(root, name)
                try:
                    with open(p, encoding="utf-8") as f:
                        parts.append(f"=== File: {os.path.relpath(p, full)} ===\n{f.read()}\n")
                except (OSError, UnicodeDecodeError):
                    continue
        return "\n".join(parts)

    def delete_project_files(self, user_id: int, project_id: int) -> None:
        """清理项目的全部磁盘产物：ZIP + 项目目录。不存在则静默跳过。"""
        project_dir = self.get_project_dir(user_id, project_id)
        zip_path = os.path.join(os.path.dirname(project_dir), f"p{project_id}.zip")
        for p in (zip_path, project_dir):
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


file_helper = FileHelper()
