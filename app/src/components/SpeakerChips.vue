<script setup lang="ts">
// Etiquetas dos participantes (cor por `color_index`). Falante sem voz cadastrada que não é "Você"
// fica com borda tracejada e "identificar"; o clique é re-emitido para abrir o menu do falante
// ("Você" não tem menu: a etiqueta não é clicável).
import type { Speaker } from "../api/types";
import { speakerColor } from "../lib/format";
import { speakerName } from "../lib/meeting";

defineProps<{ speakers: Speaker[] }>();
const emit = defineEmits<{ select: [speaker: Speaker, event: MouseEvent] }>();

const unidentified = (s: Speaker) => !s.voice_id && !s.is_me;
</script>

<template>
  <div class="chips">
    <template v-for="s in speakers" :key="s.id">
    <span v-if="s.is_me" class="chip me" :data-speaker="s.id">
      <i :style="{ background: speakerColor(s.color_index) }"></i>{{ speakerName(s) }}
    </span>
    <button
      v-else
      type="button"
      class="chip"
      :class="{ unknown: unidentified(s) }"
      :data-speaker="s.id"
      @click="emit('select', s, $event)"
    >
      <i :style="{ background: speakerColor(s.color_index) }"></i>{{ speakerName(s) }}<template v-if="unidentified(s)"> · identificar</template>
    </button>
    </template>
  </div>
</template>

<style scoped>
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 4px 0;
}
.chip {
  padding: 3px 9px;
  border-radius: 999px;
  font-size: .75rem;
  min-height:32px;
  border: 1px solid var(--border-control);
  background: transparent;
  cursor: pointer;
}
.chip:hover:not(.me) {
  background: var(--hover);
}
.chip.me {
  cursor: default;
}
.chip i {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 5px;
}
.chip.unknown {
  border-style: dashed;
  color: var(--warn);
}
</style>
