<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { useLibraryStore } from '../stores/library'

const store = useLibraryStore()

const tab = ref('general')
const settings = ref(null)
const saving = ref(false)
const savedMsg = ref('')
const floors = ref([])

// 技能审阅
const skills = ref([])
const skillDetail = ref(null)

// 蒸馏确认队列
const distillQueue = ref([])

// 表单模型
const form = ref({
  modelscope: { base_url: '', chat_model: '', distill_model: '', embed_model: '', api_key: '' },
  ollama: { base_url: '', enabled: false, model: '' },
  purchase: { max_daily_purchase: 5, no_video_unless_hot: true },
  prefs: { default_mode: 'half', max_daily_purchase: 5 },
})

onMounted(async () => {
  await loadAll()
})

async function loadAll() {
  try {
    settings.value = await api.settings()
    form.value.modelscope = { ...settings.value.modelscope, api_key: '' }
    form.value.ollama = { ...settings.value.ollama }
    form.value.purchase = { ...settings.value.purchase }
    form.value.prefs = { ...settings.value.prefs }
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
  await loadFloors()
  await loadSkills()
  await loadDistillQueue()
}

async function loadFloors() {
  try { floors.value = (await api.floors()).floors } catch { floors.value = [] }
}
async function loadSkills() {
  try { skills.value = (await api.skills('reviewing')).skills || [] } catch { skills.value = [] }
}
async function loadDistillQueue() {
  try {
    const bs = await api.books()
    distillQueue.value = bs.books.filter((b) => b.distill_status === 'awaiting')
  } catch { distillQueue.value = [] }
}

async function save() {
  saving.value = true
  savedMsg.value = ''
  try {
    settings.value = await api.saveSettings({
      modelscope: form.value.modelscope,
      ollama: form.value.ollama,
      purchase: form.value.purchase,
      prefs: form.value.prefs,
    })
    savedMsg.value = '✅ 设置已保存'
    setTimeout(() => (savedMsg.value = ''), 3000)
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
  finally { saving.value = false }
}

const modeText = {
  full: '全托管：AI 自动完成入馆/上架，主人仅审阅大事',
  half: '半托管：AI 建议，主人确认（推荐）',
  manual: '全手动：所有写操作都需主人确认',
}

// ------- 楼层编辑 -------
async function addFloor() {
  const name = prompt('新楼层名称：')
  if (!name) return
  try { await api.createFloor({ name }); await loadFloors() } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}
async function renameFloor(f) {
  const name = prompt('楼层名称：', f.name)
  if (!name || name === f.name) return
  try { await api.updateFloor(f.floor_id, { name }); await loadFloors() } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}
async function delFloor(f) {
  if (!confirm(`删除楼层 ${f.code} ${f.name}？（有书的楼层会被拒绝）`)) return
  try { await api.deleteFloor(f.floor_id); await loadFloors() } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}
async function addRoom(floor) {
  const name = prompt(`在 ${floor.name} 新建房间：`)
  if (!name) return
  try { await api.createRoom({ floor_id: floor.floor_id, name }); await loadFloors() } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}
async function addShelf(floor, room) {
  const name = prompt(`在 ${room.name} 新建书架：`)
  if (!name) return
  try { await api.createShelf({ room_id: room.room_id, name }); await loadFloors() } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}
async function delRoom(room) {
  if (!confirm(`删除房间 ${room.name}？`)) return
  try { await api.deleteRoom(room.room_id); await loadFloors() } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}
async function delShelf(shelf) {
  if (!confirm(`删除书架 ${shelf.name}？`)) return
  try { await api.deleteShelf(shelf.shelf_id); await loadFloors() } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}

// ------- 技能审阅 -------
async function openSkill(id) {
  try { skillDetail.value = await api.skill(id) } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}
async function approveSkill(id) {
  try { await api.approveSkill(id); store.toast('✅ 已批准', 'info'); await loadSkills(); skillDetail.value = null } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}
async function rejectSkill(id) {
  const reason = prompt('拒绝原因（可附改进建议）：')
  if (reason === null) return
  try { await api.rejectSkill(id, reason); store.toast('已拒绝', 'info'); await loadSkills(); skillDetail.value = null } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}

// ------- 蒸馏确认 -------
async function distillDecision(book, decision) {
  try {
    await api.distillConfirm(book.book_id, decision)
    store.toast(`蒸馏：${decision}`, 'info')
    await loadDistillQueue()
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
}
</script>

<template>
  <div>
    <h1 class="page-title">⚙ 设置</h1>
    <p class="page-sub">API、采集规则、托管程度、楼层结构、技能审阅与蒸馏确认</p>

    <div class="row mb8">
      <button class="small" :class="{ primary: tab === 'general' }" @click="tab = 'general'">基础</button>
      <button class="small" :class="{ primary: tab === 'floors' }" @click="tab = 'floors'">楼层编辑</button>
      <button class="small" :class="{ primary: tab === 'skills' }" @click="tab = 'skills'">
        技能审阅
        <span v-if="skills.length" class="pending-badge">{{ skills.length }}</span>
      </button>
      <button class="small" :class="{ primary: tab === 'distill' }" @click="tab = 'distill'">
        蒸馏确认
        <span v-if="distillQueue.length" class="pending-badge">{{ distillQueue.length }}</span>
      </button>
    </div>

    <!-- 基础设置 -->
    <template v-if="tab === 'general'">
      <div class="card mb16">
        <h3 style="margin:0 0 8px">🔌 API 配置</h3>
        <div class="grid" style="grid-template-columns:1fr 1fr">
          <label class="muted">ModelScope base_url
            <input type="text" v-model="form.modelscope.base_url" />
          </label>
          <label class="muted">聊天模型（chat_model）
            <input type="text" v-model="form.modelscope.chat_model" />
          </label>
          <label class="muted">蒸馏模型（distill_model）
            <input type="text" v-model="form.modelscope.distill_model" />
          </label>
          <label class="muted">嵌入模型（embed_model）
            <input type="text" v-model="form.modelscope.embed_model" />
          </label>
          <label class="muted">API key（留空 = 不修改；已配置：{{ settings?.modelscope?.api_key_set ? '是' : '否' }}）
            <input type="password" v-model="form.modelscope.api_key" :placeholder="settings?.modelscope?.api_key_masked || ''" />
          </label>
          <label class="muted">Ollama 启用
            <input type="checkbox" v-model="form.ollama.enabled" style="width:auto" />
          </label>
          <label class="muted">Ollama base_url
            <input type="text" v-model="form.ollama.base_url" />
          </label>
          <label class="muted">Ollama model
            <input type="text" v-model="form.ollama.model" />
          </label>
        </div>
      </div>

      <div class="card mb16">
        <h3 style="margin:0 0 8px">📥 采集规则</h3>
        <div class="grid" style="grid-template-columns:1fr 1fr">
          <label class="muted">每日采购条数（配额）
            <input type="number" v-model.number="form.purchase.max_daily_purchase" min="1" max="20" />
          </label>
          <label class="muted" style="display:flex;align-items:center;gap:6px">
            <input type="checkbox" v-model="form.purchase.no_video_unless_hot" style="width:auto" />
            视频内容除非热度高否则不采集
          </label>
        </div>
      </div>

      <div class="card mb16">
        <h3 style="margin:0 0 8px">🎛 托管程度</h3>
        <input type="range" min="0" max="2" step="1" v-model.number="form.prefs.default_mode" style="width:100%" />
        <div class="row" style="justify-content:space-between;font-size:0.85em">
          <span :class="{ 'badge': form.prefs.default_mode === 'full' }">全托管</span>
          <span :class="{ 'badge': form.prefs.default_mode === 'half' }">半托管</span>
          <span :class="{ 'badge': form.prefs.default_mode === 'manual' }">全手动</span>
        </div>
        <p class="muted mt8">{{ modeText[form.prefs.default_mode] }}</p>
      </div>

      <button class="primary" @click="save" :disabled="saving">{{ saving ? '保存中…' : '💾 保存设置' }}</button>
      <span v-if="savedMsg" class="muted" style="margin-left:10px">{{ savedMsg }}</span>
    </template>

    <!-- 楼层编辑 -->
    <template v-else-if="tab === 'floors'">
      <div class="card">
        <div class="row mb8">
          <h3 style="margin:0">🏢 楼层结构</h3>
          <span class="spacer"></span>
          <button class="small primary" @click="addFloor">＋ 新建楼层</button>
        </div>
        <div v-for="f in floors" :key="f.floor_id" class="mb16" style="border:1px solid var(--border);border-radius:10px;padding:10px">
          <div class="row">
            <b>{{ f.code }}</b>
            <span class="grow">{{ f.name }}</span>
            <button class="small" @click="renameFloor(f)">改名</button>
            <button class="small" @click="addRoom(f)">＋ 房间</button>
            <button class="small danger" @click="delFloor(f)">删除</button>
          </div>
          <div v-for="rm in (f.rooms || [])" :key="rm.room_id" style="margin-left:26px;margin-top:6px">
            <div class="row">
              <span class="grow">📦 {{ rm.name }}</span>
              <button class="small" @click="addShelf(f, rm)">＋ 书架</button>
              <button class="small danger" @click="delRoom(rm)">删除</button>
            </div>
            <div v-for="sh in (rm.shelves || [])" :key="sh.shelf_id" style="margin-left:26px;margin-top:4px" class="row">
              <span class="grow muted">📚 {{ sh.name }}</span>
              <button class="small danger" @click="delShelf(sh)">删除</button>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- 技能审阅 -->
    <template v-else-if="tab === 'skills'">
      <div class="grid" style="grid-template-columns: 1fr 1fr; align-items:start">
        <div class="card">
          <h3 style="margin:0 0 8px">🧪 待审阅技能（{{ skills.length }}）</h3>
          <div v-if="!skills.length" class="empty">没有待审阅的技能</div>
          <div v-for="s in skills" :key="s.skill_id" class="row" style="padding:8px 0;border-bottom:1px solid var(--border)">
            <div class="grow">
              <b>{{ s.name }}</b>
              <div class="muted">{{ s.skill_id }} · 拒绝 {{ s.reject_count || 0 }} 次</div>
            </div>
            <button class="small" @click="openSkill(s.skill_id)">查看</button>
          </div>
        </div>
        <div v-if="skillDetail" class="card">
          <h3 style="margin:0 0 8px">{{ skillDetail.name }}</h3>
          <div class="muted mb8">{{ skillDetail.skill_id }} · {{ skillDetail.source_book_id }}</div>
          <pre style="background:var(--bg-soft);padding:10px;border-radius:8px;white-space:pre-wrap;max-height:40vh;overflow-y:auto;font-size:0.85em">{{ skillDetail.content }}</pre>
          <div class="row mt8">
            <button class="small primary" @click="approveSkill(skillDetail.skill_id)">✅ 批准</button>
            <button class="small danger" @click="rejectSkill(skillDetail.skill_id)">✖ 拒绝</button>
            <button class="small" @click="skillDetail = null">关闭</button>
          </div>
        </div>
      </div>
    </template>

    <!-- 蒸馏确认 -->
    <template v-else-if="tab === 'distill'">
      <div class="card">
        <h3 style="margin:0 0 8px">🔬 待主人确认的蒸馏（{{ distillQueue.length }}）</h3>
        <div v-if="!distillQueue.length" class="empty">没有待确认的蒸馏</div>
        <div v-for="b in distillQueue" :key="b.book_id" class="row" style="padding:8px 0;border-bottom:1px solid var(--border)">
          <div class="grow">
            <b>{{ b.title }}</b>
            <div class="muted">蒸馏阶段待确认 · {{ b.book_id }}</div>
          </div>
          <button class="small primary" @click="distillDecision(b, 'continue')">✅ 继续</button>
          <button class="small" @click="distillDecision(b, 'skip')">⏭ 跳过</button>
          <button class="small danger" @click="distillDecision(b, 'cancel')">✖ 取消</button>
        </div>
      </div>
    </template>
  </div>
</template>
