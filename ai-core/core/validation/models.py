from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SourceInfo:
    """Информация об источнике правила (ГОСТ/НПА)"""
    document: str
    section: str
    quote: str


@dataclass
class Rule:
    """
    Правило валидации ТЗ (новый формат).

    Формат rule_id: R-<TAG>-NNN
    Например: R-SEC-001, R-AIT-001
    """
    rule_id: str
    tag: str  # Только из tags.json
    title: str
    requirement: str  # Чёткая формулировка требования
    verification_method: str  # Пошаговая инструкция проверки
    positive_examples: List[str]  # 3-5 примеров
    negative_examples: List[str]  # 2-3 примера
    severity: str  # critical|high|medium|low
    source: SourceInfo
    is_active: bool = True


@dataclass
class Evidence:
    """Доказательство выполнения требования"""
    text: str  # Цитата из ТЗ
    section: str  # Раздел где найдено
    matches: str  # Какой пункт requirement подтверждает


@dataclass
class ValidationChunkResult:
    """Результат проверки одного чанка по правилу"""
    chunk_id: str
    chunk_title: Optional[str]
    chunk_path: Optional[str]
    status: str  # pass | partial | fail
    found_evidence: List[Evidence] = field(default_factory=list)
    missing_requirements: List[str] = field(default_factory=list)
    confidence: float = 0.0  # 0.0-1.0
    explanation: str = ""  # 2-3 предложения


@dataclass
class RuleResult:
    """Агрегированный результат проверки правила"""
    rule_id: str
    tag: str
    severity: str
    rule_title: str
    status: str  # pass | partial | fail | no_chunks
    overall_confidence: float
    chunk_results: List[ValidationChunkResult] = field(default_factory=list)
    no_chunks: bool = False
