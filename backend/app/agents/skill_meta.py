"""skill_meta.py —— 技能元信息（清单 + 一句话说明 + 提示词正文）。

## 为什么需要它

内置角色（`agents.mode='skill'`）的提示词**不在 `agents` 表里** —— 它们住在
`.dsh/skills/<skill_id>/SKILL.md`。接口以前只回传表里的 `system_prompt` 列，
所以前端拿到的全是 `null`，Agent 页面里"System Prompt"那一栏永远空白（实测反馈）。

这里把技能文件的**语义**取出来给接口用：
    · description   = frontmatter 的 `description`（一句话说明）
    · prompt_text   = SKILL.md 正文（去掉 frontmatter）—— 也就是这个 Agent 真正会收到的提示词
    · 清单          = `GET /api/skills`（前端"高级模式"选 skill_id 的数据源）

读盘结果按技能 id 缓存（进程内）。技能目录不要求热更新：改了技能重启后端即可。
"""
from functools import lru_cache
from pathlib import Path

from app.agents.ai_client import ai_client, read_skill_contract, read_skill_frontmatter

# 不开放给用户直接绑定的技能（其余都可以在「高级模式」的下拉里选）。
#
# 为什么用"排除表"而不是以前的硬编码白名单：白名单注释写着"与 .dsh/skills 一一对应"，
# 但实际是 10 vs 13 —— 目录里加一个技能、白名单忘了改，用户就会在界面上看到它却选不了
# （或者选了被 422 拒）。现在改成**扫目录**，只有这三个明确不对外开放。
NOT_BINDABLE = {
    "generic-prompt-agent": "通用技能模板：是「提示词模式」Agent 的内部载体，不该被手选",
    "agent-prompt-authoring": "提示词生成器：平台内部用来把一句话写成提示词，不作为角色开放",
    "requirement-normalizer": "需求规范化器：暂无内置角色与图引用它；要开放只需从本表删掉这一行",
}


@lru_cache(maxsize=4)
def _scan() -> tuple:
    """扫 `.dsh/skills/*/SKILL.md`，返回 ((可直接绑定的 id...), {id: 元信息})。

    返回 tuple 是为了能进 lru_cache；调用方用下面两个函数取用。
    """
    skills_dir = Path(ai_client.skills_dir)
    bindable, meta = [], {}
    for d in sorted(skills_dir.iterdir()) if skills_dir.is_dir() else []:
        if not (d / "SKILL.md").is_file():
            continue
        skill_id = d.name
        fm = read_skill_frontmatter(skill_id) or {}
        inputs = [
            {
                "name": str(item.get("name") or ""),
                "required": bool(item.get("required")),
                "description": str(item.get("description") or ""),
            }
            for item in (fm.get("input") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        meta[skill_id] = {
            "skill_id": skill_id,
            "name": str(fm.get("name") or skill_id),
            "description": str(fm.get("description") or "").strip(),
            # 技能自己声明的输出契约（解析用；不是"要求模型输出什么"）
            "contract": read_skill_contract(skill_id) or "text",
            "inputs": inputs,
            "bindable": skill_id not in NOT_BINDABLE,
            "not_bindable_reason": NOT_BINDABLE.get(skill_id, ""),
        }
        if skill_id not in NOT_BINDABLE:
            bindable.append(skill_id)
    return tuple(bindable), meta


def bindable_skill_ids() -> list:
    """可以被用户绑定到 `agents.skill_id` 的技能（= 高级模式下拉里的选项）。"""
    return list(_scan()[0])


def list_skills() -> list:
    """全部技能的元信息（含不可绑定的，带 `bindable` 标记），按 skill_id 排序。"""
    meta = _scan()[1]
    return [meta[k] for k in sorted(meta)]


@lru_cache(maxsize=256)
def skill_description(skill_id: str) -> str:
    """技能的一句话说明（frontmatter）。读不到 → ""。"""
    if not skill_id:
        return ""
    desc = read_skill_frontmatter(skill_id).get("description")
    return str(desc).strip() if desc else ""


@lru_cache(maxsize=256)
def skill_prompt_text(skill_id: str) -> str:
    """技能正文（SKILL.md 去掉 frontmatter）= 这个 Agent 真正会用的提示词。读不到 → ""。"""
    if not skill_id:
        return ""
    try:
        return ai_client.load_skill_text(skill_id).strip()
    except Exception:  # noqa: BLE001 —— 技能目录缺失/改名都不该让接口 500
        return ""


def prompt_mode_summary(system_prompt: str, limit: int = 60) -> str:
    """自定义提示词 Agent 的说明：取提示词首句/前 N 字（它没有技能，没有 frontmatter 可用）。"""
    text = " ".join((system_prompt or "").split())
    if not text:
        return ""
    for sep in ("。", "！", "？", ".", "!", "?", "\n"):
        idx = text.find(sep)
        if 0 <= idx < limit:
            return text[: idx + 1]
    return text[:limit] + ("…" if len(text) > limit else "")

