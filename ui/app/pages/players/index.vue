<template>
  <div>
    <PageHeader :title="$t('players.title')" :subtitle="$t('players.subtitle')" />

    <M3Card class="mb-6" :title="$t('players.lookup.title')">
      <p class="mb-4 text-body-medium text-on-surface-variant">{{ $t('players.lookup.privacy') }}</p>
      <AppProblem :error="lookup.error.value" />
      <form class="flex flex-wrap items-start gap-3" @submit.prevent="lookup.run()">
        <M3TextField
          v-model="uuid"
          :label="$t('players.lookup.uuid')"
          :error="uuid && !isUuid(uuid) ? $t('players.lookup.invalid') : ''"
          autocomplete="off"
          spellcheck="false"
          input-class="mono"
          class="min-w-64 flex-1"
        />
        <M3Button type="submit" class="mt-2" :disabled="!isUuid(uuid)" :loading="lookup.pending.value" :loading-label="$t('common.loading')" icon="mdi-magnify">
          {{ $t('players.lookup.submit') }}
        </M3Button>
      </form>

      <div v-if="result" class="mt-6" aria-live="polite">
        <dl class="kv">
          <dt>{{ $t('players.pseudonym') }}</dt><dd class="mono">{{ result.pseudonym }}</dd>
          <dt>{{ $t('players.keyId') }}</dt><dd>{{ result.key_id }}</dd>
          <dt>{{ $t('players.known') }}</dt><dd>{{ result.known ? $t('common.yes') : $t('common.no') }}</dd>
          <dt>{{ $t('players.firstSeen') }}</dt><dd>{{ formatDateTime(result.first_seen, locale) }}</dd>
          <dt>{{ $t('players.lastSeen') }}</dt><dd>{{ formatDateTime(result.last_seen, locale) }}</dd>
          <dt>{{ $t('players.erasedAt') }}</dt><dd>{{ formatDateTime(result.erased_at, locale) }}</dd>
        </dl>
        <div class="mt-4 flex flex-wrap gap-3">
          <M3Button variant="tonal" tone="error" icon="mdi-delete-forever-outline" @click="openErasure(result.pseudonym)">{{ $t('players.erasure.open') }}</M3Button>
          <M3Button variant="tonal" icon="mdi-chart-line" :to="{ path: '/scores', query: { pseudonym: result.pseudonym } }">{{ $t('players.openScores') }}</M3Button>
        </div>
      </div>
    </M3Card>

    <M3Card class="mb-6" :title="$t('players.byPseudonym')">
      <div class="flex flex-wrap items-start gap-3">
        <M3TextField
          v-model="pseudonymInput"
          :label="$t('players.pseudonym')"
          :error="pseudonymInput && !isPseudonym(pseudonymInput) ? $t('players.invalidPseudonym') : ''"
          autocomplete="off"
          input-class="mono"
          class="min-w-64 flex-1"
        />
        <M3Button
          class="mt-2"
          variant="tonal"
          tone="error"
          :disabled="!isPseudonym(pseudonymInput)"
          icon="mdi-delete-forever-outline"
          @click="openErasure(pseudonymInput.trim().toLowerCase())"
        >
          {{ $t('players.erasure.open') }}
        </M3Button>
      </div>
    </M3Card>

    <h2 class="mb-3 text-headline-small">{{ $t('players.requests') }}</h2>
    <AppProblem :error="requests.error.value" />
    <M3DataTable :columns="headers" :items="requests.items.value" :loading="requests.loading.value">
      <template #[`item.pseudonym`]="{ item }"><span class="mono">{{ shortPseudonym(item.pseudonym) }}…</span></template>
      <template #[`item.requested_at`]="{ item }">{{ formatDateTime(item.requested_at, locale) }}</template>
      <template #[`item.key_deleted`]="{ item }">{{ item.key_deleted ? $t('common.yes') : $t('common.no') }}</template>
      <template #[`item.compaction`]="{ item }">
        <M3Chip small :tone="item.compaction_pending ? 'warning' : 'success'" :icon="item.compaction_pending ? 'mdi-timer-sand' : 'mdi-check'">
          {{ item.compaction_pending ? $t('players.compactionPending') : $t('players.compactionDone') }}
        </M3Chip>
      </template>
      <template #[`item.compacted_at`]="{ item }">{{ formatDateTime(item.compacted_at, locale) }}</template>
    </M3DataTable>
    <LoadMore :visible="requests.hasMore()" :loading="requests.loading.value" @more="requests.loadMore()" />

    <ErasureDialog v-model="dialog" :pseudonym="target" @erased="onErased" />
  </div>
</template>

<script setup lang="ts">
import { isPseudonym, isUuid, shortPseudonym } from '~/lib/erasure'
import { formatDateTime } from '~/lib/format'
import type { PlayerLookup } from '~/types'

const { t, locale } = useI18n()
const players = usePlayers()

// The UUID lives only in this component's memory: never in the URL, a query string, storage or a log line.
const uuid = ref('')
const result = ref<PlayerLookup | null>(null)
const pseudonymInput = ref('')
const dialog = ref(false)
const target = ref('')
// The API lists the requests, newest first; nothing is remembered in this browser.
const requests = usePagedList(cursor => players.erasureRequests({ limit: 50, cursor }))

const lookup = useAction(async () => {
  result.value = await players.lookup(uuid.value.trim())
  uuid.value = ''
})

function openErasure(pseudonym: string) {
  target.value = pseudonym
  dialog.value = true
}

async function onErased() {
  pseudonymInput.value = ''
  await requests.load()
}

onMounted(requests.load)

const headers = computed(() => [
  { title: t('players.pseudonym'), key: 'pseudonym' },
  { title: t('players.requestedAt'), key: 'requested_at' },
  { title: t('players.requestedBy'), key: 'requested_by' },
  { title: t('players.status'), key: 'status' },
  { title: t('players.keyDeleted'), key: 'key_deleted' },
  { title: t('players.compaction'), key: 'compaction', sortable: false },
  { title: t('players.compactedAt'), key: 'compacted_at' },
])
</script>
