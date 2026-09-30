#!/usr/bin/env python3
# _*_ coding: utf-8 _*_
"""
@Time    : 2023/9/4 15:40:40
@Author  : Stitch-z
@File    : file.py
@Describe : General file operations.
"""
from pathlib import Path
from typing import Optional, Union

import aiofiles
from fsspec.implementations.memory import MemoryFileSystem as _MemoryFileSystem

from codeharness.logs import logger
from codeharness.utils.read_docx import read_docx
from codeharness.utils.common import aread
from codeharness.utils.exceptions import handle_exception
from codeharness.utils.repo_to_markdown import is_text_file


class File:
    """A general util for file operations."""

    CHUNK_SIZE = 64 * 1024

    @classmethod
    @handle_exception
    async def write(cls, root_path: Path, filename: str, content: bytes) -> Path:
        """Write the file content to the local specified path.

        Args:
            root_path: The root path of file, such as "/data".
            filename: The name of file, such as "test.txt".
            content: The binary content of file.

        Returns:
            The full filename of file, such as "/data/test.txt".

        Raises:
            Exception: If an unexpected error occurs during the file writing process.
        """
        root_path.mkdir(parents=True, exist_ok=True)
        full_path = root_path / filename
        async with aiofiles.open(full_path, mode="wb") as writer:
            await writer.write(content)
            logger.debug(f"Successfully write file: {full_path}")
            return full_path

    @classmethod
    @handle_exception
    async def read(cls, file_path: Path, chunk_size: int = None) -> bytes:
        """Partitioning read the file content from the local specified path.

        Args:
            file_path: The full file name of file, such as "/data/test.txt".
            chunk_size: The size of each chunk in bytes (default is 64kb).

        Returns:
            The binary content of file.

        Raises:
            Exception: If an unexpected error occurs during the file reading process.
        """
        chunk_size = chunk_size or cls.CHUNK_SIZE
        async with aiofiles.open(file_path, mode="rb") as reader:
            chunks = list()
            while True:
                chunk = await reader.read(chunk_size)
                if not chunk:
                    break
                chunks.append(chunk)
            content = b"".join(chunks)
            logger.debug(f"Successfully read file, the path of file: {file_path}")
            return content

    @staticmethod
    async def is_textual_file(filename: Union[str, Path]) -> bool:
        """Determines if a given file is a textual file.

        A file is considered a textual file if it is plain text or has a
        specific set of MIME types associated with textual formats,
        including PDF and Microsoft Word documents.

        Args:
            filename (Union[str, Path]): The path to the file to be checked.

        Returns:
            bool: True if the file is a textual file, False otherwise.
        """
        is_text, mime_type = await is_text_file(filename)
        if is_text:
            return True
        if mime_type == "application/pdf":
            return True
        if mime_type in {
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-word.document.macroEnabled.12",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
            "application/vnd.ms-word.template.macroEnabled.12",
        }:
            return True
        return False

    @staticmethod
    async def read_text_file(filename: Union[str, Path]) -> Optional[str]:
        """Read the whole content of a file. Using absolute paths as the argument for specifying the file location."""
        is_text, mime_type = await is_text_file(filename)
        if is_text:
            return await File._read_text(filename)
        if mime_type == "application/pdf":
            return await File._read_pdf(filename)
        if mime_type in {
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-word.document.macroEnabled.12",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
            "application/vnd.ms-word.template.macroEnabled.12",
        }:
            return await File._read_docx(filename)
        return None

    @staticmethod
    async def _read_text(path: Union[str, Path]) -> str:
        return await aread(path)

    @staticmethod
    async def _read_pdf(path: Union[str, Path]) -> str:
        """pdf 走**知识库摄取那一条读取路**（口径同 C26：`document.py` 的 `OPTIONAL_READERS` 声明的是
        `pypdf`，缺件时由 `read_data` 里的 `_require_reader` 抛 `ReaderUnavailable` 那句人话，
        而不是 Python 原文）。

        改前的形状（09-30 审查文档 §3 P1）：第一跳是 `_omniparse_read_file`，而它 import 的
        `codeharness.utils.omniparse_client` **不在这个仓里**（`_config_compat.omniparse=None` 写着
        「按排除清单不搬」）⇒ 任何一份 pdf 一进这支就 `ModuleNotFoundError`，后面那个 `llama_index`
        兜底**从来没被走到过**（本机也没装 `llama_index`，而且它不是本仓声明的那把尺）。
        """
        from codeharness.document import read_data

        # 缺件闸不重复写：`read_data` 的 pdf 分支自己就调 `_require_reader`（刀 b 试过在这里再调一次，
        # 摘掉后 t54② 的读数一字不变 ⇒ 那行是冗余，删）。
        return "\n".join(d.page_content for d in read_data(Path(path)))

    @staticmethod
    async def _read_docx(path: Union[str, Path]) -> str:
        """docx 走 `read_docx`（python-docx）——那件本来就自带人话报错（`read_docx.py:9`）。

        这一支原本叠了**三个**缺陷，前两个把第三个盖得死死的（本轮探针才量齐）：
        ① 第一跳是不存在的 `omniparse_client` ⇒ 走到即 `ModuleNotFoundError`，下面那行从没执行过；
        ② 旧写法 `from codeharness.utils import read_docx` 拿到的是**子模块**（`utils/__init__.py` 是空的，
           没有再导出）⇒ 真让它执行，是 `TypeError: 'module' object is not callable`；
        ③ 旧那句 `"\n".join(read_docx(...))` 是**把字符串按字符拼行**（`read_docx` 返回的已经是
           `"\n".join(段落)` 的整体 str）⇒ 就算前两条都不拦，读出来的 docx 也会变成「一个字一行」。
        ⚠ 不改成知识库那支的 `docx2txt`：那是**摄取**侧 `OPTIONAL_READERS` 的声明，本机装的是 python-docx，
        换过去等于把一条现在真读得动的路改坏（C26 治的是「declare 与读得动混成一份」，不是要统一成同一个库）。
        """
        return read_docx(str(path))


class MemoryFileSystem(_MemoryFileSystem):
    @classmethod
    def _strip_protocol(cls, path):
        return super()._strip_protocol(str(path))
