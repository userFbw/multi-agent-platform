"""skills 接口 —— 平台可用技能清单（前端「高级模式」选 skill_id 的数据源）

接口清单：
  - GET /api/skills  → 全部技能的元信息（含是否可被绑定）

## 为什么要有这个接口

前端新建 Agent 时要填 `skill_id`，但以前**没有任何接口能列出有哪些技能** —— 只能让用户
手敲 `例如：pm-workflow`，而且后端白名单是硬编码的，两边必然对不上。
（实测反馈："高级模式的 skill_id 要调整成选择而不是用户自己填空"。）

数据源就是 `.dsh/skills/*/SKILL.md` 的 frontmatter（与 `GET /api/agents` 的
`description` / `prompt_text` 同源），所以技能目录一改、重启后端就同步。
"""
from fastapi import APIRouter

from app.agents import skill_meta

router = APIRouter(prefix="/api/skills", tags=["技能清单"])


@router.get("", summary="平台可用技能清单（供 Agent 高级模式下拉）")
def list_skills(bindable_only: bool = False):
    """全部技能；默认连"内部技能"也返回（带 `bindable=false` 与原因），便于界面解释。

    - `bindable=true` 的技能可以填进 `agents.skill_id`（高级模式）；
    - `bindable=false` 的是平台内部技能（如通用技能模板），选了会被后端 422 拒。
    """
    items = skill_meta.list_skills()
    if bindable_only:
        items = [s for s in items if s["bindable"]]
    return {"items": items, "total": len(items)}
