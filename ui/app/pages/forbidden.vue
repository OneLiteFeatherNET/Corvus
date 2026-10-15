<template>
  <div>
    <PageHeader :title="$t('errors.forbiddenTitle')" />
    <v-alert type="warning" variant="tonal" role="alert">{{ $t('errors.forbiddenText') }}</v-alert>
    <v-btn class="mt-4" to="/" prepend-icon="mdi-home-outline">{{ $t('nav.dashboard') }}</v-btn>
  </div>
</template>
