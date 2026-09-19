<script setup lang="ts">
import { computed, ref } from 'vue'
import Markdown from '@/components/common/Markdown.vue'
import { Check, Copy } from '@lucide/vue'

const props = defineProps<{ markdown?: string }>()

const copied = ref(false)

const hasResult = computed(() => Boolean(props.markdown))

const copy = async () => {
  if (!props.markdown) return
  await navigator.clipboard?.writeText(props.markdown)
  copied.value = true
  setTimeout(() => (copied.value = false), 1400)
}
</script>

<template>
  <div v-if="hasResult" class="result">
    <div class="result__head">
      <div class="result__titles">
        <h2 class="result__title">Task Completed</h2>
        <span class="result__sub">Final Result</span>
      </div>
      <button type="button" class="aw-btn aw-btn--default aw-btn--sm" @click="copy">
        <Check v-if="copied" :size="13" :stroke-width="2" class="result__copied" />
        <Copy v-else :size="13" :stroke-width="2" />
        {{ copied ? '已复制' : '复制' }}
      </button>
    </div>
    <div class="result__body">
      <Markdown :source="markdown ?? ''" />
    </div>
  </div>
</template>

<style scoped>
.result {
  margin-top: 26px;
}
.result__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.result__titles {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.result__title {
  font-size: var(--fs-13);
  font-weight: 700;
  letter-spacing: 0.01em;
}
.result__sub {
  font-size: var(--fs-11);
  font-weight: 550;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--color-text-tertiary);
}
.result__copied {
  color: var(--color-success);
}
.result__body {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 20px 22px;
  box-shadow: var(--shadow-xs);
}
</style>
