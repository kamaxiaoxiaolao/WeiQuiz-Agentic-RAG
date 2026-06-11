<script setup lang="ts">
import AppHeader from '@/components/AppHeader.vue'
import AdminPanel from '@/components/AdminPanel.vue'
</script>

<template>
  <div class="h-screen flex flex-col overflow-hidden bg-slate-50 font-sans antialiased">
    <AppHeader class="z-20 border-b border-slate-200 bg-white/90 backdrop-blur-md shadow-sm" />
    <main class="min-h-0 flex-1 overflow-hidden">
      <AdminPanel class="h-full" />
    </main>
  </div>
</template>
