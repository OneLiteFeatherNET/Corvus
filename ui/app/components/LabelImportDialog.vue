<template>
  <v-dialog :model-value="modelValue" max-width="900" scrollable @update:model-value="$emit('update:modelValue', $event)" @after-leave="reset">
    <v-card :title="$t('labels.import')">
      <v-card-text>
        <p class="mb-2">{{ $t('labels.importHint') }}</p>
        <code class="mono d-block mb-4">subject_type,subject,label,source,confidence[,valid_from,valid_to,note]</code>
        <AppProblem :error="action.error.value" />
        <v-alert v-if="imported !== null" type="success" variant="tonal" role="status" class="mb-3">
          {{ $t('labels.imported', { n: imported }) }}
        </v-alert>
        <v-file-input
          v-model="file"
          accept=".csv,text/csv"
          :label="$t('labels.chooseFile')"
          prepend-icon=""
          prepend-inner-icon="mdi-file-delimited-outline"
          @update:model-value="onFile"
        />
        <template v-if="preview">
          <v-alert :type="preview.valid ? 'success' : 'error'" variant="tonal" density="compact" role="status" class="mb-3">
            {{ preview.valid ? $t('labels.csvOk', { n: preview.rowCount }) : $t('labels.csvIssues', { n: preview.issues.length }) }}
          </v-alert>
          <ul v-if="preview.issues.length" class="mb-3 ml-4">
            <li v-for="(i, n) in preview.issues.slice(0, 20)" :key="n">
              <span v-if="i.line">{{ $t('common.line', { n: i.line }) }}: </span>{{ $t(`labels.csvErrors.${i.code}`) }}<span v-if="i.column"> ({{ i.column }})</span>
            </li>
            <li v-if="preview.issues.length > 20">… {{ $t('labels.moreIssues', { n: preview.issues.length - 20 }) }}</li>
          </ul>
          <v-table density="compact" class="mb-2">
            <thead><tr><th v-for="h in preview.header" :key="h" scope="col">{{ h }}</th></tr></thead>
            <tbody>
              <tr v-for="(r, n) in preview.rows.slice(0, 5)" :key="n"><td v-for="(c, k) in r" :key="k" class="mono">{{ c }}</td></tr>
            </tbody>
          </v-table>
          <p class="text-body-medium text-on-surface-variant">{{ $t('labels.previewRows', { n: Math.min(5, preview.rowCount), total: preview.rowCount }) }}</p>
        </template>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="$emit('update:modelValue', false)">{{ $t('common.close') }}</v-btn>
        <v-btn :disabled="!preview?.valid" :loading="action.pending.value" prepend-icon="mdi-upload" @click="submit">{{ $t('labels.upload') }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { validateLabelCsv } from '~/lib/label-csv'
import type { CsvPreview } from '~/lib/label-csv'

defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [boolean], 'imported': [] }>()

const labels = useLabels()
const file = ref<File | File[] | null>(null)
const text = ref('')
const preview = ref<CsvPreview | null>(null)
const imported = ref<number | null>(null)

async function onFile() {
  const f = Array.isArray(file.value) ? file.value[0] : file.value
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
  file.value = null
  text.value = ''
  preview.value = null
  imported.value = null
  action.reset()
}
</script>
