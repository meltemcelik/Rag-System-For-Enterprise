"""RAG retriever hizli testi (sunucusuz).

Kullanim:
    py test_rag.py                       # varsayilan sorular
    py test_rag.py "kendi sorun"         # tek soru
    py test_rag.py "soru" --full         # parcalarin TAM metnini goster

Backend secmek icin:
    RAG_MODE=keyword py test_rag.py      # BM25 (Ollama gerekmez)
    RAG_MODE=embed   py test_rag.py      # Ollama embeddings
"""
import asyncio
import sys

from app.rag import get_retriever, guard_reply

DEFAULT_QUERIES = [
    "Yillik izin kac gun ve devredilen izin ne zamana kadar kullanilir?",
    "Sehir disinda yemek icin gunluk harcama limiti nedir?",
    "Parola kurallari ve 2FA zorunlu mu?",
    "Kirmizi pandalar ne yer?",  # alakasiz -> guardrail devreye girmeli
]

# Ekranda gosterilecek onizleme uzunlugu. --full verilince sinir kalkar.
PREVIEW = 2000


async def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--full"]
    full = "--full" in sys.argv
    queries = args or DEFAULT_QUERIES

    retriever = get_retriever()
    print(f"retriever = {type(retriever).__name__}\n")

    for q in queries:
        chunks = await retriever.retrieve(q)
        print("=" * 70)
        print(f"SORU: {q}")

        refusal = guard_reply(retriever, chunks)
        if refusal:
            print(f"-> GUARDRAIL: {refusal}  (model cagrilmaz)\n")
            continue

        print(f"-> {len(chunks)} parca dondu")
        for i, ch in enumerate(chunks, 1):
            text = ch if full else ch[:PREVIEW]
            suffix = "" if (full or len(ch) <= PREVIEW) else " …"
            print(f"\n  [{i}] {text}{suffix}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
