<script setup lang="ts">
/**
 * 左栏 Agent 的「悬停放大详情卡」。
 *
 * 用户诉求原话：左边栏列出所有 Agent，鼠标移上去放大显示详情，"这样用户就知道每个节点该干嘛了"。
 * 所以这里只做一件事：把 agentBrief() 拼好的字段**展示清楚**，不做任何业务判断。
 * 纯展示组件：不持有 store、不发请求（数据由父组件传入）。
 */
import { FileText } from '@lucide/vue'
import type { AgentBrief } from '@/utils/agentBrief'

defineProps<{ brief: AgentBrief }>()
const emit = defineEmits<{ openFull: [] }>()

const CONTRACT_CN: Record<string, string> = {
  text: '纯文本',
  json_object: 'JSON 对象',
  json_array: 'JSON 数组',
  file_blocks: '代码文件块'
}
</script>

<template>
  <div class="brief">
    <div class="brief__head">
      <span class="brief__name">{{ brief.title }}</span>
      <span class="brief__kind">{{ brief.kindLabel }}</span>
    </div>

    <p class="brief__keys">
      <code>{{ brief.roleKey }}</code>
      <template v-if="brief.skillId">
        · 技能 <code>{{ brief.skillId }}</code>
      </template>
      <template v-else>· 无绑定技能（提示词即角色）</template>
    </p>

    <p v-if="brief.summary" class="brief__summary">{{ brief.summary }}</p>
    <p v-else class="brief__summary brief__summary--empty">（这个 Agent 没有填写说明）</p>

    <div class="brief__row">
      <span class="brief__label">产出</span>
      <span class="brief__value">
        {{ CONTRACT_CN[brief.contract] ?? brief.contract }}
        <span v-if="brief.canBeDecisionSource" class="brief__tag">可作为条件分支的判据</span>
      </span>
    </div>

    <div v-if="brief.inputs.length" class="brief__row">
      <span class="brief__label">需要的输入</span>
      <span class="brief__value">
        <template v-for="(i, idx) in brief.inputs" :key="i.name">
          <code>{{ i.name }}</code><template v-if="idx < brief.inputs.length - 1">、</template>
        </template>
        <span class="brief__hint">（拖到画布后由引擎自动组装，不用手填）</span>
      </span>
    </div>
    <div v-else class="brief__row">
      <span class="brief__label">需要的输入</span>
      <span class="brief__value">上游节点的产出文本（自动送达）</span>
    </div>

    <div class="brief__foot">
      <span class="brief__drag">按住拖到画布即可添加节点</span>
      <button v-if="brief.truncated" type="button" class="brief__more" @click.stop="emit('openFull')">
        <FileText :size="11" :stroke-width="2" />
        查看完整内容
      </button>
    </div>
  </div>
</template>

<style scoped>
.brief {
  width: 320px;
  padding: 12px 13px;
  font-size: var(--fs-12);
  line-height: 1.6;
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
}
.brief__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.brief__name {
  font-size: var(--fs-13);
  font-weight: 650;
}
.brief__kind {
  margin-left: auto;
  font-size: 10px;
  color: var(--color-text-tertiary);
  white-space: nowrap;
}
.brief__keys {
  margin-top: 3px;
  font-size: 10.5px;
  color: var(--color-text-tertiary);
}
.brief__keys code,
.brief__value code {
  padding: 0 3px;
  border-radius: 4px;
  background: var(--color-surface-active);
  font-family: ui-monospace, 'SF Mono', Consolas, monospace;
}
.brief__summary {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--color-border);
  color: var(--color-text-secondary);
}
.brief__summary--empty {
  color: var(--color-text-tertiary);
}
.brief__row {
  display: flex;
  gap: 8px;
  margin-top: 6px;
}
.brief__label {
  flex-shrink: 0;
  width: 62px;
  color: var(--color-text-tertiary);
}
.brief__value {
  min-width: 0;
  color: var(--color-text-secondary);
}
.brief__tag {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 10px;
  color: var(--color-success);
  background: var(--color-success-soft);
}
.brief__hint {
  margin-left: 4px;
  font-size: 10.5px;
  color: var(--color-text-tertiary);
}
.brief__foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--color-border);
  font-size: 10.5px;
  color: var(--color-text-tertiary);
}
.brief__more {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10.5px;
  color: var(--color-accent);
}
</style>
