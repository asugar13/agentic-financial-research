import type { EventCategory, SearchSettings } from "./api";

export interface SettingsForm { focus: string; include: string; exclude: string; results: 10 | 25 }
export interface Filters { hidden: EventCategory[]; highOnly: boolean }

export const DEFAULT_SETTINGS: SettingsForm = { focus: "", include: "", exclude: "", results: 10 };
export const DEFAULT_FILTERS: Filters = { hidden: [], highOnly: false };
const CATEGORIES: EventCategory[] = ["earnings", "guidance", "legal", "regulatory", "management", "deal", "product", "other"];

const domains = (value: string) => value.split(/[\s,]+/).map((domain) => domain.trim()).filter(Boolean);

// The request shape the API expects; empty fields reproduce the standard scan.
export function settingsPayload(form: SettingsForm): SearchSettings {
  return { focus: form.focus.trim(), include_domains: domains(form.include), exclude_domains: domains(form.exclude), num_results: form.results };
}

// A one-line summary of what Exa was asked, shown under the results.
export function describeSettings(settings: SearchSettings | undefined): string {
  if (!settings) return "";
  return [
    settings.focus && `focus “${settings.focus}”`,
    settings.include_domains.length > 0 && `only ${settings.include_domains.join(", ")}`,
    settings.exclude_domains.length > 0 && `excluding ${settings.exclude_domains.join(", ")}`,
    settings.num_results !== 10 && `${settings.num_results} results per company`,
  ].filter(Boolean).join(" · ");
}

interface Props {
  settings: SettingsForm;
  filters: Filters;
  onSettings: (settings: SettingsForm) => void;
  onFilters: (filters: Filters) => void;
}

export function SearchSettingsPanel({ settings, filters, onSettings, onFilters }: Props) {
  const customised = JSON.stringify(settings) !== JSON.stringify(DEFAULT_SETTINGS);
  const filtering = filters.hidden.length > 0 || filters.highOnly;
  const toggle = (category: EventCategory, shown: boolean) => onFilters({
    ...filters, hidden: shown ? filters.hidden.filter((item) => item !== category) : [...filters.hidden, category],
  });
  return (
    <details className="search-settings">
      <summary>Search settings{customised || filtering ? " · customised" : ""}</summary>
      <div className="search-settings__body">
        <fieldset className="search-settings__group">
          <legend>What Exa searches</legend>
          <label className="form-field">Focus
            <input className="form-field__input" maxLength={200} placeholder="e.g. AI chips and export rules"
              value={settings.focus} onChange={(event) => onSettings({ ...settings, focus: event.target.value })} />
          </label>
          <label className="form-field">Only these sources
            <input className="form-field__input" placeholder="e.g. reuters.com, sec.gov"
              value={settings.include} onChange={(event) => onSettings({ ...settings, include: event.target.value })} />
          </label>
          <label className="form-field">Exclude sources
            <input className="form-field__input" placeholder="e.g. fool.com, seekingalpha.com"
              value={settings.exclude} onChange={(event) => onSettings({ ...settings, exclude: event.target.value })} />
          </label>
          <label className="form-field">Results per company
            <select className="form-field__input" value={settings.results}
              onChange={(event) => onSettings({ ...settings, results: event.target.value === "25" ? 25 : 10 })}>
              <option value={10}>10 · about $0.007 per company</option>
              <option value={25}>25 · about $0.022 per company</option>
            </select>
          </label>
          <p className="radar-hint">Used by the next scan. Each combination of settings is cached separately.</p>
        </fieldset>
        <fieldset className="search-settings__group">
          <legend>What the results show</legend>
          <div className="search-settings__chips">
            {CATEGORIES.map((category) => (
              <label key={category} className="search-settings__chip">
                <input type="checkbox" checked={!filters.hidden.includes(category)}
                  onChange={(event) => toggle(category, event.target.checked)} />
                <span className={`category category--${category}`}>{category}</span>
              </label>
            ))}
          </div>
          <label className="checkbox-field">
            <input className="checkbox-field__input" type="checkbox" checked={filters.highOnly}
              onChange={(event) => onFilters({ ...filters, highOnly: event.target.checked })} />
            High-confidence events only
          </label>
          <p className="radar-hint">Applies instantly, with no new search.</p>
          {(customised || filtering) && (
            <button className="button button--secondary" type="button"
              onClick={() => { onSettings(DEFAULT_SETTINGS); onFilters(DEFAULT_FILTERS); }}>Reset to defaults</button>
          )}
        </fieldset>
      </div>
    </details>
  );
}
