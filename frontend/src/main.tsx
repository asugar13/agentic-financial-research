import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/dm-sans/latin-400.css";
import "@fontsource/dm-sans/latin-600.css";
import "@fontsource/instrument-serif/latin-400.css";
import { api, type Config } from "./api";
import { ResearchForm } from "./ResearchForm";
import { ResearchRun } from "./ResearchRun";
import "./style.css";

function Desk() {
  const [config, setConfig] = useState<Config | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    api<Config>("config").then(setConfig).catch(() => setError(true));
  }, []);
  if (error) return <p className="feedback feedback--error" role="alert">The research API is unavailable. Start the Python server and <a href="/">reload the desk</a>.</p>;
  return config ? <ResearchForm config={config} /> : <p role="status">Opening research desk…</p>;
}

function App() {
  const id = new URLSearchParams(window.location.search).get("run");
  return <>
    <header className="site-header"><div className="workspace site-header__inner">
      <a className="brand" href="/">
        <span className="brand__mark" aria-hidden="true">
          <i className="brand__bar" />
          <i className="brand__bar brand__bar--middle" />
          <i className="brand__bar brand__bar--tall" />
        </span> Financial Research
      </a>
      <span className="site-header__label">Analyst workspace</span>
    </div></header>
    <main className="workspace workspace--content">{id ? <ResearchRun id={id} /> : <Desk />}</main>
  </>;
}

createRoot(document.getElementById("root")!).render(<App />);
