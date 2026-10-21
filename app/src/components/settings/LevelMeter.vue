<script setup lang="ts">
// Medidor horizontal de nível em dBFS. A barra cobre de -60 a 0 dB (abaixo disso já é silêncio;
// o piso do backend é -90).
import AppIcon from "../ui/AppIcon.vue";
import { computed } from "vue";

const FLOOR_DB = -60;

const props = defineProps<{
  /** Nome do ícone de áudio. */
  icon: string;
  label: string;
  /** Nível em dBFS (≤ -90 = sem sinal). */
  db: number;
}>();

const percent = computed(() => {
  const clamped = Math.min(0, Math.max(FLOOR_DB, props.db));
  return Math.round(((clamped - FLOOR_DB) / -FLOOR_DB) * 100);
});
const level = computed(() => (props.db > -3 ? "hot" : props.db > -12 ? "loud" : "ok"));
const text = computed(() => (props.db <= -90 ? "sem sinal" : `${Math.round(props.db)} dB`));
</script>

<template>
  <div class="meter">
    <AppIcon class="icon" :name="icon" :size="18" />
    <div
      class="track"
      role="meter"
      :aria-label="label"
      aria-valuemin="-90"
      aria-valuemax="0"
      :aria-valuenow="Math.round(db)"
    >
      <div class="bar" :class="level" :style="{ width: percent + '%' }" data-test="meter-bar"></div>
    </div>
    <span class="value">{{ text }}</span>
  </div>
</template>

<style scoped>
.meter {
  display: flex;
  align-items: center;
  gap: 10px;
}
.icon {
  width: 22px;
  text-align: center;
}
.track {
  flex: 1;
  height: 10px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 5px;
  overflow: hidden;
}
.bar {
  height: 100%;

}
.bar.ok {
  background: var(--success);
}
.bar.loud {
  background: var(--warn);
}
.bar.hot {
  background: var(--danger);
}
.value {
  width: 76px;
  text-align: right;
  color: var(--muted);
  font-size: 0.85rem;
  font-variant-numeric: tabular-nums;
}
</style>
