<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useTaskStore } from '@/stores/task'
import { useAuthStore } from '@/stores/auth'
import { projectApi } from '@/api/project'
import type { ApproveOptions } from '@/api/project'
import TaskHeader from '@/components/task/TaskHeader.vue'
import TaskConversation from '@/components/task/TaskConversation.vue'
import ArtifactPanel from '@/components/task/ArtifactPanel.vue'
import AppRunner from '@/components/task/AppRunner.vue'
import Markdown from '@/components/common/Markdown.vue'
import { fromNow } from '@/utils/format'
import type { Artifact } from '@/types/task'
import { AlertTriangle, FileText, LoaderCircle, RefreshCw, X } from '@lucide/vue'

const route = useRoute()
const router = useRouter()
const store = useTaskStore()
const auth = useAuthStore()
const { tasks } = storeToRefs(store)

const previewArtifact = ref<Artifact | null>(null)

const id = computed(() => String(route.params.id))
const task = computed(() => tasks.value[id.value] ?? null)
const active = computed(() =>
  task.value ? ['pending', 'running', 'awaiting_approval'].includes(task.value.status) : false
)

async function load() {
  store.select(id.value)
  await store.loadTask(id.value)
  if (active.value) store.startPolling(id.value)
}

watch(id, load, { immediate: true })

// 状态从终态回到活动（点了审批 / 迭代 / 续跑）时也要把轮询开起来 —— 只靠 load() 那次判断不够
watch(active, (on) => {
  if (on) store.startPolling(id.value)
  else store.stopPolling()
})

onBeforeUnmount(() => store.stopPolling())

const goBack = () => {
  if (window.history.length > 1) router.back()
  else router.push('/tasks')
}

const onApprove = (opts?: ApproveOptions) => store.approve(id.value, opts)
const onReject = (f: string) => store.reject(id.value, f)
const onRevise = (f: string) => store.revise(id.value, f)
const onAbort = () => store.abortRun(id.value)
const onResume = () => store.resumeAfterAbort(id.value)

const requestPreview = () => store.loadTask(id.value, { silent: true })
// 这一跳转带不上 Authorization 头，显式带 user_id 走过渡期兼容路径
const download = () => window.open(projectApi.downloadUrl(Number(id.value), auth.user?.id), '_blank')

/**
 * 什么时候用 markdown 渲染、什么时候用代码块。
 * 光看后缀不够：`.md` 里装 JSON 的情况真实存在（审查类 Agent 声明 json_object 契约，
 * 产物仍落成 `<步骤名>.md`）—— 喂给 markdown 渲染器会变成一大段排版散掉的文字。
 */
const looksLikeJson = (text?: string) => /^\s*[[{]/.test(text || '')
const isMarkdown = (a: Artifact) =>
  (a.kind === 'markdown' || a.kind === 'report') && !looksLikeJson(a.content)
</script>

<template>
  <main class="detail">
    <div v-if="task" class="detail__grid">
      <div class="detail__main">
        <TaskHeader :task="task" @back="goBack" @abort="onAbort" />

        <div v-if="active" class="detail__sync">
          <RefreshCw :size="12" :stroke-width="2" :class="{ spin: store.polling }" />
          <span>
            实时刷新 · 5s
            <template v-if="store.lastSyncAt"> · {{ fromNow(store.lastSyncAt) }}同步</template>
          </span>
        </div>

        <p v-if="store.error" class="detail__error">{{ store.error }}</p>

        <!-- 形态预检（后端在「待审批」时算的）：PRD 判的运行形态与这张图对不上时提醒一句。
             只提示不阻断 —— 是不是换图由用户自己决定。 -->
        <p v-if="task.planWarning" class="detail__plan-warn">
          <AlertTriangle :size="13" :stroke-width="2.2" />
          <span>{{ task.planWarning }}</span>
        </p>

        <!-- 终止反馈（刚点完「终止运行」） -->
        <p
          v-if="store.abortFeedback"
          class="detail__abort-note"
          :class="{ 'detail__abort-note--bad': store.abortFeedback.kind === 'error' }"
        >
          {{ store.abortFeedback.text }}
        </p>

        <!-- 已终止（客户自己按停的）：说明清楚 + 给续跑入口，别让页面停在"看起来挂了"的状态 -->
        <div v-if="task.status === 'aborted'" class="detail__aborted">
          <p class="detail__aborted-title">本次运行已被手动终止</p>
          <p class="detail__aborted-text">
            已经跑完的步骤和产物都保留着；被终止那一步没跑完。想继续的话点右边按钮 ——
            PRD 不会重跑，从架构拆解那一步往后接着做（会重新生成代码目录）。
          </p>
          <button type="button" class="aw-btn aw-btn--primary aw-btn--sm" @click="onResume">
            重新跑开发链
          </button>
        </div>

        <TaskConversation
          :task="task"
          @approve="onApprove"
          @reject="onReject"
          @revise="onRevise"
          @open-artifact="(a) => (previewArtifact = a)"
        />
      </div>

      <aside class="detail__aside">
        <div class="detail__aside-sticky">
          <!-- 应用运行控制（S2-4）：只有前后端分离项目才渲染 -->
          <AppRunner :task="task" />
          <ArtifactPanel
            :task="task"
            @open-artifact="(a) => (previewArtifact = a)"
            @preview="requestPreview"
            @download="download"
          />
        </div>
      </aside>
    </div>

    <div v-else-if="store.loading" class="detail__loading">
      <LoaderCircle :size="18" :stroke-width="2" class="spin" />
      <span>正在加载项目…</span>
    </div>

    <div v-else class="detail__missing">
      <div class="aw-empty">
        <span class="aw-empty__icon"><FileText :size="20" :stroke-width="1.8" /></span>
        <h3>任务不存在</h3>
        <p>{{ store.error || '这个项目可能已被删除，或者链接有误。' }}</p>
        <button type="button" class="aw-btn aw-btn--default" @click="router.push('/tasks')">返回任务列表</button>
      </div>
    </div>

    <!-- 产物预览 -->
    <transition name="overlay-fade">
      <div v-if="previewArtifact" class="overlay" @click.self="previewArtifact = null">
        <div class="overlay__panel">
          <div class="overlay__head">
            <span class="overlay__title">{{ previewArtifact.name }}</span>
            <button type="button" class="overlay__close" @click="previewArtifact = null">
              <X :size="16" :stroke-width="2" />
            </button>
          </div>
          <div class="overlay__body">
            <Markdown v-if="previewArtifact.content && isMarkdown(previewArtifact)" :source="previewArtifact.content" />
            <pre v-else class="overlay__code">{{ previewArtifact.content }}</pre>
          </div>
        </div>
      </div>
    </transition>
  </main>
</template>

<style scoped>
.detail {
  padding: 4px 28px 48px;
  max-width: 1080px;
  margin: 0 auto;
}
.detail__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 288px;
  gap: 40px;
  align-items: start;
}
.detail__main {
  min-width: 0;
}
.detail__aside-sticky {
  position: sticky;
  top: 18px;
  padding-top: 70px;
}
.detail__sync {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 14px 44px;
  font-size: var(--fs-11);
  color: var(--color-text-tertiary);
}
.detail__sync svg {
  color: var(--color-accent);
}
.spin {
  animation: rot 1.2s linear infinite;
}
.detail__error {
  margin: 0 0 14px 44px;
  font-size: var(--fs-12);
  color: var(--color-danger);
}
.detail__abort-note {
  margin: 0 0 14px 44px;
  font-size: var(--fs-12);
  color: var(--color-text-secondary);
}
.detail__abort-note--bad { color: var(--color-danger); }
.detail__plan-warn {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  margin: 0 0 14px 44px;
  padding: 9px 11px;
  border: 1px solid var(--color-warn-border, var(--color-border));
  border-radius: var(--radius-md);
  background: var(--color-warn-bg, var(--color-sidebar));
  font-size: var(--fs-12);
  line-height: 1.6;
  color: var(--color-text-secondary);
}
.detail__plan-warn svg { flex: none; margin-top: 2px; color: var(--color-warn, var(--color-danger)); }
.detail__aborted {
  margin: 0 0 14px 44px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-sidebar);
}
.detail__aborted-title {
  margin: 0 0 4px;
  font-size: var(--fs-12);
  font-weight: 600;
}
.detail__aborted-text {
  margin: 0 0 8px;
  font-size: var(--fs-12);
  line-height: 1.6;
  color: var(--color-text-tertiary);
}
.detail__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 18vh 0;
  color: var(--color-text-tertiary);
  font-size: var(--fs-13);
}
.detail__loading svg {
  color: var(--color-accent);
}
.detail__missing {
  display: flex;
  justify-content: center;
  padding-top: 18vh;
}

.overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: rgba(15, 16, 19, 0.4);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px;
}
.overlay__panel {
  width: min(760px, 100%);
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
}
.overlay__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid var(--color-border);
}
.overlay__title {
  font-family: ui-monospace, 'SF Mono', Consolas, monospace;
  font-size: var(--fs-13);
  font-weight: 600;
}
.overlay__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-md);
  color: var(--color-text-tertiary);
}
.overlay__close:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.overlay__body {
  flex: 1;
  overflow: auto;
  padding: 18px 22px;
}
.overlay__code {
  font-family: ui-monospace, 'SF Mono', Consolas, monospace;
  font-size: var(--fs-12);
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--color-text);
}
.overlay__hint {
  font-size: var(--fs-13);
  color: var(--color-text-secondary);
  line-height: 1.8;
}
.overlay__hint code {
  font-family: ui-monospace, Consolas, monospace;
  background: var(--color-surface-hover);
  border-radius: 4px;
  padding: 1px 5px;
}

.overlay-fade-enter-active,
.overlay-fade-leave-active {
  transition: opacity 0.16s var(--ease);
}
.overlay-fade-enter-from,
.overlay-fade-leave-to {
  opacity: 0;
}

@keyframes rot {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1080px) {
  .detail {
    padding: 4px 18px 40px;
  }
  .detail__grid {
    grid-template-columns: 1fr;
    gap: 28px;
  }
  .detail__aside-sticky {
    position: static;
    padding-top: 0;
  }
}
</style>
