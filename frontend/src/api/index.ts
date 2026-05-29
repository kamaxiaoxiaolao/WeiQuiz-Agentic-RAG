import axios from 'axios'
import router from '@/router'

const api = axios.create({
  baseURL: '',
  timeout: 30000,
})

// 请求拦截：自动加 token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('weiquiz_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截：401 跳登录
api.interceptors.response.use(
  (res) => res,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('weiquiz_token')
      localStorage.removeItem('weiquiz_user')
      router.push('/login')
    }
    return Promise.reject(error)
  }
)

export default api
