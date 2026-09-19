<script setup lang="ts">
/**
 * PromptViewer —— 长文本（技能提示词正文）的展示组件。
 *
 * 为什么单独做一个：内置技能的 SKILL.md 正文有 **2.6–4KB**，
 * 直接塞进 `<pre>` 会把抽屉撑得极长（实测反馈："不要一股脑全部直接显示，可以换成滚轮或者点击放大加滚轮"）。
 * 所以这里给三件事：
 *   1. 限高 + 内部滚动（`max-height` + `overflow:auto`，滚到尽头不带着整页滚）；
 *   2. 「放大查看」：全屏遮罩里再看，仍然是可滚动的一整块；
 *   3. 「复制」：提示词太长时用户多半是想拿去改，复制比选中更方便。
 *
 * Agent 页与技能页共用一个实现，样式/交互不会再各写一份。
 */
import { computed, ref, watch } from 'vue'
import { Copy, Expand, X } from '@lucide/vue'

const props = withDefaults(
  defineProps<{
    text: string | null | undefined
    /** 折叠区高度（px） */
    maxHeight?: number
    /** 顶部元信息，如 "pm-workflow · 2667 字 · 来自 SKILL.md" */
    meta?: string
    emptyText?: string
  }>(),
  { maxHeight: 220, meta: '', emptyText: '（未提供提示词）' }
)

const body = computed(() => props.text?.trim() || '')
const charCount = computed(() => body.value.length)
const zoomed = ref(false)
const copied = ref(false)
let copyTimer: number | null = null

async function copy() {
  if (!body.value) return
  try {
    await navigator.clipboard.writeText(body.value)
  } catch {
    /* 剪贴板不可用（非 https / 权限）时静默失败，用户仍可手动选中 */
  }
  copied.value = true
  if (copyTimer !== null) window.clearTimeout(copyTimer)
  copyTimer = window.setTimeout(() => (copied.value = false), 1600)
}

// 放大态下按 Esc 关闭
watch(zoomed, (on) => {
  if (on) document.addEventListener('keydown', onKey)
  else document.removeEventListener('keydown', onKey)
})
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') zoomed.value = false
}
</script>

<template>
  <div class="pv">
    <div class="pv__bar">
      <span class="pv__meta">{{ meta || `${charCount} 字` }}</span>
      <span class="pv__actions">
        <button v-if="body" type="button" class="pv__btn" @click="copy">
          <Copy :size="12" :stroke-width="2" />
          {{ copied ? '已复制' : '复制' }}
        </button>
        <button v-if="body" type="button" class="pv__btn" @click="zoomed = true">
          <Expand :size="12" :stroke-width="2" />
          放大查看
        </button>
      </span>
    </div>

    <pre v-if="body" class="pv__pre" :style="{ maxHeight: `${maxHeight}px` }">{{ body }}</pre>
    <p v-else class="pv__empty">{{ emptyText }}</p>

    <!-- 放大态：全屏遮罩 + 可滚动正文 -->
    <Teleport to="body">
      <div v-if="zoomed" class="pv-modal" @click.self="zoomed = false">
        <div class="pv-modal__panel">
          <div class="pv-modal__head">
            <span class="pv-modal__title">{{ meta || '提示词正文' }}</span>
            <span class="pv-modal__count">{{ charCount }} 字</span>
            <button type="button" class="pv__btn" @click="copy">
              <Copy :size="12" :stroke-width="2" />
              {{ copied ? '已复制' : '复制' }}
            </button>
            <button type="button" class="pv-modal__close" title="关闭（Esc）" @click="zoomed = false">
              <X :size="15" :stroke-width="2" />
            </button>
          </div>
          <pre class="pv-modal__pre">{{ body }}</pre>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.pv {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.pv__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 11px;
  color: var(--color-text-faint);
}
.pv__meta {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pv__actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
.pv__btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  font-size: 11px;
  color: var(--color-text-secondary);
  transition: all 0.14s var(--ease);
}
.pv__btn:hover {
  border-color: var(--color-border-strong);
  color: var(--color-text);
}
.pv__pre {
  margin: 0;
  overflow: auto;
  overscroll-behavior: contain; /* 滚到尽头别带着整页滚 */
  font-family: ui-monospace, Consolas, monospace;
  font-size: var(--fs-12);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--color-sidebar);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 10px 12px;
  color: var(--color-text);
}
.pv__empty {
  margin: 0;
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
}

/* —— 放大态 —— */
.pv-modal {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px;
  background: rgba(15, 23, 42, 0.45);
}
.pv-modal__panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: min(920px, 100%);
  max-height: 90vh;
  padding: 16px 18px;
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg, 0 18px 48px rgba(15, 23, 42, 0.24));
}
.pv-modal__head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.pv-modal__title {
  font-size: var(--fs-13);
  font-weight: 620;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pv-modal__count {
  font-size: 11px;
  color: var(--color-text-faint);
  margin-right: auto;
}
.pv-modal__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
}
.pv-modal__close:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.pv-modal__pre {
  margin: 0;
  overflow: auto;
  overscroll-behavior: contain;
  font-family: ui-monospace, Consolas, monospace;
  font-size: var(--fs-12);
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--color-sidebar);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 12px 14px;
}
</style>
