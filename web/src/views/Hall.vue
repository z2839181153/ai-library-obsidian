<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useLibraryStore } from '../stores/library'
import { api } from '../api'

const store = useLibraryStore()
const router = useRouter()
const uploading = ref(false)
const uploadMsg = ref('')

const d = computed(() => store.dashboard || {})
const health = computed(() => d.value.health || {})
const quota = computed(() => health.value.quota || {})
const weekMax = computed(() => Math.max(1, ...(health.value.week || []).map((w) => w.count)))

onMounted(() => store.refreshDashboard())

function onFile(e) {
  const file = e.target.files?.[0]
  if (!file) return
  upload(file)
}
function onDrop(e) {
  e.preventDefault()
  const file = e.dataTransfer?.files?.[0]
  if (file) upload(file)
}
async function upload(file) {
  uploading.value = true
  uploadMsg.value = ''
  const fd = new FormData()
  fd.append('file', file)
  try {
    const r = await api.upload('/api/ingest', fd)
    if (r.created) {
      uploadMsg.value = `✅ 已入馆《${r.book.title}》，进入补书室待分类`
      store.refreshDashboard()
    } else if (r.duplicate) {
      uploadMsg.value = `📚 《${r.book.title}》已在馆内（内容重复）`
    }
  } catch (err) {
    uploadMsg.value = `❌ ${err.message}`
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <div>
    <h1 class="page-title">🏛 大厅</h1>
    <p class="page-sub">图书馆健康度与快捷入口 · {{ d.today }}</p>

    <!-- 健康度仪表 -->
    <div class="grid grid-4 mb16">
      <div class="card">
        <div class="muted">待分类书</div>
        <div style="font-size:2em;font-weight:700">{{ health.pending_classify ?? 0 }}</div>
        <div class="muted">补书室（建议 {{ health.reviewing_books ?? 0 }} + 待定 {{ health.incoming_books ?? 0 }}）</div>
      </div>
      <div class="card">
        <div class="muted">待审阅技能</div>
        <div style="font-size:2em;font-weight:700">{{ health.skills_reviewing ?? 0 }}</div>
        <div class="muted" :class="{ 'badge red': health.skills_blocked }">阻塞 {{ health.skills_blocked ?? 0 }}</div>
      </div>
      <div class="card">
        <div class="muted">索引</div>
        <div style="font-size:1.6em;font-weight:700">{{ health.index?.status || 'missing' }}</div>
        <div class="muted">revision {{ health.index?.revision ?? 0 }} · {{ health.index?.total_chunks ?? 0 }} chunks</div>
      </div>
      <div class="card">
        <div class="muted">今日采购执行率</div>
        <div style="font-size:2em;font-weight:700">{{ quota.execution_rate ?? 0 }}%</div>
        <div class="muted">共 {{ quota.total ?? 0 }} 条 · 已收 {{ quota.collected ?? 0 }}</div>
      </div>
    </div>

    <!-- 本周趋势 + 楼层鸟瞰 + 快捷操作 -->
    <div class="grid" style="grid-template-columns: 1.2fr 1fr; align-items:start">
      <div class="card">
        <h3 style="margin:0 0 10px">📈 本周入馆趋势</h3>
        <div class="row" style="gap:6px;align-items:flex-end;min-height:110px">
          <div v-for="w in (health.week || [])" :key="w.date" style="flex:1;text-align:center">
            <div style="font-size:0.75em;color:var(--ink-soft)">{{ w.count }}</div>
            <div :style="{ height: Math.max(6, 70 * w.count / weekMax) + 'px', background: 'var(--accent)', borderRadius: '4px 4px 0 0' }"></div>
            <div style="font-size:0.7em;color:var(--ink-soft)">{{ w.date.slice(5) }}</div>
          </div>
        </div>
      </div>

      <div class="card">
        <h3 style="margin:0 0 10px">🏢 楼层鸟瞰</h3>
        <div class="floor-list">
          <div v-for="f in (d.floors || [])" :key="f.floor_id" class="floor-item" @click="router.push('/floors')">
            <span style="font-weight:700">{{ f.code }}</span>
            <span class="grow">{{ f.name }}</span>
            <span class="badge">{{ f.book_count }} 本</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 快捷操作 -->
    <div class="card mt16">
      <h3 style="margin:0 0 10px">⚡ 快捷操作</h3>
      <div class="row wrap">
        <label class="primary" style="padding:8px 16px;border-radius:8px;cursor:pointer">
          📥 扔书入馆
          <input type="file" style="display:none" @change="onFile" accept=".md,.markdown,.txt,.html,.htm,.pdf" />
        </label>
        <button @click="router.push('/floors')">📚 打开补书室</button>
        <button @click="router.push('/admin')">💬 去提问</button>
        <button @click="router.push('/purchaser')">🛒 采购员</button>
      </div>
      <div
        class="mt8"
        style="border:2px dashed var(--border);border-radius:10px;padding:18px;text-align:center;color:var(--ink-soft)"
        @dragover.prevent @drop="onDrop"
      >
        或把文件拖到这里（md / txt / html / pdf）
      </div>
      <div v-if="uploading" class="mt8">上传中…</div>
      <div v-if="uploadMsg" class="mt8" style="color:var(--accent)">{{ uploadMsg }}</div>
    </div>

    <!-- 今日日报 -->
    <div class="card mt16">
      <h3 style="margin:0 0 10px">📋 今日日报</h3>
      <div v-if="!(d.reports || []).length" class="empty">今天还没有日报</div>
      <div v-else class="row wrap">
        <div v-for="rep in (d.reports || [])" :key="rep.report_id" class="badge" style="padding:6px 12px">
          {{ rep.rtype }} · {{ rep.date }}
        </div>
      </div>
    </div>
  </div>
</template>
