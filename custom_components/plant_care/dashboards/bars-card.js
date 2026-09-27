// The plants overview: thin labelled progress bars, one titled group per
// plant, laid out as many across as fit.
//
//   type: custom:plant-care-bars
//   groups:
//     - title: Monstera
//       bars:
//         - entity: sensor.plant_monstera_dli_today
//           name: Light
//           icon: mdi:white-balance-sunny
//           max: 9
//           stops: [{from: 0, color: orange}, {from: 4, color: green}]
//         - entity: sensor.plant_monstera_feed_days_since
//           name: Feed
//           icon: mdi:nutrition
//           max: 21
//           countdown: true
//
// A bar fills to its entity's state, out of `max`, or out of the attribute
// `max_attribute` names when the ceiling moves from day to day. `stops` colour
// the bar along its length: each stop's colour runs from its `from` to the
// next stop, so a bar short of the first threshold is all the first colour and
// one past it shows both.
//
// A `countdown` bar reads its state as time elapsed out of `max` and shows
// what is left, draining as it runs out.
//
// Plain DOM rather than lit: Home Assistant does not export lit to custom
// cards, and this is small enough not to need it. Built by `BarsCard` in
// bars.py, and served by the component itself.

const TAG = "plant-care-bars";

const escape = (text) =>
  String(text).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

const number = (value) =>
  value.toLocaleString(undefined, { maximumFractionDigits: 1 });

const clamp = (value) => Math.max(0, Math.min(1, value));

class PlantCareBarsCard extends HTMLElement {
  setConfig(config) {
    if (!Array.isArray(config.groups)) {
      throw new Error("groups is required");
    }
    for (const bar of config.groups.flatMap((g) => g.bars)) {
      if ((bar.max === undefined) === (bar.max_attribute === undefined)) {
        throw new Error(`${bar.entity}: give exactly one of max and max_attribute`);
      }
    }
    this._config = config;
    this._states = undefined;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (ev) => this._click(ev));
    }
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    // Every state change anywhere in the house sets `hass`; only redraw when
    // one of this card's own entities moved.
    const states = this._bars().map((bar) => hass.states[bar.entity]);
    if (this._states && states.every((s, i) => s === this._states[i])) {
      return;
    }
    this._states = states;
    this._render();
  }

  _bars() {
    return this._config.groups.flatMap((g) => g.bars);
  }

  _click(ev) {
    const row = ev.composedPath().find((el) => el.dataset?.entity);
    if (row) {
      this.dispatchEvent(
        new CustomEvent("hass-more-info", {
          bubbles: true,
          composed: true,
          detail: { entityId: row.dataset.entity },
        })
      );
    }
  }

  // The fill fraction, the text beside the bar, and whether it has run out.
  _reading(bar, stateObj, max) {
    if (!stateObj) {
      return { fraction: 0, text: "missing" };
    }
    const value = parseFloat(stateObj.state);
    if (isNaN(value) || !(max > 0)) {
      // A task never done has no days-since, and is deliberately not due.
      const never = bar.countdown && stateObj.state === "unknown";
      return {
        fraction: 0,
        text: never ? "never done" : this._hass.formatEntityState(stateObj),
      };
    }
    if (!bar.countdown) {
      return {
        fraction: value / max,
        text: this._hass.formatEntityState(stateObj),
      };
    }
    const left = max - value;
    const unit = stateObj.attributes.unit_of_measurement ?? "";
    return left >= 0
      ? { fraction: left / max, text: `${number(left)} ${unit} left` }
      : { fraction: 0, text: `${number(-left)} ${unit} overdue`, overdue: true };
  }

  // The fill is a gradient with a hard edge at each stop, laid over the whole
  // track and clipped to the value, so a colour stays where its stop put it
  // rather than stretching with the fill.
  _fill(bar, max) {
    const stops = bar.stops ?? [];
    if (!stops.length || !(max > 0)) {
      return "var(--primary-color)";
    }
    const pct = (v) => `${clamp(v / max) * 100}%`;
    const parts = stops.map((stop, i) => {
      const end = stops[i + 1]?.from ?? max;
      return `${stop.color} ${pct(stop.from)} ${pct(end)}`;
    });
    return `linear-gradient(to right, ${parts.join(", ")})`;
  }

  _row(bar) {
    const stateObj = this._hass.states[bar.entity];
    const max = bar.max ?? parseFloat(stateObj?.attributes[bar.max_attribute]);
    const reading = this._reading(bar, stateObj, max);
    const clip = (1 - clamp(reading.fraction)) * 100;
    return `
      <div class="row" data-entity="${escape(bar.entity)}">
        <ha-icon icon="${escape(bar.icon)}"></ha-icon>
        <span class="name">${escape(bar.name)}</span>
        <span class="track"><span class="fill"
          style="background: ${escape(this._fill(bar, max))}; clip-path: inset(0 ${clip}% 0 0)"
        ></span></span>
        <span class="text${reading.overdue ? " overdue" : ""}">${escape(reading.text)}</span>
      </div>`;
  }

  _render() {
    if (!this._config || !this._hass || !this.shadowRoot) {
      return;
    }
    const groups = this._config.groups
      .map(
        (group) => `
          <div class="group">
            <div class="title">${escape(group.title)}</div>
            ${group.bars.map((bar) => this._row(bar)).join("")}
          </div>`
      )
      .join("");
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 12px 16px; }
        .groups {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
          gap: 16px 32px;
        }
        .title { font-weight: 500; margin-bottom: 2px; }
        .row {
          display: grid;
          grid-template-columns: 20px 6em 1fr 7em;
          align-items: center;
          gap: 8px;
          min-height: 26px;
          cursor: pointer;
        }
        ha-icon {
          --mdc-icon-size: 18px;
          color: var(--state-icon-color, var(--secondary-text-color));
        }
        .name {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          color: var(--secondary-text-color);
        }
        .track {
          position: relative;
          height: 8px;
          border-radius: 4px;
          overflow: hidden;
          background: var(--divider-color);
        }
        .fill { position: absolute; inset: 0; }
        .text {
          text-align: right;
          white-space: nowrap;
          font-variant-numeric: tabular-nums;
        }
        .overdue { color: var(--error-color); }
      </style>
      <ha-card><div class="groups">${groups}</div></ha-card>`;
  }

  getCardSize() {
    const rows = this._config.groups.reduce((sum, g) => sum + 1 + g.bars.length, 0);
    return Math.ceil(rows / 2);
  }
}

if (!customElements.get(TAG)) {
  customElements.define(TAG, PlantCareBarsCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: TAG,
    name: "Plant care bars",
    description: "Each plant's water, light and care tasks as progress bars.",
  });
}
