import React, { useMemo, useState } from "react";
import data from "../features.json";
import checkpoint from "../../state/checkpoint.json";

// Status is never edited here: it comes from features.json, which only the
// evidence gate (tools/update_tracker.py) may change. This view is read-only.

const STATUS_ORDER = ["done", "in-progress", "failing", "blocked", "planned"];
const STATUS_LABEL = {
  done: "Done",
  "in-progress": "In progress",
  failing: "Failing",
  blocked: "Blocked",
  planned: "Planned",
};
const PARITY_LABEL = { iLovePDF: "iLovePDF", Nitro: "Nitro PDF Pro", Beyond: "Beyond both" };
const DEFAULT_RESERVE = 0.25;

const fmt = new Intl.NumberFormat("en-US");

function countByStatus(features) {
  const counts = Object.fromEntries(STATUS_ORDER.map((s) => [s, 0]));
  for (const f of features) counts[f.status] += 1;
  return counts;
}

function pct(part, whole) {
  return whole === 0 ? 0 : Math.round((part / whole) * 100);
}

// Mirrors tools/session_budget.py: unfinished work first, then planned work in phase order.
export function nextTasks(features, phases, sizes, remaining, reserve = DEFAULT_RESERVE) {
  const usable = Math.floor(Math.max(0, remaining) * (1 - reserve));
  const rank = Object.fromEntries(phases.map((p, i) => [p.id, i]));
  return features
    .filter((f) => ["in-progress", "failing", "planned"].includes(f.status) && sizes[f.estTokens] <= usable)
    .sort(
      (a, b) =>
        (a.status === "planned") - (b.status === "planned") ||
        rank[a.phase] - rank[b.phase] ||
        a.id.localeCompare(b.id),
    );
}

function StackedBar({ counts, total, label }) {
  return (
    <div className="bar" role="img" aria-label={label}>
      {STATUS_ORDER.filter((s) => s !== "planned" && counts[s] > 0).map((s) => (
        <span key={s} className={`bar-seg st-${s}`} style={{ width: `${(counts[s] / total) * 100}%` }} />
      ))}
    </div>
  );
}

function StatusPill({ status }) {
  return <span className={`pill st-${status}`}>{STATUS_LABEL[status]}</span>;
}

function Summary({ features }) {
  const counts = countByStatus(features);
  const total = features.length;
  return (
    <section className="summary" aria-labelledby="summary-h">
      <div className="summary-head">
        <h2 id="summary-h" className="eyebrow">Overall</h2>
        <p className="big">
          <span className="num">{pct(counts.done, total)}%</span>
          <span className="big-sub">
            {counts.done} of {total} features proven by passing tests
          </span>
        </p>
      </div>
      <StackedBar counts={counts} total={total} label={`${counts.done} of ${total} done`} />
      <ul className="legend">
        {STATUS_ORDER.map((s) => (
          <li key={s}>
            <span className={`dot st-${s}`} aria-hidden="true" />
            {STATUS_LABEL[s]} <span className="num muted">{counts[s]}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Phases({ features, phases, sizes }) {
  return (
    <section aria-labelledby="phases-h">
      <h2 id="phases-h" className="section-title">Roadmap by phase</h2>
      <ol className="phases">
        {phases.map((p) => {
          const rows = features.filter((f) => f.phase === p.id);
          const counts = countByStatus(rows);
          const tokens = rows.reduce((sum, f) => sum + sizes[f.estTokens], 0);
          return (
            <li key={p.id} className="phase">
              <div className="phase-top">
                <span className="phase-id">{p.id}</span>
                <span className="phase-name">{p.name}</span>
                <span className="num muted phase-count">
                  {counts.done}/{rows.length}
                </span>
              </div>
              <StackedBar counts={counts} total={rows.length} label={`${p.id}: ${counts.done} of ${rows.length} done`} />
              <div className="phase-meta muted">~{fmt.format(Math.round(tokens / 1000))}k tokens estimated</div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function Checkpoint() {
  return (
    <section className="panel" aria-labelledby="cp-h">
      <h2 id="cp-h" className="section-title">Session checkpoint</h2>
      <dl className="kv">
        <dt>Phase</dt>
        <dd>{checkpoint.phase}</dd>
        <dt>Task</dt>
        <dd className="mono">{checkpoint.taskId}</dd>
        <dt>Step</dt>
        <dd>{checkpoint.step}</dd>
        <dt>Branch</dt>
        <dd className="mono">{checkpoint.branch}</dd>
        <dt>Next action</dt>
        <dd className="next">{checkpoint.nextAction}</dd>
        <dt>Last green</dt>
        <dd className="mono small">{checkpoint.lastGreenCommit}</dd>
        <dt>Updated</dt>
        <dd className="num">{checkpoint.updated}</dd>
      </dl>
    </section>
  );
}

const KIND_LABEL = { todo: "To do", limitation: "Known limitation", decision: "Needs a decision" };

// Plain strings (the older checkpoint format) are shown under "Unassigned".
function normalizeQuestion(q) {
  return typeof q === "string" ? { phase: null, kind: "todo", text: q } : q;
}

function OpenQuestions({ phases }) {
  const questions = (checkpoint.openQuestions ?? []).map(normalizeQuestion);
  const groups = [...phases, { id: null, name: "Unassigned" }]
    .map((p) => ({ ...p, items: questions.filter((q) => q.phase === p.id) }))
    .filter((g) => g.items.length > 0);
  return (
    <section aria-labelledby="oq-h">
      <h2 id="oq-h" className="section-title">
        Open questions by phase <span className="num muted">({questions.length})</span>
      </h2>
      {groups.length === 0 ? (
        <p className="muted">None open.</p>
      ) : (
        <div className="oq-groups">
          {groups.map((g) => (
            <div key={g.id ?? "none"} className="oq-group">
              <h3 className="cat-title">
                {g.id && <span className="mono">{g.id}</span>} {g.name}
              </h3>
              <ul className="questions">
                {g.items.map((q) => (
                  <li key={q.text}>
                    <span className={`kind kind-${q.kind}`}>{KIND_LABEL[q.kind] ?? q.kind}</span> {q.text}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function Budget({ features, phases, sizes }) {
  const [remaining, setRemaining] = useState(600000);
  const usable = Math.floor(Math.max(0, remaining) * (1 - DEFAULT_RESERVE));
  const tasks = useMemo(() => nextTasks(features, phases, sizes, remaining), [features, phases, sizes, remaining]);
  return (
    <section className="panel" aria-labelledby="budget-h">
      <h2 id="budget-h" className="section-title">Token look-ahead</h2>
      <label className="field" htmlFor="remaining-tokens">
        Tokens left in this session
        <input
          id="remaining-tokens"
          type="number"
          min="0"
          step="10000"
          value={remaining}
          onChange={(e) => setRemaining(Number(e.target.value) || 0)}
        />
      </label>
      <p className="muted small">
        Usable after the 25% reserve: <span className="num">{fmt.format(usable)}</span>. Sizes: S{" "}
        {fmt.format(sizes.S / 1000)}k · M {fmt.format(sizes.M / 1000)}k · L {fmt.format(sizes.L / 1000)}k.
      </p>
      {tasks.length === 0 ? (
        <p className="warn">Nothing fits. Write the checkpoint and end the session cleanly.</p>
      ) : (
        <ol className="next-list">
          {tasks.slice(0, 6).map((f) => (
            <li key={f.id}>
              <span className="mono">{f.id}</span>
              <span className="size">{f.estTokens}</span>
              <span className="next-name">{f.name}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function Parity({ features }) {
  return (
    <section aria-labelledby="parity-h">
      <h2 id="parity-h" className="section-title">Parity coverage</h2>
      <div className="parity">
        {Object.entries(PARITY_LABEL).map(([tag, label]) => {
          const rows = features.filter((f) => f.parity.includes(tag));
          const done = rows.filter((f) => f.status === "done").length;
          return (
            <div key={tag} className="parity-item">
              <div className="parity-top">
                <span>{label}</span>
                <span className="num muted">
                  {done}/{rows.length}
                </span>
              </div>
              <StackedBar counts={countByStatus(rows)} total={rows.length} label={`${label}: ${done} of ${rows.length}`} />
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Catalog({ features, categories, phases }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [phase, setPhase] = useState("all");
  const [parity, setParity] = useState("all");

  const q = query.trim().toLowerCase();
  const visible = features.filter(
    (f) =>
      (status === "all" || f.status === status) &&
      (phase === "all" || f.phase === phase) &&
      (parity === "all" || f.parity.includes(parity)) &&
      (!q || f.id.toLowerCase().includes(q) || f.name.toLowerCase().includes(q) || f.libs.join(" ").toLowerCase().includes(q)),
  );

  return (
    <section aria-labelledby="catalog-h">
      <div className="catalog-head">
        <h2 id="catalog-h" className="section-title">
          Feature catalog <span className="num muted">({visible.length})</span>
        </h2>
        <div className="filters">
          <input
            id="filter-query"
            type="search"
            placeholder="Search ID, feature or library"
            aria-label="Search features"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select id="filter-status" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="all">All statuses</option>
            {STATUS_ORDER.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
          <select id="filter-phase" aria-label="Phase" value={phase} onChange={(e) => setPhase(e.target.value)}>
            <option value="all">All phases</option>
            {phases.map((p) => (
              <option key={p.id} value={p.id}>
                {p.id}
              </option>
            ))}
          </select>
          <select id="filter-parity" aria-label="Parity" value={parity} onChange={(e) => setParity(e.target.value)}>
            <option value="all">Any parity</option>
            {Object.entries(PARITY_LABEL).map(([tag, label]) => (
              <option key={tag} value={tag}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {visible.length === 0 && <p className="muted">No features match these filters.</p>}

      {categories.map((c) => {
        const rows = visible.filter((f) => f.category === c.id);
        if (rows.length === 0) return null;
        return (
          <div key={c.id} className="cat">
            <h3 className="cat-title">
              <span className="mono">{c.id}</span> {c.name}
            </h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">ID</th>
                    <th scope="col">Feature</th>
                    <th scope="col">Phase</th>
                    <th scope="col">Size</th>
                    <th scope="col">Parity</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((f) => (
                    <tr key={f.id}>
                      <td className="mono">{f.id}</td>
                      <td>
                        <div>{f.name}</div>
                        <div className="libs muted">{f.libs.join(" · ")}</div>
                      </td>
                      <td className="mono">{f.phase}</td>
                      <td className="mono">{f.estTokens}</td>
                      <td>
                        <div className="chips">
                          {f.parity.map((p) => (
                            <span key={p} className={`chip chip-${p.toLowerCase()}`}>
                              {p}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td>
                        <StatusPill status={f.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </section>
  );
}

export default function FeatureTracker() {
  const { features, phases, categories, sizes } = data;
  return (
    <div className="page">
      <header className="masthead">
        <div>
          <p className="eyebrow">{data.project} · build board</p>
          <h1>Every feature, and the evidence behind it</h1>
          <p className="lede muted">
            Status comes from <span className="mono">tracker/features.json</span>. A feature turns{" "}
            <StatusPill status="done" /> only when its linked tests pass in the local gate (
            <span className="mono">tools/gate.py</span>).
          </p>
        </div>
      </header>
      <Summary features={features} />
      <div className="grid-2">
        <Checkpoint />
        <Budget features={features} phases={phases} sizes={sizes} />
      </div>
      <Phases features={features} phases={phases} sizes={sizes} />
      <OpenQuestions phases={phases} />
      <Parity features={features} />
      <Catalog features={features} categories={categories} phases={phases} />
      <footer className="muted small">
        Source: github.com/kalibudz/pdfworkerz · run <span className="mono">npm run dev</span> in{" "}
        <span className="mono">tracker/</span> for a live, hot-reloading view.
      </footer>
    </div>
  );
}
