<script setup lang="ts">
import { computed, onMounted, ref, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUiStore } from '@/stores/ui'
import { useTaskStore } from '@/stores/task'
import { useAuthStore } from '@/stores/auth'
import { STATUS_META } from '@/utils/status'
import AgentIcon from '../common/AgentIcon.vue'
import PopoverMenu from '../common/PopoverMenu.vue'
import type { TaskSummary } from '@/types/task'
import {
  BarChart3,
  Bot,
  ChevronDown,
  Folder,
  GitBranch,
  Home,
  LayoutGrid,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Settings2,
  UserRound
} from '@lucide/vue'

const route = useRoute()
const router = useRouter()
const ui = useUiStore()
const taskStore = useTaskStore()
const auth = useAuthStore()

const isMac = ref(false)
onMounted(() => {
  isMac.value = /Mac|iPhone|iPad/.test(navigator.platform || '')
})

const kbd = computed(() => (isMac.value ? '⌘K' : 'Ctrl K'))
const projects = computed<TaskSummary[]>(() => taskStore.list)

interface NavItem {
  label: string
  path: string
  icon: Component
}

const spaceNav: NavItem[] = [
  { label: '首页', path: '/', icon: Home },
  { label: '任务', path: '/tasks', icon: LayoutGrid },
  { label: '工作流', path: '/workflow', icon: GitBranch },
  { label: 'Agent', path: '/agents', icon: Bot }   // 内置角色 + 自定义 Agent 的注册/管理
]

// 「技能」页已并入「Agent」页（同一个页面的两个入口 → 只留一个），这里不再重复列
const featureNav: NavItem[] = [
  { label: '统计', path: '/stats', icon: BarChart3 }
]

const activeFor = (path: string) => {
  if (path === '/') return route.path === '/'
  return route.path === path || route.path.startsWith(`${path}/`)
}

const pushIfNeeded = (path: string) => {
  if (route.path !== path) router.push(path)
}

const goNewTask = () => {
  taskStore.reset()
  pushIfNeeded('/')
}

const openTask = (t: TaskSummary) => {
  router.push(`/tasks/${t.id}`)
}

const userItems = computed(() => [
  { key: 'profile', label: '个人资料', icon: UserRound as Component, active: route.query.tab === 'profile' },
  { key: 'settings', label: '设置', icon: Settings2 as Component },
  { key: 'logout', label: '退出登录', icon: LogOut as Component, danger: true }
])

const onUserAction = (key: string) => {
  if (key === 'logout') {
    // 顺序：先停轮询/清内存缓存，再清登录态与 token（auth.logout 会一并清 token 与工作流记忆）
    try {
      taskStore.clearAll()
    } catch {
      /* 清理失败也必须能退出登录 */
    }
    auth.logout()
    // replace：登出后按浏览器返回键不应回到需要登录的页面
    void router.replace('/login')
    return
  }
  router.push({ path: '/settings', query: { tab: key === 'profile' ? 'profile' : 'general' } })
}
</script>

<template>
  <aside class="sidebar" :class="{ 'sidebar--collapsed': ui.sidebarCollapsed }">
    <!-- brand -->
    <div class="brand-row">
      <button type="button" class="brand" title="全栈应用开发平台 首页" @click="goNewTask">
        <span class="brand-mark">
          <AgentIcon icon="sparkles" :size="16" :stroke-width="2.2" />
        </span>
        <span v-if="!ui.sidebarCollapsed" class="brand-name">全栈应用开发平台</span>
      </button>
      <button
        v-if="!ui.sidebarCollapsed"
        type="button"
        class="rail-toggle"
        title="收起侧栏"
        @click="ui.toggleSidebar()"
      >
        <PanelLeftClose :size="15" :stroke-width="2" />
      </button>
      <button v-else type="button" class="rail-toggle rail-toggle--center" title="展开侧栏" @click="ui.toggleSidebar()">
        <PanelLeftOpen :size="15" :stroke-width="2" />
      </button>
    </div>

    <!-- Work / Chat 切换已移除：默认 Work 工作台 -->

    <!-- new task -->
    <button type="button" class="new-task" :title="ui.sidebarCollapsed ? '新建任务 (Ctrl K)' : ''" @click="goNewTask">
      <Plus :size="16" :stroke-width="2" />
      <span v-if="!ui.sidebarCollapsed" class="new-task__label">新建任务</span>
      <span v-if="!ui.sidebarCollapsed" class="aw-kbd">{{ kbd }}</span>
    </button>

    <!-- full nav -->
    <nav v-if="!ui.sidebarCollapsed" class="nav-scroll">
      <router-link v-for="item in spaceNav" :key="item.path" :to="item.path" class="nav-item" :class="{ 'nav-item--active': activeFor(item.path) }">
        <component :is="item.icon" :size="16" :stroke-width="1.9" />
        <span class="nav-item__label">{{ item.label }}</span>
      </router-link>

      <div class="group-label">功能</div>
      <router-link v-for="item in featureNav" :key="item.path" :to="item.path" class="nav-item" :class="{ 'nav-item--active': activeFor(item.path) }">
        <component :is="item.icon" :size="16" :stroke-width="1.9" />
        <span class="nav-item__label">{{ item.label }}</span>
      </router-link>

      <div class="group-label">项目</div>
      <button
        v-for="p in projects"
        :key="p.id"
        type="button"
        class="nav-item"
        :class="{ 'nav-item--active': String(route.params.id) === p.id }"
        @click="openTask(p)"
      >
        <Folder :size="16" :stroke-width="1.9" class="nav-item__muted" />
        <span class="nav-item__label">{{ p.title }}</span>
        <span class="aw-dot" :class="STATUS_META[p.status].dot"></span>
      </button>
      <p v-if="!projects.length" class="conv-empty">暂无项目，试试新建一个</p>
    </nav>

    <!-- collapsed rail -->
    <nav v-else class="nav-rail">
      <router-link v-for="item in [...spaceNav, ...featureNav]" :key="item.path" :to="item.path" class="rail-item" :class="{ 'rail-item--active': activeFor(item.path) }" :title="item.label">
        <component :is="item.icon" :size="17" :stroke-width="1.9" />
      </router-link>
    </nav>

    <!-- user -->
    <div class="sidebar-footer user-menu">
      <!-- direction="up"：头像在侧栏底部，向下展开会被 .sidebar 的 overflow:hidden 裁掉 -->
      <PopoverMenu :items="userItems" align="right" :width="200" direction="up" @select="onUserAction">
        <template #trigger="{ toggle, open }">
          <button type="button" class="user" :class="{ 'user--open': open }" @click.stop="toggle">
            <span class="user-avatar">
              <img v-if="auth.avatarUrl" :src="auth.avatarUrl" class="user-avatar__img" alt="" />
              <UserRound v-else :size="15" :stroke-width="2" />
            </span>
            <span v-if="!ui.sidebarCollapsed" class="user-name">{{ auth.user?.user_name || '用户' }}</span>
            <ChevronDown v-if="!ui.sidebarCollapsed" :size="13" class="user-chev" />
          </button>
        </template>
      </PopoverMenu>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  background: var(--color-sidebar);
  border-right: 1px solid var(--color-border);
  padding: 10px;
  overflow: hidden;
}

.brand-row {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 10px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 9px;
  height: 34px;
  padding: 0 8px;
  flex: 1;
  border-radius: var(--radius-md);
  min-width: 0;
}
.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 8px;
  background: var(--gradient-logo);
  color: #fff;
  flex-shrink: 0;
}
.brand-name {
  font-size: var(--fs-15);
  font-weight: 700;
  letter-spacing: -0.01em;
  white-space: nowrap;
}
.rail-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-md);
  color: var(--color-text-tertiary);
  flex-shrink: 0;
  transition: all 0.13s var(--ease);
}
.rail-toggle:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.rail-toggle--center {
  align-self: center;
  margin: 0 auto 6px;
}

.new-task {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 38px;
  padding: 0 12px;
  margin-bottom: 10px;
  border-radius: var(--radius-md);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  box-shadow: var(--shadow-xs);
  font-size: var(--fs-13);
  font-weight: 550;
  transition: all 0.16s var(--ease);
}
.new-task:hover {
  background: var(--color-surface-hover);
  border-color: var(--color-border-strong);
}
.new-task__label {
  flex: 1;
  text-align: left;
}

.nav-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 2px 0 6px;
}
.group-label {
  padding: 16px 10px 6px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.05em;
  color: var(--color-text-tertiary);
}
.group-label:first-child {
  padding-top: 6px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  height: 34px;
  padding: 0 10px;
  margin-bottom: 1px;
  border-radius: var(--radius-md);
  font-size: var(--fs-13);
  color: var(--color-text-secondary);
  text-align: left;
  transition: all 0.13s var(--ease);
}
.nav-item:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.nav-item--active {
  background: var(--color-surface);
  color: var(--color-text);
  font-weight: 550;
  box-shadow: var(--shadow-xs);
}
.nav-item--active > svg {
  color: var(--color-accent);
}
.nav-item__muted {
  color: var(--color-text-tertiary);
}
.nav-item__label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}

.conversations {
  display: flex;
  flex-direction: column;
}
.conv-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  padding: 7px 10px;
  border-radius: var(--radius-md);
  transition: background 0.13s var(--ease);
}
.conv-item:hover {
  background: var(--color-surface-hover);
}
.conv-item--active {
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
}
.conv-item__title {
  width: 100%;
  font-size: var(--fs-12);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}
.conv-item__meta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--color-text-tertiary);
}
.conv-empty {
  padding: 4px 10px;
  font-size: 12px;
  color: var(--color-text-faint);
}

.nav-rail {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 2px;
  overflow-y: auto;
  align-items: center;
}
.rail-item {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 36px;
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  transition: all 0.13s var(--ease);
}
.rail-item:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.rail-item--active {
  background: var(--color-surface);
  color: var(--color-accent);
  box-shadow: var(--shadow-xs);
}

.sidebar-footer {
  flex-shrink: 0;
  padding-top: 8px;
  border-top: 1px solid var(--color-border);
  margin-top: 4px;
}
/* PopoverMenu 的包裹层默认是 inline-flex（按内容收缩），这里让它占满整行：
   按钮才是整行宽，弹出菜单也能对齐到侧栏边缘 */
.user-menu :deep(.pop-root) {
  display: flex;
  width: 100%;
}
.user {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 6px 8px;
  border-radius: var(--radius-md);
  transition: background 0.13s var(--ease);
}
.user:hover,
.user--open {
  background: var(--color-surface-hover);
}
.user-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--gradient-logo);
  color: #fff;
  flex-shrink: 0;
  overflow: hidden;
}
.user-avatar__img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.user-name {
  flex: 1;
  font-size: var(--fs-12);
  font-weight: 600;
  text-align: left;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.user-chev {
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}
</style>
