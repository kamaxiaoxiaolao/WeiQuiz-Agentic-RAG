import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/api'

interface UserInfo {
  id: string
  username: string
  display_name: string
  email: string | null
  role: string
  status: string
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('weiquiz_token') || '')
  const user = ref<UserInfo | null>(null)

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === 'admin')

  async function fetchUser() {
    if (!token.value) return
    try {
      const res = await api.get<UserInfo>('/auth/me')
      user.value = res.data
    } catch {
      logout()
    }
  }

  function setAuth(tokenStr: string, userInfo: UserInfo) {
    token.value = tokenStr
    user.value = userInfo
    localStorage.setItem('weiquiz_token', tokenStr)
    localStorage.setItem('weiquiz_user', JSON.stringify(userInfo))
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('weiquiz_token')
    localStorage.removeItem('weiquiz_user')
  }

  return { token, user, isLoggedIn, isAdmin, fetchUser, setAuth, logout }
})
