<script setup>
defineProps({
  text: { type: String, required: true },
  label: { type: String, default: '' },
});
</script>

<template>
  <button type="button" class="help" :aria-label="label || text">
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="6.67" />
      <path d="M6.06 6a2 2 0 0 1 3.89.67c0 1.33-2 2-2 2" />
      <circle cx="8" cy="11.4" r=".85" class="dot" />
    </svg>
    <span class="tip" role="tooltip">{{ text }}</span>
  </button>
</template>

<style scoped>
.help {
  position: relative;
  flex: none;
  width: 1.077rem;
  height: 1.077rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  background: none;
  color: var(--text-faint);
  cursor: help;
  transition: color .12s;
}
.help svg {
  width: 1rem;
  height: 1rem;
  display: block;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.2;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.help svg .dot {
  fill: currentColor;
  stroke: none;
}
.help:hover,
.help:focus-visible {
  color: var(--text-dim);
  outline: none;
}
.help:focus-visible {
  color: var(--text);
  box-shadow: 0 0 0 0.154rem var(--signal-dim);
  border-radius: 50%;
}
.tip {
  position: absolute;
  left: -0.077rem;
  bottom: calc(100% + 0.538rem);
  z-index: 30;
  width: max-content;
  max-width: 19.23rem;
  padding: 0.538rem 0.692rem;
  border: 1px solid var(--line-2);
  border-radius: 0.308rem;
  background: var(--panel-2);
  color: var(--text-dim);
  font-family: var(--font-mono);
  font-size: 0.769rem;
  line-height: 1.6;
  text-align: left;
  white-space: normal;
  box-shadow: 0 0.462rem 1.385rem rgba(0, 0, 0, .35);
  opacity: 0;
  visibility: hidden;
  transform: translateY(0.154rem);
  transition: opacity .12s, transform .12s, visibility .12s;
  pointer-events: none;
}
.help:hover .tip,
.help:focus-visible .tip {
  opacity: 1;
  visibility: visible;
  transform: none;
}
</style>
