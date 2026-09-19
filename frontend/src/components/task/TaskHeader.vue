<script setup lang="ts">
import { computed, ref } from 'vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { ArrowLeft, Copy, Layers, OctagonX } from '@lucide/vue'
import type { Task } from '@/types/task'

const props = defineProps<{ task: Task }>()
const emit = defineEmits<{ back: []; abort: [] }>()

/**
 * 「终止运行」只在真的有可能在跑的时候出现（RUNNING）。
 * 和右侧应用控制条的「停止应用」是两个概念：这个停的是**这次 AI 会话**，那个停的是
 * **生成出来的应用**。所以文案刻意不同、位置也分开（用户实测反馈过容易混）。
 */
const canAbort = computed(() => props.task.status === 'running')

const aborting = ref(false)
async function onAbort() {
  const ok = window.confirm(
    '确定终止这次运行？\n\n· 已经跑完的步骤和产物都会保留\n· 之后可以在项目页点「重新跑开发链」接着跑（不会重跑 PRD）\n· 这不会停止已经生成的应用（那是右侧「停止」按钮的事）'
  )
  if (!ok) return
  aborting.value = true
  emit('abort')
  // 父组件处理完会刷新状态；这里给个短锁防止连点
  window.setTimeout(() => (aborting.value = false), 1500)
}

const roundLabel = computed(() =>
  props.task.rounds.length > 1 ? `第 ${props.task.roundNo} / ${props.task.rounds.length} 轮` : ''
)

const copyPrompt = () => {
  navigator.clipboard?.writeText(props.task.prompt || props.task.title)
}
</script>

<template>
  <header class="task-head">
    <button type="button" class="task-head__back" title="返回任务列表" @click="emit('back')">
      <ArrowLeft :size="16" :stroke-width="2" />
    </button>

    <div class="task-head__main">
      <h1 class="task-head__title">{{ task.title }}</h1>
      <div class="task-head__meta">
        <span class="task-head__meta-item"><Layers :size="12" :stroke-width="2" />项目 #{{ task.id }}</span>
        <span v-if="roundLabel" class="task-head__meta-item">{{ roundLabel }}</span>
        <span v-if="task.steps.length" class="task-head__meta-item">{{ task.steps.length }} 个步骤</span>
      </div>
    </div>

    <div class="task-head__right">
      <button type="button" class="aw-btn aw-btn--ghost aw-btn--sm task-head__copy" title="复制需求描述" @click="copyPrompt">
        <Copy :size="13" :stroke-width="2" />
      </button>
      <button
        v-if="canAbort"
        type="button"
        class="task-head__abort"
        :disabled="aborting"
        title="终止这次 AI 运行（已完成的产物保留）"
        @click="onAbort"
      >
        <OctagonX :size="13" :stroke-width="2" />{{ aborting ? '终止中…' : '终止运行' }}
      </button>
      <StatusBadge :status="task.status" pill with-icon />
    </div>
  </header>
</template>

<style scoped>
.task-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 20px 0 16px;
}
.task-head__abort {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-12);
  padding: 4px 10px;
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-sm, 4px);
  background: transparent;
  color: var(--color-danger);
  cursor: pointer;
}
.task-head__abort:hover:not(:disabled) { background: var(--color-danger); color: #fff; }
.task-head__abort:disabled { opacity: 0.5; cursor: default; }
.task-head__back {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  margin-top: 2px;
  flex-shrink: 0;
  transition: all 0.14s var(--ease);
}
.task-head__back:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.task-head__main {
  flex: 1;
  min-width: 0;
}
.task-head__title {
  font-size: var(--fs-20);
  font-weight: 700;
  letter-spacing: -0.015em;
  line-height: 1.35;
  word-break: break-word;
}
.task-head__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 7px;
}
.task-head__meta-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
}
.task-head__meta-item svg {
  color: var(--color-text-faint);
}
.task-head__right {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 2px;
  flex-shrink: 0;
}
.task-head__copy {
  color: var(--color-text-tertiary);
}
</style>
