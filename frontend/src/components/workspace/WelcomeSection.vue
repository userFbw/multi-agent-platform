<script setup lang="ts">
import AgentIcon from '@/components/common/AgentIcon.vue'
</script>

<template>
  <section class="welcome">
    <div class="welcome__emblem">
      <div class="welcome__halo welcome__halo--1"></div>
      <div class="welcome__halo welcome__halo--2"></div>
      <span class="welcome__mark">
        <AgentIcon icon="sparkles" :size="26" :stroke-width="2" />
      </span>
    </div>
    <h1 class="welcome__title">让 Agent 帮你完成任务</h1>
  </section>
</template>

<style scoped>
.welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.welcome__emblem {
  position: relative;
  width: 72px;
  height: 72px;
  margin-bottom: 22px;
}
.welcome__mark {
  position: relative;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border-radius: 18px;
  background: var(--gradient-logo);
  color: #fff;
  box-shadow: 0 6px 18px rgba(86, 88, 212, 0.25);
}
.welcome__halo {
  position: absolute;
  border-radius: 50%;
  z-index: 1;
}
.welcome__halo--1 {
  inset: 0;
  background: radial-gradient(circle, rgba(86, 88, 212, 0.12) 0%, transparent 65%);
}
.welcome__halo--2 {
  inset: 6px;
  background: rgba(86, 88, 212, 0.06);
}

.welcome__title {
  font-size: clamp(26px, 4vw, 34px);
  font-weight: 700;
  letter-spacing: -0.025em;
  line-height: 1.2;
  color: var(--color-text);
}
</style>
