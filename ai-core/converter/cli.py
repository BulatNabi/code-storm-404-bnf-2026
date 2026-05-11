import argparse
import logging
import sys
from pathlib import Path

# Добавляем текущую директорию в PYTHONPATH для независимого запуска
sys.path.append(str(Path(__file__).parent.absolute()))

from document_converter import DoclingConverter


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Конвертация PDF, DOCX, DOC и других форматов в Markdown с помощью Docling"
    )
    parser.add_argument("--input", required=True, help="Путь к исходному файлу (.pdf, .docx, .doc)")
    parser.add_argument("--output", help="Путь для сохранения результата (.md) (опционально)")
    parser.add_argument(
        "--log-level", 
        default="INFO", 
        choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    try:
        converter = DoclingConverter()
        input_path = Path(args.input)
        
        output_path = args.output
        if not output_path:
            out_dir = Path("converted_docs")
            out_dir.mkdir(exist_ok=True)
            output_path = out_dir / f"{input_path.stem}.md"
            
        converter.convert_to_markdown(input_path, output_path)
        logger.info("Конвертация успешно завершена!")
    except Exception as e:
        logger.error("Ошибка при конвертации: %s", e)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
