"""Takip sorularini bagimsiz arama sorgusuna cevirir (query condensation).

Sorun: retrieval ham kullanici mesajiyla yapiliyordu. "Peki yurt disi icin?"
gibi bir takip sorusunda mesajin kendisi hicbir belgeyle eslesmez -> bos baglam
-> gereksiz red. Cozum: gecmisle birlikte tek, kendi basina anlasilir bir soru
uret ve ARAMAYI onunla yap (modele giden mesaj degismez).

Kisa devre: gecmis yoksa ya da mesaj zaten yeterince uzun/bagimsizsa LLM'e hic
gidilmez — gereksiz gecikme olmasin.
"""
from .ollama import OllamaError

MAX_HISTORY_TURNS = 4
_SHORT_ENOUGH_TO_BE_STANDALONE = 8  # kelime

_SYSTEM = (
    "Gorevin: kullanicinin son sorusunu, onceki konusmadaki KONUYU icine katarak "
    "tek basina anlasilir bir arama sorgusuna cevirmek. 'peki', 'ya', 'bu', 'onun' "
    "gibi baglaci ve zamirleri cikar; eksik konuyu konusmadan tamamla. SADECE "
    "sorguyu yaz; aciklama, tirnak veya on ek ekleme. /no_think"
)

# Kucuk modeller (llama3.2:3b) bu meta-gorevi ornek gormeden yapmiyor: soruyu
# aynen geri veriyorlar. rag.py'deki answerability kapisiyla ayni cozum — few-shot.
#
# DENENDI VE GERI ALINDI (9 Agustos 2026): ornekleri isim obegi yerine TAM SORU
# yapmak denendi. Gerekce saglamdi — tek bir vakada olculmustu ki soru formu
# embedding esigini geciyor, isim obegi gecmiyor:
#     "parola degistirme suresi"          -> kosinus 0.4583  -> REDDEDILDI
#     "Parola ne siklikla degistirilir?"  -> kosinus 0.5507  -> GECTI
# Ama 13 vakalik takip setinde OLCULDUGUNDE net ZARAR verdi:
#     isim obegi (bu surum) : 0.7692 isabet, 0 vaka bozuldu
#     tam soru              : 0.6923 isabet, 1 vaka bozuldu
# Hedeflenen vakayi duzeltirken baska iki vakayi bozuyor. Tek vakadan yola cikip
# genellemenin neden yanlis oldugunun ornegi; tekrar denenecekse ONCE
# `python eval/run.py --multiturn` ile olculmelidir.
_EXAMPLES = [
    (
        "Kullanici: Parola kurallari nedir?\nAsistan: En az 12 karakter ve buyuk/kucuk harf.",
        "peki degistirme suresi?",
        "parola degistirme suresi",
    ),
    (
        "Kullanici: Sehir ici ulasim masrafi nasil hesaplanir?\nAsistan: Kilometre basina 7 TL.",
        "yurt disi icin de ayni mi?",
        "yurt disi seyahat masraf limiti",
    ),
    (
        "Kullanici: Sirket izin politikasindan bahseder misin?\nAsistan: Yillik izin 20 is gunudur.",
        "bu dokumandaki onemli noktalar neler?",
        "sirket izin politikasi onemli noktalar",
    ),
]


def needs_condensing(history: list[dict], message: str) -> bool:
    if not history:
        return False
    return len(message.split()) < _SHORT_ENOUGH_TO_BE_STANDALONE


def _clean(raw: str, fallback: str) -> str:
    text = raw
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[-1]
    text = text.strip().strip('"').strip()
    first = text.split("\n", 1)[0].strip()
    # Model aciklama yapmaya kalkarsa (uzun cikti) orijinal soruya don.
    if not first or len(first.split()) > 40:
        return fallback
    return first


def _prompt(convo: str, message: str) -> str:
    return f"Konusma:\n{convo}\n\nSon soru: {message}\n\nArama sorgusu:"


async def condense(ollama, model: str, history: list[dict], message: str) -> str:
    """Arama icin kullanilacak sorgu. Hatada sessizce orijinal mesaja doner."""
    if not needs_condensing(history, message):
        return message
    turns = history[-MAX_HISTORY_TURNS * 2 :]
    convo = "\n".join(
        f"{'Kullanici' if m['role'] == 'user' else 'Asistan'}: {m['content']}" for m in turns
    )
    messages = [{"role": "system", "content": _SYSTEM}]
    for ex_convo, ex_question, ex_query in _EXAMPLES:
        messages.append({"role": "user", "content": _prompt(ex_convo, ex_question)})
        messages.append({"role": "assistant", "content": ex_query})
    messages.append({"role": "user", "content": _prompt(convo, message)})
    try:
        raw = await ollama.complete(model, messages, temperature=0.0)
    except OllamaError:
        return message
    return _clean(raw, message)
