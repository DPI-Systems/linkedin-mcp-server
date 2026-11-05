"""Re-parse every stored profile after a parser change: uv run python -m scripts.reparse_profiles"""

from app.db.session import SessionLocal
from app.services.automations import reparse_profiles

if __name__ == "__main__":
    with SessionLocal() as db:
        print(f"{reparse_profiles(db)} perfiles reinterpretados")
