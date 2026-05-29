<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api'
import { User, Smile, Mail, Lock, Eye, EyeOff, LogIn, UserPlus, Loader2, CircleAlert, CircleCheck, Network } from 'lucide-vue-next'

const router = useRouter()
const auth = useAuthStore()

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const displayName = ref('')
const email = ref('')
const showPassword = ref(false)
const loading = ref(false)
const error = ref('')
const success = ref('')

function clearMessages() {
  error.value = ''
  success.value = ''
}

async function submit() {
  clearMessages()
  if (!username.value.trim()) { error.value = '请输入用户名'; return }
  if (!password.value) { error.value = '请输入密码'; return }

  if (mode.value === 'register') {
    if (password.value.length < 6) { error.value = '密码长度至少 6 位'; return }
    if (password.value !== confirmPassword.value) { error.value = '两次输入的密码不一致'; return }
    await doRegister()
  } else {
    await doLogin()
  }
}

async function doRegister() {
  loading.value = true
  try {
    const body: Record<string, string> = {
      username: username.value.trim(),
      password: password.value,
    }
    if (displayName.value.trim()) body.display_name = displayName.value.trim()
    if (email.value.trim()) body.email = email.value.trim()

    await api.post('/auth/register', body)
    success.value = '注册成功！正在自动登录...'
    await new Promise(r => setTimeout(r, 600))
    await doLogin()
  } catch (e: any) {
    const status = e.response?.status
    const code = e.response?.data?.detail?.error?.code
    if (status === 409 && code === 'AUTH_USERNAME_EXISTS') error.value = '用户名已存在，请更换一个'
    else if (status === 409 && code === 'AUTH_EMAIL_EXISTS') error.value = '邮箱已被注册'
    else error.value = '注册失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function doLogin() {
  loading.value = true
  try {
    const res = await api.post('/auth/login', {
      username: username.value.trim(),
      password: password.value,
    })
    auth.setAuth(res.data.access_token, res.data.user)
    success.value = '登录成功，正在跳转...'
    await new Promise(r => setTimeout(r, 400))
    router.push('/')
  } catch {
    error.value = '用户名或密码错误'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-50 via-white to-indigo-50 flex items-center justify-center p-4">
    <div class="w-full max-w-md fade-in">
      <!-- Logo -->
      <div class="text-center mb-8">
        <div class="inline-flex items-center justify-center h-16 w-16 rounded-2xl bg-indigo-600 text-white shadow-lg shadow-indigo-200 mb-4">
          <Network class="h-8 w-8" />
        </div>
        <h1 class="text-2xl font-bold text-slate-800">WeiQuiz Enterprise RAG</h1>
        <p class="text-sm text-slate-500 mt-1">企业私有知识库问答系统</p>
      </div>

      <!-- Card -->
      <div class="bg-white rounded-2xl shadow-xl shadow-slate-200/50 border border-slate-200 overflow-hidden">
        <!-- Tab -->
        <div class="flex border-b border-slate-100">
          <button @click="mode = 'login'; clearMessages()" class="flex-1 py-3.5 text-sm font-bold transition"
                  :class="mode === 'login' ? 'text-indigo-600 border-b-2 border-indigo-600 bg-indigo-50/30' : 'text-slate-400 hover:text-slate-600'">
            登录
          </button>
          <button @click="mode = 'register'; clearMessages()" class="flex-1 py-3.5 text-sm font-bold transition"
                  :class="mode === 'register' ? 'text-indigo-600 border-b-2 border-indigo-600 bg-indigo-50/30' : 'text-slate-400 hover:text-slate-600'">
            注册
          </button>
        </div>

        <div class="p-6 space-y-5">
          <!-- Error -->
          <div v-if="error" class="rounded-xl bg-rose-50 border border-rose-200 px-4 py-3 flex items-start gap-3">
            <CircleAlert class="h-5 w-5 text-rose-500 shrink-0 mt-0.5" />
            <p class="text-sm text-rose-700">{{ error }}</p>
          </div>

          <!-- Success -->
          <div v-if="success" class="rounded-xl bg-emerald-50 border border-emerald-200 px-4 py-3 flex items-start gap-3">
            <CircleCheck class="h-5 w-5 text-emerald-500 shrink-0 mt-0.5" />
            <p class="text-sm text-emerald-700">{{ success }}</p>
          </div>

          <!-- Username -->
          <div>
            <label class="block text-xs font-bold text-slate-500 mb-1.5">用户名</label>
            <div class="relative">
              <User class="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400 pointer-events-none" />
              <input v-model="username" type="text" @keydown.enter="submit"
                     class="w-full h-11 pl-10 pr-4 rounded-xl border border-slate-200 bg-slate-50 text-sm outline-none focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-500/10 transition"
                     placeholder="请输入用户名" />
            </div>
          </div>

          <!-- Display Name (register only) -->
          <div v-if="mode === 'register'">
            <label class="block text-xs font-bold text-slate-500 mb-1.5">昵称 <span class="text-slate-300 font-normal">(可选)</span></label>
            <div class="relative">
              <Smile class="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400 pointer-events-none" />
              <input v-model="displayName" type="text" @keydown.enter="submit"
                     class="w-full h-11 pl-10 pr-4 rounded-xl border border-slate-200 bg-slate-50 text-sm outline-none focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-500/10 transition"
                     placeholder="留空则使用用户名" />
            </div>
          </div>

          <!-- Email (register only) -->
          <div v-if="mode === 'register'">
            <label class="block text-xs font-bold text-slate-500 mb-1.5">邮箱 <span class="text-slate-300 font-normal">(可选)</span></label>
            <div class="relative">
              <Mail class="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400 pointer-events-none" />
              <input v-model="email" type="email" @keydown.enter="submit"
                     class="w-full h-11 pl-10 pr-4 rounded-xl border border-slate-200 bg-slate-50 text-sm outline-none focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-500/10 transition"
                     placeholder="name@example.com" />
            </div>
          </div>

          <!-- Password -->
          <div>
            <label class="block text-xs font-bold text-slate-500 mb-1.5">密码</label>
            <div class="relative">
              <Lock class="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400 pointer-events-none" />
              <input v-model="password" :type="showPassword ? 'text' : 'password'" @keydown.enter="submit"
                     class="w-full h-11 pl-10 pr-11 rounded-xl border border-slate-200 bg-slate-50 text-sm outline-none focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-500/10 transition"
                     placeholder="请输入密码" />
              <button @click="showPassword = !showPassword" type="button"
                      class="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
                <EyeOff v-if="showPassword" class="h-4 w-4" />
                <Eye v-else class="h-4 w-4" />
              </button>
            </div>
          </div>

          <!-- Confirm Password (register only) -->
          <div v-if="mode === 'register'">
            <label class="block text-xs font-bold text-slate-500 mb-1.5">确认密码</label>
            <div class="relative">
              <Lock class="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400 pointer-events-none" />
              <input v-model="confirmPassword" :type="showPassword ? 'text' : 'password'" @keydown.enter="submit"
                     class="w-full h-11 pl-10 pr-4 rounded-xl border border-slate-200 bg-slate-50 text-sm outline-none focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-500/10 transition"
                     placeholder="请再次输入密码" />
            </div>
          </div>

          <!-- Submit -->
          <button @click="submit" :disabled="loading"
                  class="w-full h-12 rounded-xl bg-indigo-600 text-white text-sm font-bold hover:bg-indigo-700 disabled:opacity-50 inline-flex items-center justify-center gap-2 transition shadow-lg shadow-indigo-200">
            <Loader2 v-if="loading" class="h-4 w-4 animate-spin" />
            <LogIn v-else-if="mode === 'login'" class="h-4 w-4" />
            <UserPlus v-else class="h-4 w-4" />
            {{ loading ? '处理中...' : (mode === 'login' ? '登录' : '注册') }}
          </button>

          <!-- Switch hint -->
          <p class="text-center text-xs text-slate-400">
            <template v-if="mode === 'login'">
              还没有账号？
              <button @click="mode = 'register'; clearMessages()" class="text-indigo-600 font-semibold hover:underline">立即注册</button>
            </template>
            <template v-else>
              已有账号？
              <button @click="mode = 'login'; clearMessages()" class="text-indigo-600 font-semibold hover:underline">去登录</button>
            </template>
          </p>
        </div>
      </div>

      <p class="text-center text-[11px] text-slate-400 mt-6">WeiQuiz Enterprise RAG &copy; 2026</p>
    </div>
  </div>
</template>
