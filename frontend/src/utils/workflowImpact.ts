/**
 * 「改这张图会影响谁」的提示文案。
 *
 * 项目在建的时候就把图记在项目上（`projects.workflow_id`），审批与迭代都沿用它，所以：
 *   · 保存修改 → 那些项目**以后再跑就是新图**（正在等审批的项目，开发段直接用新图）；
 *   · 删除这张图 → 它们再执行会**直接失败**（"项目选定的工作流已不可用"），
 *     平台不会悄悄换回默认模版 —— 宁可明确报错，也别让人以为跑的还是原来那张。
 * 抽成纯函数是为了能对着真实接口数据跑断言（见 项目记录/前端 图检查/工作流影响提示测试.mjs）。
 */
import type { ApiWorkflowUsage } from '@/types/api'

export type ImpactAction = 'edit' | 'delete'

export interface ImpactWarning {
  /** window.confirm 用的多行文案（纯文本，不要写 markdown 星号） */
  message: string
  projectCount: number
  activeCount: number
  /** 有影响 */
  impactful: boolean
}

const STATUS_CN: Record<string, string> = {
  INITIAL: '待启动',
  RUNNING: '执行中',
  PENDING_APPROVAL: '待审批',
  COMPLETED: '已完成',
  FAILED: '失败'
}

const label = (status: string) => STATUS_CN[status] ?? status

export function impactWarning(action: ImpactAction, usage: ApiWorkflowUsage): ImpactWarning {
  const active = usage.projects.filter((p) => p.status === 'RUNNING' || p.status === 'PENDING_APPROVAL')
  const lines = [
    action === 'edit'
      ? `这张图「${usage.name}」已被 ${usage.project_count} 个项目引用：`
      : `确认删除这张图「${usage.name}」？它已被 ${usage.project_count} 个项目引用：`
  ]
  for (const p of usage.projects) lines.push(`   · ${p.title}（${label(p.status)}）`)

  lines.push('')
  if (action === 'edit') {
    lines.push('保存后这些项目「以后再执行就是按新图跑」。')
    lines.push('正在等审批的项目：审批通过后直接按新图开发；已完成的项目：下次「迭代修改」也按新图。')
  } else {
    lines.push('删除后它们再执行会直接失败（提示"项目选定的工作流已不可用"），')
    lines.push('平台不会自动换回默认模版 —— 需要你重新给这些项目指定一张图。')
  }
  if (active.length) {
    lines.push('')
    lines.push(`其中 ${active.length} 个正处于「${active.map((p) => label(p.status)).join('/')}」状态，请特别确认。`)
  }
  lines.push('', action === 'edit' ? '仍然保存修改？' : '仍然删除？')

  return {
    message: lines.join('\n').replace(/\*\*/g, ''),   // confirm 是纯文本，星号会原样显示
    projectCount: usage.project_count,
    activeCount: active.length,
    impactful: usage.project_count > 0
  }
}
