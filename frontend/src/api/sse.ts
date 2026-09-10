import type { PlanEvent, PlanJson, ProfileInput } from '@/types/plan'

export interface StreamHandlers {
  onStep: (e: PlanEvent) => void
  onDone: (plan: PlanJson) => void
  onError: (msg: string) => void
}

/** fetch + ReadableStream 解析 SSE（POST + JSON body，EventSource 不支持）。 */
export async function streamPlan(payload: ProfileInput, handlers: StreamHandlers): Promise<void> {
  const resp = await fetch('/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  if (!resp.ok || !resp.body) {
    handlers.onError(`HTTP ${resp.status}`)
    return
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() ?? ''
    for (const block of blocks) {
      const dataLine = block.split('\n').find((l) => l.startsWith('data:'))
      if (!dataLine) continue
      const data = dataLine.slice(5).trim()
      if (!data || data === '[DONE]') continue
      try {
        const e = JSON.parse(data) as PlanEvent
        if (e.final) {
          handlers.onDone(JSON.parse(e.content) as PlanJson)
        } else {
          handlers.onStep(e)
        }
      } catch {
        // 忽略无法解析的块
      }
    }
  }
}
