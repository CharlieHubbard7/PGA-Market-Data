import os
from pathlib import Path
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization

load_dotenv()

_PROJECT_ROOT = Path(__file__).parent

API_KEY: str = os.environ["KALSHI_API_KEY"]

# DataGolf model API key — paste it into .env as DATAGOLF_API_KEY=...
# Optional: if unset, the model column simply shows "—" and nothing else breaks.
DATAGOLF_API_KEY: str | None = os.environ.get("DATAGOLF_API_KEY") or None

def load_private_key():
    key_path = _PROJECT_ROOT / os.environ["KALSHI_PRIVATE_KEY_PATH"]
    with open(key_path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)
