"use client";

import { useEffect, useState } from "react";

import { disableOpf, enableOpf, getOpfStatus } from "@/lib/api";
import type { OpfStatus } from "@/lib/types";

// Initial status fetch happens once on mount; subsequent polling only
// runs while ``loading`` is true so the header doesn't pound the API.
const POLL_MS = 1500;

export function OpfToggle() {
  const [status, setStatus] = useState<OpfStatus | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let alive = true;
    getOpfStatus()
      .then((s) => alive && setStatus(s))
      .catch(() => {
        // Backend not reachable — hide the toggle silently. The other
        // pages will surface their own connectivity errors.
        if (alive) setStatus({ ...EMPTY, available: false });
      });
    return () => {
      alive = false;
    };
  }, []);

  // Poll only while loading. Once we hit a stable state (off / on /
  // error), stop hitting the server.
  useEffect(() => {
    if (!status?.loading) return;
    let alive = true;
    const id = setInterval(() => {
      getOpfStatus()
        .then((s) => alive && setStatus(s))
        .catch(() => {});
    }, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [status?.loading]);

  // Hidden when the API was started in mock mode (no OPF available).
  if (!status || !status.available) return null;

  const onClick = async () => {
    if (pending || status.loading) return;
    setPending(true);
    try {
      // The first /enable POST blocks server-side until the worker is
      // ready (~30–60s for the real model). To keep the UI responsive
      // we kick off the request and immediately flip the visual to
      // "loading"; the polling loop above keeps things in sync.
      const next = status.enabled ? await disableOpf() : await enableOpf();
      setStatus(next);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setStatus((s) => (s ? { ...s, error: msg } : s));
    } finally {
      setPending(false);
    }
  };

  let label = "OpenAI Privacy Filter OFF";
  let cls = "opf-toggle opf-off";
  if (status.loading || pending) {
    label = "OpenAI Privacy Filter loading…";
    cls = "opf-toggle opf-loading";
  } else if (status.enabled) {
    label = "OpenAI Privacy Filter ON";
    cls = "opf-toggle opf-on";
  }

  const title = status.error
    ? `Último erro: ${status.error}`
    : status.enabled
    ? "Clique para liberar a memória do modelo (~3 GB)."
    : "Clique para subir o OPF (carregamento de ~30–60s na primeira vez).";

  return (
    <button
      type="button"
      className={cls}
      title={title}
      onClick={onClick}
      disabled={pending || status.loading}
    >
      <span className="opf-dot" />
      <span className="opf-label">{label}</span>
    </button>
  );
}

const EMPTY: OpfStatus = {
  available: false,
  enabled: false,
  loading: false,
  error: null,
  in_flight_jobs: 0,
};
