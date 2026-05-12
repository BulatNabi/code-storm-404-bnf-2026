"""Schemas for tool return values. The @tool wrappers serialize these to
JSON strings before passing to the LLM."""

from __future__ import annotations

from typing import Literal, Optional, List

from pydantic import BaseModel, Field

Source = Literal["cbu", "lex", "eurlex"]
Severity = Literal["critical", "high", "medium", "low"]


class RuleHit(BaseModel):
    rule_id:             str
    tag:                 Optional[str] = None
    title:               Optional[str] = None
    requirement:         str
    verification_method: Optional[str] = None
    severity:            Severity = "medium"
    positive_examples:   List[str] = []
    negative_examples:   List[str] = []


class DocHit(BaseModel):
    doc_id:      str
    source:      Source
    category:    Optional[str] = None
    language:    Optional[str] = None
    title:       str
    source_url:  str
    has_rules:   bool = False
    rules_count: int  = 0
    tags:        List[str] = []
    severities:  List[str] = []
    score:       float = 0.0
    highlights:  List[str] = Field(default_factory=list)
    rules:       List[RuleHit] = Field(default_factory=list)  # only filled by get_document


class RuleSearchHit(BaseModel):
    """A nested-rule match with its parent document context."""
    doc_id:     str
    source:     Source
    doc_title:  str
    source_url: str
    rule:       RuleHit
    score:      float


class TagCount(BaseModel):
    tag:   str
    count: int


class QuoteVerification(BaseModel):
    found:    bool
    in_field: Optional[Literal["full_text", "rule.requirement", "rule.title"]] = None
    snippet:  Optional[str] = None

# Схемы для итогового ответа агента
class ChecklistItem(BaseModel):
    action: str = Field(description="Конкретное действие, которое нужно выполнить")
    role: str = Field(description="Роль (например: PO, Backend, Compliance, Frontend)")
    rationale: str = Field(description="Обоснование, почему это нужно")
    doc_links: List[str] = Field(default_factory=list, description="Ссылки на документы (doc_id или url)")
    quotes: List[str] = Field(default_factory=list, description="Верифицированные цитаты из НПА")
    compliance_metric: Optional[str] = Field(None, description="Метрика для оценки соблюдения данного правила/закона (как измерить, что оно соблюдается)")

class RegulatoryDomainResult(BaseModel):
    domain: str = Field(description="Затронутая регуляторная область (тег)")
    risk_level: Severity = Field(description="Оценка риска для данной области")
    risk_assessment_details: Optional[str] = Field(None, description="Детальное описание потенциальных последствий при несоблюдении (штрафы, блокировки и т.д.)")
    reasoning: str = Field(description="Почему эта область затронута фичей")
    checklist: List[ChecklistItem] = Field(description="Чек-лист для этой области")

class FinalReport(BaseModel):
    feature_summary: str = Field(description="Краткое резюме фичи, как её понял агент")
    overall_risk: Severity = Field(description="Общий максимальный уровень риска")
    domains: List[RegulatoryDomainResult] = Field(description="Затронутые домены с чеклистами")
    documents_to_update: List[str] = Field(description="Внутренние документы (Оферта, Политика и т.д.), которые возможно придется обновить")
    red_flags: List[str] = Field(default_factory=list, description="Критические риски, требующие немедленного внимания (если есть)")
