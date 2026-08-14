<!-- 自定义对话框（替代原生 prompt/confirm）：
     支持单行输入（input）与多行输入（textarea），确认/取消按钮。
     P3 修复：浏览器原生 prompt/confirm 在 CDP/自动化下会导致页面崩溃，
     改用本组件实现可视化弹窗，同时用于删除确认（danger 样式）。 -->
<script setup>
import { onMounted, ref, watch } from 'vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  title: { type: String, default: '' },
  message: { type: String, default: '' },
  inputLabel: { type: String, default: '' },   // 设置后显示单行输入框
  inputValue: { type: String, default: '' },
  textareaLabel: { type: String, default: '' }, // 设置后显示多行输入框
  textareaValue: { type: String, default: '' },
  okText: { type: String, default: '确认' },
  cancelText: { type: String, default: '取消' },
  danger: { type: Boolean, default: false },    // 危险操作（删除）红色确认
})

const emit = defineEmits(['confirm', 'cancel'])

const input = ref('')
const textarea = ref('')

watch(
  () => props.visible,
  (v) => {
    if (v) {
      input.value = props.inputValue || ''
      textarea.value = props.textareaValue || ''
    }
  },
)

function onKeydown(e) {
  if (e.key === 'Escape') emit('cancel')
  if (e.key === 'Enter' && !props.textareaLabel) confirmIt()
}
function confirmIt() {
  if (props.textareaLabel) emit('confirm', textarea.value)
  else emit('confirm', input.value)
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="modal-mask" @click.self="emit('cancel')">
      <div class="modal-dialog" :class="{ danger }" @keydown="onKeydown">
        <div class="modal-title">{{ title }}</div>
        <p v-if="message" class="modal-message">{{ message }}</p>
        <label v-if="inputLabel" class="muted">{{ inputLabel }}
          <input v-model="input" type="text" autofocus style="width:100%" @keydown="onKeydown" />
        </label>
        <label v-if="textareaLabel" class="muted">{{ textareaLabel }}
          <textarea v-model="textarea" rows="4" style="width:100%" @keydown="onKeydown"></textarea>
        </label>
        <div class="row mt8" style="justify-content:flex-end">
          <button class="small" @click="emit('cancel')">{{ cancelText }}</button>
          <button class="small" :class="danger ? 'danger' : 'primary'" @click="confirmIt">{{ okText }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.modal-mask {
  position: fixed; inset: 0; background: rgba(0,0,0,.45);
  display: flex; align-items: center; justify-content: center; z-index: 2000;
}
.modal-dialog {
  background: var(--bg, #fff); color: var(--text, #222);
  border: 1px solid var(--border, #ddd); border-radius: 12px;
  padding: 18px; width: min(92vw, 420px); box-shadow: 0 8px 30px rgba(0,0,0,.25);
}
.modal-dialog.danger { border-color: #e5534b; }
.modal-title { font-weight: 700; font-size: 1.05em; margin-bottom: 8px; }
.modal-message { margin: 0 0 10px; color: var(--text-soft, #666); }
</style>
