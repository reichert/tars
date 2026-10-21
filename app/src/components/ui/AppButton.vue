<script setup lang="ts">
withDefaults(
  defineProps<{
    size?: "sm" | "md" | "lg";
    variant?: "primary" | "secondary" | "ghost" | "danger";
    busy?: boolean;
    disabled?: boolean;
    type?: "button" | "submit" | "reset";
  }>(),
  { variant: "secondary", size: "md", type: "button" },
);
</script>
<template>
  <button
    :type="type"
    class="btn"
    :class="{
      'btn-sm': size === 'sm',
      'btn-lg': size === 'lg',
      pri: variant === 'primary',
      ghost: variant === 'ghost',
      'btn-danger': variant === 'danger',
    }"
    :disabled="disabled || busy"
    :aria-busy="busy || undefined"
  >
    <slot />
  </button>
</template>
