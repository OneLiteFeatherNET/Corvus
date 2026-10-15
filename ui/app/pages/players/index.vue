<template>
  <div>
    <PageHeader :title="$t('players.title')" :subtitle="$t('players.subtitle')" />

    <v-card variant="flat" color="surface-container-low" class="mb-6">
      <v-card-title class="text-title-large">{{ $t('players.lookup.title') }}</v-card-title>
      <v-card-text>
        <p class="text-body-medium text-on-surface-variant mb-3">{{ $t('players.lookup.privacy') }}</p>
        <AppProblem :error="lookup.error.value" />
        <v-form class="d-flex flex-wrap ga-3 align-start" @submit.prevent="lookup.run()">
          <v-text-field
            v-model="uuid"
            :label="$t('players.lookup.uuid')"
            :error-messages="uuid && !isUuid(uuid) ? $t('players.lookup.invalid') : ''"
            autocomplete="off"
            spellcheck="false"
            class="mono"
            style="min-width: 320px; flex: 1"
          />
          <v-btn type="submit" :disabled="!isUuid(uuid)" :loading="lookup.pending.value" prepend-icon="mdi-magnify" class="mt-1">{{ $t('players.lookup.submit') }}</v-btn>
        </v-form>

        <div v-if="result" class="mt-4" aria-live="polite">
          <dl class="kv">
            <dt>{{ $t('players.pseudonym') }}</dt><dd class="mono">{{ result.pseudonym }}</dd>
            <dt>{{ $t('players.keyId') }}</dt><dd>{{ result.key_id }}</dd>
            <dt>{{ $t('players.known') }}</dt><dd>{{ result.known ? $t('common.yes') : $t('common.no') }}</dd>
            <dt>{{ $t('players.firstSeen') }}</dt><dd>{{ formatDateTime(result.first_seen, locale) }}</dd>
            <dt>{{ $t('players.lastSeen') }}</dt><dd>{{ formatDateTime(result.last_seen, locale) }}</dd>
            <dt>{{ $t('players.erasedAt') }}</dt><dd>{{ formatDateTime(result.erased_at, locale) }}</dd>
          </dl>
          <div class="d-flex flex-wrap ga-3 mt-4">
            <v-btn color="error" variant="tonal" prepend-icon="mdi-delete-forever-outline" @click="openErasure(result.pseudonym)">{{ $t('players.erasure.open') }}</v-btn>
            <v-btn variant="tonal" prepend-icon="mdi-chart-line" :to="{ path: '/scores', query: { pseudonym: result.pseudonym } }">{{ $t('players.openScores') }}</v-btn>
          </div>
        </div>
      </v-card-text>
    </v-card>

    <v-card variant="flat" color="surface-container-low" class="mb-6">
      <v-card-title class="text-title-large">{{ $t('players.byPseudonym') }}</v-card-title>
      <v-card-text class="d-flex flex-wrap ga-3 align-start">
        <v-text-field
          v-model="pseudonymInput"
          :label="$t('players.pseudonym')"
          :error-messages="pseudonymInput && !isPseudonym(pseudonymInput) ? $t('players.invalidPseudonym') : ''"
          autocomplete="off"
          class="mono"
          style="min-width: 320px; flex: 1"
        />
        <v-btn color="error" variant="tonal" class="mt-1" :disabled="!isPseudonym(pseudonymInput)" prepend-icon="mdi-delete-forever-outline" @click="openErasure(pseudonymInput.trim().toLowerCase())">
          {{ $t('players.erasure.open') }}
        </v-btn>
      </v-card-text>
    </v-card>

    <h2 class="text-headline-small mb-1">{{ $t('players.requests') }}</h2>
    <AppProblem :error="requests.error.value" />
    <v-data-table :headers="headers" :items="requests.items.value" :loading="requests.loading.value" :no-data-text="$t('common.empty')" item-value="id">
      <template #[`item.pseudonym`]="{ item }"><span class="mono">{{ shortPseudonym(item.pseudonym) }}…</span></template>
      <template #[`item.requested_at`]="{ item }">{{ formatDateTime(item.requested_at, locale) }}</template>
      <template #[`item.key_deleted`]="{ item }">{{ item.key_deleted ? $t('common.yes') : $t('common.no') }}</template>
      <template #[`item.compaction`]="{ item }">
        <v-chip size="small" label variant="tonal" :color="item.compaction_pending ? 'warning' : 'primary'" :prepend-icon="item.compaction_pending ? 'mdi-timer-sand' : 'mdi-check'">
          {{ item.compaction_pending ? $t('players.compactionPending') : $t('players.compactionDone') }}
        </v-chip>
      </template>
      <template #[`item.compacted_at`]="{ item }">{{ formatDateTime(item.compacted_at, locale) }}</template>
    </v-data-table>
    <v-btn v-if="requests.hasMore()" class="mt-3" variant="tonal" :loading="requests.loading.value" @click="requests.loadMore()">{{ $t('common.loadMore') }}</v-btn>

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

<style scoped>
.kv {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 8px 24px;
}
.kv dt {
  color: rgb(var(--v-theme-on-surface-variant));
}
</style>
