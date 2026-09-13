import type { FinalPayload, InterruptPayload, PlanEvent } from '@/types/plan'

export interface StreamHandlers {
  onIntent: (intent: string) => void
  onStep: (e: PlanEvent) => void
  onDone: (plan: FinalPayload) => void
  onError: (msg: string) => void
  /** 中断帧：图已在闸门处暂停，用户确认后调 resumeChat 才能继续 */
  onInterrupt?: (payload: InterruptPayload, threadId: string) => void
  /** 首帧 trace_id：排查中断 / 报错时要拿它去对齐后端日志 */
  onTrace?: (traceId: string) => void
}

/** SSE 帧的松散视图：/api/chat 与 /api/chat/resume 的事件格式完全一致 */
interface RawFrame {
  type?: string
  trace_id?: string
  thread_id?: string
  interrupt?: InterruptPayload
  node?: string
  intent?: string
  content?: string
  final?: boolean
}

/** 把一帧 SSE 文本块分发到对应回调；解析不了的块直接丢弃（流里可能混入空块 / 心跳）。 */
function dispatchBlock(block: string, handlers: StreamHandlers): void {
  const dataLine = block.split('\n').find((l) => l.startsWith('data:'))
  if (!dataLine) return
  const data = dataLine.slice(5).trim()
  if (!data || data === '[DONE]') return
  try {
    const e = JSON.parse(data) as RawFrame
    // 中断帧、追踪帧都没有 node 字段，必须先按 type 判断，否则会掉进下面的节点分支
    if (e.type === 'interrupt') {
      if (e.interrupt) handlers.onInterrupt?.(e.interrupt, e.thread_id ?? '')
      return
    }
    if (e.type === 'trace') {
      handlers.onTrace?.(e.trace_id ?? '')
      return
    }
    if (e.node === 'intent') {
      handlers.onIntent(String(e.intent ?? ''))
    } else if (e.final) {
      handlers.onDone(JSON.parse(e.content ?? '') as FinalPayload)
    } else {
      handlers.onStep(e as PlanEvent)
    }
  } catch {
    // 忽略无法解析的块
  }
}

/** fetch + ReadableStream 解析 SSE（POST + JSON body，EventSource 不支持）。 */
async function postSSE(url: string, body: unknown, handlers: StreamHandlers): Promise<void> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
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
    for (const block of blocks) dispatchBlock(block, handlers)
  }
}

/** 发起一轮新对话。 */
export async function streamChat(
  message: string,
  sessionId: string,
  userId: string,
  handlers: StreamHandlers,
): Promise<void> {
  await postSSE(
    '/api/chat',
    { session_id: sessionId, user_id: userId, message },
    handlers,
  )
}

/** 恢复被闸门暂停的图执行；feedback 只在 decision 为 revise 时有值。 */
export async function resumeChat(
  threadId: string,
  decision: string,
  feedback: string,
  handlers: StreamHandlers,
): Promise<void> {
  await postSSE(
    '/api/chat/resume',
    { thread_id: threadId, decision, feedback },
    handlers,
  )
}
