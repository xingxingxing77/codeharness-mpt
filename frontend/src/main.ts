import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { useThemeStore } from './stores/theme'
import './style.css'

const app = createApp(App)
app.use(createPinia())

// 挂载前先把主题落到 body 上，否则深色偏好会闪一帧浅色
const theme = useThemeStore()
theme.apply()
theme.watch()

app.mount('#app')
