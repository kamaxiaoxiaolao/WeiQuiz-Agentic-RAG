<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api'
import Icon from './Icon.vue'

const router = useRouter()
const auth = useAuthStore()

const health = ref({ ok: false, label: '未连接' })

async function checkHealth() {
  try {
    const res = await api.get('/health')
    health.value = res.data.status === 'ok'
      ? { ok: true, label: '服务正常' }
      : { ok: false, label: '服务异常' }
  } catch {
    health.value = { ok: false, label: '未连接' }
  }
}

function logout() {
  auth.logout()
  router.push('/login')
}

onMounted(checkHealth)

defineExpose({ checkHealth })
</script>

<template>
  <header class="h-16 shrink-0 border-b border-slate-100 bg-white/90 backdrop-blur-sm px-6 flex items-center justify-between shadow-sm">
    <div class="flex items-center gap-4 min-w-0">
      <div class="h-10 w-10 rounded-xl bg-gradient-primary flex items-center justify-center shadow-lg shadow-indigo-200/50">
        <Icon name="network" class="h-5 w-5 text-white" />
      </div>
      <div class="min-w-0">
        <h1 class="text-base font-bold text-slate-800 truncate">WeiQuiz Enterprise RAG</h1>
        <p class="text-[11px] text-slate-400 truncate">Agentic RAG · 企业私有知识库问答系统</p>
      </div>
    </div>

    <div class="flex items-center gap-4">
      <span 
        class="inline-flex items-center gap-2 rounded-xl border px-3.5 py-1.5 text-xs font-semibold transition-all hover:shadow-sm"
        :class="health.ok ? 'border-emerald-200 bg-gradient-to-r from-emerald-50 to-emerald-100/50 text-emerald-700' : 'border-rose-200 bg-gradient-to-r from-rose-50 to-rose-100/50 text-rose-700'"
      >
        <span 
          class="h-2.5 w-2.5 rounded-full transition-all" 
          :class="health.ok ? 'bg-emerald-500' : 'bg-rose-500 animate-pulse'"
        ></span>
        {{ health.label }}
      </span>

      <div v-if="auth.user" class="flex items-center gap-3">
        <div class="hidden sm:flex items-center gap-2 rounded-xl bg-gradient-to-r from-indigo-50 to-indigo-100/50 px-3.5 py-2 text-xs font-semibold text-indigo-700 border border-indigo-100">
          <div class="h-6 w-6 rounded-lg bg-gradient-primary flex items-center justify-center">
            <Icon name="user" class="h-3.5 w-3.5 text-white" />
          </div>
          {{ auth.user.display_name || auth.user.username }}
          <span v-if="auth.isAdmin" class="rounded-lg bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-700">ADMIN</span>
        </div>
        <button 
          @click="logout" 
          class="h-9 w-9 grid place-items-center rounded-xl border border-slate-200 bg-white text-slate-400 hover:text-rose-500 hover:border-rose-200 hover:bg-rose-50 transition-all" 
          title="退出登录"
        >
          <Icon name="log-out" class="h-4 w-4" />
        </button>
      </div>
    </div>
  </header>
</template>
