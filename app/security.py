"""Kurumsal Guvenlik ve PII (Kisisel Veri) Maskeleme Modulu.

Kullanici sorularinda ve baglam metinlerinde yer alan hassas verileri
(TC Kimlik No, IBAN, Kredi Karti, Telefon, E-posta) tespit eder ve semantik
etiketlerle maskeler. Boylece dil modeli sorunun amacini anlar ancak
gercek kisisel veriler modele sizdirilmaz.
"""
import re


# --- Duzenli Ifadeler (Regex Kurallari) ------------------------------------

# TC Kimlik No: 11 basamakli, ilk hanesi 0 olmayan sayilar
_TC_RE = re.compile(r"\b([1-9]\d{8})(\d{2})\b")

# TR IBAN: TR ile baslayan 24 haneli hesap numaralari (bosluklu/bosluksuz)
_IBAN_RE = re.compile(r"\b(TR\s*(?:\d\s*){22})(\d{2})\b", re.IGNORECASE)

# Kredi Karti: 16 basamakli sayilar (4x4 bloklar halinde veya bitisik)
_CC_RE = re.compile(r"\b((?:\d{4}[ -]?){3})(\d{4})\b")

# Telefon No: Turkiye 05xx veya 5xx ile baslayan 10-11 haneli numaralar
_PHONE_RE = re.compile(r"\b((?:0\s?5\d{2}|5\d{2})[ -]?\d{3}[ -]?\d{2}[ -]?)(\d{2})\b")

# E-posta Adresi
_EMAIL_RE = re.compile(r"\b([a-zA-Z0-9._%+-])[a-zA-Z0-9._%+-]*@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b")


def sanitize_pii(text: str) -> tuple[str, list[dict]]:
    """Metin icindeki hassas kisisel verileri maskeler.

    Returns:
        (sanitized_text, list_of_redactions)
    """
    if not text or not isinstance(text, str):
        return text, []

    redactions: list[dict] = []

    # 1. IBAN Maskeleme
    def _mask_iban(match):
        raw = match.group(0)
        clean = re.sub(r"\s+", "", raw)
        masked = f"[IBAN: TR********************{clean[-2:]}]"
        redactions.append({
            "type": "IBAN",
            "preview": raw[:6] + "...",
            "masked": masked,
        })
        return masked

    text = _IBAN_RE.sub(_mask_iban, text)

    # 2. Kredi Karti Maskeleme (16 hane)
    def _mask_cc(match):
        raw = match.group(0)
        clean = re.sub(r"[ -]", "", raw)
        masked = f"[KREDİ_KARTI: ****-****-****-{clean[-4:]}]"
        redactions.append({
            "type": "KREDİ_KARTI",
            "preview": "****" + clean[-4:],
            "masked": masked,
        })
        return masked

    text = _CC_RE.sub(_mask_cc, text)

    # 3. TC Kimlik No Maskeleme (11 hane)
    def _mask_tc(match):
        raw = match.group(0)
        masked = f"[TC_NO: *******{raw[-2:]}]"
        redactions.append({
            "type": "TC_KİMLİK_NO",
            "preview": "*******" + raw[-2:],
            "masked": masked,
        })
        return masked

    text = _TC_RE.sub(_mask_tc, text)

    # 4. Telefon Numarasi Maskeleme
    def _mask_phone(match):
        raw = match.group(0)
        clean = re.sub(r"[ -]", "", raw)
        masked = f"[TELEFON: 05**-***-**{clean[-2:]}]"
        redactions.append({
            "type": "TELEFON",
            "preview": "05**..." + clean[-2:],
            "masked": masked,
        })
        return masked

    text = _PHONE_RE.sub(_mask_phone, text)

    # 5. E-posta Maskeleme
    def _mask_email(match):
        first_char = match.group(1)
        domain = match.group(2)
        raw = match.group(0)
        masked = f"[E_POSTA: {first_char}***@{domain}]"
        redactions.append({
            "type": "E_POSTA",
            "preview": masked,
            "masked": masked,
        })
        return masked

    text = _EMAIL_RE.sub(_mask_email, text)

    return text, redactions
