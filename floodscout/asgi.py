"""
ASGI config for the FloodScout project.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "floodscout.settings")

application = get_asgi_application()
