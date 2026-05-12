from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

from shared.common.client.llm_client import LLMClient

RiskLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class RegulatoryDomain:
    tag: str
    name: str
    description: str
    default_risk: RiskLevel
    what_to_check: Tuple[str, ...]
    documents_to_update: Tuple[str, ...]
    processes_to_update: Tuple[str, ...]


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        key = item.strip()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _risk_rank(level: RiskLevel) -> int:
    return {"low": 0, "medium": 1, "high": 2}[level]


def _max_risk(levels: Iterable[RiskLevel]) -> RiskLevel:
    best: RiskLevel = "low"
    for lvl in levels:
        if _risk_rank(lvl) > _risk_rank(best):
            best = lvl
    return best


def default_fintech_domains() -> List[RegulatoryDomain]:
    return [
        RegulatoryDomain(
            tag="licensing_and_activity_scope",
            name="Лицензирование и допустимый периметр деятельности",
            description=(
                "Нужна ли лицензия/разрешение (платежные услуги, электронные деньги, "
                "микрофинансирование и т.п.), какие операции допустимы и какие ограничения "
                "есть по продукту и каналам."
            ),
            default_risk="high",
            what_to_check=(
                "Определить правовую квалификацию услуги: платежная услуга, перевод, электронные деньги, посредничество, агентская схема.",
                "Проверить, требуется ли лицензия/статус и какие операции разрешены текущей лицензией.",
                "Проверить ограничения по каналам (онлайн/агентская сеть), географии, валюте и лимитам.",
                "Проверить требования к раскрытию информации и договорной базе для выбранного статуса.",
            ),
            documents_to_update=(
                "Матрица лицензий/разрешенных операций (product compliance matrix)",
                "Реестр услуг/операций и их правовой квалификации",
                "Пользовательское соглашение/публичная оферта (если применимо)",
            ),
            processes_to_update=(
                "Процесс согласования запуска новой услуги (product launch approval)",
                "Процесс ведения реестра операций и контроль выхода за периметр лицензии",
            ),
        ),
        RegulatoryDomain(
            tag="payments_and_transfers",
            name="Платежи, переводы и электронные деньги",
            description=(
                "Любые платежи/переводы, пополнение/вывод, P2P, QR, операции с электронными деньгами, "
                "расчеты между пользователями и мерчантами."
            ),
            default_risk="high",
            what_to_check=(
                "Проверить сценарии движения денег end-to-end: кто плательщик, получатель, где хранятся средства, кто инициатор и кто отвечает за исполнение.",
                "Проверить лимиты, комиссии, статусы операций, возвраты/chargeback (если применимо) и обработку спорных операций.",
                "Проверить требования к идентификации по уровням (если лимиты зависят от KYC).",
                "Проверить хранение и сверку транзакционных данных, неизменяемость журналов, трассируемость операций.",
            ),
            documents_to_update=(
                "Описание платежных сценариев и потоков средств (funds flow)",
                "Тарифы/комиссии и правила их отображения пользователю",
                "Регламент обработки возвратов/отмен/ошибочных переводов",
            ),
            processes_to_update=(
                "Операционный процесс поддержки платежей и спорных операций",
                "Процесс reconciliation/сверки с провайдерами и банками",
            ),
        ),
        RegulatoryDomain(
            tag="cards_and_acquiring",
            name="Карты, эквайринг и платежная инфраструктура",
            description=(
                "Эквайринг, карты, токенизация, 3DS/SCA, взаимодействие с процессингом/PSP, "
                "платежные ссылки, терминалы."
            ),
            default_risk="medium",
            what_to_check=(
                "Проверить требования к аутентификации и защите операций (например, многофакторная/усиленная проверка для рискованных сценариев).",
                "Проверить хранение платежных данных: запрет хранения чувствительных данных, токенизация, разделение доступов.",
                "Проверить схемы возвратов/chargeback и претензионную работу.",
            ),
            documents_to_update=(
                "Требования по обработке платежных данных (security-by-design для платежей)",
                "Договоры/тех.спецификации с процессингом/PSP",
            ),
            processes_to_update=(
                "Процесс управления ключами/токенами и доступами к платежной инфраструктуре",
                "Процесс реагирования на инциденты платежной инфраструктуры",
            ),
        ),
        RegulatoryDomain(
            tag="aml_cft",
            name="AML/CFT и финмониторинг",
            description=(
                "Антиотмывание, выявление подозрительных операций, мониторинг транзакций, "
                "пороговые и сценарные проверки, STR/сообщения в уполномоченные органы."
            ),
            default_risk="high",
            what_to_check=(
                "Проверить, меняется ли риск-профиль клиентов/операций (новые типы переводов, новые получатели, новые каналы).",
                "Проверить сценарии transaction monitoring: правила, пороги, триггеры, качество алертов, расследования.",
                "Проверить обязательность и сроки формирования сообщений/отчетности по подозрительным операциям.",
                "Проверить соответствие санкционному/PEP/негативному скринингу (если требуется) на ключевых шагах.",
            ),
            documents_to_update=(
                "AML/CFT политика",
                "Процедуры мониторинга транзакций и расследований",
                "Риск-аппетит и риск-матрица по продуктам/клиентам",
            ),
            processes_to_update=(
                "Процесс KYC/EDD (взаимодействие с AML)",
                "Процесс расследования алертов и эскалаций в compliance",
                "Процесс подачи сообщений/отчетов в регуляторные органы (если применимо)",
            ),
        ),
        RegulatoryDomain(
            tag="kyc_onboarding",
            name="KYC, идентификация и онбординг",
            description=(
                "Идентификация/верификация клиента, уровни KYC, удаленная идентификация, "
                "согласия, UBO, анкеты и актуализация данных."
            ),
            default_risk="high",
            what_to_check=(
                "Проверить, какие данные собираются и на каком основании (минимизация и достаточность).",
                "Проверить сценарии удаленной идентификации (если есть): надежность, liveness, защита от подмен.",
                "Проверить правила обновления KYC при изменении профиля/лимитов/продуктов.",
                "Проверить требования к хранению KYC-артефактов и доказательств согласия.",
            ),
            documents_to_update=(
                "KYC/EDD процедура",
                "Анкеты/формы и текст согласий",
                "Регламент актуализации данных клиента",
            ),
            processes_to_update=(
                "Процесс онбординга и проверки клиента",
                "Процесс периодической переоценки клиента (ongoing due diligence)",
            ),
        ),
        RegulatoryDomain(
            tag="sanctions_screening",
            name="Санкционный/PEP/негативный скрининг",
            description=(
                "Проверка клиентов и получателей по санкционным спискам, PEP, негативным источникам; "
                "правила блокировок/эскалаций."
            ),
            default_risk="high",
            what_to_check=(
                "Проверить точки скрининга: при регистрации, перед переводом, при изменении данных, при получении средств (если применимо).",
                "Проверить политику false-positive, дедупликацию, ручную верификацию и SLA расследований.",
                "Проверить правила блокировок, заморозки, отклонения операций и коммуникации клиенту.",
            ),
            documents_to_update=(
                "Политика санкционного/PEP скрининга",
                "Регламент расследования совпадений и принятия решений",
            ),
            processes_to_update=(
                "Процесс управления списками/провайдерами данных",
                "Процесс расследований и эскалации совпадений",
            ),
        ),
        RegulatoryDomain(
            tag="fx_and_crossborder",
            name="Валютное регулирование и трансграничные операции",
            description=(
                "Кроссбордер переводы, валютные операции, международные платежи, "
                "ограничения по странам/валютам/целям платежа."
            ),
            default_risk="high",
            what_to_check=(
                "Проверить, является ли операция валютной/трансграничной и какие ограничения и основания применимы.",
                "Проверить требуемые данные (назначение платежа, страна, бенефициар), лимиты и документы-основания (если применимо).",
                "Проверить экраны комплаенса по странам/юрисдикциям повышенного риска.",
            ),
            documents_to_update=(
                "Политика и регламент по валютным/трансграничным операциям",
                "Справочник стран/юрисдикций повышенного риска",
            ),
            processes_to_update=(
                "Процесс комплаенс-проверки кроссбордер операций",
                "Процесс ведения ограничений по странам/валютам и их обновления",
            ),
        ),
        RegulatoryDomain(
            tag="consumer_protection",
            name="Защита прав потребителей и раскрытие информации",
            description=(
                "Прозрачность условий, комиссии, информирование, претензии, возвраты, "
                "обработка жалоб, пользовательские коммуникации."
            ),
            default_risk="medium",
            what_to_check=(
                "Проверить, что комиссии/условия/лимиты отображаются до совершения операции и понятны пользователю.",
                "Проверить пользовательский путь для спорных операций, возвратов и сроков рассмотрения обращений.",
                "Проверить корректность маркетинговых заявлений и дисклеймеров (если фича промотируется).",
            ),
            documents_to_update=(
                "Пользовательское соглашение/оферта",
                "Тарифы и условия обслуживания",
                "Политика обработки обращений/претензий",
            ),
            processes_to_update=(
                "Процесс обработки жалоб/обращений и эскалаций",
                "Процесс управления изменениями условий и уведомлений пользователю",
            ),
        ),
        RegulatoryDomain(
            tag="data_protection",
            name="Персональные данные и приватность",
            description=(
                "Сбор и обработка персональных данных, согласия, цели, срок хранения, "
                "локализация, передача третьим лицам, права субъектов данных."
            ),
            default_risk="high",
            what_to_check=(
                "Проверить перечень ПД и целей обработки (минимизация и соответствие цели).",
                "Проверить основания обработки и текст согласий; отдельно — маркетинговые согласия.",
                "Проверить трансграничную передачу, подрядчиков и локализацию данных (если применимо).",
                "Проверить сроки хранения и процедуры удаления/анонимизации, а также исполнение прав субъекта.",
            ),
            documents_to_update=(
                "Политика обработки персональных данных",
                "Реестр обработок (data processing register)",
                "Шаблоны согласий и уведомлений о приватности",
            ),
            processes_to_update=(
                "Процесс управления запросами субъектов данных (DSAR)",
                "Процесс оценки воздействия (privacy impact assessment) для новых фич",
            ),
        ),
        RegulatoryDomain(
            tag="cybersecurity_and_fraud",
            name="Информационная безопасность и антифрод",
            description=(
                "Контроль доступа, аутентификация, логирование, защита API, антифрод-механизмы, "
                "управление инцидентами и мониторинг."
            ),
            default_risk="high",
            what_to_check=(
                "Проверить аутентификацию/авторизацию и требования к MFA/2FA для критичных операций.",
                "Проверить логирование событий безопасности и финансовых операций, неизменяемость и доступность для аудита.",
                "Проверить антифрод для новых сценариев (поведенческие сигналы, лимиты, скоринг, device fingerprint).",
                "Проверить безопасность интеграций и API (rate limit, подписанные запросы, секреты, ротация ключей).",
            ),
            documents_to_update=(
                "Политика ИБ и требования security-by-design",
                "Регламент управления инцидентами",
                "Модель угроз для новых сценариев",
            ),
            processes_to_update=(
                "Процесс управления уязвимостями и security review для релизов",
                "Процесс реагирования на фрод/инциденты и взаимодействия с поддержкой",
            ),
        ),
        RegulatoryDomain(
            tag="outsourcing_and_cloud",
            name="Аутсорсинг, подрядчики и облако",
            description=(
                "Использование сторонних провайдеров (KYC, скоринг, antifraud, облако), "
                "контроль рисков, договоры, субподрядчики, требования к данным."
            ),
            default_risk="medium",
            what_to_check=(
                "Проверить, какие данные и операции уходят внешнему провайдеру и на каком основании.",
                "Проверить требования к договору (SLA, ответственность, безопасность, аудит, субподряд).",
                "Проверить локацию обработки и хранения данных, а также план выхода/замены провайдера.",
            ),
            documents_to_update=(
                "Политика управления подрядчиками/аутсорсингом",
                "DPA/соглашения обработки данных и приложения по безопасности",
                "Реестр подрядчиков и оценок рисков",
            ),
            processes_to_update=(
                "Процесс vendor due diligence и периодической переоценки",
                "Процесс управления изменениями у провайдера (model/API change management)",
            ),
        ),
        RegulatoryDomain(
            tag="reporting_and_recordkeeping",
            name="Отчетность, хранение данных и аудит",
            description=(
                "Обязательная отчетность, хранение записей операций, аудиторские следы, "
                "журналирование и готовность к проверкам."
            ),
            default_risk="medium",
            what_to_check=(
                "Проверить сроки и требования к хранению транзакционных данных и логов.",
                "Проверить полноту данных для отчетности и расследований (какие поля, источники, точность).",
                "Проверить контроль изменений и версионирование ключевых правил/настроек комплаенса.",
            ),
            documents_to_update=(
                "Политика хранения данных/records retention",
                "Каталог отчетов и владельцы данных (reporting catalog)",
                "Регламент аудита и предоставления данных по запросу",
            ),
            processes_to_update=(
                "Процесс подготовки отчетности и контроля качества данных",
                "Процесс внутреннего аудита и подготовки к регуляторным проверкам",
            ),
        ),
        RegulatoryDomain(
            tag="credit_and_scoring",
            name="Кредитование, скоринг и автоматизированные решения",
            description=(
                "Кредиты/рассрочки/овердрафт, скоринговые модели (в том числе ИИ), "
                "объяснимость решений и управление модельными рисками."
            ),
            default_risk="high",
            what_to_check=(
                "Проверить основания принятия решения и требования к объяснимости отказа (если применимо).",
                "Проверить источники данных для скоринга и правовые основания их использования.",
                "Проверить управление модельным риском: мониторинг, дрейф, валидация, контроль изменений.",
            ),
            documents_to_update=(
                "Кредитная политика и критерии принятия решений",
                "Документация модели/скоринга (model documentation)",
                "Политика управления модельным риском (model risk management)",
            ),
            processes_to_update=(
                "Процесс валидации и мониторинга моделей",
                "Процесс рассмотрения спорных решений/апелляций клиента",
            ),
        ),
    ]


class FeatureRegulatoryAssistant:
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client
        
        # Загружаем теги
        from shared.common.tags_loader import get_tags_loader
        loader = get_tags_loader()
        tags_list = loader.get_all_tags()
        
        # Загружаем динамически сгенерированные правила из НАП
        from shared.common.rules_db import get_rules_db
        rules_db = get_rules_db()
        
        self.domains = []
        for tag_name in tags_list:
            tag_info = loader.get_tag_info(tag_name)
            if not tag_info:
                continue
                
            # Ищем правила для этого тега
            tag_rules = rules_db.get_rules_by_tag(tag_name)
            
            # Формируем динамические чек-листы на основе правил
            what_to_check = []
            docs_to_update = set()
            
            if tag_rules:
                for rule in tag_rules:
                    # requirement и verification_method идут в what_to_check
                    req = rule.get("requirement", "")
                    if req:
                        what_to_check.append(req)
                    
                    # Ищем упоминания документов в примерах или verification_method
                    ver = rule.get("verification_method", "")
                    if "документ" in ver.lower() or "политик" in ver.lower() or "регламент" in ver.lower():
                        docs_to_update.add(f"Документ, покрывающий: {rule.get('title', 'Требование НАП')}")
                        
            if not what_to_check:
                what_to_check = ["Нет извлеченных правил из НАП для этого домена. (Требуется загрузить документ через Генератор Правил)"]
            if not docs_to_update:
                docs_to_update = ["Уточняется на основе внутренних регламентов"]
                
            self.domains.append(
                RegulatoryDomain(
                    tag=tag_name,
                    name=tag_info.get("tag_name", tag_name),
                    description=tag_info.get("description", ""),
                    default_risk="medium",
                    what_to_check=tuple(what_to_check),
                    documents_to_update=tuple(docs_to_update),
                    processes_to_update=("Требует ревью комплаенс-офицером",)
                )
            )
            
        # Если tags.json пуст (чего не должно быть), фоллбек на старые домены
        if not self.domains:
            self.domains = default_fintech_domains()
            
        self._domain_by_tag = {d.tag: d for d in self.domains}

    async def analyze(self, feature_text: str) -> Dict[str, Any]:
        cleaned = (feature_text or "").strip()
        if not cleaned:
            return {
                "generated_at": _now_iso_utc(),
                "input": {"feature_text": feature_text},
                "overall_risk": "low",
                "regulatory_domains": [],
                "assumptions": [],
                "questions": ["Опишите фичу: что меняется, какие пользователи, какие деньги/данные, какие интеграции."],
                "red_flags": [],
                "notes": "Пустое описание фичи.",
            }

        if self.llm is None:
            selected = self._heuristic_select(cleaned)
            return self._build_output(
                feature_text=cleaned,
                llm_selected=selected,
                assumptions=["Классификация выполнена без LLM (эвристика по ключевым словам)."],
                questions=self._default_questions(cleaned),
                red_flags=[],
            )

        llm_selected, assumptions, questions, red_flags = await self._llm_select(cleaned)
        if not llm_selected:
            llm_selected = self._heuristic_select(cleaned)
            assumptions = _stable_dedupe(
                list(assumptions)
                + [
                    "LLM не вернул домены, использована эвристика по ключевым словам.",
                ]
            )

        return self._build_output(
            feature_text=cleaned,
            llm_selected=llm_selected,
            assumptions=assumptions,
            questions=_stable_dedupe(list(questions) + self._default_questions(cleaned)),
            red_flags=red_flags,
        )

    async def _llm_select(
        self,
        feature_text: str,
    ) -> Tuple[List[Dict[str, Any]], List[str], List[str], List[str]]:
        domain_list = [
            {"tag": d.tag, "name": d.name, "description": d.description}
            for d in self.domains
        ]
        system_prompt = (
            "Ты — регуляторный ассистент для продуктовых и инженерных команд финтеха. "
            "По описанию фичи выбери релевантные регуляторные домены из предложенного списка. "
            "Верни только JSON без markdown."
        )
        task = json.dumps(
            {
                "feature_description": feature_text,
                "available_domains": domain_list,
                "output_format": {
                    "selected": [
                        {
                            "tag": "one_of_available_domains.tag",
                            "risk": "low|medium|high",
                            "reason": "кратко почему домен релевантен по описанию фичи",
                        }
                    ],
                    "assumptions": ["..."],
                    "questions": ["..."],
                    "red_flags": ["..."],
                },
                "rules": [
                    "Не придумывай домены вне списка.",
                    "Выбирай только из предоставленных available_domains.",
                    "Если информации мало, укажи assumptions и questions.",
                    "Выбирай 2–6 доменов, если не очевидно — меньше.",
                    "ВЕРНИ ТОЛЬКО ВАЛИДНЫЙ JSON.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )

        raw = await self.llm.request(
            system_prompt=system_prompt,
            task=task,
            temperature=0.0,
            max_tokens=2500,
            response_format=None, # Отключаем, чтобы не сломать Yandex API
        )
        parsed = self._safe_json_parse(raw)
        selected = parsed.get("selected") if isinstance(parsed, dict) else None
        assumptions = parsed.get("assumptions") if isinstance(parsed, dict) else None
        questions = parsed.get("questions") if isinstance(parsed, dict) else None
        red_flags = parsed.get("red_flags") if isinstance(parsed, dict) else None

        norm_selected: List[Dict[str, Any]] = []
        if isinstance(selected, list):
            for item in selected:
                if not isinstance(item, dict):
                    continue
                tag = item.get("tag")
                if not tag or tag not in self._domain_by_tag:
                    continue
                risk = item.get("risk")
                if risk not in ("low", "medium", "high"):
                    risk = self._domain_by_tag[tag].default_risk
                norm_selected.append(
                    {
                        "tag": tag,
                        "risk": risk,
                        "reason": (item.get("reason") or "").strip(),
                    }
                )

        return (
            norm_selected,
            _stable_dedupe(assumptions or []),
            _stable_dedupe(questions or []),
            _stable_dedupe(red_flags or []),
        )

    def _safe_json_parse(self, raw: str) -> Dict[str, Any]:
        cleaned = (raw or "").strip()
        # Ищем первый символ '{' и последний '}'
        start_idx = cleaned.find('{')
        end_idx = cleaned.rfind('}')
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            cleaned = cleaned[start_idx:end_idx + 1]
            
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
            return {}
        except json.JSONDecodeError as e:
            # Логируем ошибку, чтобы было проще дебажить
            import logging
            logger = logging.getLogger(__name__)
            logger.error("Не удалось распарсить JSON от LLM в FeatureAssistant: %s\nСырой ответ:\n%s", e, raw[:500])
            return {}

    def _build_output(
        self,
        feature_text: str,
        llm_selected: List[Dict[str, Any]],
        assumptions: List[str],
        questions: List[str],
        red_flags: List[str],
    ) -> Dict[str, Any]:
        final_domains = []
        all_checks = []
        all_docs = []
        all_procs = []
        risks = []
        
        for item in llm_selected:
            tag = item.get("tag")
            if not tag or tag not in self._domain_by_tag:
                continue
                
            domain = self._domain_by_tag[tag]
            risk = item.get("risk") or domain.default_risk
            if risk not in ("low", "medium", "high"):
                risk = domain.default_risk
            risks.append(risk)
            
            final_domains.append({
                "tag": domain.tag,
                "name": domain.name,
                "risk": risk,
                "reason": (item.get("reason") or "").strip(),
                "what_to_check": list(domain.what_to_check),
                "documents_to_update": list(domain.documents_to_update),
                "processes_to_update": list(domain.processes_to_update)
            })
            
            all_checks.extend(domain.what_to_check)
            all_docs.extend(domain.documents_to_update)
            all_procs.extend(domain.processes_to_update)

        overall_risk = _max_risk(risks) if risks else "low"

        return {
            "generated_at": _now_iso_utc(),
            "input": {"feature_text": feature_text},
            "overall_risk": overall_risk,
            "regulatory_domains": final_domains,
            "checks": _stable_dedupe(all_checks),
            "documents_to_update": _stable_dedupe(all_docs),
            "processes_to_update": _stable_dedupe(all_procs),
            "assumptions": assumptions,
            "questions": questions,
            "red_flags": red_flags,
        }

    def _heuristic_select(self, feature_text: str) -> List[Dict[str, Any]]:
        text = feature_text.lower()
        matches: List[Tuple[str, int]] = []

        def add(tag: str, score: int) -> None:
            matches.append((tag, score))

        if re.search(r"\b(перевод|плат[её]ж|p2p|qr|кошел[её]к|e-?money|пополнен|вывод)\b", text):
            add("payments_and_transfers", 3)
            add("aml_cft", 2)
            add("reporting_and_recordkeeping", 1)
        if re.search(r"\b(карта|эквайринг|3ds|acquiring|терминал|процессинг)\b", text):
            add("cards_and_acquiring", 2)
            add("cybersecurity_and_fraud", 2)
        if re.search(r"\b(kyc|идентификац|верификац|паспорт|селфи|liveness|онбординг)\b", text):
            add("kyc_onboarding", 3)
            add("aml_cft", 2)
            add("data_protection", 2)
        if re.search(r"\b(санкци|pep|ofac|un list|eu list)\b", text):
            add("sanctions_screening", 3)
            add("aml_cft", 2)
        if re.search(r"\b(международ|кроссбордер|cross-?border|валют|swift|visa direct|mastercard send)\b", text):
            add("fx_and_crossborder", 3)
            add("sanctions_screening", 2)
        if re.search(r"\b(персональн|pd|pii|приватн|согласие|локализац|удален(и|ие)|gdpr)\b", text):
            add("data_protection", 3)
        if re.search(r"\b(2fa|mfa|otp|биометр|фрод|antifraud|security|аутентификац|авторизац)\b", text):
            add("cybersecurity_and_fraud", 3)
        if re.search(r"\b(подрядчик|провайдер|outsourc|cloud|облак|saas|api сторонн)\b", text):
            add("outsourcing_and_cloud", 2)
            add("data_protection", 1)
        if re.search(r"\b(кредит|скоринг|лимит кредит|рассрочк|bnpl|underwriting|платежеспособност)\b", text):
            add("credit_and_scoring", 3)
            add("consumer_protection", 1)

        add("licensing_and_activity_scope", 1)

        scored: Dict[str, int] = {}
        for tag, score in matches:
            scored[tag] = max(scored.get(tag, 0), score)

        best = sorted(scored.items(), key=lambda x: (-x[1], x[0]))[:6]
        out: List[Dict[str, Any]] = []
        for tag, _score in best:
            domain = self._domain_by_tag.get(tag)
            if not domain:
                continue
            out.append(
                {
                    "tag": tag,
                    "risk": domain.default_risk,
                    "reason": "Выбрано по ключевым словам/сигналам в описании фичи.",
                }
            )
        return out

    def _default_questions(self, feature_text: str) -> List[str]:
        return [
            "Есть ли движение денег (пополнение/вывод/переводы), и кто является отправителем/получателем?",
            "Какие типы клиентов (физлица/юрлица), какие уровни KYC и какие лимиты зависят от уровня?",
            "Есть ли трансграничные операции/иностранная валюта/нерезиденты?",
            "Какие данные собираются и куда передаются (подрядчики/облако/интеграции)?",
        ]

