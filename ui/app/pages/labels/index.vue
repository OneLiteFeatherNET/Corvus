<template>
  <div>
    <PageHeader :title="$t('labels.title')" />

    <div class="mb-4 flex flex-wrap items-start gap-3">
      <M3Select v-model="subjectType" :items="typeItems" :label="$t('labels.subjectType')" class="w-full sm:w-52" />
      <M3TextField
        v-model="subject"
        :label="$t('labels.subject')"
        clearable
        :clear-label="$t('common.delete')"
        input-class="mono"
        class="w-full sm:w-80"
      />
      <div class="mt-2 flex flex-wrap gap-2 sm:ml-auto">
        <M3Button v-if="can('labels.import')" variant="tonal" icon="mdi-file-upload-outline" @click="importDialog = true">{{ $t('labels.import') }}</M3Button>
        <M3Button v-if="can('labels.create')" icon="mdi-plus" @click="createDialog = true">{{ $t('labels.create') }}</M3Button>
      </div>
    </div>

    <AppProblem :error="list.error.value" />
    <M3DataTable :columns="headers" :items="list.items.value" :loading="list.loading.value">
      <template #[`item.subject`]="{ item }"><span class="mono">{{ item.subject.slice(0, 16) }}…</span></template>
      <template #[`item.label`]="{ item }">
        <M3Chip small :tone="item.label === 'cheat' ? 'error' : 'primary'" :icon="item.label === 'cheat' ? 'mdi-alert-outline' : 'mdi-check'">
          {{ enumLabel('labels.value', item.label) }}
        </M3Chip>
      </template>
      <template #[`item.confidence`]="{ item }">{{ item.confidence.toFixed(2) }}</template>
      <template #[`item.created_at`]="{ item }">{{ formatDateTime(item.created_at, locale) }}</template>
    </M3DataTable>
    <LoadMore :visible="list.hasMore()" :loading="list.loading.value" @more="list.loadMore()" />

    <LabelCreateDialog v-if="can('labels.create')" v-model="createDialog" @created="onCreated" />
    <LabelImportDialog v-if="can('labels.import')" v-model="importDialog" @imported="list.load()" />
  </div>
</template>

<script setup lang="ts">
import { formatDateTime } from '~/lib/format'

const enumLabel = useEnumLabel()

const { t, locale } = useI18n()
const { can } = useAuth()
const labels = useLabels()
const snack = useSnackbar()

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
function onCreated() {
  snack.show(t('common.saved'))
  void list.load()
}

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
