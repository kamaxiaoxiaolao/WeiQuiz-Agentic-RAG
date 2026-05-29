<script setup lang="ts">
import { computed } from 'vue'
import {
  BotMessageSquare,
  Lightbulb,
  Trash2,
  Search,
  User,
  Cpu,
  Workflow,
  BadgeCheck,
  ExternalLink,
  Quote,
  ArrowUp,
  TextSearch,
  Route,
  MessageCircle,
  ShieldCheck,
  RefreshCw,
  Clock,
  FileSearch,
  FileText,
  Network,
  LogOut,
  MessageSquare,
  Plus,
  CheckCircle2,
  CircleDot,
  AlertCircle,
  Loader2,
  Pencil,
  Check,
  X
} from 'lucide-vue-next'

const props = defineProps<{
  name: string
}>()

const iconMap: Record<string, any> = {
  'bot-message-square': BotMessageSquare,
  'lightbulb': Lightbulb,
  'trash-2': Trash2,
  'search': Search,
  'user': User,
  'cpu': Cpu,
  'workflow': Workflow,
  'badge-check': BadgeCheck,
  'external-link': ExternalLink,
  'quote': Quote,
  'arrow-up': ArrowUp,
  'text-search': TextSearch,
  'route': Route,
  'message-circle': MessageCircle,
  'shield-check': ShieldCheck,
  'refresh-cw': RefreshCw,
  'clock': Clock,
  'file-search': FileSearch,
  'file-text': FileText,
  'network': Network,
  'log-out': LogOut,
  'message-square': MessageSquare,
  'plus': Plus,
  'check-circle-2': CheckCircle2,
  'circle-dot': CircleDot,
  'alert-circle': AlertCircle,
  'loader-2': Loader2,
  'pencil': Pencil,
  'check': Check,
  'x': X
}

const iconComponent = computed(() => {
  return iconMap[props.name]
})
</script>

<template>
  <component v-if="iconComponent" :is="iconComponent" />
</template>
