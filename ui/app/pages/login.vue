<template>
  <v-card class="pa-4 w-100" variant="flat" color="surface-container-low">
    <v-card-title class="d-flex align-center ga-3">
      <img :src="logo" alt="" width="48" height="48" style="object-fit: contain">
      <h1 class="text-headline-small">{{ $t('auth.title') }}</h1>
    </v-card-title>
    <v-card-text>
      <p class="mb-4 text-body-large">{{ $t(mode === 'oidc' ? 'auth.oidcHint' : 'auth.tokenHint') }}</p>
      <v-form v-if="mode === 'token'" @submit.prevent="submit">
        <v-text-field
          v-model="token"
          type="password"
          autocomplete="off"
          :label="$t('auth.token')"
          required
        />
        <fieldset class="border-0 pa-0">
          <legend class="text-label-large mb-1">{{ $t('auth.rolesLegend') }}</legend>
          <v-checkbox
            v-for="r in ROLES"
            :key="r"
            v-model="selected"
            :value="r"
            :label="$t(`roles.${r}`)"
            density="compact"
            hide-details
          />
        </fieldset>
        <v-btn type="submit" class="mt-4" :disabled="!token" prepend-icon="mdi-login">{{ $t('auth.login') }}</v-btn>
      </v-form>
      <v-btn v-else prepend-icon="mdi-login" @click="submit">{{ $t('auth.login') }}</v-btn>
    </v-card-text>
  </v-card>
</template>

<script setup lang="ts">
import { readBasePath } from '~/lib/base'
import { ROLES } from '~/lib/roles'
import type { Role } from '~/types'

definePageMeta({ layout: 'blank' })

const { login, loginWithToken, mode } = useAuth()
const route = useRoute()
const logo = `${readBasePath()}logo.png`
const token = ref('')
const selected = ref<Role[]>(['admin'])

function nextPath(): string {
  const n = route.query.next
  const v = Array.isArray(n) ? n[0] : n
  return v && v.startsWith('/') && !v.startsWith('//') ? v : '/'
}

async function submit() {
  if (mode === 'oidc') {
    await login(nextPath())
    return
  }
  await loginWithToken(token.value, selected.value)
  token.value = ''
  await navigateTo(nextPath())
}
</script>
