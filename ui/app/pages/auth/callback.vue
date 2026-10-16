<template>
  <div class="w-full text-center">
    <M3Progress v-if="!failure" circular :label="$t('auth.completing')" />
    <M3Alert v-else tone="error" role="alert" :title="$t('auth.failed')" class="text-start">
      {{ failure }}
      <div class="mt-4">
        <M3Button @click="retry">{{ $t('auth.login') }}</M3Button>
      </div>
    </M3Alert>
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
