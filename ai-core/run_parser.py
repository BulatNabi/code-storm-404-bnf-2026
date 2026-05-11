import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from chunker import DocumentParser, LLMClient

load_dotenv()

DEFAULT_MD_PATH = Path('/Users/mike/Projects/Techno/NAPP/ParserProject/TZ/TZ.md')
DEFAULT_MAX_DEPTH = 3


def setup_logging(verbose: bool = False, quiet: bool = False):
    """Настраивает уровень логирования."""
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )


def parse_args():
    """Парсит аргументы командной строки."""
    parser = argparse.ArgumentParser(
        description="Парсер технических заданий с восстановлением иерархии через LLM"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Включить DEBUG логирование (все детали)"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Только WARNING и ERROR (минимум вывода)"
    )
    parser.add_argument(
        "depth",
        nargs="?",
        type=int,
        default=DEFAULT_MAX_DEPTH,
        help=f"Максимальная глубина вложенности (по умолчанию: {DEFAULT_MAX_DEPTH})"
    )
    parser.add_argument(
        "file",
        nargs="?",
        type=Path,
        default=DEFAULT_MD_PATH,
        help=f"Путь к markdown файлу (по умолчанию: {DEFAULT_MD_PATH})"
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    # DEBUG: выведем что пришло в args ДО настройки логирования
    import sys as system
    system.stderr.write(f"\n=== DEBUG ===\n")
    system.stderr.write(f"sys.argv = {system.argv}\n")
    system.stderr.write(f"args.depth = {args.depth}\n")
    system.stderr.write(f"args.file = {args.file}\n")
    system.stderr.write(f"type(args.file) = {type(args.file)}\n")
    system.stderr.write(f"=============\n\n")
    system.stderr.flush()

    setup_logging(verbose=args.verbose, quiet=args.quiet)

    logger = logging.getLogger(__name__)

    max_depth = args.depth
    md_path = args.file

    if max_depth < 1:
        logger.error("Глубина должна быть >= 1")
        sys.exit(1)
    if max_depth > 6:
        logger.warning(f"Глубина {max_depth} может быть избыточной. Рекомендуется 2-4.")

    logger.info(f"Запуск с глубиной: {max_depth}")
    logger.info(f"Файл: {md_path}")

    llm_client = LLMClient.from_env()
    parser = DocumentParser(llm_client)

    chunks = await parser.parse(md_path, max_depth=max_depth)

    logger.info("=== ДЕРЕВО ЧАНКОВ ===")
    parser.tree_builder.print_tree(chunks, max_depth=max_depth)

    project_root = Path(__file__).parent
    output_dir = project_root / "chunks"
    output_dir.mkdir(exist_ok=True)

    document_name = md_path.stem
    output_path = output_dir / f"{document_name}_chunks.json"

    parser.tree_builder.save_json(
        chunks=chunks,
        output_path=output_path,
        document_name=document_name,
        include_content=True
    )


if __name__ == "__main__":
    asyncio.run(main())
