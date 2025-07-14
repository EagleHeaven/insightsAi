from dotenv import load_dotenv
load_dotenv()
import os
import requests

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

def _sanitize_prompt(prompt: str) -> str:
    # Enlève caractères invisibles et coupe si trop long
    prompt = str(prompt)
    prompt = re.sub(r"[^\x20-\x7EÀ-ÿ’€.,;:!?()\[\]\-\'\"%$@]", "", prompt)
    # Limite la taille du prompt à 4000 caractères
    return prompt[:4000]

def ask_llm(prompt: str) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Tu es Echo, analyste CX senior pour hôtels et restaurants."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 1200,
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        json_data = resp.json()
        if "choices" not in json_data or not json_data["choices"]:
            logger.error("LLM response incomplete or missing keys.")
            return ""
        # Limite la taille de la réponse (évite OOM + injection ultra longue)
        result = json_data["choices"][0]["message"]["content"]
        return result.strip()[:5000]  # 5000 caractères max en sortie
    except requests.exceptions.RequestException as e:
        logger.error(f"Request to OpenAI failed: {type(e).__name__} - {str(e)}")
        return ""
    except Exception as e:
        logger.error(f"Unexpected error in ask_llm: {type(e).__name__} - {str(e)}")
        return ""
