<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { projectApi } from '@/api/project'
import { stepApi } from '@/api/step'
import { agentTypeFromName, mapProjectStatus, type TaskStatus } from '@/types/task'
import type { AgentType } from '@/types/agent'
import { formatDuration } from '@/utils/format'
import { STATUS_META } from '@/utils/status'
import AgentIcon from '@/components/common/AgentIcon.vue'
import { BarChart3, RefreshCw } from '@lucide/vue'

const auth = useAuthStore()

interface ProjectStat {
  id: string
  title: string
  status: TaskStatus
  totalMs: number
  steps: number
  rounds: number
  failed: number
  skipped: number
}

interface AgentStat {
  name: string
  type: AgentType
  runs: number
  totalMs: number
  avgMs: number
  failed: number
}

const MAX_PROJECTS = 20

const loading = ref(false)
const error = ref('')
const projectStats = ref<ProjectStat[]>([])
const agentStats = ref<AgentStat[]>([])

const summary = computed(() => {
  const projects = projectStats.value
  const completed = projects.filter((p) => p.status === 'completed').length
  const failed = projects.filter((p) => p.status === 'failed').length
  const totalMs = projects.reduce((s, p) => s + p.totalMs, 0)
  const totalSteps = agentStats.value.reduce((s, a) => s + a.runs, 0)
  const failedSteps = agentStats.value.reduce((s, a) => s + a.failed, 0)
  return {
    projects: projects.length,
    completed,
    failed,
    avgMs: projects.length ? Math.round(totalMs / projects.length) : 0,
    totalSteps,
    successRate: totalSteps ? Math.round(((totalSteps - failedSteps) / totalSteps) * 100) : 0
  }
})

const maxAgentMs = computed(() => Math.max(1, ...agentStats.value.map((a) => a.totalMs)))
const maxProjectMs = computed(() => Math.max(1, ...projectStats.value.map((p) => p.totalMs)))

const pct = (value: number, max: number) => `${Math.max(2, Math.round((value / max) * 100))}%`

async function load() {
  if (!auth.user) return
  loading.value = true
  error.value = ''
  try {
    const projects = await projectApi.list(auth.user.id)
    const recent = projects.slice(0, MAX_PROJECTS)

    // 逐项目聚合「全部轮次」的步骤（含 revise 重构产生的多轮），而非只看最新一轮
    const results = await Promise.all(
      recent.map(async (p) => {
        try {
          const roundsResp = await stepApi.rounds(p.id)
          const rounds = roundsResp.rounds?.length ? roundsResp.rounds : [roundsResp.latest_round ?? 1]
          const stepSets = await Promise.all(
            rounds.map((r) =>
              stepApi
                .list(p.id, r)
                .then((x) => x.steps ?? [])
                .catch(() => [])
            )
          )
          return { p, steps: stepSets.flat(), rounds: rounds.length }
        } catch {
          return { p, steps: [], rounds: 0 }
        }
      })
    )

    projectStats.value = results.map(({ p, steps, rounds }) => ({
      id: String(p.id),
      title: p.title,
      status: mapProjectStatus(p.status),
      totalMs: steps.reduce((s, x) => s + (x.elapsed_ms ?? 0), 0),
      steps: steps.length,
      rounds,
      failed: steps.filter((x) => x.status === 'FAILED').length,
      skipped: steps.filter((x) => x.status === 'SKIPPED').length
    }))

    const byAgent = new Map<string, AgentStat>()
    for (const { steps } of results) {
      for (const s of steps) {
        if (s.status === 'SKIPPED') continue
        const name = s.agent_name || '未知角色'
        const cur =
          byAgent.get(name) ??
          { name, type: agentTypeFromName(name), runs: 0, totalMs: 0, avgMs: 0, failed: 0 }
        cur.runs += 1
        cur.totalMs += s.elapsed_ms ?? 0
        if (s.status === 'FAILED') cur.failed += 1
        byAgent.set(name, cur)
      }
    }
    agentStats.value = [...byAgent.values()]
      .map((a) => ({ ...a, avgMs: a.runs ? Math.round(a.totalMs / a.runs) : 0 }))
      .sort((a, b) => b.totalMs - a.totalMs)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <main class="stats">
    <div class="stats__head">
      <div>
        <h1 class="stats__title">耗时看板</h1>
        <p class="stats__sub">基于最近 {{ MAX_PROJECTS }} 个项目的全部轮次步骤聚合（数据来自 project_steps）</p>
      </div>
      <button type="button" class="aw-btn aw-btn--default aw-btn--sm" :disabled="loading" @click="load">
        <RefreshCw :size="13" :stroke-width="2" />
        刷新
      </button>
    </div>

    <p v-if="error" class="stats__error">{{ error }}</p>

    <div class="stats__cards">
      <div class="card">
        <span class="card__label">项目数</span>
        <span class="card__value">{{ summary.projects }}</span>
        <span class="card__foot">已完成 {{ summary.completed }} · 失败 {{ summary.failed }}</span>
      </div>
      <div class="card">
        <span class="card__label">平均总耗时</span>
        <span class="card__value">{{ formatDuration(summary.avgMs) }}</span>
        <span class="card__foot">按项目步骤合计</span>
      </div>
      <div class="card">
        <span class="card__label">步骤总数</span>
        <span class="card__value">{{ summary.totalSteps }}</span>
        <span class="card__foot">不含 SKIPPED</span>
      </div>
      <div class="card">
        <span class="card__label">步骤成功率</span>
        <span class="card__value">{{ summary.successRate }}%</span>
        <span class="card__foot">SUCCESS / (SUCCESS+FAILED)</span>
      </div>
    </div>

    <section class="panel">
      <h3 class="panel__title">
        <BarChart3 :size="14" :stroke-width="2" />
        按角色聚合
      </h3>
      <p v-if="loading" class="panel__empty">加载中…</p>
      <p v-else-if="!agentStats.length" class="panel__empty">暂无步骤数据。</p>
      <div v-else class="bars">
        <div v-for="a in agentStats" :key="a.name" class="bar">
          <span class="bar__icon"><AgentIcon :icon="a.type" :size="14" :stroke-width="2" /></span>
          <span class="bar__name">{{ a.name }}</span>
          <span class="bar__track"><span class="bar__fill" :style="{ width: pct(a.totalMs, maxAgentMs) }" /></span>
          <span class="bar__time">{{ formatDuration(a.totalMs) }}</span>
          <span class="bar__meta">{{ a.runs }} 次 · 均 {{ formatDuration(a.avgMs) }}<template v-if="a.failed"> · 失败 {{ a.failed }}</template></span>
        </div>
      </div>
    </section>

    <section class="panel">
      <h3 class="panel__title">按项目聚合</h3>
      <p v-if="loading" class="panel__empty">加载中…</p>
      <p v-else-if="!projectStats.length" class="panel__empty">还没有项目。</p>
      <div v-else class="bars">
        <div v-for="p in projectStats" :key="p.id" class="bar">
          <span class="aw-dot" :class="STATUS_META[p.status].dot" />
          <span class="bar__name bar__name--wide" :title="p.title">{{ p.title }}</span>
          <span class="bar__track"><span class="bar__fill" :style="{ width: pct(p.totalMs, maxProjectMs) }" /></span>
          <span class="bar__time">{{ formatDuration(p.totalMs) }}</span>
          <span class="bar__meta">{{ p.rounds }} 轮 · {{ p.steps }} 步<template v-if="p.failed"> · 失败 {{ p.failed }}</template><template v-if="p.skipped"> · 跳过 {{ p.skipped }}</template></span>
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.stats {
  max-width: 960px;
  margin: 0 auto;
  padding: 36px 28px 64px;
}
.stats__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 22px;
}
.stats__title {
  font-size: var(--fs-24);
  font-weight: 700;
  letter-spacing: -0.02em;
}
.stats__sub {
  margin-top: 4px;
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
}
.stats__error {
  margin-bottom: 14px;
  font-size: var(--fs-12);
  color: var(--color-danger);
}
.stats__cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 22px;
}
.card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xs);
}
.card__label {
  font-size: var(--fs-11);
  color: var(--color-text-tertiary);
}
.card__value {
  font-size: var(--fs-20);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.card__foot {
  font-size: 10.5px;
  color: var(--color-text-faint);
}
.panel {
  margin-bottom: 22px;
  padding: 16px 18px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xs);
}
.panel__title {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 14px;
  font-size: var(--fs-13);
  font-weight: 650;
}
.panel__empty {
  padding: 12px 0;
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
}
.bars {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.bar {
  display: grid;
  grid-template-columns: 20px minmax(90px, 150px) minmax(0, 1fr) 64px minmax(120px, auto);
  align-items: center;
  gap: 10px;
  font-size: var(--fs-12);
}
.bar__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-secondary);
}
.bar__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 550;
}
.bar__name--wide {
  font-weight: 500;
}
.bar__track {
  height: 8px;
  border-radius: 999px;
  background: var(--color-surface-active);
  overflow: hidden;
}
.bar__fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--gradient-logo);
}
.bar__time {
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: var(--color-text-secondary);
}
.bar__meta {
  font-size: 10.5px;
  color: var(--color-text-faint);
  white-space: nowrap;
}
@media (max-width: 760px) {
  .stats {
    padding: 24px 14px 40px;
  }
  .stats__cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .bar {
    grid-template-columns: 20px minmax(70px, 1fr) 56px;
  }
  .bar__track,
  .bar__meta {
    display: none;
  }
}
</style>
