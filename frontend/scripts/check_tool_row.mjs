/** 工具调用行动词/摘要派生的自测：node frontend/scripts/check_tool_row.mjs
 *  纯函数 + 断言，无框架。守的是「这一行要让人看懂在干什么」——后端只发工具名与参数，
 *  派生规则一旦漂（取错键、把整份内容当摘要、图标名不在字形表），界面上就是空白行或半截路径。 */
import fs from 'node:fs'
import { toolRow } from '../src/utils/toolRow.ts'

let failed = 0
function eq(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  if (!ok) failed++
  console.log(`${ok ? '✅' : '❌'} ${name}${ok ? '' : ` 得到 ${JSON.stringify(got)} 期望 ${JSON.stringify(want)}`}`)
}

// 1) 读类：动词 Read，长绝对路径保**最后三段**（从头切会只剩盘符；参照系是 relativize 到 cwd，
//    我们没有 cwd 可读，保尾段是同一目的的近似——第一版我把期望写成四段，是期望错不是代码错）
eq('read_file → Read', toolRow('read_file', { path: 'E:/Codeharness/workspace/s1/src/app/core/admin.py' }),
  { title: 'Read', icon: 'inspect', summary: '…/app/core/admin.py' })
eq('短路径不加省略号', toolRow('open_file', { path: 'a/b.py' }).summary, 'a/b.py')

// 2) 检索类：Grep 取 pattern、Search 取 url/query
eq('search_dir → Grep', toolRow('search_dir', { pattern: 'def get_|IngestionService|VectorIndexer' }),
  { title: 'Grep', icon: 'inspect', summary: 'def get_|IngestionService|VectorIndexer' })
eq('find_file → Glob', toolRow('find_file', { pattern: 'handler.py' }).title, 'Glob')
eq('search_internet → Search', toolRow('search_internet', { query: 'langchain astream' }).title, 'Search')

// 3) 执行与写入
eq('shell → Bash + 命令原文', toolRow('execute_shell_async', { command: 'pytest -q' }),
  { title: 'Bash', icon: 'code', summary: 'pytest -q' })
eq('write_file → Write', toolRow('write_file', { path: 'note.txt' }).title, 'Write')
eq('edit → Edit', toolRow('edit_file_by_replace', { path: 'x.py' }).title, 'Edit')

// 4) 取键优先级：path 在 command 之前（参照系 SUMMARY_KEYS 同一顺序）
eq('两键并存取路径', toolRow('read_file', { command: 'ls', path: 'a/b/c/d.txt' }).summary, '…/b/c/d.txt')

// 5) 认不出的工具：照参照系 `others` 的形状带上工具名，不糊成一句「工具调用」
eq('未知工具带名字', toolRow('teleport', { target: 'moon' }),
  { title: 'Tool call', icon: 'settings', summary: 'teleport · moon' })
eq('工具名缺失也不炸', toolRow(null, null), { title: 'Tool call', icon: 'settings', summary: 'unknown' })

// 6) 多行参数只取首行（整份文件内容不许成为摘要）
eq('摘要不吃第二行', toolRow('write_file', { content: '第一行\n第二行\n第三行' }).summary, '第一行')

// 7) 图标名必须能在 DsIcon 别名表里取到——取不到会渲染成**空 svg**（不是豆腐块），
//    这类缺陷只有活体才看得见，所以在纯函数层先钉住。
const alias = fs.readFileSync(new URL('../src/components/ui/DsIcon.vue', import.meta.url), 'utf8')
for (const icon of ['inspect', 'globe', 'edit', 'code', 'settings'])
  eq(`图标名 ${icon} 在别名表`, new RegExp(`^\\s+${icon}:`, 'm').test(alias), true)

console.log(failed ? `check_tool_row: ${failed} 条失败` : 'check_tool_row: 全绿')
process.exit(failed ? 1 : 0)
