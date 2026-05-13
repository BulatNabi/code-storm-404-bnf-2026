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
    role: str = Field(description="Роль: PO, Backend, Frontend, Compliance, Legal, Data, Security")
    rationale: str = Field(description="Обоснование: какое требование закона это закрывает")
    doc_links: List[str] = Field(default_factory=list,
                                 description="Только doc_id из ES (например 'eurlex-32016R0679'). URL'ы и title подставляются автоматически после finalize_analysis — не выдумывай их.")
    quotes: List[str] = Field(default_factory=list,
                              description="Точные ПОДСТРОКИ из поля `requirement` правил, не из title. Минимум 20 символов. Каждая цитата должна найтись substring-матчем в исходном документе.")
    compliance_metric: Optional[str] = Field(None,
                                             description="Измеримый критерий проверки (например: '100% запросов имеют consent=true в логах', 'DPIA проведена и подписана DPO')")


# Reference добавляется ПОСЛЕ работы агента — серверный enrichment по
# doc_id из ES (см. _enrich_report_with_urls в main.py). LLM это поле НЕ
# заполняет; оно появляется в финальном JSON, который видит фронт.
class DocReference(BaseModel):
    doc_id:     str
    title:      Optional[str] = None
    source:     Optional[str] = None       # cbu | lex | eurlex
    source_url: Optional[str] = None

class RegulatoryDomainResult(BaseModel):
    domain: str = Field(description="Затронутая регуляторная область (тег)")
    risk_level: Severity = Field(description="Оценка риска для данной области")
    risk_assessment_details: Optional[str] = Field(None, description="Детальное описание потенциальных последствий при несоблюдении (штрафы, блокировки и т.д.)")
    reasoning: str = Field(description="Почему эта область затронута фичей")
    checklist: List[ChecklistItem] = Field(description="Чек-лист для этой области")

class FinalReport(BaseModel):
    feature_summary: str = Field(description="Краткое резюме фичи (1-2 предложения), как её понял агент. Должно содержать предмет фичи и какие сущности/данные затрагиваются.")
    overall_risk: Severity = Field(description="Общий максимальный уровень риска по всем доменам")
    jira_comment_summary: str = Field(description=(
        "Markdown-комментарий для Jira-тикета строго по шаблону:\n"
        "## Compliance Review — <severity>\n"
        "**TL;DR:** <1 предложение про главный риск>\n\n"
        "**Затронутые области:** <список доменов>\n\n"
        "**ToDo:**\n- [ ] <action 1> (<role>)\n- [ ] <action 2> (<role>)\n...\n\n"
        "**Документы к обновлению:** <список>"
    ))
    domains: List[RegulatoryDomainResult] = Field(description=(
        "Затронутые регуляторные области. ОБЯЗАТЕЛЬНО проверь несколько "
        "областей — практически любая фича финтеха затрагивает 2-4 "
        "домена. Один domain = одна область регулирования."
    ))
    documents_to_update: List[str] = Field(description="Внутренние документы (Оферта, Privacy Policy, ROPA, DPIA, User Agreement, Terms of Service и т.д.), которые потребуется обновить с релизом")
    red_flags: List[str] = Field(default_factory=list, description="Критические блокеры — то, без чего релизить НЕЛЬЗЯ")
