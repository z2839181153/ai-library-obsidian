<!-- P6-3 配置向导：选供应商 → 选模型 → 填 key（三步傻瓜式）
     P6-4 增加第 4 步：🏢 自定义楼层（可选）——默认 4 标准楼层可增删改名后进入
     全屏遮罩对话框（自定义，不用原生 prompt/confirm）。
     流程：加载预设库（GET /api/providers）→ 供应商卡片 → 模型下拉
           → key 输入 + 测试连接（POST /api/settings/test-connection）
           → 保存并应用（POST /api/settings/apply-provider）→ 自定义楼层（可跳过） -->
<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { useLibraryStore } from '../stores/library'

const props = defineProps({
  visible: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'applied'])

const store = useLibraryStore()

const step = ref(1)
const providers = ref({})
const current = ref(null)          // /api/providers → current 段
const selectedProvider = ref('')
const chatModel = ref('')
const distillModel = ref('')
const embedModel = ref('')
const apiKey = ref('')
const embedApiKey = ref('')
const testing = ref(false)
const applying = ref(false)
const testResults = ref(null)
const loaded = ref(false)

// ---- P6-4 第 4 步：自定义楼层（可选） ----
const wizardFloors = ref([])       // GET /api/floors → floors
const wzEditing = ref(null)        // { id, name } 内联改名
const wzEditValue = ref('')
const wzAdding = ref(false)        // 新建楼层行
const wzAddValue = ref('')
const wzConfirmDel = ref(null)     // 待二次确认删除的 floor_id

const curProvider = computed(() => providers.value[selectedProvider.value] || null)

// 该供应商可用的嵌入模型；无 → 自动 fallback ModelScope
const embedModels = computed(() => curProvider.value?.embed_models || [])
// 嵌入走本供应商（用户显式选了嵌入模型）→ 需要 embed key
const embedUsesOwnProvider = computed(() => !!embedModel.value)

const existingChatKey = computed(() => current.value?.chat_key_masked || '')
const existingEmbedKey = computed(() => current.value?.embed_key_masked || '')

function tagClass(t) {
  if (t === '免费额度') return 'gold'
  if (t === '本地') return 'red'
  return ''
}

async function load() {
  try {
    const d = await api.providers()
    providers.value = d.providers || {}
    current.value = d.current
    loaded.value = true
    // 预填当前生效供应商
    if (d.current?.provider_id && providers.value[d.current.provider_id]) {
      selectedProvider.value = d.current.provider_id
      const p = providers.value[selectedProvider.value]
      chatModel.value = (p.chat_models || []).includes(d.current.chat_model)
        ? d.current.chat_model : (p.chat_models || [])[0] || ''
      distillModel.value = (p.distill_models || []).includes(d.current.distill_model)
        ? d.current.distill_model : (p.distill_models || [])[0] || chatModel.value
      embedModel.value = (p.embed_models || []).includes(d.current.embed_model)
        ? d.current.embed_model : (p.embed_models || [])[0] || ''
    }
  } catch (e) {
    store.toast(`❌ 加载供应商失败：${e.message}`, 'error')
  }
}

watch(
  () => props.visible,
  (v) => {
    if (v) {
      step.value = 1
      testResults.value = null
      apiKey.value = ''
      embedApiKey.value = ''
      wizardFloors.value = []
      wzEditing.value = null
      wzAdding.value = false
      wzConfirmDel.value = null
      load()
    }
  },
)

function selectProvider(pid) {
  selectedProvider.value = pid
  const p = providers.value[pid]
  chatModel.value = (p.chat_models || [])[0] || ''
  distillModel.value = (p.distill_models || [])[0] || chatModel.value
  embedModel.value = (p.embed_models || [])[0] || ''
}

function goStep2() {
  if (!selectedProvider.value) return
  // 重新进入时若当前供应商已有值则保留
  step.value = 2
}
function goStep3() {
  if (!chatModel.value) { store.toast('请选择聊天模型', 'error'); return }
  if (!distillModel.value) distillModel.value = chatModel.value
  step.value = 3
}

function close() { emit('close') }

async function runTest() {
  testing.value = true
  testResults.value = null
  const p = curProvider.value
  try {
    const payload = {
      base_url: p.base_url,
      chat_model: chatModel.value,
      embed_model: embedModel.value,
      embed_base_url: embedModel.value ? p.base_url : '',
      api_key: apiKey.value,                       // 留空 → 后端用已配置 key 实测
      embed_api_key: embedApiKey.value || apiKey.value,
      is_ollama: !!p.local,
    }
    const r = await api.testConnection(payload)
    testResults.value = r.results
    if (r.ok) store.toast('✅ 连接测试通过', 'info')
    else store.toast('❌ 连接测试未通过', 'error')
  } catch (e) {
    testResults.value = { chat: { ok: false, message: e.message } }
    store.toast(`❌ 测试失败：${e.message}`, 'error')
  } finally {
    testing.value = false
  }
}

async function apply() {
  const p = curProvider.value
  if (!p) return
  if (!p.local && !apiKey.value && !embedApiKey.value && !current.value?.chat_key_set) {
    store.toast('请填写 API key（或先选择 Ollama 本地模式）', 'error')
    return
  }
  applying.value = true
  try {
    const payload = {
      provider: selectedProvider.value,
      chat_model: chatModel.value,
      distill_model: distillModel.value || chatModel.value,
      embed_model: embedModel.value,           // 空 = 自动 ModelScope 免费
      api_key: apiKey.value,
      embed_api_key: embedApiKey.value,
      ollama_enabled: !!p.local,
    }
    const s = await api.applyProvider(payload)
    store.toast(`✅ 配置已保存（${p.name}）`, 'info')
    emit('applied', s)
    // P6-4：保存成功后进入「自定义楼层」可选步骤（可跳过）
    step.value = 4
    loadWizardFloors()
  } catch (e) {
    store.toast(`❌ ${e.message}`, 'error')
  } finally {
    applying.value = false
  }
}

// ---- P6-4 自定义楼层操作 ----
async function loadWizardFloors() {
  try { wizardFloors.value = (await api.floors()).floors || [] } catch { wizardFloors.value = [] }
}
function startWzEdit(f) {
  wzEditing.value = { id: f.floor_id, name: f.name }
  wzEditValue.value = f.name
}
function cancelWzEdit() { wzEditing.value = null; wzEditValue.value = '' }
async function saveWzEdit() {
  const name = wzEditValue.value.trim()
  const ed = wzEditing.value
  if (!ed || !name) { cancelWzEdit(); return }
  try {
    await api.updateFloor(ed.id, { name })
    store.toast('✅ 已改名', 'info')
    await loadWizardFloors()
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
  finally { cancelWzEdit() }
}
function toggleWzAdd() {
  wzAdding.value = !wzAdding.value
  if (wzAdding.value) wzAddValue.value = ''
}
function cancelWzAdd() { wzAdding.value = false; wzAddValue.value = '' }
async function confirmWzAdd() {
  const name = wzAddValue.value.trim()
  if (!name) return
  try {
    await api.createFloor({ name })
    store.toast('✅ 已新建楼层', 'info')
    await loadWizardFloors()
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
  finally { cancelWzAdd() }
}
function askWzDel(f) { wzConfirmDel.value = f.floor_id }
function cancelWzDel() { wzConfirmDel.value = null }
async function confirmWzDel(f) {
  try {
    await api.deleteFloor(f.floor_id)
    store.toast('✅ 已删除楼层', 'info')
    await loadWizardFloors()
  } catch (e) { store.toast(`❌ ${e.message}`, 'error') }
  finally { cancelWzDel() }
}
function finishWizard() { emit('close') }
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="wz-mask" @click.self="close">
      <div class="wz-dialog">
        <div class="wz-head">
          <div class="wz-title">⚙️ AI 图书馆 · 配置向导</div>
          <div class="wz-steps">
            <span :class="{ active: step >= 1 }">① 选供应商</span>
            <span class="wz-arrow">→</span>
            <span :class="{ active: step >= 2 }">② 选模型</span>
            <span class="wz-arrow">→</span>
            <span :class="{ active: step >= 3 }">③ 填 API key</span>
            <span class="wz-arrow">→</span>
            <span :class="{ active: step >= 4 }">④ 自定义楼层 <span class="wz-opt">可选</span></span>
          </div>
        </div>

        <!-- 第 1 步：选供应商 -->
        <div v-if="step === 1" class="wz-body">
          <div v-if="!loaded" class="loading">加载供应商…</div>
          <div v-else class="wz-provider-grid">
            <div v-for="(p, pid) in providers" :key="pid" class="wz-provider"
                 :class="{ sel: selectedProvider === pid }" @click="selectProvider(pid)">
              <div class="wz-provider-logo">{{ p.logo }}</div>
              <div class="wz-provider-name">{{ p.name }}</div>
              <div class="wz-provider-tags">
                <span v-for="t in p.tags" :key="t" class="badge" :class="tagClass(t)">{{ t }}</span>
              </div>
              <div class="wz-provider-hint">{{ p.hint }}</div>
            </div>
          </div>
          <div class="row mt8" style="justify-content:flex-end">
            <button class="small primary" :disabled="!selectedProvider" @click="goStep2">下一步 →</button>
          </div>
        </div>

        <!-- 第 2 步：选模型 -->
        <div v-else-if="step === 2" class="wz-body">
          <div class="wz-summary">
            {{ curProvider?.logo }} <b>{{ curProvider?.name }}</b>
            <span class="muted" style="margin-left:8px">{{ curProvider?.base_url }}</span>
          </div>
          <label class="muted">聊天模型（管理员问答 / 图书卡片 / 采购）
            <select v-model="chatModel">
              <option v-for="m in curProvider?.chat_models || []" :key="m" :value="m">{{ m }}</option>
            </select>
          </label>
          <label class="muted">蒸馏模型（技能蒸馏）
            <select v-model="distillModel">
              <option v-for="m in (curProvider?.distill_models?.length ? curProvider.distill_models : curProvider?.chat_models || [])" :key="m" :value="m">{{ m }}</option>
            </select>
          </label>
          <label v-if="embedModels.length" class="muted">嵌入模型（语义检索 / 星图）
            <select v-model="embedModel">
              <option v-for="m in embedModels" :key="m" :value="m">{{ m }}</option>
            </select>
          </label>
          <div v-else class="wz-fallback">
            🧩 本供应商没有嵌入接口 —— 嵌入将自动使用 <b>ModelScope 免费额度</b>（Qwen3-Embedding-0.6B），零成本。
          </div>
          <div class="row mt8" style="justify-content:space-between">
            <button class="small" @click="step = 1">← 上一步</button>
            <button class="small primary" :disabled="!chatModel" @click="goStep3">下一步 →</button>
          </div>
        </div>

        <!-- 第 3 步：填 key + 测试 -->
        <div v-else-if="step === 3" class="wz-body">
          <div class="wz-summary">
            已选：{{ curProvider?.logo }} <b>{{ curProvider?.name }}</b> ·
            <code>{{ chatModel }}</code><span v-if="embedModel"> + <code>{{ embedModel }}</code></span>
          </div>

          <template v-if="!curProvider?.local">
            <label class="muted">API key（聊天 / 蒸馏）
              <input type="password" v-model="apiKey"
                     :placeholder="existingChatKey ? `已配置 ${existingChatKey}（留空不修改）` : 'sk-…'" />
            </label>
            <a v-if="curProvider?.key_url" :href="curProvider.key_url" target="_blank" rel="noopener" class="link-btn">🔑 去获取 key ↗</a>
            <label v-if="embedUsesOwnProvider" class="muted">嵌入 API key
              <input type="password" v-model="embedApiKey"
                     :placeholder="existingEmbedKey ? `已配置 ${existingEmbedKey}` : '留空 = 用上面的 key'" />
            </label>
            <div class="muted" style="font-size:0.85em;margin-top:6px">
              🔒 key 只保存在本机 <code>data/secrets.json</code>（不入库、不上传）。免费 API 调用时文本会经手对应服务商。
            </div>
          </template>
          <div v-else class="wz-fallback">
            🦙 本地 Ollama 无需 API key —— 请确保已安装 Ollama 并已拉取模型（如 <code>qwen2.5:7b</code>、<code>nomic-embed-text</code>）。数据绝不外传。
          </div>

          <div class="row mt8">
            <button class="small" @click="runTest" :disabled="testing">
              {{ testing ? '测试中…' : '🧪 测试连接' }}
            </button>
            <button class="small primary" @click="apply" :disabled="applying">
              {{ applying ? '保存中…' : '💾 保存并应用' }}
            </button>
          </div>
          <div v-if="testResults" class="wz-results">
            <div v-for="(r, k) in testResults" :key="k" class="wz-result" :class="{ ok: r.ok }">
              {{ r.ok ? '✅' : '❌' }} {{ r.message }}
            </div>
          </div>

          <div class="row mt8" style="justify-content:space-between">
            <button class="small" @click="step = 2">← 上一步</button>
            <button class="small" @click="close">关闭</button>
          </div>
        </div>

        <!-- 第 4 步（P6-4）：🏢 自定义楼层（可选） -->
        <div v-else-if="step === 4" class="wz-body">
          <div class="wz-summary">
            🏢 <b>自定义楼层</b>（可选）—— 楼层按<b>来源媒介</b>组织藏书（如 电子书 / 网页公众号 / 聊天记录 / 视频转写）。
            可增删改名后再进入；之后也随时可在「设置 → 楼层编辑」修改。
          </div>
          <div class="row wz-floor-head">
            <b>当前楼层</b>
            <span class="spacer"></span>
            <button class="small primary" @click="toggleWzAdd">＋ 新建楼层</button>
          </div>
          <div v-if="wzAdding" class="wz-floor row">
            <input type="text" v-model="wzAddValue" placeholder="新楼层名称（如：扫描件）" class="grow"
                   @keyup.enter="confirmWzAdd" @keyup.esc="cancelWzAdd" autofocus />
            <button class="small primary" @click="confirmWzAdd">创建</button>
            <button class="small" @click="cancelWzAdd">取消</button>
          </div>
          <div v-for="f in wizardFloors" :key="f.floor_id" class="wz-floor row">
            <b class="wz-code">{{ f.code }}</b>
            <template v-if="wzEditing && wzEditing.id === f.floor_id">
              <input type="text" v-model="wzEditValue" class="grow"
                     @keyup.enter="saveWzEdit" @keyup.esc="cancelWzEdit" autofocus />
              <button class="small primary" @click="saveWzEdit">✓ 保存</button>
              <button class="small" @click="cancelWzEdit">✗</button>
            </template>
            <template v-else>
              <span class="grow wz-floor-name">{{ f.name }}</span>
              <button class="small" @click="startWzEdit(f)">改名</button>
              <button v-if="wzConfirmDel === f.floor_id" class="small danger" @click="confirmWzDel(f)">确认删除？</button>
              <button v-else class="small danger" @click="askWzDel(f)">删除</button>
            </template>
          </div>
          <div class="muted" style="font-size:0.85em;margin-top:8px">
            提示：有书的楼层不能删除；房间/书架等更细层级在设置页管理。
          </div>
          <div class="row mt8" style="justify-content:space-between">
            <button class="small" @click="step = 3">← 上一步</button>
            <div>
              <button class="small" @click="finishWizard">跳过</button>
              <button class="small primary" @click="finishWizard">🚀 完成，开始使用</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.wz-mask {
  position: fixed; inset: 0; background: rgba(0,0,0,.5);
  display: flex; align-items: center; justify-content: center; z-index: 3000;
  padding: 16px;
}
.wz-dialog {
  background: var(--bg, #fff); color: var(--text, #222);
  border: 1px solid var(--border, #ddd); border-radius: 14px;
  width: min(94vw, 760px); max-height: 90vh; overflow-y: auto;
  box-shadow: 0 10px 40px rgba(0,0,0,.3);
}
.wz-head { padding: 16px 20px 10px; border-bottom: 1px solid var(--border, #ddd); }
.wz-title { font-weight: 700; font-size: 1.1em; margin-bottom: 10px; }
.wz-steps { display: flex; gap: 6px; align-items: center; font-size: 0.9em; color: var(--ink-soft, #888); }
.wz-steps .active { color: var(--accent, #3d5a45); font-weight: 700; }
.wz-opt {
  font-size: 0.75em; font-weight: 400; color: var(--ink-soft, #999);
  border: 1px solid var(--border, #ccc); border-radius: 8px; padding: 0 5px; margin-left: 2px;
}
.wz-arrow { color: #bbb; }
.wz-floor-head { margin: 4px 0 6px; }
.wz-floor {
  border: 1px solid var(--border, #ddd); border-radius: 8px; padding: 6px 10px;
  margin-top: 6px; background: var(--bg-soft, #f7f7f4);
}
.wz-floor input { border: 1px solid var(--border, #ccc); border-radius: 6px; padding: 4px 8px; }
.wz-code { min-width: 34px; margin-right: 8px; color: var(--ink-soft, #666); }
.wz-floor-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wz-body { padding: 16px 20px 20px; }
.wz-provider-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; }
.wz-provider {
  border: 1px solid var(--border, #ddd); border-radius: 12px; padding: 12px;
  cursor: pointer; transition: all .15s; background: var(--bg-soft, #f7f7f4);
}
.wz-provider:hover { border-color: var(--accent, #3d5a45); transform: translateY(-1px); }
.wz-provider.sel { border-color: var(--accent, #3d5a45); background: #eef4ee; box-shadow: 0 0 0 2px rgba(61,90,69,.15); }
.wz-provider-logo { font-size: 1.6em; }
.wz-provider-name { font-weight: 700; margin: 4px 0; }
.wz-provider-tags { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 6px; }
.wz-provider-hint { font-size: 0.8em; color: var(--ink-soft, #666); line-height: 1.5; }
.wz-summary {
  background: var(--bg-soft, #f7f7f4); border-radius: 8px; padding: 8px 12px;
  margin-bottom: 12px; font-size: 0.9em;
}
.wz-summary code { background: #eee; border-radius: 4px; padding: 1px 5px; }
.wz-fallback {
  background: #f6ecd4; color: #7a6230; border-radius: 8px; padding: 10px 12px;
  font-size: 0.9em; margin: 8px 0;
}
.wz-results { margin-top: 10px; }
.wz-result {
  padding: 6px 10px; border-radius: 6px; font-size: 0.9em; margin-top: 4px;
  background: #f6dcd8; color: var(--danger, #a4433a);
}
.wz-result.ok { background: #e6f0e6; color: var(--accent, #3d5a45); }
label.muted { display: block; margin-top: 10px; }
label.muted input, label.muted select { width: 100%; margin-top: 4px; }
</style>
