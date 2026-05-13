import asyncio
import json
import os
import sys
from elasticsearch import AsyncElasticsearch
from dotenv import load_dotenv

# Добавляем родительскую директорию в PYTHONPATH, чтобы импорты 'agents.X' работали корректно
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

load_dotenv()

from agents.tools._es import get_es, embed, EMBEDDING_DIMS, close_es

ES_INDEX = os.environ.get("ES_INDEX", "regtech-docs")

MAPPING = {
    "mappings": {
        "properties": {
            "doc_id": {"type": "keyword"},
            "source": {"type": "keyword"},
            "category": {"type": "keyword"},
            "language": {"type": "keyword"},
            "title": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "source_url": {"type": "keyword"},
            "has_rules": {"type": "boolean"},
            "rules_count": {"type": "integer"},
            "tags": {"type": "keyword"},
            "severities": {"type": "keyword"},
            "full_text": {"type": "text"},
            "embedding": {
                "type": "dense_vector",
                "dims": EMBEDDING_DIMS,
                "index": True,
                "similarity": "cosine"
            },
            "rules": {
                "type": "nested",
                "properties": {
                    "rule_id": {"type": "keyword"},
                    "tag": {"type": "keyword"},
                    "title": {"type": "text"},
                    "requirement": {"type": "text"},
                    "verification_method": {"type": "text"},
                    "severity": {"type": "keyword"},
                    "positive_examples": {"type": "text"},
                    "negative_examples": {"type": "text"}
                }
            }
        }
    }
}

async def main():
    print(f"Index: {ES_INDEX}")
    data_path = os.path.join(os.path.dirname(__file__), "data", "mock_regulations.json")
    
    if not os.path.exists(data_path):
        print(f"Data file not found at {data_path}")
        return

    with open(data_path, "r", encoding="utf-8") as f:
        docs = json.load(f)

    es = get_es()
    
    try:
        # Create or recreate index
        exists = await es.indices.exists(index=ES_INDEX)
        if exists:
            print(f"Deleting existing index {ES_INDEX}...")
            await es.indices.delete(index=ES_INDEX)
            
        print(f"Creating index {ES_INDEX}...")
        await es.indices.create(index=ES_INDEX, body=MAPPING)

        # Index documents
        for doc in docs:
            print(f"Embedding and indexing: {doc['title']} (rules: {doc['rules_count']})")
            doc['embedding'] = await embed(doc['full_text'])
            await es.index(index=ES_INDEX, id=doc['doc_id'], document=doc)
        
        # Refresh index so it's immediately searchable
        await es.indices.refresh(index=ES_INDEX)
        print("✅ Data successfully seeded into Elasticsearch!")
    except Exception as e:
        print(f"❌ Error seeding Elasticsearch: {e}")
    finally:
        await close_es()

if __name__ == "__main__":
    asyncio.run(main())
