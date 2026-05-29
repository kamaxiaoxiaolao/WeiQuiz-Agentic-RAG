<script setup lang="ts">
import { ref } from 'vue'
import { CloudUpload, Loader2, Send } from 'lucide-vue-next'
import api from '@/api'

const selectedFiles = ref<File[]>([])
const dragging = ref(false)
const isUploading = ref(false)
const uploadStatus = ref({ ok: true, message: '', detail: '' })
const activeJob = ref<any>({})
const jobs = ref<any[]>([])
let jobTimer: ReturnType<typeof setInterval> | null = null

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

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

async function uploadDocuments() {
  if (!selectedFiles.value.length || isUploading.value) return
  isUploading.value = true
  uploadStatus.value = { ok: true, message: '正在上传并入库...', detail: '' }

  const formData = new FormData()
  selectedFiles.value.forEach((file) => formData.append('files', file))

  try {
    const { data } = await api.post('/documents/upload', formData)
    activeJob.value = { job_id: data.job_id, status: data.status || 'pending', saved_files: data.saved_files || [] }
    jobs.value.unshift(activeJob.value)
    selectedFiles.value = []
    uploadStatus.value = { ok: true, message: '上传任务已提交', detail: `Job ID: ${data.job_id}` }
    pollJob(data.job_id)
  } catch (e: any) {
    uploadStatus.value = { ok: false, message: '上传失败', detail: e?.message || String(e) }
    isUploading.value = false
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
        const summary = job.result?.report?.summary || {}
        uploadStatus.value = {
          ok: true,
          message: '入库完成',
          detail: `added=${summary.added || 0}, updated=${summary.updated || 0}, deleted=${summary.deleted || 0}`,
        }
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

function updateJob(job: any) {
  const idx = jobs.value.findIndex((j) => j.job_id === job.job_id)
  if (idx >= 0) jobs.value[idx] = job
  else jobs.value.unshift(job)
}

function jobBadgeClass(status: string) {
  if (status === 'succeeded') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (status === 'failed') return 'border-rose-200 bg-rose-50 text-rose-700'
  if (status === 'running') return 'border-indigo-200 bg-indigo-50 text-indigo-700'
  return 'border-slate-200 bg-slate-50 text-slate-500'
}

function formatJobSummary(job: any) {
  const changed = job.result?.changed
  if (changed) return `新增 ${changed.added || 0} / 更新 ${changed.updated || 0} / 删除 ${changed.deleted || 0}`

  const files = job.saved_files || []
  if (files.length) return `已保存 ${files.length} 个文件`

  return '等待入库...'
}
</script>

<template>
  <div class="min-h-0 flex flex-col bg-slate-50">
    <div class="min-h-0 flex-1 overflow-y-auto p-5 space-y-5">
      <label
        class="block cursor-pointer rounded-xl border-2 border-dashed p-5 transition"
        :class="dragging ? 'border-indigo-500 bg-indigo-50' : 'border-slate-300 bg-white hover:border-indigo-400'"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <input class="hidden" type="file" multiple accept=".txt,.md,.markdown,.pdf,.docx,.html,.htm" @change="onFileChange" />
        <div class="flex flex-col items-center text-center gap-3">
          <div class="h-12 w-12 rounded-full bg-indigo-50 text-indigo-600 grid place-items-center">
            <CloudUpload class="h-6 w-6" />
          </div>
          <div>
            <div class="text-sm font-semibold">上传知识库文档</div>
            <div class="mt-1 text-xs text-slate-500">支持 PDF、DOCX、HTML、Markdown、TXT</div>
          </div>
        </div>
      </label>

      <section v-if="selectedFiles.length" class="space-y-3">
        <div class="flex items-center justify-between">
          <span class="text-xs font-bold text-slate-500">已选择 {{ selectedFiles.length }} 个文件</span>
          <button class="text-xs font-semibold text-rose-600" @click="clearFiles">清空</button>
        </div>
        <div class="space-y-2 max-h-44 overflow-y-auto">
          <div v-for="file in selectedFiles" :key="file.name + file.size" class="rounded-lg border border-slate-200 bg-white px-3 py-2">
            <div class="text-xs font-semibold text-slate-700 truncate">{{ file.name }}</div>
            <div class="mt-0.5 text-[11px] text-slate-400">{{ formatBytes(file.size) }}</div>
          </div>
        </div>
      </section>

      <button
        class="h-11 w-full rounded-xl bg-indigo-600 text-white text-sm font-bold hover:bg-indigo-700 disabled:opacity-50 inline-flex items-center justify-center gap-2"
        :disabled="isUploading || !selectedFiles.length"
        @click="uploadDocuments"
      >
        <Loader2 v-if="isUploading" class="h-4 w-4 animate-spin" />
        <Send v-else class="h-4 w-4" />
        {{ isUploading ? '正在入库...' : '上传并入库' }}
      </button>

      <section v-if="activeJob.job_id || uploadStatus.message" class="rounded-xl border border-slate-200 bg-white p-4">
        <div class="flex items-center justify-between gap-2">
          <div class="text-sm font-bold" :class="uploadStatus.ok ? 'text-slate-800' : 'text-rose-600'">
            {{ uploadStatus.message || '入库任务' }}
          </div>
          <span v-if="activeJob.status" class="rounded-full border px-2 py-0.5 text-[10px] font-bold" :class="jobBadgeClass(activeJob.status)">
            {{ activeJob.status }}
          </span>
        </div>
        <div v-if="activeJob.job_id" class="mt-2 text-xs text-slate-400 font-mono">{{ activeJob.job_id }}</div>
        <pre v-if="uploadStatus.detail" class="mt-3 max-h-36 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-[11px] text-slate-600">{{ uploadStatus.detail }}</pre>
      </section>

      <section class="rounded-xl border border-slate-200 bg-white overflow-hidden">
        <div class="h-11 px-4 border-b border-slate-100 bg-slate-50 flex items-center justify-between">
          <h3 class="text-xs font-bold text-slate-600">入库任务记录</h3>
          <span class="text-xs text-slate-500">{{ jobs.length }}</span>
        </div>
        <div v-if="!jobs.length" class="p-5 text-center text-sm text-slate-400">暂无入库任务</div>
        <button v-for="job in jobs" :key="job.job_id" class="w-full border-t border-slate-100 px-4 py-3 text-left hover:bg-indigo-50" @click="activeJob = job">
          <div class="flex items-center justify-between gap-2">
            <span class="truncate text-[11px] font-mono text-slate-500">{{ job.job_id }}</span>
            <span class="rounded-full border px-2 text-[10px] font-bold" :class="jobBadgeClass(job.status)">{{ job.status }}</span>
          </div>
          <div class="mt-1 text-xs text-slate-700">{{ formatJobSummary(job) }}</div>
        </button>
      </section>
    </div>
  </div>
</template>
