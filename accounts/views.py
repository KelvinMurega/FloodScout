from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView


class ObtainTokenView(ObtainAuthToken):
    """POST a username and password, get back a token and the user's role.

    The role is returned so the dashboard knows whether to offer the
    administrator's actions. It is a convenience only: every protected
    endpoint checks the role itself (accounts/permissions.py).
    """

    # Slows password guessing; the rate is DEFAULT_THROTTLE_RATES["login"].
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _created = Token.objects.get_or_create(user=user)
        return Response(
            {
                "token": token.key,
                "username": user.get_username(),
                "role": user.role,
                "is_administrator": user.is_administrator(),
            }
        )


class RevokeTokenView(APIView):
    """POST with a token to delete it, so signing out ends it on the server too."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
