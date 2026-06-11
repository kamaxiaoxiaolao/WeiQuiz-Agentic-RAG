<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  Archive,
  CloudUpload,
  FileText,
  Loader2,
  RefreshCw,
  Search,
  Send,
  Trash2,
} from 'lucide-vue-next'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

type KnowledgeDocument = {
  id: string
  relative_path: string
  filename: string
  title: string
  file_type: string
  file_size: number
  updated_at: string
  status: string
  indexed_at?: string
  chunk_count: number
}

type IngestJob = {
  job_id: string
  status: string
  created_at?: number
  updated_at?: number
  saved_files?: Array<{ file_name: string; file_size: number }>
  result?: { changed?: { added?: number; updated?: number; deleted?: number } }
  error?: string
}

const auth = useAuthStore()
const selectedFiles = ref<File[]>([])
const dragging = ref(false)
const isUploading = ref(false)
const isLoadingLibrary = ref(false)
const isReindexing = ref(false)
const deletingFile = ref('')
const uploadStatus = ref({ ok: true, message: '', detail: '' })
const activeJob = ref<IngestJob | null>(null)
const jobs = ref<IngestJob[]>([])
const documents = ref<KnowledgeDocument[]>([])
const stats = ref({
  total_documents: 0,
  indexed_documents: 0,
  pending_documents: 0,
  total_size: 0,
  supported_types: [] as string[],
})
const keyword = ref('')
let jobTimer: ReturnType<typeof setInterval> | null = null

const canManageKnowledge = computed(() => auth.isAdmin)

const filteredDocuments = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return documents.value
  return documents.value.filter((doc) => {
    return doc.filename.toLowerCase().includes(q) || doc.file_type.toLowerCase().includes(q)
  })
})

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  selectedFiles.value = Array.from(input.files || [])
  uploadStatus.value = { ok: true, message: '', detail: '' }
}

function onDrop(event: DragEvent) {
  dragging.value = false
  selectedFiles.value = Array.from(event.dataTransfer?.files || [])
  uploadStatus.value = { ok: true, message: '', detail: '' }
}

function clearFiles() {
  selectedFiles.value = []
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  if (input) input.value = ''
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

async function refreshLibrary() {
  isLoadingLibrary.value = true
  try {
    const { data } = await api.get('/documents/library')
    documents.value = data.documents || []
    stats.value = data.stats || stats.value
    jobs.value = data.jobs || []
  } finally {
    isLoadingLibrary.value = false
  }
}

async function uploadDocuments() {
  if (!canManageKnowledge.value || !selectedFiles.value.length || isUploading.value) return
  isUploading.value = true
  uploadStatus.value = { ok: true, message: '正在上传并入库...', detail: '' }

  const formData = new FormData()
  selectedFiles.value.forEach((file) => formData.append('files', file))

  try {
    const { data } = await api.post('/documents/upload', formData)
    const job = { job_id: data.job_id, status: data.status || 'pending', saved_files: data.saved_files || [] }
    activeJob.value = job
    updateJob(job)
    selectedFiles.value = []
    uploadStatus.value = { ok: true, message: '上传任务已提交', detail: `Job ID: ${data.job_id}` }
    pollJob(data.job_id)
  } catch (e: any) {
    uploadStatus.value = { ok: false, message: '上传失败', detail: e?.response?.data?.detail || e?.message || String(e) }
    isUploading.value = false
  }
}

async function reindexDocuments() {
  if (!canManageKnowledge.value || isReindexing.value) return
  isReindexing.value = true
  try {
    const { data } = await api.post('/documents/reindex')
    activeJob.value = { job_id: data.job_id, status: data.status || 'pending' }
    updateJob(activeJob.value)
    pollJob(data.job_id)
  } finally {
    isReindexing.value = false
  }
}

async function deleteDocument(doc: KnowledgeDocument) {
  if (!canManageKnowledge.value || deletingFile.value) return
  const documentPath = doc.relative_path || doc.id || doc.filename
  deletingFile.value = documentPath
  try {
    const { data } = await api.delete(`/documents/files/${encodeURIComponent(documentPath)}`)
    documents.value = documents.value.filter((item) => (item.relative_path || item.id || item.filename) !== documentPath)
    if (data.reindex_job_id) {
      activeJob.value = { job_id: data.reindex_job_id, status: 'pending' }
      updateJob(activeJob.value)
      pollJob(data.reindex_job_id)
    } else {
      await refreshLibrary()
    }
  } finally {
    deletingFile.value = ''
  }
}

function pollJob(jobId: string) {
  if (jobTimer) clearInterval(jobTimer)
  jobTimer = setInterval(async () => {
    try {
      const { data: job } = await api.get(`/documents/jobs/${jobId}`)
      updateJob(job)
      activeJob.value = job

      if (job.status === 'pending' || job.status === 'running') {
        uploadStatus.value = { ok: true, message: '正在解析、切分并写入索引...', detail: `Status: ${job.status}` }
        return
      }

      clearInterval(jobTimer!)
      jobTimer = null
      isUploading.value = false

      if (job.status === 'succeeded') {
        const changed = job.result?.changed || {}
        uploadStatus.value = {
          ok: true,
          message: '知识库已更新',
          detail: `新增 ${changed.added || 0} / 更新 ${changed.updated || 0} / 删除 ${changed.deleted || 0}`,
        }
        await refreshLibrary()
      } else {
        uploadStatus.value = { ok: false, message: '入库失败', detail: job.error || '未知错误' }
      }
    } catch {
      clearInterval(jobTimer!)
      jobTimer = null
      isUploading.value = false
      uploadStatus.value = { ok: false, message: '查询任务状态失败', detail: '' }
    }
  }, 1500)
}

function updateJob(job: IngestJob | null) {
  if (!job?.job_id) return
  const idx = jobs.value.findIndex((item) => item.job_id === job.job_id)
  if (idx >= 0) jobs.value[idx] = job
  else jobs.value.unshift(job)
}

function jobBadgeClass(status: string) {
  if (status === 'succeeded') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (status === 'failed') return 'border-rose-200 bg-rose-50 text-rose-700'
  if (status === 'running') return 'border-indigo-200 bg-indigo-50 text-indigo-700'
  return 'border-slate-200 bg-slate-50 text-slate-500'
}

function docBadgeClass(status: string) {
  if (status === 'indexed') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  return 'border-amber-200 bg-amber-50 text-amber-700'
}

function formatJobSummary(job: IngestJob) {
  const changed = job.result?.changed
  if (changed) return `新增 ${changed.added || 0} / 更新 ${changed.updated || 0} / 删除 ${changed.deleted || 0}`

  const files = job.saved_files || []
  if (files.length) return `已保存 ${files.length} 个文件`

  return '等待入库'
}

onMounted(refreshLibrary)
</script>

<template>
  <div class="min-h-0 flex flex-col bg-slate-50">
    <div class="border-b border-slate-200 bg-white px-5 py-4">
      <div class="flex items-center justify-between gap-3">
        <div>
          <div class="text-sm font-bold text-slate-900">知识库管理</div>
          <div class="mt-0.5 text-xs text-slate-500">{{ stats.total_documents }} 个文档，{{ formatBytes(stats.total_size) }}</div>
        </div>
        <button
          class="h-9 w-9 rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 disabled:opacity-50 grid place-items-center"
          :disabled="isLoadingLibrary"
          title="刷新"
          @click="refreshLibrary"
        >
          <Loader2 v-if="isLoadingLibrary" class="h-4 w-4 animate-spin" />
          <RefreshCw v-else class="h-4 w-4" />
        </button>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto p-5 space-y-5">
      <section class="grid grid-cols-3 gap-2">
        <div class="rounded-lg border border-slate-200 bg-white p-3">
          <div class="text-[11px] font-bold text-slate-400">总数</div>
          <div class="mt-1 text-lg font-black text-slate-900">{{ stats.total_documents }}</div>
        </div>
        <div class="rounded-lg border border-slate-200 bg-white p-3">
          <div class="text-[11px] font-bold text-slate-400">已索引</div>
          <div class="mt-1 text-lg font-black text-emerald-600">{{ stats.indexed_documents }}</div>
        </div>
        <div class="rounded-lg border border-slate-200 bg-white p-3">
          <div class="text-[11px] font-bold text-slate-400">待处理</div>
          <div class="mt-1 text-lg font-black text-amber-600">{{ stats.pending_documents }}</div>
        </div>
      </section>

      <div v-if="!canManageKnowledge" class="rounded-lg border border-slate-200 bg-white p-4 text-xs leading-relaxed text-slate-500">
        当前账号拥有知识库读取权限。上传、删除、重建索引和任务审计仅管理员可操作。
      </div>

      <label
        v-if="canManageKnowledge"
        class="block cursor-pointer rounded-lg border-2 border-dashed p-5 transition"
        :class="dragging ? 'border-indigo-500 bg-indigo-50' : 'border-slate-300 bg-white hover:border-indigo-400'"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <input class="hidden" type="file" multiple accept=".txt,.md,.markdown,.pdf,.docx,.html,.htm" @change="onFileChange" />
        <div class="flex items-center gap-3">
          <div class="h-11 w-11 rounded-lg bg-indigo-50 text-indigo-600 grid place-items-center">
            <CloudUpload class="h-5 w-5" />
          </div>
          <div class="min-w-0">
            <div class="text-sm font-bold text-slate-800">上传文档</div>
            <div class="mt-1 text-xs text-slate-500">PDF、DOCX、HTML、Markdown、TXT</div>
          </div>
        </div>
      </label>

      <section v-if="canManageKnowledge && selectedFiles.length" class="space-y-3">
        <div class="flex items-center justify-between">
          <span class="text-xs font-bold text-slate-500">已选择 {{ selectedFiles.length }} 个文件</span>
          <button class="text-xs font-semibold text-rose-600" @click="clearFiles">清空</button>
        </div>
        <div class="space-y-2 max-h-36 overflow-y-auto">
          <div v-for="file in selectedFiles" :key="file.name + file.size" class="rounded-lg border border-slate-200 bg-white px-3 py-2">
            <div class="truncate text-xs font-semibold text-slate-700">{{ file.name }}</div>
            <div class="mt-0.5 text-[11px] text-slate-400">{{ formatBytes(file.size) }}</div>
          </div>
        </div>
      </section>

      <div v-if="canManageKnowledge" class="grid grid-cols-2 gap-2">
        <button
          class="h-10 rounded-lg bg-indigo-600 text-white text-sm font-bold hover:bg-indigo-700 disabled:opacity-50 inline-flex items-center justify-center gap-2"
          :disabled="isUploading || !selectedFiles.length"
          @click="uploadDocuments"
        >
          <Loader2 v-if="isUploading" class="h-4 w-4 animate-spin" />
          <Send v-else class="h-4 w-4" />
          {{ isUploading ? '入库中' : '上传入库' }}
        </button>
        <button
          class="h-10 rounded-lg border border-slate-200 bg-white text-sm font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50 inline-flex items-center justify-center gap-2"
          :disabled="isReindexing"
          @click="reindexDocuments"
        >
          <Loader2 v-if="isReindexing" class="h-4 w-4 animate-spin" />
          <RefreshCw v-else class="h-4 w-4" />
          重建索引
        </button>
      </div>

      <section v-if="canManageKnowledge && (activeJob || uploadStatus.message)" class="rounded-lg border border-slate-200 bg-white p-4">
        <div class="flex items-center justify-between gap-2">
          <div class="text-sm font-bold" :class="uploadStatus.ok ? 'text-slate-800' : 'text-rose-600'">
            {{ uploadStatus.message || '入库任务' }}
          </div>
          <span v-if="activeJob?.status" class="rounded-full border px-2 py-0.5 text-[10px] font-bold" :class="jobBadgeClass(activeJob.status)">
            {{ activeJob.status }}
          </span>
        </div>
        <div v-if="activeJob?.job_id" class="mt-2 truncate text-xs text-slate-400 font-mono">{{ activeJob.job_id }}</div>
        <pre v-if="uploadStatus.detail" class="mt-3 max-h-28 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-[11px] text-slate-600">{{ uploadStatus.detail }}</pre>
      </section>

      <section class="space-y-3">
        <div class="flex items-center justify-between">
          <h3 class="text-xs font-bold text-slate-500">文档列表</h3>
          <span class="text-xs text-slate-400">{{ filteredDocuments.length }}</span>
        </div>
        <div class="relative">
          <Search class="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
          <input
            v-model="keyword"
            class="h-9 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm outline-none focus:border-indigo-400"
            placeholder="搜索文件名或类型"
          />
        </div>
        <div v-if="!filteredDocuments.length" class="rounded-lg border border-slate-200 bg-white p-5 text-center text-sm text-slate-400">
          暂无文档
        </div>
        <div v-else class="space-y-2">
          <article v-for="doc in filteredDocuments" :key="doc.id" class="rounded-lg border border-slate-200 bg-white p-3">
            <div class="flex items-start gap-3">
              <div class="mt-0.5 h-9 w-9 shrink-0 rounded-lg bg-slate-100 text-slate-600 grid place-items-center">
                <FileText class="h-4 w-4" />
              </div>
              <div class="min-w-0 flex-1">
                <div class="truncate text-sm font-bold text-slate-800" :title="doc.filename">{{ doc.filename }}</div>
                <div class="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                  <span>{{ doc.file_type.toUpperCase() }}</span>
                  <span>{{ formatBytes(doc.file_size) }}</span>
                  <span>{{ formatTime(doc.updated_at) }}</span>
                </div>
                <div class="mt-2 flex items-center justify-between gap-2">
                  <span class="rounded-full border px-2 py-0.5 text-[10px] font-bold" :class="docBadgeClass(doc.status)">
                    {{ doc.status === 'indexed' ? '已索引' : '待索引' }}
                  </span>
                  <button
                    v-if="canManageKnowledge"
                    class="h-8 w-8 rounded-lg border border-slate-200 text-slate-500 hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50 grid place-items-center"
                    :disabled="deletingFile === (doc.relative_path || doc.id || doc.filename)"
                    title="删除并重建索引"
                    @click="deleteDocument(doc)"
                  >
                    <Loader2 v-if="deletingFile === (doc.relative_path || doc.id || doc.filename)" class="h-4 w-4 animate-spin" />
                    <Trash2 v-else class="h-4 w-4" />
                  </button>
                </div>
              </div>
            </div>
          </article>
        </div>
      </section>

      <section v-if="canManageKnowledge" class="rounded-lg border border-slate-200 bg-white overflow-hidden">
        <div class="h-11 px-4 border-b border-slate-100 bg-slate-50 flex items-center justify-between">
          <h3 class="text-xs font-bold text-slate-600 inline-flex items-center gap-2">
            <Archive class="h-4 w-4" />
            入库任务
          </h3>
          <span class="text-xs text-slate-500">{{ jobs.length }}</span>
        </div>
        <div v-if="!jobs.length" class="p-5 text-center text-sm text-slate-400">暂无入库任务</div>
        <button v-for="job in jobs" :key="job.job_id" class="w-full border-t border-slate-100 px-4 py-3 text-left hover:bg-indigo-50" @click="activeJob = job">
          <div class="flex items-center justify-between gap-2">
            <span class="truncate text-[11px] font-mono text-slate-500">{{ job.job_id }}</span>
            <span class="rounded-full border px-2 text-[10px] font-bold" :class="jobBadgeClass(job.status)">{{ job.status }}</span>
          </div>
          <div class="mt-1 text-xs text-slate-700">{{ formatJobSummary(job) }}</div>
          <div class="mt-1 text-[11px] text-slate-400">{{ formatTime(job.created_at || job.updated_at) }}</div>
        </button>
      </section>
    </div>
  </div>
</template>
