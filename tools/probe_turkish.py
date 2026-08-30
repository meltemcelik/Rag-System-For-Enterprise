
# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import os, sys, tempfile
from pathlib import Path
T = Path(tempfile.mkdtemp()); (T / "docs").mkdir()
os.environ["RAG_DOCS_DIR"] = str(T / "docs")
sys.path.insert(0, _REPO_ROOT)
from app import docs

for name in ["şirket_izin_politikası.md", "Çalışan Rehberi.pdf", "İnsan Kaynakları.docx",
             "ödeme-günü.md", "rapor (güncel).pdf", "normal_file.md"]:
    try:
        print(f"{name!r:40} -> {docs.safe_name(name)!r}")
    except ValueError as e:
        print(f"{name!r:40} -> RED: {e}")
