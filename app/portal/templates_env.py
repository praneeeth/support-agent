"""One Jinja environment for both portals, so they share a shell and a vocabulary."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.portal import labels

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
labels.register(templates)
templates.env.globals["brand"] = get_settings().widget_brand
