"""
Thumbnails for Skynet's frames.

TOM Toolkit only draws a data product's thumbnail when one of the file's HDUs
has EXTNAME 'SCI' (tom_dataproducts.models.is_fits_image_file). Skynet's
frames are tile-compressed, with the image in a CompImageHDU and no EXTNAME,
so TOM Toolkit never draws one. fits2image, which TOM Toolkit draws with,
reads the first 2-D HDU and handles them, so this draws it the same way,
without that check. DataProduct.get_preview then finds it and returns its URL.
"""

import logging
import os
import tempfile

from django.core.files import File
from django.core.files.base import ContentFile
from fits2image.conversions import fits_to_jpg
from tom_dataproducts.models import THUMBNAIL_DEFAULT_SIZE, DataProduct

logger = logging.getLogger(__name__)


def save_thumbnail(data_product: DataProduct, content: bytes | None = None) -> bool:
    """
    Draw and save the thumbnail of a FITS product, at TOM Toolkit's default
    size. Pass the file's bytes when they are at hand, to save reading the file
    back from storage. False, with a warning, when it can't be drawn; the
    product itself is left as it was.
    """
    source = ContentFile(content) if content is not None else data_product.data
    width, height = THUMBNAIL_DEFAULT_SIZE
    with tempfile.NamedTemporaryFile(suffix=".jpg") as jpg:
        try:
            drawn = fits_to_jpg(source, jpg.name, width=width, height=height)
        # fits2image raises a bare Exception when it finds no image, and astropy
        # whatever a corrupt file makes it raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("No thumbnail for %s: %s", data_product, exc)
            return False
        if not drawn:
            logger.warning("No thumbnail for %s", data_product)
            return False
        # TOM Toolkit's name for it: the file's name up to its first dot, then _tb
        name = os.path.basename(data_product.data.name).split(".")[0] + "_tb.jpg"
        jpg.seek(0)
        if data_product.thumbnail:
            # A redraw replaces it rather than leaving the old file behind
            data_product.thumbnail.delete(save=False)
        data_product.thumbnail.save(name, File(jpg), save=True)
    return True
