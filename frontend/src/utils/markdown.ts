const esc = (s: string) =>
  s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')

const escapeHtml = (s: string) => esc(s)

function inline(text: string): string {
  let out = text
  // inline code
  out = out.replace(/`([^`]+)`/g, (_m, code: string) => `<code>${code}</code>`)
  // links [t](u)
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, label: string, href: string) => {
    const safeHref = href.replace(/"/g, '')
    return `<a href="${safeHref}" target="_blank" rel="noreferrer">${label}</a>`
  })
  // bold
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  // italic
  out = out.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')
  out = out.replace(/__([^_]+)__/g, '<strong>$1</strong>')
  return out
}

const fenceRe = /```[\w-]*/

export function mdToHtml(src: string): string {
  if (!src) return ''
  const safe = escapeHtml(src.replace(/\r\n/g, '\n'))
  const lines = safe.split('\n')
  const tokens: string[] = []
  let i = 0

  const flushBlock = () => {}

  while (i < lines.length) {
    const line = lines[i]

    if (fenceRe.test(line)) {
      i++
      const buf: string[] = []
      while (i < lines.length && !/^```/.test(lines[i])) {
        buf.push(lines[i])
        i++
      }
      i++ // skip closing
      tokens.push(`<pre class="aw-md__pre"><code>${buf.join('\n')}</code></pre>`)
      continue
    }

    if (/^\s*$/.test(line)) {
      i++
      continue
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/)
    if (heading) {
      const n = heading[1].length
      tokens.push(`<h${n}>${inline(heading[2])}</h${n}>`)
      i++
      continue
    }

    // blockquote group
    if (/^&gt;/.test(line)) {
      const buf: string[] = []
      while (i < lines.length && /^&gt;/.test(lines[i])) {
        buf.push(lines[i].replace(/^&gt;\s?/, ''))
        i++
      }
      tokens.push(`<blockquote>${buf.map((b) => inline(b)).join('<br>')}</blockquote>`)
      continue
    }

    // table: header row + separator next
    if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|:-]+$/.test(lines[i + 1])) {
      const cells = (row: string) =>
        row
          .trim()
          .replace(/^\|/, '')
          .replace(/\|$/, '')
          .split('|')
          .map((c) => inline(c.trim()))
      const head = cells(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && /^\s*\|/.test(lines[i])) {
        rows.push(cells(lines[i]))
        i++
      }
      tokens.push(`<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join('')}</tr></thead><tbody>${rows
        .map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join('')}</tr>`)
        .join('')}</tbody></table>`)
      continue
    }

    // lists
    if (/^\s*[-*]\s+/.test(line) || /^\s*\d+\.\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line)
      const items: string[] = []
      while (i < lines.length) {
        const m = lines[i].match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/)
        if (!m) break
        items.push(`<li>${inline(m[1])}</li>`)
        i++
      }
      tokens.push(`<${ordered ? 'ol' : 'ul'}>${items.join('')}</${ordered ? 'ol' : 'ul'}>`)
      continue
    }

    if (/^\s*([-*_]){3,}\s*$/.test(line)) {
      tokens.push('<hr>')
      i++
      continue
    }

    // paragraph
    const para: string[] = []
    while (i < lines.length && lines[i].trim()) {
      if (/^```/.test(lines[i]) || /^#{1,6}\s/.test(lines[i]) || /^&gt;/.test(lines[i]) || /^\s*\|/.test(lines[i]) || /^\s*(?:[-*]|\d+\.)\s+/.test(lines[i])) break
      para.push(lines[i])
      i++
    }
    flushBlock()
    tokens.push(`<p>${para.map(inline).join('<br>')}</p>`)
  }

  return tokens.join('\n')
}
