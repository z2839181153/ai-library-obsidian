// 后端 REST 封装（同源 /api；开发期 vite proxy 到 8800）
// opts: { timeout: ms（超时自动 abort）, signal: AbortSignal（外部取消） }
async function request(method, url, body, isForm = false, opts = {}) {
  const { timeout = 0, signal } = opts
  const controller = new AbortController()
  let timer = null
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', () => controller.abort(), { once: true })
  }
  if (timeout > 0) {
    timer = setTimeout(() => controller.abort(), timeout)
  }
  try {
    const reqOpts = { method, headers: {}, signal: controller.signal }
    if (body !== undefined) {
      if (isForm || body instanceof FormData) {
        reqOpts.body = body
      } else {
        reqOpts.headers['Content-Type'] = 'application/json'
        reqOpts.body = JSON.stringify(body)
      }
    }
    const res = await fetch(url, reqOpts)
    if (!res.ok) {
      let detail = res.statusText
      try {
        const j = await res.json()
        detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
      } catch { /* ignore */ }
      throw new Error(detail || `请求失败 (${res.status})`)
    }
    const ct = res.headers.get('content-type') || ''
    return ct.includes('application/json') ? res.json() : res.text()
  } finally {
    if (timer) clearTimeout(timer)
  }
}

export const api = {
  get: (url, opts) => request('GET', url, undefined, false, opts),
  post: (url, body, opts) => request('POST', url, body, false, opts),
  put: (url, body, opts) => request('PUT', url, body, false, opts),
  del: (url, opts) => request('DELETE', url, undefined, false, opts),
  upload: (url, formData, opts) => request('POST', url, formData, true, opts),

  // 快捷端点
  dashboard: () => request('GET', '/api/dashboard'),
  floors: () => request('GET', '/api/floors'),
  books: (params = {}) => {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') q.set(k, v) })
    const qs = q.toString()
    return request('GET', `/api/books${qs ? '?' + qs : ''}`)
  },
  book: (id) => request('GET', `/api/books/${id}`),
  bookContent: (id) => request('GET', `/api/books/${id}/content`),
  bookRelated: (id, topN = 6) =>
    request('GET', `/api/books/${id}/related?top_n=${topN}`, undefined, false, { timeout: 15000 }),
  classify: (id, force = false, opts = {}) => request('POST', `/api/books/${id}/classify`, { force }, false, opts),
  confirmShelve: (id, pos = {}) => request('POST', `/api/books/${id}/confirm`, pos),
  ask: (query, cvId = null, topK = 20, opts = {}) =>
    request('POST', '/api/ask', { query, top_k: topK, cv_id: cvId }, false, opts),
  search: (query, topK = 20) => request('POST', '/api/search', { query, top_k: topK }),
  indexStatus: () => request('GET', '/api/index/status'),
  indexRun: (rebuild = false) => request('POST', '/api/index/run', { rebuild }),
  actions: (limit = 100) => request('GET', `/api/actions?limit=${limit}`),
  undoAction: (actId) => request('POST', `/api/actions/${actId}/undo`),
  conversations: () => request('GET', '/api/conversations'),
  conversation: (cvId) => request('GET', `/api/conversations/${cvId}`),
  archiveConversation: (cvId) => request('POST', `/api/conversations/${cvId}/archive`),
  skills: (status) => request('GET', `/api/skills${status ? '?status=' + status : ''}`),
  skill: (id) => request('GET', `/api/skills/${id}`),
  approveSkill: (id) => request('POST', `/api/skills/${id}/approve`),
  rejectSkill: (id, reason) => request('POST', `/api/skills/${id}/reject`, { reason }),
  unblockSkill: (id) => request('POST', `/api/skills/${id}/unblock`),
  distillStart: (id, autoConfirm = false) =>
    request('POST', `/api/distill/${id}/start`, { auto_confirm: autoConfirm }),
  distillStatus: (id) => request('GET', `/api/distill/${id}/status`),
  distillConfirm: (id, decision) =>
    request('POST', `/api/distill/${id}/confirm-stage`, { decision }),
  purchaseToday: () => request('GET', '/api/purchase/today'),
  purchaseGenerate: () => request('POST', '/api/purchase/generate'),
  purchaseCollect: (recId) => request('POST', `/api/purchase/${recId}/collect`),
  purchaseFeedback: (recId, action, note = '') =>
    request('POST', `/api/purchase/${recId}/feedback`, { action, note }),
  dailyReports: (date) =>
    request('GET', `/api/daily-reports${date ? '?date=' + date : ''}`),
  settings: () => request('GET', '/api/settings'),
  saveSettings: (patch) => request('PUT', '/api/settings', patch),
  createFloor: (body) => request('POST', '/api/floors', body),
  updateFloor: (id, body) => request('PUT', `/api/floors/${id}`, body),
  deleteFloor: (id) => request('DELETE', `/api/floors/${id}`),
  createRoom: (body) => request('POST', '/api/rooms', body),
  updateRoom: (id, body) => request('PUT', `/api/rooms/${id}`, body),
  deleteRoom: (id) => request('DELETE', `/api/rooms/${id}`),
  createShelf: (body) => request('POST', '/api/shelves', body),
  updateShelf: (id, body) => request('PUT', `/api/shelves/${id}`, body),
  deleteShelf: (id) => request('DELETE', `/api/shelves/${id}`),
}
