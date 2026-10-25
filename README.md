# tom_skynet

A [TOM Toolkit](https://tom-toolkit.readthedocs.io/) facility for the
[Skynet Robotic Telescope Network](https://skynetgo.org/), built on
[AEONlib](https://github.com/AEONplus/AEONlib).

It submits optical imaging requests sized by target SNR or by total exposure
time (Skynet picks the telescope), previews which telescopes can carry them
out, follows their status, cancels them, and saves Skynet's final science
files as data products. Each TOM user acts with their own Skynet personal API
token.

Work in progress: sidereal imaging only. See [What isn't supported](#what-isnt-supported).

## Install

`tom_skynet` needs Python 3.12 or 3.13 and TOM Toolkit 3.1. It isn't on PyPI
yet; install it from git, at a release tag:

```bash
pip install "tom-skynet @ git+https://github.com/SkynetRTN/tom_skynet@v0.2.0"
```

Add it to your TOM's `settings.py` and migrate:

```python
INSTALLED_APPS = [
    ...,
    "tom_skynet",
]

# Skynet products are saved as FITS files. TOMs made with tom_setup already
# have this type; without it products are saved with no type.
DATA_PRODUCT_TYPES = {
    ...,
    "fits_file": ("fits_file", "FITS File"),
}
```

```bash
python manage.py migrate
```

The app registers the Skynet facility, its URLs (`/skynet/`) and its card on
the user profile page itself; it doesn't go in `TOM_FACILITY_CLASSES` or
`urls.py`.

## Settings

```python
FACILITIES = {
    "SKYNET": {
        "api_root": "https://api.skynetgo.org/",
        "web_root": "https://skynetgo.org/",
        "allowed_telescope_prefix": "",
        "sites": {},
    },
}
```

| Key | Default | |
|---|---|---|
| `api_root` | `https://api.skynetgo.org/` | Skynet API |
| `web_root` | `https://skynetgo.org/` | Skynet website, for observation links |
| `allowed_telescope_prefix` | empty | Offer only time accounts whose telescopes all start with it (`sim-` keeps a test TOM on Skynet's simulators); empty offers every account |
| `sites` | none | Sites for TOM Toolkit's visibility plot, in its format: `{"Name": {"sitecode": ..., "latitude": ..., "longitude": ..., "elevation": ...}}`. Static, because the plot is drawn without a user |
| `api_token` | none | A token for users who have none of their own. Leave it unset when each user has a token |

## Skynet API tokens

Each TOM user needs an **unscoped** Skynet personal API token, minted in
Skynet under account settings, API tokens. The TOM stores it encrypted with
its `SECRET_KEY` (rotate the key with `SECRET_KEY_FALLBACKS`, or stored
tokens can't be read).

Users set their own on their profile page (the Skynet card, or
`/skynet/token/`); the TOM checks it with Skynet before storing it. Staff can
also set one with:

```bash
python manage.py skynet_set_token <username>            # prompts, without echo
echo "$TOKEN" | python manage.py skynet_set_token <username> --stdin
python manage.py skynet_set_token <username> --clear
```

or in Django admin, under Skynet profiles.

## Use

- **Request:** a target's Observe tab, Skynet. The time accounts are those
  the user can spend on Skynet. Choose target SNR (with the source's
  brightness: a reference AB magnitude, a power-law decay and spectral index,
  and extinction) or total exposure time, up to three filters, optional
  epochs repeated at a period, and the airmass and Moon constraints.
- **Validate** checks the request with Skynet, then previews the time
  account's telescopes: which can carry it out, which can observe it in the
  window, and Skynet's estimated exposure time. The estimate assumes a dark
  sky and isn't wall-clock time.
- **Submit** refuses a request that no telescope can carry out, or that none
  can observe in the window, and otherwise sends it. Skynet's refusal, if
  any, shows on the form.
- **Status:** `python manage.py updatestatus`, from cron (every 10 minutes is
  plenty). No `--username` is needed: each record is read with its owner's
  token. A record keeps being polled after the observation ends, until
  Skynet reports its products final, because frames are processed after they
  are taken.
- **Cancel:** the observation page's Cancel, by the user who submitted it (or
  staff). Frames already taken are kept, so a cancel can end as
  `PARTIALLY_COMPLETED`. TOM Toolkit shows Cancel only while a record is
  `PENDING`; to offer it until a Skynet record is terminal, add the package's
  template override to your settings (TOM Toolkit's cancel link is a plain
  GET, so as with any facility a page that loads it while the owner is logged
  in cancels the observation):

  ```python
  import tom_skynet

  TEMPLATES[0]["DIRS"].append(tom_skynet.TEMPLATE_OVERRIDES)
  ```
- **Data:** polling saves each final science file as a `fits_file` data
  product, with the id `skynet:<file id>` and Skynet's metadata (telescope,
  filter, exposure, time, airmass, pixel scale) in `extra_data`. A frame that
  Skynet processes again becomes a second product. The observation page lists
  files not saved yet, with links.

## What isn't supported

- **TOM Toolkit's REST API and cadence strategies** build the request form
  without a TOM user, so there is no token to use; they are refused with a
  message. Use Skynet's own epochs for repeated observations. The REST cancel
  (`PATCH /api/observations/<pk>/cancel/`) is refused too: it doesn't say who
  is cancelling, and the cancel would go out with the owner's token.
- Non-sidereal targets, and choosing a telescope or site.
- A reopened Skynet observation: once a record is terminal, the TOM stops
  following it.
- Thumbnails: TOM Toolkit only makes them for FITS files with a `SCI`
  extension, and Skynet's images are tile-compressed extensions with no
  `EXTNAME`.

## Development

```bash
uv sync
uv run pytest
uvx ruff check && uvx ruff format --check
uvx ty check
```

Run the tests on Python 3.12 and 3.13 (`uv run --python 3.12 pytest`). They
use recorded Skynet responses in `tests/data/` (see `tests/data/SOURCE`) and
make no network calls.
