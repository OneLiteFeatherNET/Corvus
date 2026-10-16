<template>
  <div>
    <PageHeader :title="$t('blocklist.title')" />

    <div class="mb-4 flex flex-wrap items-start gap-3">
      <M3Select v-model="kind" :items="kindItems" :label="$t('blocklist.kind')" class="w-full sm:w-56" />
      <M3Button class="mt-2 sm:ml-auto" icon="mdi-plus" @click="addDialog = true">{{ $t('blocklist.add') }}</M3Button>
    </div>

    <AppProblem :error="list.error.value || removal.error.value" />
    <M3DataTable :columns="headers" :items="rows" :loading="list.loading.value" row-key="pseudonym">
      <template #[`item.pseudonym`]="{ item }"><span class="mono">{{ item.pseudonym.slice(0, 16) }}…</span></template>
      <template #[`item.kind`]="{ item }">
        <M3Chip small :tone="item.kind === 'block' ? 'error' : 'primary'" :icon="item.kind === 'block' ? 'mdi-cancel' : 'mdi-check'">
          {{ $t(`blocklist.kinds.${item.kind}`) }}
        </M3Chip>
      </template>
      <template #[`item.source`]="{ item }">{{ $t(`blocklist.sources.${item.source}`) }}</template>
      <template #[`item.created_at`]="{ item }">{{ formatDateTime(item.created_at, locale) }}</template>
      <template #[`item.actions`]="{ item }">
        <M3IconButton
          icon="mdi-delete-outline"
          :disabled="item.source === 'erasure'"
          :label="item.source === 'erasure' ? $t('blocklist.permanent') : $t('common.delete')"
          @click="removal.run(item.pseudonym)"
        />
      </template>
    </M3DataTable>
    <LoadMore :visible="list.hasMore()" :loading="list.loading.value" @more="list.loadMore()" />

    <M3Dialog v-model="addDialog" :title="$t('blocklist.add')" @after-leave="resetAdd">
      <div class="flex flex-col gap-4 pt-1">
        <AppProblem :error="add.error.value" />
        <M3TextField
          v-model="newPseudonym"
          :label="$t('players.pseudonym')"
          autocomplete="off"
          input-class="mono"
          :error="newPseudonym && !isPseudonym(newPseudonym) ? $t('players.invalidPseudonym') : ''"
        />
        <M3Select v-model="newKind" :items="kindItems.filter(k => k.value)" :label="$t('blocklist.kind')" />
        <M3TextField v-model="newReason" :label="$t('blocklist.reason')" />
      </div>
      <template #actions>
        <M3Button variant="text" @click="addDialog = false">{{ $t('common.cancel') }}</M3Button>
        <M3Button :disabled="!isPseudonym(newPseudonym)" :loading="add.pending.value" :loading-label="$t('common.loading')" @click="add.run()">
          {{ $t('common.save') }}
        </M3Button>
      </template>
    </M3Dialog>
  </div>
</template>

<script setup lang="ts">
import { isPseudonym } from '~/lib/erasure'
import { formatDateTime } from '~/lib/format'

const { t, locale } = useI18n()
const blocklist = useBlocklist()
const snack = useSnackbar()

const kind = useQueryState('kind')
const addDialog = ref(false)
const newPseudonym = ref('')
const newKind = ref<string>('block')
const newReason = ref('')

const list = usePagedList(cursor => blocklist.list({ limit: 200, cursor }))
onMounted(list.load)

const rows = computed(() => list.items.value.filter(e => !kind.value || e.kind === kind.value))

const add = useAction(async () => {
  await blocklist.put(newPseudonym.value.trim().toLowerCase(), { kind: newKind.value as 'block' | 'allow', reason: newReason.value })
  addDialog.value = false
  snack.show(t('common.saved'))
  await list.load()
})
const removal = useAction(async (pseudonym: string) => {
  await blocklist.remove(pseudonym)
  snack.show(t('common.deleted'))
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
