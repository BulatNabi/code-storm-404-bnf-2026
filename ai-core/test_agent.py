import asyncio
import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Явно загружаем .env из папки agents, если он лежит там
env_path = Path(__file__).parent / "agents" / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    # Иначе загружаем из корня
    load_dotenv()

# Если LLM_API_KEY существует, но OPENAI_API_KEY нет, прокидываем его, 
# так как библиотека openai может строго требовать OPENAI_API_KEY в окружении
if os.environ.get("LLM_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.environ["LLM_API_KEY"]

from agents.graph import build_agent
from agents.tools._es import close_es

async def main():
    agent = build_agent()
    print("=" * 60)
    print("🏦 RegTech AI Assistant (Interactive Mode)")
    print("=" * 60)
    print("Введите описание фичи для проверки (или 'exit' для выхода).\n")
    
    while True:
        try:
            feature_desc = input("\n📝 Описание фичи: ")
            if feature_desc.strip().lower() in ('exit', 'quit', 'q'):
                print("Завершение работы...")
                break
                
            if not feature_desc.strip():
                continue
                
            print("\n🔄 Агент анализирует фичу и ищет правила в базе...\n")
            inputs = {"messages": [("user", feature_desc)]}
            
            async for event in agent.astream(inputs, stream_mode="values"):
                message = event["messages"][-1]
                # Чтобы не засорять вывод, печатаем только AI сообщения (tool calls) и финальный ответ
                if message.type == "ai":
                    if hasattr(message, "tool_calls") and message.tool_calls:
                        for call in message.tool_calls:
                            print(f"🛠️  Вызов инструмента: {call['name']} (args: {call['args']})")
                    elif message.content:
                        content = message.content.strip()
                        # Очищаем от возможных маркдаун-тегов, если LLM их добавила
                        if content.startswith("```json"):
                            content = content[7:]
                        elif content.startswith("```"):
                            content = content[3:]
                        if content.endswith("```"):
                            content = content[:-3]
                        content = content.strip()
                        
                        try:
                            report_data = json.loads(content)
                            report_path = "final_report.json"
                            with open(report_path, "w", encoding="utf-8") as f:
                                json.dump(report_data, f, ensure_ascii=False, indent=2)
                            
                            print(f"\n✅ Финальный ответ Агента успешно сформирован и сохранен в файл: {report_path}")
                            print("-" * 60)
                            print(json.dumps(report_data, ensure_ascii=False, indent=2))
                            print("-" * 60)
                        except json.JSONDecodeError:
                            print("\n⚠️ Агент вернул текст, который не удалось распарсить как JSON:\n")
                            print("-" * 60)
                            print(message.content)
                            print("-" * 60)

        except KeyboardInterrupt:
            print("\nЗавершение работы...")
            break
        except Exception as e:
            print(f"❌ Ошибка во время выполнения: {e}")
            
    await close_es()

if __name__ == "__main__":
    asyncio.run(main())
