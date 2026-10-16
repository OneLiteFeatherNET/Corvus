<template>
  <M3Card class="w-full p-6">
    <div class="flex items-center gap-3">
      <img :src="logo" alt="" width="48" height="48" class="size-12 object-contain">
      <h1 class="text-headline-small">{{ $t('auth.title') }}</h1>
    </div>
    <p class="my-4 text-body-large text-on-surface-variant">{{ $t(mode === 'oidc' ? 'auth.oidcHint' : 'auth.tokenHint') }}</p>
    <form v-if="mode === 'token'" class="flex flex-col gap-4" @submit.prevent="submit">
      <M3TextField v-model="token" type="password" autocomplete="off" :label="$t('auth.token')" required />
      <fieldset class="m-0 border-0 p-0">
        <legend class="mb-1 text-label-large">{{ $t('auth.rolesLegend') }}</legend>
        <M3Checkbox v-for="r in ROLES" :key="r" :model-value="selected.includes(r)" @update:model-value="toggleRole(r, $event)">{{ $t(`roles.${r}`) }}</M3Checkbox>
      </fieldset>
      <div>
        <M3Button type="submit" icon="mdi-login" :disabled="!token">{{ $t('auth.login') }}</M3Button>
      </div>
    </form>
    <M3Button v-else icon="mdi-login" @click="submit">{{ $t('auth.login') }}</M3Button>
  </M3Card>
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

function toggleRole(role: Role, on: boolean) {
  selected.value = on ? [...selected.value.filter(r => r !== role), role] : selected.value.filter(r => r !== role)
}

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
