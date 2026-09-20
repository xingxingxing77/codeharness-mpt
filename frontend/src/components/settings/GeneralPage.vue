<template>
  <div class="spage">
    <h1 class="sptitle">常规</h1>

    <div class="ssec">工作模式</div>
    <div class="ssec-sub">选择 MetaGPT 显示多少技术细节</div>
    <RadioCards v-model="p.workMode" :options="workModes" />

    <div class="ssec">权限</div>
    <div class="sgroup">
      <Row title="默认权限" desc="默认情况下，MetaGPT 可以读取并编辑其工作区中的文件。必要时，它可以请求额外的访问权限">
        <Toggle v-model="p.defaultPermission" />
      </Row>
      <Row title="自动审核">
        <template #desc>
          MetaGPT 可以读取和编辑其工作区中的文件。MetaGPT 会自动审核额外访问权限请求。自动审核可能会出错。<span class="slink">了解更多</span>有关高风险的信息。
        </template>
        <Toggle v-model="p.autoReview" />
      </Row>
      <Row title="完全访问权限">
        <template #desc>
          当 MetaGPT 以完全访问权限运行时，无需你批准，即可编辑你的电脑上的任何文件并运行联网命令。这会显著增加数据丢失、泄露或意外行为的风险。<span class="slink">了解更多</span>有关风险的信息。
        </template>
        <Toggle v-model="p.fullAccess" />
      </Row>
    </div>

    <div class="ssec">常规</div>
    <div class="sgroup">
      <Row title="默认打开目标" desc="默认打开文件和文件夹的位置">
        <SelectBox v-model="p.openTarget" :options="['Visual Studio', 'VS Code', '浏览器', '记事本']" />
      </Row>
      <Row title="智能体环境" desc="选择智能体在 Windows 上的运行位置">
        <SelectBox v-model="p.agentEnv" :options="['Windows 原生', 'WSL', 'Docker 沙盒']" />
      </Row>
      <Row title="集成终端 Shell" desc="选择要在集成终端中打开的 Shell。">
        <SelectBox v-model="p.shell" :options="['PowerShell', 'CMD', 'Git Bash']" />
      </Row>
      <Row title="语言" desc="应用 UI 语言">
        <SelectBox v-model="p.language" :options="['自动检测', '简体中文', 'English']" />
      </Row>
      <Row title="需按 ^ + 回车键发送长文本提示" desc="启用后，长文本提示需按 ^ + 回车键发送。">
        <Toggle v-model="p.sendWithCtrlOnly" />
      </Row>
    </div>

    <div class="ssec">弹出窗口</div>
    <div class="sgroup">
      <Row title="弹出窗口快捷键" desc="为弹出窗口设置全局快捷键。留空则保持关闭。">
        <span class="s-desc" style="font-size: 12.5px; color: var(--text-3)">{{ p.popupShortcut || '禁用' }}</span>
        <button class="sbtn" @click="setShortcut">设置</button>
      </Row>
      <Row title="默认使用无项目聊天" desc="无需项目即可开始新聊天">
        <Toggle v-model="p.noProjectChat" />
      </Row>
    </div>

    <div class="ssec">听写</div>
    <div class="sgroup">
      <Row title="按住听写快捷键" desc="在桌面任意位置按住，即可在光标处听写">
        <span class="s-desc" style="font-size: 12.5px; color: var(--text-3)">{{ p.dictationHold }}</span>
        <button class="sbtn" @click="setShortcut">设置</button>
      </Row>
      <Row title="切换听写快捷键" desc="在桌面任意位置按一次开始听写，再按一次停止">
        <span class="s-desc" style="font-size: 12.5px; color: var(--text-3)">{{ p.dictationToggle }}</span>
        <button class="sbtn" @click="setShortcut">设置</button>
      </Row>
      <Row title="听写词典" desc="听写应能识别的单词或短语">
        <Icon name="chevron-down" :size="15" style="color: var(--text-3)" />
      </Row>
      <Row title="最近的听写记录" desc="你最近的听写记录会显示在这里，便于在文本没有出现在预期位置时找回内容" />
    </div>

    <div class="ssec">通知</div>
    <div class="sgroup">
      <Row title="轮次完成通知" desc="设置 MetaGPT 完成任务时的提醒">
        <SelectBox v-model="p.noticeTurn" :options="['仅当应用失焦时', '总是', '从不']" />
      </Row>
      <Row title="启用权限通知" desc="在需要通知权限时显示提醒">
        <Toggle v-model="p.noticePermission" />
      </Row>
      <Row title="启用问题通知" desc="需要输入才能继续时显示提醒">
        <Toggle v-model="p.noticeIssue" />
      </Row>
    </div>

    <div class="ssec">Composer footer</div>
    <div class="sgroup">
      <Row title="Show context window usage" desc="Show context window usage in the composer footer">
        <Toggle v-model="p.contextUsage" />
      </Row>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useToastStore } from '../../stores/toast'
import { useSettingsStore } from '../../stores/settings'
import Icon from '../Icon.vue'
import RadioCards from './RadioCards.vue'
import Row from './Row.vue'
import SelectBox from './SelectBox.vue'
import Toggle from './Toggle.vue'

const p = useSettingsStore().prefs
const message = useToastStore()

const workModes = [
  { value: 'coding', icon: 'terminal', title: '适用于编程', desc: '更具技术性的回复和控制' },
  { value: 'daily', icon: 'chat', title: '适用于日常工作', desc: '同样强大，技术细节更少' }
]

function setShortcut() {
  message.push('演示环境：快捷键录制未开放')
}
</script>
