<template>
  <M3Dialog :model-value="modelValue" :title="$t('labels.create')" @update:model-value="$emit('update:modelValue', $event)" @after-leave="reset">
    <div class="flex flex-col gap-4 pt-1">
      <AppProblem :error="action.error.value" />
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <M3Select v-model="form.subject_type" :items="SUBJECT_TYPES" :label="$t('labels.subjectType')" />
        <M3TextField v-model="form.subject" :label="$t('labels.subject')" :error="subjectError" input-class="mono" class="sm:col-span-2" />
      </div>
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <M3Select v-model="form.label" :items="labelItems" :label="$t('labels.label')" />
        <M3Select v-model="form.source" :items="sources" :label="$t('labels.source')" />
        <M3TextField v-model.number="form.confidence" type="number" min="0" max="1" step="0.05" :label="$t('labels.confidence')" />
      </div>
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <M3TextField v-model="validFrom" type="datetime-local" :label="$t('labels.validFrom')" />
        <M3TextField v-model="validTo" type="datetime-local" :label="$t('labels.validTo')" />
      </div>
      <M3TextField v-model="form.note" multiline :rows="2" :label="$t('labels.note')" :hint="$t('labels.noteHint')" :counter="2000" />
    </div>
    <template #actions>
      <M3Button variant="text" @click="$emit('update:modelValue', false)">{{ $t('common.cancel') }}</M3Button>
      <M3Button :loading="action.pending.value" :loading-label="$t('common.loading')" @click="submit">{{ $t('common.save') }}</M3Button>
    </template>
  </M3Dialog>
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

const SUBJECT_TYPES = ['pseudonym', 'session'] as const
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
