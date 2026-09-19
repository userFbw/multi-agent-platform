<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useTaskStore } from '@/stores/task'
import { useAuthStore } from '@/stores/auth'
import { STATUS_META } from '@/utils/status'
import type { TaskStatus, TaskSummary } from '@/types/task'
import { projectApi } from '@/api/project'
import { ArrowDownToLine, ChevronRight, Layers, Plus, RefreshCw, Search, Trash2 } from '@lucide/vue'

const router = useRouter()
const store = useTaskStore()
const auth = useAuthStore()

onMounted(() => store.loadProjects())

const keyword = ref('')
const filter = ref<'all' | TaskStatus>('all')
const removing = ref<string | null>(null)

const filters: Array<{ key: 'all' | TaskStatus; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'running', label: '执行中' },
  { key: 'awaiting_approval', label: '待审批' },
  { key: 'completed', label: '已完成' },
  { key: 'failed', label: '失败' }
]

const countFor = (key: 'all' | TaskStatus) =>
  key === 'all' ? store.list.length : store.list.filter((t) => t.status === key).length

const filtered = computed(() => {
  let items = store.list
  if (filter.value !== 'all') items = items.filter((t) => t.status === filter.value)
  const kw = keyword.value.trim().toLowerCase()
  if (kw) items = items.filter((t) => t.title.toLowerCase().includes(kw))
  return items
})

const goTask = (t: TaskSummary) => router.push(`/tasks/${t.id}`)
const newTask = () => router.push('/')

const download = (t: TaskSummary) => {
  // 这一跳转带不上 Authorization 头，显式带 user_id 走过渡期兼容路径
  window.open(projectApi.downloadUrl(Number(t.id), auth.user?.id), '_blank')
}

const remove = async (t: TaskSummary) => {
  if (!window.confirm(`确认彻底删除「${t.title}」及其源码、ZIP 与 AI 记录？`)) return
  removing.value = t.id
  try {
    await store.removeProject(t.id)
  } catch (e) {
    window.alert((e as Error).message)
  } finally {
    removing.value = null
  }
}
</script>

<template>
  <main class="tasks">
    <div class="tasks__head">
      <div>
        <h1 class="tasks__title">任务</h1>
        <p class="tasks__sub">所有项目任务的执行记录</p>
      </div>
      <div class="tasks__head-actions">
        <button type="button" class="aw-btn aw-btn--default" :disabled="store.loading" @click="store.loadProjects()">
          <RefreshCw :size="14" :stroke-width="2" :class="{ spin: store.loading }" />
          刷新
        </button>
        <button type="button" class="aw-btn aw-btn--primary aw-btn--lg" @click="newTask">
          <Plus :size="16" :stroke-width="2.2" />
          新建任务
        </button>
      </div>
    </div>

    <div class="tasks__toolbar">
      <div class="tasks__search">
        <Search :size="14" :stroke-width="2" />
        <input v-model="keyword" class="tasks__search-input" placeholder="搜索任务…" />
      </div>
      <div class="aw-segment">
        <button
          v-for="f in filters"
          :key="f.key"
          type="button"
          class="aw-segment__btn"
          :class="{ 'aw-segment__btn--active': filter === f.key }"
          @click="filter = f.key"
        >
          {{ f.label }}
          <span class="tasks__count">{{ countFor(f.key) }}</span>
        </button>
      </div>
    </div>

    <p v-if="store.error" class="tasks__error">{{ store.error }}</p>

    <div v-if="filtered.length" class="tasks__list">
      <div class="tasks__row-head">
        <span class="tasks__cell tasks__cell--title">任务</span>
        <span class="tasks__cell">状态</span>
        <span class="tasks__cell tasks__cell--act">操作</span>
      </div>

      <div v-for="t in filtered" :key="t.id" class="tasks__row">
        <button type="button" class="tasks__cell tasks__cell--title" @click="goTask(t)">
          <span class="tasks__row-main">
            <span class="tasks__row-title">{{ t.title }}</span>
          </span>
        </button>
        <span class="tasks__cell">
          <span class="tasks__status">
            <span class="aw-dot" :class="STATUS_META[t.status].dot"></span>
            {{ STATUS_META[t.status].label }}
          </span>
        </span>
        <span class="tasks__cell tasks__cell--act">
          <button type="button" class="tasks__icon-btn" title="下载 ZIP" @click.stop="download(t)">
            <ArrowDownToLine :size="14" :stroke-width="2" />
          </button>
          <button
            type="button"
            class="tasks__icon-btn tasks__icon-btn--danger"
            title="删除项目"
            :disabled="removing === t.id"
            @click.stop="remove(t)"
          >
            <Trash2 :size="14" :stroke-width="2" />
          </button>
          <button type="button" class="tasks__icon-btn" title="查看详情" @click.stop="goTask(t)">
            <ChevronRight :size="15" :stroke-width="2" />
          </button>
        </span>
      </div>
    </div>

    <div v-else class="tasks__empty">
      <div class="aw-empty">
        <span class="aw-empty__icon"><Layers :size="20" :stroke-width="1.8" /></span>
        <h3>没有匹配的任务</h3>
        <p>换个关键词，或者创建一个新任务让 Agent 开工。</p>
        <button type="button" class="aw-btn aw-btn--primary" @click="newTask">
          <Plus :size="15" :stroke-width="2" />
          新建任务
        </button>
      </div>
    </div>
  </main>
</template>

<style scoped>
.tasks {
  max-width: 1040px;
  margin: 0 auto;
  padding: 36px 28px 64px;
}
.tasks__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}
.tasks__head-actions {
  display: flex;
  gap: 8px;
}
.tasks__title {
  font-size: var(--fs-24);
  font-weight: 700;
  letter-spacing: -0.02em;
}
.tasks__sub {
  margin-top: 4px;
  font-size: var(--fs-13);
  color: var(--color-text-tertiary);
}
.spin {
  animation: rot 1.1s linear infinite;
}

.tasks__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 18px;
}
.tasks__search {
  display: flex;
  align-items: center;
  gap: 8px;
  width: min(300px, 100%);
  height: 34px;
  padding: 0 12px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-tertiary);
}
.tasks__search:focus-within {
  border-color: rgba(86, 88, 212, 0.5);
}
.tasks__search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: var(--fs-13);
}
.tasks__count {
  font-size: 11px;
  color: var(--color-text-tertiary);
  background: var(--color-surface-hover);
  border-radius: 999px;
  padding: 0 6px;
  line-height: 15px;
}
.tasks__error {
  margin-bottom: 14px;
  font-size: var(--fs-12);
  color: var(--color-danger);
}

.tasks__list {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.tasks__row-head,
.tasks__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 120px 132px;
  align-items: center;
  gap: 12px;
}
.tasks__row-head {
  padding: 10px 16px;
  font-size: var(--fs-11);
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--color-text-tertiary);
  border-bottom: 1px solid var(--color-border);
  background: var(--color-sidebar);
}
.tasks__row {
  padding: 14px 16px;
  border-bottom: 1px solid var(--color-border);
  transition: background 0.13s var(--ease);
}
.tasks__row:last-child {
  border-bottom: none;
}
.tasks__row:hover {
  background: var(--color-surface-hover);
}
.tasks__cell {
  font-size: var(--fs-13);
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}
.tasks__cell--title {
  color: var(--color-text);
  width: 100%;
}
.tasks__row-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.tasks__row-title {
  font-weight: 550;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tasks__status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.tasks__cell--act {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
}
.tasks__icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-md);
  color: var(--color-text-tertiary);
  transition: all 0.13s var(--ease);
}
.tasks__icon-btn:hover {
  background: var(--color-surface-active);
  color: var(--color-text);
}
.tasks__icon-btn--danger:hover {
  background: var(--color-danger-soft);
  color: var(--color-danger);
}
.tasks__empty {
  padding: 40px 0;
}

@keyframes rot {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 720px) {
  .tasks {
    padding: 24px 14px 40px;
  }
  .tasks__row-head {
    display: none;
  }
  .tasks__row {
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .tasks__row .tasks__cell:nth-child(2) {
    display: none;
  }
}
</style>
