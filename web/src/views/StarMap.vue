<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import echarts from '../echarts'
import { api } from '../api'

const router = useRouter()
const loading = ref(true)
const error = ref('')
const data = ref({ nodes: [], links: [] })
const chartEl = ref(null)
let chart = null

// 类型筛选（默认全开）
const typeFilter = ref({
  book: true,
  skill: true,
  theme: true,
  archive: true,
  conversation: true,
})

// 着色模式：type（按类型）| time（按入馆时间）
const colorMode = ref('type')
const showUnplaced = ref(false)  // 只看补书室未归位星（默认全图）
const showBookEdges = ref(true)  // 显示书↔书边（P5-5）

const TYPE_COLORS = {
  book: '#3d5a45',
  skill: '#b98a2f',
  theme: '#7a6a5a',
  archive: '#8a5a44',
  conversation: '#4a6a8a',
}
const TYPE_LABELS = {
  book: '📕 书',
  skill: '🧪 技能',
  theme: '🗂 主题',
  archive: '🗄 档案',
  conversation: '💬 对话',
}

// P5-5：书↔书边样式（按关系类型着色；semantic 线宽映射相似度）
const BOOK_EDGE_STYLES = {
  semantic:   { color: '#2f9e8f', width: 2.2,  dashed: false, label: '内容相似' },
  same_room:  { color: '#5a7a9a', width: 1.4,  dashed: false, label: '同房间' },
  same_tag:   { color: '#8a6ab0', width: 1.2,  dashed: false, label: '同标签' },
  references: { color: '#c07a3a', width: 1.2,  dashed: true,  label: '引用' },
}
const RELATION_LABELS = {
  ...Object.fromEntries(Object.entries(BOOK_EDGE_STYLES).map(([k, v]) => [k, v.label])),
  shelved_in: '上架归属', suggested: '建议归属', raw_copy: '原始副本',
  distilled: '蒸馏来源', archived: '归档', referenced: '对话引用',
}

function timeColor(ts) {
  if (!ts) return '#bbbbbb'
  const now = Date.now() / 1000
  const days = Math.max(0, (now - ts) / 86400)
  if (days <= 1) return '#2f4a37'      // 今天：深绿
  if (days <= 7) return '#3d5a45'      // 一周内
  if (days <= 30) return '#6b8a5a'     // 一月内
  if (days <= 180) return '#b98a2f'    // 半年内
  return '#8a8a8a'                    // 更早：灰
}

const visibleNodes = computed(() => {
  let nodes = data.value.nodes.filter((n) => typeFilter.value[n.type])
  if (showUnplaced.value) {
    // 未归位星：补书室的书（incoming/reviewing）
    nodes = nodes.filter((n) => n.type !== 'book' || n.status === 'incoming' || n.status === 'reviewing')
  }
  const ids = new Set(nodes.map((n) => n.id))
  const links = data.value.links.filter((l) => ids.has(l.source) && ids.has(l.target))
  return { nodes, links }
})

const summary = computed(() => {
  const c = data.value.counts || {}
  const be = data.value.book_edges || {}
  let s = Object.entries(TYPE_LABELS).map(([k, label]) => `${label} ${c[k] ?? 0}`).join(' · ')
  if (be.total) s += ` · 🔗 书↔书边 ${be.total}（语义 ${be.semantic ?? 0} / 同房间 ${be.same_room ?? 0} / 同标签 ${be.same_tag ?? 0} / 引用 ${be.references ?? 0}）`
  return s
})

function render() {
  if (!chartEl.value) return
  if (!chart) chart = echarts.init(chartEl.value)
  const { nodes, links } = visibleNodes.value
  const nodeData = nodes.map((n) => {
    const isBook = n.type === 'book'
    const unplaced = isBook && (n.status === 'incoming' || n.status === 'reviewing')
    const color = colorMode.value === 'time'
      ? timeColor(n.ts)
      : (TYPE_COLORS[n.type] || '#888')
    const item = {
      id: n.id,
      name: n.name,
      symbolSize: isBook ? (unplaced ? 26 : 22) : (n.type === 'theme' ? 20 : 14),
      category: n.type,
      draggable: true,
      itemStyle: {
        color,
        borderColor: unplaced ? '#b98a2f' : '#fff',
        borderWidth: unplaced ? 2 : 1,
        borderType: unplaced ? 'dashed' : 'solid',
        opacity: 0.95,
      },
      label: {
        show: true,
        fontSize: 11,
        color: '#2c2a26',
        formatter: (p) => {
          const name = p.data.name || ''
          return name.length > 10 ? name.slice(0, 9) + '…' : name
        },
      },
      // 元数据给事件用
      _bookId: n.id,
      _status: n.status,
      _type: n.type,
    }
    return item
  })
  const linkData = links
    .filter((l) => showBookEdges.value || !BOOK_EDGE_STYLES[l.relation])
    .map((l) => {
      const st = BOOK_EDGE_STYLES[l.relation]
      if (st) {
        // 书↔书边：按关系类型着色；semantic 线宽映射相似度（越像越粗）
        const w = l.relation === 'semantic'
          ? Math.min(6, 1 + 5 * (l.similarity || 0.4))
          : st.width
        return {
          source: l.source,
          target: l.target,
          relation: l.relation,
          similarity: l.similarity,
          lineStyle: {
            width: w, color: st.color, curveness: 0.15, opacity: 0.8,
            type: st.dashed ? 'dashed' : 'solid',
          },
        }
      }
      return {
        source: l.source,
        target: l.target,
        relation: l.relation,
        lineStyle: { width: 1, color: '#c9bfa8', curveness: 0.15, opacity: 0.6 },
      }
    })
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      formatter: (p) => {
        if (p.dataType === 'edge') {
          const l = p.data
          const label = RELATION_LABELS[l.relation] || l.relation || ''
          let s = `🔗 ${label}`
          if (l.similarity != null) s += `<br/>相似度：${(l.similarity * 100).toFixed(0)}%`
          return s
        }
        const n = p.data
        let s = `<b>${TYPE_LABELS[n._type] || n.category || n._type}</b> ${n.name}<br/>`
        if (n._status) s += `状态：${n._status}<br/>`
        return s
      },
    },
    series: [{
      type: 'graph',
      layout: 'force',
      roam: true,
      draggable: true,
      data: nodeData,
      links: linkData,
      force: {
        repulsion: 220,
        edgeLength: 90,
        gravity: 0.08,
        friction: 0.6,
      },
      emphasis: {
        focus: 'adjacency',
        lineStyle: { width: 3 },
      },
      lineStyle: { color: '#c9bfa8', curveness: 0.15, opacity: 0.6 },
    }],
  })
  chart.off('click')
  chart.on('click', (p) => {
    if (p.dataType !== 'node') return
    const n = p.data
    if (n._type === 'book') router.push(`/book/${n._bookId}`)
  })
}

function resize() {
  if (chart) chart.resize()
}

onMounted(async () => {
  window.addEventListener('resize', resize)
  try {
    data.value = await api.get('/api/starmap')
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
    // chartEl 在 v-else 分支里，loading 置 false 后 DOM 未同步挂载，需 nextTick 再 init
    nextTick(render)
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', resize)
  if (chart) { chart.dispose(); chart = null }
})
</script>

<template>
  <div>
    <h1 class="page-title">🌌 占星室</h1>
    <p class="page-sub">书 / 技能 / 主题 / 档案 / 对话 星空图 · 未归位的星 = 补书室的书（虚线圈）</p>

    <div class="card mb16">
      <div class="row wrap" style="gap:14px">
        <div class="row wrap" style="gap:10px">
          <label v-for="(label, k) in TYPE_LABELS" :key="k" class="chk">
            <input type="checkbox" v-model="typeFilter[k]" /> {{ label }}
          </label>
        </div>
        <div class="spacer"></div>
        <div class="row" style="gap:8px">
          <button :class="{ primary: colorMode === 'type' }" class="small" @click="colorMode = 'type'; render()">按类型着色</button>
          <button :class="{ primary: colorMode === 'time' }" class="small" @click="colorMode = 'time'; render()">按入馆时间着色</button>
        </div>
        <label class="chk">
          <input type="checkbox" v-model="showUnplaced" @change="render()" /> 只看未归位星
        </label>
        <label class="chk">
          <input type="checkbox" v-model="showBookEdges" @change="render()" /> 书↔书边
        </label>
      </div>
      <div class="muted mt8">{{ summary }}</div>
    </div>

    <div v-if="error" class="card" style="color:var(--danger)">❌ {{ error }}</div>
    <div v-else-if="loading" class="loading">加载星空图…</div>
    <div v-else class="card" style="padding:6px">
      <div ref="chartEl" style="width:100%;height:calc(100vh - 260px);min-height:420px"></div>
      <div class="muted" style="padding:8px 10px">
        提示：拖拽节点可调整布局 · 点击书节点跳转阅览室 · 双击空白处放大<br/>
        <span v-if="data.book_edges && data.book_edges.total">
          <b>书↔书边</b>：<span style="color:#2f9e8f">▬ 内容相似</span>（线越粗越像）·
          <span style="color:#5a7a9a">▬ 同房间</span> · <span style="color:#8a6ab0">▬ 同标签</span> ·
          <span style="color:#c07a3a">┅ 引用</span>
          <span class="muted">（语义边来源：{{ data.book_edges.semantic_source === 'lexical' ? '词法兜底（离线）' : '卡片向量' }}）</span>
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chk { display: inline-flex; align-items: center; gap: 4px; font-size: 0.9em; cursor: pointer; }
.chk input { width: auto; }
</style>
