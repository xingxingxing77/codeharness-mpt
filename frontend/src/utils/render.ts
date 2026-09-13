import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github-dark.css'

const md: MarkdownIt = new MarkdownIt({ html: false, linkify: true, breaks: false })

md.set({
  highlight(code: string, lang: string): string {
    try {
      if (lang && hljs.getLanguage(lang)) {
        return `<pre class="hljs"><code>${hljs.highlight(code, { language: lang }).value}</code></pre>`
      }
      const auto = hljs.highlightAuto(code)
      return `<pre class="hljs"><code>${auto.value}</code></pre>`
    } catch {
      return `<pre class="hljs"><code>${md.utils.escapeHtml(code)}</code></pre>`
    }
  }
})

export function renderMarkdown(text: string): string {
  return md.render(text || '')
}

const EXT_LANG: Record<string, string> = {
  '.py': 'python',
  '.js': 'javascript',
  '.ts': 'typescript',
  '.html': 'html',
  '.css': 'css',
  '.json': 'json',
  '.md': 'markdown',
  '.yaml': 'yaml',
  '.yml': 'yaml',
  '.sh': 'bash',
  '.java': 'java',
  '.go': 'go',
  '.sql': 'sql'
}

export function langOfFilename(filename: string): string {
  if (!filename) return ''
  const i = filename.lastIndexOf('.')
  return i >= 0 ? EXT_LANG[filename.slice(i).toLowerCase()] || '' : ''
}

export function highlightCode(code: string, lang: string): string {
  try {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value
    }
    return hljs.highlightAuto(code).value
  } catch {
    return md.utils.escapeHtml(code)
  }
}
