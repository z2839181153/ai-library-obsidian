import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'hall', component: () => import('./views/Hall.vue'), meta: { title: '大厅' } },
  { path: '/floors', name: 'floors', component: () => import('./views/Floors.vue'), meta: { title: '楼层' } },
  { path: '/book/:id', name: 'book', component: () => import('./views/BookDetail.vue'), meta: { title: '阅览室' } },
  { path: '/admin', name: 'admin', component: () => import('./views/Admin.vue'), meta: { title: '管理员' } },
  { path: '/purchaser', name: 'purchaser', component: () => import('./views/Purchaser.vue'), meta: { title: '采购员' } },
  { path: '/settings', name: 'settings', component: () => import('./views/Settings.vue'), meta: { title: '设置' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = `${to.meta.title || ''} · AI 图书馆`
})

export default router
