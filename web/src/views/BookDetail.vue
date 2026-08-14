<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MarkdownIt from 'markdown-it'
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
const floors = ref([])         // 楼层下拉（P3 修复：避免自由文本填名称不匹配）
const classifyBusy = ref(false)
let classifyController = null

// ---- P4-3 阅览室增强 ----
const contentEl = ref(null)          // 原文容器（高亮 DOM 遍历）
const highlightWords = ref([])       // URL ?hl=词1,词2
const related = ref(null)            // /related：{skills, notes}
const activeSection = ref(-1)        // 当前阅读章节
const expandedSkill = ref('')        // 相关技能展开 description
let io = null

const md = new MarkdownIt({ html: false, breaks: true, linkify: true })

const bookId = computed(() => route.params.id)

// 高亮词来自 URL query（管理员搜索/问答定位跳转传入 ?hl=）
watch(
  () => route.query.hl,
  (v) => {
    highlightWords.value = String(v || '').split(',').map((s) => s.trim()).filter(Boolean)
    applyHighlight()
  },
  { immediate: true, flush: 'post' }
)
// 原文加载/切换后重放高亮（v-html 会清掉 mark）
// flush:'post'：回调在组件更新 + ref 赋值之后执行（Vue 3.5 下 nextTick 不够晚，ref 可能还是 null）
// 原文加载/切换后重放高亮（v-html 会清掉 mark）。
// 关键：content 赋值时 loading 仍为 true（DOM 未渲染书详情），等 loading 变 false
// 且 DOM 更新完成后（flush:'post'）才真正可高亮，因此同时监听 content + loading。
watch(
  [content, loading],
  () => {
    setupObserver()
    applyHighlight()
  },
  { flush: 'post' }
)

onMounted(load)
onBeforeUnmount(() => io?.disconnect())

async function loadFloors() {
  try {
    const d = await api.floors()
    floors.value = d.floors || []
    // 建议楼层是 code（如 1F）；下拉选中对应项
    const sf = book.value?.suggest_floor || ''
    if (sf && !floors.value.some((f) => f.code === sf || f.name === sf)) {
      // 建议的楼层不存在时保留文本，供用户手动确认
      shelfForm.value.floor = sf
    }
  } catch { floors.value = [] }
}

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
    await loadFloors()
    loadRelated()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

// ---- P4-3：相关技能 / 相关笔记 ----
async function loadRelated() {
  try {
    const r = await api.bookRelated(bookId.value)
    console.log('[P43-related]', JSON.stringify(r).slice(0, 300))
    related.value = r
  } catch (e) {
    console.error('[P43-related-err]', e)
    related.value = null
  }
}

// ---- P4-3：章节导航 ----
function scrollToSection(i) {
  document.getElementById(`sec-${i}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function getContentRoot() {
  return contentEl.value || document.querySelector('.book-content')
}

function setupObserver() {
  io?.disconnect()
  const root = getContentRoot()
  if (!root || typeof IntersectionObserver === 'undefined') return
  io = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)
      if (visible.length) {
        const idx = Number(visible[0].target.dataset.idx)
        if (!Number.isNaN(idx)) activeSection.value = idx
      }
    },
    { rootMargin: '-15% 0px -65% 0px' }
  )
  root.querySelectorAll('.book-section').forEach((el) => io.observe(el))
}

// ---- P4-3：markdown 渲染 + 高亮 ----
function renderMd(text) {
  return md.render(text || '')
}

function applyHighlight() {
  const root = getContentRoot()
  const words = highlightWords.value
  if (!root || !words.length) return
  // 先清掉已有 mark（v-html 重渲染 / 重复调用时不产生嵌套）
  root.querySelectorAll('mark.hl').forEach((m) => {
    m.replaceWith(document.createTextNode(m.textContent))
  })
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const targets = []
  while (walker.nextNode()) targets.push(walker.currentNode)
  targets.forEach((n) => highlightTextNode(n, words))
}

function highlightTextNode(node, words) {
  const text = node.nodeValue || ''
  const esc = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).filter(Boolean)
  if (!esc.length) return
  const re = new RegExp(`(${esc.join('|')})`, 'gi')
  const parts = text.split(re)
  if (parts.length === 1) return
  const frag = document.createDocumentFragment()
  for (const part of parts) {
    if (!part) continue
    if (words.some((w) => w && part.toLowerCase() === w.toLowerCase())) {
      const m = document.createElement('mark')
      m.className = 'hl'
      m.textContent = part
      frag.appendChild(m)
    } else {
      frag.appendChild(document.createTextNode(part))
    }
  }
  node.parentNode?.replaceChild(frag, node)
}

function toggleSkill(skillId) {
  expandedSkill.value = expandedSkill.value === skillId ? '' : skillId
}

async function classify() {
  if (classifyBusy.value) {
    // 点击"取消"：中断进行中的分类请求
    classifyController?.abort()
    classifyController = null
    classifyBusy.value = false
    store.toast('已取消分类', 'info')
    return
  }
  classifyBusy.value = true
  classifyController = new AbortController()
  try {
    // LLM 分类可能较慢（含重试）；120s 超时 + 可取消
    await api.classify(bookId.value, false, { signal: classifyController.signal, timeout: 120000 })
    store.toast('分类建议已生成', 'info')
    await load()
  } catch (e) {
    if (e.name === 'AbortError') store.toast('分类已取消/超时', 'info')
    else store.toast(`❌ ${e.message}`, 'error')
  } finally {
    classifyController = null
    classifyBusy.value = false
  }
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

const relText = { same_book: '本书蒸馏', same_room: '同房间', similar: '内容相似' }
const relCls = { same_book: 'rel-book', same_room: 'rel-room', similar: 'rel-sim' }
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
          <label class="muted">楼层
            <select v-model="shelfForm.floor" style="width:110px">
              <option value="">（请选择）</option>
              <option v-for="f in floors" :key="f.floor_id" :value="f.code">{{ f.name }}（{{ f.code }}）</option>
            </select>
          </label>
          <label class="muted">房间 <input type="text" v-model="shelfForm.room" style="width:140px" /></label>
          <label class="muted">书架 <input type="text" v-model="shelfForm.shelf" style="width:140px" /></label>
          <button v-if="book.status !== 'shelved'" class="small primary" @click="classify">
            {{ classifyBusy ? '✖ 取消' : '🔖 生成建议' }}
          </button>
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

      <!-- 三栏：章节导航 | 卡片+原文 | 相关面板 -->
      <div class="book-layout">
        <!-- 左：章节导航 -->
        <aside class="side-panel toc-panel">
          <h4 class="side-title">📑 章节</h4>
          <div v-if="!content.length" class="muted" style="font-size:0.85em">（暂无正文）</div>
          <button
            v-for="(sec, i) in content" :key="i"
            class="toc-item" :class="{ active: activeSection === i }"
            @click="scrollToSection(i)"
          >
            {{ sec.title }}
          </button>
        </aside>

        <!-- 中：图书卡片 + 原文 -->
        <div class="main-panel">
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

          <div class="card">
            <h3 style="margin:0 0 8px">📖 原文
              <span v-if="highlightWords.length" class="hl-hint">高亮：{{ highlightWords.join('、') }}</span>
            </h3>
            <div v-if="!content.length" class="empty">暂无正文</div>
            <div ref="contentEl" class="book-content">
              <section v-for="(sec, i) in content" :key="i" :id="`sec-${i}`" class="book-section" :data-idx="i">
                <h4 class="section-title">{{ sec.title }}</h4>
                <div class="section-body" v-html="renderMd(sec.content)"></div>
              </section>
            </div>
          </div>
        </div>

        <!-- 右：相关技能 / 相关笔记 -->
        <aside class="side-panel related-panel">
          <div class="card">
            <h4 class="side-title">🔗 相关技能</h4>
            <div v-if="!related?.skills?.length" class="muted" style="font-size:0.85em">暂无相关技能</div>
            <div v-for="s in related?.skills" :key="s.skill_id" class="rel-item">
              <div class="row" style="gap:6px;align-items:flex-start">
                <span class="rel-name grow" @click="toggleSkill(s.skill_id)">{{ s.name }}</span>
                <span class="rel-tag" :class="relCls[s.relation]">{{ relText[s.relation] }}</span>
              </div>
              <div class="muted" style="font-size:0.8em;line-height:1.4">
                <span class="badge">{{ s.status }}</span>
                <template v-if="s.book_title && s.relation !== 'same_book'"> · 来自《{{ s.book_title }}》</template>
                <template v-if="s.similarity !== null && s.similarity !== undefined"> · 相似 {{ s.similarity.toFixed(2) }}</template>
              </div>
              <p v-if="expandedSkill === s.skill_id && s.description" class="rel-desc">{{ s.description }}</p>
            </div>
          </div>

          <div class="card mt16">
            <h4 class="side-title">📚 相关笔记</h4>
            <div v-if="!related?.notes?.length" class="muted" style="font-size:0.85em">暂无相关笔记</div>
            <div v-for="n in related?.notes" :key="n.book_id" class="rel-item">
              <div class="row" style="gap:6px;align-items:flex-start">
                <a class="rel-name grow" @click="router.push(`/book/${n.book_id}`)">{{ n.title }}</a>
                <span class="rel-tag" :class="relCls[n.relation]">{{ relText[n.relation] }}</span>
              </div>
              <div class="muted" style="font-size:0.8em;line-height:1.4">
                <template v-if="n.relation === 'similar' && n.score !== null && n.score !== undefined">相似度 {{ n.score.toFixed(3) }}</template>
                <template v-if="n.section"> · {{ n.section }}</template>
              </div>
              <p v-if="n.snippet" class="rel-snippet">{{ n.snippet }}</p>
            </div>
          </div>
        </aside>
      </div>
    </template>
  </div>
</template>

<style>
/* P4-3 阅览室三栏布局 + markdown 渲染 + 高亮（类名全局，避免 v-html 内容穿透问题） */
.book-layout {
  display: grid;
  grid-template-columns: 210px minmax(0, 1fr) 270px;
  gap: 16px;
  align-items: start;
}
.side-panel {
  position: sticky;
  top: 76px;
  max-height: calc(100vh - 96px);
  overflow-y: auto;
}
.side-title { margin: 0 0 8px; font-size: 0.95em; color: var(--ink-soft); }
.toc-item {
  display: block;
  width: 100%;
  text-align: left;
  border: none;
  background: transparent;
  padding: 6px 9px;
  border-radius: 6px;
  font-size: 0.86em;
  color: var(--ink-soft);
  margin-bottom: 2px;
  line-height: 1.4;
}
.toc-item:hover { background: var(--accent-soft); }
.toc-item.active { background: var(--accent); color: #fff; }

.book-section { margin-bottom: 18px; }
.section-title { margin: 0 0 6px; color: var(--accent); }
.section-body { line-height: 1.75; word-break: break-word; }
.section-body p { margin: 0 0 10px; }
.section-body h1, .section-body h2, .section-body h3 { margin: 14px 0 6px; }
.section-body h4, .section-body h5 { margin: 10px 0 4px; }
.section-body ul, .section-body ol { margin: 0 0 10px; padding-left: 22px; }
.section-body code {
  background: var(--bg-soft);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 0.9em;
}
.section-body pre {
  background: var(--bg-soft);
  padding: 10px 12px;
  border-radius: 8px;
  overflow: auto;
  font-size: 0.88em;
}
.section-body blockquote {
  margin: 0 0 10px;
  padding: 4px 12px;
  border-left: 3px solid var(--gold);
  color: var(--ink-soft);
}
.section-body table { border-collapse: collapse; margin-bottom: 10px; }
.section-body th, .section-body td { border: 1px solid var(--border); padding: 5px 9px; }
mark.hl {
  background: #ffe28a;
  color: #5b3a00;
  border-radius: 3px;
  padding: 0 2px;
}
.hl-hint { font-size: 0.78em; color: var(--gold); margin-left: 8px; }

.rel-item { padding: 8px 0; border-bottom: 1px dashed var(--border); }
.rel-item:last-child { border-bottom: none; }
.rel-name { font-size: 0.92em; cursor: pointer; }
.rel-tag {
  flex-shrink: 0;
  font-size: 0.72em;
  border-radius: 999px;
  padding: 1px 7px;
  white-space: nowrap;
}
.rel-tag.rel-book { background: var(--accent-soft); color: var(--accent); }
.rel-tag.rel-room { background: #e3ecf7; color: #3a5f8a; }
.rel-tag.rel-sim { background: #f0ebe0; color: #8a7a4a; }
.rel-desc { font-size: 0.85em; color: var(--ink-soft); margin: 6px 0 0; }
.rel-snippet {
  font-size: 0.82em;
  color: var(--ink-soft);
  margin: 4px 0 0;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

@media (max-width: 1080px) {
  .book-layout { grid-template-columns: 1fr; }
  .side-panel { position: static; max-height: none; }
  .toc-panel { order: 2; }
  .main-panel { order: 1; }
  .related-panel { order: 3; }
}
</style>
