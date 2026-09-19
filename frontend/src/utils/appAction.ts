/**
 * 应用运行控制的**文案解析**（S2-3）
 *
 * 后端在端口被别的项目占着时返回 409，`detail` 形如：
 *   「端口 8100 已被项目 37 占用（应用单实例）。要切换请带 force=true，平台会先停掉它。」
 * 前端要据此问一句"要切换吗"，所以得把那个项目号挖出来 —— 这段逻辑抽成纯函数才好测，
 * 也让"后端改了文案"这件事在测试里立刻暴露（而不是悄悄变成不弹提示）。
 */

/** 文案里出现"端口 xxx 已被项目 N 占用" → 返回 N；否则 null */
export function occupiedProjectId(message: string): number | null {
  const m = /已被项目\s*(\d+)\s*占用/.exec(message || '')
  if (m) return Number(m[1])
  const m2 = /项目\s*(\d+)\s*占用/.exec(message || '')
  return m2 ? Number(m2[1]) : null
}

/** 是不是"端口被占用"这一类错误（要弹切换提示，而不是当普通失败） */
export function isOccupiedMessage(message: string): boolean {
  return /占用/.test(message || '') && (occupiedProjectId(message) !== null || /端口/.test(message || ''))
}
