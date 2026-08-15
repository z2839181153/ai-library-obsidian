<script setup>
import { computed, ref } from 'vue'
import { api } from '../api'
import { useLibraryStore } from '../stores/library'
import ModalDialog from './ModalDialog.vue'

const props = defineProps({
  book: { type: Object, required: true },
})
const emit = defineEmits(['open', 'read', 'deleted'])

const store = useLibraryStore()
const confirmDelete = ref(false)
const deleting = ref(false)

const statusText = computed(() => ({
  incoming: '待分类',
  reviewing: '待确认',
  shelved: '已上架',
  indexed: '已索引',
  deleted: '已删除',
}[props.book.status] || props.book.status))

const statusClass = computed(() => `status-${props.book.status}`)

async function doDelete() {
  if (deleting.value) return
  deleting.value = true
  try {
    await api.deleteBook(props.book.book_id)
    store.toast(`🗑 《${props.book.title}》已删除（可在档案馆恢复 30 天）`, 'info')
    confirmDelete.value = false
    emit('deleted', props.book)
  } catch (e) {
    store.toast(`❌ ${e.message}`, 'error')
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <div class="book-card" @click="$emit('open', book)">
    <div class="row">
      <div class="title grow">{{ book.title || book.book_id }}</div>
      <span class="badge" :class="statusClass">{{ statusText }}</span>
    </div>
    <div class="meta">
      <span v-if="book.suggest && book.suggest.floor">{{ book.suggest.floor }} / {{ book.suggest.room || '?' }} / {{ book.suggest.shelf || '?' }}</span>
      <span v-else class="muted">未分类</span>
      <span v-if="book.media_type" class="badge gold" style="margin-left:6px">{{ book.media_type }}</span>
      <span v-if="book.private" class="badge" style="margin-left:6px">🔒</span>
      <span v-if="book.distill_value !== undefined && book.distill_value !== null" style="margin-left:6px" :title="'蒸馏价值 ' + book.distill_value + ' / 100'">💎{{ book.distill_value }}</span>
    </div>
    <div v-if="book.status === 'shelved'" class="row" style="gap:6px">
      <button
        class="read-btn grow"
        title="打开阅览室"
        @click.stop="$emit('read', book)"
      >📖 阅读</button>
      <button
        class="read-btn danger-btn"
        title="删除此书（进档案馆，30 天内可恢复）"
        @click.stop="confirmDelete = true"
      >🗑 删除</button>
    </div>

    <ModalDialog
      :visible="confirmDelete"
      title="删除这本书？"
      :message="`《${book.title || book.book_id}》将移入档案馆，30 天内可在档案馆恢复。`"
      ok-text="确认删除"
      danger
      @confirm="doDelete"
      @cancel="confirmDelete = false"
    />
  </div>
</template>
