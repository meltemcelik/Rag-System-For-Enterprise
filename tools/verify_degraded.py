"""status() eksik embedding modelini yakaliyor mu?"""

# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import asyncio, os, sys
from pathlib import Path
ROOT = Path(_REPO_ROOT)
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)

from app.rag import BM25Retriever, Chunk, GroundedRetriever, HybridRetriever, RagConfig, status


async def main():
    chunks = [Chunk(text="deneme", source="a.md", tokens=["deneme"])]

    cfg_ok = RagConfig()
    print("gercek model :", await status(HybridRetriever(chunks, cfg_ok)))

    cfg_bad = RagConfig()
    cfg_bad.embed_model = "olmayan-model-xyz"
    print("eksik model  :", await status(HybridRetriever(chunks, cfg_bad)))

    print("keyword modu :", await status(BM25Retriever(chunks, cfg_ok)))
    print("groundcheck  :", await status(GroundedRetriever(HybridRetriever(chunks, cfg_ok), cfg_ok)))


asyncio.run(main())
