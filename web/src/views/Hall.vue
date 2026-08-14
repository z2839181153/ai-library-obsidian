<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useLibraryStore } from '../stores/library'
import { api } from '../api'
import echarts from '../echarts'

const store = useLibraryStore()
const router = useRouter()
const uploading = ref(false)
const uploadMsg = ref('')

const d = computed(() => store.dashboard || {})
const health = computed(() => d.value.health || {})
const quota = computed(() => health.value.quota || {})

const weekChartEl = ref(null)
let weekChart = null

// P5-4 继续阅读（最近阅读书）
const recentReads = ref([])
async function loadRecentReads() {
  try {
    const r = await api.recentReads(5)
    recentReads.value = (r.books || []).filter((x) => x.status !== 'deleted')
  } catch { recentReads.value = [] }
}
onMounted(() => {
  store.refreshDashboard()
  loadRecentReads()
  window.addEventListener('resize', resizeChart)
})
onUnmounted(() => {
  window.removeEventListener('resize', resizeChart)
  if (weekChart) { weekChart.dispose(); weekChart = null }
})

// 数据到达后渲染折线图（P4-6）
watch(() => health.value.week, renderWeekChart, { deep: true })

function resizeChart() { if (weekChart) weekChart.resize() }

function renderWeekChart() {
  if (!weekChartEl.value) return
  if (!weekChart) weekChart = echarts.init(weekChartEl.value)
  const week = health.value.week || []
  weekChart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 36, right: 16, top: 20, bottom: 28 },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: week.map((w) => w.date.slice(5)),
    },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      name: '入馆数',
      type: 'line',
      smooth: true,
      symbolSize: 7,
      data: week.map((w) => w.count),
      lineStyle: { color: '#3d5a45', width: 3 },
      itemStyle: { color: '#3d5a45' },
      areaStyle: { color: 'rgba(61,90,69,0.12)' },
      markLine: {
        silent: true,
        symbol: 'none',
        data: [{ type: 'average', name: '均值' }],
        lineStyle: { color: '#b98a2f', type: 'dashed' },
      },
    }],
  }, true)
}

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
    <div class="grid grid-half" style="align-items:start">
      <div class="card">
        <h3 style="margin:0 0 10px">📈 本周入馆趋势</h3>
        <div ref="weekChartEl" style="width:100%;height:150px"></div>
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

    <!-- P5-4 继续阅读 -->
    <div class="card mt16">
      <div class="row" style="align-items:center">
        <h3 style="margin:0 0 10px;flex:1">🕘 继续阅读</h3>
        <button class="link-btn" @click="router.push('/reading')">全部 →</button>
      </div>
      <div v-if="!recentReads.length" class="empty">还没读过书——打开任意一本书后这里会出现</div>
      <div v-else class="row wrap">
        <div
          v-for="b in recentReads"
          :key="b.book_id"
          class="reading-chip"
          @click="router.push(`/book/${b.book_id}`)"
        >
          <span class="grow">{{ b.title }}</span>
          <span class="badge" style="margin-left:8px">{{ b.media_type || '书' }}</span>
        </div>
      </div>
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
