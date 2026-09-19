<script setup lang="ts">
import { computed, type Component } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import AgentIcon from '@/components/common/AgentIcon.vue'
import { AlertTriangle, CircleCheck, Play, ShieldCheck, X } from '@lucide/vue'
import { useWorkflowStore } from '@/stores/workflow'
import type { WorkflowNodeKind } from '@/types/workflow'

/**
 * 画布节点上的删除按钮。
 *
 * 为什么要放在卡片上：删除入口以前只有右侧配置面板里那一个，而面板要先"选中节点"才出现 ——
 * 用户找不到，或者习惯性按 Delete 键（VueFlow 默认只认 Backspace）就以为"节点删不掉"。
 * 这里直接操作 store：节点是画布的唯一数据源，store 改了 `:nodes` 会跟着重画，
 * 相连的连线也由 store.removeNode 一起清掉（不会留下悬空依赖）。
 */
const store = useWorkflowStore()
const remove = () => store.removeNode(props.id)

const props = defineProps<{
  /** VueFlow 会把节点 id 传进来 */
  id: string
  data: {
    label: string
    kind: WorkflowNodeKind
    role?: string
    icon?: string
    isStart: boolean
    isEnd: boolean
    /** 产出 PRD 的节点 = 人工审批闸门，执行会停在这里 */
    isGate?: boolean
    /** 图检查结果：error 会失败 / warn 只是提醒；文案进 tooltip */
    issue?: 'error' | 'warn' | ''
    issueText?: string
    /** 执行条件的短描述（如 `verdict = needs_revision`）；空 = 无条件执行。
     *  只在 tooltip 里用：画布上不再挂徽标（用户反馈"画布标记太乱"），详情看右侧面板。 */
    condition?: string
  }
  selected?: boolean
}>()

/** tooltip 用「徽标 + 问题清单」拼，鼠标停在节点上就能看到原因 */
const nodeTitle = computed(() => {
  const lines = [`${props.data.label}（${props.data.role || '未绑定角色'}）`]
  if (props.data.isGate) lines.push('审批闸门：执行到这里会停下等你审批')
  if (props.data.condition) lines.push(`执行条件：${props.data.condition}（不相等就跳过这个节点）`)
  if (props.data.issueText) lines.push(props.data.issueText)
  return lines.join('\n')
})

const role = computed(() => {
  if (props.data.role) return props.data.role
  switch (props.data.kind) {
    case 'start':
      return '入口'
    case 'end':
      return '出口'
    case 'planner':
      return '任务编排'
    case 'pm':
      return '需求与方案'
    case 'dev':
      return '代码实现'
    case 'qa':
      return '测试验证'
    case 'sandbox':
      return '运行验证'
    case 'skill':
      return '自定义技能'
    default:
      return 'Agent'
  }
})

const isAgent = computed(() => props.data.kind !== 'start' && props.data.kind !== 'end')
const specialIcon = computed<Component>(() => (props.data.kind === 'start' ? Play : CircleCheck))
const agentIcon = computed(() => props.data.icon || props.data.kind)
</script>

<template>
  <div
    class="wf-node"
    :class="{ 'wf-node--selected': selected, [`wf-node--${data.issue}`]: !!data.issue }"
    :title="nodeTitle"
  >
    <template v-if="!data.isStart">
      <Handle type="target" :position="Position.Top" class="wf-node__handle" />
    </template>

    <button
      v-if="selected"
      type="button"
      class="wf-node__remove"
      title="删除这个节点（相连的连线会一起删）"
      @click.stop="remove"
    >
      <X :size="12" :stroke-width="2.6" />
    </button>

    <div class="wf-node__body">
      <span class="wf-node__icon">
        <AgentIcon v-if="isAgent" :icon="agentIcon" :size="16" :stroke-width="2" />
        <component v-else :is="specialIcon" :size="16" :stroke-width="2" />
      </span>
      <span class="wf-node__text">
        <span class="wf-node__label">{{ data.label }}</span>
        <span class="wf-node__role">{{ role }}</span>
        <span v-if="data.isGate" class="wf-node__tag wf-node__tag--gate">
          <ShieldCheck :size="10" :stroke-width="2.4" />
          审批闸门
        </span>
        <span v-if="data.issue" class="wf-node__tag" :class="`wf-node__tag--${data.issue}`">
          <AlertTriangle :size="10" :stroke-width="2.4" />
          {{ data.issue === 'error' ? '会失败' : '有提示' }}
        </span>
      </span>
    </div>

    <template v-if="!data.isEnd">
      <Handle type="source" :position="Position.Bottom" class="wf-node__handle" />
    </template>
  </div>
</template>

<style scoped>
.wf-node {
  position: relative;
  width: 208px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 14px;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.16s var(--ease), border-color 0.16s var(--ease);
}
.wf-node:hover {
  box-shadow: var(--shadow-md);
}
.wf-node--selected {
  border-color: rgba(86, 88, 212, 0.6);
  box-shadow: 0 0 0 3px var(--color-accent-ring), var(--shadow-sm);
}
.wf-node--error {
  border-color: color-mix(in srgb, var(--color-danger) 55%, transparent);
}
.wf-node--warn {
  border-color: color-mix(in srgb, var(--color-warning) 50%, transparent);
}
.wf-node__body {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 12px 14px;
}
.wf-node__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  flex-shrink: 0;
}
.wf-node__text {
  min-width: 0;
}
.wf-node__label {
  display: block;
  font-size: var(--fs-13);
  font-weight: 650;
  letter-spacing: -0.01em;
  line-height: 1.25;
}
.wf-node__role {
  display: block;
  font-size: 10.5px;
  color: var(--color-text-tertiary);
}
.wf-node__tag {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-top: 4px;
  margin-right: 5px;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 600;
  color: var(--color-text-secondary);
  background: var(--color-surface-subtle, rgba(127, 127, 127, 0.1));
}
.wf-node__tag--gate {
  color: var(--color-accent);
  background: var(--color-accent-soft);
}
.wf-node__tag--error {
  color: var(--color-danger);
  background: var(--color-danger-soft);
}
.wf-node__tag--warn {
  color: var(--color-warning);
  background: var(--color-warning-soft);
}
.wf-node__remove {
  position: absolute;
  top: -8px;
  right: -8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  color: var(--color-danger);
  background: var(--color-surface);
  border: 1px solid color-mix(in srgb, var(--color-danger) 40%, transparent);
  box-shadow: var(--shadow-sm);
  z-index: 1;
}
.wf-node__remove:hover {
  color: #fff;
  background: var(--color-danger);
}
.wf-node__handle {
  width: 8px;
  height: 8px;
  background: var(--color-surface);
  border: 2px solid var(--color-border-strong);
}
</style>
