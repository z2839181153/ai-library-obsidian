<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useLibraryStore } from '../stores/library'

const store = useLibraryStore()
const router = useRouter()

const today = ref(null)         // {quota, recommendations, auto_mode}
const reports = ref([])
const busy = ref(false)
const generating = ref(false)

onMounted(load)

async function load() {
  await Promise.all([loadToday(), loadReports()])
}
async function loadToday() {
  try { today.value = await api.purchaseToday() } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}
async function loadReports() {
  try { reports.value = (await api.dailyReports()).reports } catch { reports.value = [] }
}

async function generate() {
  generating.value = true
  try {
    const r = await api.purchaseGenerate()
    store.toast(`已生成 ${r.generated} 条推荐`, 'info')
    await load()
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
  finally { generating.value = false }
}

async function collect(rec) {
  busy.value = true
  try {
    const r = await api.purchaseCollect(rec.rec_id)
    store.toast(`✅ 《${rec.title}》已收藏入馆`, 'info')
    await Promise.all([load(), store.refreshDashboard()])
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
  finally { busy.value = false }
}

async function feedback(rec, action) {
  try {
    await api.purchaseFeedback(rec.rec_id, action)
    store.toast(action === 'ignore' ? '已忽略' : '已标记不感兴趣', 'info')
    await load()
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}

const pendingRecs = computed(() => (today.value?.recommendations || []).filter((r) => r.status === 'pending'))
const doneRecs = computed(() => (today.value?.recommendations || []).filter((r) => r.status !== 'pending'))
</script>

<template>
  <div>
    <h1 class="page-title">🛒 采购员</h1>
    <p class="page-sub">每日配额 + 推荐清单，主人确认才入馆</p>

    <div class="card mb16">
      <div class="row">
        <h3 style="margin:0">📋 今日配额</h3>
        <span class="spacer"></span>
        <button class="small primary" @click="generate" :disabled="generating || !!pendingRecs.length">
          {{ generating ? '生成中…' : (pendingRecs.length ? '已有推荐' : '生成今日推荐') }}
        </button>
      </div>
      <div class="mt8" style="font-size:1.4em;font-weight:700">
        {{ today?.quota?.stats?.total ?? 0 }} / {{ today?.quota?.max_daily ?? 0 }} 条
        <span class="badge gold" style="margin-left:8px">执行率 {{ today?.quota?.stats?.total ? Math.round(((today.quota.stats.collected || 0) + (today.quota.stats.ignored || 0) + (today.quota.stats.not_interested || 0)) / today.quota.stats.total * 100) : 0 }}%</span>
      </div>
      <details class="mt8">
        <summary class="muted">配额理由</summary>
        <p class="muted mt8" style="line-height:1.6">{{ today?.quota?.reason }}</p>
      </details>
      <div v-if="today?.auto_mode" class="badge gold mt8">自动模式：高分推荐自动入馆</div>
      <div v-else class="badge mt8">保守模式：仅推荐，主人确认后入馆</div>
    </div>

    <!-- 推荐清单 -->
    <div class="card mb16">
      <h3 style="margin:0 0 8px">📌 推荐清单（{{ pendingRecs.length }}）</h3>
      <div v-if="!pendingRecs.length" class="empty">暂无待处理推荐</div>
      <div v-else class="grid grid-3">
        <div v-for="rec in pendingRecs" :key="rec.rec_id" class="book-card">
          <div class="title">{{ rec.title }}</div>
          <div class="meta">
            <span class="badge">{{ rec.source }}</span>
            <span class="badge gold">评分 {{ rec.score }}</span>
            <a v-if="rec.url" :href="rec.url" target="_blank" rel="noopener" class="muted">🔗 来源</a>
          </div>
          <div class="muted" style="font-size:0.85em">{{ rec.reason }}</div>
          <div class="row">
            <button class="small primary grow" @click="collect(rec)" :disabled="busy">✅ 收藏入馆</button>
            <button class="small" @click="feedback(rec, 'ignore')">❌ 忽略</button>
            <button class="small" title="不感兴趣" @click="feedback(rec, 'not_interested')">🙅</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 已处理 -->
    <div v-if="doneRecs.length" class="card mb16">
      <h3 style="margin:0 0 8px">🗃 已处理</h3>
      <div class="row wrap">
        <span v-for="rec in doneRecs" :key="rec.rec_id" class="badge" :class="{ red: rec.status === 'not_interested' }">
          {{ rec.status }} · {{ rec.title }}
        </span>
      </div>
    </div>

    <!-- 历史日报 -->
    <div class="card">
      <h3 style="margin:0 0 8px">📰 历史日报</h3>
      <div v-if="!reports.length" class="empty">暂无日报</div>
      <div v-else>
        <details v-for="rep in reports" :key="rep.report_id" class="mb8">
          <summary>
            <span class="badge">{{ rep.rtype }}</span>
            <span class="muted">{{ rep.date }}</span>
            <span class="muted">· {{ rep.content?.title || '' }}</span>
          </summary>
          <div class="mt8 muted" v-if="rep.content?.items">
            <div v-for="(it, i) in rep.content.items" :key="i">· {{ it.title }}（{{ it.source }} · {{ it.score }}）</div>
          </div>
          <div class="mt8 muted" v-else-if="rep.content?.note">{{ rep.content.note }}</div>
          <div class="mt8 muted" v-else>{{ JSON.stringify(rep.content) }}</div>
        </details>
      </div>
    </div>
  </div>
</template>
