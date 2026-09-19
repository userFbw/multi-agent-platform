<script setup lang="ts">
import { computed, type Component } from 'vue'
import {
  Bot,
  Box,
  ClipboardList,
  Code2,
  FileText,
  ShieldCheck,
  Sparkles,
  Workflow
} from '@lucide/vue'

const props = withDefaults(
  defineProps<{ icon?: string; size?: number; strokeWidth?: number }>(),
  { icon: 'general', size: 16, strokeWidth: 1.9 }
)

const MAP: Record<string, Component> = {
  workflow: Workflow,
  planner: Workflow,
  clipboard: ClipboardList,
  pm: ClipboardList,
  code: Code2,
  dev: Code2,
  shield: ShieldCheck,
  qa: ShieldCheck,
  box: Box,
  sandbox: Box,
  sparkles: Sparkles,
  general: Sparkles,
  file: FileText
}

const comp = computed(() => MAP[props.icon] ?? Bot)
</script>

<template>
  <component :is="comp" :size="size" :stroke-width="strokeWidth" />
</template>
