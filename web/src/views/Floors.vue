<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useLibraryStore } from '../stores/library'
import BookCard from '../components/BookCard.vue'

const store = useLibraryStore()
const router = useRouter()

const floors = ref([])
const books = ref([])
const mode = ref('floor')       // floor | room | review（补书室）| tags（标签书架）
const activeFloor = ref(null)
const activeRoom = ref(null)
const loading = ref(false)
const error = ref('')

// 标签书架（P4-6）
const tagBooks = ref([])        // shelved 全部书（含 tags）
const activeTag = ref('')
const tagAgg = computed(() => {
  const m = {}
  for (const b of tagBooks.value) {
    for (const t of (b.tags || [])) m[t] = (m[t] || 0) + 1
  }
  return Object.entries(m).sort((a, b) => b[1] - a[1])
})
const visibleTagBooks = computed(() => {
  if (!activeTag.value) return []
  return tagBooks.value.filter((b) => (b.tags || []).includes(activeTag.value))
})

// 补书室两区
const reviewBooks = ref([])     // status=reviewing（有建议）
const incomingBooks = ref([])   // status=incoming（无建议）

onMounted(async () => {
  await load()
  await loadReviewRoom()
})

async function load() {
  try {
    loading.value = true
    const d = await api.floors()
    floors.value = d.floors
    if (floors.value.length && !activeFloor.value) {
      activeFloor.value = floors.value[0].floor_id
    }
    await loadBooks()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function loadBooks() {
  if (mode.value === 'floor') {
    const f = floors.value.find((x) => x.floor_id === activeFloor.value)
    books.value = (await api.books({ status: 'shelved' })).books.filter((b) => {
      const vp = b.vault_path || ''
      return vp.includes(`${f.code}-`) || vp.includes(`/${f.code}/`)
    })
  } else if (mode.value === 'room') {
    const f = floors.value.find((x) => x.floor_id === activeFloor.value)
    const rm = (f?.rooms || []).find((x) => x.room_id === activeRoom.value)
    books.value = (await api.books({ status: 'shelved' })).books.filter((b) => {
      const vp = b.vault_path || ''
      return vp.includes(`${f.code}-`) && vp.includes(`/${rm.name}/`)
    })
  }
}

async function loadReviewRoom() {
  const all = await api.books()
  reviewBooks.value = all.books.filter((b) => b.status === 'reviewing')
  incomingBooks.value = all.books.filter((b) => b.status === 'incoming')
}

function selectFloor(f) {
  mode.value = 'floor'
  activeFloor.value = f.floor_id
  activeRoom.value = null
  loadBooks()
}
function selectRoom(rm) {
  mode.value = 'room'
  activeRoom.value = rm.room_id
  loadBooks()
}
function showReview() {
  mode.value = 'review'
  loadReviewRoom()
}
async function loadTagBooks() {
  try {
    const all = await api.books({ status: 'shelved' })
    tagBooks.value = all.books
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}
function showTags() {
  mode.value = 'tags'
  activeTag.value = ''
  if (!tagBooks.value.length) loadTagBooks()
}
function selectTag(tag) {
  activeTag.value = activeTag.value === tag ? '' : tag
}

async function confirmBook(book, pos) {
  try {
    await api.confirmShelve(book.book_id, pos)
    store.toast(`✅ 《${book.title}》已上架`, 'info')
    await Promise.all([load(), loadReviewRoom(), store.refreshDashboard()])
  } catch (e) {
    store.toast(`❌ ${e.message}`, 'error')
  }
}
async function classifyBook(book) {
  try {
    await api.classify(book.book_id)
    store.toast(`🔖 《${book.title}》分类建议已生成`, 'info')
    await loadReviewRoom()
  } catch (e) {
    store.toast(`❌ ${e.message}`, 'error')
  }
}
</script>

<template>
  <div>
    <h1 class="page-title">📖 楼层</h1>
    <p class="page-sub">按楼层浏览馆藏，或到补书室处理新书</p>

    <div class="grid grid-main">
      <!-- 左：楼层列表 + 补书室 -->
      <div>
        <div class="floor-list">
          <div class="floor-item" :class="{ active: mode === 'review' }" @click="showReview()">
            <span>📥</span><span class="grow">补书室</span>
            <span class="badge red" v-if="reviewBooks.length + incomingBooks.length">{{ reviewBooks.length + incomingBooks.length }}</span>
          </div>
          <div class="floor-item" :class="{ active: mode === 'tags' }" @click="showTags()">
            <span>🏷</span><span class="grow">标签书架</span>
            <span class="badge" v-if="tagAgg.length">{{ tagAgg.length }}</span>
          </div>
          <div
            v-for="f in floors" :key="f.floor_id"
            class="floor-item" :class="{ active: mode !== 'review' && activeFloor === f.floor_id }"
            @click="selectFloor(f)"
          >
            <span style="font-weight:700">{{ f.code }}</span>
            <span class="grow">{{ f.name }}</span>
            <span class="badge">{{ f.media_type }}</span>
          </div>
        </div>
      </div>

      <!-- 右：内容 -->
      <div>
        <!-- 补书室 -->
        <template v-if="mode === 'review'">
          <div class="card mb16">
            <h3 style="margin:0 0 8px">💡 建议区（已有分类建议，确认后上架）</h3>
            <div v-if="!reviewBooks.length" class="empty">没有待确认的书</div>
            <div v-else class="grid grid-3">
              <div v-for="b in reviewBooks" :key="b.book_id" class="book-card" @click="router.push(`/book/${b.book_id}`)">
                <div class="title">{{ b.title }}</div>
                <div class="meta">
                  📍 {{ b.suggest.floor }} / {{ b.suggest.room }} / {{ b.suggest.shelf || '-' }}
                  <span v-if="b.distill_value !== null">💎{{ b.distill_value }}</span>
                </div>
                <div class="row">
                  <button class="small primary grow" @click.stop="confirmBook(b, {})">✅ 确认上架</button>
                  <button class="small" @click.stop="router.push(`/book/${b.book_id}`)">📖 查看</button>
                </div>
              </div>
            </div>
          </div>

          <div class="card">
            <h3 style="margin:0 0 8px">📥 待定区（刚入馆，尚无分类建议）</h3>
            <div v-if="!incomingBooks.length" class="empty">没有新书</div>
            <div v-else class="grid grid-3">
              <div v-for="b in incomingBooks" :key="b.book_id" class="book-card" @click="router.push(`/book/${b.book_id}`)">
                <div class="title">{{ b.title }}</div>
                <div class="meta">{{ b.media_type }} · 刚入馆</div>
                <div class="row">
                  <button class="small primary grow" @click.stop="classifyBook(b)">🔖 生成分类建议</button>
                  <button class="small" @click.stop="router.push(`/book/${b.book_id}`)">📖 查看</button>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- 标签书架（虚拟书架，P4-6） -->
        <template v-else-if="mode === 'tags'">
          <div class="card mb16">
            <h3 style="margin:0 0 8px">🏷 标签书架（虚拟书架）</h3>
            <p class="muted" style="margin:0 0 10px">按标签聚合馆藏书；点标签筛选，再点取消。</p>
            <div v-if="!tagAgg.length" class="empty">还没有标签（书完成分类后自动生成）</div>
            <div v-else class="row wrap" style="gap:8px">
              <button v-for="[tag, n] in tagAgg" :key="tag" class="small"
                      :class="{ primary: activeTag === tag }" @click="selectTag(tag)">
                {{ tag }} <span class="badge">{{ n }}</span>
              </button>
            </div>
          </div>
          <div v-if="activeTag" class="mb8 muted">「{{ activeTag }}」共 {{ visibleTagBooks.length }} 本</div>
          <div v-if="activeTag && !visibleTagBooks.length" class="empty">该标签下暂无书</div>
          <div v-else-if="activeTag" class="grid grid-3">
            <BookCard v-for="b in visibleTagBooks" :key="b.book_id" :book="b" @open="(x) => router.push(`/book/${x.book_id}`)" />
          </div>
        </template>

        <!-- 楼层 / 房间书列表 -->
        <template v-else>
          <div class="row wrap mb8">
            <template v-if="mode === 'floor'">
              <template v-for="rm in (floors.find(f => f.floor_id === activeFloor)?.rooms || [])" :key="rm.room_id">
                <button class="small" :class="{ primary: activeRoom === rm.room_id }" @click="selectRoom(rm)">{{ rm.name }}</button>
              </template>
            </template>
            <button v-if="mode === 'room'" class="small" @click="selectFloor(floors.find(f => f.floor_id === activeFloor))">← 返回楼层</button>
          </div>

          <div v-if="loading" class="loading">加载中…</div>
          <div v-else-if="error" class="empty">{{ error }}</div>
          <div v-else-if="!books.length" class="empty">该区域还没有书</div>
          <div v-else class="grid grid-3">
            <BookCard v-for="b in books" :key="b.book_id" :book="b" @open="(x) => router.push(`/book/${x.book_id}`)" />
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
