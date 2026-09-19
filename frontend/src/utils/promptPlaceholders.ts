/**
 * 自定义提示词里**平台真的会替换**的占位符清单 + 自造名字检测。
 *
 * 为什么两边都要这份清单：
 *   · 后端 `node_inputs.PROMPT_PLACEHOLDERS` 把它喂给「提示词工程师」Agent（写手不能再靠猜）；
 *   · 前端在这里提示手写提示词的用户，并当场标出填不了的名字。
 * 踩过的坑：有人的提示词写了 `{{project_context}}` —— 平台不认识、**不报错**、原样留在
 * 发给模型的提示词里，那段"项目背景"其实一直是空的，界面上完全看不出来。
 */

export interface PromptPlaceholder {
  name: string
  desc: string
}

/** 与后端 node_inputs.PROMPT_PLACEHOLDERS 一一对应（改一处要改两处，测试会盯着） */
export const PLACEHOLDERS: PromptPlaceholder[] = [
  { name: 'user_requirement', desc: '用户原始需求（建项目时填的那句话）' },
  { name: 'prd_content', desc: '上游产出的 PRD 全文' },
  { name: 'user_input', desc: '上游节点的全部产出 + 用户需求（整块兜底）' },
  { name: 'output_contract', desc: '平台声明的输出格式（text / json_object / …）' }
]

/** 和后端 ai_client.PLACEHOLDER_PATTERN 同源：`{{名字}}` 与 `{{input.名字}}` 都算 */
const PATTERN = /\{\{\s*(?:(?:input|output)\.)?([\w-]+)\s*\}\}/g

/** 展示用的 `{{xxx}}` 字样（模板里直接写花括号会被 Vue 解析器截断，必须拼好再传） */
export const placeholderToken = (name: string) => `{{${name}}}`

/** 这段提示词里**平台填不了**的占位符名字（去重，保持出现顺序） */
export function unknownPlaceholders(text: string): string[] {
  const bad: string[] = []
  for (const m of (text || '').matchAll(PATTERN)) {
    const name = m[1]
    if (!PLACEHOLDERS.some((p) => p.name === name) && !bad.includes(name)) bad.push(name)
  }
  return bad
}
