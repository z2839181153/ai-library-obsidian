<script setup>
// P5-4 阅览室顶层入口：最近阅读 + 最近入馆书列表，点书进入三栏阅读页
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import BookCard from '../components/BookCard.vue'

const router = useRouter()
const recentReads = ref([])
const recentBooks = ref([])
const loading = ref(true)
const error = ref('')

function fmtTime(iso) {
  if (!iso) return ''
  // 2026-08-14T20:33:00+08:00 → 08-14 20:33
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/)
  return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}` : iso
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [r, b] = await Promise.all([
      api.recentReads(8),
      api.books({ limit: 12 }),
    ])
    recentReads.value = (r.books || []).filter((x) => x.status !== 'deleted')
    recentBooks.value = (b.books || []).filter((x) => x.status !== 'deleted')
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

onMounted(load)

function openBook(b) {
  router.push(`/book/${b.book_id}`)
}
</script>

<template>
  <div>
    <h1 class="page-title">📖 阅览室</h1>
    <p class="page-sub">最近阅读与最近入馆 · 点击书进入阅读</p>

    <div v-if="error" class="card mt16" style="color:var(--danger)">❌ {{ error }}</div>
    <div v-if="loading" class="card mt16">加载中…</div>

    <!-- 最近阅读 -->
    <div class="card mt16">
      <h3 style="margin:0 0 10px">🕘 最近阅读</h3>
      <div v-if="!recentReads.length" class="empty">还没有读过书——打开任意一本书后这里会出现</div>
      <div v-else class="grid grid-3">
        <BookCard v-for="b in recentReads" :key="b.book_id" :book="b"
          @open="openBook" @read="openBook" />
      </div>
    </div>

    <!-- 最近入馆 -->
    <div class="card mt16">
      <h3 style="margin:0 0 10px">📥 最近入馆</h3>
      <div v-if="!recentBooks.length" class="empty">馆内还没有书——先去入馆吧</div>
      <div v-else class="grid grid-3">
        <BookCard v-for="b in recentBooks" :key="b.book_id" :book="b"
          @open="openBook" @read="openBook" />
      </div>
    </div>
  </div>
</template>
