<script setup>
import { onMounted, onUnmounted } from 'vue'
import { useLibraryStore } from './stores/library'

const store = useLibraryStore()

onMounted(() => {
  store.refreshDashboard()
  store.connectWS()
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
        <router-link to="/admin">管理员</router-link>
        <router-link to="/purchaser">采购员</router-link>
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
  </div>
</template>
