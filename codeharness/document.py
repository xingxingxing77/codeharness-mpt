"""文件系统相关的文档与仓库模型。来源：metagpt/document.py 全文 235 行。

⚠ 与 `codeharness/schema.py` 的 `Document` **同名不同物**（源项目同样如此）：
- `schema.Document` = Action 的产物载体（root_path + filename + content）
- 本模块 `Document` = 文档仓子系统的文件实体（path + name + content + author + status + reviews）
两边不要混用。

判定：`DocumentStatus` / `Document` / `RepoMetadata` / `Repo` 判 `复`（仅改 import 与 logger）；
`read_data` 与 `IndexableDocument` 判 `改`——源的 llama_index 依赖（SimpleDirectoryReader /
SimpleNodeParser / PDFReader）整体换成 LangChain document loaders + text splitters。

依赖策略：pandas 必需；docx/pdf 的 loader 依赖（docx2txt、pypdf）**一律函数内惰性 import**，
否则本模块 import 就会拖垮 S1 门禁。tqdm 不引入（只为进度条，去掉不影响语义）。
**惰性不等于可以不说**：这两件从 09-23 起在 `pyproject.toml` 里显式声明（C26——此前两头都没有，
白名单却宣称收这两个格式），而 `reader_available()` 负责在缺件的环境里说人话而不是抛 Python 原文。
"""
import importlib.util
from enum import Enum
from pathlib import Path
from typing import Optional, Union

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from codeharness.logs import logger


def validate_cols(content_col: str, df: pd.DataFrame):
    if content_col not in df.columns:
        raise ValueError("Content column not found in DataFrame.")


# C26：`.docx`/`.pdf` 的 loader 依赖是**可选**的（`read_data` 里惰性 import，理由见模块头）。
# 但「声明支持这个格式」与「本机读得动它」是两件事——混成一份白名单就会对用户撒谎：
# 白名单收 `.docx`，而 `Docx2txtLoader` 一缺件就抛 `ModuleNotFoundError: No module named 'docx2txt'`，
# 那句话会被摄取件原样拼进给用户看的 `errors[]`（原件却已经落进 `kb/` 了）。
OPTIONAL_READERS = {".docx": "docx2txt", ".doc": "docx2txt", ".pdf": "pypdf"}


def reader_available(suffix: str) -> bool:
    """这个后缀在**本机**读不读得动——只问它的可选依赖在不在，不猜别的。

    必须在真正 import loader **之前**问：那时候抛出来的已经是 Python 的原始报错了。
    `find_spec` 不导入模块（零副作用、零依赖代价），缺件与「没装过」同答 False。"""
    mod = OPTIONAL_READERS.get(suffix.lower())
    return mod is None or importlib.util.find_spec(mod) is not None


class ReaderUnavailable(RuntimeError):
    """可选读取组件没装。单独一个类型：调用方要能分清「这是我该翻成人话的那一类」
    与「这是真 bug，类名留着给我看」。"""


def _require_reader(suffix: str):
    """`read_data` 那两支惰性 import 之前的闸——缺件时抛这一句，而不是
    `ModuleNotFoundError: No module named 'docx2txt'`（C26：那句话会被拼进给用户看的 `errors[]`）。"""
    if not reader_available(suffix):
        raise ReaderUnavailable(f"{suffix} 本机暂时读不了：缺组件 `{OPTIONAL_READERS[suffix.lower()]}`")


def read_data(data_path: Path) -> Union[pd.DataFrame, list]:
    """按后缀分派读取。表格类返回 DataFrame，文档类返回 LangChain Document 列表。"""
    suffix = data_path.suffix
    if ".xlsx" == suffix:
        data = pd.read_excel(data_path)
    elif ".csv" == suffix:
        data = pd.read_csv(data_path)
    elif ".json" == suffix:
        data = pd.read_json(data_path)
    elif suffix in (".docx", ".doc"):
        _require_reader(suffix)
        from langchain_community.document_loaders import Docx2txtLoader
        data = Docx2txtLoader(str(data_path)).load()
    elif suffix in (".txt", ".md"):     # C3：知识库文档九成是 .md，原先会掉进下面的 NotImplementedError
        from langchain_community.document_loaders import TextLoader
        from langchain_text_splitters import CharacterTextSplitter
        docs = TextLoader(str(data_path), encoding="utf-8").load()
        # 源：SimpleNodeParser.from_defaults(separator="\n", chunk_size=256, chunk_overlap=0)
        # ⚠ 这个 splitter 只认分隔符：900 字不换行的段落原样出一块（langchain 自己打
        # `Created a chunk of size 400, which is longer than the specified 256`）⇒ 它**不是**长度上限，
        # 上限在 `document_store/embed_split.py`（C27，端点超长会静默截断）。
        splitter = CharacterTextSplitter(separator="\n", chunk_size=256, chunk_overlap=0, keep_separator=False)
        data = splitter.split_documents(docs)
    elif ".pdf" == suffix:
        _require_reader(suffix)
        from langchain_community.document_loaders import PyPDFLoader
        data = PyPDFLoader(str(data_path)).load()
    else:
        raise NotImplementedError("File format not supported.")
    return data


class DocumentStatus(Enum):
    """Indicates document status, a mechanism similar to RFC/PEP."""

    DRAFT = "draft"
    UNDERREVIEW = "underreview"
    APPROVED = "approved"
    DONE = "done"


class Document(BaseModel):
    """Document: Handles operations related to document files."""

    path: Optional[Path] = Field(default=None)
    name: str = Field(default="")
    content: str = Field(default="")

    author: str = Field(default="")
    status: DocumentStatus = Field(default=DocumentStatus.DRAFT)
    reviews: list = Field(default_factory=list)

    @classmethod
    def from_path(cls, path: Path) -> "Document":
        """Create a Document instance from a file path.

        ⚠ 对源 `document.py:80` 的**偏差修正**：源写的是 `path.read_text()` 不带编码，
        而 `to_path()` 明确按 utf-8 写——中文 Windows 上读自己写的文件会走 GBK 解码并
        `UnicodeDecodeError`。写入/读取编码必须成对。"""
        if not path.exists():
            raise FileNotFoundError(f"File {path} not found.")
        content = path.read_text(encoding="utf-8")
        return cls(content=content, path=path)

    @classmethod
    def from_text(cls, text: str, path: Optional[Path] = None) -> "Document":
        """Create a Document from a text string."""
        return cls(content=text, path=path)

    def to_path(self, path: Optional[Path] = None):
        """Save content to the specified file path."""
        if path is not None:
            self.path = path
        if self.path is None:
            raise ValueError("File path is not set.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # TODO: excel, csv, json, etc.
        self.path.write_text(self.content, encoding="utf-8")

    def persist(self):
        """Persist document to disk."""
        return self.to_path()


class IndexableDocument(Document):
    """Advanced document handling: For vector databases or search engines."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    data: Union[pd.DataFrame, list] = None
    content_col: Optional[str] = Field(default="")
    meta_col: Optional[str] = Field(default="")

    @classmethod
    def from_path(cls, data_path: Path, content_col="content", meta_col="metadata") -> "IndexableDocument":
        if not data_path.exists():
            raise FileNotFoundError(f"File {data_path} not found.")
        data = read_data(data_path)
        if isinstance(data, pd.DataFrame):
            validate_cols(content_col, data)
            return cls(data=data, content=str(data), content_col=content_col, meta_col=meta_col)
        try:
            content = data_path.read_text(encoding="utf-8")   # 与 to_path 的 utf-8 配对（源 :131 未指定）
        except Exception as e:
            logger.debug(f"Load {str(data_path)} error: {e}")
            content = ""
        return cls(data=data, content=content, content_col=content_col, meta_col=meta_col)

    def _get_docs_and_metadatas_by_df(self) -> (list, list):
        df = self.data
        docs, metadatas = [], []
        for i in range(len(df)):
            docs.append(df[self.content_col].iloc[i])
            metadatas.append({self.meta_col: df[self.meta_col].iloc[i]} if self.meta_col else {})
        return docs, metadatas

    def _get_docs_and_metadatas_by_langchain(self) -> (list, list):
        """源名 `_get_docs_and_metadatas_by_llamaindex`；LangChain Document 用 page_content。
        同时兼容 `.text`，以便调用方传 llama-index 风格对象也不炸。"""
        docs = [getattr(i, "page_content", None) or getattr(i, "text", "") for i in self.data]
        metadatas = [getattr(i, "metadata", {}) or {} for i in self.data]
        return docs, metadatas

    def get_docs_and_metadatas(self) -> (list, list):
        if isinstance(self.data, pd.DataFrame):
            return self._get_docs_and_metadatas_by_df()
        elif isinstance(self.data, list):
            return self._get_docs_and_metadatas_by_langchain()
        else:
            raise NotImplementedError("Data type not supported for metadata extraction.")


class RepoMetadata(BaseModel):
    name: str = Field(default="")
    n_docs: int = Field(default=0)
    n_chars: int = Field(default=0)
    symbols: list = Field(default_factory=list)


class Repo(BaseModel):
    """Name of this repo + 三类文件（docs / codes / assets）的内存索引。"""

    name: str = Field(default="")
    docs: dict[Path, Document] = Field(default_factory=dict)
    codes: dict[Path, Document] = Field(default_factory=dict)
    assets: dict[Path, Document] = Field(default_factory=dict)
    path: Optional[Path] = Field(default=None)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _path(self, filename):
        return self.path / filename

    @classmethod
    def from_path(cls, path: Path) -> "Repo":
        """Load documents, code, and assets from a repository path."""
        path.mkdir(parents=True, exist_ok=True)
        repo = Repo(path=path, name=path.name)
        for file_path in path.rglob("*"):
            # FIXME（源同注）：后缀白名单难支持多语言，待 repo_parser 补齐后改由解析器判定
            if file_path.is_file() and file_path.suffix in [".json", ".txt", ".md", ".py", ".js", ".css", ".html"]:
                try:
                    repo._set(file_path.read_text(encoding="utf-8"), file_path)
                except UnicodeDecodeError:
                    logger.debug(f"Skip non-utf8 file: {file_path}")
        return repo

    def to_path(self):
        """Persist all documents, code, and assets to the given repository path."""
        for group in (self.docs, self.codes, self.assets):
            for doc in group.values():
                doc.to_path()

    def _set(self, content: str, path: Path) -> Document:
        """Add a document to the appropriate category based on its file extension."""
        suffix = path.suffix
        doc = Document(content=content, path=path, name=str(path.relative_to(self.path)))
        # FIXME（源同注）：后缀白名单难支持多语言
        if suffix.lower() == ".md":
            self.docs[path] = doc
        elif suffix.lower() in [".py", ".js", ".css", ".html"]:
            self.codes[path] = doc
        else:
            self.assets[path] = doc
        return doc

    def set(self, filename: str, content: str):
        """Set a document and persist it to disk."""
        doc = self._set(content, self._path(filename))
        doc.to_path()

    def get(self, filename: str) -> Optional[Document]:
        """Get a document by its filename."""
        path = self._path(filename)
        return self.docs.get(path) or self.codes.get(path) or self.assets.get(path)

    def get_text_documents(self) -> list[Document]:
        return list(self.docs.values()) + list(self.codes.values())

    def eda(self) -> RepoMetadata:
        """n_docs / n_chars / symbols 照源 :231-235。generate_symbols 已由 S6 批2c 落地
        （repo_parser 1086 行照源整件）；except ImportError 只兜"单文件拷贝跑法"，
        不再是"能力未落地"的占位注释。"""
        n_docs = sum(len(i) for i in [self.docs, self.codes, self.assets])
        n_chars = sum(sum(len(j.content) for j in i.values()) for i in [self.docs, self.codes, self.assets])
        symbols: list = []
        try:
            from codeharness.repo_parser import RepoParser
            symbols = RepoParser(base_directory=self.path).generate_symbols()
        except ImportError:
            logger.debug("RepoParser import 失败（单文件跑法），eda().symbols 置空")
        return RepoMetadata(name=self.name, n_docs=n_docs, n_chars=n_chars, symbols=symbols)
