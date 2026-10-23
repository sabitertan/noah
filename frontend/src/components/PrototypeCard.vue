<script setup>
// A build inside a chat (builds started from the chat before the Builder page existed still show this way).
import BuildSteps from './BuildSteps.vue';
import PrototypeFrame from './PrototypeFrame.vue';

defineProps({ b: Object, error: String });
</script>

<template>
  <div class="a build">
    <BuildSteps :b="b" :error="error" />
    <PrototypeFrame v-if="b.html" :b="b" />
  </div>
</template>
