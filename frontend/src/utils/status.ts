import type { TaskStatus } from '@/types/task'

export type StatusTone = 'neutral' | 'info' | 'success' | 'danger'

export const STATUS_META: Record<
  TaskStatus,
  { label: string; tone: StatusTone; dot: string }
> = {
  pending: { label: '排队中', tone: 'neutral', dot: 'aw-dot--pending' },
  running: { label: '执行中', tone: 'info', dot: 'aw-dot--running' },
  awaiting_approval: { label: '待审批', tone: 'info', dot: 'aw-dot--pending' },
  completed: { label: '已完成', tone: 'success', dot: 'aw-dot--completed' },
  failed: { label: '失败', tone: 'danger', dot: 'aw-dot--failed' },
  stopped: { label: '已跳过', tone: 'neutral', dot: 'aw-dot--stopped' },
  aborted: { label: '已终止', tone: 'neutral', dot: 'aw-dot--stopped' }
}
