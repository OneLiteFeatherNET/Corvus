<template>
  <header class="sticky top-0 z-30 flex h-16 items-center gap-1 border-b border-outline-variant bg-surface px-2 sm:px-4">
    <slot name="leading" />
    <div class="min-w-0 flex-1"><slot name="title" /></div>
    <div class="flex items-center gap-1"><slot name="actions" /></div>
  </header>
</template>
