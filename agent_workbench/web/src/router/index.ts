import { createRouter, createWebHashHistory } from 'vue-router'

export type AppRouteName =
  | 'services'
  | 'workbench'
  | 'workbench-skills'
  | 'workbench-mcp-connections'
  | 'oauth'
  | 'logs'
  | 'about'

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
      path: '/workbench',
      name: 'workbench',
      component: () => import('../components/WorkflowWorkbenchView.vue'),
    },
    {
      path: '/workbench/skills',
      name: 'workbench-skills',
      component: () => import('../components/SkillManagerView.vue'),
    },
    {
      path: '/workbench/mcp-connections',
      name: 'workbench-mcp-connections',
      component: () => import('../components/MCPConnectionManagerView.vue'),
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
