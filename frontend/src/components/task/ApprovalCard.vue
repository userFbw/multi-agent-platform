<script setup lang="ts">
import { computed, ref } from 'vue'
import Markdown from '@/components/common/Markdown.vue'
import { Check, FileText, GitBranch, ShieldCheck, X } from '@lucide/vue'
import { DEFAULT_WORKFLOW_NAME, WORKFLOW_LABELS } from '@/utils/workflowChoice'
import type { ApproveOptions } from '@/api/project'
import type { Artifact, Task } from '@/types/task'

const props = defineProps<{ task: Task }>()
const emit = defineEmits<{
  approve: [options: ApproveOptions]
  reject: [feedback: string]
  openArtifact: [artifact: Artifact]
}>()

const feedback = ref('')
const showFeedback = ref(false)
/** 「同意 + 补充要求」：这条要求会并进 PRD 一起送下去（以前"同意"时填的东西会被直接丢掉） */
const showNote = ref(false)
const note = ref('')
const submitting = ref(false)

const isPending = computed(() => props.task.status === 'awaiting_approval')
const prd = computed(() => props.task.prd)
const preview = computed(() => (prd.value ? prd.value.split('\n').slice(0, 12).join('\n') : ''))

const prdArtifact = computed<Artifact | null>(() =>
  prd.value ? { id: 'prd', name: 'PRD.md', kind: 'markdown', content: prd.value } : null
)

/* ---- 开发链：**图在建项目时就选定并记在项目上**，这里只做只读展示 ----
 *
 * 以前这里要把首页存的图"背"到审批接口去，现在不需要了：
 * 不传 `workflow_id/workflow_name` → 后端沿用项目上记的那张图，PM 也不会重跑。
 * 传了反而是「中途换图」（会覆盖项目上那张），所以默认绝不传。
 *
 * ⚠️ `task.workflowName` 为空 = 老项目（建项目时还没这个功能）→ 后端用默认模版
 *    「复杂项目」，这里的展示也跟着回落到它。
 */
const isAgentMode = computed(() => props.task.mode === 'agent')
const workflowName = computed(() => props.task.workflowName || DEFAULT_WORKFLOW_NAME)
const workflowLabel = computed(
  () => WORKFLOW_LABELS[workflowName.value]?.label ?? workflowName.value
)
const workflowDesc = computed(() => WORKFLOW_LABELS[workflowName.value]?.desc ?? '')
/** Agent 模式下图还没出（审批之后才由编排官出），所以这里说的是"谁来决定"，不是"哪张图" */
const chainLabel = computed(() => (isAgentMode.value ? '开发链：由编排官自行决定' : `开发链：${workflowLabel.value}`))
const chainDesc = computed(() =>
  isAgentMode.value
    ? '审批通过后，编排官会读这份 PRD 自行决定派哪些 Agent（简单需求只派简单前端 + 测试，复杂需求拆前后端），并立即执行'
    : workflowDesc.value || workflowName.value
)

const approve = () => {
  emit('approve', { feedback: note.value.trim() || undefined })
  note.value = ''
  showNote.value = false
}

const confirmReject = () => {
  emit('reject', feedback.value.trim())
  feedback.value = ''
  showFeedback.value = false
}

</script>

<template>
  <div class="approval" :class="{ 'approval--done': !isPending }">
    <div class="approval__head">
      <span class="approval__icon"><ShieldCheck :size="16" :stroke-width="2" /></span>
      <div class="approval__titles">
        <span class="approval__title">人工审批</span>
        <span class="approval__sub">
          {{ isAgentMode ? 'PM 已生成 PRD，确认后由编排官出图并执行' : 'PM 已生成 PRD，确认后进入代码生成' }}
        </span>
      </div>
      <span class="approval__state">{{ isPending ? '待审批' : '已处理' }}</span>
    </div>

    <button v-if="prdArtifact" type="button" class="approval__prd" @click="emit('openArtifact', prdArtifact)">
      <span class="approval__prd-icon"><FileText :size="15" :stroke-width="2" /></span>
      <span class="approval__prd-body">
        <span class="approval__prd-name">PRD.md</span>
        <span class="approval__prd-hint">点击查看完整 PRD</span>
      </span>
    </button>

    <div class="approval__chain">
      <span class="approval__chain-icon"><GitBranch :size="13" :stroke-width="2" /></span>
      <span class="approval__chain-body">
        <span class="approval__chain-name">{{ chainLabel }}</span>
        <span class="approval__chain-desc">{{ chainDesc }}</span>
      </span>
    </div>

    <div v-if="preview && isPending" class="approval__preview">
      <Markdown :source="preview" />
    </div>

    <div v-if="isPending && showNote && !showFeedback" class="approval__reject">
      <textarea
        v-model="note"
        class="approval__textarea"
        rows="2"
        placeholder="补充要求（可留空），例如：计算器要支持键盘输入与除零提示…"
      />
      <p class="approval__note-hint">
        同意时写的要求会作为「审批时的补充要求」并进 PRD、一起传给后续节点（以前这条会被直接丢掉）。
        若这张图在闸门之后还接了第二个 PM 节点，它会据此改出一版新 PRD，下游读到的就是那一版；
        没有的话，要让 PM 重出一版请用「驳回重做」。
      </p>
    </div>

    <div v-if="isPending" class="approval__actions">
      <template v-if="!showFeedback">
        <button type="button" class="aw-btn aw-btn--default" @click="showFeedback = true">
          <X :size="14" :stroke-width="2" />
          驳回重做
        </button>
        <button
          type="button"
          class="aw-btn aw-btn--default"
          :class="showNote ? 'aw-btn--soft' : ''"
          @click="showNote = !showNote"
        >
          <FileText :size="14" :stroke-width="2" />
          {{ showNote ? '收起补充要求' : '带补充要求同意' }}
        </button>
        <button type="button" class="aw-btn aw-btn--primary" :disabled="submitting" @click="approve">
          <Check :size="14" :stroke-width="2.2" />
          同意开发
        </button>
      </template>

      <div v-else class="approval__reject">
        <textarea
          v-model="feedback"
          class="approval__textarea"
          rows="2"
          placeholder="填写驳回意见，例如：补充权限模型的角色划分…"
        />
        <p class="approval__note-hint">
          驳回会让 PM 按这份意见**重跑一遍、重新出一版 PRD**（图上 PM 只跑一次，所以走的是重跑而不是加节点）。
        </p>
        <div class="approval__reject-actions">
          <button type="button" class="aw-btn aw-btn--ghost aw-btn--sm" @click="showFeedback = false">取消</button>
          <button type="button" class="aw-btn aw-btn--primary aw-btn--sm" @click="confirmReject">
            <X :size="13" :stroke-width="2.2" />
            确认驳回
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.approval {
  background: var(--color-surface);
  border: 1px solid rgba(176, 125, 29, 0.35);
  border-radius: var(--radius-lg);
  box-shadow: 0 0 0 3px rgba(176, 125, 29, 0.08);
  padding: 16px 18px;
}
.approval--done {
  border-color: var(--color-border);
  box-shadow: var(--shadow-xs);
}
.approval__head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.approval__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--color-warning-soft);
  color: var(--color-warning);
  flex-shrink: 0;
}
.approval__titles {
  flex: 1;
  min-width: 0;
}
.approval__title {
  display: block;
  font-size: var(--fs-13);
  font-weight: 650;
}
.approval__sub {
  display: block;
  font-size: 11px;
  color: var(--color-text-tertiary);
}

/* 本项目选定的开发链（只读展示；不传参 = 后端沿用项目上这张图） */
.approval__chain {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 10px;
  padding: 8px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-hover);
}
.approval__chain-icon {
  display: inline-flex;
  align-items: center;
  color: var(--color-text-secondary);
  margin-top: 1px;
}
.approval__chain-body {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.approval__chain-name {
  font-size: var(--fs-12);
  font-weight: 620;
  color: var(--color-text);
}
.approval__chain-desc {
  font-size: 11px;
  line-height: 1.5;
  color: var(--color-text-tertiary);
}
.approval__state {
  font-size: 11px;
  font-weight: 550;
  color: var(--color-warning);
  background: var(--color-warning-soft);
  border-radius: 999px;
  padding: 3px 9px;
  flex-shrink: 0;
}
.approval__prd {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  margin-top: 14px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  text-align: left;
  transition: all 0.14s var(--ease);
}
.approval__prd:hover {
  background: var(--color-surface-hover);
  border-color: var(--color-border-strong);
}
.approval__prd-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  flex-shrink: 0;
}
.approval__prd-name {
  display: block;
  font-family: ui-monospace, 'SF Mono', Consolas, monospace;
  font-size: var(--fs-12);
  font-weight: 600;
}
.approval__prd-hint {
  display: block;
  font-size: 10.5px;
  color: var(--color-text-tertiary);
}
.approval__preview {
  margin-top: 12px;
  max-height: 210px;
  overflow: hidden;
  padding: 12px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-sidebar);
  position: relative;
  font-size: var(--fs-12);
}
.approval__preview::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 36px;
  background: linear-gradient(transparent, var(--color-sidebar));
}
.approval__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
.approval__reject {
  width: 100%;
}
.approval__textarea {
  width: 100%;
  padding: 9px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--fs-12);
  line-height: 1.6;
  resize: vertical;
  outline: none;
  min-height: 54px;
}
.approval__textarea:focus {
  border-color: rgba(86, 88, 212, 0.5);
}
.approval__note-hint {
  margin-top: 6px;
  font-size: var(--fs-12);
  line-height: 1.65;
  color: var(--color-text-tertiary);
}
.approval__reject-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}
</style>
