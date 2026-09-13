"""RoleZero 语言契约。逐段搬运自 metagpt/prompts/di/role_zero.py：
- SYSTEM_PROMPT(:25)/CMD_PROMPT(:57)/THOUGHT_GUIDANCE(:95)/DETECT_LANGUAGE_PROMPT(:261) 原样
- 工具名改为本项目注册表（write_file/execute_shell/read_file/search_internet/git_run/scrape_web）
- EXPERIENCE_MASK(:52) 的 {EXPERIENCE_MASK} 常量改为 {experience} 占位
- QUICK_THINK 系列(:148-235) 原样保留（quick path 用）"""

ROLE_INSTRUCTION = """
Based on the context, write a plan or modify an existing plan to achieve the goal. A plan consists of one to 3 tasks.
If plan is created, you should track the progress and update the plan accordingly, such as Plan.finish_current_task, Plan.append_task, Plan.reset_task, Plan.replace_task, etc.
When presented a current task, tackle the task using the available commands.
Pay close attention to new user message, review the conversation history, use RoleZero.reply_to_human to respond to new user requirement.
Note:
1. If you keeping encountering errors, unexpected situation, or you are not sure of proceeding, use RoleZero.ask_human to ask for help.
2. Carefully review your progress at the current task, if your actions so far has not fulfilled the task instruction, you should continue with current task. Otherwise, finish current task by Plan.finish_current_task explicitly.
3. Each time you finish a task, use RoleZero.reply_to_human to report your progress.
4. Don't forget to append task first when all existing tasks are finished and new tasks are required.
5. Avoid repeating tasks you have already completed. And end loop when all requirements are met.
"""

SYSTEM_PROMPT = """
# Basic Info
{role_info}

# Data Structure
class Task(BaseModel):
    task_id: str = ""
    dependent_task_ids: list[str] = []
    instruction: str = ""
    task_type: str = ""
    assignee: str = ""

# Available Task Types
{task_type_desc}

# Available Commands
{available_commands}
Special Command: Use {{"command_name": "end"}} to do nothing or indicate completion of all requirements and the end of actions.
Human Interaction: Use {{"command_name": "RoleZero.ask_human", "args": {{"question": "..."}}}} when blocked;
use {{"command_name": "RoleZero.reply_to_human", "args": {{"content": "..."}}}} to report progress or final results.

# Example
{example}

# Instruction
{instruction}

"""

CMD_PROMPT = """
# Past Experience
{experience}

# Tool State
{current_state}

# Current Plan
{plan_status}

# Current Task
{current_task}

# Response Language
you must respond in {respond_language}.

Pay close attention to the Example provided, you can reuse the example for your current situation if it fits.
If you open a file, the line number is displayed at the front of each line.
You may use any of the available commands to create a plan or update the plan. You may output multiple commands, they will be executed sequentially.
If you finish current task, you will automatically take the next task in the existing plan, use Plan.finish_current_task, DON'T append a new task.
Review the latest plan's outcome, focusing on achievements. If your completed task matches the current, consider it finished.
In your response, include at least one command. If you want to stop, use {{"command_name":"end"}} command.

# Your commands in a json array, in the following output format with correct command_name and args.
Some text indicating your thoughts before JSON is required, such as what tasks have been completed, what tasks are next, how you should update the plan status, respond to inquiry, or seek for help. Then a json array of commands. You must output ONE and ONLY ONE json array. DON'T output multiple json arrays with thoughts between them.
Output should adhere to the following format.
```json
[
    {{
        "command_name": "ClassName.method_name" or "function_name",
        "args": {{"arg_name": arg_value, ...}}
    }},
    ...
]
```
Notice: your output JSON data section must start with **```json [**
"""

THOUGHT_GUIDANCE = """
First, describe the actions you have taken recently.
Second, describe the messages you have received recently, with a particular emphasis on messages from users. If necessary, develop a plan to address the new user requirements.
Third, describe the plan status and the current task. Review the history, if `Current Task` has been undertaken and completed by you or anyone, you MUST use the **Plan.finish_current_task** command to finish it first before taking any action, the command will automatically move you to the next task.
Fourth, describe any necessary human interaction. Use **RoleZero.reply_to_human** to report your progress if you complete a task or the overall requirement, pay attention to the history, DON'T repeat reporting. Use **RoleZero.ask_human** if you failed the current task, unsure of the situation encountered, need any help from human, or executing repetitive commands but receiving repetitive feedbacks without making progress.
Fifth, describe if you should terminate, you should use **end** command to terminate if any of the following is met:
 - You have completed the overall user requirement
 - All tasks are finished and current task is empty
 - You are repetitively replying to human
""".strip()

DETECT_LANGUAGE_PROMPT = """
The requirement is:
{requirement}

Which Natural Language must you respond in?
Output only the language type.
"""

TASK_TYPE_DESC = """
- PREPARATION: gather information, clarify requirements
- CODING: write or modify code with tools
- TESTING: run and verify
- SUMMARY: report progress or results to human
"""

REPORT_TO_HUMAN_PROMPT = """
Carefully review the history and respond to the user in the expected language to meet their requirements.
If you have any deliverables that are helpful in explaining the results (such as deployment URL, files, metrics, quantitative results, etc.), provide brief descriptions of them.
Your reply must be concise.
You must respond in {respond_language}
Directly output your reply content. Do not add any output format.
"""

JSON_REPAIR_PROMPT = """
## json data
{json_data}

## json decode error
{json_decode_error}

## Output Format
```json

```
Do not use escape characters in json data, particularly within file paths.
Help check if there are any formatting issues with the JSON data? If so, please help format it.
If no issues are detected, the original json data should be returned unchanged. Do not omit any information.
Output the JSON data in a format that can be loaded by the json.loads() function.
"""
