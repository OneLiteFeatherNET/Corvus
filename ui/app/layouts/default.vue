<template>
  <div>
    <a class="skip-link" href="#main" @click.prevent="focusMain">{{ $t('common.skipToContent') }}</a>

    <v-app-bar flat color="surface-container" height="64">
      <v-app-bar-nav-icon
        class="d-lg-none"
        :aria-label="$t('common.menu')"
        @click="drawer = !drawer"
      />
      <NuxtLink to="/" class="d-flex align-center text-decoration-none ml-2" :aria-label="$t('app.name')">
        <img :src="logo" alt="" width="48" height="48" style="object-fit: contain">
        <span class="text-title-large ml-3 text-on-surface">Corvus</span>
      </NuxtLink>
      <v-spacer />
      <v-menu>
        <template #activator="{ props }">
          <v-btn v-bind="props" icon="mdi-translate" :aria-label="$t('common.language')" />
        </template>
        <v-list>
          <v-list-item
            v-for="l in locales"
            :key="l.code"
            :active="l.code === locale"
            :title="l.name"
            @click="setLocale(l.code)"
          />
        </v-list>
      </v-menu>
      <v-btn
        :icon="isDark ? 'mdi-weather-sunny' : 'mdi-weather-night'"
        :aria-label="isDark ? $t('common.lightTheme') : $t('common.darkTheme')"
        @click="toggleTheme"
      />
      <v-menu>
        <template #activator="{ props }">
          <v-btn v-bind="props" icon="mdi-account-circle-outline" :aria-label="$t('auth.account')" />
        </template>
        <v-card min-width="240">
          <v-card-text>
            <div class="text-body-large">{{ session?.subject }}</div>
            <div class="mt-2 d-flex flex-wrap ga-1">
              <v-chip v-for="r in roles" :key="r" size="small" label>{{ $t(`roles.${r}`) }}</v-chip>
              <span v-if="!roles.length" class="text-body-medium text-on-surface-variant">{{ $t('auth.noRoles') }}</span>
            </div>
          </v-card-text>
          <v-card-actions>
            <v-btn prepend-icon="mdi-logout" @click="logout">{{ $t('auth.logout') }}</v-btn>
          </v-card-actions>
        </v-card>
      </v-menu>
    </v-app-bar>

    <v-navigation-drawer v-model="drawer" color="surface-container-low" :permanent="lgAndUp" :aria-label="$t('common.navigation')">
      <v-list nav aria-label="Corvus">
        <v-list-item
          v-for="item in items"
          :key="item.key"
          :to="item.to"
          :prepend-icon="item.icon"
          :title="$t(`nav.${item.key}`)"
          :exact="item.to === '/'"
          rounded="xl"
        />
      </v-list>
    </v-navigation-drawer>

    <v-main>
      <v-container id="main" tabindex="-1" fluid class="pa-4 pa-md-6" style="max-width: 1400px; outline: none">
        <slot />
      </v-container>
    </v-main>
  </div>
</template>

<script setup lang="ts">
import { useDisplay, useTheme } from 'vuetify'
import { readBasePath } from '~/lib/base'
import { visibleNav } from '~/lib/nav'

const { session, roles, can, logout } = useAuth()
const { locale, locales, setLocale } = useI18n()
const theme = useTheme()
const { lgAndUp } = useDisplay()

const logo = `${readBasePath()}logo.png`
const drawer = ref(lgAndUp.value)
const items = computed(() => visibleNav(can))
const isDark = computed(() => theme.global.current.value.dark)

function toggleTheme() {
  const next = isDark.value ? 'light' : 'dark'
  theme.global.name.value = next
  try {
    localStorage.setItem('corvus.theme', next)
  }
  catch { /* storage unavailable: the choice just does not persist */ }
}

// Move focus to the new page after a navigation so keyboard and screen reader users start at the content.
const route = useRoute()
watch(() => route.path, () => nextTick(() => document.getElementById('main')?.focus({ preventScroll: true })))

function focusMain() {
  document.getElementById('main')?.focus()
}
</script>
