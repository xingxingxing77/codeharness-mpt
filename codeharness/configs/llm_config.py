#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM 配置。来源：metagpt/configs/llm_config.py（136 行）。

判定 `改`（R7）：**字段名与默认值逐字照源**，但载体从源的 `YamlModel` 换成 pydantic v2
`BaseModel`（由 `configs/settings.py` 的 `BaseSettings` 以 `LLM__FIELD` 双下划线注入）。

⚠ 三处名字极易写错、且会静默失效的点，已在门禁里锁住：
1. 源字段是 **`max_token`（单数）**，不是 `max_tokens`
2. 源 `timeout: int = 600`，配合 `const.USE_CONFIG_TIMEOUT = 0` 表示"用配置值"
3. 源 `api_key` 默认值是 `"sk-"`（当作"未配置"的哨兵），新栈用 `""`，
   `server/settings.load_llm_defaults` 据空串判 `llm_configured=false`
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, field_validator

from codeharness.configs.compress_msg_config import CompressType
from codeharness.const import LLM_API_TIMEOUT


class LLMType(Enum):
    """厂商类型。成员与源 llm_config.py:19-46 逐字对齐（S10 接各厂商时要按这个分派）。"""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    CLAUDE = "claude"  # alias name of anthropic
    SPARK = "spark"
    ZHIPUAI = "zhipuai"
    FIREWORKS = "fireworks"
    OPEN_LLM = "open_llm"
    GEMINI = "gemini"
    METAGPT = "metagpt"
    AZURE = "azure"
    OLLAMA = "ollama"  # /chat at ollama api
    OLLAMA_GENERATE = "ollama.generate"  # /generate at ollama api
    OLLAMA_EMBEDDINGS = "ollama.embeddings"  # /embeddings at ollama api
    OLLAMA_EMBED = "ollama.embed"  # /embed at ollama api
    QIANFAN = "qianfan"  # Baidu BCE
    DASHSCOPE = "dashscope"  # Aliyun LingJi DashScope
    MOONSHOT = "moonshot"
    MISTRAL = "mistral"
    YI = "yi"  # lingyiwanwu
    OPEN_ROUTER = "open_router"
    DEEPSEEK = "deepseek"
    SILICONFLOW = "siliconflow"
    OPENROUTER = "openrouter"
    OPENROUTER_REASONING = "openrouter_reasoning"
    BEDROCK = "bedrock"
    ARK = "ark"  # https://www.volcengine.com/docs/82379/1263482#python-sdk
    LLAMA_API = "llama_api"

    def __missing__(self, key):
        return self.OPENAI


class LLMConfig(BaseModel):
    """A class to represent the LLM configuration.

    字段顺序与分组照源 `LLMConfig`(:52-114)。
    """

    # 通用
    api_key: str = ""
    api_type: LLMType = LLMType.OPENAI
    base_url: str = "https://api.openai.com/v1"
    api_version: Optional[str] = None
    model: Optional[str] = None  # also stands for DEPLOYMENT_NAME
    pricing_plan: Optional[str] = None  # Cost Settlement Plan Parameters.

    # 厂商专有凭据（S10 各适配器按 api_type 取用）
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    session_token: Optional[str] = None
    endpoint: Optional[str] = None  # for self-deployed model on the cloud
    app_id: Optional[str] = None
    api_secret: Optional[str] = None
    domain: Optional[str] = None
    region_name: Optional[str] = None

    # 采样与生成参数
    max_token: int = 4096
    temperature: float = 0.0
    top_p: float = 1.0
    top_k: int = 0
    repetition_penalty: float = 1.0
    stop: Optional[str] = None
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    best_of: Optional[int] = None
    n: Optional[int] = None
    stream: bool = True
    seed: Optional[int] = None
    logprobs: Optional[bool] = None
    top_logprobs: Optional[int] = None
    timeout: int = LLM_API_TIMEOUT
    context_length: Optional[int] = None  # Max input tokens

    proxy: Optional[str] = None
    calc_usage: bool = True

    # 消息压缩与 RoleZero 相关
    compress_type: CompressType = CompressType.NO_COMPRESS
    use_system_prompt: bool = True
    reasoning: bool = False
    reasoning_max_token: int = 4000  # reasoning budget tokens, usually smaller than max_token

    @field_validator("max_token")
    @classmethod
    def check_max_token(cls, v):
        """源 :118 起的校验语义保留：非正值一律回落 4096。"""
        return v if v and v > 0 else 4096
