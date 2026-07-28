<script setup lang="ts">
import { computed, ref } from 'vue'

const props = withDefaults(defineProps<{
  adapterId: string
  label: string
  testId: string
  defaultExpanded?: boolean
}>(), {
  defaultExpanded: true,
})

const expanded = ref(props.defaultExpanded)
const contentId = computed(() => `hardware-adapter-content-${props.adapterId}`)
const toggleLabel = computed(() => `${expanded.value ? '收起' : '展开'}${props.label}`)

/** Keeps mounted adapter controls alive while the operator collapses their visual section. */
function toggle(): void {
  expanded.value = !expanded.value
}
</script>

<template>
  <section
    class="shrink-0 border-b border-slate-800 bg-slate-950 last:border-b-0"
    :data-testid="testId"
    :aria-label="label"
  >
    <h3>
      <button
        class="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-slate-900/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-sky-400"
        type="button"
        :aria-controls="contentId"
        :aria-expanded="expanded"
        :aria-label="toggleLabel"
        @click="toggle"
      >
        <span class="min-w-0">
          <span class="block text-[10px] font-bold uppercase tracking-widest text-slate-500">Adapter</span>
          <span class="mt-0.5 block truncate text-sm font-bold text-slate-100">{{ label }}</span>
        </span>
        <span
          aria-hidden="true"
          class="h-2.5 w-2.5 shrink-0 rotate-45 border-b-2 border-r-2 border-slate-500 transition-transform"
          :class="expanded ? '-translate-y-0.5 rotate-[225deg]' : 'translate-y-0 rotate-45'"
        ></span>
      </button>
    </h3>
    <div
      :id="contentId"
      v-show="expanded"
      :data-testid="`hardware-adapter-content-${adapterId}`"
    >
      <slot />
    </div>
  </section>
</template>
