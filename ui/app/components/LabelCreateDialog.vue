<template>
  <v-dialog :model-value="modelValue" max-width="640" @update:model-value="$emit('update:modelValue', $event)" @after-leave="reset">
    <v-card :title="$t('labels.create')">
      <v-card-text>
        <AppProblem :error="action.error.value" />
        <v-row dense>
          <v-col cols="12" sm="4"><v-select v-model="form.subject_type" :items="['pseudonym', 'session']" :label="$t('labels.subjectType')" /></v-col>
          <v-col cols="12" sm="8"><v-text-field v-model="form.subject" :label="$t('labels.subject')" :error-messages="subjectError" class="mono" /></v-col>
          <v-col cols="12" sm="4"><v-select v-model="form.label" :items="labelItems" :label="$t('labels.label')" /></v-col>
          <v-col cols="12" sm="4"><v-select v-model="form.source" :items="sources" :label="$t('labels.source')" /></v-col>
          <v-col cols="12" sm="4"><v-text-field v-model.number="form.confidence" type="number" min="0" max="1" step="0.05" :label="$t('labels.confidence')" /></v-col>
          <v-col cols="12" sm="6"><v-text-field v-model="validFrom" type="datetime-local" :label="$t('labels.validFrom')" /></v-col>
          <v-col cols="12" sm="6"><v-text-field v-model="validTo" type="datetime-local" :label="$t('labels.validTo')" /></v-col>
          <v-col cols="12"><v-textarea v-model="form.note" :label="$t('labels.note')" :hint="$t('labels.noteHint')" persistent-hint rows="2" counter="2000" /></v-col>
        </v-row>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="$emit('update:modelValue', false)">{{ $t('common.cancel') }}</v-btn>
        <v-btn :loading="action.pending.value" @click="submit">{{ $t('common.save') }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { isValidSubject } from '~/lib/label-form'
import type { LabelInput } from '~/types'

defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [boolean], 'created': [] }>()

const { t } = useI18n()
const labels = useLabels()

const blank = (): LabelInput => ({ subject_type: 'pseudonym', subject: '', label: 'cheat', source: 'staff_verdict', confidence: 1, note: '' })
const form = reactive<LabelInput>(blank())
const validFrom = ref('')
const validTo = ref('')
const showErrors = ref(false)

const sources = ['honeypot', 'staff_verdict', 'ban_import', 'weak_rule']
const labelItems = computed(() => (['legit', 'cheat'] as const).map(v => ({ value: v, title: t(`labels.value.${v}`) })))
const subjectError = computed(() => (showErrors.value && !isValidSubject(form.subject_type, form.subject) ? t('labels.errors.badSubject') : ''))

const action = useAction(async () => {
  showErrors.value = true
  if (!isValidSubject(form.subject_type, form.subject)) return
  await labels.create({
    ...form,
    subject: form.subject.trim().toLowerCase(),
    // datetime-local has no zone; the API requires one, so the browser's local zone is applied explicitly.
    valid_from: validFrom.value ? new Date(validFrom.value).toISOString() : null,
    valid_to: validTo.value ? new Date(validTo.value).toISOString() : null,
  })
  emit('created')
  emit('update:modelValue', false)
})
const submit = () => action.run()

function reset() {
  Object.assign(form, blank())
  validFrom.value = ''
  validTo.value = ''
  showErrors.value = false
  action.reset()
}
</script>
