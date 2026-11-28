import { Component, useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/dm-sans/latin-400.css";
import "@fontsource/dm-sans/latin-600.css";
import "@fontsource/instrument-serif/latin-400.css";
import { api, type Config, type Discovery, type EventCategory, type WatchlistPrices, type WatchlistScan } from "./api";
import { Discover } from "./Discover";
import { Portfolio } from "./Portfolio";
import { Radar } from "./Radar";
import { DEFAULT_FILTERS, DEFAULT_SETTINGS, SearchSettingsPanel, settingsPayload, type Filters, type SettingsForm } from "./SearchSettings";
import { EventPage } from "./EventPage";
import { ResearchForm } from "./ResearchForm";
import { ResearchRun } from "./ResearchRun";
import { WatchlistForm } from "./WatchlistForm";
import { DEFAULT_WATCHLIST, eventRows, formatTime, parseTickers, readStored, writeStored, type Row } from "./watchlist";
import "./style.css";

type View = "portfolio" | "radar" | "discover" | "research" | "event";
type Navigate = (view: View, query?: Record<string, string>) => void;
const VIEWS: { id: View; label: string }[] = [
  { id: "portfolio", label: "Portfolio" }, { id: "radar", label: "Event radar" }, { id: "discover", label: "Discover" },
  { id: "research", label: "Custom research" },
];
// Results are kept for the browser tab, so returning from a research run keeps the table.
interface Stored<T> { key: string; data: T }

const local = () => localStorage;
const session = () => sessionStorage;

function readView(): View {
  const view = new URLSearchParams(window.location.search).get("view");
  return view === "radar" || view === "discover" || view === "research" || view === "event" ? view : "portfolio";
}

// A rendering error shows a message instead of a blank page.
class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error(error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <p className="feedback feedback--error" role="alert">
      Something went wrong on this page. <a href={window.location.href}>Reload</a> to try again.
    </p>;
  }
}

function Workspace({ config, view, onNavigate }: { config: Config; view: View; onNavigate: Navigate }) {
  const [watchlist, setWatchlist] = useState(() => readStored(local, "radar-watchlist", DEFAULT_WATCHLIST));
  const [days, setDays] = useState(30);
  const [prices, setPrices] = useState(() => readStored<Stored<WatchlistPrices> | null>(session, "radar-prices", null));
  const [scan, setScan] = useState(() => readStored<Stored<WatchlistScan> | null>(session, "radar-scan", null));
  const [loading, setLoading] = useState<"prices" | "scan" | "discover" | null>(null);
  const [discovery, setDiscovery] = useState(() => readStored<Discovery | null>(session, "radar-discovery", null));
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [settings, setSettings] = useState(() => readStored<SettingsForm>(local, "radar-settings", DEFAULT_SETTINGS));
  const [filters, setFilters] = useState(() => readStored<Filters>(local, "radar-filters", DEFAULT_FILTERS));

  const tickers = parseTickers(watchlist);
  const key = JSON.stringify({ tickers, days });
  const valid = tickers.length > 0 && tickers.length <= 15;

  useEffect(() => writeStored(session, "radar-prices", prices), [prices]);
  useEffect(() => writeStored(session, "radar-scan", scan), [scan]);
  useEffect(() => writeStored(session, "radar-discovery", discovery), [discovery]);
  useEffect(() => writeStored(local, "radar-settings", settings), [settings]);
  useEffect(() => writeStored(local, "radar-filters", filters), [filters]);

  async function post<T>(path: string, body: unknown): Promise<T> {
    writeStored(local, "radar-watchlist", watchlist);
    return api<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }

  async function loadPrices() {
    setLoading("prices");
    setError("");
    try {
      setPrices({ key, data: await post<WatchlistPrices>("prices", { tickers, days }) });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Prices could not be loaded.");
    } finally {
      setLoading(null);
    }
  }

  async function runScan() {
    setLoading("scan");
    setError("");
    try {
      // Refresh the charts too if the watchlist changed, so dots land on matching lines.
      const [scanned, charted] = await Promise.all([
        post<WatchlistScan>("scan", { tickers, days, settings: settingsPayload(settings) }),
        prices?.key === key ? Promise.resolve(prices.data) : post<WatchlistPrices>("prices", { tickers, days }),
      ]);
      setScan({ key, data: scanned });
      setPrices({ key, data: charted });
      setOpen(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The scan failed.");
    } finally {
      setLoading(null);
    }
  }

  async function discover(query: string) {
    setLoading("discover");
    setError("");
    try {
      setDiscovery(await post<Discovery>("discover", { query }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The company search failed.");
    } finally {
      setLoading(null);
    }
  }

  function addHolding(ticker: string) {
    if (tickers.includes(ticker) || tickers.length >= 15) return;
    const next = [...tickers, ticker].join(", ");
    setWatchlist(next);
    writeStored(local, "radar-watchlist", next);
  }

  // Opening Portfolio draws its charts, and redraws them if the holdings changed elsewhere; prices are free.
  useEffect(() => {
    if (view === "portfolio" && valid && prices?.key !== key) void loadPrices();
  }, [view]);

  // Event-type and confidence filters only change what is shown, never the search.
  const filtered = filters.hidden.length > 0 || filters.highOnly;
  const rows = eventRows(scan?.data ?? null).filter((row) =>
    !filters.hidden.includes(row.category) && (!filters.highOnly || row.confidence === "high"));
  // When a live call fails, the API serves the last good copy; say so plainly.
  const staleScans = (scan?.data.companies ?? []).filter((company) => company.stale)
    .map((company) => `${company.ticker} (${company.scanned_at ? formatTime(company.scanned_at) : "earlier"})`);
  const stalePrices = (prices?.data.series ?? []).filter((series) => series.stale)
    .map((series) => `${series.ticker} (${series.fetched_at ? formatTime(series.fetched_at) : "earlier"})`);
  const notice = <>
    {loading === "prices" && <p className="radar-status" role="status">Loading prices…</p>}
    {loading === "scan" && <p className="radar-status" role="status">Searching recent news for {tickers.length} companies with Exa…</p>}
    {error && <p className="feedback feedback--error" role="alert">{error}</p>}
    {staleScans.length > 0 && <p className="feedback feedback--notice">Live news search failed, so these show the most recent earlier scan: {staleScans.join(", ")}.</p>}
    {stalePrices.length > 0 && <p className="feedback feedback--notice">Live prices were unavailable, so these show earlier prices: {stalePrices.join(", ")}.</p>}
  </>;
  const form = (onSubmit: () => void, actions: ReactNode) => (
    <WatchlistForm watchlist={watchlist} days={days} onWatchlist={setWatchlist} onDays={setDays} onSubmit={onSubmit}
      extra={<SearchSettingsPanel settings={settings} filters={filters} onSettings={setSettings} onFilters={setFilters} />}>
      {actions}
    </WatchlistForm>
  );
  const busy = loading !== null || !valid;

  let content: ReactNode;
  if (view === "research") content = <ResearchForm config={config} />;
  else if (view === "discover") {
    content = <Discover discovery={discovery} loading={loading === "discover"} notice={error && notice} holdings={tickers}
      onSearch={discover} onAdd={addHolding} onPortfolio={() => onNavigate("portfolio")} />;
  }
  else if (view === "event") {
    // The event lives in the address, so the page survives a reload and can be shared.
    const params = new URLSearchParams(window.location.search);
    content = <EventPage key={window.location.search} ticker={params.get("ticker") ?? ""} date={params.get("date") ?? ""}
      headline={params.get("headline") ?? ""} category={(params.get("category") ?? "other") as EventCategory}
      settings={settingsPayload(settings)} onBack={() => onNavigate("radar")} />;
  }
  else if (view === "radar") {
    content = <Radar scan={scan?.data ?? null} rows={rows} filtered={filtered} open={open} onOpen={setOpen} notice={notice}
      onResearch={(row: Row) => onNavigate("event", { ticker: row.ticker, date: row.reported_date, headline: row.headline, category: row.category })}
      form={form(runScan, <button className="button button--primary" disabled={busy}>{loading === "scan" ? "Scanning…" : "Scan"}</button>)} />;
  } else {
    content = <Portfolio prices={prices?.data ?? null} rows={scan && scan.key === prices?.key ? rows : []}
      explained={Boolean(scan && scan.key === prices?.key)} notice={notice}
      onSelect={(row: Row) => { setOpen(row.key); onNavigate("radar"); }}
      form={form(loadPrices, <>
        <button className="button button--secondary" disabled={busy}>Show prices</button>
        <button className="button button--primary" type="button" disabled={busy} onClick={runScan}>
          {loading === "scan" ? "Searching…" : "Explain with Exa"}
        </button>
      </>)} />;
  }
  // Keyed by tab: a crash in one tab clears when you switch, while the shared results above survive.
  return <ErrorBoundary key={view}>{content}</ErrorBoundary>;
}

function App() {
  const params = new URLSearchParams(window.location.search);
  const id = params.get("run");
  const [view, setView] = useState<View>(readView);
  const [config, setConfig] = useState<Config | null>(null);
  const [failed, setFailed] = useState(false);

  // Tabs switch without reloading, so results stay in memory; the URL still reflects the tab.
  const navigate: Navigate = (next, query) => {
    const search = new URLSearchParams(next === "portfolio" ? {} : { view: next, ...query }).toString();
    window.history.pushState(null, "", search ? `/?${search}` : "/");
    setView(next);
  };
  useEffect(() => {
    const onBack = () => setView(readView());
    window.addEventListener("popstate", onBack);
    return () => window.removeEventListener("popstate", onBack);
  }, []);
  useEffect(() => {
    if (!id) api<Config>("config").then(setConfig).catch(() => setFailed(true));
  }, [id]);

  let content: ReactNode;
  if (id) content = <ResearchRun id={id} />;
  else if (failed) content = <p className="feedback feedback--error" role="alert">The research API is unavailable. Start the Python server and <a href="/">reload the page</a>.</p>;
  else if (!config) content = <p role="status">Loading…</p>;
  else content = <Workspace config={config} view={view} onNavigate={navigate} />;

  return <>
    <header className="site-header"><div className="workspace site-header__inner">
      <a className="brand" href="/">
        <span className="brand__mark" aria-hidden="true">
          <i className="brand__bar" />
          <i className="brand__bar brand__bar--middle" />
          <i className="brand__bar brand__bar--tall" />
        </span> Financial Research
      </a>
      <nav className="site-header__nav" aria-label="Sections">
        {VIEWS.map((item) => (
          <a key={item.id} href={item.id === "portfolio" ? "/" : `/?view=${item.id}`}
            aria-current={!id && (view === item.id || (view === "event" && item.id === "radar")) ? "page" : undefined}
            onClick={(event) => {
              if (id || event.metaKey || event.ctrlKey || event.shiftKey) return;
              event.preventDefault();
              navigate(item.id);
            }}>{item.label}</a>
        ))}
      </nav>
    </div></header>
    <main className="workspace workspace--content">
      <ErrorBoundary key={id ?? "workspace"}>{content}</ErrorBoundary>
    </main>
  </>;
}

createRoot(document.getElementById("root")!).render(<App />);
