import json
from pathlib import Path
from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)

RULES_FILE = Path(__file__).parent.parent / "schema" / "rules.json"

class RulesDB:
    """Централизованное хранилище правил (knowledge base)."""
    
    def __init__(self, db_path: Path = RULES_FILE):
        self.db_path = db_path
        self._rules: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        """Загружает правила из файла, если он существует."""
        if not self.db_path.exists():
            logger.info("Файл правил не найден, будет создан новый: %s", self.db_path)
            self._save()
            return
            
        try:
            rules_list = json.loads(self.db_path.read_text(encoding="utf-8"))
            self._rules = {r["rule_id"]: r for r in rules_list if "rule_id" in r}
        except json.JSONDecodeError:
            logger.error("Ошибка чтения %s, файл поврежден.", self.db_path)
            self._rules = {}

    def _save(self) -> None:
        """Сохраняет текущие правила в файл."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        rules_list = list(self._rules.values())
        self.db_path.write_text(
            json.dumps(rules_list, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def add_rules(self, new_rules: List[Dict[str, Any]]) -> None:
        """Добавляет новые правила или обновляет существующие по rule_id."""
        for rule in new_rules:
            if "rule_id" not in rule:
                continue
            self._rules[rule["rule_id"]] = rule
        self._save()
        
    def get_all_rules(self) -> List[Dict[str, Any]]:
        """Возвращает список всех правил."""
        return list(self._rules.values())
        
    def get_rules_by_tag(self, tag: str) -> List[Dict[str, Any]]:
        """Возвращает правила для конкретного тега (домена)."""
        return [r for r in self._rules.values() if r.get("tag") == tag]

_rules_db = None

def get_rules_db() -> RulesDB:
    global _rules_db
    if _rules_db is None:
        _rules_db = RulesDB()
    return _rules_db
