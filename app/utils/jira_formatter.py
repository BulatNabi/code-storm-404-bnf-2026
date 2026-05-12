import re

def md_to_jira_adf(markdown_text: str) -> dict:
    """
    Конвертер Markdown -> Atlassian Document Format (ADF) для хакатона.
    Убирает синтаксис, разбивает по строкам на абзацы.
    """
    if not markdown_text.strip():
        return {"type": "doc", "version": 1, "content": []}
        
    lines = markdown_text.strip().split('\n')
    content = []
    
    for line in lines:
        if line.strip():
            # Удаляем MD-разметку, оставляем текст
            clean = re.sub(r'[*_#>`~\[\]()!]', '', line)
            content.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": clean.strip()}]
            })
            
    return {"type": "doc", "version": 1, "content": content}