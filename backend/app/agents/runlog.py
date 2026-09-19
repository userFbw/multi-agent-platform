"""runlog.py —— 项目日志生成（运行日志 / 项目BUG）

都写在项目目录下，两类用途不同：

    运行日志/   一个 Agent 节点一份 —— 这个节点是谁、干了什么、产出了什么
    项目BUG/    一次代码运行一份 —— 跑了几次、每次的断言结果与真实报错

全部由后端从**会话输出**生成，不依赖 Agent 自己写（Agent 若也写了，会被搬到项目根）。
多轮重跑用 `_r<轮次>` 后缀区分，例如 `01-生成PRD_r2.md`。
"""
import re
from pathlib import Path

# 目录名的唯一真相在 storage 层（写入方是这里，读取方是 storage + api）
from app.storage.file_helper import BUG_DIR, RUNLOG_DIR

_SNIPPET_LIMIT = 5000
_INPUT_LIMIT = 4000     # 单个输入的展示上限（完整内容见平台会话日志）
_SKIP_DIRS = (RUNLOG_DIR + "/", BUG_DIR + "/")     # Agent 写的这两类 → 放项目根，不放进 src/


def _write(directory: Path, filename: str, text: str) -> None:
    """写一个 md；任何失败都静默跳过，绝不阻断主流程。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / filename).write_text(text, encoding="utf-8")
    except OSError:
        pass


def _snippet(text: str, limit: int = _SNIPPET_LIMIT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n\n…（已截断，完整内容见平台会话日志）"


def verdict(selftest: str) -> str:
    """从 [SELFTEST] 行判读结论。

    必须按**结构化字段**读，不能直接搜 "FAIL" 子串 —— `failed=0` 里就含 FAIL，
    否则全绿的运行会被判成失败。依次尝试：runtime=/result= 字段 → 独立的 PASS/FAIL 词
    → failed=<n> 计数。
    """
    if not selftest:
        return "—"
    m = re.search(r"(?:runtime|result)\s*=\s*(PASS|FAIL)", selftest, re.I)
    if not m:
        m = re.search(r"\b(PASS|FAIL)\b", selftest, re.I)      # \b 保证 failed=0 不算 FAIL
    if not m:
        m = re.search(r"failed\s*=\s*(\d+)", selftest, re.I)
        return "—" if not m else ("✅ PASS" if m.group(1) == "0" else "❌ FAIL")
    return "✅ PASS" if m.group(1).upper() == "PASS" else "❌ FAIL"



def selftest_line(text: str) -> str:
    """取文本里的 [SELFTEST] 结论行（没有则空串）。

    **不能用 startswith 判断**：平台记录里这行带前缀（`- 结论：\\`[SELFTEST] ...\\``），
    只按行首匹配会一条都取不到。这里从 `[SELFTEST]` 处截到行尾，并剥掉包裹的反引号。

    一个节点内可能真跑多次，返回**最后一次** —— 那才是交付代码时的最终状态；
    每次的过程记录见 write_bug_logs。
    """
    found = ""
    for line in (text or "").splitlines():
        i = line.find("[SELFTEST]")
        if i != -1:
            found = line[i:].strip().strip("`").strip()
    return found


_RUN_HEAD = re.compile(r"^###\s*第\s*(\d+)\s*次运行", re.M)


def split_record(text: str) -> list[str]:
    """把平台真跑记录（.selftest_runs.md）切成"每次运行一段"。

    实测 Agent 会照着提示里的格式**虚报** [SELFTEST] PASS（后台进程明明 DEAD 也写 PASS），
    所以结论一律以平台脚本自己记的这份为准，Agent 自己写的只当兜底。
    """
    text = (text or "").strip()
    marks = [m.start() for m in _RUN_HEAD.finditer(text)]
    return [text[a:b].strip() for a, b in zip(marks, marks[1:] + [len(text)])]


def extract_runs(output: str) -> list[tuple[str, str]]:
    """按 [SELFTEST] 行切分 **Agent 自己写的** 运行叙述（没有平台记录时兜底用）。

    每次代码真跑都以一行 [SELFTEST] 收尾，所以有几行就是跑了几次。
    """
    runs, buf = [], []
    for line in (output or "").splitlines():
        buf.append(line)
        if line.strip().startswith("[SELFTEST]"):
            runs.append((line.strip(), "\n".join(buf).strip()))
            buf = []
    return runs


def verdict_of(output: str = "", record: str = "") -> str:
    """本次代码交付时的最终结论：优先平台真跑记录的最后一次，其次 Agent 自己写的。"""
    blocks = split_record(record)
    if blocks:
        return verdict(selftest_line(blocks[-1]))
    return verdict(selftest_line(output))


def is_agent_log_path(rel: str) -> bool:
    """Agent 自己写的运行日志 / 项目BUG（这些要放项目根，不放进 src/）。"""
    return rel.startswith(_SKIP_DIRS)


def _bug_names(seq: int, agent_name: str, round_no: int, n: int) -> list:
    """项目BUG 里的文件名（与 write_bug_logs 保持一致，运行日志靠它做交叉链接）。"""
    return [f"{f'{seq:02d}-' if seq else ''}{agent_name}-第{i}次运行_r{round_no}.md" for i in range(1, n + 1)]


def write_step_log(
    project_dir,
    *,
    seq: int = 0,
    name: str,
    round_no: int = 1,
    skill_id: str = "",
    agent_name: str = "",
    session_id: str = "",
    elapsed_ms: int = 0,
    status: str = "",
    error_code: str = "",
    artifact_path: str = "",
    files=(),
    output: str = "",
    record: str = "",
    inputs: dict | None = None,
    unfilled: list | None = None,
) -> None:
    """写一份「运行日志」（一个 Agent 节点一份）。seq=0 表示不编号（如 PM 这种不在 project_steps 里的步骤）。"""
    runs = split_record(record)
    selftest = selftest_line(runs[-1]) if runs else selftest_line(output)
    lines = [
        f"# {f'{seq:02d} · ' if seq else ''}{name}",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 技能 | `{skill_id or '—'}` |",
        f"| Agent | {agent_name or '—'} |",
        f"| 会话号 | `{session_id or '—'}` |",
        f"| 耗时 | {elapsed_ms / 1000:.1f} 秒 |",
        f"| 步骤状态 | {status or '—'} |",
        f"| 运行验证 | {verdict(selftest)} |",
        f"| 代码真跑次数 | {len(runs) if runs else '未真跑'} |",
        f"| 错误码 | {error_code or '—'} |",
        f"| 产物路径 | `{artifact_path or '—'}` |",
        "",
        # 这一行是给人看的：实测有人把「代码运行次数」理解成"写了几次代码"。
        # 它不是"产出代码的次数"，而是"平台脚本真的把代码跑起来了几次"。
        "> 「代码真跑次数」= 平台 `.selftest.sh` **实际执行这份代码**的次数"
        "（改一次跑一次，可能不止 1 次）；`未真跑` = 这一步没跑代码"
        "（纯文本节点，或 Agent 没执行自检脚本）。每次的过程见 `项目BUG/`。",
    ]
    if selftest:
        lines += ["", "## 运行验证结论", "", f"> `{selftest}`"]
    if files:
        shown = list(files)[:40]
        lines += ["", "## 本 Agent 产出的文件（相对项目根，可直接点开）", ""]
        lines += [f"- `{f}`" for f in shown]
        if len(list(files)) > len(shown):
            lines += [f"- …共 {len(list(files))} 个文件"]
    if runs:
        lines += ["", f"## 这次代码真跑的记录（{len(runs)} 次，逐次可见）", ""]
        lines += [f"- [`{b}`](../{BUG_DIR}/{b})" for b in _bug_names(seq, agent_name or name, round_no, len(runs))]
    # 「这一步到底收到了什么」—— 实测用户的疑问是"下游到底有没有照 PRD 干"，
    # 光看输出答不了；把引擎组装好的输入原样留在这里，对着 PRD 一比就有答案。
    if unfilled:
        # 提示词里写了平台填不了的名字（如 {{project_context}}）：不报错、原样留在提示词里，
        # 于是模型看到一个空占位符而用户毫不知情。这里必须说出来（实测踩过）。
        lines += ["", "## ⚠️ 提示词里有平台填不了的占位符", "",
                  "下面这些名字平台不认识，**原样留在了发给模型的提示词里**（模型看到一串花括号）：", ""]
        lines += [f"- `{{{{{n}}}}}`" for n in unfilled]
        lines += ["", "平台能填的名字：`user_requirement`、`prd_content`、`user_input`、`output_contract`"
                      "（两种写法都行：`{{名字}}` / `{{input.名字}}`）。",
                  "改法：把这个 Agent 的提示词里对应位置换成上面能填的名字，或直接删掉那一段。"]
    if inputs:
        lines += ["", "## 本步收到的输入（引擎自动组装，可据此核对上游要求有没有传下去）", "",
                  "| 输入 | 字符数 |", "|---|---|"]
        lines += [f"| `{k}` | {len(str(v))} |" for k, v in inputs.items()]
        for k, v in inputs.items():
            lines += ["", f"### {k}", "", "```", _snippet(str(v), _INPUT_LIMIT), "```"]
    if output.strip():
        lines += ["", "## 原始会话输出", "", "```", _snippet(output), "```"]
    lines.append("")

    _write(Path(project_dir) / RUNLOG_DIR, f"{f'{seq:02d}-' if seq else ''}{name}_r{round_no}.md", "\n".join(lines))


def write_bug_logs(
    project_dir, *, step_no: int, agent_name: str, round_no: int,
    output: str = "", record: str = "",
) -> int:
    """把每次代码运行各写一份到「项目BUG」；返回运行次数（0 = 本次没真跑过代码）。

    优先用 record（平台 .selftest.sh 自己记的，可信）；没有时退回 Agent 自己写的 [SELFTEST] 叙述。
    """
    blocks = split_record(record)
    if blocks:
        for i, block in enumerate(blocks, start=1):
            text = "\n".join([
                f"# {agent_name} · 第 {i} 次运行",
                "",
                f"- 轮次：第 {round_no} 轮",
                f"- 结论：{verdict(selftest_line(block))}",
                "- 来源：平台脚本 `.selftest.sh` 自动记录（非 Agent 自述）",
                "",
                "## 这次真跑到底发生了什么",
                "",
                block,
            ])
            _write(Path(project_dir) / BUG_DIR, f"{step_no:02d}-{agent_name}-第{i}次运行_r{round_no}.md", text)
        return len(blocks)

    runs = extract_runs(output)
    for i, (selftest, body) in enumerate(runs, start=1):
        text = "\n".join([
            f"# {agent_name} · 第 {i} 次运行",
            "",
            f"- 轮次：第 {round_no} 轮",
            f"- 结论：{verdict(selftest)}",
            "- 来源：Agent 自述（本次未使用平台 .selftest.sh，可信度较低）",
            f"- 自检结论行：`{selftest}`",
            "",
            "## 运行输出",
            "",
            "```",
            _snippet(body, 4000),
            "```",
        ])
        _write(Path(project_dir) / BUG_DIR, f"{step_no:02d}-{agent_name}-第{i}次运行_r{round_no}.md", text)
    return len(runs)


def write_overview(project_dir, *, title: str, status: str, round_no: int, rows: list) -> None:
    """写「运行日志/00-总览_r<轮次>.md」。

    rows 每项：seq（显示用字符串，可空）/ name / skill / status / elapsed_ms /
              session_id / verdict / runs（该步代码真跑了几次）
    """
    total_ms = sum(r.get("elapsed_ms") or 0 for r in rows)
    runs = sum(r.get("runs") or 0 for r in rows)
    skipped = sum(1 for r in rows if (r.get("status") or "").upper() == "SKIPPED")
    table = [
        "| # | 步骤 | 技能 | 状态 | 耗时 | 会话号 | 真跑 | 运行验证 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        table.append(
            f"| {r.get('seq') or '—'} | {r['name']} | `{r.get('skill') or '—'}` | {r.get('status') or '—'} "
            f"| {(r.get('elapsed_ms') or 0) / 1000:.1f}s | `{(r.get('session_id') or '—')[:8]}` "
            f"| {r.get('runs') or '—'} | {r.get('verdict') or '—'} |"
        )

    lines = [
        f"# 运行日志总览 · {title}",
        "",
        f"- 项目状态：**{status}**",
        f"- 本报告对应：第 **{round_no}** 轮",
        f"- 步骤总数：**{len(rows)}**　合计耗时：**{total_ms / 60000:.1f} 分钟**　代码真跑次数：**{runs}**",
        f"- 跳过节点：**{skipped}** 个（条件不满足，未实例化 Agent，故没有对应的运行日志文件）",
        "",
        *table,
        "",
        "> 每个步骤的详情见同目录 `<序号>-<步骤名>_r<轮次>.md`；",
        "> 每次代码真跑的过程见 `../项目BUG/`。",
    ]
    _write(Path(project_dir) / RUNLOG_DIR, f"00-总览_r{round_no}.md", "\n".join(lines))
