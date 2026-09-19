<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppSidebar from './AppSidebar.vue'
import { useUiStore } from '@/stores/ui'
import { useTaskStore } from '@/stores/task'
import { PanelLeft } from '@lucide/vue'

const route = useRoute()
const router = useRouter()
const ui = useUiStore()
const taskStore = useTaskStore()

const isMobile = ref(false)
const media = window.matchMedia('(max-width: 1024px)')

const sync = () => {
  isMobile.value = media.matches
  if (!isMobile.value) ui.drawerOpen = false
  else ui.sidebarCollapsed = false
}
media.addEventListener('change', sync)

onMounted(() => {
  sync()
  taskStore.loadProjects()

  const onKey = (e: KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault()
      taskStore.reset()
      router.push('/')
    }
    if (e.key === 'Escape' && ui.drawerOpen) ui.drawerOpen = false
  }
  document.addEventListener('keydown', onKey)
  cleanupKey = () => document.removeEventListener('keydown', onKey)
})

let cleanupKey: () => void = () => {}
onBeforeUnmount(() => {
  cleanupKey()
  media.removeEventListener('change', sync)
})
</script>

<template>
  <div class="shell" :class="{ mobile: isMobile, 'drawer-open': isMobile && ui.drawerOpen, collapsed: !isMobile && ui.sidebarCollapsed }">
    <div class="sidebar-wrap">
      <AppSidebar />
    </div>

    <div class="main">
      <button
        v-if="isMobile || ui.sidebarCollapsed"
        type="button"
        class="main-burger"
        :class="{ 'main-burger--pinned': !isMobile }"
        :title="isMobile ? '打开侧栏' : '展开侧栏'"
        @click="isMobile ? (ui.drawerOpen = !ui.drawerOpen) : ui.toggleSidebar()"
      >
        <PanelLeft :size="17" :stroke-width="2" />
      </button>

      <div class="main-scroll">
        <router-view v-slot="{ Component }">
          <transition name="aw-fade" mode="out-in">
            <component :is="Component" :key="route.path" />
          </transition>
        </router-view>
      </div>
    </div>

    <transition name="scrim-fade">
      <div v-if="isMobile && ui.drawerOpen" class="scrim" @click="ui.drawerOpen = false" />
    </transition>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100vh;
  width: 100%;
  overflow: hidden;
  background: var(--color-bg);
}

.sidebar-wrap {
  width: var(--sidebar-width);
  flex-shrink: 0;
  height: 100%;
  transition: width 0.22s var(--ease);
}
.shell.collapsed .sidebar-wrap {
  width: 62px;
}

.main {
  position: relative;
  flex: 1;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
}
.main-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

/* mobile drawer */
@media (max-width: 1024px) {
  .sidebar-wrap {
    position: fixed;
    left: 0;
    top: 0;
    bottom: 0;
    width: var(--sidebar-width);
    z-index: 60;
    transform: translateX(-102%);
    transition: transform 0.24s var(--ease-out);
    box-shadow: var(--shadow-lg);
  }
  .shell.drawer-open .sidebar-wrap {
    transform: translateX(0);
  }
  .scrim {
    position: fixed;
    inset: 0;
    background: rgba(15, 16, 19, 0.28);
    z-index: 55;
    backdrop-filter: blur(1px);
  }
  .scrim-fade-enter-active,
  .scrim-fade-leave-active {
    transition: opacity 0.2s var(--ease);
  }
  .scrim-fade-enter-from,
  .scrim-fade-leave-to {
    opacity: 0;
  }
}

.main-burger {
  position: fixed;
  top: 12px;
  left: 12px;
  z-index: 50;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: var(--radius-md);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  box-shadow: var(--shadow-sm);
  color: var(--color-text-secondary);
}
.main-burger:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
}
.main-burger--pinned {
  position: fixed;
  left: calc(var(--sidebar-width) - 20px);
  transition: left 0.22s var(--ease);
}
.shell.collapsed .main-burger--pinned {
  left: 12px;
}
</style>
