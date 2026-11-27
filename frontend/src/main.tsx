import { Component, useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/dm-sans/latin-400.css";
import "@fontsource/dm-sans/latin-600.css";
import "@fontsource/instrument-serif/latin-400.css";
import { api, type Config, type WatchlistPrices, type WatchlistScan } from "./api";
import { Portfolio } from "./Portfolio";
import { Radar } from "./Radar";
import { ResearchForm } from "./ResearchForm";
import { ResearchRun } from "./ResearchRun";
import { WatchlistForm } from "./WatchlistForm";
import { DEFAULT_WATCHLIST, eventRows, formatTime, parseTickers, readStored, writeStored, type Row } from "./watchlist";
import "./style.css";

type View = "portfolio" | "radar" | "research";
const VIEWS: { id: View; label: string }[] = [
  { id: "portfolio", label: "Portfolio" }, { id: "radar", label: "Event radar" }, { id: "research", label: "Custom research" },
];
// Results are kept for the browser tab, so returning from a research run keeps the table.
interface Stored<T> { key: string; data: T }

const local = () => localStorage;
const session = () => sessionStorage;

function readView(): View {
  const view = new URLSearchParams(window.location.search).get("view");
  return view === "radar" || view === "research" ? view : "portfolio";
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

function Workspace({ config, view, onNavigate }: { config: Config; view: View; onNavigate: (view: View) => void }) {
  const [watchlist, setWatchlist] = useState(() => readStored(local, "radar-watchlist", DEFAULT_WATCHLIST));
  const [days, setDays] = useState(30);
  const [prices, setPrices] = useState(() => readStored<Stored<WatchlistPrices> | null>(session, "radar-prices", null));
  const [scan, setScan] = useState(() => readStored<Stored<WatchlistScan> | null>(session, "radar-scan", null));
  const [loading, setLoading] = useState<"prices" | "scan" | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const tickers = parseTickers(watchlist);
  const key = JSON.stringify({ tickers, days });
  const valid = tickers.length > 0 && tickers.length <= 15;

  useEffect(() => writeStored(session, "radar-prices", prices), [prices]);
  useEffect(() => writeStored(session, "radar-scan", scan), [scan]);

  async function post<T>(path: string): Promise<T> {
    writeStored(local, "radar-watchlist", watchlist);
    return api<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: key });
  }

  async function loadPrices() {
    setLoading("prices");
    setError("");
    try {
      setPrices({ key, data: await post<WatchlistPrices>("prices") });
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
        post<WatchlistScan>("scan"),
        prices?.key === key ? Promise.resolve(prices.data) : post<WatchlistPrices>("prices"),
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

  // The Portfolio tab draws its charts as soon as it opens; prices are free.
  useEffect(() => {
    if (view === "portfolio" && !prices && valid) void loadPrices();
  }, [view]);

  const rows = eventRows(scan?.data ?? null);
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
    <WatchlistForm watchlist={watchlist} days={days} onWatchlist={setWatchlist} onDays={setDays} onSubmit={onSubmit}>
      {actions}
    </WatchlistForm>
  );
  const busy = loading !== null || !valid;

  let content: ReactNode;
  if (view === "research") content = <ResearchForm config={config} />;
  else if (view === "radar") {
    content = <Radar config={config} scan={scan?.data ?? null} rows={rows} open={open} onOpen={setOpen} notice={notice}
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
  const navigate = (next: View) => {
    window.history.pushState(null, "", next === "portfolio" ? "/" : `/?view=${next}`);
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
            aria-current={!id && view === item.id ? "page" : undefined}
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
