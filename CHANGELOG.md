# Changelog

## Unreleased

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
