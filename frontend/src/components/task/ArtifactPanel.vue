<script setup lang="ts">
import { computed, ref, watch, type Component } from 'vue'
import {
  AlertTriangle,
  AppWindow,
  ArrowDownToLine,
  Bug,
  ExternalLink,
  FileJson,
  FileText,
  FolderOpen,
  Layers,
  LoaderCircle,
  Package,
  RefreshCw,
  ScrollText
} from '@lucide/vue'
import { useTaskStore } from '@/stores/task'
import Markdown from '@/components/common/Markdown.vue'
import { projectApi } from '@/api/project'
import { fromNow } from '@/utils/format'
import type { ApiRunLogItem } from '@/types/api'
import type { Artifact, Task, TaskArtifact } from '@/types/task'

const props = defineProps<{ task: Task }>()
const emit = defineEmits<{
  openArtifact: [artifact: Artifact]
  preview: []
  download: []
}>()

type Tab = 'artifacts' | 'preview' | 'logs'
const tab = ref<Tab>('artifacts')

const artifacts = computed<Artifact[]>(() => {
  const list: Artifact[] = []
  if (props.task.prd) list.push({ id: 'prd', name: 'PRD.md', kind: 'markdown', content: props.task.prd })
  if (props.task.qaReport)
    list.push({ id: 'qa', name: 'QA测试报告.md', kind: 'report', content: props.task.qaReport })
  if (props.task.status === 'completed') {
    list.push({ id: 'zip', name: '项目交付包.zip', kind: 'data', hint: props.task.zipPath || '可下载' })
  }
  return list
})

/**
 * 每一步 Agent 的产出（后端 artifacts 清单）。
 *
 * 为什么单独一组：项目里的「产物」以前只有 PRD / 测试报告 / 交付包 ——
 * 画布拖出来的自定义 Agent、编排官临时加的节点，产出落在项目根（如 `审查计算器代码.md`），
 * 用户只能在运行日志里翻。这里按**每一步**列出来，点开看全文。
 */
const nodeArtifacts = computed<TaskArtifact[]>(() => props.task.nodeArtifacts ?? [])

const openingPath = ref<string | null>(null)
const nodeError = ref('')

const artifactIcon = (a: TaskArtifact): Component => {
  if (a.isDir) return FolderOpen
  return a.kind === 'json' ? FileJson : FileText
}

/** 目录产物 → 合并后的代码；.md 走 markdown 渲染，其余按代码块显示 */
const openNodeArtifact = async (a: TaskArtifact) => {
  if (!a.path || !a.exists) return
  nodeError.value = ''
  openingPath.value = a.path
  try {
    const res = await projectApi.artifactContent(Number(props.task.id), a.path)
    emit('openArtifact', {
      id: `step-${a.roundNo}-${a.stepNo}`,
      name: res.name || a.name,
      kind: a.kind === 'markdown' ? 'markdown' : 'code',
      content: res.content
    } as Artifact)
  } catch (e) {
    nodeError.value = (e as Error).message
  } finally {
    openingPath.value = null
  }
}

const tabs: Array<{ key: Tab; label: string; icon: Component }> = [
  { key: 'artifacts', label: '产物', icon: Layers },
  { key: 'preview', label: '预览', icon: AppWindow },
  { key: 'logs', label: '运行日志', icon: ScrollText }
]

const store = useTaskStore()

const counts = computed<Record<Tab, number>>(() => ({
  artifacts: artifacts.value.length + nodeArtifacts.value.filter((a) => a.path).length,
  preview: props.task.previewUrl ? 1 : 0,
  logs: logItems.value.length
}))

const kindIcon: Record<Artifact['kind'], Component> = {
  markdown: FileText,
  report: FileText,
  code: FileText,
  data: Package
}

const onArtifact = (a: Artifact) => {
  if (a.kind === 'data') emit('download')
  else emit('openArtifact', a)
}

/* ---------------- 预览注入（F1-11 / F1-13） ----------------
 * 生成项目页面里的 API 基址需要可注入：这里抓取预览 HTML，
 * 在 <head> 里补 <base>（保证相对资源正确解析）并注入 window.__API_BASE__，
 * 再用 srcdoc 渲染，避免生成页面把 /api 打到平台自身（配合后端 C-7）。
 * 抓取失败时回退为直接 iframe src，不影响展示。
 *
 * ⚠️ 注入值用独立变量 VITE_PREVIEW_API_BASE，**不能复用 VITE_API_BASE**：
 * 后者是平台自身请求基址（api/http.ts），改它会把整个平台打坏（F1-13）。
 */
const previewApiBase = (import.meta.env.VITE_PREVIEW_API_BASE as string | undefined) ?? ''
const previewSrcdoc = ref('')
const previewSrc = ref('')

/* ★ app 模式（前后端分离）不能走 srcdoc：
 *   srcdoc iframe 继承**平台页面**的来源，而生成的应用是用根路径 `/api` 请求自己后端的，
 *   在 srcdoc 里会打到平台自己的 /api 上（404 / 串味）。所以 app 模式直接给 iframe 一个
 *   真实地址（http://<当前主机名>:8100/），让它以**自己的来源**加载，/api 自然对得上。
 *   注入 window.__API_BASE__ 那套只对"纯前端静态页"有意义，保持原样。
 */
const isAppPreview = computed(() => props.task.previewKind === 'app')

/* 预览地址要能被**客户自己打开**（S2-6）：展示出来 + 一键复制。
 * 地址由 store 按"部署方声明的公网地址优先"拼好（见 utils/previewUrl.ts），这里只负责呈现。 */
const copied = ref(false)
const copying = ref(false)
const starting = ref(false)

async function copyAppUrl() {
  if (!props.task.appUrl) return
  copying.value = true
  try {
    await navigator.clipboard.writeText(props.task.appUrl)
    copied.value = true
    window.setTimeout(() => (copied.value = false), 1500)
  } catch {
    copied.value = false
  } finally {
    copying.value = false
  }
}

/** 预览区直接起应用（不用再翻到上面的控制条），起完自动刷新预览与地址 */
async function startFromPreview() {
  starting.value = true
  await store.startApp(props.task.id, false)
  starting.value = false
  await store.loadTask(props.task.id, { silent: true })
}

watch(
  () => props.task.previewUrl,
  async (url) => {
    previewSrcdoc.value = ''
    previewSrc.value = ''
    if (!url) return
    if (isAppPreview.value) {
      previewSrc.value = url          // 真来源直接加载，不做抓取与注入
      return
    }
    try {
      const abs = new URL(url, window.location.origin)
      const res = await fetch(abs.href)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const html = await res.text()
      const baseHref = abs.href.replace(/[^/]*$/, '')
      const inject =
        `<base href="${baseHref}">` +
        `<script>window.__API_BASE__=${JSON.stringify(previewApiBase)};<\/script>`
      previewSrcdoc.value = /<head[^>]*>/i.test(html)
        ? html.replace(/<head[^>]*>/i, (m) => m + inject)
        : inject + html
    } catch {
      previewSrc.value = url
    }
  },
  { immediate: true }
)

/* ---------------- 运行日志 / 项目BUG（接口文档 §2.8） ----------------
 * 每个 Agent 节点一份「运行日志」（含 00-总览），每次代码真跑一份「项目BUG」。
 * 文件名带步骤号、与步骤卡片的 step_no 对应；SKIPPED 的步骤没有日志文件。
 * ⚠️ 文件名是中文，取正文时原样回传（api 层已 encodeURIComponent）。
 */
const logItems = ref<ApiRunLogItem[]>([])
const logRoundNo = ref<number | null>(null)
const logsLoading = ref(false)
const logsError = ref('')
const activeLogName = ref<string | null>(null)
const logContent = ref('')
const logContentLoading = ref(false)
const logContentError = ref('')
const logsLoaded = ref(false)

/** 运行日志在前（含总览），项目BUG 在后 —— 后端已排序，这里只做分组 */
const logGroups = computed(() => {
  const groups: Array<{ kind: string; items: ApiRunLogItem[] }> = []
  for (const item of logItems.value) {
    let g = groups.find((x) => x.kind === item.kind)
    if (!g) {
      g = { kind: item.kind, items: [] }
      groups.push(g)
    }
    g.items.push(item)
  }
  return groups
})

const sizeText = (bytes: number) => (bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`)

/** modified 是**秒级**时间戳（本接口特有），换算后再交给 fromNow */
const timeText = (seconds: number) => fromNow(new Date(seconds * 1000).toISOString())

async function loadLogs(force = false) {
  if (logsLoading.value) return
  if (logsLoaded.value && !force) return
  logsLoading.value = true
  logsError.value = ''
  try {
    const res = await projectApi.runLogs(Number(props.task.id))
    logItems.value = res.items ?? []
    logRoundNo.value = res.round_no ?? null
    logsLoaded.value = true
    if (activeLogName.value && !logItems.value.some((i) => i.name === activeLogName.value)) {
      activeLogName.value = null
      logContent.value = ''
    }
  } catch (e) {
    logsError.value = (e as Error).message
  } finally {
    logsLoading.value = false
  }
}

async function openLog(item: ApiRunLogItem) {
  if (activeLogName.value === item.name && logContent.value) return
  activeLogName.value = item.name
  logContent.value = ''
  logContentError.value = ''
  logContentLoading.value = true
  try {
    const res = await projectApi.runLog(Number(props.task.id), item.name)
    logContent.value = res.content ?? ''
  } catch (e) {
    logContentError.value = (e as Error).message
  } finally {
    logContentLoading.value = false
  }
}

// 切到日志页时按需拉取；任务换轮次/状态变化后重新拉
// ⚠️ 依赖写成字符串：数组字面量每次求值都是新引用，会导致 5s 轮询时把正在看的日志重置
watch(tab, (next) => {
  if (next === 'logs') void loadLogs()
})
watch(
  () => `${props.task.id}|${props.task.roundNo}|${props.task.status}`,
  () => {
    logsLoaded.value = false
    logItems.value = []
    activeLogName.value = null
    logContent.value = ''
    if (tab.value === 'logs') void loadLogs(true)
  }
)
</script>

<template>
  <div class="panel">
    <div class="panel__head">
      <div class="panel__tabs">
        <button
          v-for="t in tabs"
          :key="t.key"
          type="button"
          class="panel__tab"
          :class="{ 'panel__tab--active': tab === t.key }"
          @click="tab = t.key"
        >
          <component :is="t.icon" :size="14" :stroke-width="2" />
          {{ t.label }}
          <span v-if="counts[t.key]" class="panel__tab-count">{{ counts[t.key] }}</span>
        </button>
      </div>
    </div>

    <div class="panel__actions">
      <button type="button" class="aw-btn aw-btn--default aw-btn--sm" @click="emit('preview')">
        <RefreshCw :size="13" :stroke-width="2" />
        获取预览
      </button>
      <button type="button" class="aw-btn aw-btn--default aw-btn--sm" @click="emit('download')">
        <ArrowDownToLine :size="13" :stroke-width="2" />
        下载 ZIP
      </button>
    </div>

    <div class="panel__body">
      <!-- 产物 -->
      <div v-if="tab === 'artifacts'">
        <div v-if="artifacts.length" class="panel__list">
          <button v-for="a in artifacts" :key="a.id" type="button" class="row" @click="onArtifact(a)">
            <span class="row__icon"><component :is="kindIcon[a.kind]" :size="14" :stroke-width="2" /></span>
            <span class="row__body">
              <span class="row__name">{{ a.name }}</span>
              <span v-if="a.hint" class="row__path">{{ a.hint }}</span>
            </span>
            <ArrowDownToLine v-if="a.kind === 'data'" :size="14" :stroke-width="2" class="row__action" />
          </button>
        </div>
        <p v-else class="panel__empty">暂无产物</p>

        <!-- 每一步 Agent 的产出：自定义 / 新增节点的结果在这里露出 -->
        <div v-if="nodeArtifacts.length" class="panel__group">
          <p class="panel__group-head">各 Agent 产出（{{ nodeArtifacts.length }}）</p>
          <p class="panel__group-hint">
            按执行顺序列出每一步的原始产出，点开看全文（失败或没产出文件的步骤也会列出）。
          </p>
          <div class="panel__list">
            <button
              v-for="a in nodeArtifacts"
              :key="`${a.roundNo}-${a.stepNo}`"
              type="button"
              class="row"
              :class="{ 'row--muted': !a.path || !a.exists }"
              :disabled="!a.path || !a.exists"
              @click="openNodeArtifact(a)"
            >
              <span class="row__icon"><component :is="artifactIcon(a)" :size="14" :stroke-width="2" /></span>
              <span class="row__body">
                <span class="row__name">{{ a.name }}</span>
                <span class="row__path">
                  step {{ a.stepNo }} · {{ a.skill || a.agentName || '未绑定技能' }}
                  <template v-if="a.isDir"> · 代码目录 {{ a.files }} 个文件</template>
                  <template v-else-if="a.path"> · {{ a.path }}</template>
                </span>
                <span v-if="!a.path" class="row__note">
                  <AlertTriangle :size="11" :stroke-width="2.2" />
                  {{ a.error ? `没有产出：${a.error}` : '这一步没有产出文件' }}
                </span>
                <span v-else-if="!a.exists" class="row__note">
                  <AlertTriangle :size="11" :stroke-width="2.2" />
                  文件已不在磁盘上（可能被清理过）
                </span>
              </span>
              <!-- ⚠️ 必须带上 `a.path &&`：`openingPath` 初值是 null，而中断/失败步骤的
                   `a.path` 也是 null，只写 `openingPath === a.path` 会让这些行永远显示转圈
                   （真机现象：任务早就完成了，右侧栏那几个被中断的 agent 产出一直转）。 -->
              <LoaderCircle
                v-if="a.path && openingPath === a.path"
                :size="14"
                :stroke-width="2"
                class="row__action spin"
              />
              <FileText v-else-if="a.path && a.exists" :size="14" :stroke-width="2" class="row__action" />
            </button>
          </div>
          <p v-if="nodeError" class="panel__error">{{ nodeError }}</p>
        </div>
      </div>

      <!-- 预览 -->
      <div v-else-if="tab === 'preview'">
        <div v-if="task.previewUrl" class="preview">
          <iframe
            v-if="previewSrcdoc"
            class="preview__frame"
            :srcdoc="previewSrcdoc"
            title="项目预览"
          />
          <iframe v-else class="preview__frame" :src="previewSrc || task.previewUrl" title="项目预览" />
          <a class="preview__open" :href="task.previewUrl" target="_blank" rel="noreferrer">
            <ExternalLink :size="13" :stroke-width="2" />
            在新窗口打开
          </a>
        </div>
        <p v-else class="panel__empty">
          {{ task.status === 'completed' ? '暂无可预览的网页成果（非 Web 项目或尚未生成）' : '任务完成后可获取在线预览' }}
        </p>
        <div v-if="task.previewKind === 'app'" class="panel__addr">
          <span class="panel__addr-label">地址</span>
          <code class="panel__addr-text">{{ task.appUrl }}</code>
          <button class="panel__addr-copy" type="button" @click="copyAppUrl">
            {{ copied ? '已复制' : '复制' }}
          </button>
        </div>
        <p v-if="task.previewKind === 'app' && task.appStatus !== 'running'" class="panel__hint">
          这个项目的后端还没在运行，页面打不开。
          <button class="panel__hint-btn" type="button" :disabled="starting" @click="startFromPreview">
            {{ starting ? '启动中…' : '启动应用' }}
          </button>
        </p>
      </div>

      <!-- 运行日志 / 项目BUG -->
      <div v-else class="logs">
        <div class="logs__bar">
          <span class="logs__meta">
            <template v-if="logRoundNo">第 {{ logRoundNo }} 轮 · {{ logItems.length }} 份</template>
            <template v-else>每个 Agent 节点一份，跑完可查</template>
          </span>
          <button
            type="button"
            class="logs__refresh"
            :disabled="logsLoading"
            title="刷新日志清单"
            @click="loadLogs(true)"
          >
            <RefreshCw :size="12" :stroke-width="2" :class="{ spin: logsLoading }" />
          </button>
        </div>

        <p v-if="logsError" class="panel__error">{{ logsError }}</p>
        <p v-else-if="logsLoading && !logItems.length" class="panel__empty">加载中…</p>
        <p v-else-if="!logItems.length" class="panel__empty">
          暂无运行日志。若步骤被标记「已跳过」，该步骤不会实例化 Agent，因此没有日志文件（列表缺号属正常）。
        </p>

        <template v-else>
          <div v-for="g in logGroups" :key="g.kind" class="logs__group">
            <span class="logs__group-title">
              <Bug v-if="g.kind === '项目BUG'" :size="11" :stroke-width="2" />
              <ScrollText v-else :size="11" :stroke-width="2" />
              {{ g.kind }}
            </span>
            <div class="panel__list">
              <button
                v-for="item in g.items"
                :key="item.name"
                type="button"
                class="row logs__row"
                :class="{ 'logs__row--active': activeLogName === item.name }"
                @click="openLog(item)"
              >
                <span class="row__body">
                  <span class="row__name">{{ item.name }}</span>
                  <span class="row__path">{{ sizeText(item.size) }} · {{ timeText(item.modified) }}</span>
                </span>
              </button>
            </div>
          </div>

          <div class="logs__viewer">
            <p v-if="logContentError" class="panel__error">{{ logContentError }}</p>
            <p v-else-if="logContentLoading" class="panel__empty">加载中…</p>
            <template v-else-if="logContent">
              <div class="logs__viewer-head">
                <span class="row__name">{{ activeLogName }}</span>
                <button type="button" class="logs__close" @click="activeLogName = null; logContent = ''">收起</button>
              </div>
              <div class="logs__markdown"><Markdown :source="logContent" /></div>
            </template>
            <p v-else class="panel__empty">选一份日志查看正文。</p>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
}
.panel__head {
  margin-bottom: 10px;
}
.panel__tabs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 2px;
  padding: 2px;
  background: var(--color-surface-active);
  border-radius: var(--radius-md);
}
.panel__tab {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  height: 28px;
  border-radius: 7px;
  font-size: var(--fs-11);
  font-weight: 550;
  color: var(--color-text-secondary);
  transition: all 0.14s var(--ease);
}
.panel__tab:hover {
  color: var(--color-text);
}
.panel__tab--active {
  background: var(--color-surface);
  color: var(--color-text);
  box-shadow: var(--shadow-xs);
}
.panel__tab-count {
  font-size: 10px;
  color: var(--color-text-tertiary);
}
.panel__actions {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.panel__actions .aw-btn {
  flex: 1;
}
.panel__list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.row {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 8px;
  border-radius: var(--radius-md);
  text-align: left;
  transition: background 0.13s var(--ease);
}
.row:hover {
  background: var(--color-surface-hover);
}
.row__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 8px;
  background: var(--color-surface-active);
  color: var(--color-text-secondary);
  flex-shrink: 0;
}
.row__body {
  flex: 1;
  min-width: 0;
}
.row__name {
  display: block;
  font-family: ui-monospace, 'SF Mono', Consolas, monospace;
  font-size: var(--fs-12);
  font-weight: 550;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row__path {
  display: block;
  font-size: 10.5px;
  color: var(--color-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row__action {
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}
/* 没有产物的步骤（失败/跳过）：列出来但明显不可点，省得用户以为"这一步怎么没结果" */
.row--muted {
  opacity: 0.62;
  cursor: default;
}
.row--muted:hover {
  background: transparent;
}
.row__note {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 2px;
  font-size: 10.5px;
  color: var(--color-warning);
}
.panel__group {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
}
.panel__group-head {
  padding: 0 8px;
  font-size: var(--fs-12);
  font-weight: 650;
}
.panel__group-hint {
  padding: 3px 8px 7px;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--color-text-tertiary);
}
.panel__empty {
  padding: 16px 8px;
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
  line-height: 1.6;
}
.panel__error {
  padding: 10px 8px;
  font-size: var(--fs-12);
  color: var(--color-danger);
  line-height: 1.6;
}
.panel__hint {
  padding: 8px 8px 0;
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
  line-height: 1.6;
}
.panel__hint-btn {
  margin-left: 4px;
  font-size: var(--fs-11, 11px);
  padding: 1px 8px;
  border: 1px solid var(--color-primary);
  border-radius: var(--radius-sm, 4px);
  background: transparent;
  color: var(--color-primary);
  cursor: pointer;
}
.panel__hint-btn:disabled { opacity: 0.5; cursor: default; }
.panel__addr {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px 0;
  font-size: var(--fs-11, 11px);
  color: var(--color-text-tertiary);
}
.panel__addr-label { flex: none; }
.panel__addr-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-secondary);
}
.panel__addr-copy {
  flex: none;
  font-size: var(--fs-11, 11px);
  padding: 1px 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm, 4px);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.preview {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.preview__frame {
  width: 100%;
  height: 260px;
  border: none;
  background: var(--color-sidebar);
}
.preview__open {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  font-size: var(--fs-12);
  color: var(--color-accent);
  border-top: 1px solid var(--color-border);
}

/* ---- 运行日志 ---- */
.logs__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 4px 6px;
}
.logs__meta {
  font-size: 10.5px;
  color: var(--color-text-tertiary);
}
.logs__refresh {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 7px;
  color: var(--color-text-tertiary);
  transition: all 0.13s var(--ease);
}
.logs__refresh:hover:not(:disabled) {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.logs__group + .logs__group {
  margin-top: 8px;
}
.logs__group-title {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0 4px 4px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--color-text-tertiary);
}
.logs__row--active {
  background: var(--color-accent-soft);
}
.logs__viewer {
  margin-top: 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  overflow: hidden;
}
.logs__viewer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--color-border);
}
.logs__close {
  font-size: 10.5px;
  color: var(--color-text-tertiary);
}
.logs__close:hover {
  color: var(--color-text);
}
.logs__markdown {
  max-height: 320px;
  overflow: auto;
  padding: 12px 14px;
  font-size: var(--fs-12);
}

.spin {
  animation: rot 0.9s linear infinite;
}
@keyframes rot {
  to {
    transform: rotate(360deg);
  }
}
</style>
