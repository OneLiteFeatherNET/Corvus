<template>
  <div>
    <main v-if="configError" class="mx-auto max-w-2xl px-4 py-16">
      <M3Alert tone="error" role="alert" :title="$t('errors.configTitle')">
        {{ configError }}
        <p class="mt-2">{{ $t('errors.configHint') }}</p>
      </M3Alert>
    </main>
    <NuxtLayout v-else>
      <NuxtPage />
    </NuxtLayout>
    <M3Snackbar :message="snack.message.value" :close-label="$t('common.dismiss')" @dismiss="snack.dismiss" />
  </div>
</template>

<script setup lang="ts">
import { TABLE_LABELS } from '~/components/m3/table-labels'

const configError = useState<string | null>('corvus.config-error', () => null)
const { locale } = useI18n()
const snack = useSnackbar()
const { t } = useI18n()

provide(TABLE_LABELS, computed(() => ({
  loading: t('common.loading'),
  empty: t('common.empty'),
  select: t('common.table.selectRow'),
  sortBy: t('common.table.sortBy'),
  rowsPerPage: t('common.table.rowsPerPage'),
  previous: t('common.table.previous'),
  next: t('common.table.next'),
  range: t('common.table.range', { from: '{from}', to: '{to}', total: '{total}' }),
})))

useHead({ htmlAttrs: { lang: locale } })
</script>
