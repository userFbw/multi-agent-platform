<script setup lang="ts">
import { computed, type Component } from 'vue'
import type { TaskStatus } from '@/types/task'
import { STATUS_META } from '@/utils/status'
import { CircleCheck, CircleDashed, CircleStop, LoaderCircle, XCircle } from '@lucide/vue'

const props = defineProps<{ status: TaskStatus; pill?: boolean; withIcon?: boolean }>()

const meta = computed(() => STATUS_META[props.status])

const spinning = computed(() => props.status === 'running')

const icon = computed<Component>(() => {
  switch (props.status) {
    case 'running':
      return LoaderCircle
    case 'completed':
      return CircleCheck
    case 'failed':
      return XCircle
    case 'stopped':
    case 'aborted':
      return CircleStop
    default:
      return CircleDashed
  }
})

const tone = computed(() => meta.value.tone)
</script>

<template>
  <span class="status" :class="pill ? 'status--pill' : 'status--plain'" :data-tone="tone">
    <span class="status__dot" :class="{ 'is-spin': spinning }">
      <component :is="icon" v-if="pill || withIcon || spinning" :size="11" :stroke-width="2.2" />
      <i v-else />
    </span>
    <span class="status__label">{{ meta.label }}</span>
  </span>
</template>

<style scoped>
.status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}
.status--plain {
  color: var(--color-text-secondary);
  font-size: var(--fs-12);
}
.status__dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.status--plain .status__dot i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-text-faint);
}
.status--plain .status__dot.is-spin svg {
  color: var(--color-accent);
  animation: rotate 1.2s linear infinite;
}
.status[data-tone='success'] .status__dot i {
  background: var(--color-success);
}
.status[data-tone='danger'] .status__dot i {
  background: var(--color-danger);
}
.status[data-tone='info'] .status__dot i {
  background: var(--color-accent);
  animation: blink 1.6s ease-in-out infinite;
}

.status--pill {
  height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: var(--fs-11);
  font-weight: 500;
}
.pill--neutral {
  background: var(--color-surface-hover);
  color: var(--color-text-secondary);
}
.status--pill[data-tone='neutral'] {
  background: var(--color-surface-hover);
  color: var(--color-text-secondary);
}
.status--pill[data-tone='info'] {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}
.status--pill[data-tone='success'] {
  background: var(--color-success-soft);
  color: var(--color-success);
}
.status--pill[data-tone='danger'] {
  background: var(--color-danger-soft);
  color: var(--color-danger);
}
.status--pill.is-spin svg,
.status--pill .is-spin svg {
  animation: rotate 1.2s linear infinite;
}

@keyframes rotate {
  to {
    transform: rotate(360deg);
  }
}
@keyframes blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}
</style>
