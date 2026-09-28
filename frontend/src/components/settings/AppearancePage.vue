<template>
  <div class="spage">
    <h1 class="sptitle">外观</h1>

    <div class="ssec">主题</div>
    <div class="ssec-sub">选择应用的配色方案</div>
    <RadioCards v-model="themePref" :options="themes" />

    <div class="ssec">显示</div>
    <div class="sgroup">
      <Row title="界面语言" desc="与常规设置中的语言项同步生效">
        <SelectBox v-model="p.language" :options="['自动检测', '简体中文', 'English']" />
      </Row>
      <!-- C95：原「显示」组里那个空开关（无消费者、不落盘、不参与任何样式，切了没效果、
           刷新即回）删掉了——「界面控件说了不算」的纪律不许它留在这（s8 t25 守卫盯字面量）。 -->
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { useThemeStore, type ThemePref } from '../../stores/theme'
import RadioCards from './RadioCards.vue'
import Row from './Row.vue'
import SelectBox from './SelectBox.vue'

const p = useSettingsStore().prefs
const theme = useThemeStore()

const themePref = computed({
  // 存的是「选择」而非「解析结果」：选完 system 再回来仍显示 system
  get: () => theme.pref,
  set: (v: string) => theme.set(v as ThemePref)
})

const themes = [
  { value: 'light', icon: 'sun', title: '亮色', desc: '适合明亮环境的浅色主题' },
  { value: 'dark', icon: 'moon', title: '暗色', desc: '低亮度环境下的深色主题' },
  { value: 'system', icon: 'monitor', title: '跟随系统', desc: '随操作台的明暗设置切换' }
]
</script>
