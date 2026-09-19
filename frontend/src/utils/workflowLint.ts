/**
 * 画布图的**提前检查**：把"提交后才会报错/才会失效"的限制，画的时候就说出来。
 *
 * 只覆盖**画布能造出来的那几种问题**（空图 / 角色失效 / 环 / 缺 PRD / 孤立节点），
 * 权威判定仍在后端（保存、执行、POST /api/workflows/validate 都会再校验一遍），
 * 这里只是把反馈提前，不替代服务端校验。
 */
import type { WorkflowEdge, WorkflowNode } from '@/types/workflow'

export type LintLevel = 'error' | 'warn'

export interface LintIssue {
  level: LintLevel
  /** 关联节点（画布上打标、点击定位）；图级问题为空串 */
  nodeId: string
  message: string
}

/** 产出 PRD 的角色标识：它的位置就是人工审批闸门（后端按 role_key=='pm' 找） */
const PRD_ROLE = 'pm'

/** 有向图找环，返回环上的节点 id（每个环只报一次） */
function findCycles(nodes: WorkflowNode[], edges: WorkflowEdge[]): string[][] {
  const next = new Map<string, string[]>()
  for (const n of nodes) next.set(n.id, [])
  for (const e of edges) next.get(e.source)?.push(e.target)

  const cycles: string[][] = []
  const state = new Map<string, 0 | 1 | 2>()   // 0/未访问 1/在栈上 2/已完成
  const stack: string[] = []

  const walk = (id: string) => {
    state.set(id, 1)
    stack.push(id)
    for (const to of next.get(id) ?? []) {
      const st = state.get(to) ?? 0
      if (st === 1) {
        cycles.push(stack.slice(stack.indexOf(to)))
      } else if (st === 0) {
        walk(to)
      }
    }
    stack.pop()
    state.set(id, 2)
  }
  for (const n of nodes) if ((state.get(n.id) ?? 0) === 0) walk(n.id)
  return cycles
}

/**
 * @param nodes        画布节点
 * @param edges        画布连线（= 依赖：source 成功后 target 才跑）
 * @param knownRoles   Agent 注册表里存在的 role_key；传空集合 = 注册表还没加载，跳过角色检查
 * @param contractOf   节点 → 它这次会按哪种契约解析输出（`json_object` 才能当决策源）；
 *                     不传就跳过"决策源契约"这条检查
 */
export function lintWorkflow(
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  knownRoles: Set<string>,
  contractOf?: (n: WorkflowNode) => string
): LintIssue[] {
  const issues: LintIssue[] = []
  const nameOf = (id: string) => nodes.find((n) => n.id === id)?.label ?? id

  if (!nodes.length) {
    return [{ level: 'error', nodeId: '', message: '画布是空的：先从左边拖一个技能 / Agent 进来。' }]
  }

  // —— 角色能不能解析到 Agent（运行时找不到就是这一步直接失败）——
  if (knownRoles.size) {
    for (const n of nodes) {
      if (!n.role) {
        issues.push({
          level: 'error',
          nodeId: n.id,
          message: `「${n.label}」没有绑定角色：删掉重新从左边拖一个，否则运行时解析不到 Agent。`
        })
      } else if (!knownRoles.has(n.role)) {
        issues.push({
          level: 'error',
          nodeId: n.id,
          message: `「${n.label}」的角色 ${n.role} 已不在 Agent 注册表里（被删除或改过名？）：运行时这一步会直接失败。`
        })
      }
    }
  }

  // —— 循环依赖：保存能过，运行时必然"无法推进" ——
  for (const cycle of findCycles(nodes, edges)) {
    const path = [...cycle, cycle[0]].map(nameOf).join(' → ')
    for (const id of cycle) {
      issues.push({ level: 'error', nodeId: id, message: `循环依赖（${path}）：引擎无法决定先跑谁，运行必失败。` })
    }
  }

  // —— PRD 节点 = 项目模版资格 + 人工审批闸门 ——
  const prdNodes = nodes.filter((n) => n.role === PRD_ROLE)
  if (!prdNodes.length) {
    issues.push({
      level: 'warn',
      nodeId: '',
      message:
        '没有「生成 PRD」节点（角色 pm）：这张图只能在这里点「运行」，不能当项目模版（首页「指定工作流」里不会出现），项目流程里也没有人工审批点。'
    })
  } else if (prdNodes.length > 1) {
    for (const n of prdNodes.slice(1)) {
      issues.push({
        level: 'warn',
        nodeId: n.id,
        message:
          `这是第 ${prdNodes.indexOf(n) + 1} 个「生成 PRD」节点：审批闸门只认图上第一个` +
          `（执行到它之后会停下等你审批）。这一个会正常执行，**它下游的节点读它产出的那版 PRD** ——` +
          `"审批通过后再让 PM 出一版"就是这么接的。`
      })
    }
  }

  // —— 执行条件（when）：只在节点**真的设了条件**时才检查，纯顺序的图一条都不会触发 ——
  const upstreamOf = (id: string): Set<string> => {
    const byId = new Map(nodes.map((n) => [n.id, n]))
    const seen = new Set<string>()
    const stack = [...(edges.filter((e) => e.target === id).map((e) => e.source))]
    while (stack.length) {
      const cur = stack.pop() as string
      if (seen.has(cur) || !byId.has(cur)) continue
      seen.add(cur)
      stack.push(...edges.filter((e) => e.target === cur).map((e) => e.source))
    }
    return seen
  }

  for (const n of nodes) {
    const when = n.when
    if (!when) continue
    const ref = (when.ref || '').trim()

    // ① 格式：后端只认 `<节点id>.parsed.<字段>`
    const m = /^(.+)\.parsed\.([\w-]+)$/.exec(ref)
    if (!m) {
      issues.push({
        level: 'error', nodeId: n.id,
        message: `「${n.label}」的执行条件格式不对：必须是「决策源节点id.parsed.字段名」（例如 rev.parsed.verdict），当前是「${ref || '(空)'}」——保存时后端会直接拒绝。`
      })
      continue
    }
    const [srcId, field] = [m[1], m[2]]

    // ② 决策源必须存在
    const src = nodes.find((x) => x.id === srcId)
    if (!src) {
      issues.push({
        level: 'error', nodeId: n.id,
        message: `「${n.label}」的执行条件引用了图上不存在的节点「${srcId}」——保存会被后端拒绝。`
      })
      continue
    }

    // ③ 决策源必须是**它的上游**：不是上游的话，跑到这一步时那个节点还没产出，
    //    取值必然为空 → 条件永远不成立 → 这个节点会被静默跳过（最阴的一类坑）。
    if (!upstreamOf(n.id).has(srcId)) {
      issues.push({
        level: 'error', nodeId: n.id,
        message: `「${n.label}」的执行条件取自「${src.label}」，但它不是上游：执行到这里时那边还没产出，取值必然为空 → 这个节点会被永远跳过。把连线接成「${src.label} → ${n.label}」。`
      })
    }

    // ④ 决策源必须能产出**结构化 JSON**：契约不是 json_object 时取不到字段，
    //    运行时是硬失败（ROUTE_MISSING），不是软失败。
    if (contractOf && src) {
      const kind = contractOf(src)
      if (kind && kind !== 'json_object') {
        issues.push({
          level: 'error', nodeId: src.id,
          message: `「${src.label}」被「${n.label}」的执行条件当作决策源，但它的输出内容形式是 ${kind}（需要 json_object）——运行时取不到 ${field}，这一步会硬失败。到 Agent 页面把它的「输出内容形式」改成 json_object。`
        })
      }
    }

    // ⑤ 取值不能为空
    if (when.eq === undefined || when.eq === null || String(when.eq).trim() === '') {
      issues.push({
        level: 'error', nodeId: n.id,
        message: `「${n.label}」的执行条件没填取值：条件不成立时这个节点会被跳过，取值空着后端也会拒绝。`
      })
    }
  }

  // —— 孤立节点：能跑，但拿不到任何上游产出 ——
  if (nodes.length > 1) {
    for (const n of nodes) {
      const linked = edges.some((e) => e.source === n.id || e.target === n.id)
      if (!linked) {
        issues.push({
          level: 'warn',
          nodeId: n.id,
          message: `「${n.label}」既没有上游也没有下游：它只会收到用户需求原文，拿不到别的 Agent 的产出。`
        })
      }
    }
  }

  return issues
}

export const hasError = (issues: LintIssue[]) => issues.some((i) => i.level === 'error')
export const issuesOfNode = (issues: LintIssue[], nodeId: string) => issues.filter((i) => i.nodeId === nodeId)
