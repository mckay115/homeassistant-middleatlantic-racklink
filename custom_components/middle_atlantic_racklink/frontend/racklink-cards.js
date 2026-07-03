/**
 * Custom Lovelace cards for the Middle Atlantic RackLink integration.
 *
 * Provides:
 *   - custom:racklink-pdu-card       PDU overview with live metrics and
 *                                    per-outlet switch/cycle controls.
 *   - custom:racklink-sequencer-card Load shedding and outlet sequencing
 *                                    management for a PDU.
 *
 * Both cards are configured with the PDU device (device_id) and discover
 * the integration's entities automatically, so they keep working when
 * entities are renamed.
 */

const DOMAIN = "middle_atlantic_racklink";

/* ------------------------------------------------------------------ */
/* Entity discovery helpers                                            */
/* ------------------------------------------------------------------ */

function deviceEntities(hass, deviceId) {
  return Object.values(hass.entities || {}).filter(
    (entry) =>
      entry.device_id === deviceId &&
      entry.platform === DOMAIN &&
      !entry.hidden &&
      !entry.disabled_by
  );
}

function stateOf(hass, entry) {
  return entry ? hass.states[entry.entity_id] : undefined;
}

function outletNumber(hass, entry) {
  const state = stateOf(hass, entry);
  const attr = state && state.attributes.outlet_number;
  return attr === undefined ? null : Number(attr);
}

function classify(hass, entry) {
  // Prefer the registry translation_key; fall back to attribute heuristics
  // for older frontends that do not expose it.
  if (entry.translation_key) return entry.translation_key;
  const [domain] = entry.entity_id.split(".");
  const state = stateOf(hass, entry);
  const attrs = state ? state.attributes : {};
  const outlet = attrs.outlet_number !== undefined;
  if (domain === "switch") return outlet ? "outlet" : null;
  if (domain === "button") return outlet ? "cycle_outlet" : "cycle_all_outlets";
  if (domain === "sensor" && outlet) {
    if (attrs.device_class === "power") return "outlet_power";
    if (attrs.device_class === "energy") return "outlet_energy";
    if (attrs.device_class === "current") return "outlet_current";
    if (attrs.device_class === "voltage") return "outlet_voltage";
  }
  if (domain === "sensor") return attrs.device_class || null;
  if (domain === "binary_sensor" && outlet) return "outlet_non_critical";
  if (domain === "binary_sensor") return "surge_protection";
  if (domain === "number") return "sequence_delay";
  return null;
}

function collectModel(hass, deviceId) {
  const model = {
    pdu: {},
    outlets: new Map(),
    loadShedding: null,
    sequence: null,
    sequenceDelay: null,
    cycleAll: null,
    surgeProtection: null,
  };
  for (const entry of deviceEntities(hass, deviceId)) {
    const key = classify(hass, entry);
    if (!key) continue;
    const outlet = outletNumber(hass, entry);
    if (outlet !== null) {
      if (!model.outlets.has(outlet)) model.outlets.set(outlet, {});
      const bucket = model.outlets.get(outlet);
      if (key === "outlet") bucket.switch = entry;
      else if (key === "cycle_outlet") bucket.cycle = entry;
      else if (key === "outlet_power") bucket.power = entry;
      else if (key === "outlet_energy") bucket.energy = entry;
      else if (key === "outlet_current") bucket.current = entry;
      else if (key === "outlet_non_critical") bucket.nonCritical = entry;
      continue;
    }
    if (key === "load_shedding") model.loadShedding = entry;
    else if (key === "sequence") model.sequence = entry;
    else if (key === "sequence_delay") model.sequenceDelay = entry;
    else if (key === "cycle_all_outlets") model.cycleAll = entry;
    else if (key === "surge_protection") model.surgeProtection = entry;
    else model.pdu[key] = entry;
  }
  return model;
}

function fmt(hass, entry, digits) {
  const state = stateOf(hass, entry);
  if (!state || state.state === "unknown" || state.state === "unavailable") {
    return null;
  }
  const value = Number(state.state);
  const text = Number.isFinite(value)
    ? value.toFixed(digits === undefined ? 1 : digits)
    : state.state;
  const unit = state.attributes.unit_of_measurement;
  return unit ? `${text} ${unit}` : text;
}

function firstDeviceOfIntegration(hass) {
  const entry = Object.values(hass.entities || {}).find(
    (item) => item.platform === DOMAIN && item.device_id
  );
  return entry ? entry.device_id : "";
}

function deviceName(hass, deviceId) {
  const device = (hass.devices || {})[deviceId];
  return device ? device.name_by_user || device.name : null;
}

/* ------------------------------------------------------------------ */
/* Base card                                                           */
/* ------------------------------------------------------------------ */

class RacklinkBaseCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    if (!config || !config.device_id) {
      throw new Error("Set device_id to your RackLink PDU device");
    }
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _call(domain, service, data) {
    this._hass.callService(domain, service, data);
  }

  _moreInfo(entityId) {
    const event = new CustomEvent("hass-more-info", {
      bubbles: true,
      composed: true,
      detail: { entityId },
    });
    this.dispatchEvent(event);
  }

  _render() {
    if (!this._config || !this._hass) return;
    const model = collectModel(this._hass, this._config.device_id);
    this.shadowRoot.innerHTML = `<style>${this._styles()}</style>${this._template(
      model
    )}`;
    this._bind(model);
  }

  _styles() {
    return `
      ha-card { padding: 12px 16px 16px; }
      .header {
        display: flex; align-items: baseline; justify-content: space-between;
        padding: 4px 0 8px;
      }
      .title { font-size: 1.25rem; font-weight: 500; }
      .headline { font-size: 1.25rem; font-weight: 500; color: var(--primary-color); }
      .chips { display: flex; flex-wrap: wrap; gap: 8px; padding-bottom: 8px; }
      .chip {
        background: var(--secondary-background-color);
        border-radius: 12px; padding: 4px 10px; font-size: 0.85rem;
        color: var(--secondary-text-color); cursor: pointer;
      }
      .chip b { color: var(--primary-text-color); font-weight: 500; }
      .row {
        display: flex; align-items: center; gap: 12px;
        padding: 6px 0; border-top: 1px solid var(--divider-color);
      }
      .row .grow { flex: 1; min-width: 0; }
      .row .name { font-weight: 500; cursor: pointer; }
      .row .meta { font-size: 0.8rem; color: var(--secondary-text-color); }
      .dot {
        width: 10px; height: 10px; border-radius: 50%;
        background: var(--disabled-color, #9e9e9e); flex: none;
      }
      .dot.on { background: var(--success-color, #4caf50); }
      .dot.off { background: var(--error-color, #f44336); }
      button {
        font: inherit; color: var(--primary-color);
        background: none; border: 1px solid var(--primary-color);
        border-radius: 8px; padding: 4px 10px; cursor: pointer;
      }
      button.icon { border: none; padding: 4px; font-size: 1rem; }
      button.danger { color: var(--error-color); border-color: var(--error-color); }
      button:disabled { opacity: 0.4; cursor: default; }
      .toggle {
        position: relative; width: 40px; height: 22px; flex: none;
        border-radius: 11px; border: none; padding: 0; cursor: pointer;
        background: var(--disabled-color, #9e9e9e); transition: background 0.2s;
      }
      .toggle.on { background: var(--primary-color); }
      .toggle::after {
        content: ""; position: absolute; top: 2px; left: 2px;
        width: 18px; height: 18px; border-radius: 50%;
        background: var(--card-background-color, #fff); transition: left 0.2s;
      }
      .toggle.on::after { left: 20px; }
      .stepper { display: flex; align-items: center; gap: 8px; }
      .stepper .value { min-width: 42px; text-align: center; font-weight: 500; }
      .footer { display: flex; gap: 8px; padding-top: 10px; flex-wrap: wrap; }
      .empty { color: var(--secondary-text-color); padding: 8px 0; }
      .section { border-top: 1px solid var(--divider-color); margin-top: 4px; padding-top: 4px; }
      .section-title {
        font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em;
        color: var(--secondary-text-color); padding: 4px 0;
      }
    `;
  }

  _toggleButton(entry, cls) {
    const state = stateOf(this._hass, entry);
    const on = state && state.state === "on";
    const disabled =
      !state || state.state === "unavailable" ? "disabled" : "";
    return `<button class="toggle ${on ? "on" : ""} ${cls || ""}"
      data-entity="${entry.entity_id}" data-action="toggle" ${disabled}
      title="${entry.entity_id}"></button>`;
  }

  _bind() {
    this.shadowRoot.querySelectorAll("[data-action]").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const { action, entity } = el.dataset;
        if (action === "toggle") {
          this._call("switch", "toggle", { entity_id: entity });
        } else if (action === "press") {
          this._call("button", "press", { entity_id: entity });
        } else if (action === "step") {
          const value = Number(el.dataset.value);
          this._call("number", "set_value", { entity_id: entity, value });
        } else if (action === "more-info") {
          this._moreInfo(entity);
        }
      });
    });
  }

  getCardSize() {
    return 3;
  }
}

/* ------------------------------------------------------------------ */
/* PDU overview card                                                   */
/* ------------------------------------------------------------------ */

class RacklinkPduCard extends RacklinkBaseCard {
  static getConfigElement() {
    return document.createElement("racklink-card-editor");
  }

  static getStubConfig(hass) {
    return { device_id: firstDeviceOfIntegration(hass) };
  }

  _template(model) {
    const title =
      this._config.title ||
      deviceName(this._hass, this._config.device_id) ||
      "RackLink PDU";
    const power = fmt(this._hass, model.pdu.power, 1);
    const chips = [
      ["Voltage", model.pdu.voltage, 1],
      ["Current", model.pdu.current, 2],
      ["Energy", model.pdu.energy, 2],
      ["Power factor", model.pdu.power_factor, 2],
    ]
      .filter(([, entry]) => entry && fmt(this._hass, entry) !== null)
      .map(
        ([label, entry, digits]) => `
          <span class="chip" data-action="more-info"
                data-entity="${entry.entity_id}">
            ${label} <b>${fmt(this._hass, entry, digits)}</b>
          </span>`
      )
      .join("");

    const outlets = [...model.outlets.entries()]
      .sort(([a], [b]) => a - b)
      .map(([number, bucket]) => this._outletRow(number, bucket))
      .join("");

    const footer = [];
    if (model.cycleAll) {
      footer.push(`<button class="danger" data-action="press"
        data-entity="${model.cycleAll.entity_id}">Cycle all outlets</button>`);
    }

    return `
      <ha-card>
        <div class="header">
          <span class="title">${title}</span>
          ${
            power !== null && model.pdu.power
              ? `<span class="headline" data-action="more-info"
                   data-entity="${model.pdu.power.entity_id}">${power}</span>`
              : ""
          }
        </div>
        <div class="chips">${chips}</div>
        ${outlets || '<div class="empty">No outlets found for this device.</div>'}
        ${footer.length ? `<div class="footer">${footer.join("")}</div>` : ""}
      </ha-card>
    `;
  }

  _outletRow(number, bucket) {
    if (!bucket.switch) return "";
    const state = stateOf(this._hass, bucket.switch);
    const on = state && state.state === "on";
    const unavailable = !state || state.state === "unavailable";
    const name =
      (state && (state.attributes.outlet_name || state.attributes.friendly_name)) ||
      `Outlet ${number}`;
    const metaParts = [];
    const power = fmt(this._hass, bucket.power, 1);
    if (power !== null) metaParts.push(power);
    const current = fmt(this._hass, bucket.current, 2);
    if (current !== null) metaParts.push(current);
    const nonCriticalState = stateOf(this._hass, bucket.nonCritical);
    if (nonCriticalState && nonCriticalState.state === "on") {
      metaParts.push("sheds");
    }

    return `
      <div class="row">
        <span class="dot ${unavailable ? "" : on ? "on" : "off"}"></span>
        <div class="grow">
          <div class="name" data-action="more-info"
               data-entity="${bucket.switch.entity_id}">${number}. ${name}</div>
          ${metaParts.length ? `<div class="meta">${metaParts.join(" · ")}</div>` : ""}
        </div>
        ${
          bucket.cycle
            ? `<button class="icon" title="Cycle outlet" data-action="press"
                 data-entity="${bucket.cycle.entity_id}">&#8635;</button>`
            : ""
        }
        ${this._toggleButton(bucket.switch)}
      </div>
    `;
  }

  getCardSize() {
    if (!this._config || !this._hass) return 3;
    return 2 + collectModel(this._hass, this._config.device_id).outlets.size;
  }
}

/* ------------------------------------------------------------------ */
/* Sequencer / load shedding card                                      */
/* ------------------------------------------------------------------ */

class RacklinkSequencerCard extends RacklinkBaseCard {
  static getConfigElement() {
    return document.createElement("racklink-card-editor");
  }

  static getStubConfig(hass) {
    return { device_id: firstDeviceOfIntegration(hass) };
  }

  _template(model) {
    const title =
      this._config.title ||
      `${deviceName(this._hass, this._config.device_id) || "RackLink"} power management`;

    const sections = [];

    if (model.sequence) {
      const delay = model.sequenceDelay;
      const delayState = stateOf(this._hass, delay);
      const delayValue = delayState ? Number(delayState.state) : null;
      const min = delayState ? Number(delayState.attributes.min) : 1;
      const max = delayState ? Number(delayState.attributes.max) : 60;
      sections.push(`
        <div class="section">
          <div class="section-title">Power-on sequencing</div>
          <div class="row">
            <div class="grow">
              <div class="name" data-action="more-info"
                   data-entity="${model.sequence.entity_id}">Outlet sequence</div>
              <div class="meta">Staggers outlet power-on to avoid inrush current</div>
            </div>
            ${this._toggleButton(model.sequence)}
          </div>
          ${
            delay && delayValue !== null && Number.isFinite(delayValue)
              ? `<div class="row">
                  <div class="grow">
                    <div class="name" data-action="more-info"
                         data-entity="${delay.entity_id}">Delay between outlets</div>
                  </div>
                  <div class="stepper">
                    <button data-action="step" data-entity="${delay.entity_id}"
                      data-value="${Math.max(min, delayValue - 1)}"
                      ${delayValue <= min ? "disabled" : ""}>&minus;</button>
                    <span class="value">${delayValue} s</span>
                    <button data-action="step" data-entity="${delay.entity_id}"
                      data-value="${Math.min(max, delayValue + 1)}"
                      ${delayValue >= max ? "disabled" : ""}>+</button>
                  </div>
                </div>`
              : ""
          }
        </div>
      `);
    }

    if (model.loadShedding) {
      const shedding = [...model.outlets.entries()]
        .filter(([, bucket]) => {
          const state = stateOf(this._hass, bucket.nonCritical);
          return state && state.state === "on";
        })
        .map(([number, bucket]) => {
          const state = stateOf(this._hass, bucket.switch);
          const name =
            (state &&
              (state.attributes.outlet_name || state.attributes.friendly_name)) ||
            `Outlet ${number}`;
          return `${number}. ${name}`;
        });
      sections.push(`
        <div class="section">
          <div class="section-title">Load shedding</div>
          <div class="row">
            <div class="grow">
              <div class="name" data-action="more-info"
                   data-entity="${model.loadShedding.entity_id}">Load shedding</div>
              <div class="meta">${
                shedding.length
                  ? `Powers off: ${shedding.join(", ")}`
                  : "No outlets are marked non-critical on the PDU"
              }</div>
            </div>
            ${this._toggleButton(model.loadShedding)}
          </div>
        </div>
      `);
    }

    if (model.cycleAll) {
      sections.push(`
        <div class="section">
          <div class="footer">
            <button class="danger" data-action="press"
              data-entity="${model.cycleAll.entity_id}">Cycle all outlets</button>
          </div>
        </div>
      `);
    }

    return `
      <ha-card>
        <div class="header"><span class="title">${title}</span></div>
        ${
          sections.length
            ? sections.join("")
            : '<div class="empty">No sequencing or load shedding controls found. ' +
              "These require the telnet channel (vendor features).</div>"
        }
      </ha-card>
    `;
  }

  getCardSize() {
    return 4;
  }
}

/* ------------------------------------------------------------------ */
/* Shared visual editor (device picker + title)                        */
/* ------------------------------------------------------------------ */

class RacklinkCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (schema) =>
        schema.name === "device_id" ? "PDU device" : "Title (optional)";
      this._form.addEventListener("value-changed", (ev) => {
        const config = { type: this._config.type, ...ev.detail.value };
        this._config = config;
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            bubbles: true,
            composed: true,
            detail: { config },
          })
        );
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = this._config;
    this._form.schema = [
      {
        name: "device_id",
        required: true,
        selector: { device: { integration: DOMAIN } },
      },
      { name: "title", selector: { text: {} } },
    ];
  }
}

/* ------------------------------------------------------------------ */
/* Registration                                                        */
/* ------------------------------------------------------------------ */

if (!customElements.get("racklink-pdu-card")) {
  customElements.define("racklink-pdu-card", RacklinkPduCard);
}
if (!customElements.get("racklink-sequencer-card")) {
  customElements.define("racklink-sequencer-card", RacklinkSequencerCard);
}
if (!customElements.get("racklink-card-editor")) {
  customElements.define("racklink-card-editor", RacklinkCardEditor);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "racklink-pdu-card")) {
  window.customCards.push(
    {
      type: "racklink-pdu-card",
      name: "RackLink PDU",
      description:
        "Overview and control of a Middle Atlantic RackLink PDU: live power " +
        "metrics plus per-outlet switches and cycle buttons.",
      preview: true,
      documentationURL:
        "https://github.com/mckay115/homeassistant-middleatlantic-racklink#dashboard-cards",
    },
    {
      type: "racklink-sequencer-card",
      name: "RackLink sequencer",
      description:
        "Manage RackLink outlet power-on sequencing, sequence delay, and " +
        "load shedding.",
      preview: true,
      documentationURL:
        "https://github.com/mckay115/homeassistant-middleatlantic-racklink#dashboard-cards",
    }
  );
}
