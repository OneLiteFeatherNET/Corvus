<template>
  <div>
    <PageHeader :title="$t('errors.forbiddenTitle')" />
    <M3Alert tone="warning" role="alert">{{ $t('errors.forbiddenText') }}</M3Alert>
    <M3Button class="mt-4" to="/" icon="mdi-home-outline">{{ $t('nav.dashboard') }}</M3Button>
  </div>
</template>
