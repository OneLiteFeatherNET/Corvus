<template>
  <v-dialog :model-value="modelValue" max-width="560" @update:model-value="$emit('update:modelValue', $event)" @after-leave="reset">
    <v-card :title="$t('models.transition.title', { name: model.name, version: model.version })">
      <v-card-text>
        <p class="mb-3">
          {{ $t('models.transition.current') }}:
          <StatusChip kind="stage" :value="model.stage" />
        </p>
        <AppProblem :error="action.error.value" />
        <v-radio-group v-if="targets.length" v-model="target" :label="$t('models.transition.target')">
          <v-radio v-for="s in targets" :key="s" :value="s" :label="$t(`models.stage.${s}`)" />
        </v-radio-group>
        <p v-else class="text-on-surface-variant">{{ $t('models.transition.final') }}</p>
        <v-textarea v-model="reason" :label="$t('models.transition.reason')" rows="2" counter="500" :rules="[v => v.length <= 500]" />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="$emit('update:modelValue', false)">{{ $t('common.cancel') }}</v-btn>
        <v-btn :disabled="!target" :loading="action.pending.value" @click="submit">{{ $t('models.transition.apply') }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { nextStages } from '~/lib/metrics'
import type { Model, Stage } from '~/types'

const props = defineProps<{ modelValue: boolean, model: Model }>()
const emit = defineEmits<{ 'update:modelValue': [boolean], 'changed': [Model] }>()

const models = useModels()
const target = ref<Stage | ''>('')
const reason = ref('')
const targets = computed(() => nextStages(props.model.stage) as Stage[])

// A rejected promotion (409) keeps the dialog open and shows every failed condition from the API verbatim.
const action = useAction(async () => {
  if (!target.value) return
  const updated = await models.setStage(props.model.id, target.value, reason.value)
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
