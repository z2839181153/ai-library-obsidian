<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useLibraryStore } from './stores/library'
import { api } from './api'
import SetupWizard from './components/SetupWizard.vue'

const store = useLibraryStore()

// P6-3 首次启动配置向导：未配置 key 且未启用 Ollama → 自动弹出
const wizardVisible = ref(false)

onMounted(async () => {
  store.refreshDashboard()
  store.connectWS()
  try {
    const d = await api.providers()
    if (d.needs_setup) wizardVisible.value = true
  } catch { /* 后端未就绪，忽略（设置页可手动打开） */ }
})
onUnmounted(() => {
  if (store.ws) store.ws.close()
})
</script>

<template>
  <div class="layout">
    <header class="topbar">
      <div class="brand">🏛 AI 图书馆</div>
      <nav>
        <router-link to="/">大厅</router-link>
        <router-link to="/floors">
          楼层
          <span v-if="store.pendingClassify" class="pending-badge">{{ store.pendingClassify }}</span>
        </router-link>
        <router-link to="/starmap">占星室</router-link>
        <router-link to="/reading">阅览室</router-link>
        <router-link to="/admin">管理员</router-link>
        <router-link to="/purchaser">采购员</router-link>
        <router-link to="/archive">档案馆</router-link>
        <router-link to="/settings">设置</router-link>
      </nav>
      <div class="spacer"></div>
      <div class="row" style="gap:6px">
        <span class="ws-dot" :class="{ on: store.wsConnected }" :title="store.wsConnected ? 'WS 已连接' : 'WS 未连接'"></span>
        <span class="muted" style="color:#dbe5db;font-size:0.8em">{{ store.wsConnected ? '已连接' : '连接中…' }}</span>
      </div>
    </header>

    <main class="content">
      <router-view />
    </main>

    <div class="toast-wrap">
      <div v-for="t in store.toasts" :key="t.id" class="toast" :class="t.type" @click="store.removeToast(t.id)">
        {{ t.text }}
      </div>
    </div>

    <!-- P6-3 首次启动配置向导 -->
    <SetupWizard :visible="wizardVisible" @close="wizardVisible = false" @applied="wizardVisible = false" />
  </div>
</template>
