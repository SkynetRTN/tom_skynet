import sys
from getpass import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tom_skynet.models import SkynetProfile


class Command(BaseCommand):
    help = "Set or clear a TOM user's Skynet API token (stored encrypted)"

    def add_arguments(self, parser):
        parser.add_argument("username")
        source = parser.add_mutually_exclusive_group()
        source.add_argument(
            "--stdin", action="store_true", help="Read the token from standard input"
        )
        source.add_argument(
            "--clear", action="store_true", help="Remove the stored token"
        )

    def handle(self, *args, **options):
        user_model = get_user_model()
        try:
            user = user_model.objects.get(username=options["username"])
        except user_model.DoesNotExist as exc:
            raise CommandError(f"No TOM user {options['username']!r}") from exc

        profile, _ = SkynetProfile.objects.get_or_create(user=user)
        if options["clear"]:
            profile.api_token = None
            profile.save()
            self.stdout.write(f"Cleared the Skynet API token of {user}")
            return

        if options["stdin"]:
            token = sys.stdin.readline().strip()
        else:
            token = getpass("Skynet API token: ").strip()
        if not token:
            raise CommandError("The token is empty")
        profile.api_token = token
        profile.save()
        self.stdout.write(f"Stored a Skynet API token for {user}")
