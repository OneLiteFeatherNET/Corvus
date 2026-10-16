<template>
  <form class="flex flex-col gap-4" @submit.prevent>
    <M3TextField v-model="f.name" :label="$t('pools.form.name')" :error="msgs(['nameRequired', 'nameTooLong'])" required :counter="200" />
    <M3TextField v-model="f.description" multiline :rows="2" :label="$t('pools.form.description')" :counter="2000" />

    <h2 class="mt-2 text-title-large">{{ $t('pools.form.filter') }}</h2>
    <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
      <M3Combobox v-model="f.gameContexts" :label="$t('pools.form.gameContexts')" :hint="$t('pools.form.listHint')" :remove-label="$t('common.removeItem')" />
      <M3Combobox v-model="f.backendServers" :label="$t('pools.form.backendServers')" :hint="$t('pools.form.listHint')" :remove-label="$t('common.removeItem')" />
      <M3Combobox v-model="f.brandInclude" :label="$t('pools.form.brandInclude')" :hint="$t('pools.form.listHint')" :remove-label="$t('common.removeItem')" />
      <M3Combobox v-model="f.brandExclude" :label="$t('pools.form.brandExclude')" :hint="$t('pools.form.listHint')" :remove-label="$t('common.removeItem')" />
    </div>
    <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
      <M3TextField v-model.number="f.protocolMin" type="number" min="0" :label="$t('pools.form.protocolMin')" :error="msgs(['protocolOrder', 'protocolNegative'])" />
      <M3TextField v-model.number="f.protocolMax" type="number" min="0" :label="$t('pools.form.protocolMax')" />
      <M3TextField v-model="f.dateStart" type="date" :label="$t('pools.form.dateStart')" :error="msgs(['dateOrder'])" />
      <M3TextField v-model="f.dateEnd" type="date" :label="$t('pools.form.dateEnd')" />
    </div>
    <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
      <M3Select v-model="f.labelValue" :items="labelItems" :label="$t('pools.form.labelValue')" />
      <M3TextField
        v-model.number="f.labelMinConfidence"
        type="number"
        min="0"
        max="1"
        step="0.05"
        :disabled="!f.labelValue"
        :label="$t('pools.form.labelMinConfidence')"
        :error="msgs(['confidenceRange'])"
      />
    </div>
    <div class="grid grid-cols-1 gap-4 md:grid-cols-3">
      <M3TextField v-model.number="f.minDurationS" type="number" min="0" :label="$t('pools.form.minDuration')" :error="msgs(['durationNegative'])" />
      <M3TextField v-model.number="f.maxDropRate" type="number" min="0" max="1" step="0.01" :label="$t('pools.form.maxDropRate')" :error="msgs(['dropRateRange'])" />
      <M3TextField
        v-model.number="f.retentionDays"
        type="number"
        min="1"
        max="3650"
        :label="$t('pools.form.retention')"
        :hint="$t('pools.form.retentionHint')"
        :error="msgs(['retentionRange'])"
      />
    </div>
    <M3Switch v-model="f.excludeOrphans">{{ $t('pools.form.excludeOrphans') }}</M3Switch>
  </form>
</template>

<script setup lang="ts">
import { validatePoolForm } from '~/lib/pool-filter'
import type { PoolFormError, PoolFormState } from '~/lib/pool-filter'

// The parent owns the (reactive) state object; the fields are bound to it directly through this alias.
const props = defineProps<{ form: PoolFormState, showErrors?: boolean }>()
const f = props.form
const { t } = useI18n()

const errors = computed(() => (props.showErrors ? validatePoolForm(props.form) : []))
function msgs(codes: PoolFormError[]): string {
  return codes.filter(c => errors.value.includes(c)).map(c => t(`pools.errors.${c}`)).join(' ')
}

const labelItems = computed(() => [
  { value: '', title: t('pools.form.labelAny') },
  ...(['legit', 'cheat', 'labeled', 'unlabeled'] as const).map(v => ({ value: v, title: t(`labels.value.${v}`) })),
])
</script>
