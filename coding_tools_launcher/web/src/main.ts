import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import '@vue-flow/core/dist/style.css'
import 'virtual:uno.css'
import './styles.css'

createApp(App).use(router).mount('#app')
