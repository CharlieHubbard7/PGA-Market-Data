import os
from pathlib import Path
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization

load_dotenv()

_PROJECT_ROOT = Path(__file__).parent

API_KEY: str = os.environ["KALSHI_API_KEY"]

def load_private_key():
    key_path = _PROJECT_ROOT / os.environ["KALSHI_PRIVATE_KEY_PATH"]
    with open(key_path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)
