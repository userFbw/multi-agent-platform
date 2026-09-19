<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, type Component } from 'vue'
import PopoverMenu from '@/components/common/PopoverMenu.vue'
import { useWorkflowStore } from '@/stores/workflow'
import {
  DEFAULT_WORKFLOW_NAME,
  WORKFLOW_LABELS,
  choiceToKey,
  isRunnableAsProject,
  keyToChoice,
  loadDefaultChoice,
  saveDefaultChoice,
  type WorkflowChoice
} from '@/utils/workflowChoice'
import { ImagePlus, Link2, Paperclip, ArrowUp, GitBranch, Sparkles } from '@lucide/vue'

/** 首页的两种模式 */
export type ComposeMode = 'auto' | 'workflow'

export interface ComposePayload {
  prompt: string
  /** auto = 智能编排（编排官读需求自动拆解并执行）；workflow = 指定一张图，**建项目时**就生效 */
  mode: ComposeMode
  /** 仅 mode='workflow' 有意义：随「创建项目」一起提交，图就记在项目上了 */
  workflow: WorkflowChoice
}

const props = withDefaults(defineProps<{ busy?: boolean }>(), { busy: false })

const emit = defineEmits<{
  submit: [payload: ComposePayload]
}>()

const text = ref('')
const focused = ref(false)
const area = ref<HTMLTextAreaElement | null>(null)

const canSend = computed(() => text.value.trim().length > 0 && !props.busy)

const attachItems = computed(() => [
  { key: 'file', label: '上传文件', desc: '添加任务相关的附件', icon: Paperclip as Component, hint: '未开放' },
  { key: 'image', label: '添加图片', desc: '支持截图 / 示意图', icon: ImagePlus as Component, hint: '未开放' },
  { key: 'context', label: '添加上下文', desc: '关联项目文件或知识库', icon: Link2 as Component, hint: '未开放' }
])

/* ---------------- 开发链（这个项目走哪张图）----------------
 * 图在**建项目时**定下来（随 create 请求发出去，后端记在项目上），审批与迭代都沿用它。
 * 两张项目模版决定"要不要建后端"，所以下拉里给的是人话说明而不是模版名。
 * 选择会记到 localStorage（本项目一份 + 「上次选择」一份）—— 只用于回显。 */
const workflowStore = useWorkflowStore()
/** 默认「智能编排」：只写需求即可 */
const mode = ref<ComposeMode>('auto')
const workflowKey = ref(choiceToKey(loadDefaultChoice()))

/**
 * 列表里**全部**列出来，但把"不能拿来建项目"的图置灰并写明原因。
 *
 * 为什么不是直接过滤掉：后端要求项目模版必须含「生成 PRD」的节点，否则建项目 400。
 * 以前这里是静默过滤 —— 用户在画布上存了一张不含 PM 的图，回到首页**找不到它**，
 * 只会以为保存失败或图丢了（实测反馈）。置灰 + 一句原因就没这个误会。
 */
const workflowOptions = computed(() =>
  workflowStore.workflows.map((w) => ({
    key: w.id != null ? `id:${w.id}` : `name:${w.name}`,
    label: WORKFLOW_LABELS[w.name]?.label ?? w.name,
    desc: isRunnableAsProject(w)
      ? w.builtin
        ? `${w.name}：${WORKFLOW_LABELS[w.name]?.desc ?? '内置模版'}`
        : `自定义工作流「${w.name}」`
      : `⚠️ ${w.hint || '不能作为项目模版'}`,
    disabled: !isRunnableAsProject(w)
  }))
)

/** 能选的 key：记住的选择若指向一张已不可用的图，要回落到默认 */
const usableKeys = computed(
  () => new Set(workflowOptions.value.filter((i) => !i.disabled).map((i) => i.key))
)

const workflowItems = computed(() => [
  {
    key: '',
    label: `默认（${WORKFLOW_LABELS[DEFAULT_WORKFLOW_NAME].label}）`,
    desc: `不指定时按「${DEFAULT_WORKFLOW_NAME}」跑：${WORKFLOW_LABELS[DEFAULT_WORKFLOW_NAME].desc}`,
    active: workflowKey.value === '',
    disabled: false
  },
  ...workflowOptions.value.map((i) => ({ ...i, active: workflowKey.value === i.key }))
])

const workflowLabel = computed(
  () => workflowItems.value.find((i) => i.key === workflowKey.value)?.label ?? '默认'
)
const workflowDesc = computed(
  () => workflowItems.value.find((i) => i.key === workflowKey.value)?.desc ?? ''
)

function onWorkflowSelect(key: string) {
  workflowKey.value = key
  saveDefaultChoice(keyToChoice(key))
}

onMounted(async () => {
  await workflowStore.loadWorkflows()
  // 记住的那张图可能已被删掉/改名 → 回落到默认模板，避免审批时拿到 400
  if (workflowKey.value && !usableKeys.value.has(workflowKey.value)) {
    workflowKey.value = ''
    saveDefaultChoice({})
  }
})

const attachHint = ref('')
let attachTimer: number | null = null
function onAttachSelect(key: string) {
  const item = attachItems.value.find((i) => i.key === key)
  attachHint.value = `${item?.label || '附件'}功能暂未开放`
  if (attachTimer !== null) window.clearTimeout(attachTimer)
  attachTimer = window.setTimeout(() => (attachHint.value = ''), 2400)
}

function resize() {
  const el = area.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(Math.max(el.scrollHeight, 52), 300)}px`
}

function focusInput() {
  area.value?.focus()
}

function submit() {
  if (props.busy) return
  const prompt = text.value.trim()
  if (!prompt) return
  emit('submit', { prompt, mode: mode.value, workflow: keyToChoice(workflowKey.value) })
  text.value = ''
  resize()
}

function onKeydown(e: KeyboardEvent) {
  if (e.isComposing || e.key !== 'Enter') return
  if (e.shiftKey) return
  e.preventDefault()
  submit()
}

function setPrompt(p: string) {
  text.value = p
  resize()
  nextTick(() => area.value?.focus())
}

onMounted(resize)
onBeforeUnmount(() => {
  if (attachTimer !== null) window.clearTimeout(attachTimer)
})

defineExpose({ setPrompt, focusInput })
</script>

<template>
  <div class="composer-root">
    <div class="composer" :class="{ 'composer--focused': focused }">
      <div class="composer__modes">
        <div class="aw-segment composer__segment">
          <button
            type="button"
            class="aw-segment__btn"
            :class="{ 'aw-segment__btn--active': mode === 'auto' }"
            @click="mode = 'auto'"
          >
            <Sparkles :size="13" :stroke-width="2" />
            智能编排
          </button>
          <button
            type="button"
            class="aw-segment__btn"
            :class="{ 'aw-segment__btn--active': mode === 'workflow' }"
            @click="mode = 'workflow'"
          >
            <GitBranch :size="13" :stroke-width="2" />
            指定工作流
          </button>
        </div>
        <span class="composer__mode-hint">
          {{ mode === 'auto'
            ? '只写需求：编排官自动拆解步骤并执行'
            : '自己挑一张图，建项目时就按它跑（审批后继续走完）' }}
        </span>
      </div>

      <textarea
        ref="area"
        v-model="text"
        class="composer__area"
        placeholder="输入任务，例如：帮我开发一个用户管理系统…"
        @input="resize"
        @focus="focused = true"
        @blur="focused = false"
        @keydown="onKeydown"
      />

      <div class="composer__toolbar">
        <div class="composer__toolbar-left">
          <PopoverMenu :items="attachItems" align="left" :width="230" @select="onAttachSelect">
            <template #trigger="{ toggle, open }">
              <button type="button" class="attach-btn" :class="{ 'attach-btn--open': open }" title="添加附件" @click.stop="toggle">
                <Paperclip :size="15" :stroke-width="2" />
              </button>
            </template>
          </PopoverMenu>

          <PopoverMenu
            v-if="mode === 'workflow'"
            :items="workflowItems"
            align="left"
            :width="260"
            :icon="GitBranch"
            :label="`开发链：${workflowLabel}`"
            @select="onWorkflowSelect"
          />
        </div>

        <div class="composer__toolbar-right">
          <button
            type="button"
            class="send-btn"
            :class="{ 'send-btn--ready': canSend }"
            :disabled="!canSend"
            :title="busy ? '正在提交…' : '发送任务 (Enter)'"
            @click="submit"
          >
            <ArrowUp :size="17" :stroke-width="2.4" />
          </button>
        </div>
      </div>
    </div>

    <div class="composer__meta">
      <span v-if="attachHint" class="composer__meta-hint">{{ attachHint }}</span>
      <span class="composer__meta-tip">
        <template v-if="mode === 'workflow'">开发链「{{ workflowLabel }}」· {{ workflowDesc }} · </template>
        Enter 发送 · Shift+Enter 换行
      </span>
    </div>
  </div>
</template>

<style scoped>
.composer-root {
  width: 100%;
  max-width: 760px;
}

.composer {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-sm);
  transition: border-color 0.16s var(--ease), box-shadow 0.16s var(--ease);
}
.composer:hover {
  border-color: var(--color-border-strong);
}
.composer--focused {
  border-color: rgba(86, 88, 212, 0.55);
  box-shadow: 0 0 0 3px var(--color-accent-ring), var(--shadow-sm);
}

.composer__area {
  display: block;
  width: 100%;
  min-height: 108px;
  max-height: 300px;
  padding: 18px 18px 6px;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font-size: var(--fs-15);
  line-height: 1.65;
  color: var(--color-text);
}
.composer__area::placeholder {
  color: var(--color-text-tertiary);
}

.composer__modes {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 10px 12px 0;
}
.composer__segment {
  flex-shrink: 0;
}
.composer__segment .aw-segment__btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.composer__mode-hint {
  font-size: var(--fs-11);
  color: var(--color-text-tertiary);
}
.composer__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px 10px;
}
.composer__toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.attach-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  transition: all 0.14s var(--ease);
}
.attach-btn:hover,
.attach-btn--open {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.composer__toolbar-right {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
}

.send-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  margin-left: 4px;
  border-radius: 50%;
  background: var(--color-surface-hover);
  color: var(--color-text-tertiary);
  transition: all 0.18s var(--ease);
}
.send-btn--ready {
  background: var(--color-accent);
  color: #fff;
  box-shadow: 0 2px 8px rgba(86, 88, 212, 0.32);
}
.send-btn--ready:hover {
  background: var(--color-accent-hover);
  transform: translateY(-1px);
}
.send-btn:active {
  transform: scale(0.96);
}

.composer__meta {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding: 12px 4px 0;
}
.composer__meta-hint {
  margin-right: auto;
  font-size: 11px;
  color: var(--color-accent);
  white-space: nowrap;
}
.composer__meta-tip {
  font-size: 11px;
  color: var(--color-text-faint);
  white-space: nowrap;
}
</style>
