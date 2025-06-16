from dotenv import load_dotenv
load_dotenv()
import os
import requests

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

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

        # Debugging: print full response if unexpected
        if "choices" not in json_data or not json_data["choices"]:
            print("❌ LLM response incomplete or missing:", json_data)
            return ""

        return json_data["choices"][0]["message"]["content"].strip()
    except requests.exceptions.RequestException as e:
        print("❌ Request to OpenAI failed:", str(e))
        return ""
    except Exception as e:
        print("❌ Unexpected error in ask_llm:", str(e))
        return ""
