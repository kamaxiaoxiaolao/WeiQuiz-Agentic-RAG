<script setup lang="ts">
import { ref, reactive, computed, nextTick, watch } from 'vue'
import { marked } from 'marked'
import api from '@/api'
import Icon from './Icon.vue'

interface FlowStep {
  key: string
  title: string
  status: string
  summary: string
  duration_ms: number | null
  items: { label: string; value: string }[]
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources: any[]
  citations: any[]
  trace?: any
  route?: any
  flowSteps?: FlowStep[]
}

const props = defineProps<{
  sessionId: string
}>()

const emit = defineEmits<{
  (e: 'new-session', sessionId: string): void
  (e: 'session-used', sessionId: string): void
}>()

const inputMessage = ref('')
const groundingMode = ref<'off' | 'reflection'>('off')
const isLoading = ref(false)
const streamStatus = ref('')
const chatContainer = ref<HTMLElement | null>(null)
const textarea = ref<HTMLTextAreaElement | null>(null)
const TYPEWRITER_INTERVAL_MS = 18
const TYPEWRITER_CHARS_PER_TICK = 2
const STREAM_REQUEST_TIMEOUT_MS = 30000
const API_BASE = import.meta.env.VITE_API_BASE || (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '')

type TypewriterState = {
  queue: string
  timer: number | null
  waiters: (() => void)[]
}

const typewriterStates = new WeakMap<ChatMessage, TypewriterState>()

const messages = ref<ChatMessage[]>([
  {
    role: 'assistant',
    content: '你好，我是 **WeiQuiz Enterprise RAG**。\n\n你可以上传企业制度、课件、技术文档或业务材料，然后向我提问。右侧会展示 Router、Rewrite、Retrieval、Quality Check、Generation 的完整 Agentic RAG 流程。',
    sources: [],
    citations: [],
    flowSteps: buildFlowSteps(null, 'idle'),
  },
])

const selectedSource = ref<any>(null)
const selectedMessageIndex = ref<number | null>(null)

const latestAssistant = computed(() => {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    if (messages.value[i].role === 'assistant') return messages.value[i]
  }
  return null
})

const currentAssistantMessage = computed(() => {
  if (selectedMessageIndex.value !== null) {
    const msg = messages.value[selectedMessageIndex.value]
    if (msg?.role === 'assistant') return msg
  }
  return latestAssistant.value
})

// 暴露给 DebugPanel
defineExpose({ messages, latestAssistant, selectedSource, currentAssistantMessage })

function buildFlowSteps(trace: any, generationStatus = 'idle'): FlowStep[] {
  const route = trace?.route || null
  const decomposition = trace?.decomposition || {}
  const hyde = trace?.hyde || {}
  const stepBack = trace?.step_back || {}
  const rewrite = trace?.rewrite || {}
  const quality = trace?.quality || {}
  const grounding = trace?.grounding || null
  const retrievalQuery = trace?.retrieval_query || ''
  const subQuestions = decomposition?.sub_questions || []
  const subRetrievals = decomposition?.sub_retrievals || []
  const intermediateAnswers = decomposition?.intermediate_answers || []
  const isMultiStep = route?.intent === 'multi_step' || route?.query_strategy === 'decomposition' || subQuestions.length > 0

  return [
    {
      key: 'router',
      title: '1. Query Router',
      status: route ? 'done' : generationStatus === 'idle' ? 'idle' : 'running',
      summary: route ? `识别为：${intentLabel(route.intent)}` : '等待路由判断',
      duration_ms: null,
      items: route ? [
        { label: 'intent', value: intentLabel(route.intent) },
        { label: 'strategy', value: strategyLabel(route.query_strategy) },
        { label: 'complexity', value: route.complexity || '-' },
        { label: 'tools', value: (route.tools || []).join(', ') || '-' },
        { label: 'method', value: route.method || '-' },
        { label: 'reason', value: route.reason || '-' },
      ] : [],
    },
    {
      key: 'decomposition',
      title: '2. Sub-question Decomposition',
      status: subQuestions.length ? 'done' : isMultiStep ? generationStatus === 'idle' ? 'idle' : 'running' : trace ? 'skipped' : generationStatus === 'idle' ? 'idle' : 'pending',
      summary: subQuestions.length
        ? `复杂问题已拆成 ${subQuestions.length} 个子问题`
        : isMultiStep
          ? '等待子问题分解'
          : '单跳问题，跳过分解',
      duration_ms: null,
      items: subQuestions.map((question: string, index: number) => ({
        label: `q${index + 1}`,
        value: question,
      })),
    },
    {
      key: 'hyde',
      title: '2. HyDE Transform',
      status: hyde?.retrieval_query
        ? hyde.success ? 'done' : 'warn'
        : route?.query_strategy === 'hyde'
          ? generationStatus === 'idle' ? 'idle' : 'running'
          : trace ? 'skipped' : generationStatus === 'idle' ? 'idle' : 'pending',
      summary: hyde?.retrieval_query
        ? hyde.success
          ? '已生成 hypothetical document 用于语义检索'
          : 'HyDE 转换失败，已回退原始问题'
        : route?.query_strategy === 'hyde'
          ? '等待 HyDE 查询转换'
          : '当前策略不需要 HyDE',
      duration_ms: null,
      items: hyde?.retrieval_query ? [
        { label: 'method', value: hyde.method || '-' },
        { label: 'document', value: hyde.hypothetical_document || '-' },
      ] : [],
    },
    {
      key: 'step_back',
      title: '2. Step-back Transform',
      status: stepBack?.step_back_question
        ? stepBack.success ? 'done' : 'warn'
        : route?.query_strategy === 'step_back'
          ? generationStatus === 'idle' ? 'idle' : 'running'
          : trace ? 'skipped' : generationStatus === 'idle' ? 'idle' : 'pending',
      summary: stepBack?.step_back_question
        ? stepBack.success
          ? '已生成上位背景问题'
          : 'Step-back 转换失败，已回退原始问题'
        : route?.query_strategy === 'step_back'
          ? '等待 Step-back 查询转换'
          : '当前策略不需要 Step-back',
      duration_ms: null,
      items: stepBack?.step_back_question ? [
        { label: 'method', value: stepBack.method || '-' },
        { label: 'question', value: stepBack.step_back_question || '-' },
      ] : [],
    },
    {
      key: 'retrieval',
      title: isMultiStep ? '3. Multi-hop Retrieval' : route?.query_strategy === 'step_back' ? '3. Step-back Retrieval' : '3. Retrieval',
      status: trace ? 'done' : generationStatus === 'idle' ? 'idle' : 'running',
      summary: retrievalQuery ? `检索 query：${retrievalQuery}` : '等待检索',
      duration_ms: null,
      items: subRetrievals.length
        ? subRetrievals.map((item: any) => ({
          label: `hop${item.index}`,
          value: `${item.node_count ?? 0} nodes · ${item.query || '-'}`,
        }))
        : [],
    },
    {
      key: 'quality',
      title: '4. Quality Check',
      status: quality?.quality ? qualityStatus(quality.quality) : generationStatus === 'idle' ? 'idle' : 'pending',
      summary: quality?.reason || '等待质量评估',
      duration_ms: null,
      items: [],
    },
    {
      key: 'rewrite',
      title: '5. Query Rewrite',
      status: rewrite?.rewritten ? 'done' : trace ? 'skipped' : generationStatus === 'idle' ? 'idle' : 'pending',
      summary: rewrite?.rewritten ? '检索质量较差，已改写问题并重试' : trace ? '未触发改写' : '等待质量判断',
      duration_ms: null,
      items: [],
    },
    {
      key: 'synthesis',
      title: '6. Intermediate Synthesis',
      status: intermediateAnswers.length ? 'done' : isMultiStep ? generationStatus === 'idle' ? 'idle' : 'pending' : trace ? 'skipped' : generationStatus === 'idle' ? 'idle' : 'pending',
      summary: intermediateAnswers.length
        ? `已生成 ${intermediateAnswers.length} 个子问题中间答案`
        : isMultiStep
          ? '等待子问题中间答案生成'
          : '单跳问题，跳过中间答案综合',
      duration_ms: null,
      items: intermediateAnswers.map((item: any) => ({
        label: `q${item.index}`,
        value: item.answer || '-',
      })),
    },
    {
      key: 'generation',
      title: '7. Generation',
      status: generationStatus,
      summary: generationStatus === 'done' ? '回答生成完成' : generationStatus === 'running' ? '等待生成回答' : '等待问题输入',
      duration_ms: null,
      items: [],
    },
    {
      key: 'grounding',
      title: '8. Grounding Check',
      status: grounding ? groundingStatus(grounding.verdict) : generationStatus === 'done' ? 'skipped' : generationStatus === 'idle' ? 'idle' : 'pending',
      summary: grounding
        ? grounding.verdict === 'skipped'
          ? grounding.summary || '已关闭反思模式，跳过校验'
          : `${grounding.verdict || 'unknown'} · score=${formatMaybeNumber(grounding.grounding_score)}`
        : '反思模式关闭时跳过校验',
      duration_ms: null,
      items: grounding ? [
        { label: 'summary', value: grounding.summary || '-' },
        { label: 'unsupported', value: (grounding.unsupported_points || []).slice(0, 3).join('；') || '-' },
      ] : [],
    },
  ]
}

function qualityStatus(value: string) {
  if (value === 'good' || value === 'chitchat') return 'done'
  if (value === 'bad') return 'warn'
  return 'done'
}

function groundingStatus(value: string) {
  if (value === 'pass') return 'done'
  if (value === 'skipped') return 'skipped'
  if (value === 'warning') return 'warn'
  if (value === 'fail') return 'warn'
  return 'done'
}

function formatMaybeNumber(value: any) {
  if (typeof value !== 'number') return '-'
  return value.toFixed(2)
}

function intentLabel(intent: string) {
  const map: Record<string, string> = {
    chitchat: '闲聊',
    knowledge_base: '知识库检索',
    multi_step: '多步知识库检索',
    web_search: '联网搜索',
    sql_query: '结构化查询',
  }
  return map[intent] || intent || '-'
}

function strategyLabel(strategy: string) {
  const map: Record<string, string> = {
    direct: '直接检索',
    decomposition: '子问题分解',
    hyde: 'HyDE',
    step_back: 'Step-back',
    web_search: 'Web Search',
    sql_query: 'SQL Query',
    chitchat: '闲聊直答',
  }
  return map[strategy] || strategy || '-'
}

function renderMarkdown(value: string) {
  return marked.parse(value || '', { breaks: true })
}

async function scrollToBottom() {
  await nextTick()
  if (chatContainer.value) {
    chatContainer.value.scrollTo({ top: chatContainer.value.scrollHeight, behavior: 'smooth' })
  }
}

function autoResize() {
  if (!textarea.value) return
  textarea.value.style.height = 'auto'
  textarea.value.style.height = `${textarea.value.scrollHeight}px`
}

async function sendMessage() {
  const value = inputMessage.value.trim()
  if (!value || isLoading.value) return

  messages.value.push({ role: 'user', content: value, sources: [], citations: [] })
  inputMessage.value = ''
  if (textarea.value) textarea.value.style.height = 'auto'

  const assistantMessage = reactive<ChatMessage>({
    role: 'assistant',
    content: '',
    sources: [],
    citations: [],
    trace: null,
    route: null,
    flowSteps: buildFlowSteps(null, 'running'),
  })
  messages.value.push(assistantMessage)

  isLoading.value = true
  streamStatus.value = '正在路由问题...'
  selectedSource.value = null
  await scrollToBottom()

  try {
    await streamChat(value, assistantMessage)
  } catch (error: any) {
    streamStatus.value = '流式接口请求失败'
    assistantMessage.content = `请求失败：${error.message || error}`
  } finally {
    isLoading.value = false
    streamStatus.value = ''
    if (!assistantMessage.content) assistantMessage.content = '没有收到有效回答。'
    await scrollToBottom()
    emit('session-used', props.sessionId)
  }
}

async function streamChat(value: string, assistantMessage: ChatMessage) {
  const token = localStorage.getItem('weiquiz_token')
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => {
    controller.abort()
  }, STREAM_REQUEST_TIMEOUT_MS)

  console.debug('[WeiQuiz] stream request start', {
    sessionId: props.sessionId,
    message: value,
    groundingMode: groundingMode.value,
  })

  let response: Response
  try {
    response = await fetch(`${API_BASE}/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      signal: controller.signal,
      body: JSON.stringify({
        session_id: props.sessionId,
        message: value,
        grounding_mode: groundingMode.value,
      }),
    })
  } catch (error: any) {
    if (error?.name === 'AbortError') {
      throw new Error('流式接口 30 秒未返回响应，请检查后端 /chat/stream 是否收到请求。')
    }
    throw error
  } finally {
    window.clearTimeout(timeoutId)
  }

  console.debug('[WeiQuiz] stream response', {
    ok: response.ok,
    status: response.status,
    hasBody: Boolean(response.body),
  })

  if (!response.ok || !response.body) {
    const text = await response.text()
    throw new Error(text || `HTTP ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { value: chunk, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(chunk, { stream: true })

    const frames = buffer.split('\n\n')
    buffer = frames.pop() || ''
    for (const frame of frames) {
      console.debug('[WeiQuiz] sse frame', frame.slice(0, 120))
      handleSseFrame(frame, assistantMessage)
    }
    await scrollToBottom()
  }

  if (buffer.trim()) handleSseFrame(buffer, assistantMessage)
  await waitForTypewriter(assistantMessage)
}

function typewriterState(message: ChatMessage): TypewriterState {
  let state = typewriterStates.get(message)
  if (!state) {
    state = { queue: '', timer: null, waiters: [] }
    typewriterStates.set(message, state)
  }
  return state
}

function enqueueTypewriter(message: ChatMessage, text: string) {
  if (!text) return
  const state = typewriterState(message)
  state.queue += text
  if (state.timer !== null) return

  state.timer = window.setInterval(() => {
    const next = state.queue.slice(0, TYPEWRITER_CHARS_PER_TICK)
    state.queue = state.queue.slice(TYPEWRITER_CHARS_PER_TICK)
    message.content += next
    messages.value = [...messages.value]
    void scrollToBottom()

    if (!state.queue) {
      if (state.timer !== null) {
        window.clearInterval(state.timer)
        state.timer = null
      }
      const waiters = state.waiters.splice(0)
      waiters.forEach(resolve => resolve())
    }
  }, TYPEWRITER_INTERVAL_MS)
}

function waitForTypewriter(message: ChatMessage) {
  const state = typewriterState(message)
  if (!state.queue && state.timer === null) return Promise.resolve()
  return new Promise<void>(resolve => state.waiters.push(resolve))
}

function handleSseFrame(frame: string, assistantMessage: ChatMessage) {
  const lines = frame.split(/\r?\n/)
  let event = 'message'
  const dataLines: string[] = []
  for (const line of lines) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }
  const data = dataLines.join('\n')
  if (!data || data === '[DONE]') return

  if (event === 'route') {
    assistantMessage.route = JSON.parse(data)
    assistantMessage.flowSteps = buildFlowSteps({ route: assistantMessage.route }, 'running')
    streamStatus.value = 'Router 已完成，正在检索...'
    return
  }

  if (event === 'trace') {
    assistantMessage.trace = JSON.parse(data)
    assistantMessage.route = assistantMessage.trace?.route || assistantMessage.route
    assistantMessage.flowSteps = buildFlowSteps(assistantMessage.trace, 'done')
    streamStatus.value = '检索质量检查完成，正在生成回答...'
    return
  }

  if (event === 'step') {
    const step = JSON.parse(data)
    if (assistantMessage.flowSteps && step?.key) {
      const idx = assistantMessage.flowSteps.findIndex(s => s.key === step.key)
      if (idx >= 0) {
        assistantMessage.flowSteps[idx] = { ...assistantMessage.flowSteps[idx], ...step }
      }
    }
    streamStatus.value = step?.summary || step?.title || streamStatus.value
    return
  }

  if (event === 'status') {
    streamStatus.value = data
    return
  }

  if (event === 'chunk') {
    if (!assistantMessage.content) {
      streamStatus.value = '正在生成回答...'
    }
    enqueueTypewriter(assistantMessage, data.replaceAll('\\n', '\n'))
    // 强制触发响应式更新
    return
  }

  if (event === 'result') {
    const payload = JSON.parse(data)
    assistantMessage.route = payload.route || assistantMessage.route
    assistantMessage.trace = payload.trace || assistantMessage.trace
    assistantMessage.sources = payload.source_nodes || []
    assistantMessage.citations = payload.citations || []
    if (assistantMessage.flowSteps) {
      assistantMessage.flowSteps = assistantMessage.flowSteps.map(s =>
        s.key === 'generation' ? { ...s, status: 'done', summary: '回答生成完成' } : s
      )
    }
    if (assistantMessage.sources.length) {
      selectedSource.value = assistantMessage.sources[0]
    }
  }
}

async function clearChat() {
  try {
    const { data } = await api.post('/sessions')
    emit('new-session', data.session_id)
  } catch {
    const newId = crypto.randomUUID()
    emit('new-session', newId)
  }
}

// 切换会话时加载历史消息
async function loadSessionMessages(sid: string) {
  try {
    const { data } = await api.get(`/sessions/${sid}/messages`)
    const msgs = data.messages || []
    if (msgs.length === 0) {
      messages.value = [{
        role: 'assistant',
        content: '你好，我是 **WeiQuiz Enterprise RAG**。\n\n你可以上传企业制度、课件、技术文档或业务材料，然后向我提问。',
        sources: [],
        citations: [],
        flowSteps: buildFlowSteps(null, 'idle'),
      }]
    } else {
      messages.value = msgs.map((m: any) => ({
        role: m.role,
        content: m.content,
        sources: m.sources || [],
        citations: m.citations || [],
        trace: m.trace || null,
        route: m.route || null,
        flowSteps: buildFlowSteps(m.trace, m.role === 'assistant' ? 'done' : 'idle'),
      }))
    }
  } catch (error: any) {
    if (error?.response?.status === 404) {
      messages.value = [{
        role: 'assistant',
        content: '你好，我是 **WeiQuiz Enterprise RAG**。\n\n这是一个新的会话，你可以直接提问；发送第一条消息后，系统会自动创建会话记录。',
        sources: [],
        citations: [],
        flowSteps: buildFlowSteps(null, 'idle'),
      }]
      selectedSource.value = null
      await scrollToBottom()
      return
    }
    messages.value = [{
      role: 'assistant',
      content: '你好，我是 **WeiQuiz Enterprise RAG**。\n\n你可以上传企业制度、课件、技术文档或业务材料，然后向我提问。',
      sources: [],
      citations: [],
      flowSteps: buildFlowSteps(null, 'idle'),
    }]
  }
  selectedSource.value = null
  await scrollToBottom()
}

watch(() => props.sessionId, (newId) => {
  if (newId) loadSessionMessages(newId)
}, { immediate: true })

function selectSource(source: any, index: number) {
  selectedSource.value = source
  selectedMessageIndex.value = index
}

function compactSourceTitle(source: any) {
  const title = source.parent_section_title || source.file_name || source.parent_source_path || source.source_path || '未知来源'
  return title.length > 18 ? `${title.slice(0, 18)}...` : title
}

</script>

<template>
  <section class="min-h-0 flex flex-col bg-gradient-card card-shadow">
    <!-- Header -->
    <div class="h-16 px-5 border-b border-slate-100 bg-white/80 backdrop-blur-sm flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="h-8 w-8 rounded-xl bg-gradient-primary flex items-center justify-center shadow-lg shadow-indigo-200/50">
          <Icon name="bot-message-square" class="h-4 w-4 text-white" />
        </div>
        <div>
          <h2 class="text-sm font-bold text-slate-800">知识库问答</h2>
          <p class="text-[10px] text-slate-400">Enterprise RAG System</p>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <button @click="inputMessage = 'Quantum API Gateway V3.5 为什么从 Nginx + Lua 替换为 Envoy Proxy？'; autoResize()"
                class="hidden md:inline-flex h-8 items-center rounded-lg border border-slate-200 px-3 text-xs font-semibold text-slate-600 hover:bg-slate-50 hover:border-indigo-200 transition-all">
          <Icon name="lightbulb" class="h-3 w-3 mr-1.5" />
          示例问题
        </button>
        <button @click="clearChat" class="h-8 w-8 grid place-items-center rounded-lg border border-slate-200 text-slate-400 hover:text-rose-500 hover:border-rose-200 hover:bg-rose-50 transition-all" title="清空会话">
          <Icon name="trash-2" class="h-4 w-4" />
        </button>
      </div>
    </div>

    <!-- Messages -->
    <div ref="chatContainer" class="min-h-0 flex-1 overflow-y-auto p-5 md:p-6 space-y-6 bg-gradient-slate">
      <!-- Welcome Message -->
      <div v-if="messages.length === 0" class="h-full flex flex-col items-center justify-center text-center fade-in">
        <div class="h-20 w-20 rounded-2xl bg-gradient-primary/10 flex items-center justify-center mb-6 animate-float">
          <Icon name="search" class="h-10 w-10 text-indigo-500" />
        </div>
        <h3 class="text-lg font-bold text-slate-800 mb-2">欢迎使用知识库问答系统</h3>
        <p class="text-sm text-slate-500 max-w-xs">输入您的问题，系统将通过 Agentic RAG 流程为您提供准确的答案</p>
        <button @click="inputMessage = '什么是 Agentic RAG？'; autoResize()"
                class="mt-4 px-4 py-2 rounded-xl bg-white border border-slate-200 text-sm font-semibold text-slate-700 hover:border-indigo-300 hover:text-indigo-700 hover:shadow-md transition-all">
          了解更多
        </button>
      </div>

      <div v-for="(msg, index) in messages" :key="index" class="message-enter flex" :class="msg.role === 'user' ? 'justify-end' : 'justify-start'">
          <div class="max-w-[90%] md:max-w-[80%] flex gap-3" :class="msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'">
            <!-- Avatar -->
            <div class="h-9 w-9 shrink-0 rounded-full grid place-items-center shadow-md transition-transform hover:scale-105"
                 :class="msg.role === 'user' ? 'bg-gradient-to-br from-indigo-500 to-purple-500 text-white' : 'bg-gradient-to-br from-emerald-500 to-teal-500 text-white'">
              <Icon :name="msg.role === 'user' ? 'user' : 'cpu'" class="h-4 w-4" />
            </div>

            <article 
              @click="msg.role === 'assistant' && (selectedMessageIndex = index)"
              class="min-w-0 cursor-pointer transition-all duration-200"
              :class="msg.role === 'assistant' && selectedMessageIndex === index ? 'scale-[1.01]' : ''"
            >
              <!-- User message -->
              <div v-if="msg.role === 'user'" class="rounded-2xl rounded-tr-sm bg-gradient-to-br from-indigo-600 to-indigo-700 px-5 py-3.5 text-sm leading-relaxed text-white shadow-lg shadow-indigo-900/25 whitespace-pre-wrap">
                {{ msg.content }}
              </div>

              <!-- Assistant message -->
              <div v-else class="rounded-2xl rounded-tl-sm border bg-white px-5 py-4 text-sm shadow-lg transition-all"
                   :class="selectedMessageIndex === index ? 'border-indigo-300 shadow-indigo-200/50' : 'border-slate-150 shadow-slate-200/50'">
                <div class="markdown-body" v-html="renderMarkdown(msg.content)"></div>

              <!-- Citations -->
              <div v-if="msg.citations?.length" class="mt-4 rounded-xl bg-emerald-50/50 border border-emerald-100 p-4">
                <div class="mb-3 flex items-center gap-2 text-[11px] font-bold uppercase tracking-wide text-emerald-600">
                  <div class="h-4 w-4 rounded-md bg-emerald-100 flex items-center justify-center">
                    <Icon name="badge-check" class="h-3 w-3" />
                  </div>
                  引用证据
                </div>
                <div class="grid gap-2">
                  <button v-for="citation in msg.citations" :key="citation.source_id"
                          class="rounded-xl border border-emerald-200 bg-white/80 px-4 py-3 text-left text-xs hover:border-emerald-300 hover:bg-white hover:shadow-md transition-all">
                    <div class="flex items-center justify-between gap-2">
                      <span class="font-bold text-emerald-700">来源 {{ citation.source_id }}</span>
                      <Icon name="external-link" class="h-3 w-3 text-slate-400" />
                    </div>
                    <div class="mt-1.5 line-clamp-2 font-semibold text-slate-700">{{ citation.file_name || 'unknown_file' }}</div>
                  </button>
                </div>
              </div>

              <!-- Sources -->
              <div v-if="msg.sources?.length" class="mt-4 rounded-xl bg-slate-50/50 border border-slate-100 p-4">
                <div class="mb-3 flex items-center gap-2 text-[11px] font-bold uppercase tracking-wide text-slate-600">
                  <div class="h-4 w-4 rounded-md bg-slate-100 flex items-center justify-center">
                    <Icon name="quote" class="h-3 w-3" />
                  </div>
                  来源片段
                </div>
                <div class="flex flex-wrap gap-2.5">
                  <button v-for="(node, nIndex) in msg.sources" :key="nIndex"
                          @click="selectSource(node, index)"
                          class="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700 hover:shadow-sm transition-all">
                    <span class="h-5 w-5 rounded-lg bg-gradient-primary text-white grid place-items-center text-[10px] font-bold">{{ nIndex + 1 }}</span>
                    {{ compactSourceTitle(node) }}
                  </button>
                </div>
              </div>
            </div>
          </article>
        </div>
      </div>

      <!-- Loading indicator -->
      <div v-if="isLoading" class="flex justify-start message-enter">
        <div class="flex gap-3">
          <div class="h-9 w-9 rounded-full bg-gradient-to-br from-emerald-500 to-teal-500 text-white grid place-items-center shadow-md">
            <Icon name="cpu" class="h-4 w-4" />
          </div>
          <div class="rounded-2xl rounded-tl-sm border border-slate-150 bg-white px-5 py-4 shadow-lg shadow-slate-200/50">
            <div class="flex items-center gap-2">
              <div class="flex gap-1.5">
                <span class="h-2.5 w-2.5 rounded-full bg-indigo-500 animate-bounce"></span>
                <span class="h-2.5 w-2.5 rounded-full bg-indigo-500 animate-bounce" style="animation-delay:.15s"></span>
                <span class="h-2.5 w-2.5 rounded-full bg-indigo-500 animate-bounce" style="animation-delay:.3s"></span>
              </div>
              <span class="ml-2 text-sm font-semibold text-slate-500">{{ streamStatus || '正在执行 Agentic RAG 流程...' }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Input -->
    <footer class="shrink-0 border-t border-slate-100 bg-white/90 backdrop-blur-sm p-4">
      <div class="mx-auto mb-2 flex max-w-4xl items-center justify-between gap-3 text-xs text-slate-500">
        <div class="font-medium">
          当前模式：{{ groundingMode === 'reflection' ? '反思模式，会额外校验证据一致性' : '快速模式，跳过反思校验' }}
        </div>
        <label class="flex items-center gap-2">
          <span class="font-semibold text-slate-600">回答模式</span>
          <select
            v-model="groundingMode"
            :disabled="isLoading"
            class="h-8 rounded-lg border border-slate-200 bg-white px-2 text-xs font-semibold text-slate-700 outline-none transition-all hover:border-indigo-300 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
          >
            <option value="off">快速模式</option>
            <option value="reflection">反思模式</option>
          </select>
        </label>
      </div>
      <form @submit.prevent="sendMessage" class="mx-auto flex max-w-4xl items-end gap-3 rounded-2xl border border-slate-200 bg-white p-2.5 shadow-lg shadow-slate-200/20 focus-within:border-indigo-400 focus-within:ring-4 focus-within:ring-indigo-100 transition-all">
        <textarea v-model="inputMessage"
                  :disabled="isLoading"
                  ref="textarea"
                  rows="1"
                  @input="autoResize"
                  @keydown.enter.exact.prevent="sendMessage"
                  class="min-h-[48px] max-h-36 flex-1 resize-none bg-transparent px-4 py-3 text-sm outline-none placeholder:text-slate-400 text-slate-700"
                  placeholder="输入知识库问题，Enter 发送..."></textarea>
        <button :disabled="isLoading || !inputMessage.trim()" class="h-12 w-12 shrink-0 grid place-items-center rounded-xl bg-gradient-primary text-white hover:shadow-lg hover:shadow-indigo-200 disabled:opacity-50 transition-all">
          <Icon name="arrow-up" class="h-5 w-5" />
        </button>
      </form>
      <p class="mt-2 text-center text-[11px] text-slate-400">回答来自本地知识库检索，右侧可查看 Agentic Trace 和来源详情。</p>
    </footer>
  </section>
</template>
