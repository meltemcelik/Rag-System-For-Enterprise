import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth, docs, query, security, store
from .config import settings
from .ollama import OllamaClient, OllamaError
from .rag import build_rag_messages, get_retriever, guard_reply, parse_sources, status as rag_status
from .schemas import (
    ConfigUpdate,
    DocRoles,
    LoginRequest,
    NewUser,
    PasswordChange,
    RoleUpdate,
    RuntimeConfig,
    Vote,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.init_db()
    store.init_db()
    app.state.config = RuntimeConfig(
        model=settings.default_model,
        system_prompt=settings.system_prompt,
        temperature=settings.temperature,
    )
    app.state.ollama = OllamaClient(settings.ollama_base_url)
    app.state.retriever = get_retriever()
    yield


app = FastAPI(title="RAG Backend", lifespan=lifespan)


# --- Pages -----------------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin")
async def admin():
    return FileResponse(STATIC_DIR / "admin.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- Auth ------------------------------------------------------------------
@app.post("/api/login")
async def login(body: LoginRequest, request: Request, response: Response):
    client = request.client.host if request.client else "?"
    key = f"{client}:{body.email.strip().lower()}"
    if auth.rate_limited(key):
        raise HTTPException(status_code=429, detail="çok fazla deneme, birkaç dakika sonra tekrar deneyin")
    user = auth.authenticate(body.email, body.password)
    if not user:
        auth.record_attempt(key)
        raise HTTPException(status_code=401, detail="e-posta veya şifre hatalı")
    auth.clear_attempts(key)
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.create_token(user["email"]),
        httponly=True,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
    )
    return {"email": user["email"], "is_admin": bool(user["is_admin"])}


@app.post("/api/logout")
async def logout(response: Response, session: str | None = Cookie(default=None)):
    """Çerezi silmek yetmez: kopyalanmış bir çerez süresi dolana kadar geçerli
    kalıyordu. Yalnızca BU oturum iptal edilir; diğer cihazlar etkilenmez."""
    auth.revoke_token(session)
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


@app.get("/api/me")
async def me(user: dict = Depends(auth.current_user)):
    return {
        "email": user["email"],
        "is_admin": bool(user["is_admin"]),
        "role": user.get("role") or auth.DEFAULT_ROLE,
    }


@app.post("/api/password")
async def change_password(
    body: PasswordChange, response: Response, user: dict = Depends(auth.current_user)
):
    if not auth.authenticate(user["email"], body.current_password):
        raise HTTPException(status_code=403, detail="mevcut şifre hatalı")
    try:
        auth.set_password(user["email"], body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Şifre değişince başka cihazlardaki oturumlar düşmeli; bu oturum devam etsin
    # diye taze bir çerez veriyoruz.
    auth.revoke_sessions(user["email"])
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.create_token(user["email"]),
        httponly=True,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
    )
    return {"ok": True}


# --- Status ----------------------------------------------------------------
@app.get("/api/health")
async def health():
    """RAG durumu her zaman döner; `rag.degraded=true` ise anlamsal arama kapalı
    (embedding modeli eksik) ve sistem sessizce yalnızca BM25 ile çalışıyordur.

    Model listesi TEK sefer çekilip RAG durumuna da verilir — ayrıca sorulsaydı
    Ollama'ya ikinci bir tur atılır ve ucun süresi ikiye katlanırdı."""
    try:
        models = await app.state.ollama.list_models()
    except OllamaError as exc:
        return {"ok": False, "error": str(exc),
                "rag": await rag_status(app.state.retriever)}
    return {
        "ok": True,
        "models": models,
        "rag": await rag_status(app.state.retriever, models),
    }


# --- Admin: kullanıcılar (admin yetkisi gerekli) ---------------------------
@app.get("/api/admin/users")
async def get_users(_: dict = Depends(auth.admin_required)):
    return {"users": auth.list_users()}


@app.post("/api/admin/users", status_code=201)
async def add_user(body: NewUser, _: dict = Depends(auth.admin_required)):
    try:
        auth.create_user(body.email, body.password, body.is_admin, body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@app.put("/api/admin/users/{email}/role")
async def update_role(email: str, body: RoleUpdate, _: dict = Depends(auth.admin_required)):
    if not auth.get_user(email):
        raise HTTPException(status_code=404, detail="kullanıcı yok")
    auth.set_role(email, body.role)
    return {"ok": True}


@app.delete("/api/admin/users/{email}")
async def remove_user(email: str, admin_: dict = Depends(auth.admin_required)):
    if email.strip().lower() == admin_["email"]:
        raise HTTPException(status_code=400, detail="kendini silemezsin")
    auth.delete_user(email)
    return {"ok": True}


# --- Admin: üretim ayarları (admin yetkisi gerekli) ------------------------
@app.get("/api/admin/config", response_model=RuntimeConfig)
async def get_config(_: dict = Depends(auth.admin_required)):
    return app.state.config


@app.put("/api/admin/config", response_model=RuntimeConfig)
async def update_config(update: ConfigUpdate, _: dict = Depends(auth.admin_required)):
    data = app.state.config.model_dump()
    data.update(update.model_dump(exclude_none=True))
    app.state.config = RuntimeConfig(**data)
    return app.state.config


@app.get("/api/admin/models")
async def models(_: dict = Depends(auth.admin_required)):
    try:
        return {"models": await app.state.ollama.list_models_info()}
    except OllamaError as exc:
        return {"models": [], "error": str(exc)}


# --- Dokümanlar (tüm giriş yapmış kullanıcılar için) ----------------------
@app.get("/api/docs")
async def get_accessible_docs(user: dict = Depends(auth.login_required)):
    return {"docs": docs.visible_docs(user)}


# --- Admin: doküman kütüphanesi --------------------------------------------
@app.get("/api/admin/docs")
async def get_docs(_: dict = Depends(auth.admin_required)):
    return {"docs": docs.list_docs()}


@app.post("/api/admin/docs", status_code=201)
async def upload_doc(file: UploadFile = File(...), _: dict = Depends(auth.admin_required)):
    try:
        name, replaced = docs.save_doc(file.filename or "", await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "name": name, "replaced": replaced, "reindex_required": True}


@app.delete("/api/admin/docs/{name:path}")
async def remove_doc(name: str, _: dict = Depends(auth.admin_required)):
    if not docs.delete_doc(name):
        raise HTTPException(status_code=404, detail="belge yok")
    return {"ok": True, "reindex_required": True}


@app.put("/api/admin/docs/{name:path}/roles")
async def set_doc_roles(name: str, body: DocRoles, _: dict = Depends(auth.admin_required)):
    if name not in {d["name"] for d in docs.list_docs()}:
        raise HTTPException(status_code=404, detail="belge yok")
    docs.set_roles(name, body.roles)
    return {"ok": True}


@app.post("/api/admin/reindex")
async def reindex(_: dict = Depends(auth.admin_required)):
    """Belgeleri yeniden indeksler. Embedding cache parça bazlı olduğu için
    yalnızca yeni/değişmiş parçalar yeniden embed'lenir."""
    app.state.retriever = await asyncio.to_thread(get_retriever)
    return {"ok": True, "rag": await rag_status(app.state.retriever)}


# --- Admin: LLMOps Analitik & Raporlama -----------------------------------
@app.get("/api/admin/analytics")
async def get_analytics(_: dict = Depends(auth.admin_required)):
    return store.get_analytics_summary()


@app.get("/api/admin/analytics/export")
async def export_analytics(_: dict = Depends(auth.admin_required)):
    csv_content = store.export_analytics_csv()
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=rag_analitik_raporu.csv"},
    )


# --- Admin: Güvenlik & Denetim İzi ---------------------------------------
@app.get("/api/admin/audit")
async def get_audit_logs(limit: int = 50, offset: int = 0, _: dict = Depends(auth.admin_required)):
    return {
        "logs": store.list_audit_logs(limit=limit, offset=offset),
        "stats": store.count_audit_logs(),
    }


# --- Sohbet geçmişi --------------------------------------------------------
@app.get("/api/conversations")
async def get_conversations(
    limit: int = 50, offset: int = 0, user: dict = Depends(auth.current_user)
):
    limit = max(1, min(limit, 200))
    return {
        "conversations": store.list_conversations(user["email"], limit, offset),
        "total": store.count_conversations(user["email"]),
    }


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: int, user: dict = Depends(auth.current_user)):
    messages = store.get_messages(conv_id, user["email"])
    if messages is None:
        raise HTTPException(status_code=404, detail="konuşma yok")
    return {"id": conv_id, "messages": messages}


@app.delete("/api/conversations/{conv_id}")
async def remove_conversation(conv_id: int, user: dict = Depends(auth.current_user)):
    if not store.delete_conversation(conv_id, user["email"]):
        raise HTTPException(status_code=404, detail="konuşma yok")
    return {"ok": True}


@app.post("/api/messages/{message_id}/vote")
async def vote_message(message_id: int, body: Vote, user: dict = Depends(auth.current_user)):
    if not store.set_vote(message_id, user["email"], body.vote):
        raise HTTPException(status_code=404, detail="mesaj yok")
    return {"ok": True}


@app.get("/api/admin/feedback")
async def get_feedback(_: dict = Depends(auth.admin_required)):
    """Altın sete aday toplamak için: beğenilmeyen (ve beğenilen) soru/cevaplar."""
    return {"down": store.voted_messages(-1), "up": store.voted_messages(1)}


# --- Chat ------------------------------------------------------------------
# Prompt kurulumu app/rag.py:build_rag_messages icinde — uretim ve olcum
# betikleri (eval/*) ayni fonksiyonu cagirsin diye. Iki yerde ayri durursa
# olctugumuz prompt kullanicinin gordugu prompt olmaktan cikar.
# Gecmis turu siniri da orada: MAX_HISTORY_TURNS.


def _as_int(value) -> int | None:
    """İstemciden gelen kimliği güvenle sayıya çevir; çöp veri bağlantıyı düşürmesin."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    user = auth.user_for_token(ws.cookies.get(auth.COOKIE_NAME))
    if not user:
        await ws.close(code=1008)  # policy violation: giriş yok
        return
    await ws.accept()

    history: list[dict] = []
    conv_id: int | None = None
    try:
        while True:
            payload = await ws.receive_json()
            user_message = (payload.get("message") or "").strip()
            if not user_message:
                continue

            cfg = app.state.config
            # Konuşma seçimi HER mesajda değerlendirilir: kullanıcı "Geçmiş"ten
            # başka bir konuşma açtığında aynı bağlantı üzerinden devam edebilir.
            # (Yalnızca ilk mesajda bakılsaydı, sonraki mesajlar sessizce önceki
            #  konuşmaya yazılırdı — kullanıcı ekranda doğru yerde görürdü ama
            #  kayıt yanlış konuşmaya giderdi.)
            requested = _as_int(payload.get("conversation_id"))
            if requested is not None and requested != conv_id:
                previous = store.get_messages(requested, user["email"])
                if previous is not None:  # None = yok ya da başkasının
                    conv_id = requested
                    history = [{"role": m["role"], "content": m["content"]} for m in previous]
                    await ws.send_json({"type": "conversation", "id": conv_id})
            if conv_id is None:
                conv_id = store.create_conversation(user["email"], user_message)
                await ws.send_json({"type": "conversation", "id": conv_id})
            store.add_message(conv_id, "user", user_message)

            # PII (Kişisel Veri) Maskeleme (TC No, IBAN, Kredi Kartı, Telefon, E-posta)
            sanitized_message, redactions = security.sanitize_pii(user_message)
            pii_types = [r["type"] for r in redactions]

            doc_filter = payload.get("doc_filter")
            if isinstance(doc_filter, str):
                doc_filter = doc_filter.strip() or None
            else:
                doc_filter = None

            # Takip sorusunu bağımsız arama sorgusuna çevir; modele giden mesaj
            # değişmez, yalnızca retrieval bu sorguyla yapılır.
            search_query = await query.condense(
                app.state.ollama, cfg.model, history, sanitized_message
            )
            context = await app.state.retriever.retrieve(search_query, doc_filter=doc_filter)
            context = docs.filter_context(context, user)  # rol bazlı yetki
            sources = parse_sources(context)

            # Güvenlik ve erişim denetim günlüğü (Audit Log)
            store.add_audit_log(
                email=user["email"],
                conv_id=conv_id,
                query_text=sanitized_message,
                pii_types=pii_types,
                sources=[s["source"] for s in sources],
            )

            # Guardrail: ilgili baglam yoksa modeli cagirmadan reddet (halusinasyon onleme).
            refusal = guard_reply(app.state.retriever, context)
            if refusal:
                msg_id = store.add_message(conv_id, "assistant", refusal)
                await ws.send_json({"type": "token", "content": refusal})
                await ws.send_json({
                    "type": "done",
                    "message_id": msg_id,
                    "sources": [],
                    "pii_redacted": bool(redactions),
                    "pii_types": pii_types,
                })
                history.append({"role": "user", "content": sanitized_message})
                history.append({"role": "assistant", "content": refusal})
                continue

            messages = build_rag_messages(
                sanitized_message, context, history, system_prompt=cfg.system_prompt
            )

            reply: list[str] = []
            try:
                async for token in app.state.ollama.stream_chat(
                    cfg.model, messages, cfg.temperature
                ):
                    reply.append(token)
                    await ws.send_json({"type": "token", "content": token})
            except OllamaError as exc:
                await ws.send_json({"type": "error", "content": str(exc)})
                continue

            answer = "".join(reply)
            msg_id = store.add_message(conv_id, "assistant", answer, sources)
            await ws.send_json({
                "type": "done",
                "message_id": msg_id,
                "sources": sources,
                "pii_redacted": bool(redactions),
                "pii_types": pii_types,
            })
            history.append({"role": "user", "content": sanitized_message})
            history.append({"role": "assistant", "content": answer})
    except WebSocketDisconnect:
        return