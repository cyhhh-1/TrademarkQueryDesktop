import { createApp } from 'vue'

import App from './App.vue'
import { createDesktopService, DESKTOP_SERVICE_KEY } from './services/desktop'
import './styles/tokens.css'
import './styles/app.css'

createApp(App)
  .provide(DESKTOP_SERVICE_KEY, createDesktopService())
  .mount('#app')
