<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import WelcomeSection from '@/components/workspace/WelcomeSection.vue'
import PromptComposer, { type ComposePayload } from '@/components/workspace/PromptComposer.vue'
import { useTaskStore } from '@/stores/task'
import { deriveTaskTitle } from '@/utils/format'
import { saveProjectChoice } from '@/utils/workflowChoice'

const router = useRouter()
const taskStore = useTaskStore()

const composer = ref<InstanceType<typeof PromptComposer> | null>(null)
const creating = ref(false)
const error = ref('')
/** 进行中的阶段提示（智能编排的"拆解"是同步等待，实测约 8 秒） */
const status = ref('')

const onPrompt = async (payload: ComposePayload) => {
  if (creating.value) return // 拆解要等约 8 秒，期间不接受第二次提交
  error.value = ''
  creating.value = true
  let projectId: string | null = null
  try {
    const title = deriveTaskTitle(payload.prompt)
    status.value = '正在创建项目…'

    // 两种模式的差别只有"图从哪来"：都是先 PM 出 PRD → **审批** → 再继续。
    //   「智能编排」(auto)  → mode='agent'：审批通过后由编排官读 PRD 现场出图，再跑那张图
    //   「指定工作流」(workflow) → mode='workflow'：图现在就定下来，审批后跑它的其余节点
    const isAuto = payload.mode === 'auto'
    const summary = await taskStore.createProject(title, payload.prompt, {
      mode: isAuto ? 'agent' : 'workflow',
      ...(isAuto ? {} : payload.workflow)
    })
    projectId = summary.id

    // 图已经随创建请求定在项目上了；这里存的只是"本项目选过哪张图"的本地记忆，
    // 仅供界面回显（localStorage 丢了也不影响：后端仍按项目上记的图跑）
    if (!isAuto) saveProjectChoice(summary.id, payload.workflow)
    router.push(`/tasks/${summary.id}`)
  } catch (e) {
    const msg = (e as Error).message
    // 项目可能已经建好了（例如编排失败）——把需求放回输入框，并说明项目还在
    composer.value?.setPrompt(payload.prompt)
    error.value = projectId
      ? `${msg}（项目 #${projectId} 已创建，可在任务列表打开，或在编排页重新拆解）`
      : msg
    status.value = ''
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <main class="home">
    <div class="home__inner">
      <WelcomeSection />

      <PromptComposer ref="composer" class="home__composer" :busy="creating" @submit="onPrompt" />

      <p v-if="creating" class="home__status">{{ status || '正在创建项目并启动 Agent…' }}</p>
      <p v-else-if="error" class="home__error">{{ error }}</p>
    </div>
  </main>
</template>

<style scoped>
.home {
  height: 100%;
  display: flex;
  align-items: center;
  overflow-y: auto;
  padding: 48px 24px 40px;
}
.home__inner {
  width: 100%;
  max-width: 840px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.home__composer {
  margin-top: 30px;
}
.home__status {
  margin-top: 16px;
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
}
.home__error {
  margin-top: 16px;
  font-size: var(--fs-12);
  color: var(--color-danger);
  max-width: 560px;
  text-align: center;
}

@media (max-width: 640px) {
  .home {
    padding: 36px 12px 28px;
  }
  .home__composer {
    margin-top: 20px;
  }
}
</style>
