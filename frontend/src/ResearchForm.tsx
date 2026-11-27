import { useRef, useState, type FormEvent } from "react";
import { api, providerNames, type Config, type Mode, type ResearchRunRequest } from "./api";

const starters = [
  {
    category: "Company filings", symbol: "AAPL", title: "Apple’s earnings",
    question: "What revenue did Apple report for its fiscal fourth quarter of 2025? Find and cite Apple’s original earnings release.",
    start: "2025-01-01", end: "2025-12-31",
  },
  {
    category: "Competitive landscape", symbol: "NVDA", title: "Nvidia’s position",
    question: "Which developments affected Nvidia’s competitive position over the last 30 days? Cite primary sources and distinguish reported facts from inference.",
    start: new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10),
    end: new Date().toISOString().slice(0, 10),
  },
  {
    category: "Price analysis", symbol: "AAPL, MSFT", title: "Comparing returns",
    question: "How did AAPL and MSFT daily returns compare in 2025? Calculate their volatility and 20-return rolling correlation.",
    start: "2025-01-01", end: "2025-12-31",
  },
];

export function ResearchForm({ config }: { config: Config }) {
  const [mode, setMode] = useState<Mode>(config.modes[0]);
  const [question, setQuestion] = useState(starters[0].question);
  const [symbols, setSymbols] = useState(starters[0].symbol);
  const [start, setStart] = useState(starters[0].start);
  const [end, setEnd] = useState(starters[0].end);
  const [budget, setBudget] = useState(4);
  const [review, setReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const questionField = useRef<HTMLTextAreaElement>(null);
  const demo = mode === "demo";

  function chooseStarter(item: (typeof starters)[number]) {
    if (demo) setMode(config.modes[0]);

    setQuestion(item.question);
    setSymbols(item.symbol);
    setStart(item.start);
    setEnd(item.end);
    setError("");

    questionField.current?.focus();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;

    setError("");

    const tickers = [...new Set(symbols.trim().toUpperCase().split(/[\s,]+/).filter(Boolean))];
    if (!demo && (tickers.length < 1 || tickers.length > 5 || start >= end || !question.trim())) {
      setError("Enter a question, one to five symbols, and an end date after the start date.");
      return;
    }

    const request: ResearchRunRequest = {
      question: demo ? config.demo_question : question.trim(), mode, max_tool_calls: budget, review,
    };
    if (!demo) request.scope = { symbols: tickers, start_date: start, end_date: end };

    setBusy(true);

    try {
      const result = await api<{ id: string }>("runs", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request),
      });

      window.location.assign("?run=" + encodeURIComponent(result.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start research.");
      setBusy(false);
    }
  }

  return <>
    <header className="page-intro">
      <h1 className="page-intro__title">Custom research<span className="page-intro__accent">.</span></h1>
      <p className="page-intro__description">Ask a market question. Follow the evidence behind the answer.</p>
    </header>

    <div className="research-layout">
      <form className="panel research-form" onSubmit={submit}>
        <div className="panel__header">
          <h2 className="panel__title">New brief</h2>
          <label>
            <span className="visually-hidden">Research provider</span>
            <select className="research-form__provider" value={mode}
              onChange={event => { setMode(event.target.value as Mode); setError(""); }}>
              {config.modes.map(key => <option key={key} value={key}>{providerNames[key]}</option>)}
            </select>
          </label>
        </div>

        <label className="form-field">Research question
          <textarea className="form-field__input form-field__input--question"
            ref={questionField} required maxLength={4000} readOnly={demo}
            value={demo ? config.demo_question : question}
            onChange={event => setQuestion(event.target.value)} />
        </label>

        {demo ? <p className="feedback feedback--notice">The offline demo uses a fixed question and synthetic evidence.</p> :
          <div className="research-form__scope">
            <label className="form-field form-field--symbols">Symbols
              <input className="form-field__input" required value={symbols}
                onChange={e => setSymbols(e.target.value)} />
            </label>
            <label className="form-field">Start date
              <input className="form-field__input" type="date" required value={start}
                onChange={e => setStart(e.target.value)} />
            </label>
            <label className="form-field">End date
              <input className="form-field__input" type="date" required value={end}
                onChange={e => setEnd(e.target.value)} />
            </label>
          </div>}

        <details className="research-settings">
          <summary className="research-settings__summary">Research settings</summary>
          <div className="research-settings__fields">
            <label className="form-field form-field--budget">Tool-call budget
              <input className="form-field__input" type="number" min={0} max={10} required value={budget}
                onChange={e => setBudget(Number(e.target.value))} />
            </label>
            <label className="checkbox-field">
              <input className="checkbox-field__input" type="checkbox" checked={review}
                onChange={e => setReview(e.target.checked)} /> Review the draft
            </label>
          </div>
        </details>

        {error && <p role="alert" className="feedback feedback--error">{error}</p>}

        <div className="research-form__footer">
          <span className="research-form__provider-note">
            {demo ? "Runs offline" : mode === "bedrock"
              ? "Uses your server’s Bedrock credentials"
              : "Runs with your local Ollama model"}
          </span>
          <button className="button button--primary" disabled={busy}>
            {busy ? "Starting…" : "Run research"} <span className="button__icon" aria-hidden="true">→</span>
          </button>
        </div>
      </form>

      <aside className="research-starters" aria-labelledby="starters-title">
        <h2 className="research-starters__title" id="starters-title">Research starters</h2>
        <p className="research-starters__description">Choose a question to edit.</p>

        {starters.map(item => <button className="research-starter" key={item.title}
          disabled={busy} onClick={() => chooseStarter(item)}>
          <span className="research-starter__category">{item.category}</span>
          <span className="research-starter__title">
            {item.title}<span className="research-starter__icon" aria-hidden="true">↗</span>
          </span>
        </button>)}

        <button className="research-starters__demo" type="button" disabled={busy}
          onClick={() => { setMode("demo"); setError(""); questionField.current?.focus(); }}>
          Try the offline demo <span className="research-starters__demo-icon" aria-hidden="true">→</span>
        </button>
      </aside>
    </div>
  </>;
}
