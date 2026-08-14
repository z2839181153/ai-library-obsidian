<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useLibraryStore } from '../stores/library'

const route = useRoute()
const router = useRouter()
const store = useLibraryStore()

const book = ref(null)
const card = ref(null)
const content = ref([])
const loading = ref(true)
const error = ref('')
const shelfForm = ref({ floor: '', room: '', shelf: '' })
const distill = ref(null)      // status 信息
const distillBusy = ref(false)

const bookId = computed(() => route.params.id)

onMounted(load)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const d = await api.book(bookId.value)
    book.value = d.book
    card.value = d.card
    shelfForm.value.floor = d.book.suggest_floor || ''
    shelfForm.value.room = d.book.suggest_room || ''
    shelfForm.value.shelf = d.book.suggest_shelf || ''
    const c = await api.bookContent(bookId.value)
    content.value = c.sections || []
    if (d.book.distill_status && d.book.distill_status !== 'idle') {
      try { distill.value = await api.distillStatus(bookId.value) } catch { /* ignore */ }
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function classify() {
  try {
    await api.classify(bookId.value)
    store.toast('分类建议已生成', 'info')
    await load()
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}
async function confirmShelve() {
  try {
    await api.confirmShelve(bookId.value, shelfForm.value)
    store.toast('✅ 已上架', 'info')
    store.refreshDashboard()
    await load()
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}
async function startDistill(auto = false) {
  distillBusy.value = true
  try {
    const r = await api.distillStart(bookId.value, auto)
    store.toast(`🔬 蒸馏已启动（${r.status || ''}）`, 'info')
    distill.value = r
    await load()
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
  finally { distillBusy.value = false }
}
async function confirmStage(decision) {
  try {
    const r = await api.distillConfirm(bookId.value, decision)
    store.toast(`蒸馏确认：${decision}`, 'info')
    distill.value = r
    await load()
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}

const statusText = computed(() => ({
  incoming: '待分类', reviewing: '待确认', shelved: '已上架',
  indexed: '已索引', deleted: '已删除',
}[book.value?.status] || book.value?.status))
</script>

<template>
  <div>
    <button class="ghost mb8" @click="router.push('/floors')">← 返回楼层</button>

    <div v-if="loading" class="loading">加载中…</div>
    <div v-else-if="error" class="empty">❌ {{ error }}</div>
    <template v-else>
      <div class="row mb16">
        <h1 class="page-title grow" style="margin:0">{{ book.title }}</h1>
        <span class="badge">{{ statusText }}</span>
        <span class="badge gold">{{ book.media_type }}</span>
      </div>

      <!-- 分类/上架 操作条 -->
      <div class="card mb16">
        <h3 style="margin:0 0 8px">📍 分类与上架</h3>
        <div class="row wrap">
          <label class="muted">楼层 <input type="text" v-model="shelfForm.floor" style="width:70px" /></label>
          <label class="muted">房间 <input type="text" v-model="shelfForm.room" style="width:140px" /></label>
          <label class="muted">书架 <input type="text" v-model="shelfForm.shelf" style="width:140px" /></label>
          <button v-if="book.status !== 'shelved'" class="small primary" @click="classify">🔖 生成建议</button>
          <button v-if="book.status !== 'shelved'" class="small primary" @click="confirmShelve">✅ 确认上架</button>
          <span v-if="book.status === 'shelved'" class="muted">已上架：{{ book.vault_path }}</span>
        </div>
      </div>

      <!-- 蒸馏 -->
      <div class="card mb16">
        <h3 style="margin:0 0 8px">🔬 蒸馏（cangjie-skill 流水线）</h3>
        <div class="row wrap">
          <span class="muted">状态：{{ book.distill_status || 'idle' }}</span>
          <template v-if="book.distill_status === 'awaiting'">
            <button class="small primary" :disabled="distillBusy" @click="confirmStage('continue')">✅ 主人确认，继续</button>
            <button class="small" @click="confirmStage('skip')">⏭ 跳过该阶段</button>
            <button class="small danger" @click="confirmStage('cancel')">✖ 取消</button>
          </template>
          <template v-else-if="['idle', 'failed', 'blocked'].includes(book.distill_status || 'idle')">
            <button class="small" :disabled="distillBusy" @click="startDistill(false)">▶ 启动蒸馏</button>
            <button class="small" :disabled="distillBusy" @click="startDistill(true)">▶ 自动确认模式</button>
          </template>
          <span v-else-if="book.distill_status === 'running'" class="muted">蒸馏进行中…</span>
        </div>
        <div v-if="distill" class="mt8 muted">
          <div v-for="(v, k) in distill" :key="k" class="muted">{{ k }}: {{ typeof v === 'object' ? JSON.stringify(v) : v }}</div>
        </div>
      </div>

      <!-- 图书卡片 -->
      <div v-if="card" class="card mb16">
        <h3 style="margin:0 0 8px">📇 图书卡片</h3>
        <p>{{ card.summary }}</p>
        <div class="muted">
          蒸馏价值：<b>💎{{ card.distill_value }}</b> / 100 · {{ card.distill_reason }}
        </div>
        <div class="row wrap mt8" v-if="(card.tags || []).length">
          <span v-for="t in card.tags" :key="t" class="badge">{{ t }}</span>
        </div>
        <div v-if="(card.chapters || []).length" class="mt8">
          <h4 style="margin:6px 0">章节</h4>
          <div v-for="c in card.chapters" :key="c.title" class="muted">· {{ c.title }} — {{ c.summary }}</div>
        </div>
        <div v-if="(card.concepts || []).length" class="mt8">
          <h4 style="margin:6px 0">关键概念</h4>
          <div v-for="c in card.concepts" :key="c.term" class="muted"><b>{{ c.term }}</b>：{{ c.definition }}</div>
        </div>
      </div>

      <!-- 原文 -->
      <div class="card">
        <h3 style="margin:0 0 8px">📖 原文</h3>
        <div v-if="!content.length" class="empty">暂无正文</div>
        <div v-for="(sec, i) in content" :key="i" class="mb8">
          <h4 style="margin:8px 0 4px">{{ sec.title }}</h4>
          <p style="white-space:pre-wrap;line-height:1.7;margin:0">{{ sec.content }}</p>
        </div>
      </div>
    </template>
  </div>
</template>
