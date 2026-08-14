import { defineStore } from 'pinia'
import { api } from '../api'

// 全局状态：健康度 / WS 连接 / 通知 toast
export const useLibraryStore = defineStore('library', {
  state: () => ({
    dashboard: null,
    wsConnected: false,
    ws: null,
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
      ws.onopen = () => { this.wsConnected = true }
      ws.onclose = () => {
        this.wsConnected = false
        this.ws = null
        // 5s 后重连（本地服务）
        setTimeout(() => { if (!this.ws) this.connectWS() }, 5000)
      }
      ws.onmessage = (ev) => {
        let msg
        try { msg = JSON.parse(ev.data) } catch { return }
        if (msg.type === 'notice') {
          const text = this._noticeText(msg)
          if (text) this.toast(text, 'info')
          if (msg.event === 'book_ingested') this.refreshDashboard()
        }
      }
      this.ws = ws
      return ws
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
