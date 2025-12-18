import PlacesUtils from "places";

const { Services } = ChromeUtils.importESModule(
  "resource://gre/modules/Services.sys.mjs"
);

function getPersonaBaseUrl() {
  const base = Services.env.get("EVAL_PERSONA_PROXY_URL");
  return base ? base.replace(/\/$/, "") : null;
}

function getPersonaUrl(metadata) {
  return (
    Services.env.get("EVAL_PERSONA_URL") ||
    metadata?.persona ||
    metadata?.options?.default?.persona ||
    null
  );
}

export function getProxyUrl(path) {
  if (!path) {
    return path;
  }
  const base = getPersonaBaseUrl();
  if (!base || /^https?:\/\//i.test(path)) {
    return path;
  }
  return `${base}/${String(path).replace(/^\/+/, "")}`;
}

export class Persona {
  static #visitedUrls = new Set();

  static async create(metadata) {
    const personaUrl = getPersonaUrl(metadata);
    if (!personaUrl) {
      throw new Error("Persona.create called without a persona URL.");
    }

    const response = await fetch(personaUrl);
    if (!response.ok) {
      throw new Error(`Failed to fetch persona: ${response.status}`);
    }
    const persona = await response.json();
    const history = Array.isArray(persona?.history) ? persona.history : [];
    if (!history.length) {
      return;
    }

    const visits = [];
    for (const entry of history) {
      const url = getProxyUrl(entry.snapshotPath) || entry.url;
      if (!url) {
        continue;
      }

      const when = entry.visitedAt ? new Date(entry.visitedAt) : new Date();
      const date = Number.isNaN(when.valueOf()) ? new Date() : when;
      visits.push({
        url,
        title: entry.title || entry.url,
        visits: [
          {
            date,
            transition: PlacesUtils.history.TRANSITIONS.LINK,
          },
        ],
      });
      this.#visitedUrls.add(url);
    }

    if (visits.length) {
      await PlacesUtils.history.insertMany(visits);
    }
  }

  static async destroy() {
    if (!this.#visitedUrls.size) {
      return;
    }
    await PlacesUtils.history.remove([...this.#visitedUrls]);
    this.#visitedUrls.clear();
  }
}
