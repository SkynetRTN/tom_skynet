# tom_skynet

A [TOM Toolkit](https://tom-toolkit.readthedocs.io/) facility for the
[Skynet Robotic Telescope Network](https://skynetgo.org/), built on
[AEONlib](https://github.com/AEONplus/AEONlib).

It submits optical imaging requests sized by target SNR (Skynet picks the
telescope), follows their status, and saves Skynet's final science files as
data products. Each TOM user acts with their own Skynet personal API token.

Work in progress: sidereal imaging only, and no cancel yet.

## Install

`tom_skynet` needs Python 3.12 or 3.13 and TOM Toolkit 3.1. It isn't on PyPI
yet; install it from git, pinned to a commit:

```bash
pip install "tom-skynet @ git+https://github.com/SkynetRTN/tom_skynet@<commit>"
```

Add it to your TOM's `settings.py` and migrate:

```python
INSTALLED_APPS = [
    ...,
    "tom_skynet",
]
```

```bash
python manage.py migrate
```

The app registers the Skynet facility itself; it doesn't go in
`TOM_FACILITY_CLASSES`.

## Settings

```python
FACILITIES = {
    "SKYNET": {
        "api_root": "https://api.skynetgo.org/",
        "web_root": "https://skynetgo.org/",
        "allowed_telescope_prefix": "",
    },
}
```

| Key | Default | |
|---|---|---|
| `api_root` | `https://api.skynetgo.org/` | Skynet API |
| `web_root` | `https://skynetgo.org/` | Skynet website, for observation links |
| `allowed_telescope_prefix` | empty | Offer only time accounts whose telescopes all start with it (`sim-` keeps a test TOM on Skynet's simulators); empty offers every account |
| `api_token` | none | A token for users who have none of their own. Leave it unset when each user has a token |

## Skynet API tokens

Each TOM user needs an **unscoped** Skynet personal API token, minted in
Skynet under account settings, API tokens. The TOM stores it encrypted with
its `SECRET_KEY`. Set it with:

```bash
python manage.py skynet_set_token <username>            # prompts, without echo
echo "$TOKEN" | python manage.py skynet_set_token <username> --stdin
python manage.py skynet_set_token <username> --clear
```

or in Django admin, under Skynet profiles.

## Use

- **Submit:** a target's Observe tab, Skynet. The time accounts are those the
  user can spend on Skynet. The form checks the filters and target SNR
  against the chosen account. Validate checks without submitting; Submit
  sends the request, and Skynet's refusal, if any, shows on the form.
- **Status:** `python manage.py updatestatus`, usually from cron. No
  `--username` is needed: each record is read with its owner's token. A
  record keeps being polled after the observation ends, until Skynet reports
  its products final, because frames are processed after they are taken.
- **Data:** polling saves each final science file as a data product, with
  the id `skynet:<file id>`. A frame that Skynet processes again becomes a
  second product. The observation page lists files not saved yet.

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
