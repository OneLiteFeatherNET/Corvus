<template>
  <div class="w-100 text-center">
    <v-progress-circular v-if="!failure" indeterminate :aria-label="$t('auth.completing')" />
    <v-alert v-else type="error" variant="tonal" role="alert" :title="$t('auth.failed')">
      {{ failure }}
      <div class="mt-4">
        <v-btn @click="retry">{{ $t('auth.login') }}</v-btn>
      </div>
    </v-alert>
  </div>
</template>

<script setup lang="ts">
definePageMeta({ layout: 'blank' })

const { completeLogin, login } = useAuth()
const failure = ref('')

onMounted(async () => {
  try {
    const to = await completeLogin()
    await navigateTo(to, { replace: true })
  }
  catch (e) {
    failure.value = e instanceof Error ? e.message : String(e)
  }
})

function retry() {
  void login('/')
}
</script>
