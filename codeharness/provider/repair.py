"""LLM 原始输出修复管线。来源：metagpt/utils/repair_llm_raw_output.py（398 行）逐符号移植。

判定 `改`：策略实现照源，两处随栈调整——
1. `Config.default().repair_llm_output` → `settings.repair_llm_output`
2. 源用 `import regex as re`；本文件用标准库 `re`（用到的模式均为普通 findall/DOTALL，无变长断言）

对上层暴露两类入口：
- **源签名**（S6 逐字复制 Action 时会按这些名字与签名调用）：`repair_llm_raw_output(output, req_keys, ...)`、
  `extract_content_from_output`、`retry_parse_json_text` 等 14 个符号
- **新栈自有**：`repair_to_model(raw, schema)`（模型级升级式修复）、`llm_repair_json(raw, schema, llm)`
  （最后一档：让 LLM 自修）。⚠ 前者原名也叫 `repair_llm_raw_output`，与源签名冲突，故已改名。
"""
import copy
import json
import re
from enum import Enum
from typing import Callable, Optional, Type, Union

from pydantic import BaseModel, ValidationError
from tenacity import RetryCallState, retry, stop_after_attempt, wait_fixed

from codeharness.logs import logger
from codeharness.utils.custom_decoder import CustomDecoder


class RepairType(Enum):
    CS = "case sensitivity"
    RKPM = "required key pair missing"  # condition like `[key] xx` which lacks `[/key]`
    SCM = "special character missing"  # Usually the req_key appear in pairs like `[key] xx [/key]`
    JSON = "json format"


def repair_case_sensitivity(output: str, req_key: str) -> str:
    """
    usually, req_key is the key name of expected json or markdown content, it won't appear in the value part.
    fix target string `"Shared Knowledge": ""` but `"Shared knowledge": ""` actually
    """
    if req_key in output:
        return output

    output_lower = output.lower()
    req_key_lower = req_key.lower()
    if req_key_lower in output_lower:
        lidx = output_lower.find(req_key_lower)
        source = output[lidx : lidx + len(req_key_lower)]
        output = output.replace(source, req_key)
        logger.info(f"repair_case_sensitivity: {req_key}")
    return output


def repair_special_character_missing(output: str, req_key: str = "[/CONTENT]") -> str:
    """
    fix
        1. target string `[CONTENT] xx [CONTENT] xxx [CONTENT]` lacks `/` in the last `[CONTENT]`
        2. target string `xx [CONTENT] xxx [CONTENT] xxxx` lacks `/` in the last `[CONTENT]`
    """
    sc_arr = ["/"]

    if req_key in output:
        return output

    for sc in sc_arr:
        req_key_pure = req_key.replace(sc, "")
        appear_cnt = output.count(req_key_pure)
        if req_key_pure in output and appear_cnt > 1:
            # req_key with special_character usually in the tail side
            ridx = output.rfind(req_key_pure)
            output = f"{output[:ridx]}{req_key}{output[ridx + len(req_key_pure):]}"
            logger.info(f"repair_special_character_missing: {sc} in {req_key_pure} as position {ridx}")
    return output


def repair_required_key_pair_missing(output: str, req_key: str = "[/CONTENT]") -> str:
    """
    implement the req_key pair in the begin or end of the content
        req_key format
            1. `[req_key]`, and its pair `[/req_key]`
            2. `[/req_key]`, and its pair `[req_key]`
    """
    sc = "/"  # special char
    if req_key.startswith("[") and req_key.endswith("]"):
        if sc in req_key:
            left_key = req_key.replace(sc, "")  # `[/req_key]` -> `[req_key]`
            right_key = req_key
        else:
            left_key = req_key
            right_key = f"{req_key[0]}{sc}{req_key[1:]}"  # `[req_key]` -> `[/req_key]`

        if left_key not in output:
            output = left_key + "\n" + output
        if right_key not in output:

            def judge_potential_json(routput: str, left_key: str) -> Union[str, None]:
                ridx = routput.rfind(left_key)
                if ridx < 0:
                    return None
                sub_output = routput[ridx:]
                idx1 = sub_output.rfind("}")
                idx2 = sub_output.rindex("]")
                idx = idx1 if idx1 >= idx2 else idx2
                return sub_output[: idx + 1]

            if output.strip().endswith("}") or (output.strip().endswith("]") and not output.strip().endswith(left_key)):
                # # avoid [req_key]xx[req_key] case to append [/req_key]
                output = output + "\n" + right_key
            elif judge_potential_json(output, left_key) and (not output.strip().endswith(left_key)):
                sub_content = judge_potential_json(output, left_key)
                output = sub_content + "\n" + right_key
    return output


def repair_json_format(output: str) -> str:
    """fix extra `[` or `}` in the end;并去掉 json 值之后的 # / // 注释。"""
    output = output.strip()

    if output.startswith("[{"):
        output = output[1:]
        logger.info(f"repair_json_format: {'[{'}")
    elif output.endswith("}]"):
        output = output[:-1]
        logger.info(f"repair_json_format: {'}]'}")
    elif output.startswith("{") and output.endswith("]"):
        output = output[:-1] + "}"

    # remove comments in output json string, after json value content, maybe start with #, maybe start with //
    arr = output.split("\n")
    new_arr = []
    for json_line in arr:
        comment_index = -1
        for match in re.finditer(r"(\".*?\"|\'.*?\')|(#|//)", json_line):
            if match.group(1):  # if the string value
                continue
            if match.group(2):  # if comments
                comment_index = match.start(2)
                break
        if comment_index != -1:
            json_line = json_line[:comment_index].rstrip()
        new_arr.append(json_line)
    return "\n".join(new_arr)


def _repair_llm_raw_output(output: str, req_key: str, repair_type: RepairType = None) -> str:
    repair_types = [repair_type] if repair_type else [item for item in RepairType if item not in [RepairType.JSON]]
    for rt in repair_types:
        if rt == RepairType.CS:
            output = repair_case_sensitivity(output, req_key)
        elif rt == RepairType.RKPM:
            output = repair_required_key_pair_missing(output, req_key)
        elif rt == RepairType.SCM:
            output = repair_special_character_missing(output, req_key)
        elif rt == RepairType.JSON:
            output = repair_json_format(output)
    return output


def repair_llm_raw_output(output: str, req_keys: list[str], repair_type: RepairType = None) -> str:
    """源签名：对**文本**按若干 req_key 逐档修复（open-source 模型常不严格遵守指令）。

    typical case
        1. case sensitivity        target "Original Requirements" / output "Original requirements"
        2. special character missing target [/CONTENT] / output [CONTENT]
        3. json format             target { xxx } / output { xxx }]
    """
    from codeharness.configs.settings import settings
    if not settings.repair_llm_output:
        return output
    for req_key in req_keys:
        output = _repair_llm_raw_output(output=output, req_key=req_key, repair_type=repair_type)
    return output


def repair_invalid_json(output: str, error: str) -> str:
    """
    repair the situation like there are extra chars like
    error examples
        example 1. json.decoder.JSONDecodeError: Expecting ',' delimiter: line 154 column 1 (char 2765)
        example 2. xxx.JSONDecodeError: Expecting property name enclosed in double quotes: line 14 column 1 (char 266)
    """
    pattern = r"line ([0-9]+) column ([0-9]+)"

    matches = re.findall(pattern, error, re.DOTALL)
    if len(matches) > 0:
        line_no = int(matches[0][0]) - 1
        col_no = int(matches[0][1]) - 1

        # due to CustomDecoder can handle `"": ''` or `'': ""`, so convert `"""` -> `"`, `'''` -> `'`
        output = output.replace('"""', '"').replace("'''", '"')
        arr = output.split("\n")
        if line_no >= len(arr):
            return output
        rline = arr[line_no]  # raw line
        line = arr[line_no].strip()
        # different general problems
        if line.endswith("],"):
            new_line = line.replace("]", "")
        elif line.endswith("},") and not output.endswith("},"):
            new_line = line.replace("}", "")
        elif line.endswith("},") and output.endswith("},"):
            new_line = line[:-1]
        elif col_no < len(rline) and rline[col_no] in ["'", '"'] and (line.startswith('"') or line.startswith("'")) and "," not in line:
            new_line = f",{line}"
        elif col_no - 1 >= 0 and col_no - 1 < len(rline) and rline[col_no - 1] in ['"', "'"]:
            # backslash problem like \" in the output
            char = rline[col_no - 1]
            nearest_char_idx = rline[col_no:].find(char)
            new_line = (
                rline[: col_no - 1] + "\\" + rline[col_no - 1 : col_no + nearest_char_idx]
                + "\\" + rline[col_no + nearest_char_idx :]
            )
        elif '",' not in line and "," not in line and '"' not in line:
            new_line = f'{line}",'
        elif not line.endswith(","):
            new_line = f"{line},"
        elif "," in line and len(line) == 1:
            new_line = f'"{line}'
        elif '",' in line:
            new_line = line[:-2] + "',"
        else:
            new_line = line

        arr[line_no] = new_line
        output = "\n".join(arr)
        logger.info(f"repair_invalid_json, raw error: {error}")

    return output


def run_after_exp_and_passon_next_retry(logger: "loguru.Logger") -> Callable[["RetryCallState"], None]:
    def run_and_passon(retry_state: RetryCallState) -> None:
        """失败时把**修复后的文本**塞回下一次调用的 kwargs["output"]。

        ⚠ 源里只有当 `output` 是以**关键字**传入时才真正生效（这里写的是
        `retry_state.kwargs["output"]`）——所以新栈的调用点一律写成 `output=...`，
        否则这个两档重试环第二圈仍在重解原始坏文本。这是对源的一处行为修正。
        """
        from codeharness.configs.settings import settings
        if retry_state.outcome.failed:
            if retry_state.args:
                func_param_output = retry_state.args[0]
            elif retry_state.kwargs:
                func_param_output = retry_state.kwargs.get("output", "")
            exp_str = str(retry_state.outcome.exception())

            fix_str = "try to fix it, " if settings.repair_llm_output else ""
            logger.warning(
                f"parse json from content inside [CONTENT][/CONTENT] failed at retry "
                f"{retry_state.attempt_number}, {fix_str}exp: {exp_str}"
            )
            retry_state.kwargs["output"] = repair_invalid_json(func_param_output, exp_str)

    return run_and_passon


def repair_stop_after_attempt(retry_state):
    from codeharness.configs.settings import settings
    return stop_after_attempt(3 if settings.repair_llm_output else 0)(retry_state)


@retry(stop=repair_stop_after_attempt, wait=wait_fixed(1), after=run_after_exp_and_passon_next_retry(logger))
def retry_parse_json_text(output: str) -> Union[list, dict]:
    """
    repair the json-text situation like there are extra chars like [']', '}']

    Warning
        if settings.repair_llm_output is False, retry _aask_v1 {x=3} times, and this retry not work
        if True, the _aask_v1 and this function loop for {x=3*3} times —— two-layer retry cycle
    """
    # CustomDecoder 能处理 `"": ''` / `'': ""` 等非严格 JSON（源 utils/custom_decoder.py 297 行）
    return CustomDecoder(strict=False).decode(output)


def extract_content_from_output(content: str, right_key: str = "[/CONTENT]") -> str:
    """extract xxx from [CONTENT](xxx)[/CONTENT] using regex pattern"""

    def re_extract_content(cont: str, pattern: str) -> str:
        matches = re.findall(pattern, cont, re.DOTALL)
        for match in matches:
            if match:
                cont = match
                break
        return cont.strip()

    raw_content = copy.deepcopy(content)
    pattern = r"\[CONTENT\]([\s\S]*)\[/CONTENT\]"
    new_content = re_extract_content(raw_content, pattern)

    if not new_content.startswith("{"):
        logger.warning(f"extract_content try another pattern: {pattern}")
        if right_key not in new_content:
            raw_content = copy.deepcopy(new_content + "\n" + right_key)
        new_content = re_extract_content(raw_content, pattern)
    else:
        if right_key in new_content:
            idx = new_content.find(right_key)
            new_content = new_content[:idx].strip()
    return new_content


def extract_state_value_from_output(content: str) -> str:
    """For open llm models, the instruction result may be a long text containing the target
    state number, so here add an extraction to improve success rate. (源 :342)"""
    content = content.strip()  # deal the output cases like " 0", "0\n" and so on.
    pattern = r"(?<!-)[0-9]"
    matches = re.findall(pattern, content, re.DOTALL)
    matches = list(set(matches))
    return matches[0] if len(matches) > 0 else "-1"


def repair_escape_error(commands: str) -> str:
    """
    Repairs escape errors in command responses（RoleZero 解析命令时常见）。
    两步（源 :360 注释）：
      1. `\\d`、`\\(` 这类未转义串 → `\\\\d`、`\\\\(`
      2. `\\f` 这类已被吃掉的转义符 → `\\\\f`
    """
    escape_repair_map = {
        "\a": "\\\\a",
        "\b": "\\\\b",
        "\f": "\\\\f",
        "\r": "\\\\r",
        "\t": "\\\\t",
        "\v": "\\\\v",
    }
    new_command = ""
    for index, ch in enumerate(commands):
        if ch == "\\" and index + 1 < len(commands):
            if commands[index + 1] not in ["n", '"', " "]:
                new_command += "\\"
        elif ch in escape_repair_map:
            ch = escape_repair_map[ch]
        new_command += ch
    return new_command


# ============ 新栈自有：模型级修复 ============
def _strip_fence(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()


def _fix_trailing_comma(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def _extract_json_block(text: str) -> str:
    m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    return m.group(1) if m else text


def _fix_unclosed(text: str) -> str:
    """补齐被截断的收尾。真模型实测最多的两种形态：整棵对象少一个 `}`、断在字符串字面量中间。"""
    t = text.strip()
    if not t:
        return text
    stack, in_str, esc = [], False, False
    for ch in t:
        if esc:
            esc = False
        elif in_str:
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}":
            if stack:
                stack.pop()
    if in_str:
        t += '"'                                   # 断在字符串里：先收掉引号再补括号
    return t + "".join("]" if o == "[" else "}" for o in reversed(stack))


def repair_to_model(raw: str, schema: Type[BaseModel]) -> Optional[BaseModel]:
    """升级式修复管线：每档都是前一档的**组合**，覆盖 LLM 最常见的四种坏输出
    （裸围栏 / 尾逗号 / **围栏+尾逗号** / JSON 前后带说明文字）。

    ⚠ 历史教训：早期只写了单独的"去围栏"和"修尾逗号"两档，漏了它们的**组合**档，
    模型输出"```json {...,}```"时全档失败。加档时务必同时加组合档。
    """
    candidates = (
        raw,
        _strip_fence(raw),
        _fix_trailing_comma(_strip_fence(raw)),                       # 组合档，勿删
        _fix_trailing_comma(_extract_json_block(raw)),
        _fix_trailing_comma(repair_json_format(_strip_fence(raw))),    # 组合档：注释 + 围栏
        _fix_unclosed(_fix_trailing_comma(_strip_fence(raw))),
    )
    for candidate in candidates:
        try:
            return schema.model_validate_json(candidate)
        except (ValidationError, ValueError, json.JSONDecodeError):
            continue
    return None


async def llm_repair_json(raw: str, schema: Type[BaseModel], llm) -> Optional[BaseModel]:
    """最后一档：静态修复全部失败时让 LLM 自修（源 RoleZero 的 JSON_REPAIR 重试路径）。"""
    from codeharness.prompts.role_zero import JSON_REPAIR_PROMPT
    fixed = await llm.aask(JSON_REPAIR_PROMPT.format(
        json_data=raw[:4000], json_decode_error="invalid json"), tag="json_repair")
    return repair_to_model(fixed, schema)
