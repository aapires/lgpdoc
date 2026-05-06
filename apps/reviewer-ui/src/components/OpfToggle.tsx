"use client";

import { useEffect, useState } from "react";

import { disableOpf, enableOpf, getOpfStatus } from "@/lib/api";
import type { OpfStatus } from "@/lib/types";

// Polling cadences:
//   * during loading: 1.5 s (catch the moment the worker is ready)
//   * while ON: 5 s (keep the auto-disable countdown roughly in sync)
//   * while OFF: never (no signal worth polling for)
const POLL_LOADING_MS = 1500;
const POLL_ENABLED_MS = 5000;

function formatSeconds(s: number): string {
  if (s <= 0) return "00:00";
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

export function OpfToggle() {
  const [status, setStatus] = useState<OpfStatus | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let alive = true;
    getOpfStatus()
      .then((s) => alive && setStatus(s))
      .catch(() => {
        // Backend not reachable — hide the toggle silently. Other pages
        // will surface their own connectivity errors.
        if (alive) setStatus(EMPTY);
      });
    return () => {
      alive = false;
    };
  }, []);

  // Poll while loading (fast) or while enabled (slow, for the
  // auto-disable countdown). Stop once OFF — nothing changes from the
  // server's side until the user acts.
  useEffect(() => {
    if (!status) return;
    if (!status.loading && !status.enabled) return;
    const interval = status.loading ? POLL_LOADING_MS : POLL_ENABLED_MS;
    let alive = true;
    const id = setInterval(() => {
      getOpfStatus()
        .then((s) => alive && setStatus(s))
        .catch(() => {});
    }, interval);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [status?.loading, status?.enabled]);

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

  const showCountdown =
    status.enabled &&
    status.idle_timeout_seconds > 0 &&
    status.seconds_until_auto_disable !== null;
  const countdown = showCountdown
    ? formatSeconds(status.seconds_until_auto_disable as number)
    : null;

  let label = "OpenAI Privacy Filter OFF";
  let cls = "opf-toggle opf-off";
  if (status.loading || pending) {
    label = "OpenAI Privacy Filter loading…";
    cls = "opf-toggle opf-loading";
  } else if (status.enabled) {
    label = countdown
      ? `OpenAI Privacy Filter ON · ${countdown}`
      : "OpenAI Privacy Filter ON";
    cls = "opf-toggle opf-on";
  }

  const title = status.error
    ? `Último erro: ${status.error}`
    : status.enabled
    ? showCountdown
      ? `Auto-desliga em ${countdown} sem uso. Clique para desligar agora e liberar ~3 GB de memória.`
      : "Clique para liberar a memória do modelo (~3 GB)."
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
  idle_timeout_seconds: 0,
  seconds_until_auto_disable: null,
};
