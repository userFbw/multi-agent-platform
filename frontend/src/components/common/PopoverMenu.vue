<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type CSSProperties, type Component } from 'vue'
import { Check, ChevronDown } from '@lucide/vue'

export interface MenuItemDef {
  key: string
  label: string
  desc?: string
  hint?: string
  icon?: Component
  active?: boolean
  disabled?: boolean
  danger?: boolean
}

const props = withDefaults(
  defineProps<{
    items: MenuItemDef[]
    align?: 'left' | 'right'
    /** 展开方向：down = 向下（默认）；up = 向上（放页面底部的元素要用，否则会跑出视口） */
    direction?: 'down' | 'up'
    icon?: Component
    label?: string
    width?: number
    triggerClass?: string
  }>(),
  { align: 'left', direction: 'down', width: 220 }
)

const emit = defineEmits<{
  select: [key: string]
}>()

const GAP = 6 // 与触发元素的间距
const EDGE = 8 // 与视口边缘的最小留白

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const anchorEl = ref<HTMLElement | null>(null)
/** 实际使用的方向：空间不够会自动翻转（例如底部的菜单向上、顶部的菜单向下） */
const actualDirection = ref<'down' | 'up'>('down')
/** 定位算好之前先隐藏，避免菜单在左上角闪一帧 */
const ready = ref(false)
const pos = ref({ left: 0, top: 0 })

const toggle = () => {
  open.value = !open.value
}

const pick = (item: MenuItemDef) => {
  if (item.disabled) return
  emit('select', item.key)
  open.value = false
}

const onDocClick = (e: MouseEvent) => {
  const t = e.target as Node
  // 菜单已 teleport 到 body，不再是 root 的子节点 —— 两处都要判断，否则点菜单项会被当成"点外面"
  if (root.value?.contains(t) || anchorEl.value?.contains(t)) return
  open.value = false
}

const onKey = (e: KeyboardEvent) => {
  if (e.key === 'Escape') open.value = false
}

/** 按触发元素的视口位置算 fixed 坐标；上下空间不够时自动翻转 */
async function updatePosition() {
  const el = root.value
  if (!el) return
  const r = el.getBoundingClientRect()
  await nextTick()
  const h = anchorEl.value?.offsetHeight ?? 0
  const spaceBelow = window.innerHeight - r.bottom - GAP - EDGE
  const spaceAbove = r.top - GAP - EDGE

  let dir = props.direction
  if (dir === 'up' && spaceAbove < h && spaceBelow > spaceAbove) dir = 'down'
  else if (dir === 'down' && spaceBelow < h && spaceAbove > spaceBelow) dir = 'up'
  actualDirection.value = dir

  // 水平：先按对齐方式算，再钳进视口（触发元素可能比菜单窄，右对齐时会伸到屏幕外）
  const w = props.width
  const raw = props.align === 'right' ? r.right - w : r.left
  const maxLeft = Math.max(EDGE, window.innerWidth - w - EDGE)
  pos.value = {
    left: Math.min(Math.max(raw, EDGE), maxLeft),
    top: dir === 'up' ? r.top - GAP : r.bottom + GAP
  }
}

/** 定位放在外层锚点上（translate 对齐），动画放在内层菜单上 —— 否则动画的 transform 会覆盖定位 */
const anchorStyle = computed<CSSProperties>(() => ({
  width: `${props.width}px`,
  left: `${pos.value.left}px`,
  top: `${pos.value.top}px`,
  transform: `translate(0, ${actualDirection.value === 'up' ? '-100%' : '0'})`,
  visibility: ready.value ? 'visible' : 'hidden'
}))

function bindListeners() {
  window.addEventListener('scroll', updatePosition, true) // capture：内层滚动容器也能捕获
  window.addEventListener('resize', updatePosition)
}
function unbindListeners() {
  window.removeEventListener('scroll', updatePosition, true)
  window.removeEventListener('resize', updatePosition)
}

watch(open, async (isOpen) => {
  if (!isOpen) {
    unbindListeners()
    ready.value = false
    return
  }
  ready.value = false
  actualDirection.value = props.direction
  try {
    await updatePosition()
  } finally {
    ready.value = true // 即使定位测量异常也要显示，不能把菜单卡成不可见
  }
  bindListeners()
})

onMounted(() => {
  document.addEventListener('mousedown', onDocClick)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocClick)
  document.removeEventListener('keydown', onKey)
  unbindListeners()
})
</script>

<template>
  <div ref="root" class="pop-root">
    <slot name="trigger" :toggle="toggle" :open="open">
      <button
        type="button"
        class="pop-trigger"
        :class="[triggerClass, { 'pop-trigger--open': open }]"
        @click="toggle"
      >
        <component :is="icon" v-if="icon" :size="15" :stroke-width="1.9" />
        <span class="pop-trigger__label">{{ label }}</span>
        <ChevronDown :size="14" class="pop-trigger__chev" :class="{ rotate: open }" />
      </button>
    </slot>

    <Teleport to="body">
      <transition name="pop">
        <div v-if="open" ref="anchorEl" class="pop-anchor" :style="anchorStyle">
          <div
            class="pop-menu aw-menu"
            :class="actualDirection === 'up' ? 'pop-menu--up' : 'pop-menu--down'"
          >
            <button
              v-for="item in items"
              :key="item.key"
              type="button"
              class="aw-menu__item"
              :class="{
                'aw-menu__item--danger': item.danger,
                'aw-menu__item--active': item.active
              }"
              :disabled="item.disabled"
              @click="pick(item)"
            >
              <span v-if="item.icon" class="pop-menu__lead">
                <component :is="item.icon" :size="15" :stroke-width="1.9" />
              </span>
              <span class="aw-menu__label">
                <span class="pop-menu__main">{{ item.label }}</span>
                <span v-if="item.desc" class="pop-menu__desc">{{ item.desc }}</span>
              </span>
              <span v-if="item.hint" class="aw-menu__hint">{{ item.hint }}</span>
              <Check v-if="item.active" :size="14" :stroke-width="2.2" />
            </button>
          </div>
        </div>
      </transition>
    </Teleport>
  </div>
</template>

<style scoped>
.pop-root {
  position: relative;
  display: inline-flex;
}
.pop-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 10px;
  border-radius: var(--radius-md);
  font-size: var(--fs-12);
  color: var(--color-text-secondary);
  transition: background 0.14s var(--ease), color 0.14s var(--ease);
  user-select: none;
  max-width: 240px;
}
.pop-trigger:hover,
.pop-trigger--open {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.pop-trigger__label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pop-trigger__chev {
  flex-shrink: 0;
  transition: transform 0.16s var(--ease);
  color: var(--color-text-tertiary);
}
.pop-trigger__chev.rotate {
  transform: rotate(180deg);
}

/* 锚点：只负责 fixed 定位与对齐（transform 由内联样式给） */
/* z-index 70：高于移动端抽屉(60)与遮罩(55)，低于页面级弹层(100) */
.pop-anchor {
  position: fixed;
  z-index: 70;
}
.pop-menu {
  width: 100%;
}
.pop-menu--down {
  animation: popInDown 0.14s var(--ease-out);
}
.pop-menu--up {
  animation: popInUp 0.14s var(--ease-out);
}
.pop-menu__lead {
  display: inline-flex;
}
.pop-menu__main {
  display: block;
}
.pop-menu__desc {
  display: block;
  margin-top: 1px;
  font-size: var(--fs-11);
  color: var(--color-text-tertiary);
  line-height: 1.4;
}

@keyframes popInDown {
  from {
    opacity: 0;
    transform: translateY(-3px) scale(0.985);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}
@keyframes popInUp {
  from {
    opacity: 0;
    transform: translateY(3px) scale(0.985);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.pop-enter-active,
.pop-leave-active {
  transition: opacity 0.12s var(--ease);
}
.pop-enter-from,
.pop-leave-to {
  opacity: 0;
}
</style>
