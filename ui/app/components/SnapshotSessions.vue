<template>
  <section>
    <h2 class="mb-2 text-title-medium">{{ $t('snapshots.sessionList') }}</h2>
    <AppProblem :error="list.error.value" />
    <M3DataTable :columns="headers" :items="list.items.value" :loading="list.loading.value" row-key="session_id">
      <template #[`item.session_id`]="{ item }">
        <span class="inline-flex items-center gap-1">
          <span class="mono">{{ item.session_id }}</span>
          <M3IconButton icon="mdi-content-copy" :label="$t('common.copy')" @click="copy(item.session_id)" />
        </span>
      </template>
      <template #[`item.pseudonym`]="{ item }">
        <span v-if="item.pseudonym" class="inline-flex items-center gap-1">
          <span class="mono" :title="item.pseudonym">{{ shortHex(item.pseudonym) }}</span>
          <M3IconButton icon="mdi-content-copy" :label="$t('common.copy')" @click="copy(item.pseudonym)" />
        </span>
        <span v-else>–</span>
      </template>
      <template #[`item.started_at`]="{ item }">{{ formatDateTime(item.started_at, locale) }}</template>
      <template #[`item.game_contexts`]="{ item }">
        <div class="flex flex-wrap gap-1">
          <M3Chip v-for="gc in sessionGameContexts(item)" :key="gc" small>{{ gc }}</M3Chip>
        </div>
      </template>
      <template #[`item.labels`]="{ item }">
        <div class="flex flex-wrap gap-1">
          <M3Chip
            v-for="(l, i) in item.labels"
            :key="`${l.label}-${l.source}-${i}`"
            small
            :tone="l.label === 'cheat' ? 'error' : 'primary'"
          >
            {{ enumLabel('labels.value', l.label) }}
          </M3Chip>
        </div>
      </template>
      <template #[`item.actions`]="{ item }">
        <M3Button
          v-if="can('labels.create')"
          variant="text"
          icon="mdi-tag-plus-outline"
          @click="openLabel(item.session_id)"
        >
          {{ $t('snapshots.labelSession') }}
        </M3Button>
      </template>
    </M3DataTable>
    <LoadMore :visible="list.hasMore()" :loading="list.loading.value" @more="list.loadMore()" />

    <LabelCreateDialog
      v-if="can('labels.create')"
      v-model="labelDialog"
      :subject="labelSubject"
      @created="onLabelCreated"
    />
  </section>
</template>

<script setup lang="ts">
import { formatDateTime } from '~/lib/format'
import type { LabelSubject } from '~/lib/label-form'
import { sessionGameContexts, shortHex } from '~/lib/session-view'

const props = defineProps<{ snapshotId: string }>()

const { t, locale } = useI18n()
const { can } = useAuth()
const enumLabel = useEnumLabel()
const snapshots = useSnapshots()
const snack = useSnackbar()

// Erased players' sessions are left out: they must not be labelled any more.
const list = usePagedList(cursor => snapshots.sessions(props.snapshotId, { limit: 50, cursor, excludeErased: true }))
onMounted(list.load)

const labelDialog = ref(false)
const labelSubject = ref<LabelSubject | null>(null)

const headers = computed(() => [
  { title: t('snapshots.sessionId'), key: 'session_id', sortable: false },
  { title: t('snapshots.pseudonym'), key: 'pseudonym', sortable: false },
  { title: t('snapshots.started'), key: 'started_at', sortable: false },
  { title: t('snapshots.gameContexts'), key: 'game_contexts', sortable: false },
  { title: t('labels.title'), key: 'labels', sortable: false },
  { title: '', key: 'actions', sortable: false },
])

function openLabel(sessionId: string) {
  labelSubject.value = { type: 'session', id: sessionId }
  labelDialog.value = true
}

function onLabelCreated() {
  snack.show(t('common.saved'))
  void list.load()
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    snack.show(t('common.copied'))
  }
  catch {
    snack.show(t('common.copyFailed'))
  }
}
</script>
