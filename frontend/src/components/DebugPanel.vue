<script setup lang="ts">
import { computed } from 'vue'
import Icon from './Icon.vue'

interface ChatPanelExposed {
  messages: any[]
  latestAssistant: any
  selectedSource: any
  currentAssistantMessage: any
}

const props = defineProps<{
  chatPanel: ChatPanelExposed | null
}>()

const selectedSource = computed(() => props.chatPanel?.selectedSource || null)
const currentAssistant = computed(() => props.chatPanel?.currentAssistantMessage || null)
const latestTrace = computed(() => currentAssistant.value?.trace || null)
const latestRoute = computed(() => currentAssistant.value?.route || null)

function sourceTitle(source: any) {
  return source?.parent_section_title || source?.file_name || source?.parent_source_path || source?.source_path || '未知来源'
}

function intentLabel(intent: string) {
  const map: Record<string, string> = {
    chitchat: '闲聊',
    knowledge_base: '知识库检索',
    multi_step: '多步知识库检索',
    web_search: '联网搜索',
    sql_query: '结构化查询',
  }
  return map[intent] || intent || '-'
}
</script>

<template>
  <aside class="hidden xl:flex min-h-0 bg-gradient-slate flex-col">
    <div class="h-16 px-5 border-b border-slate-100 bg-white/80 backdrop-blur-sm flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="h-8 w-8 rounded-xl bg-gradient-primary flex items-center justify-center shadow-lg shadow-indigo-200/50">
          <Icon name="text-search" class="h-4 w-4 text-white" />
        </div>
        <div>
          <h2 class="text-sm font-bold text-slate-800">链路调试</h2>
          <p class="text-[10px] text-slate-400">Debug Panel</p>
        </div>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto p-5 space-y-5">
      <!-- Trace -->
      <section class="rounded-2xl border border-slate-150 bg-white p-5 shadow-sm">
        <div class="mb-4 flex items-center gap-2 text-sm font-bold text-slate-800">
          <div class="h-5 w-5 rounded-lg bg-indigo-100 flex items-center justify-center">
            <Icon name="workflow" class="h-3.5 w-3.5 text-indigo-600" />
          </div>
          Agentic Trace
        </div>
        
        <div v-if="!latestTrace" class="text-sm text-slate-400 py-6 text-center">
          <div class="h-12 w-12 rounded-xl bg-slate-100 flex items-center justify-center mx-auto mb-3">
            <Icon name="search" class="h-6 w-6 text-slate-400" />
          </div>
          发送问题后，这里会展示完整的运行过程
        </div>
        
        <div v-else class="space-y-3">
          <!-- Route -->
          <div class="rounded-xl border border-slate-100 bg-gradient-to-br from-slate-50 to-white p-4">
            <div class="flex items-center gap-2 text-xs font-bold text-indigo-600 mb-3">
              <Icon name="route" class="h-3 w-3" />
              Route
            </div>
            <div class="grid grid-cols-2 gap-3 text-xs">
              <div class="rounded-lg bg-white border border-slate-100 p-2">
                <span class="text-slate-400">意图</span>
                <div class="font-semibold text-slate-700">{{ intentLabel(latestRoute?.intent) }}</div>
              </div>
              <div class="rounded-lg bg-white border border-slate-100 p-2">
                <span class="text-slate-400">方法</span>
                <div class="font-mono text-slate-700">{{ latestRoute?.method || '-' }}</div>
              </div>
            </div>
            <div v-if="latestRoute?.reason" class="mt-3 rounded-lg bg-indigo-50/50 border border-indigo-100 p-2 text-xs text-slate-600">
              {{ latestRoute.reason }}
            </div>
          </div>
          
          <!-- Query -->
          <div v-if="latestTrace.original_query" class="rounded-xl border border-slate-100 bg-gradient-to-br from-slate-50 to-white p-4">
            <div class="flex items-center gap-2 text-xs font-bold text-slate-600 mb-2">
              <Icon name="message-circle" class="h-3 w-3" />
              Original Query
            </div>
            <div class="text-xs text-slate-700 break-words font-medium">{{ latestTrace.original_query }}</div>
          </div>
          
          <!-- Retrieval Query -->
          <div v-if="latestTrace.retrieval_query" class="rounded-xl border border-slate-100 bg-gradient-to-br from-slate-50 to-white p-4">
            <div class="flex items-center gap-2 text-xs font-bold text-slate-600 mb-2">
              <Icon name="search" class="h-3 w-3" />
              Retrieval Query
            </div>
            <div class="text-xs text-slate-700 break-words font-medium">{{ latestTrace.retrieval_query }}</div>
          </div>
          
          <!-- Quality Check -->
          <div v-if="latestTrace.quality" class="rounded-xl border border-slate-100 bg-gradient-to-br from-slate-50 to-white p-4">
            <div class="flex items-center gap-2 text-xs font-bold text-slate-600 mb-3">
              <Icon name="shield-check" class="h-3 w-3" />
              Quality Check
            </div>
            <div class="flex items-center gap-3">
              <span class="text-xs text-slate-400">结果：</span>
              <span 
                class="px-2.5 py-1 rounded-lg text-xs font-bold"
                :class="{
                  'bg-emerald-100 text-emerald-700': latestTrace.quality?.quality === 'good',
                  'bg-amber-100 text-amber-700': latestTrace.quality?.quality === 'bad',
                  'bg-slate-100 text-slate-600': !['good', 'bad'].includes(latestTrace.quality?.quality)
                }"
              >
                {{ latestTrace.quality?.quality === 'good' ? 'PASS' : latestTrace.quality?.quality === 'bad' ? 'FAIL' : latestTrace.quality?.quality || '-' }}
              </span>
            </div>
            <div v-if="latestTrace.quality?.reason" class="mt-2 text-xs text-slate-500">
              {{ latestTrace.quality.reason }}
            </div>
          </div>
          
          <!-- Retry Count -->
          <div v-if="latestTrace.retry_count > 0" class="rounded-xl border border-amber-200 bg-gradient-to-br from-amber-50 to-amber-100/50 p-4">
            <div class="flex items-center gap-2 text-xs font-bold text-amber-700">
              <Icon name="refresh-cw" class="h-3 w-3" />
              Retry Count: {{ latestTrace.retry_count }}
            </div>
          </div>
          
          <!-- Timings -->
          <div v-if="latestTrace.timings" class="rounded-xl border border-slate-100 bg-gradient-to-br from-slate-50 to-white p-4">
            <div class="flex items-center gap-2 text-xs font-bold text-slate-600 mb-3">
              <Icon name="clock" class="h-3 w-3" />
              Performance
            </div>
            <div class="grid grid-cols-3 gap-2">
              <div v-if="latestTrace.timings.router_ms" class="rounded-lg bg-white border border-slate-100 p-2.5 text-center">
                <div class="text-[10px] text-slate-400">Router</div>
                <div class="text-sm font-bold text-indigo-600">{{ latestTrace.timings.router_ms }}<span class="text-[10px] font-normal text-slate-400 ml-0.5">ms</span></div>
              </div>
              <div v-if="latestTrace.timings.retrieval_ms" class="rounded-lg bg-white border border-slate-100 p-2.5 text-center">
                <div class="text-[10px] text-slate-400">Retrieval</div>
                <div class="text-sm font-bold text-emerald-600">{{ latestTrace.timings.retrieval_ms }}<span class="text-[10px] font-normal text-slate-400 ml-0.5">ms</span></div>
              </div>
              <div v-if="latestTrace.timings.generation_ms" class="rounded-lg bg-white border border-slate-100 p-2.5 text-center">
                <div class="text-[10px] text-slate-400">Generation</div>
                <div class="text-sm font-bold text-purple-600">{{ latestTrace.timings.generation_ms }}<span class="text-[10px] font-normal text-slate-400 ml-0.5">ms</span></div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- Source Detail -->
      <section class="rounded-2xl border border-slate-150 bg-white p-5 shadow-sm">
        <div class="mb-4 flex items-center justify-between gap-2">
          <div class="flex items-center gap-2 text-sm font-bold text-slate-800">
            <div class="h-5 w-5 rounded-lg bg-indigo-100 flex items-center justify-center">
              <Icon name="file-search" class="h-3.5 w-3.5 text-indigo-600" />
            </div>
            Source Detail
          </div>
          <span v-if="selectedSource" class="rounded-lg bg-indigo-100 px-2.5 py-1 text-[10px] font-bold text-indigo-700">
            {{ selectedSource.retrieval_mode || 'chunk' }}
          </span>
        </div>

        <div v-if="!selectedSource" class="text-sm text-slate-400 py-6 text-center">
          <div class="h-12 w-12 rounded-xl bg-slate-100 flex items-center justify-center mx-auto mb-3">
            <Icon name="file-text" class="h-6 w-6 text-slate-400" />
          </div>
          点击回答下方的来源片段查看详情
        </div>

        <div v-else class="space-y-4">
          <div class="rounded-xl border border-slate-100 bg-gradient-to-br from-slate-50 to-white p-4">
            <div class="text-[10px] font-bold text-slate-400 uppercase tracking-wide">Document</div>
            <div class="mt-1.5 text-sm font-bold leading-relaxed break-words text-slate-700">{{ sourceTitle(selectedSource) }}</div>
          </div>
          
          <div class="grid grid-cols-2 gap-3">
            <div class="rounded-xl border border-indigo-100 bg-gradient-to-br from-indigo-50/50 to-white p-4">
              <div class="text-[10px] font-bold text-slate-400 uppercase tracking-wide">Score</div>
              <div class="mt-1.5 text-xl font-bold text-indigo-600">{{ Number(selectedSource.score || 0).toFixed(4) }}</div>
            </div>
            <div class="rounded-xl border border-slate-100 bg-gradient-to-br from-slate-50/50 to-white p-4">
              <div class="text-[10px] font-bold text-slate-400 uppercase tracking-wide">Page</div>
              <div class="mt-1.5 text-xl font-bold text-slate-600">{{ selectedSource.parent_page_range || '-' }}</div>
            </div>
          </div>
          
          <div class="rounded-xl border border-slate-100 bg-gradient-to-br from-slate-50 to-white p-4">
            <div class="flex items-center gap-2 text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-3">
              <Icon name="quote" class="h-3 w-3" />
              Retrieved Text
            </div>
            <div class="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg border border-slate-100 bg-white p-3.5 text-xs leading-relaxed text-slate-700">{{ selectedSource.text }}</div>
          </div>
          
          <div class="rounded-xl border border-slate-100 bg-gradient-to-br from-slate-50/50 to-white p-4">
            <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-3">Metadata</div>
            <dl class="space-y-2.5 text-[11px]">
              <div class="flex justify-between items-start">
                <dt class="font-bold text-slate-400 w-16 shrink-0">Parent ID</dt>
                <dd class="break-all font-mono text-slate-600 flex-1 text-right">{{ selectedSource.parent_id || '-' }}</dd>
              </div>
              <div class="flex justify-between items-start">
                <dt class="font-bold text-slate-400 w-16 shrink-0">Chunk ID</dt>
                <dd class="break-all font-mono text-slate-600 flex-1 text-right">{{ selectedSource.child_chunk_id || selectedSource.chunk_id || '-' }}</dd>
              </div>
              <div class="flex justify-between items-start">
                <dt class="font-bold text-slate-400 w-16 shrink-0">File Path</dt>
                <dd class="break-all font-mono text-slate-600 flex-1 text-right">{{ selectedSource.parent_source_path || selectedSource.source_path || '-' }}</dd>
              </div>
            </dl>
          </div>
        </div>
      </section>
    </div>
  </aside>
</template>
