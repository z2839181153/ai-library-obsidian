<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useLibraryStore } from '../stores/library'

const router = useRouter()
const store = useLibraryStore()

const tab = ref('raw')   // raw | deleted | reports | distill | backup
const summary = ref(null)
const rawItems = ref([])
const deletedItems = ref([])
const reports = ref([])
const distillLogs = ref([])
const loading = ref(false)
const error = ref('')
const backupMsg = ref('')
const restoreDays = ref(30)

const TABS = [
  { key: 'raw', label: '🗃 原始副本' },
  { key: 'deleted', label: '🗑 已删除' },
  { key: 'reports', label: '📰 历史日报' },
  { key: 'distill', label: '🔬 蒸馏过程' },
  { key: 'backup', label: '💾 备份导出' },
]

const rawLinked = computed(() => rawItems.value.filter((i) => i.linked).length)
const rawOrphan = computed(() => rawItems.value.length - rawLinked.value)

onMounted(async () => {
  await loadSummary()
  await switchTab(tab.value)
})

async function loadSummary() {
  try {
    summary.value = await api.get('/api/archive/summary')
    restoreDays.value = summary.value?.restore_days ?? 30
  } catch (e) {
    error.value = e.message || '加载失败'
  }
}

async function switchTab(k) {
  tab.value = k
  error.value = ''
  if (k === 'raw') {
    loading.value = true
    try { rawItems.value = (await api.get('/api/archive/raw')).items } catch (e) { error.value = e.message }
    finally { loading.value = false }
  } else if (k === 'deleted') {
    loading.value = true
    try {
      const d = await api.get('/api/archive/deleted')
      deletedItems.value = d.items
      restoreDays.value = d.restore_days ?? 30
    } catch (e) { error.value = e.message }
    finally { loading.value = false }
  } else if (k === 'reports') {
    loading.value = true
    try { reports.value = (await api.get('/api/archive/reports')).reports } catch (e) { error.value = e.message }
    finally { loading.value = false }
  } else if (k === 'distill') {
    loading.value = true
    try { distillLogs.value = (await api.get('/api/archive/distill-logs')).items } catch (e) { error.value = e.message }
    finally { loading.value = false }
  } else if (k === 'backup') {
    backupMsg.value = ''
  }
}

async function restoreBook(bookId, title) {
  if (!confirm(`恢复《${title}》到补书室？`)) return
  try {
    await api.post(`/api/archive/${bookId}/restore`, {})
    store.toast(`✅ 已恢复《${title}》`, 'info')
    deletedItems.value = deletedItems.value.filter((i) => i.book_id !== bookId)
    await loadSummary()
  } catch (e) {
    store.toast(`❌ ${e.message}`, 'error')
  }
}

async function doBackup() {
  backupMsg.value = '打包中…'
  try {
    const res = await fetch('/api/archive/backup')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const cd = res.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename="?([^";]+)"?/)
    const fname = m ? m[1] : `ai-library-backup.zip`
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = fname
    a.click()
    URL.revokeObjectURL(url)
    backupMsg.value = `✅ 已导出 ${(blob.size / 1024 / 1024).toFixed(2)} MB（${fname}）`
  } catch (e) {
    backupMsg.value = `❌ ${e.message}`
  }
}

function fmtSize(n) {
  if (!n) return '-'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

function fmtDate(s) {
  if (!s) return '-'
  return s.slice(0, 19).replace('T', ' ')
}

function reportTitle(r) {
  try {
    const c = typeof r.content === 'string' ? JSON.parse(r.content) : (r.content || {})
    return c.title || c.summary || (c.items ? `${c.items.length} 条` : '') || r.report_id
  } catch {
    return r.report_id
  }
}

function reportBody(r) {
  try {
    const c = typeof r.content === 'string' ? JSON.parse(r.content) : (r.content || {})
    return JSON.stringify(c).slice(0, 200)
  } catch {
    return String(r.content || '').slice(0, 200)
  }
}
</script>

<template>
  <div>
    <h1 class="page-title">🗄 档案馆</h1>
    <p class="page-sub">原始不可变副本 · 已删除（{{ restoreDays }} 天可恢复） · 历史日报 · 蒸馏过程 · 备份导出</p>

    <div v-if="error" class="card mb16" style="color:var(--danger)">❌ {{ error }}</div>

    <div class="card mb16">
      <div class="row wrap" style="gap:14px">
        <button
          v-for="t in TABS" :key="t.key"
          :class="{ primary: tab === t.key }"
          @click="switchTab(t.key)"
        >{{ t.label }}</button>
        <div class="spacer"></div>
        <div class="muted" v-if="summary">
          📕 原始 {{ summary.raw_count }} · 🗑 已删除 {{ summary.deleted_count }} · 📰 日报 {{ summary.report_count }} · 🔬 蒸馏 {{ summary.distill_log_count }}
        </div>
      </div>
    </div>

    <!-- 原始副本 -->
    <div v-if="tab === 'raw'" class="card">
      <div class="row mb16">
        <div><b>原始不可变副本</b> <span class="muted">（内容寻址，入馆时复制）</span></div>
        <div class="spacer"></div>
        <div class="muted">已关联 {{ rawLinked }} · 游离 {{ rawOrphan }}</div>
      </div>
      <div v-if="loading" class="loading">加载中…</div>
      <table v-else class="tbl">
        <thead><tr><th>哈希（前 12 位）</th><th>关联书</th><th>大小</th><th>修改时间</th></tr></thead>
        <tbody>
          <tr v-for="i in rawItems" :key="i.hash">
            <td><code>{{ i.hash.slice(0, 12) }}…</code></td>
            <td>
              <a v-if="i.book_id" href="#" @click.prevent="router.push(`/book/${i.book_id}`)">{{ i.book_title }}</a>
              <span v-else class="muted">（未关联）</span>
            </td>
            <td>{{ fmtSize(i.size) }}</td>
            <td class="muted">{{ fmtDate(i.modified_at) }}</td>
          </tr>
          <tr v-if="!rawItems.length"><td colspan="4" class="empty">暂无原始副本</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 已删除 -->
    <div v-if="tab === 'deleted'" class="card">
      <div class="row mb16">
        <div><b>已删除的书</b> <span class="muted">（{{ restoreDays }} 天内可恢复）</span></div>
        <div class="spacer"></div>
        <div class="muted">{{ deletedItems.length }} 本</div>
      </div>
      <div v-if="loading" class="loading">加载中…</div>
      <table v-else class="tbl">
        <thead><tr><th>书名</th><th>类型</th><th>删除时间</th><th>剩余恢复</th><th></th></tr></thead>
        <tbody>
          <tr v-for="i in deletedItems" :key="i.book_id">
            <td>{{ i.title }}</td>
            <td class="muted">{{ i.media_type || '-' }}</td>
            <td class="muted">{{ fmtDate(i.deleted_at) }}</td>
            <td>
              <span v-if="i.days_left !== null && i.days_left > 0" class="badge" :class="{ red: i.days_left <= 3 }">{{ i.days_left }} 天</span>
              <span v-else class="badge red">已过期</span>
            </td>
            <td>
              <button class="small primary" @click="restoreBook(i.book_id, i.title)">恢复</button>
            </td>
          </tr>
          <tr v-if="!deletedItems.length"><td colspan="5" class="empty">暂无已删除的书</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 历史日报 -->
    <div v-if="tab === 'reports'" class="card">
      <div class="mb16"><b>历史日报</b></div>
      <div v-if="loading" class="loading">加载中…</div>
      <table v-else class="tbl">
        <thead><tr><th>日期</th><th>类型</th><th>内容</th></tr></thead>
        <tbody>
          <tr v-for="r in reports" :key="r.report_id">
            <td class="muted">{{ r.date }}</td>
            <td><span class="badge">{{ r.rtype }}</span></td>
            <td>{{ reportTitle(r) }} <div class="muted">{{ reportBody(r) }}</div></td>
          </tr>
          <tr v-if="!reports.length"><td colspan="3" class="empty">暂无日报</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 蒸馏过程记录 -->
    <div v-if="tab === 'distill'" class="card">
      <div class="mb16"><b>蒸馏过程记录</b> <span class="muted">（vault/archive/distill-logs/）</span></div>
      <div v-if="loading" class="loading">加载中…</div>
      <div v-else-if="!distillLogs.length" class="empty">暂无蒸馏记录</div>
      <div v-else class="grid grid-3">
        <div v-for="d in distillLogs" :key="d.slug" class="card" style="padding:12px">
          <div style="font-weight:600">{{ d.slug }}</div>
          <div class="muted mb8">{{ d.count }} 个文件</div>
          <ul style="margin:0;padding-left:16px;font-size:0.85em;color:var(--ink-soft)">
            <li v-for="f in d.files.slice(0, 8)" :key="f">{{ f }}</li>
            <li v-if="d.files.length > 8" class="muted">… 还有 {{ d.files.length - 8 }} 个</li>
          </ul>
        </div>
      </div>
    </div>

    <!-- 备份导出 -->
    <div v-if="tab === 'backup'" class="card">
      <div class="mb16"><b>备份导出</b></div>
      <p class="muted">打包 data（数据库 / 向量索引 / 原始副本）与 vault（馆藏 / 技能产物）为一个 zip，可随时导入恢复。</p>
      <button class="primary" @click="doBackup">💾 导出备份 zip</button>
      <div class="mt8" v-if="backupMsg">{{ backupMsg }}</div>
    </div>
  </div>
</template>
