import { createRouter, createWebHashHistory } from 'vue-router'

export type AppRouteName = 'services' | 'oauth' | 'logs' | 'about'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      redirect: '/services',
    },
    {
      path: '/services',
      name: 'services',
      component: () => import('../components/ServiceView.vue'),
    },
    {
      path: '/oauth',
      name: 'oauth',
      component: () => import('../components/OAuthClientView.vue'),
    },
    {
      path: '/logs',
      name: 'logs',
      component: () => import('../components/LogView.vue'),
    },
    {
      path: '/about',
      name: 'about',
      component: () => import('../components/AboutRouteView.vue'),
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/services',
    },
  ],
})
