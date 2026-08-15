/**
 * The board-opening workflow: empty, importing and failed states.
 *
 * Failure shows the parser's actual diagnosis -- an engineer debugging an
 * export needs the reason, not a status code. The dev-fixture shortcut only
 * appears when the backend (development builds only) offers one.
 */

import { useRef, useState } from "react";
import { useBoardActions, useDevFixture } from "../hooks/useBoardActions";
import { useBoardState } from "../state/boardState";

export function OpenBoard() {
  const { state } = useBoardState();
  const { importFile, importFixture } = useBoardActions();
  const devFixture = useDevFixture();
  const fileInput = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);

  if (state.phase.status === "importing") {
    return (
      <div className="open-board">
        <div className="open-board__card" role="status">
          <p>
            <strong>Importing {state.phase.sourceName}…</strong>
          </p>
          <p className="empty-state">
            Parsing, resolving references, building the board model and normalising copper geometry.
          </p>
        </div>
      </div>
    );
  }

  const failed = state.phase.status === "failed" ? state.phase : null;

  const onDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files.item(0);
    if (file) {
      void importFile(file);
    }
  };

  return (
    <div className="open-board">
      {/* biome-ignore lint/a11y/noStaticElementInteractions: drag-and-drop target; keyboard users use the Browse button below */}
      <div
        className={`open-board__card${dragging ? " open-board__card--dragging" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <p>
          <strong>Open an IPC-2581 board to begin</strong>
        </p>
        <p className="empty-state">Drop a file here, or</p>
        <button
          type="button"
          className="button button--primary"
          onClick={() => fileInput.current?.click()}
        >
          Browse…
        </button>
        {devFixture ? (
          <p>
            <button
              type="button"
              className="button button--ghost"
              onClick={() => void importFixture(devFixture)}
            >
              Load local fixture: {devFixture}
            </button>
          </p>
        ) : null}
        <input
          ref={fileInput}
          type="file"
          accept=".xml,.cvg"
          hidden
          aria-label="PCB source file"
          onChange={(event) => {
            const file = event.target.files?.item(0);
            if (file) {
              void importFile(file);
            }
            event.target.value = "";
          }}
        />
        {failed ? (
          <div className="open-board__error" role="alert">
            <strong>Import of {failed.sourceName} failed.</strong>
            {"\n"}
            {failed.message}
          </div>
        ) : null}
      </div>
    </div>
  );
}
