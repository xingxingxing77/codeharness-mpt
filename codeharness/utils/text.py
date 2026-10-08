import re
from typing import Generator, Sequence

from codeharness.utils.token_counter import TOKEN_MAX, count_output_tokens


def reduce_message_length(
    msgs: Generator[str, None, None],
    model_name: str,
    system_text: str,
    reserved: int = 0,
) -> str:
    """Reduce the length of concatenated message segments to fit within the maximum token size.

    Args:
        msgs: A generator of strings representing progressively shorter valid prompts.
        model_name: The name of the encoding to use. (e.g., "gpt-3.5-turbo")
        system_text: The system prompts.
        reserved: The number of reserved tokens.

    Returns:
        The concatenated message segments reduced to fit within the maximum token size.

    Raises:
        RuntimeError: If it fails to reduce the concatenated message length.
    """
    max_token = TOKEN_MAX.get(model_name, 2048) - count_output_tokens(system_text, model_name) - reserved
    for msg in msgs:
        if count_output_tokens(msg, model_name) < max_token or model_name not in TOKEN_MAX:
            return msg

    raise RuntimeError("fail to reduce message length")


def generate_prompt_chunk(
    text: str,
    prompt_template: str,
    model_name: str,
    system_text: str,
    reserved: int = 0,
) -> Generator[str, None, None]:
    """Split the text into chunks of a maximum token size.

    Args:
        text: The text to split.
        prompt_template: The template for the prompt, containing a single `{}` placeholder. For example, "### Reference\n{}".
        model_name: The name of the encoding to use. (e.g., "gpt-3.5-turbo")
        system_text: The system prompts.
        reserved: The number of reserved tokens.

    Yields:
        The chunk of text.
    """
    paragraphs = text.splitlines(keepends=True)
    current_token = 0
    current_lines = []

    reserved = reserved + count_output_tokens(prompt_template + system_text, model_name)
    # 100 is a magic number to ensure the maximum context length is not exceeded
    max_token = TOKEN_MAX.get(model_name, 2048) - reserved - 100

    while paragraphs:
        paragraph = paragraphs.pop(0)
        token = count_output_tokens(paragraph, model_name)
        if current_token + token <= max_token:
            current_lines.append(paragraph)
            current_token += token
        elif token > max_token:
            paragraphs = split_paragraph(paragraph) + paragraphs
            continue
        else:
            yield prompt_template.format("".join(current_lines))
            current_lines = [paragraph]
            current_token = token

    if current_lines:
        yield prompt_template.format("".join(current_lines))


def split_paragraph(paragraph: str, sep: str = ".,", count: int = 2) -> list[str]:
    """Split a paragraph into multiple parts.

    Args:
        paragraph: The paragraph to split.
        sep: The separator character.
        count: The number of parts to split the paragraph into.

    Returns:
        A list of split parts of the paragraph.
    """
    for i in sep:
        sentences = list(_split_text_with_ends(paragraph, i))
        if len(sentences) <= 1:
            continue
        ret = ["".join(j) for j in _split_by_count(sentences, count)]
        return ret
    return list(_split_by_count(paragraph, count))


def decode_unicode_escape(text: str) -> str:
    """Decode a text with unicode escape sequences.

    Args:
        text: The text to decode.

    Returns:
        The decoded text.
    """
    return text.encode("utf-8").decode("unicode_escape", "ignore")


def _split_by_count(lst: Sequence, count: int):
    avg = len(lst) // count
    remainder = len(lst) % count
    start = 0
    for i in range(count):
        end = start + avg + (1 if i < remainder else 0)
        yield lst[start:end]
        start = end


def _split_text_with_ends(text: str, sep: str = "."):
    parts = []
    for i in text:
        parts.append(i)
        if i == sep:
            yield "".join(parts)
            parts = []
    if parts:
        yield "".join(parts)



_TRUNC_NOTE = re.compile("…\\[已截断，原长 (\\d+) 字\\]$")


def compact_note(n: int) -> str:
    """压缩摘要的尾部锚点——与 `clip` 的 `…[已截断，原长 N 字]` 同族（同一句式：省略号 + 方括号
    + 只数事实）。为什么要有它：窗口外的消息被摘要吸收后，模型看不出「更早的内容只是被压缩了」，
    锚点把**已压缩多少条**这个事实挂出来。措辞纪律同 clip：**不许写「可检索回」**——读腿现网
    读数（09-27 全天 kb 腿 1 成功 / 595 失败）之下，引导模型去查一个查不到的东西比不提更糟。"""
    return f"…[已压缩 {n} 条]"


# ---- C185（P4）：classic/react 的**跨阶段事实清单**——自报块的渲染/抽取 ----
FACTS_MAX_PER_REPORT = 5      # 一次自报最多收几条（= 自报指令里那个数，两处同源）
FACTS_MAX_ENTRY_CHARS = 200   # 单条事实的额度（超了走 `clip`，标记照 house 口径挂在句尾）
FACTS_BLOCK_QUOTA = 2000      # **注入段**总额度（字，含留痕行——口径同 `clip`：标记不走额外额度）
_FACTS_NOTE_RESERVE = 20      # 给留痕行预扣的量，保证「含标记总长 ≤ FACTS_BLOCK_QUOTA」可证

FACTS_HINT = (
    f"\n\n另外：请在 facts 字段里列出本阶段**定下的关键事实**（≤{FACTS_MAX_PER_REPORT} 条、每条一行、只写下游必须遵守的"
    "结论——如接口名、文件名、技术选型、硬约束），**不要复述过程**。")
"""自报指令：只在目标 schema 声明了 `facts` 字段时才拼（见 `base/action.py::_ask`）。
措辞纪律与 `compact_note` 同族：只数事实、不承诺任何检索能力。"""

_FACTS_MIN_CHARS = 2          # 短于这个长度的条目丢掉（防「-」「。」这类空条目混进清单）
FACTS_FIELD = "facts"         # 自报字段名（唯一的那个字面量：schema 声明处、`_ask` 问法、`_structured` 排除处共用）


def render_facts(items) -> str:
    """把累积的事实清单渲染成**注入段**（走 `runtime.FACTS_CONTEXT`，与 KB/LTM 两块同形）。
    空清单 ⇒ 空串——调用点据此**逐字不变**（既有 prompt 判据一格不受影响）。

    C186：这一段**必须有额度**。清单在图 state 里照旧只增不减（那是账本），但渲染视图按
    `FACTS_BLOCK_QUOTA` 从最新往回装，装不下的挂一行事实性说明——不静默（同 `clip` 那句理由：
    看不出被切的一方会把半截当全量下结论，而这一方是模型自己）。为什么原来那版不行：现证
    （零花费，真 `Agent.as_node`+真 `merge_facts`，10-08）13 次自报 × 每次 5 条 ⇒ 末发注入段
    **3,008 token**；把「每次自报」换成模型真能给出的 40 条 × 每条 200 字 ⇒ 清单 520 条、
    **单发 187,208 token**，一发就撞穿任何档位（`context_overflow` 那条死因由 C147 钉着）。"""
    rows = [str(x).strip() for x in (items or [])]
    rows = [r for r in rows if len(r) >= _FACTS_MIN_CHARS]
    if not rows:
        return ""
    quota = FACTS_BLOCK_QUOTA - _FACTS_NOTE_RESERVE
    used = len("[跨阶段事实]\n")
    kept, dropped = [], 0
    for i in range(len(rows) - 1, -1, -1):        # 从最新往回装（保序输出）
        line = f"- {rows[i]}"
        if used + len(line) + 1 > quota:
            dropped = i + 1                        # 剩下 i+1 条更旧的没进这一段
            break
        kept.append(line)
        used += len(line) + 1
    body = "[跨阶段事实]\n" + "\n".join(reversed(kept))
    if dropped:
        body += f"\n…[更早 {dropped} 条未列出]"
    return body


def facts_from_instruct(instruct) -> list[str]:
    """从动作产出的 `instruct_content` 里取**自报**事实（`facts` 字段）。
    容错：非 dict / 非 list / 缺字段一律回空——收集这一侧不许因为它抛而把动作打断。

    C186：这里是自报进账本的**唯一收口**，所以两个上界都放在它身上（`FACTS_HINT` 那句「≤N 条」
    原本只是对模型的请求，schema 里 `list[str]` 没有 `max_length`，不夹等于没有界）：条数超过
    `FACTS_MAX_PER_REPORT` 的部分不入库并**响一声**（`[facts-capped]`，与 `[silent-lost]` 同族——
    被丢掉的东西必须有地方查得到），单条超过 `FACTS_MAX_ENTRY_CHARS` 走 `clip` 留截断标记。"""
    if not isinstance(instruct, dict):
        return []
    v = instruct.get("facts")
    if not isinstance(v, (list, tuple)):
        return []
    rows = [str(x).strip() for x in v if str(x).strip()]
    if len(rows) > FACTS_MAX_PER_REPORT:
        from codeharness.logs import logger
        logger.warning(f"[facts-capped] 自报 {len(rows)} 条，只收前 {FACTS_MAX_PER_REPORT} 条，"
                       f"另 {len(rows) - FACTS_MAX_PER_REPORT} 条未入清单")
    return [clip(x, FACTS_MAX_ENTRY_CHARS) for x in rows[:FACTS_MAX_PER_REPORT]]


def _raw_len(s: str) -> int:
    """这段文本的**最上游**原长：它自己若带着上一站 `clip` 的标记，就继承那个数，而不是报自己的长度。

    为什么要继承（09-29 现量，账在 `plan/rag-knowledge.md` §1.5「nested_claim 失真」）：两站叠套时——
    `read_file` 先按 2 万切、`role_zero` 再按 4 千切——**上一站那句标记正好躺在被下一站切掉的那截里**，
    于是 3 万字的文件回喂给模型时写着「原长 20000 字」，而 20001 字与 30000 字两个文件在第二站长得一模一样。
    R7 修的是「每站自己不留痕」，这条补的是同一族谎的另一半——「链上留的痕只承认真上一站的输出」。

    ponytail: 只认**结尾那一串、且数字大于当前长度**（真被截断过的必然如此）。读进来的正文自己以一句
    `…[已截断，原长 999999 字]` 收尾且比它长，就会被继承——那是引用不是截断，但继承来的仍是「这段文本
    自称的原长」而非新判断；要彻底分开得给标记加签名/定界符，不为没出现过的形状先加机制。
    """
    m = _TRUNC_NOTE.search(s)
    if m:
        n = int(m.group(1))
        if n > len(s):
            return n
    return len(s)


def clip(text, n: int) -> str:
    """按字符裁到 n 个，裁掉了就把「原长多少」挂在末尾——**含标记后总长仍 ≤ n**。

    为什么必须有这句：被切的正文此前看不出被切，模型会把半截当全量下结论（C96/R3 那族的「把故障
    演成空态」，这次被骗的是模型自己）。为什么额度从正文里扣：调用点那个上限的语义是「这条结果最多
    占多少上下文」，标记若走额外额度等于悄悄放宽预算，且 `tests/s4_tools.py:129` 那条 `<= 10000`
    可以一字不改。措辞只数得出不判断（C16：不写「已省略无关内容」那种替读者下结论的话）。

    原长走 `_raw_len`（叠套时继承上游真值），不是 `len(s)`——单站行为不变，链上才说得出真话。

    ponytail: `n` 比标记本身（约 16 字）还小时正文只能为 0、总长会超过 `n`。最小调用点是 500，够不到；
    真要下探到那个量级就得先砍措辞，不是改这里。

    P1：`n` 的语义是「这条结果最多占多少上下文」——朴素 1:1 的字符实现经会话校准系数 k 换算
    （`ContextBudget.clip_quota`，**只收紧不放宽**）。尺经 `CURRENT_BUDGET` 现取：无会话上下文
    （脚本/单测/离线）或未校准（k=1）时恒等——16 处调用点与既有 `<= N` 断言一字不动。
    """
    s = text if isinstance(text, str) else str(text)
    from codeharness.runtime import CURRENT_BUDGET
    _budget = CURRENT_BUDGET.get()
    if _budget is not None:
        n = _budget.clip_quota(n)
    if len(s) <= n:
        return s
    note = "…[已截断，原长 " + str(_raw_len(s)) + " 字]"
    return s[:max(n - len(note), 0)] + note

