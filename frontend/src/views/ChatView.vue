<script setup lang="ts">
import { onMounted, ref, nextTick } from 'vue'
import { useAuthStore } from '@/stores/auth'
import AppHeader from '@/components/AppHeader.vue'
import KnowledgeSidebar from '@/components/KnowledgeSidebar.vue'
import SessionList from '@/components/SessionList.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import DebugPanel from '@/components/DebugPanel.vue'

const auth = useAuthStore()
const chatPanelRef = ref<InstanceType<typeof ChatPanel> | null>(null)
const sessionListRef = ref<InstanceType<typeof SessionList> | null>(null)

// 左侧边栏 Tab：'knowledge' | 'sessions'
const activeTab = ref<'knowledge' | 'sessions'>('sessions')

// 当前活跃会话 ID
const activeSessionId = ref<string>(crypto.randomUUID())

function handleNewSession(newId: string) {
  activeSessionId.value = newId
  nextTick(() => sessionListRef.value?.loadSessions())
}

function handleSelectSession(sessionId: string) {
  activeSessionId.value = sessionId
}

function handleSessionUsed() {
  // 消息发送后刷新会话列表（更新 last_message_at）
  sessionListRef.value?.loadSessions()
}

onMounted(async () => {
  if (!auth.user) {
    await auth.fetchUser()
  }
})
</script>

<template>
  <div class="h-screen flex flex-col overflow-hidden">
    <AppHeader />

    <main class="min-h-0 flex-1 grid grid-cols-1 xl:grid-cols-[330px_minmax(0,1fr)_420px]">
      <!-- 左侧边栏：Tab 切换 -->
      <div class="min-h-0 flex flex-col border-r border-slate-200 bg-slate-50">
        <!-- Tab 按钮 -->
        <div class="flex border-b border-slate-200 bg-white">
          <button @click="activeTab = 'sessions'"
                  class="flex-1 h-10 text-xs font-bold transition-colors"
                  :class="activeTab === 'sessions' ? 'text-indigo-600 border-b-2 border-indigo-600 bg-indigo-50/50' : 'text-slate-500 hover:text-slate-700'">
            会话列表
          </button>
          <button @click="activeTab = 'knowledge'"
                  class="flex-1 h-10 text-xs font-bold transition-colors"
                  :class="activeTab === 'knowledge' ? 'text-indigo-600 border-b-2 border-indigo-600 bg-indigo-50/50' : 'text-slate-500 hover:text-slate-700'">
            知识库管理
          </button>
        </div>

        <!-- Tab 内容 -->
        <div class="min-h-0 flex-1 overflow-hidden">
          <SessionList v-show="activeTab === 'sessions'"
                       ref="sessionListRef"
                       :active-session-id="activeSessionId"
                       @select="handleSelectSession"
                       @new="handleNewSession" />
          <div v-show="activeTab === 'knowledge'" class="h-full overflow-y-auto">
            <KnowledgeSidebar />
          </div>
        </div>
      </div>

      <ChatPanel ref="chatPanelRef"
                 :session-id="activeSessionId"
                 @new-session="handleNewSession"
                 @session-used="handleSessionUsed" />
      <DebugPanel :chat-panel="chatPanelRef" />
    </main>
  </div>
</template>
