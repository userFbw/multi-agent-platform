"""builtin_workflows.py —— 平台内置工作流定义（数据化，取代写死的分支）

供 workflow_engine.run_workflow 消费。节点 schema（校验见 validate_workflow）：
    id, name,
    agent: {"role_key": "..."} 或 {"id": <agents.id>},
    deps: [nodeId...],          # 上游必须先结束（SUCCESS/SKIPPED 都算结束，FAILED 会中止）
    when: {"ref": "x.parsed.level", "eq": "complex"},   # 省略 = 恒真；不满足 → 该步记 SKIPPED
    inputs: {占位符: 绑定值},
    output_kind: json_object | json_array | file_blocks | text（可省略，由技能声明推断）
    task_note,                  # 附加执行要求（可选）
    artifact_file: "PRD.md",    # 文本类输出落盘文件名（可选）
    code_dir: "src",            # 代码类输出合并目录（可选，全图只允许一个取值）

输入绑定值语法（见 workflow_engine.BindContext.bind）：
    {"ref": "seed.xxx"}                    → 外部种子参数
    {"ref": "<nodeId>.output"}             → 上游节点文本输出
    {"ref": "<nodeId>.parsed.字段"}        → 上游节点契约解析结果
    {"ref": "x.output", "from_marker": "…"}  → 从该标记截到尾
    {"ref": "x.output", "until_marker": "…"} → 截到该标记前
    {"code_combined": true}                → 项目 src/ 全部源码拼接文本（喂 QA）
    其它字面量                              → 原样传入
"""

# =====================================================================
# 两张「项目模版」——用户在建项目之前选一张，节点就按这张图定死
#
# 设计要点（三组共同确认的口径）：
#   ① 模版里【没有分类器】：选图这个动作本身就是"要不要后端"的答案，
#      无需再让某个 Agent 在运行期判断一次。分类器只在 Agent 自行调度
#      （编排官生成的图）与用户自定义画布里有意义。
#   ② 模版里【没有 when】：节点全部无条件执行，"选了什么就跑什么"。
#   ③ 模版【从 PM 开始】：图覆盖整个项目（PM 出 PRD → 人工审批 → 开发 → QA）。
#      审批闸门卡在图中间：创建项目时只跑 PM 段（run_workflow 的 stop_after），
#      审批通过后跑剩下的（run_workflow 的 preset 把已跑好的 PRD 接上），
#      所以 PM 全程只跑一次。
#
# 另：**真跑要求不写在这里** —— 它由 workflow_engine 按"节点输出契约是不是代码类"
# 自动追加（见 RUN_NOTE）。这样内置图、编排官生成的图、用户画布图**都**能覆盖到，
# 而不是每写一张图就要记得抄一遍那段要求。
# =====================================================================

# 简单项目：PM → 简单前端（静态三件套）→ QA
WORKFLOW_SIMPLE = {
    "name": "简单项目",
    "description": "简单项目：产品经理出 PRD → 简单前端直出静态三件套 → 自动化测试（适合纯前端页面，如计算器/计时器，不含登录与数据库）",
    "nodes": [
        {
            "id": "pm", "name": "生成 PRD",
            "agent": {"role_key": "pm"},
            "inputs": {"user_requirement": {"ref": "seed.user_requirement"}},
            "output_kind": "text",
            "task_note": "严格按 pm-workflow 技能输出 PRD 全文作为最终回答，不要任何额外寒暄。",
            "artifact_file": "PRD.md",
        },
        {
            "id": "simple", "name": "简单前端开发",
            "agent": {"role_key": "simple-frontend"},
            "deps": ["pm"],
            "inputs": {"prd_content": {"ref": "pm.output"}},
            "output_kind": "json_array",
            "code_dir": "src",
            "task_note": "严格按 simple-frontend 技能输出 JSON 数组契约。",
        },
        {
            "id": "qa", "name": "自动化测试",
            "agent": {"role_key": "qa"},
            "deps": ["simple"],
            "inputs": {
                "prd_content": {"ref": "pm.output"},
                "code_text": {"code_combined": True},
            },
            "output_kind": "text",
            "task_note": "严格按 qa-workflow 技能输出《自动化测试报告》全文。",
            "artifact_file": "Test_Report.md",
        },
    ],
}

# 复杂项目：PM → 架构拆解任务书 →（后端 ∥ 前端）→ QA
WORKFLOW_FULLSTACK = {
    "name": "复杂项目",
    "description": "复杂项目：产品经理出 PRD → 架构师拆前后端任务书 → 后端与前端并行开发 → 自动化测试（适合含后端/数据库/登录等项目）",
    "nodes": [
        {
            "id": "pm", "name": "生成 PRD",
            "agent": {"role_key": "pm"},
            "inputs": {"user_requirement": {"ref": "seed.user_requirement"}},
            "output_kind": "text",
            "task_note": "严格按 pm-workflow 技能输出 PRD 全文作为最终回答，不要任何额外寒暄。",
            "artifact_file": "PRD.md",
        },
        {
            "id": "architect", "name": "架构拆解任务书",
            "agent": {"role_key": "architect"},
            "deps": ["pm"],
            "inputs": {"prd_content": {"ref": "pm.output"}},
            "output_kind": "text",
            "task_note": "严格按 architect-planner 输出《系统架构与任务拆分说明书》全文。",
            "artifact_file": "Task_Plan.md",
        },
        {
            "id": "backend", "name": "后端开发",
            "agent": {"role_key": "backend-executor"},
            "deps": ["architect"],
            "inputs": {
                "prd_content": {"ref": "pm.output"},
                "backend_task": {"ref": "architect.output", "from_marker": "后端开发任务书"},
            },
            "output_kind": "file_blocks",
            "code_dir": "src",
            "task_note": "严格按 backend-executor 技能输出后端代码。",
        },
        {
            "id": "frontend", "name": "前端开发",
            "agent": {"role_key": "frontend-executor"},
            "deps": ["architect"],
            "inputs": {
                "prd_content": {"ref": "pm.output"},
                "frontend_task": {"ref": "architect.output", "until_marker": "后端开发任务书"},
            },
            "output_kind": "file_blocks",
            "code_dir": "src",
            "task_note": "严格按 frontend-executor 技能输出前端代码。",
        },
        {
            "id": "qa", "name": "自动化测试",
            "agent": {"role_key": "qa"},
            "deps": ["backend", "frontend"],
            "inputs": {
                "prd_content": {"ref": "pm.output"},
                "code_text": {"code_combined": True},
            },
            "output_kind": "text",
            "task_note": "严格按 qa-workflow 技能输出《自动化测试报告》全文。",
            "artifact_file": "Test_Report.md",
        },
    ],
}

# 没指定图时用哪张：取"复杂项目"——它对任何需求都成立（不会因为少一个后端而报废），
# 代价是比简单链慢。前端选定后由创建接口带上具体图名。
DEFAULT_WORKFLOW_NAME = WORKFLOW_FULLSTACK["name"]

# 只跑「任务编排官」的图：读需求 + 可用技能清单 → 产出编排计划（plan JSON）。
#
# 为什么做成一张图而不是在路由里直接调技能：
#   这样"编排"这一步和别的步骤一样是一次**节点执行** —— 有 project_steps 行、有会话号、
#   有运行日志和产物落盘（Plan.json），也就是"编排决策"第一次可被追溯。
#
# 动态值（可用技能清单随库变化）走 seeds 传入，图本身保持静态数据 —— 不把数据变成代码。
# ⚠️ 这张图**不再对用户暴露**（不在 BUILTIN_WORKFLOWS 里）：它只是"编排官出图"那一步的载体，
# 由 planner_runner.compose_plan 直接引用。用户能在界面上选的只有下面两张项目模版。
WORKFLOW_PLAN = {
    "name": "plan_only",
    "description": "任务编排官单独跑一步：读 PRD（或原始需求）拆成可执行的步骤列表",
    "nodes": [
        {
            "id": "planner", "name": "编排决策",
            "agent": {"role_key": "planner"},
            # 输入以 PRD 为主（Agent 模式下 PM 已经跑完、用户已审批过它）；原始需求只在拿不到 PRD 时兜底。
            # 两个都绑上没关系：技能正文用哪个占位符，引擎就填哪个。
            "inputs": {
                "prd_content": {"ref": "seed.prd_content"},
                "user_requirement": {"ref": "seed.user_requirement"},
                "available_skills": {"ref": "seed.available_skills"},
            },
            "output_kind": "json_object",
            "artifact_file": "Plan.json",
            "task_note": "严格按 planner 技能输出唯一合法 JSON（goal / steps / est_complexity），不要任何额外文字。",
        },
    ],
}

# 用户可见的内置模版：就这两张，且与首页下拉里的选项一一对应（同名同义）
BUILTIN_WORKFLOWS = {
    WORKFLOW_SIMPLE["name"]: WORKFLOW_SIMPLE,
    WORKFLOW_FULLSTACK["name"]: WORKFLOW_FULLSTACK,
}

# 旧名字 → 现名：历史项目（projects.workflow_name）与前端 localStorage 里存过英文名，
# 解析时自动映射，免得老项目一审批就报"项目选定的工作流已不可用"。
#
# `dev_only_chain` / `full_dev_chain` 是上一版的两张图（分类器决定分支），都已下线；
# 统一映射到「复杂项目」：它不会因为少一个后端而产出报废的东西（安全优先）。
LEGACY_WORKFLOW_NAMES = {
    "simple_chain": "简单项目",
    "fullstack_chain": "复杂项目",
    "dev_only_chain": "复杂项目",
    "full_dev_chain": "复杂项目",
}
