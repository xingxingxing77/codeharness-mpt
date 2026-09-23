/** 工具调用行的「动词 + 摘要」派生。
 *
 * 照参照系的派生法，不是我们自己发明的：`ui-tool/src/client/tool/models/tool-call-model.ts:24-70`
 * 的 `TOOL_VARIANTS`/`VARIANT_TITLES` + `:143-151` 的 `SUMMARY_KEYS`——它那一行
 * `Read · app\api\endpoints\admin.py` 的"标题"**也不是模型给的**，而是「变体名 + 从 args 里挑一个键」。
 * 所以我们后端只发事实（工具名 + 参数摘要 + 结果首行），这里派生显示，
 * 不去新造一个"意图"字段要求模型填（那要动 prompt，属 C6 那类风险）。 */

/** 本仓 18 只工具 → 参照系那六个变体名。没列进来的走 `Tool call · 工具名`。 */
const TOOL_VARIANT: Record<string, string> = {
  read_file: 'Read', open_file: 'Read', goto_line: 'Read', scroll_down: 'Read', scroll_up: 'Read',
  search_dir: 'Grep', search_file: 'Grep', find_file: 'Glob',
  search_internet: 'Search',
  write_file: 'Write', create_file: 'Write', append_file: 'Write',
  edit_file_by_replace: 'Edit', insert_content_at_line: 'Edit',
  execute_shell_async: 'Bash', terminal_command: 'Bash',
  git_create_pull: 'Bash', git_create_issue: 'Bash'
}

const VARIANT_ICON: Record<string, string> = {
  Read: 'inspect', Grep: 'inspect', Glob: 'inspect', Search: 'globe',
  Write: 'edit', Edit: 'edit', Bash: 'code'
}

/** 摘要取哪个参数：照参照系 `SUMMARY_KEYS` 的优先级（路径 → 命令 → 查询 → 其它）。 */
const SUMMARY_KEYS = ['path', 'file_path', 'filename', 'command', 'query', 'pattern', 'url', 'repo_name']

/** 长绝对路径在窄列里会被从头切掉（看不见文件名），所以保尾段——
 *  参照系是 `relativizeToCwd()` 去掉 cwd 前缀，我们没有 cwd 可读，取最后三段是同一目的的近似。 */
function tail(path: string, keep = 3): string {
  const parts = path.split(/[\\/]/).filter(Boolean)
  if (parts.length <= keep) return path
  return '…/' + parts.slice(-keep).join('/')
}

const PATH_KEYS = new Set(['path', 'file_path', 'filename'])

export function toolRow(tool?: string | null, args?: Record<string, string> | null):
  { title: string; icon: string; summary: string } {
  const variant = (tool && TOOL_VARIANT[tool]) || ''
  const a = args || {}
  let summary = ''
  for (const k of SUMMARY_KEYS) {
    if (a[k]) { summary = PATH_KEYS.has(k) ? tail(a[k]) : a[k]; break }
  }
  if (!summary) {
    const first = Object.values(a)[0] || ''
    summary = String(first).split('\n')[0]
  }
  return {
    // 参照系 `others` 变体的形状是 `${toolName} · ${base}`：认不出动词时至少让人看见是哪个工具，
    // 而不是统一一句"工具调用"糊过去。
    title: variant || 'Tool call',
    icon: VARIANT_ICON[variant] || 'settings',
    summary: variant ? summary : `${tool || 'unknown'}${summary ? ' · ' + summary : ''}`
  }
}
