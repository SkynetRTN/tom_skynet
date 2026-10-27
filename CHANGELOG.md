# Changelog

## 0.3.0 (2026-10-26)

- Time accounts by ownership: the form and profile card offer the accounts the
  user owns and organizations' accounts, labelled with their owner, and never
  another user's personal account. The record's parameters name the owner.
- Removed `allowed_telescope_prefix`: which telescopes an account reaches is
  Skynet's to decide.
- A record whose observation its owner can no longer read (a 404) stops being
  polled, and its page says so (migration `0003`).
- Needs AEONlib's `SkynetFacility.me()` and `InstrumentPools.owner()`.

## 0.2.1 (2026-10-26)

- Thumbnails for Skynet's frames, drawn when a product is saved: TOM Toolkit
  draws none for tile-compressed images with no `SCI` extension. The
  `skynet_thumbnails` command draws them for products saved before.

## 0.2.0 (2026-10-25)

- Cancel an observation from the record page, by the user who submitted it or
  staff; an opt-in template (`tom_skynet.TEMPLATE_OVERRIDES`) offers Cancel
  until a Skynet record is terminal.
- Request by total exposure time as well as target SNR; extinction, Moon
  separation, and epochs repeated at a period.
- Validate checks the request with Skynet and previews the telescopes: which
  can carry it out, which can observe it in the window, and the estimated
  exposure time. Submit refuses a request no telescope can carry out or
  observe in its window.
- Requests without a TOM user (the REST API, cadence strategies) are refused
  with a message, and so is the REST cancel.
- Static observing sites for the visibility plot (`FACILITIES["SKYNET"]["sites"]`).
- Data products are saved as `fits_file` with Skynet's metadata in
  `extra_data`; unsaved products link to their files; a poll reads Skynet's
  progress once.
- A profile card and page for each user's own Skynet API token, checked with
  Skynet before it's stored.

## 0.1.0

- Request form, submit, status polling with the record owner's token, data
  products and observation links (proof of concept).
