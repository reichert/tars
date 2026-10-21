<script setup lang="ts">
import {
  useAppearance,
  type Appearance,
} from "../../composables/useAppearance";
import AppIcon from "./AppIcon.vue";
const { preference, setAppearance } = useAppearance();
</script>
<template>
  <label class="appearance-control"
    ><AppIcon name="sun" :size="16" /><span class="sr-only">Aparência</span
    ><select
      :value="preference"
      @change="
        setAppearance(($event.target as HTMLSelectElement).value as Appearance)
      "
    >
      <option value="system">Tema do sistema</option>
      <option value="light">Tema claro</option>
      <option value="dark">Tema escuro</option>
    </select></label
  >
</template>
<style scoped>
.appearance-control {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: var(--font-meta);
}
select {
  min-width: 0;
  max-width: 100%;
  min-height: 36px;
  background: transparent;
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 4px 8px;
  font: inherit;
}
</style>
