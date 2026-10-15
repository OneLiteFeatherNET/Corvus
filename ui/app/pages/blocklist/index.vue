<template>
  <div>
    <PageHeader :title="$t('blocklist.title')">
      <template #actions>
        <v-select v-model="kind" :items="kindItems" :label="$t('blocklist.kind')" density="compact" hide-details style="min-width: 180px" />
        <v-btn prepend-icon="mdi-plus" @click="addDialog = true">{{ $t('blocklist.add') }}</v-btn>
      </template>
    </PageHeader>

    <AppProblem :error="list.error.value || removal.error.value" />
    <v-data-table :headers="headers" :items="rows" :loading="list.loading.value" :no-data-text="$t('common.empty')" item-value="pseudonym" hover>
      <template #[`item.pseudonym`]="{ item }"><span class="mono">{{ item.pseudonym.slice(0, 16) }}…</span></template>
      <template #[`item.kind`]="{ item }">
        <v-chip size="small" label variant="tonal" :color="item.kind === 'block' ? 'error' : 'primary'" :prepend-icon="item.kind === 'block' ? 'mdi-cancel' : 'mdi-check'">{{ $t(`blocklist.kinds.${item.kind}`) }}</v-chip>
      </template>
      <template #[`item.source`]="{ item }">{{ $t(`blocklist.sources.${item.source}`) }}</template>
      <template #[`item.created_at`]="{ item }">{{ formatDateTime(item.created_at, locale) }}</template>
      <template #[`item.actions`]="{ item }">
        <v-btn
          icon="mdi-delete-outline"
          variant="text"
          size="small"
          :disabled="item.source === 'erasure'"
          :aria-label="item.source === 'erasure' ? $t('blocklist.permanent') : $t('common.delete')"
          @click="removal.run(item.pseudonym)"
        />
      </template>
    </v-data-table>
    <v-btn v-if="list.hasMore()" class="mt-3" variant="tonal" :loading="list.loading.value" @click="list.loadMore()">{{ $t('common.loadMore') }}</v-btn>

    <v-dialog v-model="addDialog" max-width="560" @after-leave="resetAdd">
      <v-card :title="$t('blocklist.add')">
        <v-card-text>
          <AppProblem :error="add.error.value" />
          <v-text-field v-model="newPseudonym" :label="$t('players.pseudonym')" class="mono" autocomplete="off" :error-messages="newPseudonym && !isPseudonym(newPseudonym) ? $t('players.invalidPseudonym') : ''" />
          <v-select v-model="newKind" :items="kindItems.filter(k => k.value)" :label="$t('blocklist.kind')" />
          <v-text-field v-model="newReason" :label="$t('blocklist.reason')" />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="addDialog = false">{{ $t('common.cancel') }}</v-btn>
          <v-btn :disabled="!isPseudonym(newPseudonym)" :loading="add.pending.value" @click="add.run()">{{ $t('common.save') }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { isPseudonym } from '~/lib/erasure'
import { formatDateTime } from '~/lib/format'

const { t, locale } = useI18n()
const blocklist = useBlocklist()

const kind = useQueryState('kind')
const addDialog = ref(false)
const newPseudonym = ref('')
const newKind = ref<'block' | 'allow'>('block')
const newReason = ref('')

const list = usePagedList(cursor => blocklist.list({ limit: 200, cursor }))
onMounted(list.load)

const rows = computed(() => list.items.value.filter(e => !kind.value || e.kind === kind.value))

const add = useAction(async () => {
  await blocklist.put(newPseudonym.value.trim().toLowerCase(), { kind: newKind.value, reason: newReason.value })
  addDialog.value = false
  await list.load()
})
const removal = useAction(async (pseudonym: string) => {
  await blocklist.remove(pseudonym)
  await list.load()
})

function resetAdd() {
  newPseudonym.value = ''
  newReason.value = ''
  newKind.value = 'block'
  add.reset()
}

const kindItems = computed(() => [
  { value: '', title: t('common.all') },
  { value: 'block', title: t('blocklist.kinds.block') },
  { value: 'allow', title: t('blocklist.kinds.allow') },
])
const headers = computed(() => [
  { title: t('players.pseudonym'), key: 'pseudonym' },
  { title: t('blocklist.kind'), key: 'kind' },
  { title: t('blocklist.source'), key: 'source' },
  { title: t('blocklist.reason'), key: 'reason' },
  { title: t('common.created'), key: 'created_at' },
  { title: t('common.createdBy'), key: 'created_by' },
  { title: '', key: 'actions', sortable: false, align: 'end' as const },
])
</script>
