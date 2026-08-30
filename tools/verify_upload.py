
# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import asyncio, os, sys
from pathlib import Path
ROOT = Path(_REPO_ROOT)
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from app.rag import get_retriever, parse_sources


async def main():
    r = get_retriever()
    ctx = await r.retrieve("kedi besleme politikasi")
    print("KAYNAKLAR:", [s["source"] for s in parse_sources(ctx)])


asyncio.run(main())
