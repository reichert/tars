<script setup lang="ts">
// Liga/desliga que salva na hora; se o backend recusar, volta o controle ao valor confirmado.
import { computed } from "vue";
import type { SettingKey, SettingsForm } from "./useSettingsForm";

const props = defineProps<{
  form: SettingsForm;
  field: SettingKey;
  label: string;
  hint?: string;
}>();

const checked = computed(() => Boolean((props.form.settings.value as Record<string, unknown> | null)?.[props.field]));
const id = `toggle-${props.field}`;

async function onChange(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement;
  const ok = await props.form.save(props.field, input.checked as never);
  if (!ok) input.checked = checked.value;
}
</script>

<template>
  <div class="toggle">
    <label class="row" :for="id">
      <input :id="id" type="checkbox" :checked="checked" :disabled="form.saving[field]" :aria-describedby="hint ? `${id}-hint` : undefined" :aria-invalid="form.errors[field] ? true : undefined" :aria-errormessage="form.errors[field] ? `${id}-error` : undefined" @change="onChange" />
      <span>{{ label }}</span>
    </label>
    <p v-if="hint" :id="`${id}-hint`" class="s-note">{{ hint }}</p>
    <p v-if="form.errors[field]" :id="`${id}-error`" class="s-error" role="alert" data-test="field-error">{{ form.errors[field] }}</p>
  </div>
</template>

<style scoped>
.toggle {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.row {
  display: flex;
  gap: 10px;
  align-items: center;
  cursor: pointer;
  font-weight: 500;
}
input {
  width: 18px;
  height: 18px;
  accent-color: var(--accent);
}
</style>
