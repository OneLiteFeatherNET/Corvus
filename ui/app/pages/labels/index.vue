<template>
  <div>
    <PageHeader :title="$t('labels.title')">
      <template #actions>
        <v-btn v-if="can('labels.create')" prepend-icon="mdi-plus" @click="createDialog = true">{{ $t('labels.create') }}</v-btn>
        <v-btn v-if="can('labels.import')" variant="tonal" prepend-icon="mdi-file-upload-outline" @click="importDialog = true">{{ $t('labels.import') }}</v-btn>
      </template>
    </PageHeader>

    <v-row dense>
      <v-col cols="12" sm="4"><v-select v-model="subjectType" :items="typeItems" :label="$t('labels.subjectType')" hide-details /></v-col>
      <v-col cols="12" sm="8"><v-text-field v-model="subject" :label="$t('labels.subject')" clearable hide-details class="mono" /></v-col>
    </v-row>

    <AppProblem :error="list.error.value" />
    <v-data-table class="mt-4" :headers="headers" :items="list.items.value" :loading="list.loading.value" :no-data-text="$t('common.empty')" item-value="id" hover>
      <template #[`item.subject`]="{ item }"><span class="mono">{{ item.subject.slice(0, 16) }}…</span></template>
      <template #[`item.label`]="{ item }">
        <v-chip size="small" label variant="tonal" :color="item.label === 'cheat' ? 'error' : 'primary'" :prepend-icon="item.label === 'cheat' ? 'mdi-alert-outline' : 'mdi-check'">{{ $t(`labels.value.${item.label}`) }}</v-chip>
      </template>
      <template #[`item.confidence`]="{ item }">{{ item.confidence.toFixed(2) }}</template>
      <template #[`item.created_at`]="{ item }">{{ formatDateTime(item.created_at, locale) }}</template>
    </v-data-table>
    <v-btn v-if="list.hasMore()" class="mt-3" variant="tonal" :loading="list.loading.value" @click="list.loadMore()">{{ $t('common.loadMore') }}</v-btn>

    <LabelCreateDialog v-if="can('labels.create')" v-model="createDialog" @created="list.load()" />
    <LabelImportDialog v-if="can('labels.import')" v-model="importDialog" @imported="list.load()" />
  </div>
</template>

<script setup lang="ts">
import { formatDateTime } from '~/lib/format'

const { t, locale } = useI18n()
const { can } = useAuth()
const labels = useLabels()

const subjectType = useQueryState('subject_type')
const subject = useQueryState('subject')
const createDialog = ref(false)
const importDialog = ref(false)

const list = usePagedList(cursor => labels.list({
  limit: 50,
  cursor,
  subjectType: subjectType.value || undefined,
  subject: subject.value.trim() || undefined,
}))
onMounted(list.load)
watch([subjectType, subject], () => list.load())

const typeItems = computed(() => [
  { value: '', title: t('common.all') },
  { value: 'pseudonym', title: 'pseudonym' },
  { value: 'session', title: 'session' },
])
const headers = computed(() => [
  { title: t('labels.subjectType'), key: 'subject_type' },
  { title: t('labels.subject'), key: 'subject' },
  { title: t('labels.label'), key: 'label' },
  { title: t('labels.source'), key: 'source' },
  { title: t('labels.confidence'), key: 'confidence', align: 'end' as const },
  { title: t('common.created'), key: 'created_at' },
  { title: t('common.createdBy'), key: 'created_by' },
])
</script>
