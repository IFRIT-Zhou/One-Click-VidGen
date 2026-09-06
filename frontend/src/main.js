// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Zhou Ruoyu and He Yun
// AGPL-3.0 Section 7 terms: ../../ADDITIONAL_TERMS.md

import { createApp } from 'vue'
import App from './App.vue'
import Studio from './Studio.vue'
import './style.css'

const legacy = location.pathname === '/legacy.html'
if (legacy) createApp(App).mount('#app')
else import('./prototype/prototype.css').then(() => import('./studio.css')).then(() => createApp(Studio).mount('#app'))
