import logging
import httpx
import re
from typing import Optional

from ..models.context import SessionObject

logger = logging.getLogger(__name__)

class SummaryUpdater:
    def __init__(self, ollama_url="http://localhost:11434/api/generate"):
        self.ollama_url = ollama_url
        self.ollama_model = "llama3" # Or whichever small model is commonly available

    async def update_summary(self, session: SessionObject, user_message: str, response_text: str) -> str:
        """
        Updates the conversation summary asynchronously.
        Returns the new summary string.
        """
        new_summary = ""
        # Try Strategy 1: Ollama
        try:
            prompt = f"""Given the previous conversation summary and the latest exchange, produce a concise 3-5 sentence summary of what has been discussed and decided so far.
Previous summary: {session.conversation_summary}
Latest user message: {user_message}
Latest assistant response: {response_text[:500]}
New summary:"""
            
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    self.ollama_url,
                    json={
                        "model": self.ollama_model,
                        "prompt": prompt,
                        "stream": False
                    },
                    timeout=3.0
                )
                if res.status_code == 200:
                    data = res.json()
                    new_summary = data.get("response", "").strip()
        except Exception as e:
            # Fallback to Strategy 2: Heuristic
            logger.debug(f"Ollama summarization unavailable, falling back to heuristic: {e}")

        if not new_summary:
            # Strategy 2: Heuristic extraction
            # Extract first sentence
            match = re.split(r'(?<=[.!?]) +', response_text)
            first_sentence = match[0] if match else response_text[:100]
            
            # Remove any markdown headers or code block markers from the sentence
            first_sentence = re.sub(r'```.*?```', '', first_sentence, flags=re.DOTALL)
            first_sentence = re.sub(r'#+\s*', '', first_sentence)
            first_sentence = first_sentence.replace('\n', ' ').strip()
            
            if len(first_sentence) > 150:
                first_sentence = first_sentence[:147] + "..."

            if session.conversation_summary:
                new_summary = f"{session.conversation_summary}\n- {first_sentence}"
            else:
                new_summary = f"- {first_sentence}"
                
        # Trim to roughly 500 tokens (approx 2000 chars)
        if len(new_summary) > 2000:
            # keep the last 2000 chars roughly, split by lines
            lines = new_summary.split('\n')
            while len('\n'.join(lines)) > 2000 and len(lines) > 1:
                lines.pop(0)
            new_summary = '\n'.join(lines)

        session.conversation_summary = new_summary
        return new_summary
