import logging

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import User, UserRole
from app.security import hash_password

logger = logging.getLogger(__name__)


async def ensure_default_admin() -> None:
    settings = get_settings()
    async with SessionLocal() as db:
        existing = (
            await db.execute(select(User).where(User.email == settings.admin_email))
        ).scalar_one_or_none()
        if existing is not None:
            return
        admin = User(
            email=settings.admin_email,
            password_hash=hash_password(settings.admin_password),
            role=UserRole.admin,
        )
        db.add(admin)
        await db.commit()
        logger.warning(
            "Default admin created: %s — change the password immediately!",
            settings.admin_email,
        )
