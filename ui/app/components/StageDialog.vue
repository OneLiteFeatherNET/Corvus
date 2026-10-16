<template>
  <M3Dialog
    :model-value="modelValue"
    :title="$t('models.transition.title', { name: model.name, version: model.version })"
    @update:model-value="$emit('update:modelValue', $event)"
    @after-leave="reset"
  >
    <div class="flex flex-col gap-4 pt-1">
      <p class="flex items-center gap-2">
        {{ $t('models.transition.current') }}:
        <StatusChip kind="stage" :value="model.stage" />
      </p>
      <AppProblem :error="action.error.value" />
      <M3RadioGroup
        v-if="targets.length"
        v-model="target"
        :legend="$t('models.transition.target')"
        :options="targets.map(s => ({ value: s, label: $t(`models.stage.${s}`) }))"
      />
      <p v-else class="text-on-surface-variant">{{ $t('models.transition.final') }}</p>
      <M3TextField
        v-model="reason"
        multiline
        :rows="2"
        :label="$t('models.transition.reason')"
        :counter="500"
        :error="reason.length > 500 ? $t('models.transition.reason') : ''"
      />
    </div>
    <template #actions>
      <M3Button variant="text" @click="$emit('update:modelValue', false)">{{ $t('common.cancel') }}</M3Button>
      <M3Button :disabled="!target" :loading="action.pending.value" :loading-label="$t('common.loading')" @click="submit">
        {{ $t('models.transition.apply') }}
      </M3Button>
    </template>
  </M3Dialog>
</template>

<script setup lang="ts">
import { nextStages } from '~/lib/metrics'
import type { Model, Stage } from '~/types'

const props = defineProps<{ modelValue: boolean, model: Model }>()
const emit = defineEmits<{ 'update:modelValue': [boolean], 'changed': [Model] }>()

const models = useModels()
const target = ref<string>('')
const reason = ref('')
const targets = computed(() => nextStages(props.model.stage) as Stage[])

// A rejected promotion (409) keeps the dialog open and shows every failed condition from the API verbatim.
const action = useAction(async () => {
  if (!target.value) return
  const updated = await models.setStage(props.model.id, target.value as Stage, reason.value)
  emit('changed', updated)
  emit('update:modelValue', false)
})
const submit = () => action.run()

function reset() {
  target.value = ''
  reason.value = ''
  action.reset()
}
</script>
