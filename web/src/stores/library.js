import { defineStore } from 'pinia'
import { api } from '../api'

// 全局状态：健康度 / WS 连接 / 通知 toast
export const useLibraryStore = defineStore('library', {
  state: () => ({
    dashboard: null,
    wsConnected: false,
    ws: null,
    wsListeners: [],
    wsReconnectDelay: 1000,      // P4-6：指数退避重连（1s→2s→…→30s 上限）
    wsMaxReconnectDelay: 30000,
    toasts: [],
    toastSeq: 0,
    pendingClassify: 0,
  }),
  actions: {
    async refreshDashboard() {
      try {
        this.dashboard = await api.dashboard()
        this.pendingClassify = this.dashboard?.health?.pending_classify ?? 0
      } catch (e) {
        console.warn('dashboard 加载失败', e)
      }
    },
    // ------- WS -------
    connectWS() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const url = `${proto}://${location.host}/ws/chat`
      let ws
      try {
        ws = new WebSocket(url)
      } catch (e) {
        console.warn('WS 不可用', e)
        return null
      }
      ws.onopen = () => {
        this.wsConnected = true
        this.wsReconnectDelay = 1000   // 重连成功 → 退避重置
      }
      ws.onclose = () => {
        this.wsConnected = false
        this.ws = null
        // P4-6：指数退避重连（1s→2s→4s→…上限 30s），比固定 5s 更稳
        const delay = this.wsReconnectDelay
        this.wsReconnectDelay = Math.min(delay * 2, this.wsMaxReconnectDelay)
        setTimeout(() => { if (!this.ws) this.connectWS() }, delay)
      }
      ws.onmessage = (ev) => {
        let msg
        try { msg = JSON.parse(ev.data) } catch { return }
        // 事件订阅者（P4-5：Admin.vue 流式聊天等）
        this.wsListeners.forEach((cb) => {
          try { cb(msg) } catch (e) { console.warn('WS listener error', e) }
        })
        if (msg.type === 'notice') {
          const text = this._noticeText(msg)
          if (text) this.toast(text, 'info')
          if (['book_ingested', 'batch_ingested', 'batch_index_done'].includes(msg.event)) {
            this.refreshDashboard()
          }
        }
      }
      this.ws = ws
      return ws
    },
    // 订阅 WS 消息（返回退订函数）
    onWSEvent(cb) {
      this.wsListeners.push(cb)
      return () => {
        const i = this.wsListeners.indexOf(cb)
        if (i >= 0) this.wsListeners.splice(i, 1)
      }
    },
    sendWS(obj) {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify(obj))
        return true
      }
      return false
    },
    _noticeText(msg) {
      switch (msg.event) {
        case 'book_ingested':
          return `📥 新书入馆：${msg.title || msg.book_id}（补书室）`
        case 'batch_ingested':
          return `📥 批量入馆：${msg.count || 0} 本进入补书室`
        case 'batch_index_progress':
          return ''   // 逐本进度太吵，静默（dashboard 刷新覆盖）
        case 'batch_index_done':
          if (msg.error) return `⚠️ 批量索引失败：${msg.error}`
          return msg.fallback
            ? `⚡ 批量索引完成（词法先行，向量待补）：${msg.books || 0} 本`
            : `⚡ 批量索引完成：${msg.books || 0} 本 / ${msg.chunks || 0} chunks`
        case 'distill_progress':
          return `🔬 蒸馏进度：${msg.book_id || ''} ${msg.stage || ''}`
        case 'skill_review_ready':
          return `🧪 新技能待审阅：${msg.name || msg.skill_id || ''}`
        case 'purchase_ready':
          return `🛒 今日采购推荐已生成（${msg.count || 0} 条）`
        default:
          return ''
      }
    },
    // ------- Toast -------
    toast(text, type = 'info') {
      const id = ++this.toastSeq
      this.toasts.push({ id, text, type })
      setTimeout(() => {
        this.toasts = this.toasts.filter((t) => t.id !== id)
      }, 5000)
    },
    removeToast(id) {
      this.toasts = this.toasts.filter((t) => t.id !== id)
    },
  },
})
