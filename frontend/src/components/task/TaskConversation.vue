<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AgentIcon from '@/components/common/AgentIcon.vue'
import AgentStep from './AgentStep.vue'
import ApprovalCard from './ApprovalCard.vue'
import TaskResult from './TaskResult.vue'
import { RefreshCw, Send } from '@lucide/vue'
import { formatDuration } from '@/utils/format'
import type { ApproveOptions } from '@/api/project'
import type { Artifact, Task } from '@/types/task'

const props = defineProps<{ task: Task }>()
const emit = defineEmits<{
  approve: [options: ApproveOptions]
  reject: [feedback: string]
  revise: [feedback: string, iteration: 'incremental' | 'regenerate']
  openArtifact: [artifact: Artifact]
}>()

const feedback = ref('')

const running = computed(() => props.task.status === 'running' || props.task.status === 'pending')
const awaiting = computed(() => props.task.status === 'awaiting_approval')
const completed = computed(() => props.task.status === 'completed')
const failed = computed(() => props.task.status === 'failed')
const aborted = computed(() => props.task.status === 'aborted')

const resultMarkdown = computed(() => props.task.qaReport || props.task.prd || '')

/**
 * 「当前进行到哪一步、由哪个 Agent 负责」。
 *
 * 后端现在会在节点**开跑时**把步骤置成 `RUNNING`（以前只有 PENDING → SUCCESS/FAILED，
 * 中途什么都看不到），配合 5 秒轮询就能实时显示。长节点实测 95–115 秒，这一行很有用。
 */
const currentStep = computed(() => props.task.steps.find((s) => s.rawStatus === 'RUNNING') ?? null)

/** 头部这行"已执行 X"要跟着秒针走（只看 5 秒轮询会显得卡住） */
const nowMs = ref(Date.now())
let ticker: number | null = null
watch(
  running,
  (on) => {
    if (on && ticker === null) ticker = window.setInterval(() => (nowMs.value = Date.now()), 1000)
    if (!on && ticker !== null) {
      window.clearInterval(ticker)
      ticker = null
    }
  },
  { immediate: true }
)
onBeforeUnmount(() => ticker !== null && window.clearInterval(ticker))

const currentElapsed = computed(() => {
  const st = currentStep.value
  if (!st?.startedAtMs) return ''
  return formatDuration(nowMs.value - st.startedAtMs)
})
const finishedCount = computed(
  () => props.task.steps.filter((s) => s.rawStatus === 'SUCCESS' || s.rawStatus === 'SKIPPED').length
)

/* 什么状态能「接着跑」：
 *   completed → 提修改意见（迭代）
 *   failed / aborted → 重跑开发链（真机踩到：以前这两个状态**没有入口**，页面只写了一句
 *     「请提出修改意见后重试」，但输入框不渲染 → 项目卡死在失败态，客户没法继续）
 */
const canResume = computed(() => failed.value || aborted.value)

/**
 * 两个按钮对应两种迭代模式（真机需求）：
 *   · 增量修改（默认）：**保留现有代码**，Agent 在原有文件上局部改 —— 便宜、不丢上一版细节
 *   · 重新生成：**先删掉 src/ 再按需求重写** —— 需求大改或想彻底重来时用，代价是重跑整条链
 */
const submitRevise = (iteration: 'incremental' | 'regenerate') => {
  const text = feedback.value.trim()
  // 失败/终止后允许直接重跑：不给意见就用一句默认说明（后端 revise 的 feedback 是必填）
  const payload = text || (canResume.value ? '上一次运行没跑完，请沿用已完成的产物继续把项目做完。' : '')
  if (!payload) return
  if (iteration === 'regenerate') {
    const ok = window.confirm(
      '确定「重新生成」？\n\n· 会**先删掉现在的 src/ 代码**，再按需求从头重写\n' +
      '· 上一版里没写进需求文档的实现细节可能丢失\n' +
      '· 想保留现有代码、只改你要求的部分，请用「增量修改」'
    )
    if (!ok) return
  }
  emit('revise', payload, iteration)
  feedback.value = ''
}
</script>

<template>
  <div class="convo">
    <!-- 用户需求 -->
    <div class="convo__row convo__row--user">
      <div class="convo__bubble convo__bubble--user">{{ task.prompt || '（无需求描述）' }}</div>
    </div>

    <!-- 步骤流 -->
    <div v-if="task.steps.length" class="convo__row">
      <span class="convo__avatar"><AgentIcon icon="planner" :size="15" :stroke-width="2" /></span>
      <div class="convo__col">
        <div class="convo__bubble">
          已拆解为 {{ task.steps.length }} 个步骤，执行情况如下：
        </div>
        <div class="convo__steps">
          <AgentStep v-for="step in task.steps" :key="step.id" :step="step" />
        </div>
      </div>
    </div>

    <!-- 审批（两种模式都在 PM 之后；差别只是通过之后是"按选定的图跑"还是"编排官出图再跑"） -->
    <div v-if="awaiting || (completed && task.prd)" class="convo__block">
      <ApprovalCard
        :task="task"
        @approve="(o) => emit('approve', o)"
        @reject="(f) => emit('reject', f)"
        @open-artifact="(a) => emit('openArtifact', a)"
      />
    </div>

    <!-- 失败 -->
    <div v-else-if="failed" class="convo__note">
      <span class="convo__note-line" />
      <span class="convo__note-text">任务执行失败，请查看步骤错误信息；已完成的产物都保留着，可用下方「重新跑开发链」从当前进度继续（PRD 不会重跑）。</span>
      <span class="convo__note-line" />
    </div>

    <!-- 最终结果 -->
    <div v-if="completed && resultMarkdown" class="convo__block">
      <TaskResult :markdown="resultMarkdown" />
    </div>

    <!-- 运行中：显示"当前进行到哪一步、由哪个 Agent 负责" -->
    <div v-if="running" class="convo__typing">
      <span class="convo__typing-dots"><i /><i /><i /></span>
      <span v-if="currentStep">
        <strong>{{ currentStep.name }}</strong> 执行中
        <span class="convo__typing-by">
          （{{ currentStep.agentName }}<template v-if="currentElapsed"> · 已执行 {{ currentElapsed }}</template>）
        </span>
        · 已完成 {{ finishedCount }}/{{ task.steps.length }}
      </span>
      <span v-else>Agent 正在执行，步骤将实时刷新…</span>
    </div>

    <!-- 完成后提修改意见；失败/已终止后重跑开发链（两种情况都要有入口，别让项目卡死） -->
    <div v-if="completed || canResume" class="convo__revise">
      <input
        v-model="feedback"
        class="convo__revise-input"
        :placeholder="canResume
          ? '写清要改什么，然后选「增量修改」（保代码）或「重新生成」（重写）'
          : '对结果不满意？输入修改意见，让程序员重新修改…'"
        @keydown.enter="submitRevise('incremental')"
      />
      <button
        type="button"
        class="convo__revise-btn"
        :disabled="!feedback.trim() && !canResume"
        :title="'保留现有代码，只改你要求的部分（更省额度、不会丢上一版的实现细节）'"
        @click="submitRevise('incremental')"
      >
        <Send :size="14" :stroke-width="2" />
        增量修改
      </button>
      <button
        type="button"
        class="convo__revise-btn convo__revise-btn--danger"
        :disabled="!feedback.trim() && !canResume"
        :title="'先删掉现在的代码，再按需求从头重写（需求大改时用）'"
        @click="submitRevise('regenerate')"
      >
        <RefreshCw :size="14" :stroke-width="2" />
        重新生成
      </button>
    </div>
  </div>
</template>

<style scoped>
.convo {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.convo__row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.convo__row--user {
  justify-content: flex-end;
}
.convo__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--gradient-logo);
  color: #fff;
  flex-shrink: 0;
  margin-top: 2px;
}
.convo__col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.convo__bubble {
  max-width: 100%;
  padding: 11px 15px;
  font-size: var(--fs-14);
  line-height: 1.7;
  border-radius: var(--radius-xl);
  border-top-left-radius: 4px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  box-shadow: var(--shadow-xs);
  white-space: pre-wrap;
}
.convo__bubble--user {
  max-width: 78%;
  background: var(--color-accent-soft);
  border: none;
  box-shadow: none;
  border-top-right-radius: 4px;
}
.convo__steps {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.convo__block {
  min-width: 0;
}
.convo__note {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 6px;
}
.convo__note-line {
  flex: 1;
  height: 1px;
  background: var(--color-border);
}
.convo__note-text {
  font-size: var(--fs-11);
  color: var(--color-danger);
}
.convo__typing-by {
  color: var(--color-text-faint);
}
.convo__typing strong {
  font-weight: 620;
  color: var(--color-text-secondary);
}
.convo__typing {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-left: 40px;
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
}
.convo__typing-dots {
  display: flex;
  gap: 4px;
}
.convo__typing-dots i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--color-accent);
  animation: bounce 1.2s ease-in-out infinite;
}
.convo__typing-dots i:nth-child(2) {
  animation-delay: 0.15s;
}
.convo__typing-dots i:nth-child(3) {
  animation-delay: 0.3s;
}
.convo__revise {
  display: flex;
  gap: 8px;
  padding-left: 40px;
}
.convo__revise-input {
  flex: 1;
  min-width: 0;
  height: 38px;
  padding: 0 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  font-size: var(--fs-13);
  outline: none;
}
.convo__revise-input:focus {
  border-color: rgba(86, 88, 212, 0.5);
}
.convo__revise-btn--danger {
  border-color: var(--color-danger, #dc2626);
  color: var(--color-danger, #dc2626);
}
.convo__revise-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 38px;
  padding: 0 14px;
  border-radius: var(--radius-md);
  background: var(--color-accent);
  color: #fff;
  font-size: var(--fs-13);
  font-weight: 550;
  flex-shrink: 0;
}
.convo__revise-btn:disabled {
  background: #c9c9ef;
  cursor: not-allowed;
}

@keyframes bounce {
  0%,
  100% {
    transform: translateY(0);
    opacity: 0.5;
  }
  50% {
    transform: translateY(-4px);
    opacity: 1;
  }
}
</style>
