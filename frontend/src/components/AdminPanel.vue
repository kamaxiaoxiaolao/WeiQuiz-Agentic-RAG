<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  Activity,
  BookOpen,
  Database,
  FileText,
  HardDrive,
  History,
  Loader2,
  RefreshCw,
  Search,
  Shield,
  UserCog,
  Users,
} from 'lucide-vue-next'
import api from '@/api'

type ManagedUser = {
  id: string
  username: string
  display_name: string
  email: string | null
  role: string
  status: string
  created_at?: string
  last_login_at?: string
}

type KnowledgeDocument = {
  id: string
  relative_path: string
  filename: string
  file_type: string
  file_size: number
  status: string
  updated_at?: string
}

type IngestJob = {
  job_id: string
  status: string
  trigger_type?: string
  created_by?: string
  created_at?: number
  updated_at?: number
  saved_files?: Array<{ file_name: string; file_size: number }>
  result?: { changed?: { added?: number; updated?: number; deleted?: number } }
  error?: string
}

type AuditLog = {
  id: string
  actor_username?: string
  action: string
  resource_type: string
  resource_id?: string
  resource_name?: string
  status: string
  detail?: Record<string, unknown>
  ip_address?: string
  created_at?: string
}

type AdminTab = 'overview' | 'users' | 'knowledge' | 'jobs' | 'audit'

const activeTab = ref<AdminTab>('overview')
const isLoading = ref(false)
const error = ref('')

const overview = ref<any>({
  users: { total: 0, active: 0, admins: 0, disabled: 0 },
  knowledge: { total_documents: 0, indexed_documents: 0, pending_documents: 0, total_size: 0, file_types: [] },
  jobs: { recent_total: 0, running: 0, failed: 0, latest: [] },
})

const users = ref<ManagedUser[]>([])
const userTotal = ref(0)
const userKeyword = ref('')
const roleFilter = ref('')
const statusFilter = ref('')
const updatingUserId = ref('')
const creatingUser = ref(false)
const newUser = ref({ username: '', password: '', display_name: '', email: '', role: 'user' })

const documents = ref<KnowledgeDocument[]>([])
const documentKeyword = ref('')
const jobs = ref<IngestJob[]>([])

const auditLogs = ref<AuditLog[]>([])
const auditTotal = ref(0)
const auditActor = ref('')
const auditAction = ref('')
const auditResourceType = ref('')
const auditStatus = ref('')

const tabs: Array<{ key: AdminTab; label: string; icon: any }> = [
  { key: 'overview', label: '总览', icon: Activity },
  { key: 'users', label: '用户', icon: Users },
  { key: 'knowledge', label: '知识库', icon: BookOpen },
  { key: 'jobs', label: '任务', icon: Database },
  { key: 'audit', label: '审计', icon: History },
]

const actionLabels: Record<string, string> = {
  'user.create': '创建用户',
  'user.update': '修改用户',
  'knowledge.upload': '上传文档',
  'knowledge.delete': '删除文档',
  'knowledge.reindex': '重建索引',
}

const filteredDocuments = computed(() => {
  const q = documentKeyword.value.trim().toLowerCase()
  if (!q) return documents.value
  return documents.value.filter((doc) => (
    doc.filename.toLowerCase().includes(q) ||
    doc.relative_path.toLowerCase().includes(q) ||
    doc.file_type.toLowerCase().includes(q)
  ))
})

function selectTab(tab: AdminTab) {
  activeTab.value = tab
}

function tabClass(key: AdminTab) {
  return activeTab.value === key
    ? 'bg-white text-indigo-600 shadow-sm border border-slate-200 font-bold'
    : 'text-slate-500 hover:text-slate-800 hover:bg-white/50'
}

function formatBytes(bytes = 0) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatTime(value?: string | number) {
  if (!value) return '-'
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleString()
}

function roleBadgeClass(role: string) {
  return role === 'admin'
    ? 'border-amber-200 bg-amber-50 text-amber-700'
    : 'border-slate-200 bg-slate-50 text-slate-600'
}

function statusBadgeClass(status: string) {
  if (['active', 'indexed', 'succeeded'].includes(status)) return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (['disabled', 'failed'].includes(status)) return 'border-rose-200 bg-rose-50 text-rose-700'
  if (status === 'running') return 'border-indigo-200 bg-indigo-50 text-indigo-700'
  return 'border-amber-200 bg-amber-50 text-amber-700'
}

function actionLabel(action: string) {
  return actionLabels[action] || action
}

function jobSummary(job: IngestJob) {
  const changed = job.result?.changed
  if (changed) return `新增 ${changed.added || 0} / 更新 ${changed.updated || 0} / 删除 ${changed.deleted || 0}`
  if (job.saved_files?.length) return `保存 ${job.saved_files.length} 个文件`
  if (job.error) return job.error
  return '等待处理'
}

function auditDetail(log: AuditLog) {
  if (!log.detail || !Object.keys(log.detail).length) return ''
  return JSON.stringify(log.detail)
}

async function loadOverview() {
  const { data } = await api.get('/admin/overview')
  overview.value = data
  jobs.value = data.jobs?.latest || jobs.value
}

async function loadUsers() {
  const { data } = await api.get('/admin/users', {
    params: {
      keyword: userKeyword.value.trim(),
      role: roleFilter.value,
      status: statusFilter.value,
      page_size: 100,
    },
  })
  users.value = data.users || []
  userTotal.value = data.total || 0
}

async function loadKnowledge() {
  const { data } = await api.get('/documents/library')
  documents.value = data.documents || []
  jobs.value = data.jobs || jobs.value
}

async function loadJobs() {
  const { data } = await api.get('/documents/jobs', { params: { limit: 30 } })
  jobs.value = data.jobs || []
}

async function loadAuditLogs() {
  const { data } = await api.get('/admin/audit-logs', {
    params: {
      actor: auditActor.value.trim(),
      action: auditAction.value,
      resource_type: auditResourceType.value,
      status: auditStatus.value,
      page_size: 80,
    },
  })
  auditLogs.value = data.logs || []
  auditTotal.value = data.total || 0
}

async function refreshAll() {
  isLoading.value = true
  error.value = ''
  try {
    await Promise.all([loadOverview(), loadUsers(), loadKnowledge(), loadJobs(), loadAuditLogs()])
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '加载后台数据失败'
  } finally {
    isLoading.value = false
  }
}

async function updateUser(user: ManagedUser, patch: Partial<ManagedUser>) {
  updatingUserId.value = user.id
  error.value = ''
  try {
    const { data } = await api.put(`/admin/users/${user.id}`, patch)
    const idx = users.value.findIndex((item) => item.id === user.id)
    if (idx >= 0) users.value[idx] = data
    await Promise.all([loadOverview(), loadAuditLogs()])
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '更新用户失败'
  } finally {
    updatingUserId.value = ''
  }
}

async function createManagedUser() {
  if (creatingUser.value) return
  creatingUser.value = true
  error.value = ''
  try {
    await api.post('/admin/users', {
      username: newUser.value.username.trim(),
      password: newUser.value.password,
      display_name: newUser.value.display_name.trim() || undefined,
      email: newUser.value.email.trim() || undefined,
      role: newUser.value.role,
    })
    newUser.value = { username: '', password: '', display_name: '', email: '', role: 'user' }
    await Promise.all([loadUsers(), loadOverview(), loadAuditLogs()])
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '创建用户失败'
  } finally {
    creatingUser.value = false
  }
}

function handleRoleChange(user: ManagedUser, event: Event) {
  updateUser(user, { role: (event.target as HTMLSelectElement).value })
}

function handleStatusChange(user: ManagedUser, event: Event) {
  updateUser(user, { status: (event.target as HTMLSelectElement).value })
}

onMounted(refreshAll)
</script>

<template>
  <div class="min-h-0 flex flex-col bg-slate-50">
    <div class="border-b border-slate-200 bg-white px-5 py-4">
      <div class="flex items-center justify-between gap-3">
        <div>
          <div class="text-sm font-bold text-slate-900">后台管理</div>
          <div class="mt-0.5 text-xs text-slate-500">用户、角色、知识库、任务和操作审计</div>
        </div>
        <button
          class="h-9 w-9 rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 disabled:opacity-50 grid place-items-center"
          :disabled="isLoading"
          title="刷新"
          @click="refreshAll"
        >
          <Loader2 v-if="isLoading" class="h-4 w-4 animate-spin" />
          <RefreshCw v-else class="h-4 w-4" />
        </button>
      </div>

      <div class="mt-4 grid grid-cols-5 gap-1 rounded-lg border border-slate-200 bg-slate-100/70 p-1">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          class="h-8 rounded-md text-xs font-semibold transition inline-flex items-center justify-center gap-1"
          :class="tabClass(tab.key)"
          @click="selectTab(tab.key)"
        >
          <component :is="tab.icon" class="h-3.5 w-3.5" />
          <span>{{ tab.label }}</span>
        </button>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto p-5">
      <div v-if="error" class="mb-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs font-semibold text-rose-700">
        {{ error }}
      </div>

      <section v-if="activeTab === 'overview'" class="space-y-5">
        <div class="grid grid-cols-2 gap-3">
          <div class="rounded-lg border border-slate-200 bg-white p-4">
            <div class="flex items-center gap-2 text-xs font-bold text-slate-400"><Users class="h-4 w-4" />用户总数</div>
            <div class="mt-2 text-2xl font-black text-slate-900">{{ overview.users.total }}</div>
            <div class="mt-1 text-xs text-slate-500">管理员 {{ overview.users.admins }}，禁用 {{ overview.users.disabled }}</div>
          </div>
          <div class="rounded-lg border border-slate-200 bg-white p-4">
            <div class="flex items-center gap-2 text-xs font-bold text-slate-400"><FileText class="h-4 w-4" />知识文档</div>
            <div class="mt-2 text-2xl font-black text-slate-900">{{ overview.knowledge.total_documents }}</div>
            <div class="mt-1 text-xs text-slate-500">已索引 {{ overview.knowledge.indexed_documents }}，待处理 {{ overview.knowledge.pending_documents }}</div>
          </div>
          <div class="rounded-lg border border-slate-200 bg-white p-4">
            <div class="flex items-center gap-2 text-xs font-bold text-slate-400"><HardDrive class="h-4 w-4" />存储体量</div>
            <div class="mt-2 text-2xl font-black text-slate-900">{{ formatBytes(overview.knowledge.total_size) }}</div>
            <div class="mt-1 truncate text-xs text-slate-500">{{ overview.knowledge.file_types?.join(', ') || '暂无类型' }}</div>
          </div>
          <div class="rounded-lg border border-slate-200 bg-white p-4">
            <div class="flex items-center gap-2 text-xs font-bold text-slate-400"><Database class="h-4 w-4" />最近任务</div>
            <div class="mt-2 text-2xl font-black text-slate-900">{{ overview.jobs.recent_total }}</div>
            <div class="mt-1 text-xs text-slate-500">运行中 {{ overview.jobs.running }}，失败 {{ overview.jobs.failed }}</div>
          </div>
        </div>
      </section>

      <section v-else-if="activeTab === 'users'" class="space-y-4">
        <div class="rounded-lg border border-slate-200 bg-white p-4">
          <div class="mb-3 text-xs font-bold text-slate-500">创建账号</div>
          <div class="grid gap-2">
            <input v-model="newUser.username" class="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm outline-none focus:border-indigo-400" placeholder="用户名" />
            <input v-model="newUser.password" class="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm outline-none focus:border-indigo-400" placeholder="初始密码，至少 6 位" type="password" />
            <div class="grid grid-cols-2 gap-2">
              <input v-model="newUser.display_name" class="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm outline-none focus:border-indigo-400" placeholder="昵称" />
              <select v-model="newUser.role" class="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs font-semibold text-slate-700">
                <option value="user">普通用户</option>
                <option value="admin">管理员</option>
              </select>
            </div>
            <input v-model="newUser.email" class="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm outline-none focus:border-indigo-400" placeholder="邮箱，可选" />
            <button
              class="h-9 rounded-lg bg-indigo-600 text-sm font-bold text-white hover:bg-indigo-700 disabled:opacity-50 inline-flex items-center justify-center gap-2"
              :disabled="creatingUser || !newUser.username.trim() || newUser.password.length < 6"
              @click="createManagedUser"
            >
              <Loader2 v-if="creatingUser" class="h-4 w-4 animate-spin" />
              <span>{{ creatingUser ? '创建中' : '创建账号' }}</span>
            </button>
          </div>
        </div>

        <div class="grid grid-cols-[1fr_88px_88px] gap-2">
          <div class="relative">
            <Search class="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
            <input v-model="userKeyword" class="h-9 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm outline-none focus:border-indigo-400" placeholder="搜索用户" @keyup.enter="loadUsers" />
          </div>
          <select v-model="roleFilter" class="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs font-semibold text-slate-700" @change="loadUsers">
            <option value="">角色</option>
            <option value="admin">管理员</option>
            <option value="user">普通用户</option>
          </select>
          <select v-model="statusFilter" class="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs font-semibold text-slate-700" @change="loadUsers">
            <option value="">状态</option>
            <option value="active">启用</option>
            <option value="disabled">禁用</option>
          </select>
        </div>

        <div class="text-xs font-bold text-slate-400">共 {{ userTotal }} 个账号</div>

        <article v-for="user in users" :key="user.id" class="rounded-lg border border-slate-200 bg-white p-4">
          <div class="flex items-start gap-3">
            <div class="h-10 w-10 shrink-0 rounded-lg bg-indigo-50 text-indigo-600 grid place-items-center"><UserCog class="h-5 w-5" /></div>
            <div class="min-w-0 flex-1">
              <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                  <div class="truncate text-sm font-bold text-slate-900">{{ user.display_name || user.username }}</div>
                  <div class="mt-0.5 truncate text-xs text-slate-500">{{ user.username }} · {{ user.email || '未设置邮箱' }}</div>
                </div>
                <Loader2 v-if="updatingUserId === user.id" class="h-4 w-4 animate-spin text-indigo-500" />
              </div>
              <div class="mt-3 flex flex-wrap items-center gap-2">
                <span class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-bold" :class="roleBadgeClass(user.role)"><Shield class="h-3 w-3" />{{ user.role }}</span>
                <span class="rounded-full border px-2 py-0.5 text-[10px] font-bold" :class="statusBadgeClass(user.status)">{{ user.status === 'active' ? '启用' : '禁用' }}</span>
              </div>
              <div class="mt-3 grid grid-cols-2 gap-2">
                <select class="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs font-semibold text-slate-700 outline-none focus:border-indigo-400" :value="user.role" :disabled="updatingUserId === user.id" @change="handleRoleChange(user, $event)">
                  <option value="user">普通用户</option>
                  <option value="admin">管理员</option>
                </select>
                <select class="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs font-semibold text-slate-700 outline-none focus:border-indigo-400" :value="user.status" :disabled="updatingUserId === user.id" @change="handleStatusChange(user, $event)">
                  <option value="active">启用</option>
                  <option value="disabled">禁用</option>
                </select>
              </div>
              <div class="mt-3 text-[11px] text-slate-400">创建：{{ formatTime(user.created_at) }} · 最近登录：{{ formatTime(user.last_login_at) }}</div>
            </div>
          </div>
        </article>
      </section>

      <section v-else-if="activeTab === 'knowledge'" class="space-y-4">
        <div class="relative">
          <Search class="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
          <input v-model="documentKeyword" class="h-9 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm outline-none focus:border-indigo-400" placeholder="搜索文档、路径或类型" />
        </div>
        <article v-for="doc in filteredDocuments" :key="doc.id" class="rounded-lg border border-slate-200 bg-white p-3">
          <div class="flex items-start gap-3">
            <div class="h-9 w-9 shrink-0 rounded-lg bg-slate-100 text-slate-600 grid place-items-center"><FileText class="h-4 w-4" /></div>
            <div class="min-w-0 flex-1">
              <div class="truncate text-sm font-bold text-slate-900">{{ doc.filename }}</div>
              <div class="mt-1 truncate text-[11px] text-slate-400">{{ doc.relative_path }}</div>
              <div class="mt-2 flex flex-wrap items-center gap-2">
                <span class="rounded-full border px-2 py-0.5 text-[10px] font-bold" :class="statusBadgeClass(doc.status)">{{ doc.status === 'indexed' ? '已索引' : '待处理' }}</span>
                <span class="text-[11px] text-slate-400">{{ doc.file_type.toUpperCase() }}</span>
                <span class="text-[11px] text-slate-400">{{ formatBytes(doc.file_size) }}</span>
              </div>
            </div>
          </div>
        </article>
      </section>

      <section v-else-if="activeTab === 'jobs'" class="space-y-3">
        <article v-for="job in jobs" :key="job.job_id" class="rounded-lg border border-slate-200 bg-white p-4">
          <div class="flex items-center justify-between gap-2">
            <span class="truncate text-[11px] font-mono text-slate-500">{{ job.job_id }}</span>
            <span class="rounded-full border px-2 py-0.5 text-[10px] font-bold" :class="statusBadgeClass(job.status)">{{ job.status }}</span>
          </div>
          <div class="mt-2 text-xs font-semibold text-slate-700">{{ job.trigger_type || 'ingest' }} · {{ jobSummary(job) }}</div>
          <div class="mt-1 text-[11px] text-slate-400">创建：{{ formatTime(job.created_at) }} · 更新：{{ formatTime(job.updated_at) }}</div>
          <div v-if="job.error" class="mt-3 rounded-lg bg-rose-50 p-3 text-[11px] text-rose-700">{{ job.error }}</div>
        </article>
      </section>

      <section v-else class="space-y-4">
        <div class="grid grid-cols-[1fr_110px_96px] gap-2">
          <div class="relative">
            <Search class="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
            <input v-model="auditActor" class="h-9 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm outline-none focus:border-indigo-400" placeholder="搜索操作者" @keyup.enter="loadAuditLogs" />
          </div>
          <select v-model="auditResourceType" class="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs font-semibold text-slate-700" @change="loadAuditLogs">
            <option value="">资源</option>
            <option value="user">用户</option>
            <option value="knowledge_job">入库任务</option>
            <option value="knowledge_document">文档</option>
          </select>
          <select v-model="auditStatus" class="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs font-semibold text-slate-700" @change="loadAuditLogs">
            <option value="">状态</option>
            <option value="succeeded">成功</option>
            <option value="failed">失败</option>
          </select>
        </div>

        <select v-model="auditAction" class="h-9 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs font-semibold text-slate-700" @change="loadAuditLogs">
          <option value="">全部操作</option>
          <option value="user.create">创建用户</option>
          <option value="user.update">修改用户</option>
          <option value="knowledge.upload">上传文档</option>
          <option value="knowledge.delete">删除文档</option>
          <option value="knowledge.reindex">重建索引</option>
        </select>

        <div class="text-xs font-bold text-slate-400">共 {{ auditTotal }} 条审计记录</div>

        <article v-for="log in auditLogs" :key="log.id" class="rounded-lg border border-slate-200 bg-white p-4">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="text-sm font-bold text-slate-900">{{ actionLabel(log.action) }}</div>
              <div class="mt-1 truncate text-xs text-slate-500">
                {{ log.actor_username || '-' }} · {{ log.resource_type }} · {{ log.resource_name || log.resource_id || '-' }}
              </div>
            </div>
            <span class="shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-bold" :class="statusBadgeClass(log.status)">
              {{ log.status === 'succeeded' ? '成功' : '失败' }}
            </span>
          </div>
          <div class="mt-3 text-[11px] text-slate-400">时间：{{ formatTime(log.created_at) }} · IP：{{ log.ip_address || '-' }}</div>
          <pre v-if="auditDetail(log)" class="mt-3 max-h-28 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-[11px] text-slate-600">{{ auditDetail(log) }}</pre>
        </article>
      </section>
    </div>
  </div>
</template>
