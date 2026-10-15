<template>
  <v-form @submit.prevent>
    <v-text-field v-model="f.name" :label="$t('pools.f.name')" :error-messages="msgs(['nameRequired', 'nameTooLong'])" required counter="200" />
    <v-textarea v-model="f.description" :label="$t('pools.f.description')" rows="2" auto-grow counter="2000" />

    <h2 class="text-title-large mt-4 mb-2">{{ $t('pools.f.filter') }}</h2>
    <v-row dense>
      <v-col cols="12" md="6">
        <v-combobox v-model="f.gameContexts" :label="$t('pools.f.gameContexts')" multiple chips closable-chips :hint="$t('pools.f.listHint')" persistent-hint />
      </v-col>
      <v-col cols="12" md="6">
        <v-combobox v-model="f.backendServers" :label="$t('pools.f.backendServers')" multiple chips closable-chips :hint="$t('pools.f.listHint')" persistent-hint />
      </v-col>
      <v-col cols="12" md="6">
        <v-combobox v-model="f.brandInclude" :label="$t('pools.f.brandInclude')" multiple chips closable-chips :hint="$t('pools.f.listHint')" persistent-hint />
      </v-col>
      <v-col cols="12" md="6">
        <v-combobox v-model="f.brandExclude" :label="$t('pools.f.brandExclude')" multiple chips closable-chips :hint="$t('pools.f.listHint')" persistent-hint />
      </v-col>
      <v-col cols="6" md="3">
        <v-text-field v-model.number="f.protocolMin" type="number" min="0" :label="$t('pools.f.protocolMin')" :error-messages="msgs(['protocolOrder', 'protocolNegative'])" />
      </v-col>
      <v-col cols="6" md="3">
        <v-text-field v-model.number="f.protocolMax" type="number" min="0" :label="$t('pools.f.protocolMax')" />
      </v-col>
      <v-col cols="6" md="3">
        <v-text-field v-model="f.dateStart" type="date" :label="$t('pools.f.dateStart')" :error-messages="msgs(['dateOrder'])" />
      </v-col>
      <v-col cols="6" md="3">
        <v-text-field v-model="f.dateEnd" type="date" :label="$t('pools.f.dateEnd')" />
      </v-col>
      <v-col cols="12" md="6">
        <v-select
          v-model="f.labelValue"
          :items="labelItems"
          :label="$t('pools.f.labelValue')"
          item-title="title"
          item-value="value"
        />
      </v-col>
      <v-col cols="12" md="6">
        <v-text-field
          v-model.number="f.labelMinConfidence"
          type="number"
          min="0"
          max="1"
          step="0.05"
          :disabled="!f.labelValue"
          :label="$t('pools.f.labelMinConfidence')"
          :error-messages="msgs(['confidenceRange'])"
        />
      </v-col>
      <v-col cols="12" md="4">
        <v-text-field v-model.number="f.minDurationS" type="number" min="0" :label="$t('pools.f.minDuration')" :error-messages="msgs(['durationNegative'])" />
      </v-col>
      <v-col cols="12" md="4">
        <v-text-field v-model.number="f.maxDropRate" type="number" min="0" max="1" step="0.01" :label="$t('pools.f.maxDropRate')" :error-messages="msgs(['dropRateRange'])" />
      </v-col>
      <v-col cols="12" md="4">
        <v-text-field v-model.number="f.retentionDays" type="number" min="1" max="3650" :label="$t('pools.f.retention')" :hint="$t('pools.f.retentionHint')" persistent-hint :error-messages="msgs(['retentionRange'])" />
      </v-col>
      <v-col cols="12">
        <v-switch v-model="f.excludeOrphans" :label="$t('pools.f.excludeOrphans')" color="primary" hide-details />
      </v-col>
    </v-row>
  </v-form>
</template>

<script setup lang="ts">
import { validatePoolForm } from '~/lib/pool-filter'
import type { PoolFormError, PoolFormState } from '~/lib/pool-filter'

// The parent owns the (reactive) state object; the fields are bound to it directly through this alias.
const props = defineProps<{ form: PoolFormState, showErrors?: boolean }>()
const f = props.form
const { t } = useI18n()

const errors = computed(() => (props.showErrors ? validatePoolForm(props.form) : []))
function msgs(codes: PoolFormError[]): string[] {
  return codes.filter(c => errors.value.includes(c)).map(c => t(`pools.errors.${c}`))
}

const labelItems = computed(() => [
  { value: '', title: t('pools.f.labelAny') },
  ...(['legit', 'cheat', 'labeled', 'unlabeled'] as const).map(v => ({ value: v, title: t(`labels.value.${v}`) })),
])
</script>
