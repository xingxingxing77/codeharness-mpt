"""InvoiceOCR：源 actions/invoice_ocr.py 语义——发票图片 → 结构化字段。
OCR 提供方为可插拔接口：默认无配置时给出明确提示（源项目用 Azure Document Intelligence，
排除清单口径不变——接线上留好槽位，填 key 即启用）。"""
from pydantic import BaseModel, Field
from codeharness.base.action import BaseAction
from codeharness.schema import Message


class InvoiceData(BaseModel):
    """= 源 InvoiceOCR 的关键字段集"""
    invoice_number: str = ""
    invoice_date: str = ""
    seller: str = ""
    buyer: str = ""
    total_amount: str = ""
    tax_amount: str = ""
    items: list[dict] = Field(default_factory=list)


class InvoiceOCR(BaseAction):
    ocr_provider: object = None     # 注入 async callable(image_path) -> dict；默认 None

    async def run(self, msg: Message) -> Message:
        image_path = (msg.instruct_content or {}).get("image_path", msg.content)
        if not self.ocr_provider:
            data = InvoiceData()
            note = ("[未配置 OCR 服务] 需在 InvoiceOCR(ocr_provider=...) 注入实现"
                    "（源项目用 Azure Document Intelligence，见参考速查排除清单）。"
                    "角色与流水线已就绪。")
        else:
            raw = await self.ocr_provider(image_path)
            data = InvoiceData(**raw)
            note = "发票识别完成"
        return Message(content=note, role="assistant", cause_by=self.name, sent_from="InvoiceOCRAssistant",
                       instruct_content=data.model_dump(), instruct_schema="InvoiceData")
