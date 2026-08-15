<!-- P5-2 批量入馆对话框：
     格式下拉（必选）+ 多文件选择/拖拽（>10 本截断提示）+ 提交前同格式校验。
     提交 POST /api/ingest/batch → 展示逐本结果（registered / duplicate / error），
     索引由后端后台任务 + WS 进度广播完成。 -->
<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api'

const props = defineProps({
  visible: { type: Boolean, default: false },
  preset: { type: Array, default: () => [] },   // Hall 拖入多文件时预填
})
const emit = defineEmits(['close', 'done'])

const MAX = 10
const FORMATS = [
  { value: 'markdown', label: 'Markdown（.md）', exts: ['.md', '.markdown'] },
  { value: 'text', label: '纯文本（.txt）', exts: ['.txt'] },
  { value: 'html', label: '网页/HTML（.html）', exts: ['.html', '.htm'] },
  { value: 'pdf', label: 'PDF 电子书（.pdf）', exts: ['.pdf'] },
]

const format = ref('')
const files = ref([])
const isPrivate = ref(false)
const uploading = ref(false)
const errorMsg = ref('')
const results = ref([])
const doneMsg = ref('')

function reset() {
  format.value = ''
  files.value = []
  isPrivate.value = false
  uploading.value = false
  errorMsg.value = ''
  results.value = []
  doneMsg.value = ''
}

watch(
  () => props.visible,
  (v) => {
    if (v) {
      reset()
      if (props.preset && props.preset.length) {
        files.value = props.preset.slice(0, MAX)
        if (props.preset.length > MAX) {
          errorMsg.value = `最多 ${MAX} 本，已自动截断（拖入 ${props.preset.length} 个）`
        }
      }
    }
  },
)

const acceptExts = computed(() => {
  const f = FORMATS.find((x) => x.value === format.value)
  return f ? f.exts.join(',') : ''
})
const formatLabel = computed(() => {
  const f = FORMATS.find((x) => x.value === format.value)
  return f ? f.label : ''
})

function onPick(e) {
  addFiles(Array.from(e.target.files || []))
  e.target.value = ''   // 允许重复选择同一文件
}
function onDrop(e) {
  e.preventDefault()
  addFiles(Array.from(e.dataTransfer?.files || []))
}
function addFiles(list) {
  if (!list.length) return
  errorMsg.value = ''
  const fs = [...files.value, ...list]
  if (fs.length > MAX) {
    errorMsg.value = `最多 ${MAX} 本，已自动截断（共选了 ${fs.length} 个文件）`
    files.value = fs.slice(0, MAX)
  } else {
    files.value = fs
  }
}
function removeFile(i) {
  files.value.splice(i, 1)
}

// 同格式校验（提交前完成）
const formatError = computed(() => {
  if (!format.value) return '请先选择格式'
  if (!files.value.length) return ''
  const f = FORMATS.find((x) => x.value === format.value)
  const bad = files.value.filter((file) => {
    const dot = file.name.lastIndexOf('.')
    const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : ''
    return !f.exts.includes(ext)
  })
  return bad.length
    ? `以下文件与所选格式（${f.label}）不符：${bad.map((x) => x.name).join('、')}`
    : ''
})

async function submit() {
  if (formatError.value || !files.value.length) return
  uploading.value = true
  errorMsg.value = ''
  results.value = []
  doneMsg.value = ''
  const fd = new FormData()
  fd.append('format', format.value)
  if (isPrivate.value) fd.append('private', 'true')
  files.value.forEach((f) => fd.append('files', f))
  try {
    const r = await api.ingestBatch(fd)
    results.value = r.results || []
    const ok = results.value.filter((x) => x.status === 'registered').length
    const dup = results.value.filter((x) => x.status === 'duplicate').length
    const err = results.value.filter((x) => x.status === 'error').length
    doneMsg.value = `✅ 入馆 ${ok} 本${dup ? `，重复 ${dup}` : ''}${err ? `，失败 ${err}` : ''}（索引后台进行中）`
    emit('done', r)
  } catch (err) {
    errorMsg.value = `❌ ${err.message}`
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="bim-mask" @click.self="emit('close')">
      <div class="bim-dialog">
        <div class="bim-title">📥 批量入馆（≤{{ MAX }} 本 / 次，同一格式）</div>

        <!-- 格式下拉（必选） -->
        <label class="muted">格式 <span style="color:#e5534b">*</span>
          <select v-model="format" style="width:100%">
            <option value="" disabled>请选择格式（一次只能上传同一格式）</option>
            <option v-for="f in FORMATS" :key="f.value" :value="f.value">{{ f.label }}</option>
          </select>
        </label>

        <!-- 选择 / 拖拽 -->
        <label class="bim-drop" @dragover.prevent @drop="onDrop">
          <input type="file" multiple style="display:none" :accept="acceptExts" @change="onPick" />
          <div class="bim-drop-inner">
            <div>📂 点击选择或把文件拖到这里</div>
            <div v-if="formatLabel" class="muted">当前格式：{{ formatLabel }}</div>
            <div v-else class="muted">（先选格式，再选文件）</div>
          </div>
        </label>

        <!-- 文件列表 -->
        <div v-if="files.length" class="bim-files">
          <div v-for="(f, i) in files" :key="i" class="bim-file">
            <span class="grow" :title="f.name">{{ f.name }}</span>
            <span class="badge">{{ (f.size / 1024).toFixed(0) }}KB</span>
            <button class="small" style="margin-left:8px" @click="removeFile(i)">✕</button>
          </div>
        </div>

        <label class="muted row" style="gap:6px;margin-top:8px">
          <input v-model="isPrivate" type="checkbox" /> 私密书（内容不发送 API）
        </label>

        <div v-if="formatError" class="bim-error">{{ formatError }}</div>
        <div v-if="errorMsg" class="bim-error">{{ errorMsg }}</div>
        <div v-if="uploading" class="muted mt8">上传登记中…</div>
        <div v-if="doneMsg" class="bim-ok mt8">{{ doneMsg }}</div>

        <!-- 逐本结果 -->
        <div v-if="results.length" class="bim-results">
          <div v-for="(r, i) in results" :key="i" class="bim-file">
            <span class="grow" :title="r.filename">{{ r.filename }}</span>
            <span v-if="r.status === 'registered'" class="badge green">✅ 已入馆</span>
            <span v-else-if="r.status === 'duplicate'" class="badge amber">📚 重复</span>
            <span v-else class="badge red">❌ {{ r.error }}</span>
          </div>
        </div>

        <div class="row mt8" style="justify-content:flex-end">
          <button class="small" @click="emit('close')">关闭</button>
          <button
            class="small primary"
            :disabled="uploading || !files.length || !!formatError"
            @click="submit"
          >
            {{ uploading ? '上传中…' : `入馆 ${files.length || ''} 本` }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.bim-mask {
  position: fixed; inset: 0; background: rgba(0,0,0,.45);
  display: flex; align-items: center; justify-content: center; z-index: 2000;
}
.bim-dialog {
  background: var(--bg, #fff); color: var(--text, #222);
  border: 1px solid var(--border, #ddd); border-radius: 12px;
  padding: 18px; width: min(94vw, 560px); max-height: 86vh; overflow-y: auto;
  box-shadow: 0 8px 30px rgba(0,0,0,.25);
}
.bim-title { font-weight: 700; font-size: 1.05em; margin-bottom: 12px; }
.bim-drop {
  display: block; margin-top: 10px; cursor: pointer;
  border: 2px dashed var(--border, #ccc); border-radius: 10px; padding: 16px;
  text-align: center; color: var(--ink-soft, #666);
}
.bim-drop-inner { pointer-events: none; }
.bim-files { margin-top: 10px; max-height: 200px; overflow-y: auto; }
.bim-file {
  display: flex; align-items: center; padding: 4px 6px;
  border-bottom: 1px solid var(--border, #eee); font-size: .92em;
}
.bim-error { margin-top: 8px; color: #e5534b; font-size: .9em; }
.bim-ok { color: var(--accent, #3d5a45); font-size: .92em; }
.bim-results { margin-top: 10px; }
.badge.green { color: #2e7d32; }
.badge.amber { color: #b26a00; }
.badge.red { color: #e5534b; }
</style>
