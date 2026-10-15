import { createWebHistory } from 'vue-router'
import type { RouterConfig } from 'nuxt/schema'
import { readBasePath } from '~/lib/base'

// Clean history-mode URLs under whatever base path the container serves the app at.
export default <RouterConfig>{
  history: () => createWebHistory(readBasePath()),
}
