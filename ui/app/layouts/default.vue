<template>
  <div class="min-h-screen">
    <a
      class="absolute -top-16 left-2 z-[70] rounded-full bg-primary px-4 py-2 text-on-primary focus:top-2"
      href="#main"
      @click.prevent="focusMain"
    >{{ $t('common.skipToContent') }}</a>

    <M3TopAppBar>
      <template #leading>
        <M3IconButton v-if="!isDesktop" icon="mdi-menu" :label="$t('common.menu')" :aria-expanded="String(drawer)" @click="drawer = true" />
      </template>
      <template #title>
        <NuxtLink to="/" class="inline-flex items-center gap-3 rounded-full py-1 pr-3 pl-1 text-on-surface no-underline" :aria-label="$t('app.name')">
          <img :src="logo" alt="" width="40" height="40" class="size-10 object-contain">
          <span class="text-title-large">Corvus</span>
        </NuxtLink>
      </template>
      <template #actions>
        <M3Menu :label="$t('common.language')">
          <template #activator="{ props: a, toggle }">
            <M3IconButton v-bind="a" icon="mdi-translate" :label="$t('common.language')" @click="toggle" />
          </template>
          <template #default="{ close }">
            <M3MenuItem
              v-for="l in locales"
              :key="l.code"
              :selected="l.code === locale"
              @click="pick(l.code, close)"
            >
              {{ l.name }}
            </M3MenuItem>
          </template>
        </M3Menu>
        <M3IconButton
          :icon="isDark ? 'mdi-weather-sunny' : 'mdi-weather-night'"
          :label="isDark ? $t('common.lightTheme') : $t('common.darkTheme')"
          @click="toggleTheme"
        />
        <M3Menu :label="$t('auth.account')" :menu="false">
          <template #activator="{ props: a, toggle }">
            <M3IconButton v-bind="a" icon="mdi-account-circle-outline" :label="$t('auth.account')" @click="toggle" />
          </template>
          <div class="w-64 p-4">
            <p class="break-words text-body-large">{{ session?.subject }}</p>
            <div class="mt-2 flex flex-wrap gap-1">
              <M3Chip v-for="r in roles" :key="r" small>{{ $t(`roles.${r}`) }}</M3Chip>
              <span v-if="!roles.length" class="text-body-medium text-on-surface-variant">{{ $t('auth.noRoles') }}</span>
            </div>
            <div class="mt-4 flex justify-end">
              <M3Button variant="text" icon="mdi-logout" @click="logout">{{ $t('auth.logout') }}</M3Button>
            </div>
          </div>
        </M3Menu>
      </template>
    </M3TopAppBar>

    <div class="flex">
      <M3NavDrawer
        v-if="isDesktop"
        class="sticky top-16 h-[calc(100dvh-4rem)] overflow-y-auto"
        :items="entries"
        :active-path="route.path"
        :label="$t('common.navigation')"
      />
      <M3NavDrawer
        v-else
        v-model:open="drawer"
        modal
        :items="entries"
        :active-path="route.path"
        :label="$t('common.navigation')"
        :close-label="$t('common.close')"
      />
      <main id="main" tabindex="-1" class="min-w-0 flex-1 px-4 py-6 outline-none md:px-6">
        <div class="max-w-[88rem]">
          <slot />
        </div>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { readBasePath } from '~/lib/base'
import { visibleNav } from '~/lib/nav'

const { session, roles, can, logout } = useAuth()
const { locale, locales, setLocale } = useI18n()
const { isDark, toggle: toggleTheme } = useColorTheme()
const route = useRoute()
const { t } = useI18n()

const isDesktop = useMediaQuery('(min-width: 1024px)')
const logo = `${readBasePath()}logo.png`
const drawer = ref(false)
const entries = computed(() => visibleNav(can).map(i => ({ to: i.to, icon: i.icon, label: t(`nav.${i.key}`) })))

function pick(code: 'de' | 'en', close: () => void) {
  void setLocale(code)
  close()
}

// Move focus to the new page after a navigation so keyboard and screen reader users start at the content.
watch(() => route.path, () => nextTick(() => document.getElementById('main')?.focus({ preventScroll: true })))

function focusMain() {
  document.getElementById('main')?.focus()
}
</script>
