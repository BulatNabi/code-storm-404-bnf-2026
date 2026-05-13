import json
import re
import textwrap
from pathlib import Path
from typing import Dict, List
from collections import defaultdict

from .models import RuleResult
from shared.common.tags_loader import get_tags_loader


def build_summary(rule_results: List[RuleResult]) -> Dict:
    """
    Создает сводку по результатам валидации.

    Новый формат статусов: pass, partial, fail, no_chunks
    """
    total_rules = len(rule_results)
    by_status = {"pass": 0, "partial": 0, "fail": 0, "no_chunks": 0}
    severity_breakdown: Dict[str, int] = {}
    total_confidence = 0.0
    scored_rules = 0
    rules_without_chunks: List[str] = []

    for r in rule_results:
        if r.status in by_status:
            by_status[r.status] += 1
        if r.no_chunks:
            rules_without_chunks.append(r.rule_id)
        if r.severity:
            severity_breakdown[r.severity] = (
                severity_breakdown.get(r.severity, 0) + 1
            )
        # Считаем среднюю уверенность только для правил с чанками
        if len(r.chunk_results) > 0:
            total_confidence += r.overall_confidence
            scored_rules += 1

    avg_confidence = total_confidence / scored_rules if scored_rules else 0.0

    return {
        "total_rules": total_rules,
        "by_status": by_status,
        "by_severity": severity_breakdown,
        "overall_confidence": avg_confidence,
        "rules_without_chunks": rules_without_chunks,
    }


def _section_key(path: str, level: int = 2) -> str:
    parts = [p.strip() for p in path.split("->") if p.strip()]
    return " -> ".join(parts[:level]) if parts else ""


def aggregate_sections(
    rule_results: List[RuleResult],
    level: int = 2,
) -> List[Dict]:
    """
    Агрегирует результаты по разделам ТЗ.

    Новый формат: используется chunk_results вместо chunks,
    статусы: pass, partial, fail
    """
    buckets: Dict[str, Dict] = {}

    for r in rule_results:
        for c in r.chunk_results:
            key = _section_key(c.chunk_path or "", level=level)
            if not key:
                continue
            bucket = buckets.setdefault(
                key,
                {
                    "path": key,
                    "counts": {
                        "processed": 0,
                        "pass": 0,
                        "partial": 0,
                        "fail": 0,
                    },
                    "confidence_sum": 0.0,
                    "confidence_cnt": 0,
                    "rules_with_issues": set(),
                },
            )
            bucket["counts"]["processed"] += 1
            if c.status in bucket["counts"]:
                bucket["counts"][c.status] += 1
            bucket["confidence_sum"] += c.confidence
            bucket["confidence_cnt"] += 1
            if c.status in {"fail", "partial"}:
                bucket["rules_with_issues"].add(r.rule_id)

    sections = []
    for bucket in buckets.values():
        counts = bucket["counts"]
        avg_confidence = 0.0
        if bucket["confidence_cnt"]:
            avg_confidence = (
                bucket["confidence_sum"] / bucket["confidence_cnt"]
            )

        # Определяем общий статус раздела
        if counts["fail"] > 0:
            status = "fail"
        elif counts["partial"] > 0:
            status = "partial"
        else:
            status = "pass"

        sections.append(
            {
                "path": bucket["path"],
                "status": status,
                "confidence": avg_confidence,
                "counts": counts,
                "rules_with_issues": sorted(bucket["rules_with_issues"]),
            }
        )

    sections.sort(key=lambda x: x["path"])
    return sections


def to_json_report(rule_results: List[RuleResult]) -> Dict:
    """
    Создает JSON-отчет с полными результатами валидации.

    Новый формат: overall_confidence вместо score,
    found_evidence и missing_requirements
    """
    summary = build_summary(rule_results)
    sections = aggregate_sections(rule_results, level=2)

    # Группируем правила по категориям из tags.json
    tags_loader = get_tags_loader()
    rules_by_category: Dict[str, List[Dict]] = {}

    for r in rule_results:
        category = tags_loader.get_category(r.tag) or "unknown"

        rule_dict = {
            "rule_id": r.rule_id,
            "tag": r.tag,
            "severity": r.severity,
            "rule_title": r.rule_title,
            "status": r.status,
            "overall_confidence": r.overall_confidence,
            "no_chunks": r.no_chunks,
            "chunk_results": [
                {
                    "chunk_id": c.chunk_id,
                    "chunk_title": c.chunk_title,
                    "chunk_path": c.chunk_path,
                    "status": c.status,
                    "confidence": c.confidence,
                    "explanation": c.explanation,
                    "found_evidence": [
                        {
                            "text": ev.text,
                            "section": ev.section,
                            "matches": ev.matches,
                        }
                        for ev in c.found_evidence
                    ],
                    "missing_requirements": c.missing_requirements,
                }
                for c in r.chunk_results
            ],
        }

        if category not in rules_by_category:
            rules_by_category[category] = []
        rules_by_category[category].append(rule_dict)

    return {
        "summary": summary,
        "sections": sections,
        "rules_by_category": rules_by_category,
    }


def _clean_text(text: str, max_len: int = 100) -> str:
    """Очищает текст от markdown и обрезает"""
    text = re.sub(r'[*_`#>]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(' ', 1)[0] + '...'
    return text


def to_markdown_report(rule_results: List[RuleResult]) -> str:
    """
    Создает компактный отчет с двумя разделами:
    ЧАСТЬ 1: Анализ по правилам (группировка по категориям)
    ЧАСТЬ 2: Анализ по разделам ТЗ (группировка по путям)
    """
    summary = build_summary(rule_results)
    tags_loader = get_tags_loader()
    lines = []

    # === HEADER ===
    lines.append("# 📋 Отчет по валидации ТЗ")
    lines.append("")
    lines.append(f"**Правил проверено:** {summary['total_rules']}")
    lines.append(f"**Средняя уверенность:** {summary['overall_confidence']:.0%}")
    lines.append("")
    lines.append("**Статистика:**  ")
    lines.append(
        f"✅ {summary['by_status']['pass']} pass | "
        f"⚠️ {summary['by_status']['partial']} partial | "
        f"❌ {summary['by_status']['fail']} fail | "
        f"⊘ {summary['by_status']['no_chunks']} no data"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # === ЧАСТЬ 1: ПО ПРАВИЛАМ ===
    lines.append("## 📑 ЧАСТЬ 1: Анализ по правилам")
    lines.append("")

    # Группируем правила по категориям
    rules_by_cat = defaultdict(list)
    for r in rule_results:
        cat = tags_loader.get_category(r.tag) or "unknown"
        rules_by_cat[cat].append(r)

    # Выводим по категориям
    for category in sorted(rules_by_cat.keys()):
        cat_rules = rules_by_cat[category]
        lines.append(f"### 📂 {category.upper()}")
        lines.append("")

        # Сортируем: fail > partial > pass
        status_order = {"fail": 0, "partial": 1, "pass": 2, "no_chunks": 3}
        sorted_rules = sorted(
            cat_rules,
            key=lambda x: (status_order.get(x.status, 9), x.severity, x.rule_id)
        )

        for r in sorted_rules:
            icon = {"pass": "✅", "partial": "⚠️", "fail": "❌", "no_chunks": "⊘"}.get(r.status, "?")

            lines.append(f"#### {icon} **{r.rule_id}**: {r.rule_title}")
            lines.append(f"*Тег: `{r.tag}` | Критичность: `{r.severity}` | Уверенность: {r.overall_confidence:.0%}*")
            lines.append("")

            if r.no_chunks:
                lines.append("> Нет подходящих разделов в ТЗ")
                lines.append("")
                continue

            # Группируем чанки по статусу
            fail_chunks = [c for c in r.chunk_results if c.status == "fail"]
            partial_chunks = [c for c in r.chunk_results if c.status == "partial"]
            pass_chunks = [c for c in r.chunk_results if c.status == "pass"]

            # Детально показываем проблемные
            for chunks, label, emoji in [
                (fail_chunks, "Провалено", "❌"),
                (partial_chunks, "Частично", "⚠️"),
            ]:
                if not chunks:
                    continue

                lines.append(f"**{emoji} {label}** ({len(chunks)} раздел(ов)):")
                for c in chunks:
                    section = c.chunk_path or c.chunk_title or f"chunk_{c.chunk_id}"
                    lines.append(f"- `{section}` — {c.confidence:.0%}")

                    # Доказательства (первые 2)
                    if c.found_evidence:
                        lines.append(f"  - ✓ Найдено: {len(c.found_evidence)}")
                        for ev in c.found_evidence[:2]:
                            txt = _clean_text(ev.text, 80)
                            lines.append(f"    • {txt}")

                    # Недостающее (первые 2)
                    if c.missing_requirements:
                        lines.append(f"  - ✗ Отсутствует: {len(c.missing_requirements)}")
                        for miss in c.missing_requirements[:2]:
                            miss_txt = _clean_text(miss, 80)
                            lines.append(f"    • {miss_txt}")

                    # Объяснение (полный текст без обрезки)
                    if c.explanation:
                        # Только очищаем от markdown, но не обрезаем
                        expl = re.sub(r'[*_`#>]+', '', c.explanation)
                        expl = re.sub(r'\s+', ' ', expl).strip()
                        lines.append(f"  - 💬 {expl}")

                lines.append("")

            # Детально показываем pass
            if pass_chunks:
                lines.append(f"**✅ Пройдено** ({len(pass_chunks)} раздел(ов)):")
                for c in pass_chunks:
                    section = c.chunk_path or c.chunk_title or f"chunk_{c.chunk_id}"
                    lines.append(f"- `{section}` — {c.confidence:.0%}")

                    # Объяснение (полный текст без обрезки)
                    if c.explanation:
                        expl = re.sub(r'[*_`#>]+', '', c.explanation)
                        expl = re.sub(r'\s+', ' ', expl).strip()
                        lines.append(f"  💬 {expl}")

                    # Показываем что найдено (первые 2 доказательства)
                    if c.found_evidence:
                        lines.append(f"  ✓ Найдено: {len(c.found_evidence)}")
                        for ev in c.found_evidence[:2]:
                            txt = _clean_text(ev.text, 80)
                            lines.append(f"    • {txt}")

                lines.append("")

        lines.append("---")
        lines.append("")

    # === ЧАСТЬ 2: ПО РАЗДЕЛАМ ТЗ ===
    lines.append("## 📄 ЧАСТЬ 2: Анализ по разделам ТЗ")
    lines.append("")

    # Собираем все чанки с правилами
    sections_map = defaultdict(list)  # section_path -> [(rule, chunk_result), ...]
    for r in rule_results:
        if r.no_chunks:
            continue
        for c in r.chunk_results:
            section_path = c.chunk_path or c.chunk_title or f"chunk_{c.chunk_id}"
            sections_map[section_path].append((r, c))

    # Сортируем разделы: сначала с проблемами
    def section_priority(item):
        path, rules = item
        fail_count = sum(1 for _, c in rules if c.status == "fail")
        partial_count = sum(1 for _, c in rules if c.status == "partial")
        return (-(fail_count + partial_count), path)

    for section_path in sorted(sections_map.keys(), key=lambda p: section_priority((p, sections_map[p]))):
        rules_in_section = sections_map[section_path]

        # Определяем общий статус раздела
        has_fail = any(c.status == "fail" for _, c in rules_in_section)
        has_partial = any(c.status == "partial" for _, c in rules_in_section)
        section_icon = "❌" if has_fail else ("⚠️" if has_partial else "✅")

        lines.append(f"### {section_icon} `{section_path}`")
        lines.append("")

        # Группируем правила по статусу
        fail_rules = [(r, c) for r, c in rules_in_section if c.status == "fail"]
        partial_rules = [(r, c) for r, c in rules_in_section if c.status == "partial"]
        pass_rules = [(r, c) for r, c in rules_in_section if c.status == "pass"]

        # Проблемные правила детально
        for rules_list, label, emoji in [
            (fail_rules, "Провалено", "❌"),
            (partial_rules, "Частично", "⚠️"),
        ]:
            if not rules_list:
                continue

            lines.append(f"**{emoji} {label}** ({len(rules_list)} правил(а)):")
            for r, c in rules_list:
                lines.append(f"- **{r.rule_id}**: {r.rule_title} (уверенность: {c.confidence:.0%})")

                # Объяснение (полный текст без обрезки)
                if c.explanation:
                    # Только очищаем от markdown, но не обрезаем
                    expl = re.sub(r'[*_`#>]+', '', c.explanation)
                    expl = re.sub(r'\s+', ' ', expl).strip()
                    lines.append(f"  {expl}")

                # Самое важное что отсутствует (1 пункт)
                if c.missing_requirements:
                    miss = _clean_text(c.missing_requirements[0], 80)
                    lines.append(f"  ✗ {miss}")

            lines.append("")

        # Пройденные правила детально
        if pass_rules:
            lines.append(f"**✅ Пройдено** ({len(pass_rules)} правил(а)):")
            for r, c in pass_rules:
                lines.append(f"- **{r.rule_id}**: {r.rule_title} (уверенность: {c.confidence:.0%})")

                # Объяснение (полный текст без обрезки)
                if c.explanation:
                    expl = re.sub(r'[*_`#>]+', '', c.explanation)
                    expl = re.sub(r'\s+', ' ', expl).strip()
                    lines.append(f"  {expl}")

            lines.append("")

        lines.append("---")
        lines.append("")

    # === ПРАВИЛА БЕЗ ДАННЫХ ===
    skipped_rules = [r for r in rule_results if r.no_chunks]
    if skipped_rules:
        lines.append("## ⊘ Правила без данных")
        lines.append("")
        lines.append("*Не найдено подходящих разделов в ТЗ:*")
        lines.append("")
        for r in sorted(skipped_rules, key=lambda x: x.rule_id):
            lines.append(f"- **{r.rule_id}**: {r.rule_title} (`{r.tag}`, критичность: `{r.severity}`)")
        lines.append("")

    return "\n".join(lines)


def save_json(report: Dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_markdown(report_md: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_md, encoding="utf-8")
