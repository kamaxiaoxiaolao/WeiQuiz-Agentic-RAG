<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { BookOpen, MessageSquareText } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import AppHeader from '@/components/AppHeader.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import DebugPanel from '@/components/DebugPanel.vue'
import KnowledgeSidebar from '@/components/KnowledgeSidebar.vue'
import SessionList from '@/components/SessionList.vue'

const auth = useAuthStore()
const chatPanelRef = ref<InstanceType<typeof ChatPanel> | null>(null)
const sessionListRef = ref<InstanceType<typeof SessionList> | null>(null)
const activeTab = ref<'sessions' | 'knowledge'>('sessions')
const activeSessionId = ref<string>(crypto.randomUUID())

const tabs = [
  { key: 'sessions', label: '会话', icon: MessageSquareText },
  { key: 'knowledge', label: '知识库', icon: BookOpen },
]

function tabClass(key: string) {
  return activeTab.value === key
    ? 'bg-white text-indigo-600 shadow-sm border border-slate-200 font-bold'
    : 'text-slate-500 hover:text-slate-800 hover:bg-white/50'
}

function selectTab(key: string) {
  if (key === 'sessions' || key === 'knowledge') {
    activeTab.value = key
  }
}

function handleNewSession(newId: string) {
  activeSessionId.value = newId
  nextTick(() => sessionListRef.value?.loadSessions())
}

function handleSelectSession(sessionId: string) {
  activeSessionId.value = sessionId
}

function handleSessionUsed() {
  sessionListRef.value?.loadSessions()
}

onMounted(async () => {
  if (!auth.user) {
    await auth.fetchUser()
  }
})
</script>

<template>
  <div class="h-screen flex flex-col overflow-hidden bg-slate-50 font-sans antialiased selection:bg-indigo-500/20">
    <AppHeader class="z-20 border-b border-slate-200/60 bg-white/80 backdrop-blur-md shadow-sm shadow-slate-100/50" />

    <main class="min-h-0 flex-1 grid grid-cols-1 xl:grid-cols-[350px_minmax(0,1fr)_420px] bg-white">
      <aside class="min-h-0 flex flex-col border-r border-slate-200/60 bg-slate-50/50">
        <div class="border-b border-slate-200/40 bg-white/70 p-3 backdrop-blur-sm">
          <div class="grid grid-cols-2 gap-1 rounded-lg border border-slate-200 bg-slate-100/70 p-1">
            <button
              v-for="tab in tabs"
              :key="tab.key"
              class="h-9 rounded-md text-xs font-semibold transition inline-flex items-center justify-center gap-1.5"
              :class="tabClass(tab.key)"
              @click="selectTab(tab.key)"
            >
              <component :is="tab.icon" class="h-4 w-4" />
              <span>{{ tab.label }}</span>
            </button>
          </div>
        </div>

        <div class="min-h-0 flex-1 overflow-hidden">
          <SessionList
            v-show="activeTab === 'sessions'"
            ref="sessionListRef"
            :active-session-id="activeSessionId"
            class="h-full"
            @select="handleSelectSession"
            @new="handleNewSession"
          />
          <div v-show="activeTab === 'knowledge'" class="h-full overflow-y-auto custom-scrollbar">
            <KnowledgeSidebar />
          </div>
        </div>
      </aside>

      <ChatPanel
        ref="chatPanelRef"
        :session-id="activeSessionId"
        class="bg-white"
        @new-session="handleNewSession"
        @session-used="handleSessionUsed"
      />

      <DebugPanel :chat-panel="chatPanelRef" class="border-l border-slate-200/60 bg-slate-50/30" />
    </main>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 5px;
  height: 5px;
}

.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}

.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 9999px;
}

.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}
</style>
