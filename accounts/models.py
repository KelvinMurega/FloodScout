from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """The single USERS entity from the ERD (Chapter 4, Section 4.4.1).

    The Use Case Diagram (4.3.1) and Class Diagram (4.3.4) both describe two
    actors, System Administrator and End User, sharing one authentication
    base and distinguished only by role — this mirrors that directly rather
    than creating two separate user classes.
    """

    class Role(models.TextChoices):
        ADMINISTRATOR = "admin", "System Administrator"
        END_USER = "user", "End User"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.END_USER)

    def is_administrator(self) -> bool:
        # A Django superuser (`createsuperuser`) counts as well, so the first
        # account on a new install can administer without a role edit.
        return self.is_superuser or self.role == self.Role.ADMINISTRATOR

    def __str__(self) -> str:
        return self.username
