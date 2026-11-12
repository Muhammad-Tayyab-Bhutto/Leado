"""Print .env as shell-safe `export` lines (handles any special characters in values)."""
import shlex, sys
from dotenv import dotenv_values
for k, v in dotenv_values(sys.argv[1] if len(sys.argv) > 1 else ".env").items():
    print(f"export {k}={shlex.quote(v or '')}")
