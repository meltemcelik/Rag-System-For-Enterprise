from pydantic import BaseModel, Field


class RuntimeConfig(BaseModel):
    """Live, admin-editable generation settings held in app state."""

    model: str
    system_prompt: str
    temperature: float = Field(ge=0.0, le=2.0)


class ConfigUpdate(BaseModel):
    """Partial update from the admin panel; unset fields are left unchanged."""

    model: str | None = None
    system_prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class LoginRequest(BaseModel):
    email: str
    password: str


class NewUser(BaseModel):
    email: str
    password: str
    is_admin: bool = False
    role: str = "user"


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class RoleUpdate(BaseModel):
    role: str


class DocRoles(BaseModel):
    """Bir belgeyi görebilecek roller; boş liste = herkese açık."""

    roles: list[str] = Field(default_factory=list)


class Vote(BaseModel):
    vote: int = Field(ge=-1, le=1)  # 1 beğeni, -1 beğenmeme, 0 geri al
