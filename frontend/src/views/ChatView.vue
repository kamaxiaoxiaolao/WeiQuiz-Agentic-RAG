这是为你精简、美化并规范化后的**完整单文件组件（SFC）代码**。

修改了局部色彩搭配，将高亮色统一为了精致的**靛蓝色系（Indigo）**，并增强了现代 UI 的悬浮卡片质感，你可以直接复制使用：

```vue
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
  <div class="h-screen flex flex-col overflow-hidden bg-slate-50 font-sans antialiased selection:bg-indigo-500/20">
    <AppHeader class="z-20 border-b border-slate-200/60 bg-white/80 backdrop-blur-md shadow-sm shadow-slate-100/50" />

    <main class="min-h-0 flex-1 grid grid-cols-1 xl:grid-cols-[330px_minmax(0,1fr)_420px] bg-white">
      
      <div class="min-h-0 flex flex-col border-r border-slate-200/60 bg-slate-50/50">
        
        <div class="p-3 border-b border-slate-200/40 bg-white/60 backdrop-blur-sm">
          <div class="flex space-x-1 p-1 bg-slate-200/40 rounded-xl border border-slate-200/20">
            <button @click="activeTab = 'sessions'"
                    class="flex-1 flex items-center justify-center gap-2 h-8 text-[13px] font-medium rounded-lg transition-all duration-200 select-none"
                    :class="activeTab === 'sessions' 
                      ? 'bg-white text-indigo-600 shadow-sm border border-slate-200/30 font-semibold' 
                      : 'text-slate-500 hover:text-slate-800 hover:bg-white/40'">
              <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="opacity-80"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
              <span>会话列表</span>
            </button>
            
            <button @click="activeTab = 'knowledge'"
                    class="flex-1 flex items-center justify-center gap-2 h-8 text-[13px] font-medium rounded-lg transition-all duration-200 select-none"
                    :class="activeTab === 'knowledge' 
                      ? 'bg-white text-indigo-600 shadow-sm border border-slate-200/30 font-semibold' 
                      : 'text-slate-500 hover:text-slate-800 hover:bg-white/40'">
              <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="opacity-80"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z"/><path d="M6 6h10M6 10h10"/></svg>
              <span>知识库管理</span>
            </button>
          </div>
        </div>

        <div class="min-h-0 flex-1 overflow-hidden">
          <SessionList v-show="activeTab === 'sessions'"
                       ref="sessionListRef"
                       :active-session-id="activeSessionId"
                       @select="handleSelectSession"
                       @new="handleNewSession"
                       class="h-full" />
          <div v-show="activeTab === 'knowledge'" class="h-full overflow-y-auto custom-scrollbar">
            <KnowledgeSidebar />
          </div>
        </div>
      </div>

      <ChatPanel ref="chatPanelRef"
                 :session-id="activeSessionId"
                 @new-session="handleNewSession"
                 @session-used="handleSessionUsed"
                 class="bg-white" />
                 
      <DebugPanel :chat-panel="chatPanelRef" 
                  class="border-l border-slate-200/60 bg-slate-50/30" />
    </main>
  </div>
</template>

<style scoped>
/* 优化 Webkit 内核浏览器的区域滚动条，使其更符合极简美学 */
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

```