<template>
  <span v-if="label" class="agentChip" :title="`当前子 agent：${label}`">
    <DsIcon class="ic" name="user" :size="14" />
    <span class="txt">{{ label }}</span>
    <span v-if="running" class="live" aria-hidden="true" />
  </span>
</template>

<script setup lang="ts">
/** 会话头部的「此刻是哪个子 agent」胶囊。参照系**流内不显示角色名**（取证：
 *  `ui-subagent/src/client/SubagentHeaderLineage.tsx` 把它挂在**头部**槽位
 *  `conversation.session.header.lineage`，树行文案是 `label · role · activity`），
 *  所以这里也放头部、不放进行内——语义最近的类比是 `AgentPresetLabel`（"当前跑的是什么"）。
 *  几何照本仓同槽位的现成胶囊 `.modeChip`（`ConversationRoot.vue:292`）：
 *  参照系那份用的 `--dsw-alias-fill-tsp-secondary` 在我们 token 表里没有，
 *  硬造一个 token 不如复用同族读数，两个胶囊并排才不会长得不一样。 */
import DsIcon from '../ui/DsIcon.vue'

defineProps<{ label: string; running?: boolean }>()
</script>

<style scoped>
.agentChip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 180px;
  padding: 1px 8px;
  border-radius: 12px;
  background: var(--dsw-alias-bg-module-platform);
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-secondary);
  white-space: nowrap;
}

/* 参照系 AgentPresetLabel.module.css：图标 flex:none + opacity .7 */
.ic {
  flex: none;
  opacity: 0.7;
}

.txt {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 跑动中的小圆点：颜色用 state-success-primary，停着的时候不出现（宁缺不假） */
.live {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--dsw-alias-state-success-primary);
}
</style>
