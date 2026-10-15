<template>
  <v-app>
    <v-main v-if="configError">
      <v-container class="py-16" style="max-width: 640px">
        <v-alert type="error" variant="tonal" :title="$t('errors.configTitle')" role="alert">
          {{ configError }}
          <p class="mt-2">{{ $t('errors.configHint') }}</p>
        </v-alert>
      </v-container>
    </v-main>
    <NuxtLayout v-else>
      <NuxtPage />
    </NuxtLayout>
  </v-app>
</template>

<script setup lang="ts">
const configError = useState<string | null>('corvus.config-error', () => null)
const { locale } = useI18n()

useHead({ htmlAttrs: { lang: locale } })
</script>
