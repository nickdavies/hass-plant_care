# plant_care

A Home Assistant component for running a plant-care operation: moisture
monitoring, watering detection, grow light scheduling, daily light integral as a
service objective, recurring care tasks, one consolidated feed of everything
outstanding, routed and tabbed per owner.

**Status: feature complete, not yet deployed.** Everything described here works
and is tested. None of it has run against real hardware, and every tolerance in
it is a guess until a fortnight of data says otherwise.

## Why this exists

Every other plant integration is config-flow only, so its configuration lives in
`.storage` rather than git. This one takes its config from YAML, via
`CONFIG_SCHEMA` — HA-native, no config flow, fully version controlled.

The config is expressive and tightly validated. Three lookup tables — probe
models, care tasks, DLI categories — mean a fact is written once and referenced
by name; what a plant gets is derived from which fields it has, never from a
feature flag. Every check, cross-references included, runs inside
`CONFIG_SCHEMA`, so `check_config` refuses a bad config before Home Assistant
starts with it — and a CI job running `check_config` catches it on a pull
request.

The config carries **facts** — entity ids, probe hardware characteristics,
measured calibration endpoints. Every **decision** — thresholds, smoothing
windows, staleness rules, defaults — lives in `model/policy.py`.

```yaml
plant_care:
  groups: !include ../../shared/groups.yaml   # shared with light_motion_profiles
  owners:                                     # a `groups` key is a group, else a person
    nick:
      action: notify.nick
      icon: mdi:human-male                    # their tab's icon; people only
    britta:
      action: notify.britta                   # no icon, so mdi:account
    primary:
      action: notify.phones
  system_notify: notify.phones                # faults belonging to no plant
  probe_models:
    thirdreality_soil_gen2: { heartbeat_minutes: 10, deadband_pp: 1.0 }
  care_tasks:
    water: { display: Water, icon: mdi:watering-can, detected_by: calibrated_moisture }
    feed:  { display: Feed,  icon: mdi:nutrition }
  dli_categories:
    foliage_tropical: { low: 4, high: 9 }
  lights:
    - name: study_shelf
      switch: switch.nick_study_outlet_sansi_100w_lamp
      lux_to_ppfd: 0.0125
      window:
        awake_aware:
          presence: sensor.person_presence_nick
          on_if_awake_after: "06:00"
          on_after: "09:00"
          on_even_if_asleep_until: "17:00"
          on_until: "19:00"
  lux_sensors:
    - name: study_shelf
      entities: [sensor.esphome_study_lux_1, sensor.esphome_study_lux_2]
  plants:
    - name: passionfruit
      species: passiflora edulis
      owner: nick
      moisture:
        model: thirdreality_soil_gen2
        entities:
          moisture: sensor.roam_sensor_moisture_1_soil_moisture
          temperature: sensor.roam_sensor_moisture_1_temperature
          battery: sensor.roam_sensor_moisture_1_battery
        calibration: { field_capacity: 79.54, dry_point: 53.18, fc_tolerance: 8 }
      care:
        - { task: feed, every_days: 14 }
    - name: monstera
      owner: primary
      moisture:
        model: thirdreality_soil_gen2
        entities: { moisture: sensor.nick_study_sensor_monstera_window_soil_moisture }
        calibration: calibrating
      lights: [study_shelf]
      lux: study_shelf
      dli: { category: foliage_tropical }
    - name: front_step_pot
      owner: britta
      care:
        - { task: water, every_days: 4 }
```

Entity ids are written explicitly rather than derived from a stem and a suffix
convention: every string that names something outside the file is a real Home
Assistant entity, whichever integration created it.

## Layout

```
custom_components/plant_care/
├── model/              pure Python, zero Home Assistant imports
│   ├── plant.py          the domain types
│   ├── light.py          windows, presence, lux→PPFD, on-time bounds
│   ├── dli.py            bands, error budget, burn rates, the integrator
│   ├── signals.py        smoothing, watering detection, moisture health
│   ├── policy.py         the decisions, with their reasoning
│   ├── owners.py         who a plant belongs to, and whose phone that is
│   ├── config.py         the schema, the tables, cross-reference resolution
│   ├── markdown.py       the outstanding feed, rendered for a card
│   └── naming.py         every entity id, in one place
├── moisture.py         one coordinator per probe
├── light_control.py    one controller per grow light
├── dli.py              one coordinator per measured plant
├── feed.py             what "outstanding" means, in one place
├── notifier.py         pushes each new feed item once, to its owner's action
├── store.py            durable events, flags, killswitch stamps, DLI history
├── lovelace.py         dashboard-from-code, vendored from light_motion_profiles
└── sensor.py, binary_sensor.py, button.py, switch.py, dashboards/
```

**`model/` imports no Home Assistant on purpose.** Its tests need no framework
mocking, and its type hints are real — HA's own typing is loose enough that
letting it leak into the domain logic would erode both. The HA edge is confined
to the platform modules.

This diverges from the sibling `light_motion_profiles` component, which mocks
the entire `homeassistant` package in `tests/conftest.py` to achieve the same
thing. Keeping the imports out in the first place seemed better than mocking
them away.

## Tests

```bash
python -m pytest tests/ -c tests/pytest.ini          # no HA needed
python -m pytest tests_integration/                  # needs pytest-homeassistant-custom-component
```

## Design notes worth knowing

**Absence of a calibration means "calibrating", never a default.** A plant
without both measured endpoints can be monitored but not judged. `Calibrating`
is an explicit type rather than `None`, because `None` is falsy and the first
`if plant.moisture.calibration:` anyone writes would silently treat a
calibrating plant as one with no probe at all.

**A care task never marked done is not overdue.** Adding a plant should not
produce an instant backlog; the clock starts at the first mark-done. `None`
days-since is a real value, not a missing one.

**No "I did a thing" button for anything auto-detectable.** A moisture probe
sees a watering by anyone, with no phone nearby, so a watering button would be a
worse second source of truth. Parsing enforces this by refusing to schedule a
task marked `detected_by: calibrated_moisture` on a plant that can detect it;
buttons exist only for feeding, pest checks and the like.

**Needs-water is a latch, cleared only by a detected watering.** Moisture
drifting up on its own — a cool night, a probe settling back into the soil — is
not someone having watered the plant. The asymmetry is the point.

The one hole in that: a watering during the minute Home Assistant is restarting
is invisible, because the trailing minimum re-seeds from the first reading
after it and there is no rise left to detect. `plant_care.record_watering`
exists for that case and for history that predates the component. It takes a
plant and an optional `when`, clears the latch as a detected watering would,
and the days-since sensor exposes the exact `last_watered` it produced so an
entry can be checked. A service with a timestamp, not a dashboard button: the
button is what would become the second source of truth.

**Every plant has one owner: a person or a group.** A name in `owners` is a
group iff it is a key of `groups`, which is the household file
`light_motion_profiles` also includes, so membership cannot drift. A group has
one shared notify action. Membership decides dashboards: each member of a group
gets its plants on their own tab (`plants/<person>`) and in
`sensor.plant_outstanding_<person>`, so members of a group in `owners` must be
people in `owners`. Faults belonging to no plant go to `system_notify` and the
shared `plants/all` tab only.

A person may set an `icon` for their tab, defaulting to `mdi:account`. A group
may not: it has no tab, so the icon would render nowhere and read as one that
failed to apply — that config is refused rather than ignored.

**Anything that appears in the feed is pushed once.** A sensor is only read by
whoever is looking, and a silent probe found three days later has already cost
the plant. Each new item goes to its owner's action the moment it appears —
identified by what it is about, not its text, since days-since ticks on every
recompute — and never again while it stays. The announced set is persisted,
because Home Assistant restarts far more often than a plant is watered and every
restart would otherwise re-send everything outstanding. It is kept per action,
so a missing phone is retried without the others repeating and a plant handed to
a new owner reaches them.

**The feed sensors render themselves.** Each carries the count as its state,
the items as `items`, and the same list rendered as `markdown`. The generated
tabs are not the only place this list appears — the household dashboards in
`hass-configs` show it too — and a card that renders `items` itself has to know
which fields each item kind carries, so a second copy of that Jinja is one that
will not be updated when an item grows a field. A card is therefore two lines:

```yaml
type: markdown
content: |
  ## Needs attention ({{ states('sensor.plant_outstanding_nick') }})

  {{ state_attr('sensor.plant_outstanding_nick', 'markdown') }}
```

Neither attribute is recorded: the count is worth a history, the prose is
re-derivable at any time and would otherwise be written to the database on
every measurement that moves.

**The coordinator subscribes to `state_reported` as well as `state_changed`, and
reads `last_reported`.** A pot sitting still reports the same number for hours;
Home Assistant fires no state *change* for that, and `last_updated` would hand
every repeat the timestamp of the first one. Either mistake stalls the confirm
clock so a dry plant never flags. Both are covered by tests.

**Health checks report faults whose failure mode is silence.** A flat battery, a
probe knocked out of the soil, or water channelling down the side of a dried-out
rootball all look exactly like stable healthy soil to anything watching a
threshold. They go into the same feed as care items, faults first, because a
silent probe means nothing else about that plant can be believed.

**Probe availability and waterlogging are budgets, not thresholds.** A
threshold sees only the episode in front of it, so a probe that drops out for
twenty minutes every night never trips it — every episode heals first. The
budget sees the week: 99% availability over seven days, and a per-plant share
of the week a pot may sit above field capacity. A single two-hour dropout is
inside the budget and produces nothing, which is the false positive the old
threshold used to raise. A probe genuinely down still pages after six hours.

**A heartbeat is missed after a multiple of it, not the instant it is late.**
The probe's clock is not Home Assistant's, and moisture is judged over hours.
A report counts as missed after `missed_heartbeat_after` heartbeats (2.0, so
twenty minutes: one dropped report is forgiven, two in a row are not), and
the silence then runs from when the report was due.

The watering-shortfall check has a known blind spot worth stating: it only runs
on a *detected* watering, and complete channelling produces no rise to detect.
That case is caught instead by the needs-water latch never clearing — which is
why the latch is cleared only by a detected watering, never by moisture drifting
up on its own. There is a test named after it.

**A killswitch freezes a fixture; it does not turn it off.** One boolean rather
than a kill plus a force, because two can contradict each other. That makes a
frozen fixture dangerous in both directions — stuck off starves the plants under
it, stuck on gives them a 24-hour photoperiod — so nothing reports the boolean.
What gets reported is the *outcome*: actual on-time against what the window
allows, which reads the same for a dead bulb, a dropped outlet, a manual toggle
and a frozen automation. Plus a 48-hour backstop for the killswitch itself,
because forgetting it is the real failure mode.

**The killswitch's "on since" lives in the component's store, not in
`last_changed`.** `RestoreState` brings a switch's value back after a restart but
stamps it with the restore time, so a two-day-old killswitch would read as brand
new on every restart and the 48-hour backstop would never fire.

**On-time survives a restart, and the break in it is filled in.** Config sync
restarts Home Assistant within a minute of anything landing on `hass-configs`
main, so an afternoon of editing is several restarts. A counter starting again
at zero on each one does not just display the wrong number — the comparison
window restarts with it, so the check goes blind to the whole morning and a bulb
that died at nine is forgiven by a deploy at four. Nothing but the controller
commands the switch, so across a short gap the lamp held the state it was last
recorded in and the gap is credited from it. An outage past
`MAX_RESTART_GAP_MINUTES` is not guessed at: the day's total is carried on for
the dashboard, and the comparison reopens from start-up so it only ever judges a
stretch something was watching. `tracked_minutes` on the sensor is the part of
the day the attributes are talking about.

**Light is an SLO with a continuous error budget, not a threshold.** The budget
is cumulative mol/m² of deviation from a *band*, so a day 10% short burns a
little and a day at zero burns a lot — the distinction a "days outside the band"
count cannot make. A band rather than a point target, because with a point every
day deviates, the burn rate never returns to zero, and the numbers stop meaning
anything.

**Nothing about today is reported as a projection.** Light is not flat across a
day, so extrapolating a morning's rate would call every sunrise a disaster. The
two intra-day signals are statements about what can no longer change:
accumulation has already passed the upper bound, or the lower bound is out of
reach even at the best rate this plant has ever managed.

**The fast burn is asymmetric, and necessarily so.** A day's shortfall cannot
exceed the band's lower bound while a day's excess has no ceiling, so for most
houseplants no single dark day reaches a 10× threshold. That is why the fast page
is "burn rate **or** outside survival", and why the intra-day checks exist: the
low side is covered by two signals that do not depend on the budget's scale.
`tests/test_dli.py` pins what the shipped numbers actually do, including what
they deliberately do *not* alert on.

**lux→PPFD belongs to the emitter, not the sensor.** Lux is weighted by human
vision and PPFD counts photons, so the ratio depends entirely on the spectrum.
Each lamp declares its own factor and the conversion switches with the lamp. Two
lamps of different types over one sensor cannot be told apart at all — the band's
width and the error budget are what absorb that, which is the whole argument for
an SLO here rather than a target. Tune the budget before tuning factors.

**A lux fixture is averaged, and a dead member is skipped rather than counted as
zero.** One probe shaded by a single leaf reports a value true for that spot and
wrong for the plant; a probe that has dropped out would otherwise read as
darkness, which looks exactly like a failed lamp.
