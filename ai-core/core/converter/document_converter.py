import logging
import warnings
from pathlib import Path
from typing import Optional

# Подавляем предупреждения от transformers (конкретно Accessing `__path__` from `.models.aria.image_processing_aria`)
warnings.filterwarnings("ignore", module="transformers")

try:
    from docling.document_converter import DocumentConverter
except ImportError:
    DocumentConverter = None

logger = logging.getLogger(__name__)


class DoclingConverter:
    """Утилита для конвертации PDF/DOCX/DOC документов в Markdown с помощью библиотеки docling."""
    
    def __init__(self):
        if DocumentConverter is None:
            raise RuntimeError(
                "Библиотека docling не установлена. "
                "Пожалуйста, установите её командой `pip install docling`."
            )
        self.converter = DocumentConverter()

    def convert_to_markdown(
        self, 
        input_path: str | Path, 
        output_path: Optional[str | Path] = None
    ) -> str:
        """
        Конвертирует документ в markdown.
        
        Args:
            input_path: Путь к исходному файлу (.pdf, .docx, .doc и др.)
            output_path: Путь для сохранения результата (.md). Если None, только возвращает текст.
            
        Returns:
            Текст в формате Markdown.
        """
        input_file = Path(input_path)
        if not input_file.exists():
            raise FileNotFoundError(f"Входной файл не найден: {input_file}")

        logger.info("Запуск конвертации файла %s через Docling...", input_file.name)
        result = self.converter.convert(str(input_file))
        markdown_text = result.document.export_to_markdown()

        if output_path:
            out_file = Path(output_path)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(markdown_text, encoding="utf-8")
            logger.info("Markdown успешно сохранен в %s", out_file)

        return markdown_text
