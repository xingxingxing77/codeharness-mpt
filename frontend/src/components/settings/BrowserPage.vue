<template>
  <div class="spage">
    <h1 class="sptitle">浏览器</h1>
    <p class="spdesc">管理 MetaGPT 的浏览器。可在<span class="slink">计算机使用设置</span>中设置 Google Chrome</p>

    <div class="sgroup empty" style="margin-top: 18px">应用内浏览器插件不可用</div>

    <div class="ssec">数据</div>
    <div class="sgroup">
      <Row title="浏览数据" desc="清除应用内浏览器中的网站数据和缓存">
        <button class="sbtn" @click="clearData">
          清除所有浏览数据
          <Icon name="chevron-down" :size="13" />
        </button>
      </Row>
      <Row title="批注截图" desc="截图可帮助 MetaGPT 更好地理解并处理评论，但会增加套餐用量">
        <SelectBox v-model="p.screenshot" :options="['始终包含', '提示时', '从不']" />
      </Row>
    </div>

    <div class="ssec">权限</div>
    <div class="sgroup">
      <Row title="审批">
        <template #desc>
          选择是否让 MetaGPT 在打开网站前先请求批准。<span class="slink">了解更多</span>
        </template>
        <SelectBox v-model="p.webApproval" :options="['始终访问', '每次询问', '从不']" />
      </Row>
    </div>

    <div class="list-sec">
      <div>
        <div class="ssec" style="margin: 0">已屏蔽的域名</div>
        <div class="ssec-sub" style="margin-top: 3px">MetaGPT 绝不会打开这些网站</div>
      </div>
      <span class="grow" />
      <button class="sbtn" @click="addDomain('blocked')">
        <Icon name="plus" :size="13" />
        添加
      </button>
    </div>
    <div class="sgroup empty">没有已屏蔽的域名</div>

    <div class="list-sec">
      <div>
        <div class="ssec" style="margin: 0">允许的域名</div>
        <div class="ssec-sub" style="margin-top: 3px">无需询问即可打开的域名</div>
      </div>
      <span class="grow" />
      <button class="sbtn" @click="addDomain('allowed')">
        <Icon name="plus" :size="13" />
        添加
      </button>
    </div>
    <div class="sgroup empty">没有允许的域名</div>
  </div>
</template>

<script setup lang="ts">
import { useToastStore } from '../../stores/toast'
import { useSettingsStore } from '../../stores/settings'
import Icon from '../Icon.vue'
import Row from './Row.vue'
import SelectBox from './SelectBox.vue'

const p = useSettingsStore().prefs
const message = useToastStore()

function clearData() {
  message.push('已清除应用内浏览数据', 'success')
}

function addDomain(kind: 'blocked' | 'allowed') {
  message.push('演示环境：域名管理未开放')
  void kind
}
</script>

<style scoped>
.list-sec {
  display: flex;
  align-items: center;
  margin: 30px 0 10px;
}

.grow {
  flex: 1;
}
</style>
