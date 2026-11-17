# Brand kits

A kit is a YAML file: a name, optionally a logo, a palette, fonts, a call to action, and
the language the narration is written in. Nothing is required.

Kits are looked for in `./brands/`, then your config directory, then the ones bundled here.
`creatoros doctor` prints which one is in use and where it came from.

The failure mode for a missing asset is a plainer video, never an exception. A kit naming a
logo that has moved renders the wordmark and says so; a kit naming a font that is not
installed falls back to the bundled one. A day's run is not lost because an image moved.

```yaml
name: My Channel
tagline: One idea a day.
logo: logo.png          # relative to this file
cta: Subscribe
palette:
  accent: "#FF5C00"     # see core/brand.py DEFAULT_PALETTE for every key
language:
  primary: es
```

`nexlore-ai.yaml` is a real channel's kit, kept as a worked example.
