<script setup>
import { computed } from 'vue'

const props = defineProps({
  book: { type: Object, required: true },
})

const statusText = computed(() => ({
  incoming: '待分类',
  reviewing: '待确认',
  shelved: '已上架',
  indexed: '已索引',
  deleted: '已删除',
}[props.book.status] || props.book.status))

const statusClass = computed(() => `status-${props.book.status}`)
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
  </div>
</template>
