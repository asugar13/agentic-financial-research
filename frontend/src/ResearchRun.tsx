import { useEffect, useState } from "react";
import { api, providerNames, type ResearchRunResponse, type Source } from "./api";

function revealSource(id: string) {
  const entry = document.getElementById("source-" + id) as HTMLDetailsElement | null;
  if (!entry) return;

  entry.open = true;
  entry.querySelector("summary")?.focus({ preventScroll: true });
  entry.scrollIntoView({ block: "nearest" });
}

function SourceList({ sources }: { sources: Source[] }) {
  return <aside className="panel source-notebook">
    <div className="panel__header">
      <h2 className="panel__title">Source notebook</h2>
      <span className="item-count">{sources.length}</span>
    </div>

    {sources.length === 0 && <p>Collected evidence will appear here.</p>}

    {sources.map(item => <details key={item.id} id={"source-" + item.id} className="source-card">
      <summary className="source-card__summary">
        <span className="source-card__meta"><span className="citation">{item.id}</span> {item.kind}</span>
        <strong className="source-card__title">{item.title}</strong>
        <span className="source-card__publisher">{item.publisher}</span>
      </summary>

      <p className="source-card__excerpt">{item.excerpt}</p>
      <small className="source-card__period">
        {item.period_start}{item.period_start !== item.period_end && " – " + item.period_end}
      </small>

      {/^https?:\/\//i.test(item.url) && (
        <a className="source-card__link" href={item.url} target="_blank" rel="noreferrer">
          Open original source ↗
        </a>
      )}
    </details>)}
  </aside>;
}

export function ResearchRun({ id }: { id: string }) {
  const [run, setRun] = useState<ResearchRunResponse | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;

    async function refresh() {
      try {
        const current = await api<ResearchRunResponse>(
          "runs/" + encodeURIComponent(id), { signal: controller.signal },
        );

        if (controller.signal.aborted) return;

        setRun(current);
        setError("");

        if (current.status === "running") timer = window.setTimeout(refresh, 1000);
      } catch (err) {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Could not load this run.");
      }
    }

    void refresh();

    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [id, attempt]);

  return <>
    <a className="back-link" href="/?view=radar">← Event radar</a>

    {error && <div className="feedback feedback--error" role="alert">
      {error} <button onClick={() => setAttempt(attempt + 1)}>Retry loading</button>
    </div>}

    {run ? <>
      <header className="page-intro page-intro--run">
        <div className="run-meta">
          <span className={"run-status run-status--" + run.status} role="status">
            {{ succeeded: "Research complete", failed: "Research failed", running: "Research in progress" }[run.status]}
          </span>
          <span>{providerNames[run.mode]}{run.mode === "demo" && " · Synthetic evidence"}</span>
          {run.saved_at && <span>Saved {new Date(run.saved_at).toLocaleDateString(undefined, { dateStyle: "medium" })}</span>}
        </div>
        <h1 className="page-intro__title page-intro__title--question">{run.question}</h1>
      </header>

      {run.error && <div className="feedback feedback--error" role="alert">{run.error}</div>}

      <div className="research-layout">
        <div className="research-layout__main">
          <section className="panel report">
            <div className="panel__header"><h2 className="panel__title">Findings</h2></div>

            {run.report ? <>
              <h3 className="report__title">{run.report.title}</h3>
              {run.report.claims.length === 0 && <p>No supported findings were produced.</p>}

              {run.report.claims.map((claim, index) => <div className="report-claim" key={index}>
                <span className="report-claim__kind">{claim.kind}</span>
                <p className="report-claim__statement">
                  {claim.statement} {claim.evidence_ids.map(key => (
                    <button key={key} className="citation citation--interactive"
                      aria-label={"Read source " + key} onClick={() => revealSource(key)}>
                      {key}
                    </button>
                  ))}
                </p>
              </div>)}

              <h4 className="report__limitations-title">Limitations</h4>
              <ul className="report__limitations">
                {run.report.limitations.map((note, index) => (
                  <li className="report__limitation" key={index}>{note}</li>
                ))}
              </ul>
            </> : <p>
              {run.status === "failed"
                ? "This run ended before a final report was available."
                : "Gathering evidence and preparing the report…"}
            </p>}
          </section>

          <details className="panel run-activity" open={run.status === "running"}>
            <summary className="run-activity__summary">
              Run activity <span className="item-count run-activity__count">{run.events.length}</span>
            </summary>
            {run.events.length ? <ol className="run-activity__list">
              {run.events.map((line, i) => <li className="run-activity__item" key={i}>{line}</li>)}
            </ol> : <p>Starting the research graph…</p>}
          </details>
        </div>

        <SourceList sources={run.sources} />
      </div>
    </> : !error && <p role="status">Opening research run…</p>}
  </>;
}
