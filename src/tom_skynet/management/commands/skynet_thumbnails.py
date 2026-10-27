from django.core.management.base import BaseCommand
from django.db.models import Q
from tom_dataproducts.models import DataProduct

from tom_skynet.facility import PRODUCT_PREFIX
from tom_skynet.thumbnails import save_thumbnail


class Command(BaseCommand):
    help = (
        "Draw the thumbnails of saved Skynet data products that have none "
        "(products saved before tom_skynet drew them)"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--redraw",
            action="store_true",
            help="Draw them again for every saved Skynet product",
        )

    def handle(self, *args, **options):
        products = DataProduct.objects.filter(
            product_id__startswith=PRODUCT_PREFIX
        ).exclude(data="")
        if not options["redraw"]:
            products = products.filter(Q(thumbnail="") | Q(thumbnail__isnull=True))
        drawn = failed = 0
        for data_product in products.order_by("pk"):
            if save_thumbnail(data_product):
                drawn += 1
            else:
                failed += 1
        self.stdout.write(f"Drew {drawn} thumbnails; {failed} couldn't be drawn")
