<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useLibraryStore } from '../stores/library'
import markdownit from 'markdown-it'

const md = markdownit({ linkify: true, breaks: true })

const store = useLibraryStore()
const router = useRouter()

const convs = ref([])
const activeCv = ref(null)
const messages = ref([])
const input = ref('')
const sending = ref(false)

const tab = ref('chat')        // chat | search | actions
const searchQ = ref('')
const searchResults = ref([])
const searchBusy = ref(false)
const actions = ref([])

const chatLog = ref(null)

// P4-5 流式聊天：WS 消息处理
let wsOff = null
let streamMsgIndex = -1        // 当前流式 assistant 消息在 messages 中的下标

onMounted(async () => {
  await loadConvs()
  await loadActions()
  wsOff = store.onWSEvent(handleWSEvent)
})
onUnmounted(() => {
  if (wsOff) wsOff()
})

async function loadConvs() {
  try { convs.value = (await api.conversations()).conversations } catch { convs.value = [] }
}

async function openCv(cvId) {
  activeCv.value = cvId
  const d = await api.conversation(cvId)
  messages.value = d.messages
  scrollBottom()
}

// ------- P4-5 流式聊天 -------
function handleWSEvent(msg) {
  if (msg.type === 'chat_start') {
    messages.value.push({ role: 'assistant', content: '', refs: msg.refs || [], streaming: true })
    streamMsgIndex = messages.value.length - 1
    scrollBottom()
  } else if (msg.type === 'chat_token') {
    if (streamMsgIndex >= 0) {
      messages.value[streamMsgIndex].content += msg.delta || ''
      scrollBottom()
    }
  } else if (msg.type === 'chat_done') {
    if (streamMsgIndex >= 0) {
      const m = messages.value[streamMsgIndex]
      m.streaming = false
      if (msg.cancelled) {
        m.content = m.content || '（已取消）'
      } else if (!m.content && msg.answer) {
        m.content = msg.answer
      }
      if (!(m.refs || []).length) m.refs = msg.refs || []
      if (msg.cv_id) activeCv.value = msg.cv_id
      streamMsgIndex = -1
    }
    sending.value = false
    loadConvs()
    scrollBottom()
  }
}

async function send() {
  const q = input.value.trim()
  if (!q || sending.value) return
  sending.value = true
  streamMsgIndex = -1
  messages.value.push({ role: 'user', content: q, refs: [] })
  input.value = ''
  scrollBottom()
  const sent = store.sendWS({
    type: 'ask_stream',
    content: q,
    top_k: 20,
    cv_id: activeCv.value || null,
  })
  if (!sent) {
    sending.value = false
    messages.value.push({ role: 'assistant', content: '（WS 未连接，无法发送；请稍候重试）', refs: [] })
    scrollBottom()
  }
}

function cancelAsk() {
  // 取消流式：后端回 chat_done(cancelled=true)
  store.sendWS({ type: 'cancel' })
}

function scrollBottom() {
  nextTick(() => {
    if (chatLog.value) chatLog.value.scrollTop = chatLog.value.scrollHeight
  })
}

function renderMdBold(text) {
  // 简化渲染：链接化 [[catalog/bk_xx]] 引用
  return md.render(text)
}
function refHref(link) {
  const m = /bk_[a-z0-9]+/.exec(link || '')
  // P4-3：阅览室高亮（hl= 搜索词）
  const q = searchQ.value?.trim()
  return m ? `/book/${m[0]}${q ? '?hl=' + encodeURIComponent(q) : ''}` : '#'
}

async function doSearch() {
  if (!searchQ.value.trim()) return
  searchBusy.value = true
  try {
    const r = await api.search(searchQ.value, 20)
    searchResults.value = r.books || []
  } catch (e) {
    store.toast(`❌ ${e.message}`, 'error')
  } finally { searchBusy.value = false }
}

async function loadActions() {
  try { actions.value = (await api.actions(100)).actions } catch { actions.value = [] }
}
async function undo(act) {
  try {
    await api.undoAction(act.act_id)
    store.toast('已撤销', 'info')
    await loadActions()
    await store.refreshDashboard()
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}

const groupedConvs = computed(() => {
  const g = {}
  for (const c of convs.value) {
    (g[c.group] = g[c.group] || []).push(c)
  }
  return g
})
</script>

<template>
  <div>
    <h1 class="page-title">💬 管理员</h1>
    <p class="page-sub">与馆藏对话、搜索索引、操作账本</p>

    <div class="grid" style="grid-template-columns: 260px 1fr; align-items:start">
      <!-- 左：对话历史 -->
      <div class="card" style="max-height:70vh;overflow-y:auto">
        <h3 style="margin:0 0 8px">🗂 对话历史</h3>
        <div v-if="!convs.length" class="empty">还没有对话</div>
        <template v-for="(list, group) in groupedConvs" :key="group">
          <div class="muted mt8" style="font-size:0.85em">{{ group }}</div>
          <div
            v-for="c in list" :key="c.cv_id"
            class="floor-item" :class="{ active: activeCv === c.cv_id }"
            style="padding:6px 10px" @click="openCv(c.cv_id)"
          >
            <span class="grow" style="font-size:0.88em">{{ c.title || c.cv_id }}</span>
            <span class="muted" style="font-size:0.75em">{{ c.msg_count }}</span>
          </div>
        </template>
      </div>

      <!-- 右：Tab -->
      <div>
        <div class="row mb8">
          <button class="small" :class="{ primary: tab === 'chat' }" @click="tab = 'chat'">聊天</button>
          <button class="small" :class="{ primary: tab === 'search' }" @click="tab = 'search'">索引搜索</button>
          <button class="small" :class="{ primary: tab === 'actions' }" @click="tab = 'actions'">操作账本</button>
        </div>

        <!-- 聊天 -->
        <template v-if="tab === 'chat'">
          <div class="chat-log" ref="chatLog">
            <div v-if="!messages.length" class="empty">问点什么吧，比如「检索增强是什么？」</div>
            <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
              <span v-html="renderMdBold(m.content)"></span><span v-if="m.streaming" class="cursor">▍</span>
              <div v-if="(m.refs || []).length" class="refs">
                <router-link v-for="(r, j) in m.refs" :key="j" :to="refHref(r.link || '')" style="margin-right:8px">
                  📄 {{ r.title }}
                </router-link>
              </div>
            </div>
            <div v-if="sending && streamMsgIndex < 0" class="msg assistant">思考中…</div>
          </div>
          <div class="row mt8">
            <input type="text" v-model="input" placeholder="提问（Enter 发送）"
                   @keyup.enter="send" :disabled="sending" />
            <button v-if="!sending" class="primary" @click="send">发送</button>
            <button v-else class="danger" @click="cancelAsk">✖ 取消</button>
          </div>
          <div class="row mt8">
            <button class="small" :disabled="!activeCv"
                    @click="api.archiveConversation(activeCv).then(() => store.toast('已归档为书','info')).catch(e => store.toast('❌ '+e.message,'error'))">
              🗄 归档当前对话为书
            </button>
          </div>
        </template>

        <!-- 索引搜索 -->
        <template v-else-if="tab === 'search'">
          <div class="row">
            <input type="text" v-model="searchQ" placeholder="全站搜索（FTS5 + 向量）" @keyup.enter="doSearch" />
            <button class="primary" @click="doSearch" :disabled="searchBusy">搜索</button>
          </div>
          <div v-if="searchResults.length" class="card mt8">
            <div v-for="b in searchResults" :key="b.book_id" class="row" style="padding:8px 0;border-bottom:1px solid var(--border)">
              <div class="grow">
                <router-link :to="`/book/${b.book_id}?hl=${encodeURIComponent(searchQ.value.trim())}`"><b>{{ b.title }}</b></router-link>
                <div class="muted">{{ (b.snippet || '').slice(0, 140) }}</div>
                <div class="muted" style="font-size:0.8em">命中 {{ (b.hit_chunks || []).length }} 段 · {{ b.score ? b.score.toFixed(3) : '' }}</div>
              </div>
            </div>
          </div>
        </template>

        <!-- 操作账本 -->
        <template v-else-if="tab === 'actions'">
          <div class="card">
            <table class="tbl">
              <thead><tr><th>动作</th><th>目标</th><th>理由</th><th>时间</th><th></th></tr></thead>
              <tbody>
                <tr v-for="a in actions" :key="a.act_id">
                  <td><span class="badge">{{ a.action_type }}</span></td>
                  <td class="muted">{{ a.target_id }}</td>
                  <td class="muted">{{ a.reason }}</td>
                  <td class="muted" style="font-size:0.8em">{{ (a.created_at || '').slice(0, 16) }}</td>
                  <td>
                    <button v-if="a.status === 'done'" class="small" @click="undo(a)">↩ 撤销</button>
                    <span v-else class="muted">{{ a.status }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* P4-5 流式光标：逐字输出时在末尾闪烁 */
.cursor {
  display: inline-block;
  margin-left: 1px;
  color: var(--accent);
  animation: cursor-blink 1s step-start infinite;
}
@keyframes cursor-blink {
  50% { opacity: 0; }
}
</style>
