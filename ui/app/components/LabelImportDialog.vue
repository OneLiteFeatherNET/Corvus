<template>
  <M3Dialog :model-value="modelValue" width="lg" :title="$t('labels.import')" @update:model-value="$emit('update:modelValue', $event)" @after-leave="reset">
    <div class="flex flex-col gap-4 pt-1">
      <div>
        <p class="mb-2">{{ $t('labels.importHint') }}</p>
        <code class="mono block">subject_type,subject,label,source,confidence[,valid_from,valid_to,note]</code>
      </div>
      <AppProblem :error="action.error.value" />
      <M3Alert v-if="imported !== null" tone="success">{{ $t('labels.imported', { n: imported }) }}</M3Alert>
      <M3FileInput ref="fileInput" accept=".csv,text/csv" :label="$t('labels.chooseFile')" @change="onFile" />
      <template v-if="preview">
        <M3Alert :tone="preview.valid ? 'success' : 'error'">
          {{ preview.valid ? $t('labels.csvOk', { n: preview.rowCount }) : $t('labels.csvIssues', { n: preview.issues.length }) }}
        </M3Alert>
        <ul v-if="preview.issues.length" class="list-disc pl-5">
          <li v-for="(i, n) in preview.issues.slice(0, 20)" :key="n">
            <span v-if="i.line">{{ $t('common.line', { n: i.line }) }}: </span>{{ $t(`labels.csvErrors.${i.code}`) }}<span v-if="i.column"> ({{ i.column }})</span>
          </li>
          <li v-if="preview.issues.length > 20">… {{ $t('labels.moreIssues', { n: preview.issues.length - 20 }) }}</li>
        </ul>
        <div class="overflow-x-auto rounded-md border border-outline-variant">
          <table class="w-full border-collapse text-body-small">
            <thead>
              <tr class="bg-surface-container text-start"><th v-for="h in preview.header" :key="h" scope="col" class="px-3 py-2 text-start">{{ h }}</th></tr>
            </thead>
            <tbody>
              <tr v-for="(r, n) in preview.rows.slice(0, 5)" :key="n" class="border-t border-outline-variant">
                <td v-for="(c, k) in r" :key="k" class="mono px-3 py-2">{{ c }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="text-body-medium text-on-surface-variant">{{ $t('labels.previewRows', { n: Math.min(5, preview.rowCount), total: preview.rowCount }) }}</p>
      </template>
    </div>
    <template #actions>
      <M3Button variant="text" @click="$emit('update:modelValue', false)">{{ $t('common.close') }}</M3Button>
      <M3Button :disabled="!preview?.valid" :loading="action.pending.value" :loading-label="$t('common.loading')" icon="mdi-upload" @click="submit">
        {{ $t('labels.upload') }}
      </M3Button>
    </template>
  </M3Dialog>
</template>

<script setup lang="ts">
import { validateLabelCsv } from '~/lib/label-csv'
import type { CsvPreview } from '~/lib/label-csv'

defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [boolean], 'imported': [] }>()

const labels = useLabels()
const fileInput = ref<{ reset: () => void } | null>(null)
const text = ref('')
const preview = ref<CsvPreview | null>(null)
const imported = ref<number | null>(null)

async function onFile(f: File | null) {
  imported.value = null
  if (!f) {
    text.value = ''
    preview.value = null
    return
  }
  text.value = await f.text()
  preview.value = validateLabelCsv(text.value, f.size)
}

// The server validates again (all rows or none) and answers 422 with the failing lines.
const action = useAction(async () => {
  const res = await labels.importCsv(text.value)
  imported.value = res.imported
  emit('imported')
})
const submit = () => action.run()

function reset() {
  fileInput.value?.reset()
  text.value = ''
  preview.value = null
  imported.value = null
  action.reset()
}
</script>
