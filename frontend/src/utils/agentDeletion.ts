/**
 * 「删除自定义 Agent」的确认文案。
 *
 * 为什么单独抽出来：删除是硬删，而工作流里存的是 `agent.role_key` —— Agent 没了，
 * 图上那些节点的角色就解析不到（画布标红、运行到那一步直接失败）。确认框必须把
 * **会影响哪几张图**说清楚，而不是一句干巴巴的"确认删除？"。
 * 抽成纯函数是为了能对着真实接口数据跑断言（见 项目记录/前端 图检查/Agent删除提示测试.mjs）。
 */
import type { ApiAgentUsage } from '@/types/api'

export interface DeletionWarning {
  /** 兜底确认文案（一行，给 window.confirm 用） */
  message: string
  /** 影响面拆开给 UI 用 */
  workflowCount: number
  workflowNames: string[]
  projectCount: number
  /** 有影响（需要用户多看一眼） */
  impactful: boolean
}

export function deletionWarning(usage: ApiAgentUsage): DeletionWarning {
  const names = usage.workflows.map((w) => w.name)
  const lines = [`确认删除自定义 Agent「${usage.name}」？`]

  if (names.length) {
    lines.push('', `⚠️ 它正被 ${names.length} 张工作流引用：`)
    for (const w of usage.workflows) {
      lines.push(`   · ${w.name}（节点：${w.nodes.join('、')}）`)
    }
    lines.push(
      '',
      '删除后这些图里对应的节点会「解析不到角色」：画布上会标红提示，',
      '运行到那一步会直接失败（需要你在图上删掉该节点，或换成别的 Agent）。'
    )
  } else {
    lines.push('', '当前没有工作流引用它，删除不影响任何图。')
  }

  if (usage.project_count) {
    lines.push(
      '',
      `另有 ${usage.project_count} 个历史项目用过它${usage.project_titles.length ? `（如「${usage.project_titles[0]}」）` : ''}：`,
      '那些项目的步骤记录会显示不出这个 Agent 的名字，已有产物不受影响。'
    )
  }

  lines.push('', '删除后无法恢复，确认删除？')

  return {
    message: lines.join('\n'),
    workflowCount: names.length,
    workflowNames: names,
    projectCount: usage.project_count,
    impactful: names.length > 0 || usage.project_count > 0
  }
}
