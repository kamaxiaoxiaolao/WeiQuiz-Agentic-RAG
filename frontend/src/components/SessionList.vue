<script setup lang="ts">
import { ref, onMounted } from 'vue'
import api from '@/api'
import Icon from './Icon.vue'

interface Session {
  session_id: string
  title: string
  created_at: string | null
  last_message_at: string | null
}

const emit = defineEmits<{
  (e: 'select', sessionId: string): void
  (e: 'new', sessionId: string): void
}>()

const props = defineProps<{
  activeSessionId: string
}>()

const sessions = ref<Session[]>([])
const loading = ref(false)
const editingSessionId = ref<string | null>(null)
const editingTitle = ref('')

async function loadSessions() {
  loading.value = true
  try {
    const { data } = await api.get('/sessions')
    sessions.value = data.sessions || []
  } catch {
    sessions.value = []
  } finally {
    loading.value = false
  }
}

async function deleteSession(sessionId: string, event: Event) {
  event.stopPropagation()
  
  const session = sessions.value.find(s => s.session_id === sessionId)
  const title = session?.title || '此会话'
  
  if (!confirm(`确定要删除会话「${title}」吗？此操作无法撤销。`)) {
    return
  }
  
  try {
    await api.delete(`/sessions/${sessionId}`)
    sessions.value = sessions.value.filter(s => s.session_id !== sessionId)
  } catch { /* ignore */ }
}

function startEdit(session: Session, event: Event) {
  event.stopPropagation()
  editingSessionId.value = session.session_id
  editingTitle.value = session.title
}

async function saveEdit(sessionId: string, event: Event) {
  event?.stopPropagation()
  const title = editingTitle.value.trim()
  if (!title) return
  
  try {
    await api.put(`/sessions/${sessionId}/title`, { title })
    const session = sessions.value.find(s => s.session_id === sessionId)
    if (session) {
      session.title = title
    }
  } catch (error) {
    console.error('更新会话标题失败:', error)
  } finally {
    editingSessionId.value = null
    editingTitle.value = ''
  }
}

function cancelEdit(event: Event) {
  event.stopPropagation()
  editingSessionId.value = null
  editingTitle.value = ''
}

async function createNewSession() {
  loading.value = true
  try {
    // 立即调用后端创建会话
    const { data } = await api.post('/sessions')
    const newSession: Session = {
      session_id: data.session_id,
      title: data.title,
      created_at: data.created_at,
      last_message_at: data.last_message_at || data.created_at,
    }
    // 插入到列表最前面
    sessions.value = [newSession, ...sessions.value]
    // 通知父组件切换到新会话
    emit('new', data.session_id)
  } catch (error) {
    console.error('创建会话失败:', error)
    // 降级方案：本地创建临时会话
    const tempId = crypto.randomUUID()
    const tempSession: Session = {
      session_id: tempId,
      title: '新会话',
      created_at: new Date().toISOString(),
      last_message_at: new Date().toISOString(),
    }
    sessions.value = [tempSession, ...sessions.value]
    emit('new', tempId)
  } finally {
    loading.value = false
  }
}

function formatTime(iso: string | null) {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin}分钟前`
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `${diffHour}小时前`
  const diffDay = Math.floor(diffHour / 24)
  if (diffDay < 7) return `${diffDay}天前`
  return d.toLocaleDateString('zh-CN')
}

onMounted(loadSessions)

defineExpose({ loadSessions })
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- Header -->
    <div class="h-14 px-5 border-b border-slate-200 bg-white flex items-center justify-between">
      <div class="flex items-center gap-2">
        <Icon name="message-square" class="h-4 w-4 text-indigo-600" />
        <h2 class="text-sm font-bold">会话列表</h2>
      </div>
      <button @click="createNewSession"
              class="h-8 w-8 grid place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 hover:text-indigo-600 hover:border-indigo-300"
              title="新建会话">
        <Icon name="plus" class="h-4 w-4" />
      </button>
    </div>

    <!-- Session list -->
    <div class="min-h-0 flex-1 overflow-y-auto">
      <div v-if="loading" class="p-5 text-center text-sm text-slate-400">加载中...</div>
      <div v-else-if="!sessions.length" class="p-5 text-center text-sm text-slate-400">暂无会话</div>
      <div v-else class="py-2">
        <div v-for="session in sessions" :key="session.session_id"
             class="w-full px-4 py-3 text-left hover:bg-indigo-50 transition-colors group"
             :class="session.session_id === activeSessionId ? 'bg-indigo-50 border-l-2 border-indigo-500' : 'border-l-2 border-transparent'">
          <!-- 编辑模式 -->
          <div v-if="editingSessionId === session.session_id" class="flex items-center gap-2">
            <input v-model="editingTitle"
                   @keyup.enter="saveEdit(session.session_id, $event)"
                   @keyup.esc="cancelEdit($event)"
                   class="flex-1 h-8 px-2 rounded border border-indigo-300 text-sm font-semibold text-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-200"
                   autofocus />
            <button @click="saveEdit(session.session_id, $event)"
                    class="h-8 w-8 grid place-items-center rounded bg-indigo-500 text-white hover:bg-indigo-600"
                    title="保存">
              <Icon name="check" class="h-4 w-4" />
            </button>
            <button @click="cancelEdit($event)"
                    class="h-8 w-8 grid place-items-center rounded bg-slate-200 text-slate-600 hover:bg-slate-300"
                    title="取消">
              <Icon name="x" class="h-4 w-4" />
            </button>
          </div>
          <!-- 显示模式 -->
          <div v-else @click="$emit('select', session.session_id)" class="flex items-center justify-between gap-2 cursor-pointer">
            <span class="text-sm font-semibold text-slate-700 truncate flex-1">{{ session.title || '新会话' }}</span>
            <div class="flex items-center gap-1">
              <button @click="startEdit(session, $event)"
                      class="shrink-0 h-6 w-6 grid place-items-center rounded text-slate-400 opacity-0 group-hover:opacity-100 hover:text-indigo-600 hover:bg-indigo-50"
                      title="编辑名称">
                <Icon name="pencil" class="h-3 w-3" />
              </button>
              <button @click="deleteSession(session.session_id, $event)"
                      class="shrink-0 h-6 w-6 grid place-items-center rounded text-slate-400 opacity-0 group-hover:opacity-100 hover:text-rose-600 hover:bg-rose-50"
                      title="删除会话">
                <Icon name="trash-2" class="h-3 w-3" />
              </button>
            </div>
          </div>
          <div class="mt-1 text-xs text-slate-400">{{ formatTime(session.last_message_at || session.created_at) }}</div>
        </div>
      </div>
    </div>
  </div>
</template>
