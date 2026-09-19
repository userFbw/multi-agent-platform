<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch, type Component } from 'vue'
import AgentIcon from '@/components/common/AgentIcon.vue'
import { formatDuration } from '@/utils/format'
import type { TaskStep } from '@/types/task'
import { CircleCheck, CircleStop, Clock, FileText, LoaderCircle, XCircle } from '@lucide/vue'

const props = defineProps<{ step: TaskStep }>()

const isRunning = computed(() => props.step.status === 'running')
const isCompleted = computed(() => props.step.status === 'completed')
const isFailed = computed(() => props.step.status === 'failed')
const isStopped = computed(() => props.step.status === 'stopped')

const statusIcon = computed<Component>(() => {
  if (isCompleted.value) return CircleCheck
  if (isRunning.value) return LoaderCircle
  if (isFailed.value) return XCircle
  if (isStopped.value) return CircleStop
  return Clock
})

const tone = computed(() => {
  if (isRunning.value) return 'running'
  if (isFailed.value) return 'failed'
  if (isStopped.value) return 'stopped'
  if (isCompleted.value) return 'done'
  return 'pending'
})

const stateLabel = computed(() => {
  if (isRunning.value) return '进行中'
  if (isCompleted.value) return '完成'
  if (isFailed.value) return '失败'
  if (isStopped.value) return '已跳过'
  return '待执行'
})

/**
 * 失败/异常提示的颜色语义（接口文档 §七）：
 *  - `CONTRACT_SOFTFAIL` 是**软失败**：步骤状态仍是 SUCCESS（下游是 LLM，给文本就读得懂），
 *    只是输出没按技能声明的结构走 —— 用黄色提示，**不要渲染成红色错误**；
 *  - 其余（`ROUTE_MISSING` / `PLACEHOLDER_MISSING` / `SKILL_NOT_FOUND` / `DSH_*`）是硬失败，红色；
 *  - 展示条件是「有 `error` 就展示」——非预期异常可能 error_code 为空、只有 error 文案。
 */
/**
 * 「已执行多久」——进行中的步骤要**实时**显示，否则长节点（实测 95–125 秒，超时上限 15 分钟）
 * 在界面上只有一句"进行中"，用户没法判断它是真在跑还是卡住了。
 *
 * 后端在节点开跑时写 `started_at_ms`（epoch 毫秒），完成时才写 `elapsed_ms`；
 * 所以进行中就用 `Date.now() - startedAtMs` 自己算，每秒刷新一次。
 * 只有正在跑的步骤才起定时器，避免给每个已完成步骤挂一个无用的 interval。
 */
const nowMs = ref(Date.now())
let ticker: number | null = null

const liveElapsedMs = computed(() => {
  if (props.step.elapsedMs) return props.step.elapsedMs      // 已完成：用后端给的最终耗时
  if (!isRunning.value || !props.step.startedAtMs) return null
  return Math.max(0, nowMs.value - props.step.startedAtMs)
})

function startTicker() {
  if (ticker !== null) return
  ticker = window.setInterval(() => (nowMs.value = Date.now()), 1000)
}
function stopTicker() {
  if (ticker !== null) {
    window.clearInterval(ticker)
    ticker = null
  }
}

watch(
  () => isRunning.value,
  (running) => (running ? startTicker() : stopTicker()),
  { immediate: true }
)
onBeforeUnmount(stopTicker)

const isSoftFail = computed(() => props.step.errorCode === 'CONTRACT_SOFTFAIL')
const errorTone = computed(() => (isSoftFail.value ? 'warn' : 'error'))
const errorText = computed(() =>
  isSoftFail.value
    ? `软失败（内容已产出，这一步仍算成功）：${props.step.error ?? ''}`
    : (props.step.error ?? '')
)
</script>

<template>
  <div class="step-wrap">
    <div class="step" :class="`step--${tone}`">
      <span class="step__status" :class="`step__status--${tone}`">
        <component :is="statusIcon" :size="15" :stroke-width="2.2" :class="{ spin: isRunning }" />
      </span>

      <span class="step__avatar">
        <AgentIcon :icon="step.agentType" :size="15" :stroke-width="2" />
      </span>

      <div class="step__who">
        <div class="step__title-row">
          <span class="step__no">#{{ step.stepNo }}</span>
          <span class="step__name">{{ step.name }}</span>
        </div>
        <span class="step__agent">{{ step.agentName }}</span>
      </div>

      <div class="step__spacer" />

      <span v-if="liveElapsedMs" class="step__elapsed" :class="{ 'step__elapsed--live': isRunning }">
        {{ isRunning ? '已执行 ' : '' }}{{ formatDuration(liveElapsedMs) }}
      </span>

      <span class="step__state" :class="`step__state--${tone}`">{{ stateLabel }}</span>
    </div>

    <div v-if="step.error" class="step__error" :class="`step__error--${errorTone}`">
      <span class="step__error-code">{{ step.errorCode || 'ERROR' }}</span>
      <span>{{ errorText }}</span>
    </div>
    <div v-else-if="step.artifactPath" class="step__artifact">
      <FileText :size="12" :stroke-width="2" />
      <span>{{ step.artifactPath }}</span>
    </div>
  </div>
</template>

<style scoped>
.step {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 14px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xs);
  transition: border-color 0.18s var(--ease);
}
.step--running {
  border-color: rgba(86, 88, 212, 0.35);
  box-shadow: 0 0 0 3px var(--color-accent-ring);
}
.step--failed {
  border-color: rgba(207, 71, 71, 0.4);
}
.step--stopped {
  opacity: 0.7;
}
.step__status {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--color-surface-hover);
  color: var(--color-text-faint);
}
.step__status--running {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}
.step__status--done {
  background: var(--color-success-soft);
  color: var(--color-success);
}
.step__status--failed {
  background: var(--color-danger-soft);
  color: var(--color-danger);
}
.spin {
  animation: rot 1.1s linear infinite;
}
.step__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--color-surface-hover);
  color: var(--color-text-secondary);
  flex-shrink: 0;
}
.step--running .step__avatar {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}
.step__who {
  min-width: 0;
}
.step__title-row {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
.step__no {
  font-size: 10.5px;
  color: var(--color-text-faint);
  font-variant-numeric: tabular-nums;
}
.step__name {
  font-size: var(--fs-13);
  font-weight: 650;
  letter-spacing: -0.01em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.step__agent {
  display: block;
  font-size: 11px;
  color: var(--color-text-tertiary);
  margin-top: 1px;
}
.step__spacer {
  flex: 1;
}
.step__elapsed--live {
  color: var(--color-accent);
  font-variant-numeric: tabular-nums;
}
.step__elapsed {
  font-size: var(--fs-11);
  color: var(--color-text-faint);
  font-variant-numeric: tabular-nums;
}
.step__state {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 550;
  flex-shrink: 0;
  background: var(--color-surface-hover);
  color: var(--color-text-secondary);
}
.step__state--running {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}
.step__state--done {
  background: var(--color-success-soft);
  color: var(--color-success);
}
.step__state--failed {
  background: var(--color-danger-soft);
  color: var(--color-danger);
}
.step__artifact,
.step__error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 6px 0 0 46px;
  font-size: var(--fs-11);
}
.step__artifact {
  color: var(--color-text-tertiary);
}
.step__artifact svg {
  color: var(--color-text-faint);
}
.step__error {
  color: var(--color-danger);
}
.step__error--warn {
  color: var(--color-warning);
}
.step__error-code {
  font-family: ui-monospace, Consolas, monospace;
  font-weight: 600;
}

@keyframes rot {
  to {
    transform: rotate(360deg);
  }
}
</style>
