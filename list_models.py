"""Helper: print the Gemini models your API key can use for generateContent."""
import os

from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
for m in client.models.list():
    name = m.name.replace("models/", "")
    if "generateContent" in (m.supported_actions or []) and not any(
        bad in name for bad in ("tts", "image", "embedding", "robotics", "lyria", "live")
    ):
        print(name)
