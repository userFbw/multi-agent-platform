<script setup lang="ts">
import { computed } from 'vue'
import { mdToHtml } from '@/utils/markdown'

const props = defineProps<{ source: string }>()
const html = computed(() => mdToHtml(props.source))
</script>

<template>
  <div class="aw-md" v-html="html" />
</template>

<style scoped>
.aw-md {
  font-size: var(--fs-14);
  line-height: 1.72;
  color: var(--color-text);
  word-break: break-word;
}
.aw-md :deep(h1),
.aw-md :deep(h2),
.aw-md :deep(h3),
.aw-md :deep(h4) {
  font-weight: 650;
  letter-spacing: -0.01em;
  color: var(--color-text);
  line-height: 1.35;
}
.aw-md :deep(h1) {
  font-size: var(--fs-20);
  margin: 4px 0 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--color-border);
}
.aw-md :deep(h2) {
  font-size: var(--fs-17);
  margin: 22px 0 10px;
}
.aw-md :deep(h3) {
  font-size: var(--fs-15);
  margin: 18px 0 8px;
}
.aw-md :deep(h4) {
  font-size: var(--fs-14);
  margin: 14px 0 6px;
}
.aw-md :deep(p) {
  margin: 0 0 12px;
}
.aw-md :deep(ul),
.aw-md :deep(ol) {
  margin: 0 0 12px;
  padding-left: 22px;
}
.aw-md :deep(li) {
  margin: 3px 0;
}
.aw-md :deep(strong) {
  font-weight: 650;
}
.aw-md :deep(code) {
  font-family: ui-monospace, 'SF Mono', 'Cascadia Code', Consolas, monospace;
  font-size: 0.88em;
  background: var(--color-surface-hover);
  border: 1px solid var(--color-border);
  border-radius: 5px;
  padding: 1px 5px;
}
.aw-md :deep(pre.aw-md__pre) {
  margin: 12px 0;
  padding: 14px 16px;
  background: #f6f7f8;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: auto;
}
.aw-md :deep(pre.aw-md__pre code) {
  background: none;
  border: none;
  padding: 0;
  font-size: var(--fs-12);
  line-height: 1.65;
  color: #33363d;
}
.aw-md :deep(blockquote) {
  margin: 12px 0;
  padding: 4px 14px;
  border-left: 3px solid var(--color-border-strong);
  color: var(--color-text-secondary);
}
.aw-md :deep(a) {
  color: var(--color-accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.aw-md :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 12px 0;
  font-size: var(--fs-13);
}
.aw-md :deep(th),
.aw-md :deep(td) {
  border: 1px solid var(--color-border);
  padding: 7px 12px;
  text-align: left;
}
.aw-md :deep(th) {
  background: var(--color-surface-hover);
  font-weight: 600;
}
.aw-md :deep(hr) {
  border: none;
  border-top: 1px solid var(--color-border);
  margin: 18px 0;
}
</style>
