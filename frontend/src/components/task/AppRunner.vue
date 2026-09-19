<script setup lang="ts">
/**
 * AppRunner —— 生成项目的「应用运行」控制条（S2-4）
 *
 * 客户要的是"点开就能看到正在跑的项目"，所以这块必须回答三个问题：
 *   ① 现在跑没跑？（状态徽标 + 端口 + 启动耗时）
 *   ② 没跑我该点哪？（启动 / 停止 / 重启）
 *   ③ 起不来是为什么？（应用日志抽屉：装依赖日志尾部 + 运行输出尾部）
 *
 * 状态与地址都读 store（AppRunner 和预览区共用一份，不各拉各的接口）。
 * 端口被**别的项目**占着时（应用单实例），后端返回 409 → 这里问一句"要切换吗"，
 * 确认后才带 force=true 去停掉对方。
 */
import { computed, ref } from 'vue'
import { useTaskStore } from '@/stores/task'
import type { Task } from '@/types/task'
import { projectApi } from '@/api/project'
import { ChevronDown, ExternalLink, Play, RotateCw, ScrollText, Square } from '@lucide/vue'

const props = defineProps<{ task: Task }>()
const store = useTaskStore()

const logsOpen = ref(false)
const logsLoading = ref(false)
const installTail = ref('')
const appLog = ref<string[]>([])
const busy = ref<'' | 'start' | 'stop' | 'restart'>('')

const isApp = computed(() => props.task.previewKind === 'app')
const running = computed(() => props.task.appStatus === 'running')
const occupiedBy = computed(() => store.appFeedback?.occupiedBy ?? null)

const badge = computed(() => {
  switch (props.task.appStatus) {
    case 'running': return { text: '运行中', tone: 'ok' as const }
    case 'occupied': return { text: '端口被占用', tone: 'warn' as const }
    case 'stopped': return { text: '已停止', tone: 'muted' as const }
    default: return { text: '未启动', tone: 'muted' as const }
  }
})

async function onStart(force = false) {
  busy.value = 'start'
  await store.startApp(props.task.id, force)
  busy.value = ''
}

async function onStop() {
  busy.value = 'stop'
  await store.stopApp(props.task.id)
  busy.value = ''
}

async function onRestart() {
  busy.value = 'restart'
  await store.restartApp(props.task.id)
  busy.value = ''
}

async function toggleLogs() {
  logsOpen.value = !logsOpen.value
  if (!logsOpen.value) return
  logsLoading.value = true
  try {
    const r = await projectApi.appLogs(Number(props.task.id), 300)
    installTail.value = r.install_log_tail || '（还没有安装日志：依赖装过就不再重装）'
    appLog.value = r.app_log ?? []
  } catch (e) {
    installTail.value = `读取日志失败：${(e as Error).message}`
    appLog.value = []
  } finally {
    logsLoading.value = false
  }
}
</script>

<template>
  <!-- 纯前端项目没有后端进程，这块整个不出现（不打扰简单项目） -->
  <section v-if="isApp" class="runner">
    <div class="runner__bar">
      <span class="runner__title">应用运行</span>
      <span class="runner__badge" :class="`runner__badge--${badge.tone}`">
        <i class="runner__dot" />{{ badge.text }}
      </span>
      <span v-if="task.appPort" class="runner__meta">端口 {{ task.appPort }}</span>
      <span v-if="running && task.appUrl" class="runner__meta">{{ task.appUrl }}</span>

      <span class="runner__spacer" />

      <button
        v-if="!running"
        class="runner__btn runner__btn--primary"
        :disabled="busy !== ''"
        @click="onStart(false)"
      >
        <Play :size="13" :stroke-width="2" />{{ busy === 'start' ? '启动中…' : '启动应用' }}
      </button>
      <template v-else>
        <button class="runner__btn" :disabled="busy !== ''" @click="onRestart">
          <RotateCw :size="13" :stroke-width="2" />{{ busy === 'restart' ? '重启中…' : '重启' }}
        </button>
        <button class="runner__btn" :disabled="busy !== ''" @click="onStop">
          <Square :size="13" :stroke-width="2" />{{ busy === 'stop' ? '停止中…' : '停止' }}
        </button>
      </template>
      <button class="runner__btn" :class="{ 'runner__btn--on': logsOpen }" @click="toggleLogs">
        <ScrollText :size="13" :stroke-width="2" />日志
        <ChevronDown :size="12" :stroke-width="2" :class="{ 'runner__chev--up': logsOpen }" />
      </button>
    </div>

    <!-- 反馈：成功一句；被别的项目占端口则直接给"切换"按钮（S2-3） -->
    <p
      v-if="store.appFeedback"
      class="runner__note"
      :class="`runner__note--${store.appFeedback.kind}`"
    >
      {{ store.appFeedback.text }}
      <button
        v-if="occupiedBy"
        class="runner__switch"
        :disabled="busy !== ''"
        @click="onStart(true)"
      >
        停掉项目 {{ occupiedBy }} 并启动本项目
      </button>
    </p>
    <p v-else-if="running && task.appUrl" class="runner__note runner__note--ok">
      客户可以直接打开
      <a :href="task.appUrl" target="_blank" rel="noreferrer" class="runner__link">
        {{ task.appUrl }}<ExternalLink :size="11" :stroke-width="2" />
      </a>
    </p>

    <div v-if="logsOpen" class="runner__logs">
      <p v-if="logsLoading" class="runner__logs-loading">正在读取日志…</p>
      <template v-else>
        <p class="runner__logs-title">依赖安装（尾部）</p>
        <pre class="runner__pre">{{ installTail }}</pre>
        <p class="runner__logs-title">运行输出（尾部 {{ appLog.length }} 行）</p>
        <pre class="runner__pre">{{ appLog.length ? appLog.join('\n') : '（还没有运行输出）' }}</pre>
      </template>
    </div>
  </section>
</template>

<style scoped>
.runner {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 8px 10px;
  margin-bottom: 10px;
  background: var(--color-surface, transparent);
}
.runner__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.runner__title {
  font-size: var(--fs-12);
  font-weight: 600;
}
.runner__spacer { flex: 1; }
.runner__badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-11, 11px);
  padding: 1px 7px;
  border-radius: 999px;
  border: 1px solid var(--color-border);
}
.runner__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.runner__badge--ok { color: var(--color-success, #16a34a); }
.runner__badge--warn { color: var(--color-warning, #d97706); }
.runner__badge--muted { color: var(--color-text-tertiary); }
.runner__meta {
  font-size: var(--fs-11, 11px);
  color: var(--color-text-tertiary);
}
.runner__btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-11, 11px);
  padding: 3px 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm, 4px);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.runner__btn:hover:not(:disabled) { border-color: var(--color-primary); color: var(--color-primary); }
.runner__btn:disabled { opacity: 0.5; cursor: default; }
.runner__btn--primary { border-color: var(--color-primary); color: var(--color-primary); }
.runner__btn--on { border-color: var(--color-primary); color: var(--color-primary); }
.runner__chev--up { transform: rotate(180deg); }
.runner__note {
  margin: 6px 0 0;
  font-size: var(--fs-11, 11px);
  line-height: 1.6;
  color: var(--color-text-tertiary);
}
.runner__note--ok { color: var(--color-text-secondary); }
.runner__note--warn { color: var(--color-warning, #d97706); }
.runner__note--error { color: var(--color-danger); }
.runner__switch {
  margin-left: 6px;
  font-size: var(--fs-11, 11px);
  padding: 2px 8px;
  border: 1px solid currentColor;
  border-radius: var(--radius-sm, 4px);
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.runner__link {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--color-primary);
  text-decoration: none;
}
.runner__logs { margin-top: 8px; }
.runner__logs-loading { font-size: var(--fs-11, 11px); color: var(--color-text-tertiary); }
.runner__logs-title {
  margin: 6px 0 2px;
  font-size: var(--fs-11, 11px);
  color: var(--color-text-tertiary);
}
.runner__pre {
  margin: 0;
  max-height: 160px;
  overflow: auto;
  padding: 6px 8px;
  font-size: 11px;
  line-height: 1.5;
  background: var(--color-sidebar);
  border-radius: var(--radius-sm, 4px);
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
