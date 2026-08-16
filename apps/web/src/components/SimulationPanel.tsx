/**
 * Simulation setup drawer: analysis → net → terminals → values → accuracy →
 * estimate → queue.
 *
 * Net-first filtering keeps large boards manageable: after choosing a net,
 * only components, terminals and vias on that net appear. Source and Load
 * accept an *attachment group* — any number of terminals and vias sharing one
 * equipotential/current draw (a BGA rail's pins, a pin plus a nearby via) —
 * picked from a checkbox tree grouped by component designator, with a
 * select-all/none control per designator and a separate Vias section.
 * Resistance's From/To stay single-terminal (ADR-0010: a resistance probe
 * measures between two representative points, not two groups).
 *
 * Selecting a terminal highlights its physical pads in the viewport, and
 * clicking a pad in the viewport (when a picker is armed) adds that terminal
 * to whichever attachment is armed here — cross-probing both ways.
 *
 * Estimates come from the backend's own mesher sizing pass and are refreshed
 * debounced on every draft change; the queue button submits and the queue
 * drawer takes over. Server-side budgets are authoritative — an over-budget
 * draft shows the refusal here rather than silently degrading accuracy.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, estimateSimulation, queueSimulation } from "../api/client";
import type {
  AccuracyProfile,
  BoardReviewResponse,
  EstimateResponse,
  SimulationDraftRequest,
  SimulationKind,
  TerminalResponse,
  ViaResponse,
} from "../api/types";
import { useBoardState } from "../state/boardState";

const ACCURACY_LABELS: { id: AccuracyProfile; label: string }[] = [
  { id: "preview", label: "Preview" },
  { id: "standard", label: "Standard" },
  { id: "high", label: "High" },
  { id: "verification", label: "Verification" },
];

/** Debounce for estimate refreshes while the user edits the draft. */
const ESTIMATE_DEBOUNCE_MS = 400;

interface LoadRow {
  terminalIds: string[];
  viaIds: string[];
  currentA: string;
}

export function SimulationPanel() {
  const { state, dispatch } = useBoardState();
  const review = state.review;

  const [kind, setKind] = useState<SimulationKind>("ir_drop");
  const [netId, setNetId] = useState<string>("");
  const [sourceTerminalIds, setSourceTerminalIds] = useState<string[]>([]);
  const [sourceViaIds, setSourceViaIds] = useState<string[]>([]);
  const [toId, setToId] = useState<string | null>(null);
  const [sourceVoltage, setSourceVoltage] = useState("0.85");
  const [loads, setLoads] = useState<LoadRow[]>([{ terminalIds: [], viaIds: [], currentA: "1.0" }]);
  const [accuracy, setAccuracy] = useState<AccuracyProfile>("standard");
  const [viaPlatingUm, setViaPlatingUm] = useState("25");
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null);
  const [estimateError, setEstimateError] = useState<string | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [queueing, setQueueing] = useState(false);
  /** Which picker consumes the next viewport terminal pick. */
  const [armedTarget, setArmedTarget] = useState<string | null>(null);

  const netTerminals = useMemo(
    () => (review ? review.terminals.filter((t) => t.net_id === netId) : []),
    [review, netId],
  );
  const netVias = useMemo(
    () => (review ? review.vias.filter((v) => v.net_id === netId) : []),
    [review, netId],
  );

  // Cross-probe: viewport picks flow into whichever picker is armed. For a
  // multi-select attachment (Source/Load) a pick adds to the group rather
  // than replacing it, so arming once and clicking several pads builds up a
  // group; resistance's Source is single-terminal (ADR-0010), so a pick
  // there replaces instead.
  useEffect(() => {
    if (state.pickedTerminalId === null || armedTarget === null) {
      return;
    }
    const picked = state.pickedTerminalId;
    if (netTerminals.some((t) => t.id === picked)) {
      if (armedTarget === "source") {
        setSourceTerminalIds((ids) => (kind === "resistance" ? [picked] : addUnique(ids, picked)));
      } else if (armedTarget === "to") {
        setToId(picked);
      } else if (armedTarget.startsWith("load-")) {
        const index = Number(armedTarget.slice(5));
        setLoads((rows) =>
          rows.map((row, i) =>
            i === index ? { ...row, terminalIds: addUnique(row.terminalIds, picked) } : row,
          ),
        );
      }
    }
    setArmedTarget(null);
    dispatch({ type: "terminal-pick-consumed" });
  }, [state.pickedTerminalId, armedTarget, netTerminals, kind, dispatch]);

  // Highlight every referenced terminal in the viewport.
  useEffect(() => {
    const ids = [
      ...sourceTerminalIds,
      ...(toId !== null ? [toId] : []),
      ...loads.flatMap((row) => row.terminalIds),
    ];
    dispatch({ type: "terminals-highlighted", terminalIds: ids });
  }, [sourceTerminalIds, toId, loads, dispatch]);

  const draft = useMemo((): SimulationDraftRequest | null => {
    if (!netId || (sourceTerminalIds.length === 0 && sourceViaIds.length === 0)) {
      return null;
    }
    if (kind === "resistance") {
      if (toId === null) {
        return null;
      }
      return {
        kind,
        net_id: netId,
        source_terminal_ids: sourceTerminalIds,
        source_via_ids: sourceViaIds,
        to_terminal_ids: [toId],
        accuracy,
        via_plating_um: numberOrNull(viaPlatingUm),
      };
    }
    const parsedLoads = loads
      .filter((row) => row.terminalIds.length > 0 || row.viaIds.length > 0)
      .map((row) => ({
        terminal_ids: row.terminalIds,
        via_ids: row.viaIds,
        current_a: Number(row.currentA),
      }))
      .filter((row) => Number.isFinite(row.current_a) && row.current_a > 0);
    if (parsedLoads.length === 0 || !Number.isFinite(Number(sourceVoltage))) {
      return null;
    }
    return {
      kind,
      net_id: netId,
      source_terminal_ids: sourceTerminalIds,
      source_via_ids: sourceViaIds,
      source_voltage_v: Number(sourceVoltage),
      loads: parsedLoads,
      accuracy,
      via_plating_um: numberOrNull(viaPlatingUm),
    };
  }, [
    kind,
    netId,
    sourceTerminalIds,
    sourceViaIds,
    toId,
    sourceVoltage,
    loads,
    accuracy,
    viaPlatingUm,
  ]);

  // Debounced estimate refresh.
  const draftRef = useRef(draft);
  draftRef.current = draft;
  useEffect(() => {
    setEstimate(null);
    setEstimateError(null);
    if (!draft || !review) {
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      estimateSimulation(review.board_id, draft, controller.signal)
        .then((response) => {
          if (draftRef.current === draft) {
            setEstimate(response);
          }
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) {
            return;
          }
          setEstimateError(error instanceof ApiError ? error.message : String(error));
        });
    }, ESTIMATE_DEBOUNCE_MS);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [draft, review]);

  const onQueue = useCallback(async () => {
    if (!draft || !review) {
      return;
    }
    setQueueing(true);
    setQueueError(null);
    try {
      await queueSimulation(review.board_id, draft);
      dispatch({ type: "bottom-tab-changed", tab: "jobs" });
      dispatch({ type: "simulation-panel-toggled", open: false });
    } catch (error) {
      setQueueError(error instanceof ApiError ? error.message : String(error));
    } finally {
      setQueueing(false);
    }
  }, [draft, review, dispatch]);

  if (!review) {
    return null;
  }

  const canQueue =
    draft !== null && estimate?.connectivity_ok === true && !estimate.over_budget && !queueing;

  return (
    <aside className="panel panel--right simulation-panel" aria-label="Simulation setup">
      <div className="panel__header simulation-panel__header">
        New simulation
        <button
          type="button"
          className="button button--ghost"
          onClick={() => dispatch({ type: "simulation-panel-toggled", open: false })}
        >
          Close
        </button>
      </div>
      <div className="panel__body simulation-panel__body">
        <label className="sim-field">
          <span className="sim-field__label">Analysis</span>
          <select
            value={kind}
            onChange={(event) => {
              // Resistance accepts only a single terminal per side, no vias
              // (ADR-0010); clear whatever a multi-member IR-drop source had
              // picked so a stale via/group selection can't ride along
              // invisibly into a resistance draft.
              setKind(event.target.value as SimulationKind);
              setSourceTerminalIds([]);
              setSourceViaIds([]);
              setToId(null);
              setLoads([{ terminalIds: [], viaIds: [], currentA: "1.0" }]);
            }}
          >
            <option value="ir_drop">DC IR drop</option>
            <option value="resistance">Terminal resistance</option>
          </select>
        </label>

        <label className="sim-field">
          <span className="sim-field__label">Net</span>
          <select
            value={netId}
            onChange={(event) => {
              setNetId(event.target.value);
              setSourceTerminalIds([]);
              setSourceViaIds([]);
              setToId(null);
              setLoads([{ terminalIds: [], viaIds: [], currentA: "1.0" }]);
            }}
          >
            <option value="">Select a net…</option>
            {review.nets.map((net) => (
              <option key={net.id} value={net.id}>
                {net.name}
              </option>
            ))}
          </select>
        </label>

        {netId ? (
          <>
            {kind === "resistance" ? (
              <TerminalPicker
                label="From"
                review={review}
                terminals={netTerminals}
                selectedId={sourceTerminalIds[0] ?? null}
                onSelect={(id) => setSourceTerminalIds([id])}
                armed={armedTarget === "source"}
                onArm={() => {
                  setArmedTarget("source");
                  dispatch({ type: "terminal-pick-armed", armed: true });
                }}
              />
            ) : (
              <AttachmentPicker
                label="Source"
                review={review}
                terminals={netTerminals}
                vias={netVias}
                selectedTerminalIds={sourceTerminalIds}
                selectedViaIds={sourceViaIds}
                onChange={(terminalIds, viaIds) => {
                  setSourceTerminalIds(terminalIds);
                  setSourceViaIds(viaIds);
                }}
                armed={armedTarget === "source"}
                onArm={() => {
                  setArmedTarget("source");
                  dispatch({ type: "terminal-pick-armed", armed: true });
                }}
              />
            )}
            {kind === "ir_drop" ? (
              <label className="sim-field">
                <span className="sim-field__label">Source voltage</span>
                <span className="sim-field__unit-row">
                  <input
                    type="number"
                    step="0.01"
                    value={sourceVoltage}
                    onChange={(event) => setSourceVoltage(event.target.value)}
                    aria-label="Source voltage in volts"
                  />
                  <span className="sim-field__unit">V</span>
                </span>
              </label>
            ) : null}

            {kind === "resistance" ? (
              <TerminalPicker
                label="To"
                review={review}
                terminals={netTerminals}
                selectedId={toId}
                onSelect={setToId}
                armed={armedTarget === "to"}
                onArm={() => {
                  setArmedTarget("to");
                  dispatch({ type: "terminal-pick-armed", armed: true });
                }}
              />
            ) : (
              <div className="sim-loads">
                <span className="sim-field__label">Loads</span>
                {loads.map((row, index) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: rows are positional slots (append/remove only)
                  <div className="sim-load-row" key={index}>
                    <AttachmentPicker
                      label={`Load ${index + 1}`}
                      compact
                      review={review}
                      terminals={netTerminals}
                      vias={netVias}
                      selectedTerminalIds={row.terminalIds}
                      selectedViaIds={row.viaIds}
                      onChange={(terminalIds, viaIds) =>
                        setLoads((rows) =>
                          rows.map((r, i) => (i === index ? { ...r, terminalIds, viaIds } : r)),
                        )
                      }
                      armed={armedTarget === `load-${index}`}
                      onArm={() => {
                        setArmedTarget(`load-${index}`);
                        dispatch({ type: "terminal-pick-armed", armed: true });
                      }}
                    />
                    <span className="sim-field__unit-row">
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        value={row.currentA}
                        aria-label={`Load ${index + 1} current in amperes`}
                        onChange={(event) =>
                          setLoads((rows) =>
                            rows.map((r, i) =>
                              i === index ? { ...r, currentA: event.target.value } : r,
                            ),
                          )
                        }
                      />
                      <span className="sim-field__unit">A</span>
                      {loads.length > 1 ? (
                        <button
                          type="button"
                          className="button button--ghost"
                          aria-label={`Remove load ${index + 1}`}
                          onClick={() => setLoads((rows) => rows.filter((_, i) => i !== index))}
                        >
                          ×
                        </button>
                      ) : null}
                    </span>
                  </div>
                ))}
                <button
                  type="button"
                  className="button button--ghost"
                  onClick={() =>
                    setLoads((rows) => [...rows, { terminalIds: [], viaIds: [], currentA: "1.0" }])
                  }
                >
                  + Add load
                </button>
              </div>
            )}

            <div className="sim-field">
              <span className="sim-field__label">Accuracy</span>
              <div className="sim-accuracy" role="radiogroup" aria-label="Accuracy profile">
                {ACCURACY_LABELS.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    className="sim-accuracy__stop"
                    aria-pressed={accuracy === option.id}
                    onClick={() => setAccuracy(option.id)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>

            <details className="sim-advanced">
              <summary>Advanced numerical settings</summary>
              <label className="sim-field">
                <span className="sim-field__label">Assumed via plating</span>
                <span className="sim-field__unit-row">
                  <input
                    type="number"
                    step="1"
                    min="1"
                    value={viaPlatingUm}
                    aria-label="Assumed via plating thickness in micrometres"
                    onChange={(event) => setViaPlatingUm(event.target.value)}
                  />
                  <span className="sim-field__unit">µm</span>
                </span>
              </label>
              <p className="sim-note">
                Used only for vias whose fabrication data omits plating; recorded as an assumption
                in the result.
              </p>
            </details>

            <EstimateSummary estimate={estimate} error={estimateError} pending={draft !== null} />

            {queueError ? <p className="sim-error">{queueError}</p> : null}
            <button
              type="button"
              className="button sim-queue-button"
              disabled={!canQueue}
              onClick={onQueue}
            >
              {queueing ? "Queueing…" : "Queue simulation"}
            </button>
          </>
        ) : null}
      </div>
    </aside>
  );
}

function addUnique(ids: string[], id: string): string[] {
  return ids.includes(id) ? ids : [...ids, id];
}

function numberOrNull(text: string): number | null {
  const value = Number(text);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function EstimateSummary({
  estimate,
  error,
  pending,
}: {
  estimate: EstimateResponse | null;
  error: string | null;
  pending: boolean;
}) {
  if (error) {
    return <p className="sim-error">{error}</p>;
  }
  if (!estimate) {
    return pending ? <p className="sim-note">Estimating…</p> : null;
  }
  return (
    <section className="sim-estimate" aria-label="Compute estimate">
      <div className="sim-estimate__row">
        <span>Estimated mesh</span>
        <span className="numeric">~{formatCount(estimate.triangles)} triangles</span>
      </div>
      <div className="sim-estimate__row">
        <span>DOFs</span>
        <span className="numeric">~{formatCount(estimate.dofs)}</span>
      </div>
      <div className="sim-estimate__row">
        <span>Memory</span>
        <span className="numeric">~{(estimate.estimated_memory_bytes / 1e9).toFixed(2)} GB</span>
      </div>
      <div className="sim-estimate__row">
        <span>Compute</span>
        <span className={`sim-compute sim-compute--${estimate.compute_class}`}>
          {estimate.compute_class.replace("_", " ")}
        </span>
      </div>
      {!estimate.connectivity_ok ? (
        <p className="sim-error">{estimate.connectivity_message}</p>
      ) : null}
      {estimate.over_budget ? (
        <p className="sim-error">
          This mesh exceeds the configured execution budget ({formatCount(estimate.dofs)} of{" "}
          {formatCount(estimate.budget_dofs)} DOFs). Reduce accuracy — it is never lowered silently.
        </p>
      ) : null}
      {estimate.duplicate_result_job_id ? (
        <p className="sim-note">
          An identical analysis already completed ({estimate.duplicate_result_job_id}).
        </p>
      ) : null}
    </section>
  );
}

function formatCount(value: number): string {
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1)} M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(0)} k`;
  }
  return String(value);
}

/** Expandable component → terminal tree, single-select (resistance From/To). */
function TerminalPicker({
  label,
  review,
  terminals,
  selectedId,
  onSelect,
  armed,
  onArm,
}: {
  label: string;
  review: BoardReviewResponse;
  terminals: TerminalResponse[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  armed: boolean;
  onArm: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const componentsById = useMemo(
    () => new Map(review.components.map((component) => [component.id, component])),
    [review],
  );
  const selected = terminals.find((t) => t.id === selectedId) ?? null;

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const byComponent = new Map<string, TerminalResponse[]>();
    for (const terminal of terminals) {
      if (needle && !terminal.name.toLowerCase().includes(needle)) {
        continue;
      }
      const key = terminal.component_id ?? "";
      const bucket = byComponent.get(key);
      if (bucket) {
        bucket.push(terminal);
      } else {
        byComponent.set(key, [terminal]);
      }
    }
    return [...byComponent.entries()].sort(([a], [b]) => {
      const nameA = componentsById.get(a)?.reference_designator ?? "";
      const nameB = componentsById.get(b)?.reference_designator ?? "";
      return nameA.localeCompare(nameB);
    });
  }, [terminals, query, componentsById]);

  return (
    <div className="terminal-picker">
      <span className="sim-field__label">{label}</span>
      <div className="terminal-picker__control">
        <button
          type="button"
          className="terminal-picker__value"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {selected ? selected.name : "Select terminal…"}
        </button>
        <button
          type="button"
          className="button button--ghost terminal-picker__pick"
          aria-pressed={armed}
          title="Pick in viewport"
          onClick={onArm}
        >
          ⌖
        </button>
      </div>
      {open ? (
        <div className="terminal-picker__tree">
          <input
            type="search"
            className="net-search"
            placeholder="Search designator or pin…"
            aria-label={`Search ${label} terminals`}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {groups.map(([componentId, members]) => {
            const component = componentsById.get(componentId);
            return (
              <details key={componentId || "(loose)"} open={groups.length <= 3}>
                <summary>
                  {component
                    ? `${component.reference_designator}${
                        component.part_number ? ` · ${component.part_number}` : ""
                      }`
                    : "(no component)"}
                  <span className="terminal-picker__count">{members.length}</span>
                </summary>
                <ul className="terminal-picker__list">
                  {members.map((terminal) => (
                    <li key={terminal.id}>
                      <button
                        type="button"
                        className="terminal-picker__item"
                        aria-pressed={terminal.id === selectedId}
                        onClick={() => {
                          onSelect(terminal.id);
                          setOpen(false);
                        }}
                      >
                        {terminal.name}
                      </button>
                    </li>
                  ))}
                </ul>
              </details>
            );
          })}
          {groups.length === 0 ? <p className="empty-state">No terminals match.</p> : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Multi-select attachment picker: any number of terminals (checkbox tree,
 * grouped by component designator with a select-all/none per group) plus any
 * number of vias (a separate flat checkbox section). Used for Source/Load,
 * which — unlike a resistance probe's From/To — accept a whole group.
 */
function AttachmentPicker({
  label,
  review,
  terminals,
  vias,
  selectedTerminalIds,
  selectedViaIds,
  onChange,
  armed,
  onArm,
  compact = false,
}: {
  label: string;
  review: BoardReviewResponse;
  terminals: TerminalResponse[];
  vias: ViaResponse[];
  selectedTerminalIds: string[];
  selectedViaIds: string[];
  onChange: (terminalIds: string[], viaIds: string[]) => void;
  armed: boolean;
  onArm: () => void;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const componentsById = useMemo(
    () => new Map(review.components.map((component) => [component.id, component])),
    [review],
  );
  const selectedTerminalSet = useMemo(() => new Set(selectedTerminalIds), [selectedTerminalIds]);
  const selectedViaSet = useMemo(() => new Set(selectedViaIds), [selectedViaIds]);

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const byComponent = new Map<string, TerminalResponse[]>();
    for (const terminal of terminals) {
      if (needle && !terminal.name.toLowerCase().includes(needle)) {
        continue;
      }
      const key = terminal.component_id ?? "";
      const bucket = byComponent.get(key);
      if (bucket) {
        bucket.push(terminal);
      } else {
        byComponent.set(key, [terminal]);
      }
    }
    return [...byComponent.entries()].sort(([a], [b]) => {
      const nameA = componentsById.get(a)?.reference_designator ?? "";
      const nameB = componentsById.get(b)?.reference_designator ?? "";
      return nameA.localeCompare(nameB);
    });
  }, [terminals, query, componentsById]);

  const filteredVias = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) {
      return vias;
    }
    return vias.filter((via) => via.id.toLowerCase().includes(needle));
  }, [vias, query]);

  const toggleTerminal = (id: string, checked: boolean) => {
    const next = checked
      ? addUnique(selectedTerminalIds, id)
      : selectedTerminalIds.filter((t) => t !== id);
    onChange(next, selectedViaIds);
  };
  const toggleVia = (id: string, checked: boolean) => {
    const next = checked ? addUnique(selectedViaIds, id) : selectedViaIds.filter((v) => v !== id);
    onChange(selectedTerminalIds, next);
  };
  const setGroupChecked = (members: TerminalResponse[], checked: boolean) => {
    const memberIds = new Set(members.map((m) => m.id));
    const next = checked
      ? [
          ...selectedTerminalIds,
          ...members.map((m) => m.id).filter((id) => !selectedTerminalSet.has(id)),
        ]
      : selectedTerminalIds.filter((id) => !memberIds.has(id));
    onChange(next, selectedViaIds);
  };
  const setAllViasChecked = (checked: boolean) => {
    const memberIds = new Set(filteredVias.map((v) => v.id));
    const next = checked
      ? [
          ...selectedViaIds,
          ...filteredVias.map((v) => v.id).filter((id) => !selectedViaSet.has(id)),
        ]
      : selectedViaIds.filter((id) => !memberIds.has(id));
    onChange(selectedTerminalIds, next);
  };

  const totalSelected = selectedTerminalIds.length + selectedViaIds.length;
  const summary =
    totalSelected === 0
      ? "Select attachment…"
      : `${selectedTerminalIds.length} terminal${selectedTerminalIds.length === 1 ? "" : "s"}` +
        (selectedViaIds.length > 0
          ? `, ${selectedViaIds.length} via${selectedViaIds.length === 1 ? "" : "s"}`
          : "");

  return (
    <div className={`terminal-picker${compact ? " terminal-picker--compact" : ""}`}>
      <span className="sim-field__label">{label}</span>
      <div className="terminal-picker__control">
        <button
          type="button"
          className="terminal-picker__value"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {summary}
        </button>
        <button
          type="button"
          className="button button--ghost terminal-picker__pick"
          aria-pressed={armed}
          title="Pick in viewport"
          onClick={onArm}
        >
          ⌖
        </button>
      </div>
      {open ? (
        <div className="terminal-picker__tree">
          <input
            type="search"
            className="net-search"
            placeholder="Search designator, pin or via…"
            aria-label={`Search ${label} attachments`}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {groups.map(([componentId, members]) => {
            const component = componentsById.get(componentId);
            const checkedCount = members.filter((m) => selectedTerminalSet.has(m.id)).length;
            const groupChecked = checkedCount === members.length;
            const groupIndeterminate = checkedCount > 0 && !groupChecked;
            return (
              <details key={componentId || "(loose)"} open={groups.length <= 3}>
                <summary>
                  <input
                    type="checkbox"
                    className="terminal-picker__group-select"
                    checked={groupChecked}
                    ref={(el) => {
                      if (el) el.indeterminate = groupIndeterminate;
                    }}
                    aria-label={`Select all terminals on ${component?.reference_designator ?? "this component"}`}
                    onClick={(event) => event.stopPropagation()}
                    onChange={(event) => setGroupChecked(members, event.target.checked)}
                  />
                  {component
                    ? `${component.reference_designator}${
                        component.part_number ? ` · ${component.part_number}` : ""
                      }`
                    : "(no component)"}
                  <span className="terminal-picker__count">
                    {checkedCount > 0 ? `${checkedCount}/` : ""}
                    {members.length}
                  </span>
                </summary>
                <ul className="terminal-picker__list">
                  {members.map((terminal) => (
                    <li key={terminal.id}>
                      <label className="terminal-picker__checkbox-item">
                        <input
                          type="checkbox"
                          checked={selectedTerminalSet.has(terminal.id)}
                          onChange={(event) => toggleTerminal(terminal.id, event.target.checked)}
                        />
                        {terminal.name}
                      </label>
                    </li>
                  ))}
                </ul>
              </details>
            );
          })}
          {groups.length === 0 ? <p className="empty-state">No terminals match.</p> : null}

          {vias.length > 0 ? (
            <details open={filteredVias.length > 0 && filteredVias.length <= 8}>
              <summary>
                <input
                  type="checkbox"
                  className="terminal-picker__group-select"
                  checked={
                    filteredVias.length > 0 && filteredVias.every((v) => selectedViaSet.has(v.id))
                  }
                  ref={(el) => {
                    if (!el) return;
                    const checkedCount = filteredVias.filter((v) =>
                      selectedViaSet.has(v.id),
                    ).length;
                    el.indeterminate = checkedCount > 0 && checkedCount < filteredVias.length;
                  }}
                  aria-label="Select all vias"
                  onClick={(event) => event.stopPropagation()}
                  onChange={(event) => setAllViasChecked(event.target.checked)}
                />
                Vias
                <span className="terminal-picker__count">
                  {selectedViaIds.length > 0 ? `${selectedViaIds.length}/` : ""}
                  {filteredVias.length}
                </span>
              </summary>
              <ul className="terminal-picker__list">
                {filteredVias.map((via) => (
                  <li key={via.id}>
                    <label className="terminal-picker__checkbox-item">
                      <input
                        type="checkbox"
                        checked={selectedViaSet.has(via.id)}
                        onChange={(event) => toggleVia(via.id, event.target.checked)}
                      />
                      {via.id}{" "}
                      <span className="sim-note">
                        ({via.from_layer_id}–{via.to_layer_id})
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
