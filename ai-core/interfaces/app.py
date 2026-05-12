import sys
import os
import warnings

# Подавляем известные предупреждения от сторонних библиотек
warnings.filterwarnings("ignore", module="transformers")

# Добавляем текущую директорию в PYTHONPATH для корректного импорта модулей ai-core
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import streamlit as st
import asyncio
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from shared.common.client.llm_client import LLMClient
from interfaces.feature_assistant import FeatureRegulatoryAssistant
from core.rules_generator.generator import RuleGenerator
from core.converter.document_converter import DoclingConverter

# Загружаем переменные окружения (.env)
load_dotenv()

st.set_page_config(page_title="AI Core RegTech", page_icon="🏦", layout="wide")

@st.cache_resource
def get_llm_client():
    try:
        return LLMClient.from_env()
    except Exception as e:
        return None

llm = get_llm_client()

st.title("RegTech AI Assistant 🏦")

if llm is None:
    st.warning("⚠️ LLM клиент не настроен. Пожалуйста, убедитесь, что в файле .env (или в окружении) прописаны `YANDEX_CLOUD_API_KEY` и `YANDEX_CLOUD_FOLDER`. Ассистент будет работать в режиме fallback (эвристика), а генерация правил будет недоступна.")

tab1, tab2, tab3 = st.tabs(["Анализ фичей (Feature Assistant)", "Генерация правил из НАП", "Конвертер документов (Docling)"])

# ==========================================
# ВКЛАДКА 1: Feature Assistant
# ==========================================
with tab1:
    st.header("Анализ продуктовой фичи (на базе загруженных НАП)")
    st.markdown("Введите описание вашей продуктовой фичи. Ассистент определит затронутые регуляторные области и **автоматически сформирует чек-лист на основе правил, ранее извлеченных из загруженных документов (НАП)**.")
    
    # Показываем статус базы знаний
    from shared.common.rules_db import get_rules_db
    db = get_rules_db()
    total_rules = len(db.get_all_rules())
    if total_rules == 0:
        st.warning("⚠️ База знаний пуста. Рекомендуется сначала загрузить документы (НАП) во вкладке 'Генерация правил из НАП', чтобы чек-листы формировались динамически.")
    else:
        st.success(f"📚 База знаний активна: загружено {total_rules} регуляторных правил.")
        
    feature_text = st.text_area("Опишите фичу", height=150, placeholder="Например: Внедряем автоматический перевод на новый электронный кошелек с привязкой к номеру телефона...")
    
    col1, col2 = st.columns([1, 4])
    with col1:
        use_llm = st.checkbox("Использовать LLM", value=(llm is not None), disabled=(llm is None))
    
    if st.button("Анализировать фичу", type="primary"):
        if not feature_text.strip():
            st.error("Пожалуйста, введите описание фичи.")
        else:
            with st.spinner("Проводим анализ..."):
                active_llm = llm if use_llm else None
                assistant = FeatureRegulatoryAssistant(llm_client=active_llm)
                
                # Запуск асинхронного метода в синхронном Streamlit
                result = asyncio.run(assistant.analyze(feature_text))
                
                st.subheader("Результаты анализа")
                
                # Отображение риска
                risk_color = {
                    "low": "🟢 Низкий",
                    "medium": "🟡 Средний",
                    "high": "🔴 Высокий"
                }
                overall_risk = result.get('overall_risk', 'low')
                st.markdown(f"**Общий уровень риска:** {risk_color.get(overall_risk, overall_risk)}")
                
                # Домены
                domains = result.get('regulatory_domains', [])
                if domains:
                    st.markdown("### Затронутые регуляторные домены")
                    for d in domains:
                        d_risk = d.get('risk', 'low')
                        with st.expander(f"{d.get('name')} (Риск: {d_risk.upper()})", expanded=True):
                            st.write(f"**Обоснование:** {d.get('reason')}")
                            
                            c1, c2, c3 = st.columns(3)
                            with c1:
                                st.markdown("**Что проверить:**")
                                for check in d.get('what_to_check', []):
                                    st.markdown(f"- {check}")
                            with c2:
                                st.markdown("**Документы для обновления:**")
                                for doc in d.get('documents_to_update', []):
                                    st.markdown(f"- {doc}")
                            with c3:
                                st.markdown("**Процессы для обновления:**")
                                for proc in d.get('processes_to_update', []):
                                    st.markdown(f"- {proc}")
                else:
                    st.info("Регуляторные домены не обнаружены.")
                
                # Дополнительная информация
                st.markdown("### Дополнительная информация")
                if result.get("questions"):
                    st.markdown("**Уточняющие вопросы:**")
                    for q in result.get("questions", []):
                        st.markdown(f"- ❓ {q}")
                        
                if result.get("assumptions"):
                    st.markdown("**Сделанные допущения:**")
                    for a in result.get("assumptions", []):
                        st.markdown(f"- 💡 {a}")
                        
                if result.get("red_flags"):
                    st.markdown("**Красные флаги:**")
                    for rf in result.get("red_flags", []):
                        st.markdown(f"- 🚩 **{rf}**")

                st.write("---")
                with st.expander("Сырой JSON ответ"):
                    st.json(result)

# ==========================================
# ВКЛАДКА 2: Rule Generator
# ==========================================
with tab2:
    st.header("Извлечение правил из НАП")
    st.markdown("Загрузите нормативно-правовой акт или внутренний регламент в формате **Markdown (.md)**. LLM извлечет атомарные правила и при необходимости динамически добавит новые теги.")
    
    uploaded_file = st.file_uploader("Загрузите документ", type=["md"])
    
    if st.button("Сгенерировать правила", type="primary", key="gen_rules"):
        if uploaded_file is None:
            st.error("Пожалуйста, загрузите Markdown файл.")
        elif llm is None:
            st.error("Для извлечения правил требуется активный LLM клиент. Проверьте .env файл.")
        else:
            with st.spinner("Чтение документа и генерация правил (может занять 1-3 минуты)..."):
                # Сохраняем во временный файл
                with tempfile.NamedTemporaryFile(delete=False, suffix=".md", mode="wb") as tmp:
                    tmp.write(uploaded_file.getvalue())
                    tmp_path = tmp.name
                
                try:
                    generator = RuleGenerator(
                        llm_client=llm,
                        max_tokens=50000,
                        chunk_size=40000,
                        overlap=2000,
                        temperature=0.0,
                        response_max_tokens=8000,
                    )
                    
                    result = asyncio.run(generator.generate_rules(doc_path=tmp_path))
                    
                    if result.get("status") == "error":
                        st.error(f"Ошибка при генерации: {result.get('error')}")
                    else:
                        st.success(f"Успешно извлечено правил: {len(result.get('rules', []))}")
                        
                        meta = result.get("metadata", {})
                        st.caption(f"Время обработки: {meta.get('processing_time', 0):.1f} сек | Вызовов LLM: {meta.get('llm_calls', 0)}")
                        
                        rules = result.get("rules", [])
                        for i, rule in enumerate(rules):
                            tag = rule.get('tag', 'unknown')
                            severity = rule.get('severity', 'medium')
                            
                            with st.expander(f"[{tag}] {rule.get('title', 'Без названия')}"):
                                st.markdown(f"**ID:** `{rule.get('rule_id')}` | **Критичность:** `{severity}`")
                                st.markdown(f"**Требование:** {rule.get('requirement')}")
                                st.markdown(f"**Как проверить:** {rule.get('verification_method')}")
                                
                                src = rule.get('source', {})
                                if src:
                                    st.info(f"**Источник:** {src.get('document', '')} ({src.get('section', '')})\n\n*\"{src.get('quote', '')}\"*")
                                    
                        st.write("---")
                        with st.expander("Сырой JSON ответ"):
                            st.json(result)
                except Exception as e:
                    st.error(f"Произошла непредвиденная ошибка: {e}")
                finally:
                    # Удаляем временный файл
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)

# ==========================================
# ВКЛАДКА 3: Docling Converter
# ==========================================
with tab3:
    st.header("Конвертер документов (PDF/DOCX/DOC → Markdown)")
    st.markdown("Загрузите документ в формате PDF или Word (DOCX, DOC). Библиотека **Docling** извлечёт текст, структуру и таблицы, сохранив их в формате Markdown, который идеально подходит для передачи в LLM.")
    
    upload_file_convert = st.file_uploader("Выберите файл для конвертации", type=["pdf", "docx", "doc", "pptx", "xlsx", "html"])
    
    if st.button("Конвертировать", type="primary", key="convert_doc"):
        if upload_file_convert is None:
            st.error("Пожалуйста, выберите файл.")
        else:
            with st.spinner("Извлекаем текст и структуру документа... Это может занять некоторое время (работают модели OCR)."):
                try:
                    converter = DoclingConverter()
                    
                    # Сохраняем исходный файл
                    ext = Path(upload_file_convert.name).suffix
                    with tempfile.NamedTemporaryFile(delete=False, suffix=ext, mode="wb") as tmp_in:
                        tmp_in.write(upload_file_convert.getvalue())
                        tmp_in_path = tmp_in.name
                        
                    # Конвертируем (получаем текст)
                    markdown_result = converter.convert_to_markdown(tmp_in_path)
                    
                    st.success("✅ Документ успешно конвертирован!")
                    
                    st.download_button(
                        label="📥 Скачать Markdown",
                        data=markdown_result,
                        file_name=f"{Path(upload_file_convert.name).stem}.md",
                        mime="text/markdown"
                    )
                    
                    with st.expander("Посмотреть результат", expanded=True):
                        st.markdown(markdown_result)
                        
                except Exception as e:
                    st.error(f"Ошибка конвертации: {e}")
                finally:
                    if 'tmp_in_path' in locals() and os.path.exists(tmp_in_path):
                        os.unlink(tmp_in_path)
